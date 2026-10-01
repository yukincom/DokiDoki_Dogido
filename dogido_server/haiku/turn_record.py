"""Record the service boundary, including code/legacy paths and silent outcomes.

This is diagnostic data, never dialogue context or permission to mutate a poem.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import logging
from typing import Any
from uuid import uuid4

LOGGER = logging.getLogger(__name__)
_turn: ContextVar[dict[str, Any] | None] = ContextVar("workshop_record", default=None)


def _private_input(session: Any) -> bool:
    """Capture before dispatch: a return-from-Web input can clear this state."""
    foreground = getattr(session, "foreground_dialogue", None)
    runtime = getattr(session, "language_runtime", None)
    return bool(
        getattr(foreground, "route", None) == "web"
        or (runtime is not None and runtime.active_research_ttl_ms() is not None)
    )


def snapshot(workshop: Any) -> dict[str, Any] | None:
    if workshop is None:
        return None
    return {
        "workshop_id": workshop.hud_id,
        "entry_id": workshop.entry_id,
        "open": workshop.open,
        "combat_paused": workshop.combat_paused,
        "close_reason": workshop.close_reason,
        "canonical": workshop.display_line(),
        "pending": workshop.pending_revision,
    }


def note_input(raw: str, semantic: str, *, turn_id: str = "", source: str = "text") -> None:
    if (current := _turn.get()) is not None:
        current.update(
            player_text=raw, semantic_player_text=semantic, input_turn_id=turn_id, source=source
        )


def note_route(route: str) -> None:
    if (current := _turn.get()) is not None:
        current["route"] = route


def note_steps(steps: list[dict[str, Any]]) -> bool:
    if (current := _turn.get()) is None:
        return False
    current["steps"] = steps[-6:]
    return True


def note_validation(reasons: tuple[str, ...]) -> None:
    if (current := _turn.get()) is not None:
        current["validation_codes"] = list(reasons)[:16]


def note_operation(
    action: str, outcome: str, *, canonical_after: str | None = None, revision_id: str | None = None
) -> None:
    if (current := _turn.get()) is not None:
        current["operation"] = {
            "action": action,
            "outcome": outcome,
            "canonical_after": canonical_after,
            "revision_id": revision_id,
        }


def record_workshop_event(method):
    @wraps(method)
    def recorded(self, event, session_id=None, idempotency_key=None):
        session = self._ensure_session(event, session_id)
        original_workshop = session.haiku_workshop
        before = snapshot(original_workshop)
        private = _private_input(session)
        raw = (event.meta.user_text or "").strip()
        current = {"route": "code", "steps": [], "validation_codes": []}
        token = _turn.set(current)
        result = None
        error = None
        try:
            result = method(self, event, session.session_id, idempotency_key)
            return result
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            _turn.reset(token)
            try:
                after = snapshot(
                    session.haiku_workshop
                    or (
                        original_workshop
                        if original_workshop and not original_workshop.open
                        else None
                    )
                )
                raw = current.get("player_text") or raw
                duplicate = result is not None and result.response.deduplicated
                active = bool((before and before["open"]) or (after and after["open"]))
                if (
                    self.memory is not None
                    and not private
                    and not duplicate
                    and active
                    and (raw or before != after)
                ):
                    state = before or after or {}
                    actions = result.actions if result is not None else []
                    semantic = current.get("semantic_player_text") or raw
                    operation = current.get("operation", {})
                    outcome = (
                        "failed"
                        if error
                        else "closed"
                        if before and before["open"] and not (after and after["open"])
                        else operation["outcome"]
                        if operation
                        else "canonical_changed"
                        if before and after and before["canonical"] != after["canonical"]
                        else "pending_changed"
                        if before and after and before["pending"] != after["pending"]
                        else "paused"
                        if after and after["combat_paused"]
                        else "actions_selected"
                        if actions
                        else "no_action"
                    )
                    self.memory.save_haiku_workshop_turn(
                        entry_id=state.get("entry_id"),
                        player_text=raw,
                        semantic_player_text=semantic or raw,
                        base_verse=state.get("canonical", ""),
                        pending_before=(before or {}).get("pending"),
                        pending_after=(after or {}).get("pending"),
                        steps=current["steps"],
                        observed_at=event.observed_at,
                        session_id=session.session_id,
                        record_context={
                            "event_kind": "turn_result" if raw else "lifecycle",
                            "turn_id": result.response.event_id
                            if result
                            else f"hwerror_{uuid4().hex}",
                            "workshop_id": state.get("workshop_id"),
                            "route": current["route"],
                            "outcome": outcome,
                            "state_before": before,
                            "state_after": after,
                            "canonical_after": operation.get("canonical_after")
                            or (after or {}).get("canonical"),
                            "input_turn_id": current.get("input_turn_id"),
                            "source": current.get("source", "text"),
                            "operation": operation,
                            "validation_codes": current["validation_codes"],
                            "error_kind": error,
                            "result_scope": "service_decision",
                            "playback_status": "not_observed",
                            "actions": [
                                {
                                    "layer": a.layer,
                                    "cue_id": a.cue_id,
                                    "route_owner": a.route_owner,
                                    "deferred": a.defer_player_input,
                                }
                                for a in actions
                            ],
                        },
                    )
            except Exception as exc:
                # Recording must not replace a reply, save error or original exception.
                LOGGER.warning("haiku_workshop_record_failed detail=%s", type(exc).__name__)

    return recorded


def record_boundary(service, session, *, before, after, text="", kind, result):
    """Admission and disconnection can finish without a game event."""
    if service.memory is None or not ((before or {}).get("open") or (after or {}).get("open")):
        return
    if text and _private_input(session):
        return
    state = before or after or {}
    try:
        service.memory.save_haiku_workshop_turn(
            entry_id=state.get("entry_id"),
            player_text=text,
            base_verse=state.get("canonical", ""),
            pending_before=(before or {}).get("pending"),
            pending_after=(after or {}).get("pending"),
            steps=[],
            session_id=session.session_id,
            record_context={
                "event_kind": kind,
                "turn_id": None,
                "workshop_id": state.get("workshop_id"),
                "state_before": before,
                "state_after": after,
                "canonical_after": (after or {}).get("canonical"),
                "result": result,
                "result_scope": "service_decision",
                "playback_status": "not_observed",
            },
        )
    except Exception as exc:
        LOGGER.warning("haiku_workshop_record_failed detail=%s", type(exc).__name__)


def record_workshop_admission(method):
    @wraps(method)
    def recorded(self, text, *, source="text"):
        session = max(
            self.sessions.values(),
            key=lambda s: s.last_seen_at or datetime.min.replace(tzinfo=timezone.utc),
            default=None,
        )
        before = snapshot(session.haiku_workshop) if session else None
        private = _private_input(session) if session else False
        result = method(self, text, source=source)
        if session and not private:
            record_boundary(
                self,
                session,
                before=before,
                after=snapshot(session.haiku_workshop),
                text=text,
                kind="input_admission",
                result={**result, "source": source},
            )
        return result

    return recorded
