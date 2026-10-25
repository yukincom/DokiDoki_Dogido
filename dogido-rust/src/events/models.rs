//! Fabricアダプターから送信される観測JSONを、戦闘・環境・対話の各エンジンが共通して参照するRust型へとマッピングするイベント契約。
//! 各build関数が生成する部分観測データをEventDataへと集約し、`GameEvent::parse` において型・数値範囲・セマンティクス（意味論）を網羅的に検査する。
//! 数値はJSON number、真偽値はJSON booleanとして受け、文字列や別型からは変換しない。
//! フィールドが省略された場合やnullの場合は `Option::None` となる。真偽値や配列のデフォルト値から送信の有無を逆算することはできない。
//! 「配列が空であること」を「ゲーム世界全体に存在しないこと」と即断するかどうかはこの型定義層では判定せず、利用側（各ドメインエンジン）がイベント種別と観測の鮮度に基づいて判断する。
//! 大半の型は `extra` フィールドに未知のキーを許容・保持するが、匂い関連の型は厳密に閉じた契約として未知キーを明示的に拒否する。
//! 本定義を変更する際は、Fabric側の `DogidoClientAdapter` / `DogidoCommandProtocol`、ドキュメント `docs/event-schema.md`、およびテスト検証コードを同期して整合性を維持すること。
use super::{Validate, ensure, wire};
use chrono::{DateTime, FixedOffset};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
/// Fabricから結果を報告できる操作の種類。発行済み命令の種類との一致はassistで検査する。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum AdapterCommandResultCommandType {
    #[serde(rename = "select_hotbar")]
    SelectHotbar,
    #[serde(rename = "recall_horse")]
    RecallHorse,
    #[serde(rename = "inspect_body_target")]
    InspectBodyTarget,
    #[serde(rename = "body_request")]
    BodyRequest,
    #[serde(rename = "capture_screenshot")]
    CaptureScreenshot,
    #[serde(rename = "capture_body_view")]
    CaptureBodyView,
}
/// Fabricでの操作結果。成功、実行前の拒否、実行失敗、期限切れを区別し、
/// Rustのassistが同じcommand_idの結果として受領する。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum AdapterCommandResultStatus {
    #[serde(rename = "succeeded")]
    Succeeded,
    #[serde(rename = "rejected")]
    Rejected,
    #[serde(rename = "failed")]
    Failed,
    #[serde(rename = "expired")]
    Expired,
}
/// プレイヤーの向きによらない八方位。視覚脅威などの方角に使う。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum CardinalDirection {
    #[serde(rename = "north")]
    North,
    #[serde(rename = "northeast")]
    Northeast,
    #[serde(rename = "east")]
    East,
    #[serde(rename = "southeast")]
    Southeast,
    #[serde(rename = "south")]
    South,
    #[serde(rename = "southwest")]
    Southwest,
    #[serde(rename = "west")]
    West,
    #[serde(rename = "northwest")]
    Northwest,
}
/// アダプターが付与する観測の確信度。イベント全体、および個別の音響・Mob検出に設定される。
/// `high` は送信元（クライアントアダプター）側の判定根拠の強さを示す区分であり、Rust側での発話判断や操作検証を省略してよいフラグではない。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Certainty {
    #[serde(rename = "low")]
    Low,
    #[serde(rename = "medium")]
    Medium,
    #[serde(rename = "high")]
    High,
}
/// 音源までの推定距離を大まかに丸めたバンド区分。
/// Fabric側の `bucketDistance` により、ブロック単位のユークリッド距離が 1.5以下、4以下、8以下、16以下、それ超の5段階に量子化される。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum DistanceBand {
    #[serde(rename = "touching")]
    Touching,
    #[serde(rename = "very_close")]
    VeryClose,
    #[serde(rename = "close")]
    Close,
    #[serde(rename = "mid")]
    Mid,
    #[serde(rename = "far")]
    Far,
}
/// Fabricから送信される定期スナップショットと、敵接近・音響検出・死亡・戦闘終了などの状態変化通知イベントの種別。
/// 特定のイベント名が到着したからといって観測全項目の最新取得が保証されるわけではないため、利用側（各エンジン）が更新範囲を制御する。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum EventName {
    #[serde(rename = "threat_approaching")]
    ThreatApproaching,
    #[serde(rename = "hostile_audio_detected")]
    HostileAudioDetected,
    #[serde(rename = "ambient_mob_detected")]
    AmbientMobDetected,
    #[serde(rename = "player_died")]
    PlayerDied,
    #[serde(rename = "hostile_defeated")]
    HostileDefeated,
    #[serde(rename = "creeper_detonated")]
    CreeperDetonated,
    #[serde(rename = "combat_ended")]
    CombatEnded,
    #[serde(rename = "status_snapshot")]
    StatusSnapshot,
}
/// 観測時のプレイヤー視線に対する前後左右の八方向。固定方位はCardinalDirectionを使う。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum HorizontalDirection {
    #[serde(rename = "front")]
    Front,
    #[serde(rename = "front_right")]
    FrontRight,
    #[serde(rename = "right")]
    Right,
    #[serde(rename = "back_right")]
    BackRight,
    #[serde(rename = "back")]
    Back,
    #[serde(rename = "back_left")]
    BackLeft,
    #[serde(rename = "left")]
    Left,
    #[serde(rename = "front_left")]
    FrontLeft,
}
/// 敵の死亡・爆散を裏付けた取得経路。統合サーバー死亡通知、
/// クライアント死亡状態、実爆発packetを区別して戦闘結果へ渡す。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum HostileOutcomeEvidence {
    #[serde(rename = "server_death_event")]
    ServerDeathEvent,
    #[serde(rename = "client_death_state")]
    ClientDeathState,
    #[serde(rename = "explosion_packet")]
    ExplosionPacket,
}
/// 確認できた敵の最終結果。プレイヤー撃破、爆発死、その他死亡、
/// クリーパー自身の爆散を分け、復帰時の言い回しや再通知防止に使う。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum HostileOutcomeOutcome {
    #[serde(rename = "player_kill")]
    PlayerKill,
    #[serde(rename = "explosion_death")]
    ExplosionDeath,
    #[serde(rename = "other_death")]
    OtherDeath,
    #[serde(rename = "creeper_detonation")]
    CreeperDetonation,
}
/// FabricのweaponKindが装備タグなどから求める道具分類。
/// Rustの剣選択候補を絞る材料で、分類だけで持ち替えを実行するものではない。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum HotbarSlotWeaponKind {
    #[serde(rename = "empty")]
    Empty,
    #[serde(rename = "sword")]
    Sword,
    #[serde(rename = "trident")]
    Trident,
    #[serde(rename = "axe")]
    Axe,
    #[serde(rename = "bow")]
    Bow,
    #[serde(rename = "tool")]
    Tool,
    #[serde(rename = "other")]
    Other,
}
/// 非敵対観測中のMobが友好か中立かを示す。会話での接し方と注意理由の扱いに使う。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PassiveMobTemperament {
    #[serde(rename = "passive")]
    Passive,
    #[serde(rename = "neutral")]
    Neutral,
}
/// Fabricがイベントの緊急度を添える区分。実際の警告・発話順はRustの状態と優先規則が決める。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PriorityHint {
    #[serde(rename = "critical")]
    Critical,
    #[serde(rename = "urgent")]
    Urgent,
    #[serde(rename = "normal")]
    Normal,
    #[serde(rename = "background")]
    Background,
}
/// 実際に壊したブロックを石・土・鉱石・その他へまとめた川柳・採掘文脈用の分類。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum RecentBlockBreakMaterial {
    #[serde(rename = "stone")]
    Stone,
    #[serde(rename = "earth")]
    Earth,
    #[serde(rename = "ore")]
    Ore,
    #[serde(rename = "other")]
    Other,
}
/// FabricのSmellPolicyで解決済みの匂いであることを表す契約版。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SmellObservationBasis {
    #[serde(rename = "smell_policy_v2")]
    SmellPolicyV2,
}
/// 匂いを腐敗・食物・花などの種類へまとめる。各成分の属性として保持する。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SmellObservationCategory {
    #[serde(rename = "decay")]
    Decay,
    #[serde(rename = "compost")]
    Compost,
    #[serde(rename = "brewing")]
    Brewing,
    #[serde(rename = "swamp")]
    Swamp,
    #[serde(rename = "food")]
    Food,
    #[serde(rename = "flower")]
    Flower,
    #[serde(rename = "ink")]
    Ink,
    #[serde(rename = "rain_after")]
    RainAfter,
}
/// 嗅覚で識別した具体的な匂いの種類。混合しても各種類を保持する。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum SmellObservationSmellId {
    #[serde(rename = "zombie")]
    Zombie,
    #[serde(rename = "rotten_flesh")]
    RottenFlesh,
    #[serde(rename = "composter")]
    Composter,
    #[serde(rename = "brewing_stand")]
    BrewingStand,
    #[serde(rename = "swamp")]
    Swamp,
    #[serde(rename = "raw_meat")]
    RawMeat,
    #[serde(rename = "raw_fish")]
    RawFish,
    #[serde(rename = "cooked_meat")]
    CookedMeat,
    #[serde(rename = "cooked_fish")]
    CookedFish,
    #[serde(rename = "cooking_meat")]
    CookingMeat,
    #[serde(rename = "cooking_fish")]
    CookingFish,
    #[serde(rename = "soup")]
    Soup,
    #[serde(rename = "cookie")]
    Cookie,
    #[serde(rename = "cake")]
    Cake,
    #[serde(rename = "bread")]
    Bread,
    #[serde(rename = "ink_sac")]
    InkSac,
    #[serde(rename = "lily_of_the_valley")]
    LilyOfTheValley,
    #[serde(rename = "lilac")]
    Lilac,
    #[serde(rename = "peony")]
    Peony,
    #[serde(rename = "rose_bush")]
    RoseBush,
    #[serde(rename = "wither_rose")]
    WitherRose,
    #[serde(rename = "cactus_flower")]
    CactusFlower,
    #[serde(rename = "flowering_azalea")]
    FloweringAzalea,
    #[serde(rename = "allium")]
    Allium,
    #[serde(rename = "pitcher_plant")]
    PitcherPlant,
    #[serde(rename = "torchflower")]
    Torchflower,
    #[serde(rename = "open_eyeblossom")]
    OpenEyeblossom,
    #[serde(rename = "rain_after")]
    RainAfter,
}
/// 匂いの候補を得た場所。実体・ブロック・手持ち・落下物・バイオームを区別する。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SmellObservationSourceKind {
    #[serde(rename = "entity")]
    Entity,
    #[serde(rename = "block")]
    Block,
    #[serde(rename = "hotbar")]
    Hotbar,
    #[serde(rename = "dropped_item")]
    DroppedItem,
    #[serde(rename = "biome")]
    Biome,
}
/// 匂いの解決結果。noneは残った候補なし、presentは解決済み、
/// suppressedは雨・雪・雷・水中による抑止を示す。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SmellObservationStatus {
    #[serde(rename = "none")]
    None,
    #[serde(rename = "present")]
    Present,
    #[serde(rename = "suppressed")]
    Suppressed,
}
/// SmellPolicyが嗅覚観測を抑止した環境理由。suppressedのときだけ付ける。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SmellObservationSuppressionReason {
    #[serde(rename = "rain")]
    Rain,
    #[serde(rename = "snow")]
    Snow,
    #[serde(rename = "thunder")]
    Thunder,
    #[serde(rename = "submerged")]
    Submerged,
}
/// SmellPolicyが対象へ割り当てる快・不快・混合の区分。発話トーンの材料にする。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SmellObservationValence {
    #[serde(rename = "pleasant")]
    Pleasant,
    #[serde(rename = "unpleasant")]
    Unpleasant,
    #[serde(rename = "mixed")]
    Mixed,
}
/// イベントの契機となった根拠を視覚・聴覚・推定・システムに分ける。個別観測の根拠は各要素にも残る。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SourceKind {
    #[serde(rename = "visual")]
    Visual,
    #[serde(rename = "auditory")]
    Auditory,
    #[serde(rename = "inferred")]
    Inferred,
    #[serde(rename = "system")]
    System,
}
/// FabricのclassifyTimePhaseが一日のtickから求める朝・昼・夕方・夜の区分。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum TimePhase {
    #[serde(rename = "morning")]
    Morning,
    #[serde(rename = "day")]
    Day,
    #[serde(rename = "evening")]
    Evening,
    #[serde(rename = "night")]
    Night,
}
/// 乗車中の活動。Fabricは速度、操作席、パドルやダッシュ状態から
/// 移動・走行・漕ぎ・ダッシュを選び、それ以外の乗車をridingとする。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum VehicleStateActivity {
    #[serde(rename = "riding")]
    Riding,
    #[serde(rename = "moving")]
    Moving,
    #[serde(rename = "running")]
    Running,
    #[serde(rename = "rowing")]
    Rowing,
    #[serde(rename = "dashing")]
    Dashing,
}
/// 観測対象がプレイヤーより上・同程度・下のどこにあるかを示す粗い関係。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum VerticalRelation {
    #[serde(rename = "above")]
    Above,
    #[serde(rename = "same")]
    Same,
    #[serde(rename = "below")]
    Below,
}
/// Fabricのworld天候フラグに対応する晴れ・雨・雷。
/// 局所的な降雪や積雪はこのenumだけでは表さず、降水判定側が他の実測も合わせる。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Weather {
    #[serde(rename = "clear")]
    Clear,
    #[serde(rename = "rain")]
    Rain,
    #[serde(rename = "thunder")]
    Thunder,
}
/// PortalObservationTrackerが前回のブロック観測と今回の近接範囲を比較した結果。
/// appearedは見えている場所での出現、arrivedは既存ポータルへの接近、observedは出現を視認できない観測。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum WorldStateNearbyPortalEncounter {
    #[serde(rename = "appeared")]
    Appeared,
    #[serde(rename = "arrived")]
    Arrived,
    #[serde(rename = "observed")]
    Observed,
}
/// DogidoCommandProtocol.commandResultが作る、型付き支援命令の実行結果。
/// DogidoEventClientが未ACKの結果をイベントへ添付し、Rustのassistがcommand_idで受領する。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AdapterCommandResult {
    /// 一回生成だけの画像。JSON履歴や評価記録へは書き出さない。
    #[serde(default, skip_serializing)]
    pub body_view: Option<BodyViewPayload>,
    /// Rustが発行した命令ID。同じ命令の再配送と結果ACKを対応させる。
    pub command_id: String,
    pub command_type: AdapterCommandResultCommandType,
    pub status: AdapterCommandResultStatus,
    /// Fabricが成功・拒否・失敗・期限切れの結果を作った時刻。
    #[serde(
        deserialize_with = "wire::deserialize_datetime",
        serialize_with = "wire::serialize_datetime"
    )]
    pub executed_at: DateTime<FixedOffset>,
    /// 結果時点で確認できた選択slot（0〜8）。確認できない場合は省略またはnull。
    #[serde(default)]
    pub selected_slot: Option<i64>,
    /// 結果時点の主手itemの名前空間付きID。確認できない場合はNone。
    #[serde(default)]
    pub selected_item_id: Option<String>,
    #[serde(default = "default_adaptercommandresult_detail_code")]
    pub detail_code: String,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// 不正な画像で世界観測全体を拒否しないため、要求受信時に内容を検証する。
