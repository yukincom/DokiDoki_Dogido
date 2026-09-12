from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from dogido_server.app import create_app
from dogido_server.config import Settings
from dogido_server.haiku.edit_contract import PLAYER_LINE_EDIT_CONTRACT_VERSION
from dogido_server.haiku.hud import WorkshopHudSnapshots, project_workshop
from dogido_server.haiku.workshop import (
    PlayerLineReplacement, RecentHaikuWorkshop, advance_workshop_revision,
    build_player_line_revision, clear_pending_revision, pause_workshop_for_combat,
    resume_workshop_after_combat,
)


NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
VERSE = "くわをもち\nはたけのまえで\nひとやすみ"


def session():
    return SimpleNamespace(
        session_id="ses_hud", last_sequence=4,
        haiku_workshop=RecentHaikuWorkshop(surface_text=VERSE, emitted_at=NOW),
        machine=SimpleNamespace(state=SimpleNamespace(mode="normal")),
    )


def stage(workshop):
    result = build_player_line_revision(workshop, PlayerLineReplacement(text="ひとねむり", explicit_line_index=2))
    assert result.text
    workshop.pending_revision = result.text
    workshop.pending_revision_surface_text = result.surface_text
    workshop.pending_revision_lines = result.lines
    workshop.pending_revision_base_text = result.base_text
    workshop.pending_revision_edits = [dict(edit) for edit in result.edits]
    workshop.pending_revision_edit_contract = PLAYER_LINE_EDIT_CONTRACT_VERSION
    workshop.pending_revision_source = "player_line_confirmed"
    workshop.hud_editing = True
    workshop.hud_selected_line = 2


def test_current_and_pending_are_distinct_and_get_never_mutates():
    s = session()
    stage(s.haiku_workshop)
    snapshot = project_workshop(s)
    assert snapshot["canonical_lines"] == VERSE.splitlines()
    assert snapshot["pending_lines"] == ["くわをもち", "はたけのまえで", "ひとねむり"]
    assert snapshot["selected_line"] == 2
    s.haiku_workshop.pending_revision_base_text = "stale"
    assert project_workshop(s)["pending_lines"] == []
    assert s.haiku_workshop.pending_revision.endswith("ひとねむり")


def test_mark_does_not_mean_edit_and_closed_or_missing_verse_is_hidden():
    s = session()
    s.haiku_workshop.marked_line_index = 1
    assert project_workshop(s)["editing"] is False
    assert project_workshop(s)["selected_line"] is None
    s.haiku_workshop.surface_text = "malformed"
    assert project_workshop(s)["state"] == "closed"
    s.haiku_workshop = None
    assert project_workshop(s)["canonical_lines"] == []


def test_pause_keeps_identity_and_verse_but_hides_edit_cue():
    s = session()
    stage(s.haiku_workshop)
    before = project_workshop(s)
    pause_workshop_for_combat(s.haiku_workshop, now=NOW)
    paused = project_workshop(s)
    assert paused["state"] == "danger"
    assert paused["pending_lines"] == before["pending_lines"]
    assert not paused["editing"]
    resume_workshop_after_combat(s.haiku_workshop, now=NOW + timedelta(seconds=30), reason="safe", ask_confirmation=True)
    assert project_workshop(s)["workshop_id"] == before["workshop_id"]
    assert project_workshop(s)["state"] == "open"
    s.machine.state.mode = "alert"
    assert project_workshop(s)["state"] == "danger"
    s.haiku_workshop.combat_override_signature = "verified-single-enemy"
    assert project_workshop(s)["state"] == "open"
    assert project_workshop(s)["provisional_resume"]
    pause_workshop_for_combat(s.haiku_workshop, now=NOW + timedelta(seconds=40))
    assert not project_workshop(s)["provisional_resume"]


def test_accept_and_reject_update_projection_without_false_pending():
    s = session()
    stage(s.haiku_workshop)
    clear_pending_revision(s.haiku_workshop)
    assert project_workshop(s)["canonical_lines"] == VERSE.splitlines()
    assert not project_workshop(s)["editing"]
    stage(s.haiku_workshop)
    advance_workshop_revision(s.haiku_workshop, revision_id="rev_test")
    snapshot = project_workshop(s)
    assert snapshot["canonical_lines"][-1] == "ひとねむり"
    assert snapshot["pending_lines"] == []
    assert snapshot["state"] == "open"
    assert not snapshot["editing"]


def test_cache_is_detached_id_stable_and_removed_sessions_disappear():
    s = session()
    cache = WorkshopHudSnapshots()
    cache.publish({s.session_id: s})
    first = cache.get(s.session_id)
    first["canonical_lines"].clear()
    assert len(cache.get(s.session_id)["canonical_lines"]) == 3
    s.haiku_workshop.open = False
    assert cache.get(s.session_id)["state"] == "open"  # only publish commits a projection
    cache.publish({s.session_id: s})
    assert cache.get(s.session_id)["state"] == "closed"
    cache.publish({})
    assert cache.get(s.session_id) is None


def make_client():
    return TestClient(create_app(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False, auth_token="hud-test-token")))


AUTH = {"Authorization": "Bearer hud-test-token"}
CREATE = {"adapter_name": "hud-test", "adapter_version": "1", "game": "minecraft-java", "schema_version": "2026-05-24", "player_name": "test"}


def test_endpoint_auth_creation_closure_and_no_store():
    with make_client() as client:
        assert client.get("/api/v1/haiku-workshop/snapshot?session_id=ses_unknown").status_code == 401
        sid = client.post("/api/v1/adapter-sessions", json=CREATE, headers=AUTH).json()["session_id"]
        url = f"/api/v1/haiku-workshop/snapshot?session_id={sid}"
        result = client.get(url, headers=AUTH)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["state"] == "closed"
        client.delete(f"/api/v1/adapter-sessions/{sid}", headers=AUTH)
        assert client.get(url, headers=AUTH).status_code == 404


def test_cached_get_does_not_wait_for_busy_service_worker(monkeypatch):
    with make_client() as client:
        sid = client.post("/api/v1/adapter-sessions", json=CREATE, headers=AUTH).json()["session_id"]
        service = client.app.state.service
        original = service.heartbeat
        entered, release = Event(), Event()
        def busy(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, "heartbeat", busy)
        with ThreadPoolExecutor(max_workers=2) as pool:
            action = pool.submit(client.post, f"/api/v1/adapter-sessions/{sid}/heartbeat", json={"sent_at": NOW.isoformat()}, headers=AUTH)
            try:
                assert entered.wait(2)
                read = pool.submit(client.get, f"/api/v1/haiku-workshop/snapshot?session_id={sid}", headers=AUTH)
                assert read.result(timeout=1).status_code == 200
            finally:
                release.set()
            assert action.result(timeout=2).status_code == 200


def test_worker_failure_still_publishes_completed_state(monkeypatch):
    with make_client() as client:
        sid = client.post("/api/v1/adapter-sessions", json=CREATE, headers=AUTH).json()["session_id"]
        service = client.app.state.service
        def fail(*args, **kwargs):
            service.sessions[sid].haiku_workshop = RecentHaikuWorkshop(surface_text=VERSE, emitted_at=NOW)
            raise ValueError("test failure after state change")
        monkeypatch.setattr(service, "heartbeat", fail)
        with pytest.raises(ValueError):
            client.post(f"/api/v1/adapter-sessions/{sid}/heartbeat", json={"sent_at": NOW.isoformat()}, headers=AUTH)
        assert client.get(f"/api/v1/haiku-workshop/snapshot?session_id={sid}", headers=AUTH).json()["state"] == "open"
