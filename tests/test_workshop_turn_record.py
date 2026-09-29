"""Real service boundary coverage, including paths that bypass the agent."""

from datetime import timedelta
import json
import pytest
from test_workshop_agent import _service_session, _agent_payload, NOW
from dogido_server.models import GameEvent


def event(text, sequence=1):
    return GameEvent.model_validate(
        {
            "schema_version": "2026-05-24",
            "adapter": "test",
            "sequence": sequence,
            "observed_at": (NOW + timedelta(seconds=sequence)).isoformat(),
            "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "normal",
                "certainty": "high",
            },
            "player": {"name": "p", "health": 20},
            "world": {
                "time_of_day": 3000,
                "time_phase": "morning",
                "weather": "clear",
                "sky_visible": True,
                "local_light": 15,
            },
            "meta": {"user_text": text},
        }
    )


def records(service):
    p = service.memory.haiku_workshop_turns_path
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_fixed_close_is_recorded_once_and_duplicate_event_is_not(tmp_path):
    service, s = _service_session(tmp_path, [])
    e = event("今日はここまで")
    service.process_event(e, s.session_id)
    rows = records(service)
    assert len(rows) == 1, rows
    assert rows[0]["player_text"] == "今日はここまで"
    assert rows[0]["state_before"]["open"] is True
    assert rows[0]["state_after"]["open"] is False
    assert rows[0]["outcome"] == "closed"
    assert rows[0]["playback_status"] == "not_observed"
    service.process_event(e, s.session_id)
    assert records(service) == rows


def test_agent_turn_has_one_complete_record_not_two(tmp_path):
    text = "羊ってどういう意味"
    service, s = _service_session(
        tmp_path,
        [
            _agent_payload(
                "explain",
                evidence=text,
                purpose="understand_meaning",
                speech="春の風の中を歩く羊やで。",
            )
        ],
    )
    service.process_event(event(text), s.session_id)
    rows = records(service)
    assert len(rows) == 1, rows
    assert rows[0]["route"] == "agent"
    assert rows[0]["steps"][0]["action"] == "explain"
    assert rows[0]["base_verse"] == rows[0]["canonical_after"]
    assert "speech" not in rows[0]["steps"][0]


def test_disabled_agent_still_records_legacy_path(tmp_path):
    service, s = _service_session(tmp_path, [])
    service.settings.haiku_workshop_agent_enabled = False
    service.settings.llm_enabled = False
    service.process_event(event("この句はどういう意味？"), s.session_id)
    rows = records(service)
    assert len(rows) == 1 and rows[0]["player_text"] == "この句はどういう意味？"


def test_failed_record_does_not_change_successful_close(tmp_path, monkeypatch, caplog):
    service, s = _service_session(tmp_path, [])

    def fail(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service.memory, "save_haiku_workshop_turn", fail)
    got = service.process_event(event("今日はここまで"), s.session_id)
    assert got.response.accepted and s.haiku_workshop is None
    assert "haiku_workshop_record_failed" in caplog.text


def test_disabled_memory_does_not_write_record(tmp_path):
    service, s = _service_session(tmp_path, [])
    service.memory = None
    service.process_event(event("今日はここまで"), s.session_id)
    assert not (tmp_path / "memory/long_term/haiku_workshop_turns.jsonl").exists()


def test_queued_input_is_recorded_at_admission_then_when_attached(tmp_path):
    text = "この句はどういう意味？"
    service, s = _service_session(
        tmp_path, [_agent_payload("explain", evidence=text, speech="春の風の中を歩く羊やで。")]
    )
    service.push_player_input(text, source="voice")
    assert [r["event_kind"] for r in records(service)] == ["input_admission"]
    service.process_event(event(""), s.session_id)
    rows = records(service)
    assert [r["event_kind"] for r in rows] == ["input_admission", "turn_result"]
    assert rows[-1]["player_text"] == text and rows[-1]["source"] == "voice"
    service.process_event(event("", 2), s.session_id)
    assert records(service) == rows


def test_held_input_does_not_become_a_new_result_on_every_tick(tmp_path):
    service, s = _service_session(tmp_path, [])
    service.settings.llm_enabled = False
    service.settings.platform_ai_provider = "chat"
    service.push_player_input("今日はいい日だね", source="voice")
    for sequence in (1, 2):
        s.machine.state.mode = "panic"
        e = event("", sequence)
        e.combat.combat_active_hint = True
        service.process_event(e, s.session_id)
    rows = records(service)
    assert sum(r["event_kind"] == "input_admission" for r in rows) == 1
    assert not any(r["event_kind"] == "turn_result" for r in rows)


def test_rejected_input_and_session_close_have_records_without_game_event(tmp_path):
    service, s = _service_session(tmp_path, [])
    result = service.push_player_input("")
    assert not result["accepted"]
    assert records(service)[-1]["result"]["reason"] == "empty_text"
    service.close_session(s.session_id)
    last = records(service)[-1]
    assert last["event_kind"] == "lifecycle" and last["result"]["reason"] == "session_closed"
    assert last["state_after"] is None


def test_processing_exception_is_recorded_without_hiding_it(tmp_path, monkeypatch):
    service, s = _service_session(tmp_path, [])

    def fail(*args, **kwargs):
        raise RuntimeError("processing failure")

    monkeypatch.setattr(service, "_drain_playback_events", fail)
    with pytest.raises(RuntimeError, match="processing failure"):
        service.process_event(event("今の句"), s.session_id)
    last = records(service)[-1]
    assert last["outcome"] == "failed" and last["error_kind"] == "RuntimeError"
    assert last["state_before"] == last["state_after"]


@pytest.mark.parametrize("entry", ["event", "admission"])
def test_web_private_input_is_not_recorded_even_when_dispatch_returns_to_game(entry):
    from types import SimpleNamespace
    from dogido_server.haiku.turn_record import record_workshop_admission, record_workshop_event

    saved = []
    workshop = SimpleNamespace(hud_id="workshop", entry_id="entry", open=True,
                               combat_paused=True, close_reason=None,
                               display_line=lambda: "current verse", pending_revision=None)
    foreground = SimpleNamespace(route="web")
    session = SimpleNamespace(session_id="session", haiku_workshop=workshop,
                              foreground_dialogue=foreground, language_runtime=None,
                              last_seen_at=NOW)
    service = SimpleNamespace(sessions={"session": session},
                              memory=SimpleNamespace(save_haiku_workshop_turn=lambda **kw: saved.append(kw)),
                              _ensure_session=lambda *args: session)
    private_text = "https://example.invalid/private この引用のあと冒険へ戻る"
    if entry == "event":
        @record_workshop_event
        def dispatch(self, event, session_id, idempotency_key):
            foreground.route = "casual"
            return SimpleNamespace(response=SimpleNamespace(deduplicated=False, event_id="event"), actions=[])
        dispatch(service, event(private_text), session.session_id)
    else:
        @record_workshop_admission
        def dispatch(self, text, *, source):
            foreground.route = "casual"
            return {"accepted": True}
        dispatch(service, private_text, source="voice")
    assert saved == []
