from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import math
import re
from uuid import uuid4

from dogido_server.assist.types import ActionContext, ActionName, ActionSpec, RiskPolicy
from dogido_server.haiku.workshop import explicit_workshop_line_index
from dogido_server.models import HotbarSlot, SelectHotbarCommand


SELECT_HOTBAR_CAPABILITY = "client.hotbar.select.v1"
SELECT_SWORD_LLM_MIN_CONFIDENCE = 0.90
SELECT_SWORD_RULE_VERSION = "2026-09-02.1"

_SWORD_TARGETS = ("剣", "けん", "つるぎ", "ソード", "そーど")
_DIRECT_SWORD_REQUESTS = frozenset(
    {
        "剣",
        "つるぎ",
        "ソード",
        "剣お願い",
        "けんお願い",
        "剣にして",
        "けんにして",
        "剣持って",
        "けん持って",
        "剣を持って",
        "けんを持って",
        "剣装備",
        "けん装備",
    }
)
_SWORD_TARGET_PATTERN = r"(?:剣|けん|つるぎ|ソード|そーど)"
_REQUEST_SUFFIX_PATTERN = (
    r"(?:ください|下さい|くれ|くれる|くれへん|くれない|くれませんか|"
    r"もらえる|もらえますか|もらうことはできますか|"
    r"ほしい|欲しい|ほしいんだけど|欲しいんだけど|お願い|頼む)?"
)
_EXPLICIT_SWORD_COMMAND_RE = re.compile(
    rf"{_SWORD_TARGET_PATTERN}(?:"
    r"(?:に|へ)?(?:持ち替えて?|もちかえて?|切り替えて?|きりかえて?)|"
    r"(?:に|へ|を)?(?:装備(?:して)?|そうび(?:して)?|選んで|えらんで|"
    r"構えて?|かまえて?|持って)|"
    r"(?:に|へ|を)?(?:変更(?:を)?(?:して)?|へんこう(?:を)?(?:して)?|"
    r"変えて|かえて)|"
    r"にして)"
    rf"{_REQUEST_SUFFIX_PATTERN}"
    r"(?:な|ね|よ)?$"
)
_SWORD_SELECTION_PREFERENCE_RE = re.compile(
    r"(?:剣|けん|つるぎ|ソード|そーど)(?:の)?(?:方|ほう)(?:が)?"
    r"(?:よくない|良くない|いい|ええ)(?:の|ん)?(?:か)?"
)
_DIRECT_SWORD_SELECTION_PREFERENCE_RE = re.compile(
    rf"(?:(?:そろそろ|ここは|今は?|次は?|やっぱり?|もう))?"
    rf"{_SWORD_TARGET_PATTERN}(?:の)?(?:方|ほう)(?:が)?"
    r"(?:よくない|良くない|いい|ええ)(?:の|ん)?(?:か)?$"
)
_NON_REQUEST_MARKERS = (
    "話",
    "ある",
    "持ってる",
    "作",
    "好き",
    "かっこ",
    "どこ",
    "何本",
    "なんぼん",
)
_NEGATED_ACTION_MARKERS = ("ないで", "なくていい", "んでいい", "やめて", "不要", "いらない")
_FULL_TEXT_NON_REQUEST_MARKERS = (
    *_NEGATED_ACTION_MARKERS,
    "わけではない",
    "わけではありません",
    "わけじゃない",
    "わけやない",
    "つもりはない",
    "つもりはありません",
    "つもりじゃない",
    "つもりやない",
    "てほしくない",
    "てほしくありません",
    "という意味ではない",
    "という意味ではありません",
    "という意味じゃない",
    "たら",
    "場合",
    "すると",
    "したら",
    "どうなる",
    "何が起き",
    "べき",
)
_ACTION_META_MENTION_RE = re.compile(
    r"(?:って(?:何|なに|どういう(?:意味|こと)|どんな意味|言葉の意味)|"
    r"(?<!こ)とは(?:何|なに|どういう意味|どんな意味)?|"
    r"(?:の|という(?:言葉|表現)?(?:の)?)(?:意味|意図)|"
    r"というのは(?:何|なに|どういう(?:意味|こと)))"
)
_ACTION_REPORT_MARKERS = (
    "という表現",
    "という言葉",
    "という文",
    "は命令文",
    "と書いて",
    "って書いて",
    "と言った",
    "って言った",
    "と言って",
    "って言って",
    "と言われ",
    "って言われ",
)
_QUOTE_CHARS = "「」『』\"'“”‘’"
_WORKSHOP_EDIT_CONTEXT_MARKERS = (
    "句",
    "川柳",
    "俳句",
)
_DIRECT_WORLD_COMMAND_PREFIX_RE = re.compile(
    r"(?:(?:ねえ|なあ)?ドギド(?:を)?|"
    r"(?:今の)?(?:武器|手持ち|持ち物|装備)(?:を)?|"
    r"(?:斧|おの|弓|ゆみ|つるはし|ツルハシ|シャベル|スコップ|クワ|素手)から|"
    r"そろそろ|ちょっと|今は?|ここは)*$"
)
_PAST_SWORD_ACTION_RE = re.compile(
    r"(?:持ち替え|もちかえ|切り替え|きりかえ|装備し|そうびし|"
    r"選ん|えらん|構え|かまえ|変更し|へんこうし|変え|かえ|にし)"
    r"(?:た|ました)(?:(?:ん(?:だ|です)?|よ|ね|よね|けど|が|から|ので|"
    r"だけ|ところ|わ|か|っけ|でしょう))*$"
)
_VOICE_SWORD_TARGET_ALIASES = ("県", "件", "券", "腱", "チェン", "ケン")
_VOICE_SWORD_HOMOPHONE_ACTION_RE = re.compile(
    rf"(?:{'|'.join(map(re.escape, _VOICE_SWORD_TARGET_ALIASES))})[\s、,]*(?:に|へ|を)?[\s、,]*"
    r"(?:持ち替え|もちかえ|切り替え|きりかえ|装備|そうび|構え|かまえ|"
    r"変更|へんこう|変えて|かえて|ハインコ)"
)


