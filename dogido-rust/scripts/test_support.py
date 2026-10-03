"""Synthetic inputs and wire assertions for current Rust integration checks.

This module uses only the standard library. It does not load a Python runtime,
compute an oracle result, start processes, or initialize a dictionary/model.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINES = ["くさちのひ", "くろきつるぎの", "かげのさむさ"]
ALTERNATIVES = ["あさのひかり", "くろいつるぎの", "かげはさむし"]


def event(ms, *, distance=8.2, direction="left", kind="zombie", entity="z1",
          fuse=None, approaching=False, biome="plains", damage=None):
    """One synthetic observation; no gameplay judgment or expected output."""
    return {
        "schema_version": "2026-05-24", "adapter": "fixture",
        "observed_at": (datetime(2026, 9, 24, tzinfo=timezone.utc)
                        + timedelta(milliseconds=ms)).isoformat(),
        "event": {"name": "status_snapshot", "source_kind": "system",
                  "priority_hint": "background", "certainty": "high"},
        "player": {"name": "試験"},
        "world": {"time_phase": "night", "biome": biome,
                  "sky_visible": True, "danger_darkness_score": 0.0},
        "combat": {"recent_damage_ms": damage},
        "visual_threats": [{"type": kind, "entity_id": entity, "distance": distance,
                            "direction": {"horizontal": direction, "vertical": "same"},
                            "approaching": approaching, "fuse_active": fuse}],
    }


def atom(index, **kwargs):
    return dict(atom_id=f"observation:haiku:source:{index}",
                text=("草地の日", "黒い剣", "冷たい影")[index % 3],
                source_ref=f"observation:{index}", field_path="observed_label",
                observation_role="test", kind="observation", claim_class="factual",
                claim_scopes=["observed_state"], basis_atom_ids=[], **kwargs)


def base():
    """Synthetic poem request shared by bridge tests; assertions stay in tests."""
    return {"details": {}, "source_atoms": [atom(i) for i in range(3)],
            "fallback_text": "まとまらんかった。。。", "max_tokens": 192,
            "grounding_max_tokens": 512, "generation_strategy": "three_slot",
            "max_regeneration_rounds": 6, "llm_enabled": True}


def read_jsonl(path, *, missing_ok=False):
    """Read persisted wire rows without applying memory or adoption policy."""
    path = Path(path)
    if missing_ok and not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        assert isinstance(row, dict), (str(path), number, row)
        rows.append(row)
    return rows


def assert_timestamp(value):
    assert isinstance(value, str) and value, value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, value


def assert_session_created(body):
    assert isinstance(body, dict), body
    assert isinstance(body.get("session_id"), str) and body["session_id"], body
    assert body["accepted_schema_version"] == "2026-05-24", body
    assert body["event_endpoint"] == "/api/v1/game-events", body
    assert body["batch_endpoint"] == "/api/v1/game-events/batch", body
    assert type(body["heartbeat_interval_ms"]) is int and body["heartbeat_interval_ms"] == 5000, body
    assert type(body["max_batch_size"]) is int and body["max_batch_size"] == 25, body
    assert_timestamp(body["server_time"])
    return body["session_id"]


def assert_health(body):
    assert isinstance(body, dict) and body.get("ok") is True, body
    assert body["service"] == "dogido-server", body
    assert isinstance(body["version"], str) and body["version"], body
    assert body["runtime"] == "rust" and body["phase"] == "connection_only", body
    assert body["dialogue_ready"] is False and body["llm_enabled"] is False, body


def assert_session_ack(body, session_id, *, timestamp=False):
    assert isinstance(body, dict) and body.get("ok") is True, body
    assert body["session_id"] == session_id, body
    if timestamp:
        assert_timestamp(body["server_time"])


def assert_closed_hud(body, session_id, sequence):
    expected = {
        "schema_version": 1, "session_id": session_id, "observed_sequence": sequence,
        "workshop_id": None, "state": "closed", "character_state": "normal",
        "canonical_lines": [], "pending_lines": [], "editing": False,
        "selected_line": None, "provisional_resume": False,
    }
    assert isinstance(body, dict), body
    assert {k: v for k, v in body.items() if k != "revision"} == expected, body
    assert type(body["revision"]) is int and body["revision"] >= 0, body


def assert_voice_diagnostic(value):
    """Check the current eight-field Rust diagnostic wire contract."""
    assert isinstance(value, dict), value
    assert set(value) == {"schema_version", "event", "level", "recognized_text",
                          "reason", "detail", "prompt_mode", "duration_ms"}, value
    assert type(value["schema_version"]) is int and value["schema_version"] == 1, value
    assert value["event"] in {
        "capture", "context", "vad_rejected", "vad_error", "stt_started", "stt_finished",
        "stt_queue", "stt_result", "stt_rejected", "stt_error", "wake_word_rejected", "delivery",
    }, value
    assert value["level"] in {"info", "warning", "error"}, value
    for name, limit in (("recognized_text", 500), ("reason", 120), ("detail", 800)):
        field = value[name]
        assert field is None or isinstance(field, str) and len(field) <= limit, (name, field)
    assert value["prompt_mode"] in {None, "normal", "haiku_workshop"}, value
    duration = value["duration_ms"]
    assert duration is None or type(duration) is int and 0 <= duration <= 60000, value


def completion_response(content, *, finish="stop"):
    return {
        "id": "transport-fixture", "model": "synthetic-model", "object": "chat.completion",
        "created": 0,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": 1234, "completion_tokens": 19, "total_tokens": 1253},
    }
