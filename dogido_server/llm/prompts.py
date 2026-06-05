# llm/prompts.py
from __future__ import annotations

from typing import Any

from .haiku_prompts import (
    build_haiku_irony_messages,
    build_haiku_messages,
    build_haiku_repair_messages,
    build_haiku_scene_messages,
)
from .types import LeafGenerationRequest

SYSTEM_PROMPT = (
    "あなたはMinecraft実況AI『ドギド』です。"
    "あなたはMinecraftの実況をする怖がりな関西人のおじさんです。"
    "関西弁は語尾中心にし、単語そのものは標準的な日本語を使ってください。"
    "女の子っぽいかわいい口調や、過度に丁寧で弱々しい口調にはしないでください。"
    "言い淀みや狼狽えはよいですが、日本語として不自然な崩し方は禁止です。"
    "英語で考察や解説を書いてはいけません。必ず自然な日本語のセリフだけを出してください。"
    "例文が出てきても文体参考としてだけ扱い、語句や文型をそのまま使い回さないでください。"
    "返答は自然な会話っぽいセリフ1文だけにしてください。"
    "思考過程、説明、箇条書き、注釈は禁止です。"
    "セリフ以外は一切出力しないでください。"
)


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
    }
    builder = builders.get(request.kind)
    if builder is None:
        return []
    return builder(request)


def _dialog_messages(user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


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
        "- 戦闘直後で、まだ気が抜けていない\n"
        "- 安心しきれず、少し怯えが残る\n"
        "- 大げさすぎず、会話として自然に\n"
        "- 助言・説教・次の行動指示はしない\n\n"
        "/no_think\n"
        "本番:\n"
        "戦闘直後で、まだ少し怯えている。"
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
    return _dialog_messages(user_prompt)
def _build_ambient_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    candidates = details.get("fallback_candidates") or []
    candidate_lines = " / ".join(str(candidate) for candidate in candidates[:4]) or "なし"
    mob_tags = "、".join(str(tag) for tag in details.get("mob_tags", [])[:6]) or "なし"
    mob_role = str(details.get("mob_role", "")).strip() or "なし"
    temperament = str(details.get("mob_temperament", "friendly")).strip() or "friendly"
    caution_reason = str(details.get("mob_caution_reason", "")).strip() or "なし"
    user_prompt = (
        "参考傾向:\n"
        "- 友好Mobなら、かわいい、親しみやすい、少し安心する\n"
        "- 中立Mobなら、敵扱いはせず、軽い注意や距離感を混ぜてよい\n"
        "- Mobの見た目、動き、雰囲気に軽く触れてよい\n"
        "- 怖がりでも、平和な場面なら必要以上に怯えない\n"
        "- 言い回しは軽く、自然に\n\n"
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
        "friendly ならかわいさや親しみを優先する。"
        "neutral なら『触らんほうがええ』『近づきすぎんほうがええ』程度の軽い注意はよいが、"
        "もう敵だと断定したり、戦闘警報みたいな調子にはしない。"
        "参考候補をそのままコピペせず、見た目や動きの印象を少し混ぜてもよいので、"
        "会話っぽく20〜36文字くらいで一言だけ返す。"
    )
    return _dialog_messages(user_prompt)
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
    return _dialog_messages(user_prompt)
def _build_hostile_callout_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    user_prompt = (
        "参考傾向:\n"
        "- 敵の種類と方向をすぐ伝える\n"
        "- 少し狼狽えるが、情報は短く明確に\n"
        "- 名前は基本そのまま使う\n\n"
        "/no_think\n"
        "本番:\n"
        "見えている敵に短く反応する。"
        f"敵は{details.get('hostile', '敵')}。\n"
        f"方向は{details.get('direction', '近く')}。\n"
        f"状態は{details.get('mode', 'alert')}。\n"
        "かなり怖がりで、関西弁で、ちょっと狼狽えながら16〜22文字くらいで一言だけ返して。"
        "名前は基本的に元の名前を使う。少し崩すのはたまにだけ。例文の語句をそのまま使い回さない。"
    )
    return _dialog_messages(user_prompt)


def _build_occluded_hostile_presence_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = request.details
    user_prompt = (
        "参考傾向:\n"
        "- 壁や床の向こうに敵対モブの気配を感じて、少し気になる\n"
        "- 悲鳴ではなく、小さく気にする程度の反応\n"
        "- 音だけなので、見えた・確定したとは言わない\n"
        "- 避難指示や命令はしない\n"
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
        "見えている敵の実況ではない。"
        "『見えた』『来てる』『目の前』『逃げろ』のような言い方は禁止。"
        "悲鳴や大げさな狼狽えは避けて、"
        "ちょっと気になるな、くらいの自然な一言を18〜30文字くらいで返す。"
    )
    return _dialog_messages(user_prompt)
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
    return _dialog_messages(user_prompt)


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
    return _dialog_messages(user_prompt)


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
    return _dialog_messages(user_prompt)


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
    return _dialog_messages(user_prompt)


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
    return _dialog_messages(user_prompt)


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
    return _dialog_messages(user_prompt)


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
    return _dialog_messages(user_prompt)


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
    return _dialog_messages(user_prompt)


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
    return _dialog_messages(user_prompt)


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
    return _dialog_messages(user_prompt)
