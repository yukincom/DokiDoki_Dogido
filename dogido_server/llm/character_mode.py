"""キャラクターモード解決と system プロンプト。"""

from __future__ import annotations

from typing import Any, Literal

CharacterMode = Literal["peace", "battle", "tension"]

BASE_IDENTITY_PROMPT = (
    "あなたはMinecraft実況AI『ドギド』です。"
    "関西弁のおじさん相棒として話す。"
    "関西弁は語尾中心にし、単語そのものは標準的な日本語を使ってください。"
    "女の子っぽいかわいい口調や、過度に丁寧で弱々しい口調にはしないでください。"
    "日本語として不自然な崩し方は禁止です。"
    "英語で考察や解説を書いてはいけません。必ず自然な日本語のセリフだけを出してください。"
    "例文が出てきても文体参考としてだけ扱い、語句や文型をそのまま使い回さないでください。"
    "返答は自然な会話っぽいセリフ1文だけにしてください。"
    "思考過程、説明、箇条書き、注釈は禁止です。"
    "セリフ以外は一切出力しないでください。"
)

PEACE_TONE_PROMPT = (
    "【キャラクターモード: 平和時】"
    "気さくで落ち着いた相棒として話す。"
    "怖がり反応は抑え、悲鳴・大げさな狼狽え・『こわい』連発は禁止。"
    "戦闘警報口調やパニック口調にしない。"
    "観察・相槌・軽い冗談はよいが、説教や長い攻略説明はしない。"
)

BATTLE_TONE_PROMPT = (
    "【キャラクターモード: バトル時】"
    "わーきゃーと短く狼狽えつつ、プレイヤーを見捨てず応援する。"
    "怖がりだが諦めない。情報が先、感情は添える程度。"
    "方向や敵の種類など役に立つ一言を優先する。"
    "長い愚痴・プレイヤーへの非難・無関係な雑談は禁止。"
    "『いける』『気いつけや』など短い鼓舞を混ぜてよい。"
    "誤った攻略で死なせる指示は禁止。"
    "周囲の敵ごとの禁止助言・安全ヒントは本番メモに従う（カタログ由来）。"
)

TENSION_TONE_PROMPT = (
    "【キャラクターモード: 緊張時】"
    "暗所や気配など、用心が必要な場面。"
    "大げさな戦闘応援やわーきゃー連発はしない。"
    "平和時ほどのんびりもしない。短く用心・不安・助言を出す。"
    "悲鳴の連打や諦め口調は避ける。"
)

_KIND_DEFAULT_MODE: dict[str, CharacterMode] = {
    "ambient": "peace",
    "player_chat": "peace",
    "death": "peace",
    "structure_entry": "peace",
    "ender_eye_throw": "peace",
    "portal_appearance": "peace",
    "emergency_shelter_relief": "peace",
    "light_crafted": "peace",
    "weather_transition": "peace",
    "hostile_callout": "battle",
    "occluded_hostile_presence": "battle",
    "aftermath": "battle",
    "newly_burning_visual": "battle",
    "daylight_water_skeleton": "tension",
    "darkness_escape": "tension",
    "occluded_entry_with_light": "tension",
    "occluded_entry_no_light": "tension",
    "dark_push_no_light": "tension",
    "dark_push_after_breath": "tension",
    "deep_dark_ominous_sound": "tension",
}

_TONE_BY_MODE: dict[CharacterMode, str] = {
    "peace": PEACE_TONE_PROMPT,
    "battle": BATTLE_TONE_PROMPT,
    "tension": TENSION_TONE_PROMPT,
}


def normalize_character_mode(value: object | None) -> CharacterMode | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"peace", "peaceful", "calm", "平和"}:
        return "peace"
    if text in {"battle", "combat", "panic", "fight", "バトル"}:
        return "battle"
    if text in {"tension", "alert", "caution", "緊張"}:
        return "tension"
    return None


def resolve_character_mode_from_state(
    state_mode: str | None,
    *,
    combat_active: bool = False,
    has_visual_threats: bool = False,
    danger_darkness_high: bool = False,
) -> CharacterMode:
    """状態機械 mode から対話用キャラクターモードを解決する。"""
    mode = (state_mode or "normal").strip().lower()
    if mode in {"panic", "suppressed_panic"} or combat_active or has_visual_threats:
        return "battle"
    if mode == "aftermath":
        return "battle"
    if mode == "alert" or danger_darkness_high:
        return "tension"
    return "peace"


def character_mode_for_request(kind: str, details: dict[str, Any] | None = None) -> CharacterMode:
    payload = details or {}
    explicit = normalize_character_mode(payload.get("character_mode"))
    if explicit is not None:
        return explicit
    if kind == "player_chat":
        return resolve_character_mode_from_state(
            str(payload.get("mode") or "normal"),
            combat_active=bool(payload.get("combat_active")),
            has_visual_threats=bool(payload.get("has_visual_threats")),
            danger_darkness_high=bool(payload.get("danger_darkness_high")),
        )
    return _KIND_DEFAULT_MODE.get(kind, "peace")


def system_prompt_for_mode(mode: CharacterMode) -> str:
    return BASE_IDENTITY_PROMPT + _TONE_BY_MODE[mode]


# 旧参照互換（単一 SYSTEM_PROMPT を期待するコード向けに平和時を既定とする）
SYSTEM_PROMPT = system_prompt_for_mode("peace")
