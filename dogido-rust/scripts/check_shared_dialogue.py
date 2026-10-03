#!/usr/bin/env python3
"""共通観測・通常会話の沈黙を模擬LLM/TTSと実HTTPで確認する。実モデルなし。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from check_dialogue import dependencies, register, request, row, running, snapshot, submit, wait_for
from check_environment_reactions import reaction_context

ROOT = Path(__file__).resolve().parents[1]


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-shared-dialogue-") as tmp, dependencies() as (dep, control, seen):
        folder = Path(tmp)
        control["choice"] = {"action": "silent", "speech": ""}

        def respond(incoming):
            if reaction_context(incoming) is not None:
                return {"action": "silent", "speech": ""}
            if any('"action":"silent"' in m["content"] for m in incoming["messages"] if m["role"] == "system"):
                return control["choice"]
            return None

        control["structured_handler"] = respond
        with running(ROOT / "target/debug/dogido-rust", folder, dep, extra_args=("--max-tokens", "384")) as (base, _, _):
            def history(sid):
                return next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)

            def calls():
                return [r["body"] for r in seen if r["path"] == "/v1/chat/completions"]

            def audio():
                return [r for r in seen if r["path"].startswith("/audio_query?")]

            sid = register(base)
            first = submit(base, sid, "今日はゆっくりしよう")
            result = wait_for(lambda: row(base, first, {"quiet"}))
            assert result["dialogue_action"] == "silent" and not result["text"], result
            assert [r["role"] for r in history(sid)] == ["user", "event"], history(sid)
            assert history(sid)[1]["reaction"] == "silent"
            assert not audio() and len(calls()) == 2, seen
            passed.append("normal_silence_has_no_tts_or_repair_and_records_reaction")

            control["choice"] = {"action": "speak", "speech": "そやな、ゆっくりしよ。"}
            start = len(calls())
            second = submit(base, sid, "そばにいてね")
            wait_for(lambda: row(base, second, {"completed"}))
            assert all("黙って受け止めた" in json.dumps(c, ensure_ascii=False) for c in calls()[start:]), calls()[start:]
            assert [r["role"] for r in history(sid)] == ["user", "event", "user", "assistant"]
            passed.append("planner_and_reply_see_silence_as_an_event")

            # Empty/nonconforming model output must not be interpreted as assent.
            control["choice"] = {"action": "silent", "speech": "まだ話している"}
            third = submit(base, sid, "そのあとどうしようかな")
            result = wait_for(lambda: row(base, third, {"completed"}))
            assert result["dialogue_action"] == "speak" and result["text"], result
            assert len([r for r in history(sid) if r.get("reaction") == "silent"]) == 1
            passed.append("invalid_silence_is_repaired_or_fallback_not_recorded_as_silence")
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

            sid = register(base, preview=False)
            sequence = 0

            def event(weather, smell):
                nonlocal sequence
                sequence += 1
                return request(base, "/api/v1/game-events", {
                    "schema_version": "2026-05-24", "adapter": "fixture", "sequence": sequence,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                    "player": {"name": "試験", "dimension": "minecraft:overworld"},
                    "world": {"weather": weather, "biome": "plains", "sky_visible": True,
                              "ceiling_height": 20, "local_light": 15, "time_phase": "day"},
                    "smell_observation": smell}, sid=sid)

            bread = {"status": "present", "smell_id": "bread", "category": "food", "valence": "pleasant",
                     "specificity": "source", "source_kind": "hotbar", "effective_strength": 3}
            event("clear", {"status": "none"})
            event("rain", bread)
            event("rain", bread)
            wait_for(lambda: any(r["source"] == "game_observation" and r["playback_status"] == "not_selected"
                                for r in snapshot(base)["utterances"] if r["session_id"] == sid))
            speech = "そやな、一緒に帰ろか。雨も降ってきたし、パンの匂いでお腹もすいたわ。"
            control["choice"] = {"action": "speak", "speech": speech}
            start = len(calls())
            turn = submit(base, sid, "そろそろ帰ろうか")
            result = wait_for(lambda: row(base, turn, {"completed"}))
            assert result["text"] == speech, result
            leaf = next(c for c in calls()[start:] if c["max_tokens"] == 384 and reaction_context(c) is None)
            user = leaf["messages"][1]["content"]
            header = "【現在の環境観測と変化（会話履歴とは別）】\n"
            context, _ = json.JSONDecoder().raw_decode(user.split(header, 1)[1])
            assert context["observations"]["weather"]["precipitation_kind"] == "rain", context
            assert context["observations"]["smell"]["status"] == "present", context
            assert "effective_strength" not in json.dumps(context), context
            env = next(c for c in calls() if reaction_context(c) is not None)
            assert env["messages"][0]["content"] == leaf["messages"][0]["content"]
            assert any(r.get("reaction") == "silent" for r in history(sid)), history(sid)
            passed.append("player_reply_combines_rain_and_smell_using_shared_base_observation_history")

            (folder / "player_mode").write_text("fail")
            control["choice"] = {"action": "speak", "speech": "そばで見守っとるで。"}
            before = len([r for r in history(sid) if r["role"] != "user"])
            turn = submit(base, sid, "ありがとう")
            wait_for(lambda: row(base, turn, {"failed"}))
            assert len([r for r in history(sid) if r["role"] != "user"]) == before, history(sid)
            passed.append("playback_failure_is_neither_spoken_history_nor_chosen_silence")
    print(json.dumps({"passed": passed, "count": len(passed), "live_model": False,
                      "real_minecraft": False, "real_audio": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