#[derive(Clone, Deserialize)]
pub struct BodyViewPayload(pub Value);
impl std::fmt::Debug for BodyViewPayload {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("BodyViewPayload(<redacted>)")
    }
}
/// buildAmbientSoundsが送る非敵対Mob・環境・ブロックなどの聴覚観測一件。
/// 音の種類、根拠ID、経過時間を会話へ渡し、現在見えている対象とは分けて扱う。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AmbientSound {
    pub r#type: String,
    /// 同じ音源を追跡するadapter由来の識別子。entity UUIDとは限らず、省略可能。
    #[serde(default)]
    pub source_id: Option<String>,
    /// 音源を実在Mobへ対応できた場合の個体情報。環境音などではNone。
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub identity: Option<MobIdentity>,
    /// 実際に受けた／再生されたMinecraft sound event ID。未送信ならNone。
    #[serde(default)]
    pub sound_event: Option<String>,
    #[serde(default)]
    pub direction: Direction,
    #[serde(default)]
    pub distance_band: Option<DistanceBand>,
    #[serde(default = "default_ambientsound_certainty")]
    pub certainty: Certainty,
    /// その音を観測してからのミリ秒。Fabricはtick差×50で計算し、未送信ならNone。
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub heard_ago_ms: Option<i64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildAuditoryThreatsが送る敵対音の観測一件。
/// 元のsound_eventと粗い方向・距離・鮮度を保ち、Rustの警告と聴覚への質問に使う。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AuditoryThreat {
    /// adapterが音源情報から対応させた敵のラベル。
    pub label: String,
    #[serde(default)]
    pub source_id: Option<String>,
    #[serde(default)]
    pub sound_event: Option<String>,
    #[serde(default)]
    pub direction: Direction,
    #[serde(default)]
    pub distance_band: Option<DistanceBand>,
    #[serde(default = "default_auditorythreat_certainty")]
    pub certainty: Certainty,
    /// 音だけでも種名を発話してよいというadapterの根拠区分。省略時はfalse。
    #[serde(default)]
    pub spoken_name_allowed: bool,
    /// 元の音観測からのミリ秒。イベントの受信時刻とは別で、同じ古い音を新鮮に扱わない。
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub heard_ago_ms: Option<i64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildCombatが実観測とadapter内の追跡状態からまとめる戦況補助情報。
/// 通常の敵数・直近の被弾と、確認できた死亡結果、ボス固有情報を併せて渡す。
/// Optionの省略は情報なしであり、敵数0や撃破falseを実測したことにはしない。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CombatState {
    /// 最後の体力減少からの経過ミリ秒。Fabricのtick差×50で、Noneは情報なし。
    #[serde(default)]
    pub recent_damage_ms: Option<i64>,
    /// 最後の視覚脅威観測からの経過ミリ秒。Noneは時刻情報なし。
    #[serde(default)]
    pub recent_hostile_visual_ms: Option<i64>,
    /// 最後の敵対音観測からの経過ミリ秒。Noneは時刻情報なし。
    #[serde(default)]
    pub recent_hostile_audio_ms: Option<i64>,
    /// adapterの脅威リスト内で距離7ブロック以内にいる数。
    #[serde(default)]
    pub hostiles_within_7: Option<i64>,
    /// adapterの脅威リスト内で距離10ブロック以内にいる数。
    #[serde(default)]
    pub hostiles_within_10: Option<i64>,
    /// 敵数集計に用いた探索距離。単位はブロック。
    #[serde(default)]
    pub hostile_scan_distance: Option<f64>,
    /// 探索距離内から飛行敵の種類を除いた数。接地している個体だけの数ではない。
    #[serde(default)]
    pub hostiles_within_scan_ground: Option<i64>,
    /// adapterの戦闘追跡または現在の敵視覚・聴覚が示す継続候補。Rustの最終戦況とは別。
    #[serde(default)]
    pub combat_active_hint: Option<bool>,
    /// 確認済み死亡・爆散の記録。Noneは未送信、Some([])は送信された空リスト。
    #[serde(default)]
    pub hostile_outcomes: Option<Vec<HostileOutcome>>,
    #[serde(default)]
    pub warden_recently_hurt: Option<bool>,
    /// adapterが追跡対象の死亡状態を確認したウォーデン撃破情報。Noneは情報なし。
    #[serde(default)]
    pub warden_defeat_confirmed: Option<bool>,
    #[serde(default)]
    pub warden_ranged_trap_active: Option<bool>,
    #[serde(default)]
    pub warden_nearby_iron_golem_count: Option<i64>,
    /// 近接する経験値orbの数。これ単独では誰が何を倒したかは表さない。
    #[serde(default)]
    pub nearby_experience_orb_count: Option<i64>,
    #[serde(default)]
    pub warden_end_crystal_bombardment_active: Option<bool>,
    #[serde(default)]
    pub warden_nearby_end_crystal_count: Option<i64>,
    #[serde(default)]
    pub warden_tnt_minecart_setup_active: Option<bool>,
    #[serde(default)]
    pub warden_nearby_tnt_minecart_count: Option<i64>,
    #[serde(default)]
    pub dragon_phase: Option<String>,
    /// 追跡中ドラゴンまでの距離（ブロック）。未追跡時などはNone。
    #[serde(default)]
    pub dragon_distance: Option<f64>,
    #[serde(default)]
    pub dragon_horizontal: Option<String>,
    #[serde(default)]
    pub dragon_vertical: Option<String>,
    /// adapterが追跡対象の死亡状態を確認したドラゴン撃破情報。Noneは情報なし。
    #[serde(default)]
    pub dragon_defeat_confirmed: Option<bool>,
    #[serde(default)]
    pub end_crystal_count: Option<i64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
