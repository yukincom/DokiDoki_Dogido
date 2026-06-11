# models.py

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DogidoModel(BaseModel):
    """全モデルの基底クラス。

    extra="allow": アダプタのバージョンアップで未知フィールドが来ても落とさずに受け取る。
    populate_by_name=True: alias とフィールド名の両方でアクセスできるようにする。
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---- イベントスキーマ共通 enum ----
# 仕様 §6 参照

class SourceKind(str, Enum):
    """情報の取得元種別。

    VISUAL: プレイヤーが実際に視認している情報（最も確度が高い）
    AUDITORY: 音だけで検知した情報（方向は出してよいが断定は避ける）
    INFERRED: 暗所の広がり・湧きリスクなどの推定情報
    SYSTEM: サーバー内部で生成したイベント（死亡通知など）
    """
    VISUAL = "visual"
    AUDITORY = "auditory"
    INFERRED = "inferred"
    SYSTEM = "system"


class Certainty(str, Enum):
    """情報の確度レベル。"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PriorityHint(str, Enum):
    """イベントの処理優先度ヒント。

    ルールエンジンが最終的な優先度を決定するが、
    アダプタ側からも「緊急度の目安」を付けて送る。
    CRITICAL > URGENT > NORMAL > BACKGROUND の順。
    """
    CRITICAL = "critical"
    URGENT = "urgent"
    NORMAL = "normal"
    BACKGROUND = "background"


class HorizontalDirection(str, Enum):
    """プレイヤー視点からの水平方向（8方位）。"""
    FRONT = "front"
    FRONT_RIGHT = "front_right"
    RIGHT = "right"
    BACK_RIGHT = "back_right"
    BACK = "back"
    BACK_LEFT = "back_left"
    LEFT = "left"
    FRONT_LEFT = "front_left"


class VerticalRelation(str, Enum):
    """プレイヤーとの垂直位置関係。"""
    ABOVE = "above"
    SAME = "same"
    BELOW = "below"


class DistanceBand(str, Enum):
    """音由来の脅威で使う距離帯。

    音だけで検知した場合は正確な距離を断定しないため、
    TOUCHING / VERY_CLOSE / CLOSE / MID / FAR の帯域で表現する。
    """
    TOUCHING = "touching"
    VERY_CLOSE = "very_close"
    CLOSE = "close"
    MID = "mid"
    FAR = "far"


class EventName(str, Enum):
    """主要イベント名。1 メッセージに 1 つだけ持つ。

    STATUS_SNAPSHOT は現在の仕様書にも記載があり、定期スナップショット送信用に使う。
    """
    THREAT_DETECTED = "threat_detected"
    THREAT_APPROACHING = "threat_approaching"
    HOSTILE_AUDIO_DETECTED = "hostile_audio_detected"
    DANGER_DARKNESS_CHANGED = "danger_darkness_changed"
    RESOURCE_OPTION_FOUND = "resource_option_found"
    AMBIENT_MOB_DETECTED = "ambient_mob_detected"
    PLAYER_DIED = "player_died"
    TIME_PHASE_CHANGED = "time_phase_changed"
    COMBAT_ENDED = "combat_ended"
    STATUS_SNAPSHOT = "status_snapshot"


class TimePhase(str, Enum):
    """Minecraft の時間帯区分。

    time_of_day (0〜24000) を 4 区分に抽象化したもの。
    アダプタ側でマッピングして送る。
    """
    MORNING = "morning"
    DAY = "day"
    EVENING = "evening"
    NIGHT = "night"


class Weather(str, Enum):
    """天候。"""
    CLEAR = "clear"
    RAIN = "rain"
    THUNDER = "thunder"


# ---- 共通オブジェクト ----

class Direction(DogidoModel):
    """方向オブジェクト。水平・垂直とも省略可能。"""
    horizontal: HorizontalDirection | None = None
    vertical: VerticalRelation | None = None


class Position(DogidoModel):
    """ワールド座標（Minecraft Java 版座標系）。"""
    x: float | None = None
    y: float | None = None
    z: float | None = None


# ---- イベント本体 ----