def _compact(text: str) -> str:
    return re.sub(r"[\s、。！？!?・,./]", "", (text or "").strip())


def mentions_sword_target(text: str) -> bool:
    compact = _compact(text)
    return any(target in compact for target in _SWORD_TARGETS)


def is_bare_sword_target(text: str) -> bool:
    """操作語のない単独語。かなの「けん」を実行キーにしないためにも使う。"""

    return _compact(text) in _SWORD_TARGETS


def _full_text_disqualifies_sword_request(text: str) -> bool:
    compact = _compact(text)
    return bool(
        any(marker in compact for marker in _FULL_TEXT_NON_REQUEST_MARKERS)
        or _ACTION_META_MENTION_RE.search(compact) is not None
        or _PAST_SWORD_ACTION_RE.search(compact) is not None
        or any(marker in compact for marker in _ACTION_REPORT_MARKERS)
        or any(char in text for char in _QUOTE_CHARS)
    )


def _is_direct_sword_selection_preference(text: str) -> bool:
    """疑問符で閉じた、剣を選択先とする直接の相談だけを許可する。"""

    return bool(
        (text or "").rstrip().endswith(("?", "？"))
        and _DIRECT_SWORD_SELECTION_PREFERENCE_RE.fullmatch(_compact(text)) is not None
        and not _full_text_disqualifies_sword_request(text)
    )


def has_workshop_edit_context(text: str) -> bool:
    """現在句の既知行呼称または詩句の明示を含むか。"""

    compact = _compact(text)
    return bool(
        explicit_workshop_line_index(text) is not None
        or any(marker in compact for marker in _WORKSHOP_EDIT_CONTEXT_MARKERS)
    )


def _has_direct_world_command_prefix(
    text: str,
    *,
    allow_voice_prefix: bool = False,
) -> bool:
    """剣より前が現在の世界操作を直接指す閉じた語彙か。

    条件節・後刻指定・例示など任意の前置きが付いた文は、世界操作の
    fast path へ入れない。``時と`` は実測済みの音声認識誤変換として
    voice-only で扱う。
    """

    compact = _compact(text)
    match = _EXPLICIT_SWORD_COMMAND_RE.search(compact)
    if match is None:
        return False
    prefix = compact[: match.start()]
    return bool(
        _DIRECT_WORLD_COMMAND_PREFIX_RE.fullmatch(prefix) is not None
        or (allow_voice_prefix and prefix == "時と")
    )