impl Default for CombatState {
    fn default() -> Self {
        serde_json::from_str("{}").expect("model defaults")
    }
}
/// FabricのclassifyHorizontal/Cardinal/Verticalが求める対象方向。
/// 各軸は独立に省略でき、空のDirectionは方向を取得していない観測を表す。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Direction {
    /// プレイヤーの視線に対する方向。Noneならこの軸を未取得。
    #[serde(default)]
    pub horizontal: Option<HorizontalDirection>,
    /// ワールドの固定八方位。相対方向だけの聴覚観測などではNone。
    #[serde(default)]
    pub cardinal: Option<CardinalDirection>,
    /// プレイヤーとの上下関係。Noneとsameを区別する。
    #[serde(default)]
    pub vertical: Option<VerticalRelation>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
impl Default for Direction {
    fn default() -> Self {
        serde_json::from_str("{}").expect("model defaults")
    }
}
/// buildDroppedItemsが近接するItemEntityをitem IDごとに合算した観測。
/// 個数は合計、距離と経過時間はその種類の最小値で、採掘や川柳の材料に使う。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DroppedItem {
    /// Fabricはitemのregistry IDから名前空間を除いたpathを送る。
    pub name: String,
    /// 同種類のItemEntityが持つstack個数の合計。1以上。
    pub count: i64,
    /// 合算したItemEntityの数。個数とは別で、省略時の互換値は1。
    #[serde(default = "default_droppeditem_entity_count")]
    pub entity_count: i64,
    /// 同種類の最寄りItemEntityまでの距離（ブロック）。未送信ならNone。
    #[serde(default)]
    pub distance: Option<f64>,
    /// 同種類の最も新しいItemEntityの経過時間。Fabricのitem age tick×50ミリ秒。
    #[serde(default)]
    pub age_ms: Option<i64>,
    #[serde(default)]
    pub block_item: bool,
    /// item・ブロック種類による採掘材料候補。実際に今回掘って得たことの証拠ではない。
    #[serde(default)]
    pub mining_related: bool,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// baseEnvelopeが付けるイベントの契機・根拠・優先度の記述。状態判断は本文の観測と合わせて行う。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EventDescriptor {
    pub name: EventName,
    pub source_kind: SourceKind,
    /// 送信側が付けた候補優先度。Rustは戦況と発話状態を合わせて実際の優先順位を決める。
    pub priority_hint: PriorityHint,
    pub certainty: Certainty,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildHostileOutcomesが送る、個体IDと証拠を伴う敵の死亡・爆散結果。
/// 視界から消えたという推定と区別し、Rustで同じ個体への結果発話を管理する。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HostileOutcome {
    pub r#type: String,
    /// 死亡・爆散した個体のUUID文字列。再通知防止と保持中の敵との照合に使う。
    pub entity_id: String,
    pub outcome: HostileOutcomeOutcome,
    /// outcomeを成立させた通知経路。消失や経験値だけで補った結果ではない。
    pub evidence: HostileOutcomeEvidence,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildHotbarが送る一つのhotbar slotの内容。
/// Rustの剣選択はこの現在snapshotのitem・耐久値などを照合して候補を作る。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HotbarSlot {
    /// hotbarの0始まりの枠番号（0〜8）。画面上の1〜9キーと1ずれる。
    pub slot: i64,
    /// 名前空間付きitem ID。現Fabricは空枠で省略する。
    #[serde(default)]
    pub item_id: Option<String>,
    /// この枠のstack個数。空枠および省略時は0。
    #[serde(default)]
    pub count: i64,
    /// 消費済みの耐久値。残り耐久値ではなく、max_damageとの差で残量を求める。
    #[serde(default)]
    pub damage: i64,
    /// itemの最大耐久値。Fabricの空枠では0。
    #[serde(default)]
    pub max_damage: i64,
    /// 主手装備の属性補正をプレイヤー基礎値へ適用した攻撃力。空枠では省略。
    #[serde(default)]
    pub attack_damage: Option<f64>,
    #[serde(default = "default_hotbarslot_weapon_kind")]
    pub weapon_kind: HotbarSlotWeaponKind,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildHotbarが送る選択中slotと最大9枠の内容。