class EventDescriptor(DogidoModel):
    """メッセージごとの主要イベントを 1 つだけ記述する。

    source_kind / certainty の組み合わせで発話制限ルールを適用する:
    - visual + high -> 具体名・後ろ警告ともに許可
    - auditory + low -> 方向のみ、具体名は原則禁止
    - inferred -> 「湧きそう」などの推定表現に留める
    """
    name: EventName
    source_kind: SourceKind
    priority_hint: PriorityHint
    certainty: Certainty


# ---- プレイヤー状態 ----

class PlayerState(DogidoModel):
    """プレイヤー本人の観測状態（仕様 §9）。

    yaw: 水平視線角度（0=南, 90=西, 180=北, 270=東）
    pitch: 垂直視線角度（-90=真上, 90=真下）
    """
    name: str | None = None
    position: Position = Field(default_factory=Position)
    yaw: float | None = None
    pitch: float | None = None
    health: float | None = None  # 最大 20。0 で死亡
    hunger: int | None = None  # 最大 20
    dimension: str | None = None  # 例: "minecraft:overworld" / "minecraft:the_nether"
    held_item: str | None = None  # 手持ちアイテムの Minecraft item id
    active_status_effects: list[str] = Field(default_factory=list)  # 例: ["mining_fatigue"]


# ---- ワールド状態 ----

class WorldState(DogidoModel):
    """プレイヤー周辺のワールド状態（仕様 §10）。

    暗所判定について:
        単純な local_light だけでなく connected_dark_volume / nearest_dark_spawn_distance も使い
        「足元は明るいが先に暗闇が続いている」ケースを拾う。
        最終的な危険度判断は danger_darkness_score を優先する（仕様 §10 注意参照）。
        enclosure_score は補助指標として使うが、単独での最終判定には使わない。

    仕様書に記載のある基本フィールドに加え、
    水没状態・ドア/ベッド周辺・リスポーン地点など拡張フィールドも含む。
    すべて optional にしてあり、アダプタ側の段階実装を許容する。
    """
    time_of_day: int | None = None  # 0〜24000（Minecraft ゲーム内時刻）
    time_phase: TimePhase | None = None
    weather: Weather | None = None
    biome: str | None = None  # 例: "plains" / "dripstone_caves"
    structure: str | None = None  # プレイヤー座標を含む構造物 id（例: "village_plains" / "ancient_city"）。構造物外は省略
    local_light: int | None = None  # プレイヤー足元のブロック光レベル（0〜15）
    sky_visible: bool | None = None  # 空が見えているか（屋外判定の補助）
    ceiling_height: float | None = None  # 天井までの高さ（ブロック数）
    overhead_cover_type: str | None = None  # 天井の素材ヒント（"solid" / "glass" など）
    is_submerged: bool | None = None  # 水中にいるか
    submerged_depth_blocks: int | None = None  # 水面からの深さ
    air_supply: int | None = None  # 残り空気量（水中溺死リスク判定用）
    nearby_door_count: int | None = None  # 近くのドア数
    open_door_count: int | None = None  # 開いているドア数（侵入リスク補助）
    nearby_bed_count: int | None = None  # 近くのベッド数（睡眠促進判定用）
    nearby_sleeping_people_count: int | None = None  # 近くで寝ている人数（マルチ対応の予約）
    drafty_opening_count: int | None = None  # 外気が入る開口部の数（湧きリスク補助）
    respawn_point_set: bool | None = None  # リスポーン地点が設定済みか
    respawn_distance: float | None = None  # リスポーン地点までの距離
    cardinal_wall_count: int | None = None  # 東西南北4方向の壁の数（0〜4, 屋内度の補助）
    double_height_open_side_count: int | None = None  # 足元+頭上の2マスが連続で開いている横開口数
    safe_zone_with_door: bool | None = None  # ドア付きの安全区画にいるか
    # ---- 暗所危険度スコア群（仕様 §6 / §10）----
    enclosure_score: float | None = None  # 屋内・地下っぽさの補助指標（0.0〜1.0）
    connected_dark_volume: int | None = None  # 周辺とつながっている暗い空間の広さ（ブロック数）
    nearest_dark_spawn_distance: float | None = None  # 最近の暗所湧きポイントまでの距離
    danger_darkness_score: float | None = None  # 総合暗所危険度（0.0〜1.0, 最優先で参照）
    nearby_light_source_count: int | None = None  # 周辺の実光源ブロック数
    nearest_light_source_distance: float | None = None  # 最近傍の実光源までの距離
    nearby_damaging_light_source_count: int | None = None  # 近距離の危険光源数（炎・焚き火・マグマ等）
    nearest_damaging_light_source_distance: float | None = None  # 最近傍の危険光源までの距離
    standing_on_magma_block: bool | None = None  # 足元がマグマブロックか
    nearby_firefly_bush_count: int | None = None  # 周辺のホタルブッシュ数（雰囲気演出用）
    ominous_sound_kind: str | None = None  # 例: "sculk_shrieker" / "warden_heartbeat" / "warden_sonic_boom"
    ominous_sound_recent_ms: int | None = None  # 最近の不穏音からの経過ミリ秒
    boss_omen_kind: str | None = None  # 例: "ender_dragon_arena" / "ender_dragon_summon" / "wither_assembly"
    rain_sound_recent_ms: int | None = None  # 最近の雨音観測からの経過ミリ秒
    thunder_sound_recent_ms: int | None = None  # 最近の雷鳴観測からの経過ミリ秒
    nearby_lightning_strike_recent_ms: int | None = None  # 近距離落雷の観測からの経過ミリ秒
    nearby_lightning_strike_distance: float | None = None  # 最近の近距離落雷までの距離
    ender_eye_launch_recent_ms: int | None = None  # プレイヤー近傍でのエンダーアイ投擲音からの経過ミリ秒
    nearby_portal_type: str | None = None  # 近距離に存在するポータルブロックの種類（"nether_portal" / "end_portal" / "end_gateway"）
    nearby_portal_distance: float | None = None  # 最近傍のポータルブロックまでの距離
    nearby_end_portal_frame_distance: float | None = None  # 4ブロック以内のエンドポータルフレームまでの距離


