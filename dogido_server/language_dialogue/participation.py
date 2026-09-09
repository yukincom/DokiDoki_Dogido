"""独立音声試験で、宛先のない室内会話を通常対話へ混ぜない小さな状態機械。"""

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata


class ParticipationState(str, Enum):
    ACTIVE = "active"
    QUIET = "quiet"
    MIC_OFF = "mic_off"


class ParticipationEvent(str, Enum):
    EXPLICIT_TALK = "explicit_talk"
    EXPLICIT_LISTEN = "explicit_listen"
    IDLE_TIMEOUT = "idle_timeout"
    DIRECT_CALL = "direct_call"
    SIDE_CONVERSATION_RESOLVED = "side_conversation_resolved"
    FALSE_SUPPRESSION_CORRECTED = "false_suppression_corrected"
    CAPTURE_STOPPED = "capture_stopped"


@dataclass(frozen=True)
class ParticipationDecision:
    before: ParticipationState
    after: ParticipationState
    event: ParticipationEvent
    reason: str


def transition(state: ParticipationState, event: ParticipationEvent) -> ParticipationDecision:
    """I/Oを行わず、明示操作と確定済みの会話状態だけで遷移する。"""
    after = state
    if event is ParticipationEvent.EXPLICIT_TALK:
        after, reason = ParticipationState.ACTIVE, "user_enabled_dialogue"
    elif event in {ParticipationEvent.EXPLICIT_LISTEN, ParticipationEvent.CAPTURE_STOPPED}:
        after, reason = ParticipationState.MIC_OFF, "user_listen_only" if event is ParticipationEvent.EXPLICIT_LISTEN else "capture_stopped"
    elif event is ParticipationEvent.IDLE_TIMEOUT:
        after, reason = ParticipationState.QUIET, "dialogue_idle"
    elif event is ParticipationEvent.DIRECT_CALL:
        if state is ParticipationState.QUIET:
            after, reason = ParticipationState.ACTIVE, "direct_call"
        else:
            reason = "direct_call_ignored_while_microphone_off" if state is ParticipationState.MIC_OFF else "already_active"
    elif event in {
        ParticipationEvent.SIDE_CONVERSATION_RESOLVED,
        ParticipationEvent.FALSE_SUPPRESSION_CORRECTED,
    }:
        after = ParticipationState.ACTIVE
        reason = event.value
    else:  # pragma: no cover - Enum追加時のfail-closed。
        raise ValueError(f"unsupported participation event: {event}")
    return ParticipationDecision(state, after, event, reason)


_DIRECT_CALL_PREFIX = re.compile(r"^(?:あれ|ねえ|ねぇ|なあ|おーい)[\s、,。.!！?？:：]*")
_DIRECT_CALL = re.compile(
    r"^ドギド(?:"
    r"[\s、,。.!！?？:：]+(?P<body>.*)"
    r"|(?:いる|おる|聞いてる|聞いてますか|きいてる|きいてますか)[\s。.!！?？]*"
    r"|$)"
)

_TOPIC_SHIFT = re.compile(
    r"^(?:あの[\s、,。]*|えっと[\s、,。]*)?"
    r"(?:ところで|さて|そういえば|それはそうと|話(?:は|を)?変わるけど|別の話(?:やけど|だけど)?)"
)

_QUESTION_WORDS = (
    "何", "なに", "どう", "どこ", "いつ", "だれ", "誰", "なぜ", "なんで",
    "どっち", "どれ", "いくつ", "教えて", "知ってる", "分かる", "わかる",
)

_MINECRAFT_WORDS = (
    "マインクラフト", "マイクラ", "クラフト", "ネザー", "エンド", "レッドストーン",
    "クリーパー", "ゾンビ", "スケルトン", "エンダーマン", "ツルハシ",
)


def direct_call_body(text: str) -> str | None:
    """文頭の明示名指しと、狭い呼びかけ前置きだけを受理する。"""
    normalized = unicodedata.normalize("NFKC", text).strip()
    normalized = _DIRECT_CALL_PREFIX.sub("", normalized, count=1)
    match = _DIRECT_CALL.fullmatch(normalized)
    if not match:
        return None
    return (match.group("body") or "").strip()


def has_topic_shift_cue(text: str) -> bool:
    """話題変更を本人が明示した短い接続表現だけを拾う。"""
    normalized = unicodedata.normalize("NFKC", text).strip()
    return bool(_TOPIC_SHIFT.search(normalized))


def is_obvious_question(text: str) -> bool:
    """STTが疑問符を落としても、明示質問を抑止しないためのfail-open判定。"""
    normalized = unicodedata.normalize("NFKC", text).strip()
    if "?" in normalized or "？" in normalized:
        return True
    if any(word in normalized for word in _QUESTION_WORDS):
        return True
    return bool(re.search(r"(?:かな|かい|ですか|ますか|んか)[\s。.!！]*$", normalized))


def is_obvious_minecraft_topic(text: str) -> bool:
    """正本カタログの固有名と作品名だけで、明白なMinecraft発話をfail-openにする。"""
    normalized = unicodedata.normalize("NFKC", text)
    if any(word in normalized for word in _MINECRAFT_WORDS):
        return True
    try:
        from dogido_server.dialogue.chat_policy import catalog_labels_mentioned_in_text

        return any(
            len(label) >= 4
            for label in catalog_labels_mentioned_in_text(normalized)
        )
    except Exception:  # カタログ障害を理由に入力を抑止しない。
        return False


def is_playful_vocalization(text: str) -> bool:
    """意味を決めつけず、繰り返しのかな遊びだけを短い相槌へ流す。"""
    normalized = unicodedata.normalize("NFKC", text)
    if is_obvious_question(normalized):
        return False
    compact = re.sub(r"[\s、,。.!！?？ー〜~]", "", normalized)
    if not 4 <= len(compact) <= 40 or not re.fullmatch(r"[ぁ-ゖァ-ヺ]+", compact):
        return False
    bigrams = [compact[index:index + 2] for index in range(len(compact) - 1)]
    return any(bigrams.count(part) >= 2 for part in set(bigrams))


def companion_reaction(text: str) -> str:
    if is_playful_vocalization(text):
        return "歌かいな。ご機嫌やな〜。"
    return "おっ、聞いてるで。"


def resolves_side_conversation(text: str) -> bool:
    """保留後に、脇の会話や騒音から戻ったと読める代表表現。"""
    normalized = unicodedata.normalize("NFKC", text)
    return bool(
        re.search(r"待たせ(?:た|て)(?:ね|な|ごめん)?", normalized)
        or re.search(r"(?:うるさ|騒がし)くて.*(?:ごめん|すまん)", normalized)
        or re.search(r"(?:ごめん|すまん).*(?:うるさ|騒がし)かった", normalized)
    )


def corrects_false_suppression(text: str) -> bool:
    """直前の保留が自分宛だったと本人が訂正する代表表現。"""
    normalized = unicodedata.normalize("NFKC", text)
    addressed = any(
        phrase in normalized
        for phrase in ("ドギドに言った", "ドギドに言うた", "お前に言った", "君に言った", "あなたに言った")
    )
    checking = bool(re.search(r"(?:聞いてる|聞いてますか)[\s。.!！?？]*$", normalized))
    return addressed or checking