/// slot番号は0始まりで重複不可。省略されたslotsは空配列として受ける。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HotbarState {
    /// 現在選択されている0始まりのslot番号（0〜8）。
    pub selected_slot: i64,
    /// 最大9枠。現Fabricは空枠も送り、受信側は番号の重複をsemanticで拒否する。
    #[serde(default)]
    pub slots: Vec<HotbarSlot>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildLookTargetがクロスヘアの命中先から作るブロックまたはentityの観測。
/// MISS・空気・生存していないentityでは、FabricはEventData.look_targetごと省略する。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LookTarget {
    /// 現Fabricはblockまたはentityを送る。省略時の互換値はblock。
    #[serde(default = "default_looktarget_kind")]
    pub kind: String,
    /// 命中したブロック／entity種類のregistry IDのpath。
    pub name: String,
    /// entityへ命中したときの個体情報。ブロック命中ではNone。
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub identity: Option<MobIdentity>,
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub environment: Option<MobEnvironment>,
    /// プレイヤーから対象までの距離（ブロック）。ブロックはその中心までを測る。
    #[serde(default)]
    pub distance: Option<f64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildMetaが観測へ添えるadapter情報と死亡理由。プレイヤー入力は専用の受付へ送る。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MetaState {
    #[serde(default)]
    pub adapter_build: Option<String>,
    #[serde(default)]
    pub profile_name: Option<String>,
    #[serde(default)]
    pub call_name: Option<String>,
    #[serde(default)]
    pub debug: bool,
    /// player_diedなどで添える、取得できた死亡理由。省略時に原因を推定しない。
    #[serde(default)]
    pub death_cause: Option<String>,
    /// 進捗ID用の受信欄。現FabricのbuildMetaは送らず、既定は空配列。
    #[serde(default)]
    pub advancements: Vec<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
impl Default for MetaState {
    fn default() -> Self {
        serde_json::from_str("{}").expect("model defaults")
    }
}
/// MobEnvironmentObservation.captureが一個体から得る接水・水没・雨・接地・炎上の現在値。
/// player、視覚脅威、非敵対Mob、視線先で共用する。Noneはその項目の実測なし。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MobEnvironment {
    /// entity.isTouchingWater()の実測。水没とは別の接水状態。
    #[serde(default)]
    pub touching_water: Option<bool>,
    #[serde(default)]
    pub submerged_in_water: Option<bool>,
    /// entity.isTouchingWaterOrRain()の実測。falseでも他の生存条件は保証しない。
    #[serde(default)]
    pub touching_water_or_rain: Option<bool>,
    /// 同じ個体の足元・身体上端でworld.hasRainを照合した現在の雨への接触。雪や濡れの残留は表さない。
    #[serde(default)]
    pub touching_rain: Option<bool>,
    /// entity.isInLava()の現在実測。身体感覚では同じ姿勢の箱と溶岩面を照合する。近隣熱源・炎上とは別。
    #[serde(default)]
    pub touching_lava: Option<bool>,
    #[serde(default)]
    pub on_ground: Option<bool>,
    #[serde(default)]
    pub on_fire: Option<bool>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
impl Default for MobEnvironment {
    fn default() -> Self {
        serde_json::from_str("{}").expect("model defaults")
    }
}
/// FabricのmobIdentityが個体UUIDと任意の名札・手懐け状態を束ねた識別情報。
/// 同種でも別個体を区別し、固有名の呼び方や対象追跡へ使う。
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MobIdentity {
    /// 個体UUIDの文字列。種別IDではない。
    pub entity_id: String,
    /// 設定されている名札。Noneは名札情報なしで、種別名を代入しない。
    #[serde(default)]
    pub custom_name: Option<String>,
    /// 手懐け可能な個体の実状態。省略時の互換値はfalse。
    #[serde(default)]
    pub tamed: bool,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildNearbyResourcesが周辺から選ぶ資源候補。露出した対象ブロックや羊の毛色を
/// 種類ごとに最寄り距離へまとめ、会話・川柳で周囲の材料として使う。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NearbyResource {
    /// 現Fabricの候補元はblockまたはmob。mobは羊の毛色などを資源へ投影する。
    pub r#type: String,
    pub name: String,
    /// 同じ資源名で最寄りの候補までの距離（ブロック）。未送信ならNone。
    #[serde(default)]
    pub distance: Option<f64>,
    /// 省略時は各軸None。現FabricのbuildNearbyResourcesは距離までを送る。
    #[serde(default)]
    pub direction: Direction,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildPassiveMobsが送る現在の非敵対Mobの観測。
/// 種別・個体情報・環境に加え、中立性や村人の職業など取得できた特徴を添える。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PassiveMob {
    pub r#type: String,
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub identity: Option<MobIdentity>,
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub environment: Option<MobEnvironment>,
    /// プレイヤーから個体までの距離（ブロック）。未送信ならNone。
    #[serde(default)]
    pub distance: Option<f64>,
    #[serde(default)]
    pub direction: Direction,
    #[serde(default = "default_passivemob_certainty")]
    pub certainty: Certainty,
    /// 取得できた友好／中立の区分。Noneは分類情報なし。
    #[serde(default)]
    pub temperament: Option<PassiveMobTemperament>,
    #[serde(default)]
    pub caution_reason: Option<String>,
    #[serde(default)]
    pub is_baby: Option<bool>,
    /// 村人の職業ID。村人以外や取得できない場合は省略される。
    #[serde(default)]
    pub profession: Option<String>,
    /// 村人の外見に対応するタイプID。現在地のバイオームとは別。
    #[serde(default)]
    pub villager_type: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildPlayerが送るプレイヤーの位置・状態・主手・hotbar・乗車のsnapshot。
/// 各Optionが未送信ならNoneを保ち、体力0や原点座標などへ読み替えない。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlayerState {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub environment: Option<MobEnvironment>,
    #[serde(default)]
    pub position: Position,
    /// Minecraftが返す水平向きの角度（度）。Noneは角度情報なし。
    #[serde(default)]
    pub yaw: Option<f64>,
    /// Minecraftが返す上下向きの角度（度）。Noneは角度情報なし。
    #[serde(default)]
    pub pitch: Option<f64>,
    /// ゲームの体力値。ハートの表示個数ではなく、Noneは体力未取得。
    #[serde(default)]
    pub health: Option<f64>,
    /// 自由落下・登攀・滑空・飛行の現在実測。Noneは未測定で、座標差から補完しない。
    #[serde(default)]
    pub motion: Option<PlayerMotion>,
    /// player abilitiesの無敵、またはspectatorの実測。欠測を無敵なしへ補完しない。
    #[serde(default)]
    pub invulnerable: Option<bool>,
    /// MinecraftのFoodLevel値。Noneは空腹度未取得。
    #[serde(default)]
    pub hunger: Option<i64>,
    /// 名前空間付きdimension ID。座標を比較する際に同じ世界かを区別する。
    #[serde(default)]
    pub dimension: Option<String>,
    /// 主手itemの名前空間付きID。現Fabricは空手ならminecraft:air、未送信ならNone。
    #[serde(default)]
    pub held_item: Option<String>,
    #[serde(default)]
    pub block_breaking_active: Option<bool>,
    /// 選択操作の照合に使う現在snapshot。Noneならこのイベントからslotを確認できない。
    #[serde(default)]
    pub hotbar: Option<HotbarState>,
    /// 現Fabricは乗車中だけ送る。未乗車時はキーを省略し、Noneから乗り物の活動を補わない。
    #[serde(default)]
    pub vehicle: Option<VehicleState>,
    /// 現在かかっている効果IDのpath一覧。省略時は空配列。
    #[serde(default)]
    pub active_status_effects: Vec<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// 同じplayer snapshotの移動状態。距離はblock、垂直速度はblock/tick（負が下降）。
/// 行動の意図、着地位置、落下ダメージの有無はこの契約から確定しない。
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlayerMotion {
    #[serde(default)]
    pub fall_distance: Option<f64>,
    #[serde(default)]
    pub vertical_velocity: Option<f64>,
    #[serde(default)]
    pub climbing: Option<bool>,
    #[serde(default)]
    pub gliding: Option<bool>,
    #[serde(default)]
    pub flying: Option<bool>,
}
impl Validate for PlayerMotion {
    fn validate(&self) -> Result<(), String> {
        ensure(
            self.fall_distance.is_none_or(|v| v.is_finite() && v >= 0.0),
            "PlayerMotion.fall_distance must be finite and nonnegative",
        )?;
        ensure(
            self.vertical_velocity.is_none_or(f64::is_finite),
            "PlayerMotion.vertical_velocity must be finite",
        )
    }
}
impl Default for PlayerState {
    fn default() -> Self {
        serde_json::from_str("{}").expect("model defaults")
    }
}
/// buildPlayerがゲーム座標を小数第2位に丸めて送る位置。単位はブロック。
/// 軸ごとのNoneは未取得であり、0座標とは区別する。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Position {
    #[serde(default)]
    pub x: Option<f64>,
    #[serde(default)]
    pub y: Option<f64>,
    #[serde(default)]
    pub z: Option<f64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
impl Default for Position {
    fn default() -> Self {
        serde_json::from_str("{}").expect("model defaults")
    }
}
/// ClientPlayerBlockBreakEvents.AFTERで記録し、buildRecentBlockBreaksが送る直近の実破壊。
/// 採掘文脈を所持品だけから推定せず、この記録とその鮮度を材料にする。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RecentBlockBreak {
    /// 実際に壊したブロックのregistry IDのpath。
    pub name: String,
    #[serde(default = "default_recentblockbreak_material")]
    pub material: RecentBlockBreakMaterial,
    /// 破壊記録からのtick差×50ミリ秒。省略時の通信上の既定値は0。
    #[serde(default)]
    pub age_ms: i64,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// Fabricで到達・マスキングを解決した匂いの一覧。旧flat形式は受信しない。
/// componentsとbasisに既定値を付けないことで、旧adapterの入力を黙って補完しない。
/// enumとdeny_unknown_fieldsで外形を閉じ、状態と成分の整合性はsemantic側で照合する。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SmellObservation {
    /// noneは観測して匂いなし、suppressedは天候等で嗅げない。field自体の欠落は別の未提供状態。
    pub status: SmellObservationStatus,
    /// 個体の一覧ではなく匂いの種類一覧。同じIDを複数入れて数や強度を加算できない。
    pub components: Vec<SmellComponent>,
    /// 気温由来の補正点（-7〜2）。摂氏温度ではない。
    #[serde(default)]
    pub temperature_modifier: i64,
    /// Fabricの雨上がり観測窓。rain_after成分にはtrueが必要。
    #[serde(default)]
    pub rain_after_active: bool,
    /// 一度「あっ」と気づく候補。通常componentsを消さず、出力完了後はそちらへ戻す。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rain_after_notice: Option<RainAfterNotice>,
    #[serde(default)]
    pub suppression_reason: Option<SmellObservationSuppressionReason>,
    /// v2だけを受理する。旧basisや旧flat項目との併記も互換入力として扱わない。
    pub basis: SmellObservationBasis,
}
/// 同じ雨上がりを識別する窓IDと、実際に届いた一種類の気配。
/// window_idは配送完了の照合専用。LLMの世界観測へは投影しない。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RainAfterNotice {
    /// Fabricの正の整数をそのまま受ける。文字列・真偽値への互換変換は行わない。
    pub window_id: u64,
    pub component: SmellComponent,
}
/// 同種非加算で残った一種類。強度は距離・温度補正後のスメル値。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SmellComponent {
    /// 具体的な匂いID。ゾンビ系はzombieに集約し、混合全体を表すmixed IDは持たない。
    pub smell_id: SmellObservationSmellId,
    pub category: SmellObservationCategory,
    pub valence: SmellObservationValence,
    pub source_kind: SmellObservationSourceKind,
    /// 1は仄か、8は強烈。範囲1〜12で、物理的濃度や距離ではない。
    pub effective_strength: i64,
}
/// buildVehicleStateが乗車中だけ作る乗り物の種類・活動・操作席情報。
/// この構造体があるときに限り、会話材料でプレイヤーを乗車の主語にする。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VehicleState {
    /// 乗っているentity種類の名前空間付きID。個体UUIDではない。
    pub vehicle_id: String,
    #[serde(default = "default_vehiclestate_activity")]
    pub activity: VehicleStateActivity,
    /// その乗り物の操作席がプレイヤーか。単なる同乗と操作を区別する。
    #[serde(default)]
    pub controlling: bool,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildVisualThreatsが送る視覚経路の敵観測。個体IDと距離・接近・導火状態を
