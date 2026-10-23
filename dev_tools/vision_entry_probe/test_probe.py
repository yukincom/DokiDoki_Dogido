"""予測評価の因果順序・欠損・境界・反例を検証する。合成座標は実ゲームの成績に数えない。"""
import copy
import math
import unittest

from probe import Config, Frame, analyze, forecast, inside


def row(t, x, *, visible=False, yaw=0, eye_x=0, reason=None, epoch="one", age=0):
    return {
        "kind": "sample", "evidence": "synthetic", "session_id": "s", "session_instance": "i",
        "received_mono_ms": t*1000+age,
        "sample": {
            "version": 1, "status": "observed", "age_ms": age,
            "context": {"owner_id": "o", "body_id": "b", "epoch": epoch, "dimension": "minecraft:overworld"},
            "server_tick": round(t*20), "snapshot_revision": round(t*20)+1,
            "self_pose": {"eye_position": {"x": eye_x, "y": 1, "z": 0}, "view_yaw": yaw, "view_pitch": 0},
            "player_pose": {"position": {"x": x, "y": 0, "z": 4}},
            "vision": {"player_visibility": "visible" if visible else "not_visible",
                       "reason": reason or ("in_view" if visible else "outside_fov"),
                       "range_blocks": 16, "horizontal_fov_degrees": 120, "vertical_fov_degrees": 90}
        }
    }


def crossing():
    # 水平境界x=4*tan(60deg)=6.928...をt=1.768秒付近に横切る。
    return [row(i*.5, 14-4*i*.5, visible=i >= 4) for i in range(8)]


class ProbeTest(unittest.TestCase):
    def test_known_crossing_and_generation_wait_are_distinct(self):
        result = analyze(crossing())
        self.assertEqual(result["outcomes"]["hit"], 1)
        p = result["predictions"][0]
        self.assertEqual(p["issued_ms"], 500)
        self.assertAlmostEqual(p["eta_ms"], 1800)
        self.assertEqual(p["entry"]["lower_ms"], 1500)
        self.assertEqual(p["entry"]["upper_ms"], 2000)
        self.assertEqual(p["estimated_remaining_generation_ms"], 100)
        self.assertEqual(result["latency_kind"], "counterfactual_fixed_generation_only_not_playback")

    def test_reversal_does_not_change_prior_forecast(self):
        straight = crossing()
        reverse = straight[:3] + [row(i*.5, 10+4*(i*.5-1)) for i in range(3, 9)]
        a, b = analyze(straight), analyze(reverse)
        self.assertEqual(a["predictions"][0]["eta_ms"], b["predictions"][0]["eta_ms"])
        self.assertEqual(b["outcomes"]["miss"], 1)
        self.assertEqual(len(b["predictions"]), 1)

    def test_static_hidden_and_visible_are_not_entries(self):
        for visible in (True, False):
            result = analyze([row(i*.5, 2 if visible else 12, visible=visible) for i in range(8)])
            self.assertEqual(result["all_entries"], 0)
            self.assertEqual(result["predictions"], [])

    def test_sampling_interval_crossing_deadline_is_uncertain(self):
        rows = [row(0, 14), row(.5, 12), row(1, 10), row(2.9, 10), row(3.4, 6, visible=True)]
        # deadline=3.0秒。実際の進入は2.9〜3.4秒なので的中とも失敗とも断定しない。
        result = analyze(rows)
        self.assertEqual(result["outcomes"]["uncertain"], 1)
        self.assertEqual(result["precision_interval"], [0, 1])

    def test_http_round_trip_widens_entry_bracket(self):
        rows = crossing()
        rows[3]["request_duration_ms"] = 200
        rows[4]["request_duration_ms"] = 200
        result = analyze(rows)
        self.assertEqual(result["entries"][0]["lower_ms"], 1300)
        self.assertEqual(result["entries"][0]["upper_ms"], 2000)

    def test_slow_http_is_included_in_freshness_limit(self):
        old = row(1, 10, age=100)
        old["request_duration_ms"] = 2000
        result = analyze(crossing()[:2]+[old])
        self.assertEqual(result["outcomes"]["censored"], 1)
        self.assertEqual(result["counts"]["breaks"], 1)

    def test_missing_record_and_context_change_are_censored(self):
        for boundary in (
            {"kind": "gap", "evidence": "synthetic", "received_mono_ms": 1000},
            row(1, 10, epoch="two"),
        ):
            result = analyze(crossing()[:2]+[boundary])
            self.assertEqual(result["outcomes"]["censored"], 1)
            self.assertEqual(result["all_entries"], 0)

    def test_end_before_deadline_is_not_a_miss(self):
        result = analyze(crossing()[:3])
        self.assertEqual(result["outcomes"]["censored"], 1)
        self.assertEqual(result["outcomes"]["miss"], 0)

    def test_occlusion_drops_prepared_candidate(self):
        rows = crossing()[:3]+[row(1.5, 8, reason="occluded"), row(2, 6, visible=True)]
        result = analyze(rows)
        self.assertEqual(result["outcomes"]["miss"], 1)
        self.assertFalse(result["entries"][0]["eligible"])
        self.assertIsNone(result["entries"][0]["prediction_id"])

    def test_duplicate_does_not_add_evidence(self):
        duplicate = copy.deepcopy(crossing()[1])
        duplicate["received_mono_ms"] += 100
        duplicate["sample"]["age_ms"] += 100
        result = analyze(crossing()[:2]+[duplicate]+crossing()[2:])
        self.assertEqual(result["counts"]["duplicate_frames"], 1)
        self.assertEqual(result["outcomes"]["hit"], 1)

    def test_stale_observation_breaks_series(self):
        result = analyze(crossing()[:2]+[row(1, 10, age=2000)])
        self.assertEqual(result["outcomes"]["censored"], 1)
        self.assertEqual(result["counts"]["breaks"], 1)

    def test_teleport_speed_not_extrapolated(self):
        result = analyze([row(0, 100), row(.5, 12), row(1, 6, visible=True)])
        self.assertEqual(result["predictions"], [])
        self.assertEqual(result["eligible_entries"], 1)

    def test_heading_wrap_and_south_convention(self):
        config = Config()
        f = Frame.read(row(0, 0), config)
        self.assertTrue(inside((0, 1, 0), (0, 0, 4), 0, 0, f, 1.8))
        self.assertFalse(inside((0, 1, 0), (0, 0, -4), 0, 0, f, 1.8))
        a = Frame.read(row(0, 14, yaw=179), config)
        b = Frame.read(row(.5, 12, yaw=-179), config)
        # 一周を横切る小回転を358度の急回転として扱わない。
        self.assertEqual((b.yaw-a.yaw+180) % 360-180, 2)
        self.assertIsNone(forecast(a, b, config))

    def test_invalid_data_and_mixed_sources_fail(self):
        bad = row(0, math.nan)
        with self.assertRaises(ValueError):
            analyze([bad])
        actual = row(.5, 12)
        actual["evidence"] = "live_snapshot"
        with self.assertRaises(ValueError):
            analyze([row(0, 14), actual])
        with self.assertRaises(ValueError):
            analyze([row(1, 12), row(0, 14)])

    def test_body_motion_is_relative(self):
        # 同じ速度で並進すれば相対位置は変わらず、進入予測は出ない。
        result = analyze([row(i*.5, 14-2*i, eye_x=-2*i) for i in range(5)])
        self.assertEqual(result["predictions"], [])


if __name__ == "__main__":
    unittest.main()
