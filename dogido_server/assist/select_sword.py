from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import math
import re
from uuid import uuid4

from dogido_server.assist.types import ActionContext, ActionName, ActionSpec, RiskPolicy
from dogido_server.models import HotbarSlot, SelectHotbarCommand


SELECT_HOTBAR_CAPABILITY = "client.hotbar.select.v1"
SELECT_SWORD_LLM_MIN_CONFIDENCE = 0.90
SELECT_SWORD_RULE_VERSION = "2026-08-27.1"

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
_REQUEST_ACTION_RE = re.compile(
    r"(?:持ち替え|もちかえ|切り替え|きりかえ|装備して|そうびして|"
    r"選んで|えらんで|構えて|かまえて|変更|へんこう|変えて|かえて|にして)"
)
_NON_REQUEST_MARKERS = (
    "話",
    "ある",
    "ない",
    "持ってる",
    "作",
    "好き",
    "かっこ",
    "どこ",
    "何本",
    "なんぼん",
)
_NEGATED_ACTION_MARKERS = ("ないで", "なくていい", "んでいい", "やめて", "不要", "いらない")
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


def is_explicit_select_sword_request(text: str) -> bool:
    """誤反応を避けた代表命令のfast path。曖昧形はQwenへ回す。"""

    compact = _compact(text)
    if not compact or not mentions_sword_target(compact):
        return False
    if any(marker in compact for marker in _NEGATED_ACTION_MARKERS):
        return False
    if compact in _DIRECT_SWORD_REQUESTS:
        return True
    if any(marker in compact for marker in _NON_REQUEST_MARKERS):
        return False
    return _REQUEST_ACTION_RE.search(compact) is not None


def is_unambiguous_select_sword_request(text: str) -> bool:
    """workshop中でも句の語替えと衝突しない、操作語を伴う命令。"""

    compact = _compact(text)
    if not mentions_sword_target(compact):
        return False
    return any(
        marker in compact
        for marker in ("持ち替え", "もちかえ", "切り替え", "きりかえ", "装備", "そうび", "構えて", "かまえて")
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
    if not is_explicit_select_sword_request(repaired):
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
    if (
        len(_compact(evidence)) < 2
        or evidence not in player_text
        or not mentions_sword_target(evidence)
        or is_bare_sword_target(evidence)
        or any(marker in _compact(evidence) for marker in _NEGATED_ACTION_MARKERS)
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
