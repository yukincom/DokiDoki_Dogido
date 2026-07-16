"""F′: structure × biome plausibility（SM 1 行）。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dogido_server.config import Settings
from dogido_server.entry_catalog import (
    build_plausibility_hint_lines,
    find_catalog_topics,
    structure_ids_for_plausibility,
    structure_related_mobs,
    structures_for_mob,
)
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.models import GameEvent
from dogido_server.state_machine import DogidoStateMachine


class StructureLinkDataTests(unittest.TestCase):
    def test_related_mobs_on_priority_structures(self) -> None:
        self.assertIn("pillager", structure_related_mobs("pillager_outpost"))
        self.assertIn("elder_guardian", structure_related_mobs("monument"))
        self.assertIn("witch", structure_related_mobs("Witch_hut"))
        self.assertIn("pillager_outpost", structures_for_mob("pillager"))


class PlausibilityLinesTests(unittest.TestCase):
    def test_outpost_possible_in_taiga(self) -> None:
        lines = build_plausibility_hint_lines(
            structure_ids=["pillager_outpost"],
            current_biome_id="taiga",
            current_biome_label="タイガ",
        )
        joined = "\n".join(lines)
        self.assertIn("ありうる", joined)
        self.assertIn("襲撃", joined)

    def test_monument_unlikely_on_plains(self) -> None:
        lines = build_plausibility_hint_lines(
            structure_ids=["monument"],
            current_biome_id="plains",
            current_biome_label="平原",
        )
        self.assertTrue(any("生成されにくい" in line for line in lines))

    def test_zenzen_base_phrase_expands_to_outpost(self) -> None:
        hits = find_catalog_topics("前哨基地ある？")
        ids = structure_ids_for_plausibility(hits)
        self.assertIn("pillager_outpost", ids)

    def test_pillager_visual_only_does_not_force_outpost(self) -> None:
        # 種名だけ・前哨語なし → structure を無理に足さない
        hits = find_catalog_topics("ピリジャー")
        # ラベル一致で pillager はヒットしうるが、前哨語が無ければ structure 展開しない
        ids = structure_ids_for_plausibility(hits)
        self.assertNotIn("pillager_outpost", ids)


class PlayerChatPlausibilityIntegrationTests(unittest.TestCase):
    def test_chat_details_include_plausibility_for_outpost_in_taiga(self) -> None:
        machine = DogidoStateMachine(
            Settings(llm_enabled=False, decision_policy="py_trees")
        )
        event = GameEvent.model_validate(
            {
                "schema_version": "2026-05-24",
                "adapter": "unit-test",
                "observed_at": datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc).isoformat(),
                "sequence": 1,
                "event": {
                    "name": "status_snapshot",
                    "source_kind": "system",
                    "priority_hint": "background",
                    "certainty": "high",
                },
                "player": {
                    "name": "tester",
                    "position": {"x": 0, "y": 64, "z": 0},
                    "dimension": "minecraft:overworld",
                },
                "world": {
                    "time_phase": "day",
                    "weather": "clear",
                    "biome": "taiga",
                    "local_light": 15,
                    "sky_visible": True,
                },
                "meta": {"user_text": "前哨基地ある？"},
            }
        )
        machine.player_input = __import__(
            "dogido_server.player_input", fromlist=["route_player_input"]
        ).route_player_input("前哨基地ある？")
        # _render は内部で details を組む。骨子 or fallback が返る
        text = machine._render_player_chat_reply(event)
        self.assertTrue(text)

        hits = find_catalog_topics("前哨基地ある？")
        lines = build_plausibility_hint_lines(
            topic_hits=hits,
            current_biome_id="taiga",
            current_biome_label="タイガ",
        )
        hints = "\n".join(f"- {line}" for line in lines)
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="f",
                details={
                    "user_text": "前哨基地ある？",
                    "mode": "normal",
                    "time_phase": "day",
                    "threat_summary": "とくになし",
                    "plausibility_hints": hints,
                    "reply_stance": "hypothesis",
                    "reply_policy": "候補を弱く",
                    "place_context": "タイガ",
                },
            )
        )
        content = messages[1]["content"]
        self.assertIn("知識リンク", content)
        self.assertIn("ありうる", content)
        self.assertIn("生成しうる", content)


if __name__ == "__main__":
    unittest.main()