# ---- 脅威情報 ----

class VisualThreat(DogidoModel):
    """視認できている敵エンティティ（仕様 §11）。

    視認済みなので具体名（type）を使ってよい。
    発話でも具体名を出してよい。
    on_fire / in_water はドギドの特殊反応に使う:
        on_fire=True -> ゾンビが燃えると喜ぶ
        in_water=True -> 水に入って燃えないゾンビに呻く
    """
    type: str
    entity_id: str | None = None  # 同一エンティティの追跡用 ID（省略可）
    distance: float | None = None  # プレイヤーとの距離（ブロック数）
    direction: Direction = Field(default_factory=Direction)
    approaching: bool = False  # プレイヤーに近づいているか
    on_fire: bool = False
    in_water: bool = False
    certainty: Certainty = Certainty.HIGH  # 視認済みなので基本 HIGH


class AuditoryThreat(DogidoModel):
    """音だけで検知した脅威（仕様 §12）。

    未視認敵の正確な座標・entity_id は送らない。
    spoken_name_allowed=False の間は発話で具体名を出さない。
    以前に視認していた敵の記憶から推定する場合のみ、控えめな表現を許可する。

    label の推奨値:
        hostile_presence / hostile_voice_like / movement_like / explosive_threat_like
    """
    label: str  # 脅威の種類ラベル（具体的なモブ名ではない）
    source_id: str | None = None  # 過去の視認記憶と紐付けるための任意 ID
    sound_event: str | None = None  # 内部処理用の Minecraft サウンドイベント名
    direction: Direction = Field(default_factory=Direction)
    distance_band: DistanceBand | None = None  # 正確な距離は持たず帯域で表現
    certainty: Certainty = Certainty.LOW  # 音だけなので基本 LOW
    spoken_name_allowed: bool = False  # 発話で具体名を出してよいか（原則 False）


class PassiveMob(DogidoModel):
    """周囲にいる非敵対モブ（仕様 §13）。

    脅威判定には使わず、ドギドの「かわい〜！」系リアクションや川柳の題材に使う。
    友好（passive）種に加え、まだ敵対していない中立（neutral）種も
    temperament="neutral" として載ってよい。
    """
    type: str
    distance: float | None = None
    direction: Direction = Field(default_factory=Direction)
    certainty: Certainty = Certainty.HIGH
    temperament: str | None = None  # "friendly" / "neutral"
    caution_reason: str | None = None  # 例: "provoked_only", "darkness", "territorial"


