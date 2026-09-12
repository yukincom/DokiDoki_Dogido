"""Read-only workshop projection. No game actions, inference, or memory writes."""
from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any

from dogido_server.haiku.verse import split_haiku_verse
from dogido_server.haiku.workshop import pending_revision_is_current


def project_workshop(session: Any) -> dict[str, object]:
    workshop = session.haiku_workshop
    result: dict[str, object] = {
        "schema_version": 1,
        "session_id": session.session_id,
        "observed_sequence": session.last_sequence or 0,
        "workshop_id": None,
        "state": "closed",
        "canonical_lines": [],
        "pending_lines": [],
        "editing": False,
        "selected_line": None,
        "provisional_resume": False,
    }
    if workshop is None or not workshop.open:
        return result
    canonical = split_haiku_verse(workshop.display_surface())
    if len(canonical) != 3:
        return result
    pending = (
        split_haiku_verse(workshop.editing_surface())
        if workshop.pending_revision and pending_revision_is_current(workshop)
        else []
    )
    provisional = bool(workshop.combat_override_signature and not workshop.combat_paused)
    danger = workshop.combat_paused or (
        session.machine.state.mode in {"alert", "panic"} and not provisional
    )
    result.update(
        workshop_id=workshop.hud_id,
        state="danger" if danger else "open",
        canonical_lines=canonical,
        pending_lines=pending if len(pending) == 3 else [],
        editing=workshop.hud_editing and not danger,
        provisional_resume=provisional and not danger,
        selected_line=(
            workshop.hud_selected_line
            if workshop.hud_editing and not danger
            and workshop.hud_selected_line in (0, 1, 2)
            else None
        ),
    )
    return result


class WorkshopHudSnapshots:
    """Publish only on the service worker; GET reads detached values under a short lock."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshots: dict[str, dict[str, object]] = {}
        self._revision = 0

    def publish(self, sessions: Any) -> None:
        snapshots = {key: project_workshop(session) for key, session in sessions.items()}
        with self._lock:
            if snapshots != self._snapshots:
                self._revision += 1
            self._snapshots = snapshots

    def get(self, session_id: str) -> dict[str, object] | None:
        with self._lock:
            snapshot = self._snapshots.get(session_id)
            if snapshot is None:
                return None
            return {**deepcopy(snapshot), "revision": self._revision}

    def clear(self) -> None:
        with self._lock:
            self._snapshots = {}
            self._revision += 1
