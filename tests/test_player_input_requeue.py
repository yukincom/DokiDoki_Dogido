"""panic 中の player_input requeue が毎 tick ループしないこと。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dogido_server.config import Settings
from dogido_server.models import (
    AdapterSessionCreateRequest,
    Certainty,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    TimePhase,
    VisualThreat,
    Weather,
    WorldState,
)
from dogido_server.service import DogidoService


def _snapshot(
    now: datetime,
    *,
    seq: int,
    user_text: str | None = None,
    with_pillager: bool = False,
) -> GameEvent:
    threats = []
    if with_pillager:
        threats = [
            VisualThreat(
                type="pillager",
                distance=4.0,
                entity_id="e1",
            )
        ]
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test",
        observed_at=now,
        sequence=seq,
        event=EventDescriptor(
            name=EventName.STATUS_SNAPSHOT,
            source_kind=SourceKind.SYSTEM,
            priority_hint=PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="p",
            position=Position(x=0, y=64, z=0),
            dimension="minecraft:overworld",
        ),
        world=WorldState(
            time_phase=TimePhase.DAY,
            weather=Weather.CLEAR,
            biome="plains",
            local_light=15,
            sky_visible=True,
        ),
        visual_threats=threats,
        meta=MetaState(user_text=user_text),
    )


class PanicPlayerInputHoldTests(unittest.TestCase):
    def test_pending_not_reattached_every_tick_while_panic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=False,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
            created = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="t",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            sess = service.sessions[created.session_id]
            now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

            sid = created.session_id
            # モードを panic に固定（毎 tick attach→requeue 防止の対象）
            sess.machine.state.mode = "panic"
            sess.pending_player_text = "いっぱい人がいるじゃん 怖っ何あれ"

            # panic のまま数フレーム → pending は残り、user_text には載せない
            for i in range(1, 5):
                service.process_event(
                    _snapshot(now + timedelta(seconds=i), seq=i, with_pillager=True),
                    session_id=sid,
                )
                self.assertEqual(
                    sess.pending_player_text,
                    "いっぱい人がいるじゃん 怖っ何あれ",
                    msg=f"seq={i} pending should stay (no attach loop)",
                )

            # 落ち着いたら attach → chat 経路で消化
            sess.machine.state.mode = "normal"
            service.process_event(
                _snapshot(now + timedelta(seconds=20), seq=20, with_pillager=False),
                session_id=sid,
            )
            self.assertIsNone(sess.pending_player_text)


if __name__ == "__main__":
    unittest.main()