class NearbyResource(DogidoModel):
    """周辺の取得候補ブロック・資源（仕様 §15）。

    松明やベッドの材料候補を暗所対処フローで参照する。
    例: coal_ore があれば「これで松明作ろや！」の判断材料になる。
    """
    type: str  # "block" など
    name: str  # Minecraft block/item id（例: "coal_ore", "oak_log"）
    distance: float | None = None
    direction: Direction = Field(default_factory=Direction)


class CombatState(DogidoModel):
    """戦闘・被弾に関する直近の集約状態（仕様 §16）。

    状態機械が panic / alert 遷移を判断するためにすぐ使える形にまとめたもの。
    recent_*_ms: 該当イベントからの経過ミリ秒（値が小さいほど直近）
    hostiles_within_*: 指定距離内の敵の数
    combat_active_hint: アダプタ側が「交戦中」と判断しているか
    """
    recent_damage_ms: int | None = Field(default=None, ge=0)
    recent_hostile_visual_ms: int | None = Field(default=None, ge=0)
    recent_hostile_audio_ms: int | None = Field(default=None, ge=0)
    hostiles_within_7: int | None = Field(default=None, ge=0)  # 7マス以内（panic 移行の閾値）
    hostiles_within_10: int | None = Field(default=None, ge=0)  # 10マス以内（複数敵警戒の閾値）
    hostiles_within_30_ground: int | None = Field(default=None, ge=0)  # 30マス以内の地上系敵数
    combat_active_hint: bool | None = None
    warden_recently_hurt: bool | None = None
    warden_defeat_confirmed: bool | None = None
    warden_ranged_trap_active: bool | None = None
    warden_nearby_iron_golem_count: int | None = Field(default=None, ge=0)
    nearby_experience_orb_count: int | None = Field(default=None, ge=0)
    warden_end_crystal_bombardment_active: bool | None = None
    warden_nearby_end_crystal_count: int | None = Field(default=None, ge=0)
    warden_tnt_minecart_setup_active: bool | None = None
    warden_nearby_tnt_minecart_count: int | None = Field(default=None, ge=0)


class MetaState(DogidoModel):
    """補助メタ情報（仕様 §17）。

    death_cause: player_died イベント時に死因を付ける（モンスター / 落下 / 溺死など）
    user_text: ユーザーがテキスト入力した場合の本文（「うるさい」検知にも使う）
    call_name: ドギドがプレイヤーを呼ぶときの名前（設定で変えられる）
    """
    adapter_build: str | None = None
    profile_name: str | None = None
    call_name: str | None = None
    debug: bool = False
    death_cause: str | None = None
    user_text: str | None = None


# ---- トップレベルイベント ----

class GameEvent(DogidoModel):
    """Fabric クライアントアダプタから dogido-server へ送るメッセージ本体（仕様 §4）。

    必須: schema_version / game / adapter / observed_at / event
    推奨: sequence / visual_threats / auditory_threats / inventory / combat
    任意: passive_mobs / nearby_resources / meta
    passive_mobs には非敵対状態の中立モブも temperament="neutral" で含まれる。
    旧スキーマ名 peaceful_mobs も受信時に受け付ける。

    inventory: キーは Minecraft item id、値は所持数。
               松明・石炭・木材・ベッド材料の有無を暗所対処フローで参照する。
    """
    schema_version: str
    game: str = "minecraft-java"
    adapter: str
    observed_at: datetime
    sequence: int | None = Field(default=None, ge=0)
    event: EventDescriptor
    player: PlayerState = Field(default_factory=PlayerState)
    world: WorldState = Field(default_factory=WorldState)
    visual_threats: list[VisualThreat] = Field(default_factory=list)
    auditory_threats: list[AuditoryThreat] = Field(default_factory=list)
    passive_mobs: list[PassiveMob] = Field(default_factory=list)
    inventory: dict[str, int] = Field(default_factory=dict)
    nearby_resources: list[NearbyResource] = Field(default_factory=list)
    combat: CombatState = Field(default_factory=CombatState)
    meta: MetaState = Field(default_factory=MetaState)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_peaceful_mobs(cls, value: object) -> object:
        # 旧スキーマ名 peaceful_mobs からの移行用
        if not isinstance(value, dict):
            return value
        if "passive_mobs" not in value and "peaceful_mobs" in value:
            cloned = dict(value)
            cloned["passive_mobs"] = cloned.get("peaceful_mobs")
            return cloned
        return value


