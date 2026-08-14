"""川柳workshopの戦闘pause／再開ポリシー。

状態機械は戦況を決め、このモジュールは句pinを保持するかだけを決める。
OS AIは中断中発話の意味抽出だけ。発話・保存・敵への行動判断はコードが決める。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from dogido_server.models import EventName, GameEvent

from .workshop import (
    RecentHaikuWorkshop,
    classify_workshop_intent,
    combat_resume_confirmation_decision,
    close_workshop,
    is_open,
    mentioned_workshop_line_fragment,
    pause_workshop_for_combat,
    record_workshop_activity,
    resume_workshop_after_combat,
)

LOGGER = logging.getLogger("uvicorn.error")

COMBAT_WORKSHOP_INPUT_ACTIONS = frozenset(
    {
        "resume_workshop",
        "workshop_input",
        "unrelated",
        "uncertain",
    }
)
COMBAT_WORKSHOP_INPUT_MIN_CONFIDENCE = 0.75


@dataclass(frozen=True, slots=True)
class WorkshopCombatUpdate:
    """serviceへ返す、コード確定済みの小さな実行指示。"""

    reply_text: str | None = None
    consume_player_input: bool = False
    replace_speech: bool = False
    closed: bool = False


@dataclass(frozen=True, slots=True)
class CombatWorkshopInputAnalysis:
    """OS AIが抽出した中断中発話の意味。状態変更はこの型の外で行う。"""

    action: str = "uncertain"
    confidence: float = 0.0
    evidence: str = ""


def event_interrupts_workshop(
    event: GameEvent,
    *,
    recent_damage_window_ms: int,
) -> bool:
    """現在フレームに、句より優先する実脅威があるか。"""

    if event.visual_threats or event.auditory_threats:
        return True
    # 古いadapterはcombat_endedにも直前のactive hintを残すことがある。
    if event.event.name == EventName.COMBAT_ENDED:
        return False
    recent_damage = event.combat.recent_damage_ms
    if recent_damage is not None and recent_damage <= recent_damage_window_ms:
        return True
    return bool(event.combat.combat_active_hint)


def threat_signature(event: GameEvent) -> str:
    """暫定再開後に「同じ単独敵」かを照合する短い署名。"""

    keys = [
        f"{(threat.entity_id or '-').strip()}:{(threat.type or '').strip().lower()}"
        for threat in event.visual_threats
        if (threat.type or "").strip()
    ]
    return "|".join(sorted(keys))


def build_combat_workshop_input_details(
    workshop: RecentHaikuWorkshop,
    player_text: str,
) -> dict[str, object]:
    """中断中の発話だけを端末内AIへ渡す、小さな入力契約。"""

    return {
        "verse": workshop.editing_line(),
        "player_text": (player_text or "").strip(),
        "allowed_actions": sorted(COMBAT_WORKSHOP_INPUT_ACTIONS),
    }


def _compact_evidence(text: str | None) -> str:
    """空白と引用符の揺れだけを除き、AIの根拠が原文にあるか照合する。"""

    return "".join(
        char
        for char in str(text or "").casefold()
        if not char.isspace() and char not in "「」『』\"'"
    )


def finalize_combat_workshop_input_payload(
    payload: dict[str, Any] | None,
    *,
    player_text: str,
    min_confidence: float = COMBAT_WORKSHOP_INPUT_MIN_CONFIDENCE,
) -> CombatWorkshopInputAnalysis:
    """OS AI出力を、発話中の根拠を持つ閉じた値だけに絞る。"""

    if not isinstance(payload, dict):
        return CombatWorkshopInputAnalysis()
    action = str(payload.get("action") or "uncertain").strip()
    if action not in COMBAT_WORKSHOP_INPUT_ACTIONS:
        return CombatWorkshopInputAnalysis()
    raw_confidence = payload.get("confidence")
    try:
        confidence = float(raw_confidence) if not isinstance(raw_confidence, bool) else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
    evidence = str(payload.get("evidence") or "").strip()[:120]
    player_compact = _compact_evidence(player_text)
    evidence_compact = _compact_evidence(evidence)
    if (
        not 0.0 <= confidence <= 1.0
        or confidence < min_confidence
        or len(evidence_compact) < 2
        or evidence_compact not in player_compact
    ):
        return CombatWorkshopInputAnalysis()
    return CombatWorkshopInputAnalysis(
        action=action,
        confidence=confidence,
        evidence=evidence,
    )


def fallback_combat_workshop_input_analysis(
    workshop: RecentHaikuWorkshop,
    text: str | None,
) -> CombatWorkshopInputAnalysis:
    """OS AIが判断不能な場合だけ使う、閉じた明示表現のfallback。"""

    source = str(text or "").strip()
    if not source:
        return CombatWorkshopInputAnalysis()
    compact = "".join(source.split())
    if combat_resume_confirmation_decision(source) == "resume":
        return CombatWorkshopInputAnalysis("resume_workshop", 1.0, source)
    if any(
        marker in compact
        for marker in (
            "句を続け",
            "句の続き",
            "句に戻",
            "川柳を続け",
            "川柳の続き",
            "ワークショップを続け",
            "ワークショップに戻",
            "推敲を続け",
            "添削を続け",
            "さっきの句",
        )
    ):
        return CombatWorkshopInputAnalysis("resume_workshop", 1.0, source)
    if mentioned_workshop_line_fragment(workshop, source) is not None:
        return CombatWorkshopInputAnalysis("workshop_input", 1.0, source)
    return CombatWorkshopInputAnalysis()


def _low_threat_resume_ready(
    event: GameEvent,
    *,
    stable_since: datetime | None,
    recent_damage_window_ms: int,
    resume_delay_ms: int,
) -> bool:
    """動けない敵を無視できる最小条件。明示的な再開意思は別に確認する。"""

    if len(event.visual_threats) != 1 or event.auditory_threats:
        return False
    threat = event.visual_threats[0]
    hostile_type = (threat.type or "").strip().lower()
    if any(name in hostile_type for name in ("warden", "dragon", "wither")):
        return False
    if threat.approaching or threat.distance is None or threat.distance <= 3.0:
        return False
    if event.combat.hostiles_within_10 is not None and event.combat.hostiles_within_10 > 1:
        return False
    recent_damage = event.combat.recent_damage_ms
    if recent_damage is not None and recent_damage <= recent_damage_window_ms:
        return False
    if not threat_signature(event) or stable_since is None:
        return False
    stable_ms = max(0.0, (event.observed_at - stable_since).total_seconds() * 1000.0)
    return stable_ms >= resume_delay_ms


def _has_victory_evidence(event: GameEvent) -> bool:
    return bool(
        (event.combat.nearby_experience_orb_count or 0) > 0
        or event.combat.warden_defeat_confirmed
        or event.combat.dragon_defeat_confirmed
    )


def _resume_prompt(workshop: RecentHaikuWorkshop, reason: str) -> str:
    if reason == "victory":
        lead = "やったな。倒せたみたいや。"
    elif reason == "escaped":
        lead = "ひとまず離れられたみたいやな。"
    else:
        lead = "落ち着いたみたいやな。"
    return f"{lead}中断してた句はこれやで。\n{workshop.editing_line()}\n続ける？"


def update_workshop_combat_state(
    workshop: RecentHaikuWorkshop | None,
    event: GameEvent,
    *,
    raw_player_text: str | None,
    input_action: str = "uncertain",
    input_present: bool | None = None,
    state_mode: str,
    has_speech: bool,
    stable_since: datetime | None,
    recent_damage_window_ms: int,
    combat_clear_time_ms: int,
    resume_delay_ms: int,
    session_id: str,
) -> WorkshopCombatUpdate:
    """戦闘フレームに合わせてpause／再開をコードで確定する。"""

    if not is_open(workshop) or workshop is None:
        return WorkshopCombatUpdate()

    danger = event_interrupts_workshop(
        event,
        recent_damage_window_ms=recent_damage_window_ms,
    )
    signature = threat_signature(event)
    raw_text = str(raw_player_text or "").strip()
    player_input_present = bool(raw_text) if input_present is None else bool(input_present)
    resume_requested = input_action in {"resume_workshop", "workshop_input"}
    substantive_resume = input_action == "workshop_input"

    if not workshop.combat_paused:
        if danger:
            override_is_safe = bool(
                workshop.combat_override_signature
                and signature == workshop.combat_override_signature
                and _low_threat_resume_ready(
                    event,
                    stable_since=stable_since,
                    recent_damage_window_ms=recent_damage_window_ms,
                    resume_delay_ms=resume_delay_ms,
                )
            )
            if override_is_safe:
                return WorkshopCombatUpdate()
            if pause_workshop_for_combat(
                workshop,
                now=event.observed_at,
                hostile_types=[threat.type for threat in event.visual_threats],
            ):
                LOGGER.warning(
                    "haiku_workshop_paused session_id=%s reason=combat hostiles=%s "
                    "pending=%s verse=%s",
                    session_id,
                    ",".join(workshop.combat_hostile_types) or "auditory_or_damage",
                    bool(workshop.pending_revision),
                    workshop.editing_line()[:100],
                )
        elif workshop.combat_override_signature:
            workshop.combat_override_signature = None
        return WorkshopCombatUpdate()

    # 中断中でも明示終了だけは安全なコード判定で受け付ける。
    if (
        raw_text
        and classify_workshop_intent(raw_text, verse=workshop.editing_line()) == "close"
    ):
        close_workshop(workshop, reason="combat_interrupted_close")
        LOGGER.warning(
            "haiku_workshop_closed session_id=%s reason=combat_interrupted_close",
            session_id,
        )
        return WorkshopCombatUpdate(
            reply_text=None if has_speech else "おけ、句はここまでにしよか。",
            consume_player_input=True,
            closed=True,
        )

    if danger:
        # 復帰待ち中に戦闘が再開したら、前回の勝利／離脱ラベルは捨てる。
        workshop.combat_resume_pending_reason = None
        current_types = [
            str(threat.type)
            for threat in event.visual_threats
            if (threat.type or "").strip()
        ]
        if current_types:
            workshop.combat_hostile_types = list(dict.fromkeys(current_types))
        if not resume_requested:
            return WorkshopCombatUpdate()
        if _low_threat_resume_ready(
            event,
            stable_since=stable_since,
            recent_damage_window_ms=recent_damage_window_ms,
            resume_delay_ms=resume_delay_ms,
        ):
            resume_workshop_after_combat(
                workshop,
                now=event.observed_at,
                reason="ignored",
                ask_confirmation=False,
                override_signature=signature,
            )
            record_workshop_activity(workshop, now=event.observed_at)
            LOGGER.warning(
                "haiku_workshop_resumed session_id=%s reason=player_override signature=%s verse=%s",
                session_id,
                signature,
                workshop.editing_line()[:100],
            )
            if substantive_resume:
                return WorkshopCombatUpdate()
            return WorkshopCombatUpdate(
                reply_text=(
                    "敵はまだ見えとるけど、今は近づいてきてへん。句は戻すで。\n"
                    f"{workshop.editing_line()}"
                ),
                consume_player_input=True,
                replace_speech=True,
            )
        LOGGER.warning(
            "haiku_workshop_resume_rejected session_id=%s reason=threat_not_stable "
            "signature=%s approaching=%s damage_ms=%s",
            session_id,
            signature or "-",
            any(threat.approaching for threat in event.visual_threats),
            event.combat.recent_damage_ms,
        )
        return WorkshopCombatUpdate(
            reply_text=None if has_speech else "まだ近づかれるかもしれん。句はしまっとくで。",
            consume_player_input=True,
        )

    paused_ms = (
        max(0.0, (event.observed_at - workshop.combat_paused_at).total_seconds() * 1000.0)
        if workshop.combat_paused_at is not None
        else 0.0
    )
    clear_enough = event.event.name == EventName.COMBAT_ENDED or (
        state_mode == "normal"
        and not bool(event.combat.combat_active_hint)
        and paused_ms >= combat_clear_time_ms
    )
    if not clear_enough:
        return WorkshopCombatUpdate()

    if workshop.combat_resume_pending_reason is None:
        if _has_victory_evidence(event):
            workshop.combat_resume_pending_reason = "victory"
        elif event.event.name == EventName.COMBAT_ENDED:
            workshop.combat_resume_pending_reason = "escaped"
        else:
            workshop.combat_resume_pending_reason = "safe"
    reason = workshop.combat_resume_pending_reason or "safe"

    # 戦闘終了・余韻のセリフを優先し、その音声が終わる次のnormal frameで戻す。
    if state_mode != "normal" or has_speech:
        LOGGER.warning(
            "haiku_workshop_resume_waiting session_id=%s reason=%s mode=%s speech=%s",
            session_id,
            reason,
            state_mode,
            has_speech,
        )
        return WorkshopCombatUpdate()

    # 戦闘直後に別件を話している最中は、再開確認を重ねない。
    # その入力はpausedのまま通常chatへ渡し、次の無入力frameで句を戻す。
    if player_input_present and not resume_requested:
        return WorkshopCombatUpdate()

    direct_resume = bool(player_input_present and resume_requested)
    substantive_resume = bool(direct_resume and substantive_resume)
    resume_workshop_after_combat(
        workshop,
        now=event.observed_at,
        reason=reason,
        ask_confirmation=not direct_resume,
    )
    record_workshop_activity(workshop, now=event.observed_at)
    LOGGER.warning(
        "haiku_workshop_resumed session_id=%s reason=%s confirmation=%s verse=%s",
        session_id,
        reason,
        not direct_resume,
        workshop.editing_line()[:100],
    )
    if substantive_resume:
        return WorkshopCombatUpdate()
    if direct_resume:
        return WorkshopCombatUpdate(
            reply_text=f"戻ろか。中断してた句はこれやで。\n{workshop.editing_line()}",
            consume_player_input=True,
            replace_speech=True,
        )
    return WorkshopCombatUpdate(reply_text=_resume_prompt(workshop, reason))