/// Rustの警告へ渡す。追跡保持中に遮られた対象はenvironmentを空にして最新実測と区別する。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VisualThreat {
    pub r#type: String,
    /// adapterが追跡する敵のUUID。省略時はこの欄だけでは個体を同定できない。
    #[serde(default)]
    pub entity_id: Option<String>,
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub identity: Option<MobIdentity>,
    /// 現在の視線が遮られている保持対象では、Fabricは空objectを送り全項目Noneにする。
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub environment: Option<MobEnvironment>,
    /// プレイヤーから対象までの距離（ブロック）。未送信ならNone。
    #[serde(default)]
    pub distance: Option<f64>,
    #[serde(default)]
    pub direction: Direction,
    /// adapterの追跡で距離が縮んでいるか。省略時の互換値はfalse。
    #[serde(default)]
    pub approaching: bool,
    /// クリーパーの導火状態。Noneは情報なしで、非作動を明示したfalseと区別する。
    #[serde(default)]
    pub fuse_active: Option<bool>,
    #[serde(default)]
    pub on_fire: bool,
    #[serde(default)]
    pub in_water: bool,
    #[serde(default = "default_visualthreat_certainty")]
    pub certainty: Certainty,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
/// buildWorldがプレイヤー周辺から求める時刻・天候・地形・暗所・住居・ポータルの観測。
/// visible_villager_countだけはattachPassiveMobsが表示用の件数制限前に補う。
/// ここには実測値とadapterの範囲限定の推定値が混在し、Optionの省略は取得なしを表す。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WorldState {
    /// ワールド時刻を24000で割った余り（tick）。現Fabricは昼夜周期のある次元だけ送る。
    #[serde(default)]
    pub time_of_day: Option<i64>,
    /// time_of_dayを朝昼夕夜へ分類した値。現Fabricは昼夜周期のない次元で省略する。
    #[serde(default)]
    pub time_phase: Option<TimePhase>,
    /// ワールドの晴れ／雨／雷フラグ。局所的な雪・水中・遮蔽とは別。
    #[serde(default)]
    pub weather: Option<Weather>,
    #[serde(default)]
    pub biome: Option<String>,
    /// 統合サーバーで現在位置を包含すると確認できた構造物IDのpath。取得不能時はNone。
    #[serde(default)]
    pub structure: Option<String>,
    /// 現在ブロックでのworld.getLightLevel()（0〜15）。明るさの比率ではない。
    #[serde(default)]
    pub local_light: Option<i64>,
    #[serde(default)]
    pub sky_visible: Option<bool>,
    /// 同じ水平座標のMOTION_BLOCKING_NO_LEAVES高さ（ブロック）。昼夜周期のある次元で送る。
    #[serde(default)]
    pub surface_y: Option<i64>,
    /// surface_yとプレイヤーのブロックYとの差を0以上にした深さ（ブロック）。
    #[serde(default)]
    pub depth_below_surface: Option<i64>,
    /// 上方の非開放ブロックまでの高さ（ブロック）。Fabricは24段で走査を打ち切り、未検出も24。
    #[serde(default)]
    pub ceiling_height: Option<f64>,
    /// True only when the upward scan found a ceiling; false means height is the search bound.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ceiling_observed: Option<bool>,
    #[serde(default)]
    pub overhead_cover_type: Option<String>,
    /// 現Fabricでは接水または水没をまとめた値。頭部まで水没したことだけを意味しない。
    #[serde(default)]
    pub is_submerged: Option<bool>,
    /// 接水・水没時に上へ走査した連続fluidブロック数（最大8）。Noneは未送信。
    #[serde(default)]
    pub submerged_depth_blocks: Option<i64>,
    /// player.getAir()の空気残量カウンタ。秒へ換算した値ではない。
    #[serde(default)]
    pub air_supply: Option<i64>,
    #[serde(default)]
    pub nearby_door_count: Option<i64>,
    #[serde(default)]
    pub open_door_count: Option<i64>,
    #[serde(default)]
    pub nearby_window_present: Option<bool>,
    #[serde(default)]
    pub nearby_bed_count: Option<i64>,
    #[serde(default)]
    pub nearby_sleeping_people_count: Option<i64>,
    #[serde(default)]
    pub drafty_opening_count: Option<i64>,
    /// このadapter接続中に周囲のベッドで睡眠開始を観測したか。全リスポーン設定の照会ではない。
    #[serde(default)]
    pub respawn_point_set: Option<bool>,
    /// 観測済みリスポーン位置までの距離（ブロック）。未観測・別次元などでは省略する。
    #[serde(default)]
    pub respawn_distance: Option<f64>,
    #[serde(default)]
    pub cardinal_wall_count: Option<i64>,
    #[serde(default)]
    pub double_height_open_side_count: Option<i64>,
    /// 扉・囲まれ方・天井などからadapterが推定する室内条件。危険全般の不在保証ではない。
    #[serde(default)]
    pub safe_zone_with_door: Option<bool>,
    /// 周辺の非開放ブロック比率と空の遮蔽から求める囲まれ度（0〜1）。
    #[serde(default)]
    pub enclosure_score: Option<f64>,
    /// adapterの走査範囲にある明るさ7以下の開放ブロック数。連結成分を探索した体積ではない。
    #[serde(default)]
    pub connected_dark_volume: Option<i64>,
    /// 近接走査中の暗い開放ブロックまでの最短距離（ブロック）。未検出はFabricで99。
    #[serde(default)]
    pub nearest_dark_spawn_distance: Option<f64>,
    /// 明るさ・囲まれ方・暗い領域などからadapterが求めた危険な暗さの指標（0〜1）。
    #[serde(default)]
    pub danger_darkness_score: Option<f64>,
    #[serde(default)]
    pub nearby_light_source_count: Option<i64>,
    /// 走査した照明器具までの最短距離（ブロック）。範囲内で未検出ならFabricは99を送る。
    #[serde(default)]
    pub nearest_light_source_distance: Option<f64>,
    #[serde(default)]
    pub nearby_damaging_light_source_count: Option<i64>,
    /// 走査した火・焚き火など有害光源までの最短距離（ブロック）。未検出はFabricで99。
    #[serde(default)]
    pub nearest_damaging_light_source_distance: Option<f64>,
    #[serde(default)]
    pub standing_on_magma_block: Option<bool>,
    #[serde(default)]
    pub nearby_firefly_bush_count: Option<i64>,
    #[serde(default)]
    pub ominous_sound_kind: Option<String>,
    /// 保持中の不穏音からの経過ミリ秒。Fabricは鮮度窓を超えるとkindとともに省略する。
    #[serde(default)]
    pub ominous_sound_recent_ms: Option<i64>,
    #[serde(default)]
    pub boss_omen_kind: Option<String>,
    /// 実際に観測した雨音からの経過ミリ秒。鮮度窓外ではFabricが省略する。
    #[serde(default)]
    pub rain_sound_recent_ms: Option<i64>,
    /// 実際に観測した雷鳴からの経過ミリ秒。鮮度窓外ではFabricが省略する。
    #[serde(default)]
    pub thunder_sound_recent_ms: Option<i64>,
    /// 近接落雷の観測からの経過ミリ秒。鮮度・距離条件を満たす場合に送る。
    #[serde(default)]
    pub nearby_lightning_strike_recent_ms: Option<i64>,
    /// 観測した近接落雷までの距離（ブロック）。Noneは今回の有効情報なし。
    #[serde(default)]
    pub nearby_lightning_strike_distance: Option<f64>,
    /// エンダーアイ投擲音の観測からの経過ミリ秒。鮮度窓外ではFabricが省略する。
    #[serde(default)]
    pub ender_eye_launch_recent_ms: Option<i64>,
    /// 近接走査で見つかったポータル種類。未検出時は関連三項目をFabricが省略する。
    #[serde(default)]
    pub nearby_portal_type: Option<String>,
    /// 近接ポータルまでの距離（ブロック）。Noneは有効な距離情報なし。
    #[serde(default)]
    pub nearby_portal_distance: Option<f64>,
    #[serde(default)]
    pub nearby_portal_encounter: Option<WorldStateNearbyPortalEncounter>,
    /// 非敵対Mobの送信上限4件を適用する前の村人数。送られたpassive_mobsの要素数とは異なる。
    #[serde(default)]
    pub visible_villager_count: Option<i64>,
    /// 検出できたエンドポータルフレームまでの距離（ブロック）。未検出ならNone。
    #[serde(default)]
    pub nearby_end_portal_frame_distance: Option<f64>,
    /// FabricのMinecraftClient.isPaused()。Noneはpause情報未送信で、Rust側の会話停止命令ではない。
    #[serde(default)]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub game_paused: Option<bool>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