def is_unambiguous_voice_select_sword_request(text: str) -> bool:
    """voice補正後でもworkshopを迂回できる、対象直結の世界操作。"""

    return bool(
        is_explicit_voice_select_sword_request(text)
        and not has_workshop_edit_context(text)
        and _has_direct_world_command_prefix(text, allow_voice_prefix=True)
    )


def _is_explicit_select_sword_request(
    text: str,
    *,
    allow_voice_prefix: bool,
) -> bool:
    """代表命令を、剣より前の直接性まで含めて検証する。"""

    compact = _compact(text)
    if not compact or not mentions_sword_target(compact):
        return False
    if _full_text_disqualifies_sword_request(text):
        return False
    if compact in _DIRECT_SWORD_REQUESTS:
        return True
    if any(marker in compact for marker in _NON_REQUEST_MARKERS):
        return False
    return bool(
        _EXPLICIT_SWORD_COMMAND_RE.search(compact) is not None
        and _has_direct_world_command_prefix(
            text,
            allow_voice_prefix=allow_voice_prefix,
        )
    )


def is_explicit_select_sword_request(text: str) -> bool:
    """誤反応を避けた代表命令のfast path。曖昧形はQwenへ回す。"""

    return _is_explicit_select_sword_request(text, allow_voice_prefix=False)


def is_explicit_voice_select_sword_request(text: str) -> bool:
    """音声で実測した限定接頭語だけを追加した代表命令判定。"""

    return _is_explicit_select_sword_request(text, allow_voice_prefix=True)


def is_unambiguous_select_sword_request(text: str) -> bool:
    """workshop中でも句の語替えと衝突しない、操作語を伴う命令。"""

    compact = _compact(text)
    return bool(
        not is_bare_sword_target(text)
        and is_explicit_select_sword_request(text)
        and not has_workshop_edit_context(text)
        and _has_direct_world_command_prefix(text)
        and any(
            marker in compact
            for marker in (
                "持ち替え",
                "もちかえ",
                "切り替え",
                "きりかえ",
                "装備",
                "そうび",
                "構えて",
                "かまえて",
            )
        )
    )


def interpret_voice_select_sword_request(text: str) -> str | None:
    """Voice-only repair for the observed ``県に持ち替え`` sword homophone.

    A bare ``県`` remains ordinary conversation.  The repair is offered only when
    the homophone is directly followed by a weapon-selection verb, and the
    repaired sentence still passes the same narrow explicit-request guard.
    """

    source = text or ""
    match = _VOICE_SWORD_HOMOPHONE_ACTION_RE.search(source)
    if match is None:
        return None
    matched = match.group(0)
    repaired_match = matched
    for alias in _VOICE_SWORD_TARGET_ALIASES:
        if alias in repaired_match:
            repaired_match = repaired_match.replace(alias, "剣", 1)
            break
    repaired_match = repaired_match.replace("ハインコ", "変更")
    repaired = f"{source[:match.start()]}{repaired_match}{source[match.end():]}"
    if not is_explicit_voice_select_sword_request(repaired):
        return None
    return repaired


@dataclass(frozen=True, slots=True)
class SelectSwordIntent:
    requested: bool = False
    evidence: str = ""
    confidence: float = 0.0
    source: str = "none"


