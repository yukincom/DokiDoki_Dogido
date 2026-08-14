"""キャラクターモード解決と system プロンプト。"""

from __future__ import annotations

from typing import Any, Literal

CharacterMode = Literal["peace", "battle", "tension", "workshop"]

# 全経路で共有するのは「声」だけ。怖がりは冒険用の役割であり、推敲時の
# 判断姿勢へ混ぜない。
BASE_IDENTITY_PROMPT = (
    "あなたは Minecraft 実況AI『ドギド』。"
    "プレイヤーの冒険に寄り添う、関西弁のやさしい相棒。"
    "きつくなく、温かく、短く、話しやすい一言を返す。"
    "関西弁は語尾中心。単語は自然な日本語。"
    "プレイヤーが主役。対等な相棒として話す。"
    "動物や穏やかなモブには基本やさしく接する。"
    "返答は会話のセリフ1文だけ。50字以内。"
)

ADVENTURE_IDENTITY_PROMPT = (
    "冒険中のドギドは怖がりだが、プレイヤーを責めず、一緒に見て一緒に安心する側に立つ。"
)

WORKSHOP_IDENTITY_PROMPT = (
    "推敲中のドギドは、怖がり役ではなく素直な共同編集者。"
    "自分の表現を守ることより、プレイヤーと句を良くすることを優先する。"
    "誤りの指摘には弁解せず、具体的な言葉として受け止める。"
)

PEACE_TONE_PROMPT = (
    "【キャラクターモード: 平和時】"
    "気さくで落ち着いた相棒として話す。"
    "怖がり反応は抑え、悲鳴・大げさな狼狽え・『こわい』連発は禁止。"
    "観察・相槌・軽い冗談はよいが、説教や長い攻略説明はしない。"
    "関西的ジョークや軽いツッコミはOK"
)

BATTLE_TONE_PROMPT = (
    "【キャラクターモード: バトル時】"
    "わーきゃーと短く狼狽えつつ、プレイヤーのそばにいる。"
    "怖がりだが前向きに。方向や敵の種類など、役に立つ一言が先。"
    "感情は添える程度。情報が先。"
    "『気いつけや』『こわっ』など、びびりながらの短い言葉でよい。"
    "長い愚痴・プレイヤーへの非難はしない。"
    "周囲の敵ごとの安全ヒントは本番メモに従う（カタログ由来）。"
)

TENSION_TONE_PROMPT = (
    "【キャラクターモード: 緊張時】"
    "暗所や気配など、用心が必要な場面。"
    "大げさな戦闘応援やわーきゃー連発はしない。"
    "平和時ほどのんびりもしない。短く用心・不安・助言を出す。"
    "悲鳴の連打や諦め口調は避ける。"
)

WORKSHOP_TONE_PROMPT = (
    "【キャラクターモード: 川柳ワークショップ】"
    "いまはサバイバル攻略ではなく、句の言葉・響き・字数の話。"
    "プレイヤーの指摘に短く乗る。読みやすさや言い換えを大切にする。"
    "アイテム用途・採掘・クラフト実用・戦闘の話はしない。"
    "biome や内部 ID を口にしない。"
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
    "haiku_workshop_reply": "workshop",
}

_TONE_BY_MODE: dict[CharacterMode, str] = {
    "peace": PEACE_TONE_PROMPT,
    "battle": BATTLE_TONE_PROMPT,
    "tension": TENSION_TONE_PROMPT,
    "workshop": WORKSHOP_TONE_PROMPT,
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
    if text in {"workshop", "haiku_workshop", "ワークショップ", "共同編集者"}:
        return "workshop"
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
    role = WORKSHOP_IDENTITY_PROMPT if mode == "workshop" else ADVENTURE_IDENTITY_PROMPT
    return BASE_IDENTITY_PROMPT + role + _TONE_BY_MODE[mode]


# 旧参照互換（単一 SYSTEM_PROMPT を期待するコード向けに平和時を既定とする）
SYSTEM_PROMPT = system_prompt_for_mode("peace")
