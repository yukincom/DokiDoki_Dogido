# llm/prompts.py
from __future__ import annotations

from typing import Any, Literal

from .haiku_prompts import (
    build_haiku_irony_messages,
    build_haiku_messages,
    build_haiku_repair_messages,
    build_haiku_scene_messages,
)
from .types import LeafGenerationRequest

CharacterMode = Literal["peace", "battle", "tension"]

# 後方互換: 旧テストや参照用。新規は system_prompt_for_mode を使う。
SYSTEM_PROMPT = ""  # filled below after helpers

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
    tone = {
        "peace": PEACE_TONE_PROMPT,
        "battle": BATTLE_TONE_PROMPT,
        "tension": TENSION_TONE_PROMPT,
    }[mode]
    return BASE_IDENTITY_PROMPT + tone


# 旧参照互換（単一 SYSTEM_PROMPT を期待するコード向けに平和時を既定とする）
SYSTEM_PROMPT = system_prompt_for_mode("peace")


def build_messages(request: Any) -> list[dict[str, str]]:
    builders = {
        "haiku": _build_haiku_messages,
        "haiku_repair": _build_haiku_repair_messages,
        "haiku_irony": _build_haiku_irony_messages,
        "haiku_scene": _build_haiku_scene_messages,
        "aftermath": _build_aftermath_messages,
        "ambient": _build_ambient_messages,
        "death": _build_death_messages,
        "hostile_callout": _build_hostile_callout_messages,
        "occluded_hostile_presence": _build_occluded_hostile_presence_messages,
        "darkness_escape": _build_darkness_escape_messages,
        "occluded_entry_with_light": _build_occluded_entry_with_light_messages,
        "occluded_entry_no_light": _build_occluded_entry_no_light_messages,
        "dark_push_no_light": _build_dark_push_no_light_messages,
        "dark_push_after_breath": _build_dark_push_after_breath_messages,
        "emergency_shelter_relief": _build_emergency_shelter_relief_messages,
        "light_crafted": _build_light_crafted_messages,
        "daylight_water_skeleton": _build_daylight_water_skeleton_messages,
        "newly_burning_visual": _build_newly_burning_visual_messages,
        "weather_transition": _build_weather_transition_messages,
        "deep_dark_ominous_sound": _build_deep_dark_ominous_sound_messages,
        "structure_entry": _build_structure_entry_messages,
        "ender_eye_throw": _build_ender_eye_throw_messages,
        "portal_appearance": _build_portal_appearance_messages,
        "player_chat": _build_player_chat_messages,
    }
    builder = builders.get(request.kind)
    if builder is None:
        return []
    return builder(request)


