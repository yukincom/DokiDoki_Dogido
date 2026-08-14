from __future__ import annotations

import unittest

from pydantic import ValidationError

from dogido_server.models import PlayerState, VehicleState
from dogido_server.player_activity import player_vehicle_fact, vehicle_label


class PlayerVehicleActivityTests(unittest.TestCase):
    def test_unmounted_player_omits_vehicle_and_has_no_llm_fact(self) -> None:
        player = PlayerState(name="player")

        self.assertNotIn("vehicle", player.model_dump(exclude_none=True))
        self.assertEqual(player_vehicle_fact(player.vehicle), "")

    def test_horse_running_fact_keeps_player_as_subject(self) -> None:
        fact = player_vehicle_fact(
            VehicleState(
                vehicle_id="minecraft:horse",
                activity="running",
                controlling=True,
            )
        )

        self.assertEqual(fact, "プレイヤーはウマに乗って走っている")

    def test_boat_and_minecart_use_japanese_catalog_or_fallback_labels(self) -> None:
        self.assertEqual(vehicle_label("minecraft:oak_boat"), "オークのボート")
        self.assertEqual(
            player_vehicle_fact(
                VehicleState(vehicle_id="minecraft:oak_boat", activity="rowing")
            ),
            "プレイヤーはオークのボートに乗って漕いでいる",
        )
        self.assertEqual(vehicle_label("minecraft:minecart"), "トロッコ")
        self.assertEqual(vehicle_label("example:unknown_mount"), "乗り物")

    def test_unknown_activity_is_rejected_before_it_reaches_prompt(self) -> None:
        with self.assertRaises(ValidationError):
            VehicleState.model_validate(
                {"vehicle_id": "minecraft:horse", "activity": "flying"}
            )


if __name__ == "__main__":
    unittest.main()
