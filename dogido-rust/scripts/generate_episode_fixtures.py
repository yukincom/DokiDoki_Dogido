#!/usr/bin/env python3
"""Read canonical Python records without models, audio, servers or persistent I/O."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.models import GameEvent, OutputFlags, AdapterCommandResult
from dogido_server.state_machine.types import AudioAction
from dogido_server.episode_log import EpisodeRecorder

base = {"schema_version": "2026-05-24", "adapter": "fixture", "observed_at": "2026-09-29T03:00:00+09:00", "sequence": 5,
        "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"}}
inputs = [
    {},
    {"meta": {"user_text": " こんにちは "}, "player": {"position": {"x": 1, "y": 64, "z": 2}}, "world": {"weather": "clear", "time_phase": "morning", "biome": "plains"}},
    {"visual_threats": [{"type": "zombie", "distance": 7, "entity_id": "z1"}, {"type": "creeper", "distance": 2, "entity_id": "c1"}, {"type": "spider", "distance": 2, "entity_id": "s1"}], "smell_observation": {"status": "none"}},
    {"auditory_threats": [{"label": "zombie", "distance_band": "close", "source_id": "z1"}], "passive_mobs": [{"type": "cat", "distance": 2}], "look_target": {"kind": "entity", "name": "cat", "distance": 2}},
]
rows = []
for extra in inputs:
    event = GameEvent.model_validate(base | extra)
    for kind in ("none", "actions", "receipts"):
        actions = [AudioAction(layer="panic_cue", text="ひいっ！", cue_id="spot_hostile_gasp", interrupt=True, protect_ms=1200)] if kind == "actions" else []
        receipts = [AdapterCommandResult(command_id="cmd_x", command_type="select_hotbar", status="failed", executed_at=datetime(2026, 9, 29, tzinfo=timezone.utc), detail_code="result_mismatch")] if kind == "receipts" else []
        recorder = EpisodeRecorder(Path("/unused"))
        captured = []
        recorder._append = lambda payload: captured.append(payload) or True
        recorder.record(event=event, event_id="evt_fixture", session_id="ses_fixture",
            state_before={"mode": "normal", "pending_haiku_after_preface": False, "player_input_queued": False, "workshop": "none"},
            mode_after="panic" if actions else "normal", combat_active=bool(actions), actions=actions,
            output_flags=OutputFlags(panic_cue_enqueued=bool(actions)), haiku_emitted=False, interpreted_user_text=None,
            recorded_at=datetime(2026, 9, 29, tzinfo=timezone.utc), adapter_commands=[], command_results=receipts)
        expected = captured[0]
        expected.pop("episode_id")
        rows.append({"event": event.model_dump(mode="json"), "expected": expected})
(ROOT / "dogido-rust/fixtures/episode-records.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
print(f"episode parity fixtures: {len(rows)}")