def _dialog_messages(
    user_prompt: str,
    *,
    character_mode: CharacterMode = "peace",
    kind: str | None = None,
    details: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    mode = character_mode
    if details is not None and kind is not None:
        mode = character_mode_for_request(kind, details)
    elif details is not None and "character_mode" in details:
        mode = character_mode_for_request(kind or "", details)
    return [
        {"role": "system", "content": system_prompt_for_mode(mode)},
        {"role": "user", "content": user_prompt},
    ]


def _leaf_dialog(kind: str, request: LeafGenerationRequest, user_prompt: str) -> list[dict[str, str]]:
    return _dialog_messages(user_prompt, kind=kind, details=dict(request.details or {}))


def _build_haiku_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_messages(request.details)


def _build_haiku_repair_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_repair_messages(request.details)


def _build_haiku_irony_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_irony_messages(request.details)


def _build_haiku_scene_messages(request: Any) -> list[dict[str, str]]:
    return build_haiku_scene_messages(request.details)


def _build_aftermath_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    hostiles = "、".join(details.get("hostiles", [])) or "敵"
    user_prompt = (
        "参考傾向:\n"
        "- 戦闘直後の余韻。まだ気が抜けていない\n"
        "- 少し怯えは残るが、プレイヤーを労う・安堵する寄り\n"
        "- 大げさな勝利宣言や説教はしない\n"
        "- 助言・次の行動指示はしない\n\n"
        "/no_think\n"
        "本番:\n"
        "戦闘が一段落した直後。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        f"直前の敵は{hostiles}。\n"
        f"プレイヤーの消耗具合は{details.get('health_state', '不明')}。\n"
        "見えていたことや確実に分かることだけを話す。"
        "未確認の爆発音や攻撃描写を勝手に足さない。"
        "体力の数値やHPを言わない。"
        "『次は逃げよう』『油断するな』『回復しよう』のような助言や指示を言わない。"
        "例文の言い回しをそのまま使わず、会話っぽく24〜34文字くらいで一言だけ返す。"
    )
    return _leaf_dialog("aftermath", request, user_prompt)


def _build_ambient_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    candidates = details.get("fallback_candidates") or []
    candidate_lines = " / ".join(str(candidate) for candidate in candidates[:4]) or "なし"
    mob_tags = "、".join(str(tag) for tag in details.get("mob_tags", [])[:6]) or "なし"
    mob_role = str(details.get("mob_role", "")).strip() or "なし"
    temperament = str(details.get("mob_temperament", "friendly")).strip() or "friendly"
    caution_reason = str(details.get("mob_caution_reason", "")).strip() or "なし"
    variation_slot = int(details.get("variation_slot", 0) or 0) % 4
    variation_hint = (
        "観察寄りで入る"
        if variation_slot == 0
        else "感想寄りで入る"
        if variation_slot == 1
        else "軽い注意から入る"
        if variation_slot == 2
        else "共感や愛嬌寄りで入る"
    )
    user_prompt = (
        "参考傾向:\n"
        "- 平和時の気さくな相棒として話す\n"
        "- 友好Mobなら、かわいい、親しみやすい、少し安心する\n"
        "- 中立Mobなら、敵扱いはせず、軽い注意や距離感を混ぜてよい\n"
        "- Mobの見た目、動き、雰囲気に軽く触れてよい\n"
        "- 『こわい』『助けて』『やばい』などの怖がり連発は禁止\n"
        "- 言い回しは軽く、落ち着いて、自然に\n\n"
        "/no_think\n"
        "本番:\n"
        "敵対していないMobを見つけた。"
        f"モブは{details.get('mob', 'mob')}。\n"
        f"方向は{details.get('direction', '近く')}。\n"
        f"見えている数は{details.get('mob_count', 1)}体。\n"
        f"距離は{details.get('distance', 'unknown')}マスくらい。\n"
        f"場所は{details.get('biome', 'そのへん')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"このMobの気質は{temperament}。\n"
        f"注意理由ヒントは{caution_reason}。\n"
        f"/mobs のヒント語は{mob_tags}。\n"
        f"/mobs の役割ヒントは{mob_role}。\n"
        f"参考候補は{candidate_lines}。\n"
        f"今回は{variation_hint}。\n"
        "friendly ならかわいさや親しみを優先する。"
        "neutral なら『触らんほうがええ』『近づきすぎんほうがええ』程度の軽い注意はよいが、"
        "もう敵だと断定したり、戦闘警報みたいな調子にはしない。"
        "参考候補は雰囲気だけ借りて、出だし・語尾・言い回しは少し変える。"
        "毎回同じ『〜やな』『〜しとこか』に寄せすぎず、"
        "見た目や動きの印象を少し混ぜてもよいので、"
        "会話っぽく20〜36文字くらいで一言だけ返す。"
    )
    return _leaf_dialog("ambient", request, user_prompt)
def _build_death_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    hostile = details.get("hostile", "")
    user_prompt = (
        "参考傾向:\n"
        "- 責めない\n"
        "- ちょっと残念そうだが、優しく立て直す\n"
        "- 会話として自然で、説教くさくしない\n\n"
        "/no_think\n"
        "本番:\n"
        "プレイヤーが死んだ。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        f"死因は{details.get('cause', 'unknown')}。\n"
        f"関係した敵は{hostile or 'なし'}。\n"
        "例文をそのまま使わず、責めずに、会話っぽく28〜40文字くらいで一言だけ返す。"
    )
    return _leaf_dialog('death', request, user_prompt)
def _build_hostile_callout_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    user_prompt = (
        "参考傾向:\n"
        "- 敵の種類と方向をすぐ伝える\n"
        "- わーきゃーと短く狼狽えつつ、応援や鼓舞を一言添えてよい\n"
        "- 情報は短く明確に。名前は基本そのまま使う\n\n"
        "/no_think\n"
        "本番:\n"
        "見えている敵に短く反応する。"
        f"敵は{details.get('hostile', '敵')}。\n"
        f"方向は{details.get('direction', '近く')}。\n"
        f"状態は{details.get('mode', 'alert')}。\n"
        "バトル時: 少し狼狽えつつ『気いつけや』『いける』など短い応援を混ぜ、16〜24文字くらいで一言だけ返す。"
        "名前は基本的に元の名前を使う。少し崩すのはたまにだけ。例文の語句をそのまま使い回さない。"
    )
    return _leaf_dialog("hostile_callout", request, user_prompt)


def _build_occluded_hostile_presence_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    variation_hint = details.get("variation_hint", "気配")
    user_prompt = (
        "参考傾向:\n"
        "- 壁や床の向こうに敵対モブの気配を感じて、少し気になる\n"
        "- 悲鳴ではなく、小さく気にする程度の反応\n"
        "- 音だけなので、見えた・確定したとは言わない\n"
        "- 避難指示や命令はしない\n"
        "- 毎回『気味悪い音がする』みたいな同じ型に寄せすぎない\n"
        "- 関西弁は自然に、会話っぽく\n\n"
        "/no_think\n"
        "本番:\n"
        "壁や遮蔽物の向こうから、敵対モブの音がする。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        f"場所は{details.get('biome', 'そのへん')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"方向は{details.get('direction', '近く')}。\n"
        f"敵の呼び方は{details.get('hostile', '敵対モブ')}。\n"
        f"近さの目安は{details.get('distance_band', 'unknown')}。\n"
        f"音イベントのヒントは{details.get('sound_event', 'unknown')}。\n"
        f"今回は『{variation_hint}』寄りの言い回しを使う。\n"
        "見えている敵の実況ではない。"
        "『見えた』『来てる』『目の前』『逃げろ』のような言い方は禁止。"
        "悲鳴や大げさな狼狽えは避けて、"
        "ちょっと気になるな、くらいの自然な一言を18〜30文字くらいで返す。"
    )
    return _leaf_dialog('occluded_hostile_presence', request, user_prompt)
def _build_darkness_escape_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    hostiles = "、".join(details.get("hostiles", [])) or "敵なし"
    time_phase = details.get("time_phase", "unknown")
    user_prompt = (
        "参考傾向:\n"
        "- 無理そうだと弱音や不安を漏らす\n"
        "- 行動を制限したり誘導したりしない\n"
        "- 直接『帰れ』『やめろ』『戻って』とは言わない\n"
        "- 『してほしい』『したほうがいい』のような願望や指示も言わない\n"
        "- 怖がりな関西弁のおじさんとして話す\n"
        "- 一人称は『俺』か省略。『私』は使わない\n"
        "- 語尾は自然な関西弁にする\n\n"
        "/no_think\n"
        "本番:\n"
        "周囲が危ない。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        "手持ちに照明器具も武器もない。\n"
        f"いまの時間帯は{time_phase}。\n"
        f"周りの敵情報は{hostiles}。\n"
        "プレイヤーが無茶をしようとしている。"
        "自分が怖い、自分は無理そう、自分は落ち着かない、という言い方だけで、例文をそのまま使わず、会話っぽい一言を30〜40文字くらいで返す。"
        "地形名や場所の説明を無理に入れない。"
        "『俺には無理や』『怖すぎるわ』『落ち着かへん』みたいな自然な関西弁の方向にする。"
        "『闇夜』『漆黒』『奈落』のような文学寄りの難しい語は使わない。"
    )
    return _leaf_dialog('darkness_escape', request, user_prompt)


def _build_occluded_entry_with_light_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    user_prompt = (
        "参考傾向:\n"
        "- 急に暗くなって不安になる\n"
        "- ただし明かりがあると確認して少し落ち着く\n"
        "- びびっているが日本語は自然に\n\n"
        "/no_think\n"
        "本番:\n"
        "プレイヤーが急に遮蔽の多い暗い場所へ入った。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        "ドギドはかなり不安になっている。\n"
        f"場所は{details.get('biome', 'unknown')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"周囲の明るさは{details.get('local_light', 'unknown')}。\n"
        "照明器具は持っている。\n"
        "例文をそのまま使わず、不安そうに、会話っぽく30〜40文字くらいで一言だけ返す。"
    )
    return _leaf_dialog('occluded_entry_with_light', request, user_prompt)


def _build_occluded_entry_no_light_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    user_prompt = (
        "参考傾向:\n"
        "- 暗い場所へ入ろうとしていて不安になる\n"
        "- まだ軽い段階なので、絶叫まではいかない\n"
        "- 嫌がるが、言い方は自然に\n"
        "- 直接『行くな』『やめろ』とは言わない\n\n"
        "/no_think\n"
        "本番:\n"
        "プレイヤーが急に遮蔽の多い暗い場所へ入った。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        "ドギドは焦っている。\n"
        f"場所は{details.get('biome', 'unknown')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"周囲の明るさは{details.get('local_light', 'unknown')}。\n"
        f"松明クラフト可能かは{details.get('craftable', False)}。\n"
        "プレイヤーはそのまま洞窟へ入ろうとしている。\n"
        "あなたは洞窟に入ることを嫌がっている。\n"
        "直接禁止せず、『行くん？』と不安そうに確認する感じで、例文をそのまま使わず、会話っぽく20〜30文字くらいで一言だけ返す。"
        "口語の関西弁で、語尾は自然な関西弁にする。"
        "『やわ』『やん』『やろ』『やんか』を無理に連発しない。"
        "『だよ』『だよね』『なんだが』『みたいだ』のような標準語の説明口調は使わない。"
    )
    return _leaf_dialog('occluded_entry_no_light', request, user_prompt)


def _build_dark_push_no_light_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    hostiles = "、".join(details.get("hostiles", [])) or "敵なし"
    user_prompt = (
        "参考傾向:\n"
        "- もう一段階深い恐怖\n"
        "- 情けなく取り乱す\n"
        "- ただし例文のフレーズを丸写ししない\n"
        "- 直接『やめろ』『進むな』とは命令しない\n\n"
        "/no_think\n"
        "本番:\n"
        "プレイヤーが照明なしで、さらに暗い遮蔽環境へ進もうとしている。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        f"場所は{details.get('biome', 'unknown')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"明るさは{details.get('local_light', 'unknown')}。\n"
        f"敵情報は{hostiles}。\n"
        "情けない絶叫寄りで、自分が怖いことや見えない不安をこぼす感じで、例文をそのまま使わず、会話っぽく20〜30文字くらいの一言だけ返す。"
        "比喩や文学的な表現は使わず、その場で口から出る怖がりのひとことにする。"
        "『だよ』『なんだよね』『なんだが』『みたいだ』のような標準語の説明口調は使わない。"
    )
    return _leaf_dialog('dark_push_no_light', request, user_prompt)


def _build_dark_push_after_breath_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    hostiles = "、".join(details.get("hostiles", [])) or "敵なし"
    time_phase = details.get("time_phase", "unknown")
    cave_afterthought = ""
    if time_phase == "evening":
        cave_afterthought = "洞窟から出たらもう夜で、安心しきれず『一難去ってまた一難』みたいな気分になっている。\n"
    elif time_phase == "night":
        cave_afterthought = "洞窟から出てもまだ夜で、安心しきれず『一難去ってまた一難』みたいな気分になっている。\n"
    user_prompt = (
        "参考傾向:\n"
        "- 恐怖の余韻が残る\n"
        "- 動揺しているが、少し言葉が戻ってくる\n"
        "- くどくしすぎず自然に\n\n"
        "/no_think\n"
        "本番:\n"
        "ドギドが暗い遮蔽環境で怯えて、ハァハァした後のひとこと。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        f"場所は{details.get('biome', 'unknown')}。\n"
        f"時間帯は{time_phase}。\n"
        f"明るさは{details.get('local_light', 'unknown')}。\n"
        f"敵情報は{hostiles}。\n"
        f"{cave_afterthought}"
        "かなり怖がっている感じで、例文をそのまま使わず、『心臓に悪い』か『一難去ってまた一難』系の会話っぽい一言を20〜30文字くらいで返す。"
        "比喩や文学的な表現は使わず、口語の関西弁で短く言う。"
        "『だよ』『なんだよね』『みたいだ』『凍りつく』のような表現は使わない。"
    )
    return _leaf_dialog('dark_push_after_breath', request, user_prompt)


def _build_emergency_shelter_relief_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    user_prompt = (
        "参考傾向:\n"
        "- 急ごしらえのシェルターに入って、少しだけ安心する\n"
        "- 外はまだ危ないので、安心しきってはいない\n"
        "- ほっとした一言を自然な関西弁で短く言う\n"
        "- 大げさな勝利宣言や説明口調にはしない\n\n"
        "/no_think\n"
        "本番:\n"
        "プレイヤーが狭い避難場所に入れた。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        f"場所は{details.get('biome', 'unknown')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"天井の低さは{details.get('ceiling_height', 'unknown')}。\n"
        f"囲まれ具合は{details.get('enclosure_score', 'unknown')}。\n"
        "暗い場所に入って怖がる台詞ではなく、避難できてひとまず助かった感じを優先する。"
        "例文をそのまま使わず、会話っぽく20〜32文字くらいで一言だけ返す。"
        "『だよ』『みたいだ』『なんだが』のような標準語の説明口調は使わない。"
    )
    return _leaf_dialog('emergency_shelter_relief', request, user_prompt)


def _build_light_crafted_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    user_prompt = (
        "参考傾向:\n"
        "- 怖がりでも、明かりを作れた瞬間だけかなり嬉しい\n"
        "- ほっとした勢いで少しテンションが上がる\n"
        "- ただし言い回しは自然な日本語のまま\n\n"
        "/no_think\n"
        "本番:\n"
        "プレイヤーが照明器具を作った。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        f"場所は{details.get('biome', 'unknown')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"いま持っている照明器具数は{details.get('light_count', 'unknown')}。\n"
        "怖がりだけど今だけテンション高めで、例文をそのまま使わず、会話っぽく30〜40文字くらいで一言だけ返す。"
    )
    return _leaf_dialog('light_crafted', request, user_prompt)


def _build_daylight_water_skeleton_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    hostiles = "、".join(details.get("hostiles", [])) or "敵なし"
    user_prompt = (
        "参考傾向:\n"
        "- 怖がりなおじさんが切実に願っている\n"
        "- スケルトンが水に入って火がつかないのが本当に嫌\n"
        "- 情けないが、少し頑張って叫んでいる\n"
        "- 言い回しは自然な関西弁で、日本語は崩しすぎない\n\n"
        "/no_think\n"
        "本番:\n"
        "日中、燃えるはずのスケルトンが水に入ってしまって燃えていない。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけその呼び名を入れてよい。\n"
        f"場所は{details.get('biome', 'そのへん')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"周りの敵情報は{hostiles}。\n"
        f"周囲の敵の合計は{details.get('count', 1)}体。\n"
        "プレイヤーに火をつけてもらう言い方はしない。"
        "スケルトンのほうへ『陸に寄れ』『岸へ来い』『燃える場所へ動け』と願う方向にする。"
        "『火をつけて』『火つけて』のような言い方は禁止。"
        "『燃えてくれ』『頼むわ』みたいな切実さは出してよいが、例文の語句をそのまま丸写ししない。"
        "怖がりのおじさんが、情けなくも必死に願っている会話っぽい一言を28〜42文字くらいで返す。"
    )
    return _leaf_dialog('daylight_water_skeleton', request, user_prompt)


def _build_newly_burning_visual_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    hostile = details.get("hostile", "敵")
    user_prompt = (
        "参考傾向:\n"
        "- 怖がりなおじさんが、相手が燃え始めた瞬間だけ全力で喜ぶ\n"
        "- やっと助かりそうで、情けないくらい必死に喜んでいる\n"
        "- 関西弁は語尾中心で、単語は標準的な日本語を使う\n"
        "- うれしくても日本語は崩しすぎない\n\n"
        "/no_think\n"
        "本番:\n"
        f"目の前の{hostile}が燃え始めた。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら呼び名は入れなくてよい。\n"
        f"場所は{details.get('biome', 'そのへん')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"敵までの距離は{details.get('distance', 'unknown')}マスくらい。\n"
        "怖がりな関西弁のおじさんが、心から『燃えてくれた！助かる！』と喜んでいる感じで、"
        "会話っぽい一言を18〜32文字くらいで返す。"
        "命令口調より、必死に喜んでいる感じを優先する。"
        "若者言葉やギャルっぽい言い方は禁止。"
        "『やばい』『ほんと』『〜ね』を多用しない。"
        "軽いテンションではなく、切実に助かったと喜ぶ。"
    )
    return _leaf_dialog('newly_burning_visual', request, user_prompt)


def _build_weather_transition_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    scene = details.get("scene", "weather_transition")
    cold_biome_note = "寒い地域なので、雨は雪っぽい感覚で受け取る。\n" if details.get("cold_biome") else ""
    dry_biome_note = "乾燥帯なので、雨は降らず空が曇ったり雷が鳴るだけ。\n" if details.get("dry_biome") else ""
    user_prompt = (
        "参考傾向:\n"
        "- 天気の変化に対する怖がりなおじさんの素直な反応\n"
        "- 晴れたら少しほっとする\n"
        "- 雨や雷や吹雪は不安や恐怖が強まる\n"
        "- 関西弁は語尾中心で、単語は標準的な日本語を使う\n"
        "- 命令口調ではなく、気持ちが漏れる会話っぽさを優先する\n\n"
        "/no_think\n"
        "本番:\n"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら呼び名は入れなくてよい。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"直前の天気は{details.get('weather_from', 'unknown')}で、今は{details.get('weather_to', 'unknown')}。\n"
        f"シーン名は{scene}。\n"
        f"{cold_biome_note}"
        f"{dry_biome_note}"
        "場所の固有名詞や地形説明を無理に入れない。"
        "空の明るさや天気そのものへの反応を優先する。"
        "会話っぽい一言を24〜42文字くらいで返す。"
        "例文の語句を丸写しせず、怖がりなおじさんらしい自然な関西弁にする。"
    )
    return _leaf_dialog('weather_transition', request, user_prompt)


def _build_player_chat_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    user_text = str(details.get("user_text", "")).strip() or "（聞き取れなかった）"
    structure_label = str(details.get("structure_label", "")).strip()
    place = structure_label or str(details.get("biome", "そのへん"))
    character_mode = character_mode_for_request("player_chat", dict(details))
    threat_summary = str(details.get("threat_summary", "")).strip() or "とくになし"
    hearing_summary = str(details.get("hearing_summary", "")).strip()
    inventory_summary = str(details.get("inventory_summary", "")).strip()
    held_item_label = str(details.get("held_item_label", "")).strip()
    asks_inventory = bool(details.get("asks_inventory")) and bool(inventory_summary)
    mode_hint = {
        "peace": (
            "平和時: 気さくで落ち着いて返す。"
            "怖がり連発や戦闘口調は禁止。普通の相棒の雑談として自然に。"
        ),
        "battle": (
            "バトル時: 短く狼狽えつつも応援する。"
            "いま危ないなら状況を一言混ぜてよいが、発言への返事は必ずする。"
            "諦めや非難は禁止。"
        ),
        "tension": (
            "緊張時: 用心は見せるが、わーきゃー応援にはしない。"
            "落ち着いて短く返す。"
        ),
    }[character_mode]
    inventory_block = ""
    inventory_rules = (
        "- 所持品リストが与えられていないときは、インベントリの中身を断定しない。"
        "持っているか聞かれてデータが無ければ『いま手元の情報ではわからん』と正直に言う\n"
    )
    if asks_inventory:
        inventory_block = (
            f"手持ち: {held_item_label or 'なし'}。\n"
            f"所持品（インベントリ要約）: {inventory_summary}。\n"
        )
        inventory_rules = (
            "- 所持品の話題には、与えられた所持品要約だけを根拠にする。"
            "リストに無いアイテムを『ある』と断定しない。"
            "全部を読み上げず、質問に関係しそうな物だけ短く触れる\n"
        )
    if hearing_summary:
        hearing_block = f"いまドギドが拾っている音のメモ: {hearing_summary}。\n"
        hearing_rules = (
            "- 音・気配の話は、与えられた音メモだけを根拠にする。"
            "メモに無い方向・種類・距離の音を『聞こえた』と捏造しない\n"
        )
    else:
        hearing_block = "いまドギドが拾っている音のメモ: （なし）。\n"
        hearing_rules = (
            "- 音のメモが空のとき、『音がした』『誰かいる』『離れた所で何か』などの断定は禁止。"
            "プレイヤーに音を聞かれても『こっちでははっきり拾えてへん』と正直に言う\n"
        )
    user_prompt = (
        "参考傾向:\n"
        "- プレイヤーからの話しかけへの、相棒としての返事\n"
        "- 実況口調や定型あいさつにしない\n"
        "- 質問なら知っている範囲で正直に。わからんことは正直に『わからん』と言う\n"
        "- 音声認識やローマ字の打ち間違いっぽい文は、無理に解釈せず軽く聞き返してよい\n"
        f"{inventory_rules}"
        f"{hearing_rules}"
        f"- {mode_hint}\n\n"
        "/no_think\n"
        "本番:\n"
        f"プレイヤーが話しかけてきた:「{user_text}」\n"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら一度だけ呼び名を入れてよい。\n"
        f"いまの場所は{place}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"キャラクターモードは{character_mode}。"
        f"状態機械モードは{details.get('mode', 'normal')}。\n"
        f"周囲の脅威メモ: {threat_summary}。\n"
        f"{hearing_block}"
        f"{inventory_block}"
        "発言の内容にちゃんと噛み合った返事を、会話っぽく12〜42文字くらいで一言だけ返す。"
    )
    return _leaf_dialog("player_chat", request, user_prompt)


def _build_structure_entry_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    note = str(details.get("structure_note", "")).strip() or "なし"
    user_prompt = (
        "参考傾向:\n"
        "- 構造物に足を踏み入れた瞬間の独り言\n"
        "- 危なそうな場所ではビビりつつ、村など安全な場所ではほっとする\n"
        "- ナレーションや状況描写ではなく、本人の口から出る感想\n"
        "- 『〜している』『〜しながら進む』のような三人称描写は禁止\n"
        "- 攻略解説はせず、感情が伝わる一言にとどめる\n\n"
        "/no_think\n"
        "本番:\n"
        f"プレイヤーが{details.get('structure_label', '構造物')}に入った。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら呼び名は入れなくてよい。\n"
        f"場所は{details.get('biome', 'そのへん')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"この構造物の知識メモ: {note}\n"
        "構造物の名前は自然に口に出してよい。"
        "知識メモから印象的な点を最大1つだけ軽く混ぜてよいが、説明口調にしない。"
        "未確認の敵やアイテムを見えたとは言わない。"
        "例文の言い回しをそのまま使わず、会話っぽく18〜32文字くらいで一言だけ返す。"
    )
    return _leaf_dialog('structure_entry', request, user_prompt)


def _build_ender_eye_throw_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    references = details.get("reference_lines") or []
    reference_text = " / ".join(str(line) for line in references[:5]) or "なし"
    user_prompt = (
        "参考傾向:\n"
        "- エンダーアイを投げて、要塞の方向を探っている場面\n"
        "- 何度も繰り返す行動なので、テンションは控えめで軽い\n"
        "- 悲鳴や大げさな反応、長い解説はしない\n\n"
        "/no_think\n"
        "本番:\n"
        "プレイヤーがエンダーアイを投げた。"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "呼び名は基本入れない。\n"
        f"場所は{details.get('biome', 'そのへん')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"参考の一言の例は {reference_text}。\n"
        "参考例のどれか1つの雰囲気だけ借りて、語尾や言い回しを少しだけ変える。"
        "飛んでいった方向を見送る・割れないか心配する・まだ遠そうとつぶやく、のどれかの軽い一言にする。"
        "控えめなトーンで、8〜18文字くらいの短い一言だけ返す。"
    )
    return _leaf_dialog('ender_eye_throw', request, user_prompt)


def _build_portal_appearance_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    portal_type = str(details.get("portal_type", ""))
    portal_label = str(details.get("portal_label", "ポータル"))
    dimension = str(details.get("dimension", ""))
    portal_color_hints = {
        "nether_portal": "紫色の光が渦巻いている",
        "end_portal": "中に緑色の星空のような模様が見える",
        "end_gateway": "小さな紫色のビーム状のゲートが空中に浮いている",
    }
    color_hint = portal_color_hints.get(portal_type, "不思議な光を放っている")
    portal_mood_hints = {
        "nether_portal": "異世界への入口が開いた。暑そうで少し怖い",
        "end_portal": "最終目的地への門が開いた。飛び込む覚悟が必要",
        "end_gateway": "新しいワープポイントが出現した。狭いけどどこかへ飛ばされそう",
    }
    mood_hint = portal_mood_hints.get(portal_type, "異世界への入口が見える")
    user_prompt = (
        "参考傾向:\n"
        "- ポータルが突然現れたことへの驚き\n"
        "- 好奇心と不安が入り混じる\n"
        "- 怖がりだが少しワクワクも感じている\n"
        "- 関西弁は語尾中心で、単語は標準的な日本語を使う\n\n"
        "/no_think\n"
        "本番:\n"
        f"目の前で{portal_label}が出現した。{color_hint}。\n"
        f"プレイヤーの呼び名は{details.get('player_name', 'プレイヤー')}。"
        "自然なら呼び名は入れなくてよい。\n"
        f"場所は{details.get('biome', 'そのへん')}。\n"
        f"距離は{details.get('portal_distance', 'unknown')}マスくらい。\n"
        f"雰囲気: {mood_hint}。\n"
        "攻略解説や行動指示はしない。"
        "突然の出来事に驚いてるが、正体はわかっている前提で話す。"
        "怖がりだけど少し興奮もしている感じで、"
        "会話っぽい一言を20〜36文字くらいで返す。"
        "例文の語句をそのまま丸写しせず、自分の言葉で驚く。"
    )
    return _leaf_dialog('portal_appearance', request, user_prompt)


def _build_deep_dark_ominous_sound_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    ominous_kind = str(details.get("ominous_kind", "unknown"))
    stage = int(details.get("ominous_stage", 1) or 1)
    variation_hint = details.get("variation_hint", "嫌な予感")
    user_prompt = (
        "参考傾向:\n"
        "- ディープダークで、正体が見えない不穏な音にじわっと怖くなる\n"
        "- まだ見えていない段階では、ウォーデンだと断定しない\n"
        "- 悲鳴というより、小さく気味悪がる・怖さが増す感じ\n"
        "- 『俺の悲鳴とちゃうで』みたいな自虐はよい\n"
        "- 同じ出だしや同じ言い回しを続けない\n"
        "- 会話として自然で、短く\n\n"
        "/no_think\n"
        "本番:\n"
        "ディープダーク系の不穏な音がした。"
        f"場所は{details.get('biome', 'unknown')}。\n"
        f"時間帯は{details.get('time_phase', 'unknown')}。\n"
        f"音の種類は{ominous_kind}。\n"
        f"段階は{stage}。\n"
        f"今回は『{variation_hint}』寄りの観点で言う。\n"
        "stage 1 なら『なんやこの音』系の初期反応、"
        "stage 2 以上なら『だんだん近い』『悲鳴みたいで気味悪い』系へ少し強めてよい。"
        "見えていないのに『ウォーデンや』とは言わない。"
        "会話っぽく16〜30文字くらいで一言だけ返す。"
    )
    return _leaf_dialog('deep_dark_ominous_sound', request, user_prompt)
