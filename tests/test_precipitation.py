"""川柳・雑談で共有する降水／積雪のコード判定。"""

from __future__ import annotations

import unittest

from dogido_server.state_machine.precipitation import resolve_precipitation_context


class PrecipitationContextTests(unittest.TestCase):
    def resolve(
        self,
        *,
        current_y: float = 64,
        weather: str = "clear",
        temperature: float = 0.25,
        snow_start_y: int | None = 153,
        group: str = "cold",
        downfall: float = 0.8,
        blocks: tuple[str, ...] = (),
        dimension: str = "minecraft:overworld",
    ):
        return resolve_precipitation_context(
            current_y=current_y,
            biome_temperature=temperature,
            snow_start_y=snow_start_y,
            biome_group_id=group,
            biome_downfall=downfall,
            weather=weather,
            dimension=dimension,
            nearby_block_names=blocks,
        )

    def test_clear_taiga_below_snow_height_has_no_snow_evidence(self) -> None:
        context = self.resolve()

        self.assertFalse(context.snowfall_zone)
        self.assertEqual(context.precipitation_kind, "none")
        self.assertEqual(context.snow_evidence, "none")
        self.assertFalse(context.snow_can_be_scene_material)
        self.assertIn("現在は降水なし", context.prompt_line())
        self.assertIn("降雪環境なし", context.prompt_line())
        self.assertIn("雪や積雪を現在場面の材料にしない", context.prompt_line())
        self.assertNotIn("Y64", context.prompt_line())
        self.assertNotIn("0.25", context.prompt_line())
        self.assertNotIn("Y153", context.prompt_line())
        self.assertEqual(
            context.to_prompt_details(),
            {
                "precipitation_kind": "none",
                "precipitation_possible": True,
                "thunder_active": False,
                "snowfall_environment": "no",
                "surface_snow_observed": False,
                "weather_context": context.prompt_line(),
            },
        )

    def test_rain_world_state_becomes_local_snow_above_threshold(self) -> None:
        context = self.resolve(current_y=160, weather="rain")

        self.assertTrue(context.snowfall_zone)
        self.assertEqual(context.precipitation_kind, "snow")
        self.assertEqual(context.snow_evidence, "active_snowfall")
        self.assertTrue(context.snow_can_be_scene_material)
        self.assertIn("地表の積雪は未観測", context.prompt_line())

    def test_below_threshold_precipitation_stays_rain(self) -> None:
        context = self.resolve(weather="thunder")

        self.assertEqual(context.precipitation_kind, "rain")
        self.assertEqual(context.snow_evidence, "none")
        self.assertTrue(context.thunder_active)
        self.assertIn("雷鳴あり", context.prompt_line())

    def test_observed_surface_snow_is_evidence_even_when_clear(self) -> None:
        context = self.resolve(blocks=("minecraft:snow",))

        self.assertEqual(context.snow_evidence, "observed_surface")
        self.assertTrue(context.surface_snow_observed)
        self.assertTrue(context.snow_can_be_scene_material)

    def test_snowy_biome_uses_temperature_when_no_height_rule(self) -> None:
        context = self.resolve(
            temperature=-0.5,
            snow_start_y=None,
            weather="rain",
            group="snowy",
            downfall=0.4,
        )

        self.assertTrue(context.snowfall_zone)
        self.assertEqual(context.precipitation_kind, "snow")

    def test_dry_biome_does_not_turn_bad_weather_into_rain_or_snow(self) -> None:
        context = self.resolve(
            weather="thunder",
            temperature=2.0,
            snow_start_y=None,
            group="dry",
            downfall=0.0,
        )

        self.assertFalse(context.snowfall_zone)
        self.assertEqual(context.precipitation_kind, "none")
        self.assertFalse(context.precipitation_possible)
        self.assertIn("降水環境なし", context.prompt_line())


if __name__ == "__main__":
    unittest.main()