# ---- API リクエスト / レスポンス ----

class AdapterSessionCreateRequest(DogidoModel):
    """アダプタ起動時のセッション登録リクエスト。

    capabilities: アダプタが対応しているイベント種別の一覧。
                  サーバー側が「このアダプタは auditory_threats を送れるか」を把握するために使う。
    call_name: ドギドがプレイヤーを呼ぶときの名前（セッション単位で上書き可能）。
    """
    adapter_name: str
    adapter_version: str
    game: str = "minecraft-java"
    schema_version: str
    player_name: str
    profile_name: str | None = None
    call_name: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class AdapterSessionCreateResponse(DogidoModel):
    """セッション登録成功時のレスポンス。

    heartbeat_interval_ms: この間隔でハートビートを送ること（アダプタへの指示）
    max_batch_size: バッチ送信の上限数
    """
    session_id: str
    accepted_schema_version: str
    server_time: datetime
    event_endpoint: str
    batch_endpoint: str
    heartbeat_interval_ms: int
    max_batch_size: int


class BatchEventRequest(DogidoModel):
    """複数イベントをまとめて送るバッチリクエスト。

    WebSocket 移行前の暫定手段として設けている。
    イベント数の上限は max_batch_size で制御（app.py 側でチェック）。
    """
    events: list[GameEvent] = Field(default_factory=list)


class HeartbeatRequest(DogidoModel):
    """死活確認 + シーケンス追跡リクエスト。

    last_sequence: アダプタが最後に送信したシーケンス番号（抜け検知用）
    """
    last_sequence: int | None = Field(default=None, ge=0)
    sent_at: datetime


class HealthResponse(DogidoModel):
    """GET /healthz のレスポンス。Kubernetes / Docker ヘルスチェック用。"""
    ok: bool
    service: str
    version: str


class StateResponse(DogidoModel):
    """イベント受付レスポンスに含まれる現在のドギドの状態サマリ。"""
    mode: str
    combat_active: bool


class OutputFlags(DogidoModel):
    """イベント処理結果として何が出力キューに積まれたかを示すフラグ。

    デバッグ・テスト時に「この入力でパニック悲鳴が出たか」を確認するために使う。
    """
    panic_cue_enqueued: bool = False
    callout_enqueued: bool = False
    speech_enqueued: bool = False


class AcceptedEventResponse(DogidoModel):
    """POST /api/v1/game-events のレスポンス。

    deduplicated=True: 同一イベントとして扱われたため再処理をスキップした
    state: 処理後のドギドの状態（デバッグ・フロントエンド表示用）
    outputs: 何の音声出力が発生したか（デバッグ用）
    """
    accepted: bool
    event_id: str
    session_id: str
    sequence: int | None = None
    deduplicated: bool = False
    state: StateResponse | None = None
    outputs: OutputFlags | None = None
    server_time: datetime


class BatchAcceptedResponse(DogidoModel):
    """POST /api/v1/game-events/batch のレスポンス。"""
    accepted: bool
    received: int  # 受け取ったイベント総数
    processed: int  # 実際に処理したイベント数
    deduplicated: int  # 重複としてスキップしたイベント数
    server_time: datetime


class HeartbeatResponse(DogidoModel):
    """ハートビートレスポンス。"""
    ok: bool
    session_id: str
    server_time: datetime


class CloseSessionResponse(DogidoModel):
    """DELETE /api/v1/adapter-sessions/{session_id} のレスポンス。"""
    ok: bool
    session_id: str


class StateSummary(DogidoModel):
    """内部用の状態サマリモデル。

    現時点では明示的な利用箇所はないが、サービス内部で
    状態と出力アクションをまとめて扱う用途を想定して残している。
    actions: state_machine が生成した AudioAction 相当のアクションリスト
    """
    mode: str
    combat_active: bool
    actions: list[dict[str, Any]] = Field(default_factory=list)