def finalize_select_sword_intent_payload(
    payload: object,
    *,
    player_text: str,
    min_confidence: float = SELECT_SWORD_LLM_MIN_CONFIDENCE,
) -> SelectSwordIntent:
    """Qwen出力を閉じたenum・発話中evidence・高信頼度で検証する。"""

    if not isinstance(payload, dict):
        return SelectSwordIntent()
    if payload.get("intent") != "select_weapon" or payload.get("weapon_kind") != "sword":
        return SelectSwordIntent()
    if payload.get("is_request") is not True:
        return SelectSwordIntent()
    raw_confidence = payload.get("confidence")
    if isinstance(raw_confidence, bool):
        return SelectSwordIntent()
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        return SelectSwordIntent()
    evidence = str(payload.get("evidence") or "").strip()[:120]
    if not (0.0 <= confidence <= 1.0) or confidence < min_confidence:
        return SelectSwordIntent()
    compact_evidence = _compact(evidence)
    compact_player_text = _compact(player_text)
    has_action = _EXPLICIT_SWORD_COMMAND_RE.search(compact_evidence) is not None
    has_selection_preference = (
        _SWORD_SELECTION_PREFERENCE_RE.search(compact_evidence) is not None
    )
    full_text_is_explicit = is_explicit_select_sword_request(player_text)
    full_text_has_selection_preference = _is_direct_sword_selection_preference(
        player_text
    )
    if (
        len(compact_evidence) < 2
        or evidence not in player_text
        or not mentions_sword_target(evidence)
        or is_bare_sword_target(evidence)
        or not (has_action or has_selection_preference)
        or not (full_text_is_explicit or full_text_has_selection_preference)
        or (
            not has_selection_preference
            and any(marker in compact_evidence for marker in _NON_REQUEST_MARKERS)
        )
        or any(marker in compact_evidence for marker in _NEGATED_ACTION_MARKERS)
        or any(marker in compact_player_text for marker in _FULL_TEXT_NON_REQUEST_MARKERS)
        or _ACTION_META_MENTION_RE.search(compact_player_text) is not None
        or _PAST_SWORD_ACTION_RE.search(compact_player_text) is not None
    ):
        return SelectSwordIntent()
    return SelectSwordIntent(True, evidence, confidence, "qwen")


@dataclass(frozen=True, slots=True)
class WeaponSelection:
    slot: int
    item_id: str
    weapon_kind: str
    used_fallback: bool


def _remaining_durability(slot: HotbarSlot) -> float:
    if slot.max_damage <= 0:
        return math.inf
    return float(max(0, slot.max_damage - slot.damage))


def select_weapon_slot(slots: list[HotbarSlot]) -> WeaponSelection | None:
    """docsの剣優先・fallback順を決定的に適用する。"""

    candidates = [slot for slot in slots if slot.item_id and slot.count > 0]
    swords = [slot for slot in candidates if slot.weapon_kind == "sword"]
    if swords:
        chosen = min(
            swords,
            key=lambda slot: (
                slot.attack_damage if slot.attack_damage is not None else math.inf,
                _remaining_durability(slot),
                slot.slot,
            ),
        )
        return WeaponSelection(chosen.slot, str(chosen.item_id), chosen.weapon_kind, False)

    for kind in ("trident", "axe", "bow", "tool"):
        same_kind = [slot for slot in candidates if slot.weapon_kind == kind]
        if not same_kind:
            continue
        chosen = min(
            same_kind,
            key=lambda slot: (
                -(slot.attack_damage if slot.attack_damage is not None else -math.inf),
                _remaining_durability(slot),
                slot.slot,
            ),
        )
        return WeaponSelection(chosen.slot, str(chosen.item_id), chosen.weapon_kind, True)
    return None


def _selection(context: ActionContext) -> WeaponSelection | None:
    hotbar = context.event.player.hotbar
    if hotbar is None:
        return None
    return select_weapon_slot(hotbar.slots)


def _build_command(context: ActionContext) -> SelectHotbarCommand:
    selection = _selection(context)
    if selection is None:  # gateの直後にも再評価し、直接handlerを呼ばれてもfail closed
        raise ValueError("select_sword is unavailable")
    return SelectHotbarCommand(
        command_id=f"cmd_{uuid4().hex}",
        slot=selection.slot,
        expected_item_id=selection.item_id,
        issued_at=context.now,
        expires_at=context.now + timedelta(seconds=2),
    )


def build_select_sword_spec() -> ActionSpec:
    return ActionSpec(
        name=ActionName.SELECT_SWORD,
        capability=SELECT_HOTBAR_CAPABILITY,
        policy=RiskPolicy.AUTO,
        available=lambda context: _selection(context) is not None,
        handler=_build_command,
    )