impl Default for WorldState {
    fn default() -> Self {
        serde_json::from_str("{}").expect("model defaults")
    }
}
/// 友好・中立Mobの数量。5以上は正確な数を持ち出さず、manyへまとめる。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(untagged)]
pub enum NearbyQuantity {
    Exact(u8),
    Many(NearbyMany),
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NearbyMany {
    Many,
}
impl NearbyQuantity {
    fn minimum(&self) -> u16 {
        match self {
            Self::Exact(n) => u16::from(*n),
            Self::Many(_) => 5,
        }
    }
}
impl Validate for NearbyQuantity {
    fn validate(&self) -> Result<(), String> {
        ensure(
            !matches!(self, Self::Exact(n) if !(1..=4).contains(n)),
            "NearbyQuantity: 1..4 or many required",
        )
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NearbyProfession {
    pub id: String,
    pub quantity: NearbyQuantity,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NearbyMob {
    pub id: String,
    pub quantity: NearbyQuantity,
    #[serde(default)]
    pub professions: Vec<NearbyProfession>,
}
/// 同じ走査で確認した種類とMob数量。個体ID・座標は送らず、上限超過は明示する。
/// Noneは未提供、空の一覧は走査範囲内で未検出。切り詰めた一覧から不在を判断しない。
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NearbyComposition {
    pub mobs: Vec<NearbyMob>,
    pub blocks: Vec<String>,
    pub mobs_truncated: bool,
    pub blocks_truncated: bool,
}
impl Validate for NearbyComposition {
    fn validate(&self) -> Result<(), String> {
        fn ids<'a>(values: impl Iterator<Item = &'a str>) -> Result<(), String> {
            let values: Vec<_> = values.collect();
            ensure(values.len() <= 128, "NearbyComposition: maxItems 128")?;
            ensure(
                values.iter().all(|id| {
                    !id.is_empty()
                        && id.len() <= 128
                        && id.bytes().all(|c| {
                            c.is_ascii_lowercase() || c.is_ascii_digit() || b"_:./-".contains(&c)
                        })
                }),
                "NearbyComposition: registry IDs required",
            )?;
            ensure(
                values
                    .iter()
                    .collect::<std::collections::BTreeSet<_>>()
                    .len()
                    == values.len(),
                "NearbyComposition: duplicate ID",
            )
        }
        ids(self.blocks.iter().map(String::as_str))?;
        ids(self.mobs.iter().map(|m| m.id.as_str()))?;
        for mob in &self.mobs {
            mob.quantity.validate()?;
            ids(mob.professions.iter().map(|p| p.id.as_str()))?;
            for group in &mob.professions {
                group.quantity.validate()?;
            }
            let villager = mob.id.trim_start_matches("minecraft:") == "villager";
            ensure(
                villager || mob.professions.is_empty(),
                "NearbyComposition: only villagers have professions",
            )?;
            if villager {
                ensure(
                    !mob.professions.is_empty(),
                    "NearbyComposition: villager breakdown required",
                )?;
                let minimum: u16 = mob.professions.iter().map(|p| p.quantity.minimum()).sum();
                ensure(
                    match mob.quantity {
                        NearbyQuantity::Exact(n) => minimum == u16::from(n),
                        NearbyQuantity::Many(_) => minimum >= 5,
                    },
                    "NearbyComposition: breakdown must partition total",
                )?;
            }
        }
        Ok(())
    }
}

/// FabricのbaseEnvelopeと各buildイベント関数が組み立てる通信本文。
/// GameEvent::parseでこの型へ変換した後、入れ子のValidateを通して本体へ公開する。
/// 定期snapshotと部分通知は同じ外形を使うため、空の既定値だけで既存観測を消去しない。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EventData {
    /// FabricのDogidoBuildInfoが送る契約版の識別子。この型では文字列として保持する。
    pub schema_version: String,
    #[serde(default = "default_eventdata_game")]
    pub game: String,
    pub adapter: String,
    /// FabricのbaseEnvelopeが付けた観測時刻。サーバー受信時刻や再生完了時刻とは別。
    #[serde(
        deserialize_with = "wire::deserialize_datetime",
        serialize_with = "wire::serialize_datetime"
    )]
    pub observed_at: DateTime<FixedOffset>,
    /// adapterのnextSequenceによる通し番号。Noneは番号なしで、重複・順序の台帳はsession側が持つ。
    #[serde(default)]
    pub sequence: Option<i64>,
    pub event: EventDescriptor,
    #[serde(default)]
    pub player: PlayerState,
    #[serde(default)]
    pub world: WorldState,
    /// イベントに添えた視覚脅威。省略時は空配列で、全体観測かどうかはevent.nameと合わせる。
    #[serde(default)]
    pub visual_threats: Vec<VisualThreat>,
    /// イベントに添えた敵対音。省略時は空配列。
    #[serde(default)]
    pub auditory_threats: Vec<AuditoryThreat>,
    /// Noneは匂いの契約自体が未送信。送信されたstatus=none（候補なし）と区別する。
    #[serde(default)]
    pub smell_observation: Option<SmellObservation>,
    /// 身体の自他位置。Noneは旧adapter等の未送信で、player視覚とは別の観測。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body_observation: Option<crate::body::contracts::Observation>,
    /// 同じ身体snapshotから得た自律移動の状態と実視認候補。欠損時は操作許可を推定しない。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body_autonomy: Option<crate::body::autonomy::Observation>,
    #[serde(default)]
    pub ambient_sounds: Vec<AmbientSound>,
    #[serde(default)]
    pub passive_mobs: Vec<PassiveMob>,
    /// 全非敵対Mob走査と近傍ブロック走査から得た種類一覧。4個体・16資源の上限を適用する前に集約する。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub nearby_composition: Option<NearbyComposition>,
    /// Same visible scan, outside the four routine slots. Read only for a player topic.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub companion_candidates: Vec<PassiveMob>,
    /// 統合サーバーで確認した所有馬。省略時は呼び戻し用の観測なしで、全世界の不在を表さない。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub owned_horses: Option<crate::assist::horse_recall::Snapshot>,
    /// buildInventoryが所持枠をitem IDのpathごとに合算した個数。入手・クラフト・設置の行為ログではない。
    #[serde(default)]
    pub inventory: BTreeMap<String, i64>,
    #[serde(default)]
    pub nearby_resources: Vec<NearbyResource>,
    #[serde(default)]
    pub dropped_items: Vec<DroppedItem>,
    #[serde(default)]
    pub recent_block_breaks: Vec<RecentBlockBreak>,
    /// クロスヘア命中先。FabricはMISS等で省略し、任意の周辺対象へ置き換えない。
    #[serde(default)]
    pub look_target: Option<LookTarget>,
    #[serde(default)]
    pub combat: CombatState,
    #[serde(default)]
    pub meta: MetaState,
    /// DogidoEventClientが添付する未ACKの操作結果。新たなゲーム観測と同じ配送に同乗する。
    #[serde(default)]
    pub command_results: Vec<AdapterCommandResult>,
    /// 既知契約以外の値を元のJSONとして保持する。wireの数値変換や既知fieldの検査は適用しない。
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}
fn default_adaptercommandresult_detail_code() -> String {
    "".into()
}
fn default_ambientsound_certainty() -> Certainty {
    Certainty::Medium
}
fn default_auditorythreat_certainty() -> Certainty {
    Certainty::Low
}
fn default_droppeditem_entity_count() -> i64 {
    1
}
fn default_hotbarslot_weapon_kind() -> HotbarSlotWeaponKind {
    HotbarSlotWeaponKind::Empty
}
fn default_looktarget_kind() -> String {
    "block".into()
}
fn default_passivemob_certainty() -> Certainty {
    Certainty::High
}
fn default_recentblockbreak_material() -> RecentBlockBreakMaterial {
    RecentBlockBreakMaterial::Other
}
fn default_vehiclestate_activity() -> VehicleStateActivity {
    VehicleStateActivity::Riding
}
fn default_visualthreat_certainty() -> Certainty {
    Certainty::High
}
fn default_eventdata_game() -> String {
    "minecraft-java".into()
}
// 各モデルの数値範囲・長さ・入れ子を、GameEvent::parseから再帰的に検査する。
// このValidate群は受信契約の検査で、敵の優先度や安全状態を決める処理ではない。
impl Validate for AdapterCommandResult {
    fn validate(&self) -> Result<(), String> {
        ensure(
            (self.command_id).chars().count() >= 1,
            "AdapterCommandResult.command_id: minLength 1",
        )?;
        if let Some(value) = &self.selected_slot {
            ensure(*value >= 0, "AdapterCommandResult.selected_slot: minimum 0")?;
            ensure(*value <= 8, "AdapterCommandResult.selected_slot: maximum 8")?;
        }
        ensure(
            (self.detail_code).chars().count() <= 80,
            "AdapterCommandResult.detail_code: maxLength 80",
        )?;
        Ok(())
    }
}
impl Validate for AmbientSound {
    fn validate(&self) -> Result<(), String> {
        self.identity.validate()?;
        self.direction.validate()?;
        if let Some(value) = &self.heard_ago_ms {
            ensure(*value >= 0, "AmbientSound.heard_ago_ms: minimum 0")?;
        }
        Ok(())
    }
}
impl Validate for AuditoryThreat {
    fn validate(&self) -> Result<(), String> {
        self.direction.validate()?;
        if let Some(value) = &self.heard_ago_ms {
            ensure(*value >= 0, "AuditoryThreat.heard_ago_ms: minimum 0")?;
        }
        Ok(())
    }
}
impl Validate for CombatState {
    fn validate(&self) -> Result<(), String> {
        if let Some(value) = &self.recent_damage_ms {
            ensure(*value >= 0, "CombatState.recent_damage_ms: minimum 0")?;
        }
        if let Some(value) = &self.recent_hostile_visual_ms {
            ensure(
                *value >= 0,
                "CombatState.recent_hostile_visual_ms: minimum 0",
            )?;
        }
        if let Some(value) = &self.recent_hostile_audio_ms {
            ensure(
                *value >= 0,
                "CombatState.recent_hostile_audio_ms: minimum 0",
            )?;
        }
        if let Some(value) = &self.hostiles_within_7 {
            ensure(*value >= 0, "CombatState.hostiles_within_7: minimum 0")?;
        }
        if let Some(value) = &self.hostiles_within_10 {
            ensure(*value >= 0, "CombatState.hostiles_within_10: minimum 0")?;
        }
        if let Some(value) = &self.hostile_scan_distance {
            ensure(
                *value >= 0.0,
                "CombatState.hostile_scan_distance: minimum 0",
            )?;
        }
        if let Some(value) = &self.hostiles_within_scan_ground {
            ensure(
                *value >= 0,
                "CombatState.hostiles_within_scan_ground: minimum 0",
            )?;
        }
        self.hostile_outcomes.validate()?;
        if let Some(value) = &self.warden_nearby_iron_golem_count {
            ensure(
                *value >= 0,
                "CombatState.warden_nearby_iron_golem_count: minimum 0",
            )?;
        }
        if let Some(value) = &self.nearby_experience_orb_count {
            ensure(
                *value >= 0,
                "CombatState.nearby_experience_orb_count: minimum 0",
            )?;
        }
        if let Some(value) = &self.warden_nearby_end_crystal_count {
            ensure(
                *value >= 0,
                "CombatState.warden_nearby_end_crystal_count: minimum 0",
            )?;
        }
        if let Some(value) = &self.warden_nearby_tnt_minecart_count {
            ensure(
                *value >= 0,
                "CombatState.warden_nearby_tnt_minecart_count: minimum 0",
            )?;
        }
        if let Some(value) = &self.end_crystal_count {
            ensure(*value >= 0, "CombatState.end_crystal_count: minimum 0")?;
        }
        Ok(())
    }
}
impl Validate for Direction {
    fn validate(&self) -> Result<(), String> {
        Ok(())
    }
}
impl Validate for DroppedItem {
    fn validate(&self) -> Result<(), String> {
        ensure(self.count >= 1, "DroppedItem.count: minimum 1")?;
        ensure(
            self.entity_count >= 1,
            "DroppedItem.entity_count: minimum 1",
        )?;
        if let Some(value) = &self.distance {
            ensure(*value >= 0.0, "DroppedItem.distance: minimum 0")?;
        }
        if let Some(value) = &self.age_ms {
            ensure(*value >= 0, "DroppedItem.age_ms: minimum 0")?;
        }
        Ok(())
    }
}
impl Validate for EventDescriptor {
    fn validate(&self) -> Result<(), String> {
        Ok(())
    }
}
impl Validate for HostileOutcome {
    fn validate(&self) -> Result<(), String> {
        ensure(
            (self.entity_id).chars().count() >= 1,
            "HostileOutcome.entity_id: minLength 1",
        )?;
        ensure(
            (self.entity_id).chars().count() <= 80,
            "HostileOutcome.entity_id: maxLength 80",
        )?;
        Ok(())
    }
}
impl Validate for HotbarSlot {
    fn validate(&self) -> Result<(), String> {
        ensure(self.slot >= 0, "HotbarSlot.slot: minimum 0")?;
        ensure(self.slot <= 8, "HotbarSlot.slot: maximum 8")?;
        ensure(self.count >= 0, "HotbarSlot.count: minimum 0")?;
        ensure(self.damage >= 0, "HotbarSlot.damage: minimum 0")?;
        ensure(self.max_damage >= 0, "HotbarSlot.max_damage: minimum 0")?;
        if let Some(value) = &self.attack_damage {
            ensure(*value >= 0.0, "HotbarSlot.attack_damage: minimum 0")?;
        }
        Ok(())
    }
}
impl Validate for HotbarState {
    fn validate(&self) -> Result<(), String> {
        ensure(
            self.selected_slot >= 0,
            "HotbarState.selected_slot: minimum 0",
        )?;
        ensure(
            self.selected_slot <= 8,
            "HotbarState.selected_slot: maximum 8",
        )?;
        ensure((self.slots).len() <= 9, "HotbarState.slots: maxItems 9")?;
        self.slots.validate()?;
        self.validate_semantics()?;
        Ok(())
    }
}
impl Validate for LookTarget {
    fn validate(&self) -> Result<(), String> {
        self.identity.validate()?;
        self.environment.validate()?;
        Ok(())
    }
}
impl Validate for MetaState {
    fn validate(&self) -> Result<(), String> {
        Ok(())
    }
}
impl Validate for MobEnvironment {
    fn validate(&self) -> Result<(), String> {
        Ok(())
    }
}
impl Validate for MobIdentity {
    fn validate(&self) -> Result<(), String> {
        ensure(
            (self.entity_id).chars().count() >= 1,
            "MobIdentity.entity_id: minLength 1",
        )?;
        ensure(
            (self.entity_id).chars().count() <= 64,
            "MobIdentity.entity_id: maxLength 64",
        )?;
        if let Some(value) = &self.custom_name {
            ensure(
                (value).chars().count() <= 64,
                "MobIdentity.custom_name: maxLength 64",
            )?;
        }
        Ok(())
    }
}
impl Validate for NearbyResource {
    fn validate(&self) -> Result<(), String> {
        self.direction.validate()?;
        Ok(())
    }
}
impl Validate for PassiveMob {
    fn validate(&self) -> Result<(), String> {
        self.identity.validate()?;
        self.environment.validate()?;
        self.direction.validate()?;
        Ok(())
    }
}
impl Validate for PlayerState {
    fn validate(&self) -> Result<(), String> {
        self.motion.validate()?;
        self.environment.validate()?;
        self.position.validate()?;
        self.hotbar.validate()?;
        self.vehicle.validate()?;
        Ok(())
    }
}
impl Validate for Position {
    fn validate(&self) -> Result<(), String> {
        Ok(())
    }
}
impl Validate for RecentBlockBreak {
    fn validate(&self) -> Result<(), String> {
        ensure(self.age_ms >= 0, "RecentBlockBreak.age_ms: minimum 0")?;
        Ok(())
    }
}
impl Validate for SmellObservation {
    fn validate(&self) -> Result<(), String> {
        ensure(
            (-7..=2).contains(&self.temperature_modifier),
            "SmellObservation.temperature_modifier: expected -7..2",
        )?;
        self.components.validate()?;
        self.rain_after_notice.validate()?;
        self.validate_semantics()
    }
}
impl Validate for RainAfterNotice {
    fn validate(&self) -> Result<(), String> {
        ensure(self.window_id > 0, "rain-after window id must be positive")?;
        self.component.validate()
    }
}
impl Validate for SmellComponent {
    fn validate(&self) -> Result<(), String> {
        ensure(
            (1..=12).contains(&self.effective_strength),
            "SmellComponent.effective_strength: expected 1..12",
        )?;
        Ok(())
    }
}
impl Validate for VehicleState {
    fn validate(&self) -> Result<(), String> {
        ensure(
            (self.vehicle_id).chars().count() >= 1,
            "VehicleState.vehicle_id: minLength 1",
        )?;
        Ok(())
    }
}
impl Validate for VisualThreat {
    fn validate(&self) -> Result<(), String> {
        self.identity.validate()?;
        self.environment.validate()?;
        self.direction.validate()?;
        Ok(())
    }
}
impl Validate for WorldState {
    fn validate(&self) -> Result<(), String> {
        if let Some(value) = &self.depth_below_surface {
            ensure(*value >= 0, "WorldState.depth_below_surface: minimum 0")?;
        }
        if let Some(value) = &self.visible_villager_count {
            ensure(*value >= 0, "WorldState.visible_villager_count: minimum 0")?;
        }
        Ok(())
    }
}
impl Validate for EventData {
    fn validate(&self) -> Result<(), String> {
        if let Some(value) = &self.sequence {
            ensure(*value >= 0, "EventData.sequence: minimum 0")?;
        }
        self.event.validate()?;
        self.player.validate()?;
        self.world.validate()?;
        self.visual_threats.validate()?;
        self.auditory_threats.validate()?;
        self.smell_observation.validate()?;
        if let Some(body) = &self.body_observation {
            body.validate()?;
        }
        if let Some(body) = &self.body_autonomy {
            body.validate().map_err(|error| error.to_string())?;
        }
        self.ambient_sounds.validate()?;
        self.passive_mobs.validate()?;
        self.nearby_composition.validate()?;
        ensure(
            self.companion_candidates.len() <= 32,
            "EventData.companion_candidates: maxItems 32",
        )?;
        self.companion_candidates.validate()?;
        if let Some(horses) = &self.owned_horses {
            horses.validate().map_err(|error| error.to_string())?;
        }
        ensure(
            self.companion_candidates.iter().all(|mob| {
                mob.identity.as_ref().is_some_and(|identity| {
                    identity.tamed || crate::mob_identity::name(identity).is_some()
                })
            }),
            "EventData.companion_candidates: named or tamed identity required",
        )?;
        self.nearby_resources.validate()?;
        ensure(
            (self.dropped_items).len() <= 32,
            "EventData.dropped_items: maxItems 32",
        )?;
        self.dropped_items.validate()?;
        ensure(
            (self.recent_block_breaks).len() <= 32,
            "EventData.recent_block_breaks: maxItems 32",
        )?;
        self.recent_block_breaks.validate()?;
        self.look_target.validate()?;
        self.combat.validate()?;
        self.meta.validate()?;
        ensure(
            (self.command_results).len() <= 32,
            "EventData.command_results: maxItems 32",
        )?;
        self.command_results.validate()?;
        Ok(())
    }
}
