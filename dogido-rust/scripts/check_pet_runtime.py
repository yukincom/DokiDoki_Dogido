#!/usr/bin/env python3
"""Pet identity and sound selection through native HTTP with owned mocks only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time

from check_chat_native import objects
from check_dialogue import dependencies, running, register, request, submit, wait_for, row, snapshot

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/dogido-rust")
    args = parser.parse_args()
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-pets-") as tmp, dependencies() as (dep, control, seen):
        with running(args.binary.resolve(), Path(tmp), dep,
                     haiku_settings={"enabled": False, "memory_enabled": False}) as (base, process, log):
            sid = register(base, preview=False)
            sequence = 0
            identity = {"entity_id": "cat-1", "custom_name": "クロちゃん", "tamed": True}
            cat = {"type": "cat", "distance": 3, "direction": {"horizontal": "right"}, "identity": identity}
            sound = {"type": "cat", "source_id": "cat-1", "identity": identity,
                     "sound_event": "entity.cat.ambient", "direction": {"horizontal": "right"}, "heard_ago_ms": 0}
            fire = {"type": "block:campfire", "source_id": "fire-1", "sound_event": "block.campfire.crackle",
                    "direction": {"horizontal": "left"}, "heard_ago_ms": 0}

            def send(**fields):
                nonlocal sequence
                sequence += 1
                return request(base, "/api/v1/game-events", {
                    "schema_version": "2026-05-24", "adapter": "fixture", "sequence": sequence,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                    "player": {"name": "試験", "dimension": "minecraft:overworld"},
                    "world": {"sky_visible": True, "ceiling_height": 20, "local_light": 15, "time_phase": "day"}, **fields}, sid=sid)

            def calls():
                return [r["body"] for r in seen if r["path"] == "/v1/chat/completions"]

            def plan(incoming):
                if incoming["max_tokens"] != 640:
                    return None
                text = [r for r in objects(incoming) if r["turn_id"] == "current"][-1]["text"]
                action = "answer_observation" if "音" in text or "声" in text else "continue_conversation"
                query = ""
                if "いる？" in text:
                    action, query = "check_entity_presence", "クロちゃん"
                elif "これ何" in text:
                    action, query = "identify_entity", "これ"
                return {"action": action, "focus": text, "entity_query": query,
                        "evidence": [{"turn_id": "current", "quote": text}], "confidence": .95, "repair": None}

            def say(text, answer):
                control["leaf"] = answer
                before = len(calls())
                turn = submit(base, sid, text)
                result = wait_for(lambda: row(base, turn, {"completed", "failed", "unsupported"}))
                assert result["playback_status"] == "completed", (result, log.read_text())
                leaf = [c for c in calls()[before:] if c["max_tokens"] == 72]
                prompt = "\n".join(m["content"] for c in leaf for m in c["messages"])
                return result, prompt, leaf

            control["structured_handler"] = plan
            send()
            send(passive_mobs=[cat])
            time.sleep(.35)
            assert not calls(), calls()
            assert not snapshot(base)["utterances"], snapshot(base)
            passed.append("tamed_mob_silent_without_model_call")

            send(passive_mobs=[cat], ambient_sounds=[sound, fire])
            result, prompt, leaf = say("何の音？", "焚き火の音やな。")
            assert result["text"] == "焚き火の音やな。", result
            assert "焚き火の音" in prompt and "クロちゃんの声" not in prompt, prompt
            passed.append("generic_sound_prioritizes_nonpet")

            result, prompt, leaf = say("クロちゃんの声？", "クロちゃんの声やな。")
            assert result["text"] == "クロちゃんの声やな。", result
            assert "クロちゃんの声 右" in prompt, prompt
            passed.append("explicit_name_selects_same_individual_sound")

            result, prompt, leaf = say("クロちゃんがクリーパーを追い払ってくれたー", "さすが猫やな、頼もしいわ。")
            assert result["text"] == "さすが猫やな、頼もしいわ。", result
            assert "名前と種類" in prompt and '"species":"ネコ"' in prompt, prompt
            assert "自分が目撃したことにはしない" in prompt, prompt
            passed.append("player_report_uses_name_species_mapping")

            send(passive_mobs=[cat], look_target={"kind": "entity", "name": "minecraft:cat", "identity": identity})
            result, prompt, leaf = say("これ何？", "それはクロちゃんやで。")
            assert result["text"] == "それはクロちゃんやで。", result
            passed.append("crosshair_uses_custom_name")

            # A new session has no earlier audio, so another cat cannot borrow this name.
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            sid = register(base, preview=False)
            other = {**sound, "source_id": "cat-2", "identity": {"entity_id": "cat-2", "custom_name": "シロ", "tamed": True}}
            send(passive_mobs=[cat], ambient_sounds=[other])
            result, prompt, leaf = say("クロちゃんの声？", "これは採用されない。")
            assert not leaf and "クロちゃんの声や" not in result["text"], result
            passed.append("same_species_different_id_is_not_the_named_sound")

            send()
            result, prompt, leaf = say("クロちゃんいる？", "これは採用されない。")
            assert not leaf and "クロちゃんは確認できてへん" in result["text"], result
            passed.append("remembered_name_is_not_current_presence")
        assert process.returncode == 0
    report = ROOT / "reports/pet-runtime-check.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps({"passed": passed, "model": "mock", "tts": "mock", "playback": "mock",
                                  "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2) + "\n")
    print(f"PASS {len(passed)} pet HTTP checks; all owned processes stopped")


if __name__ == "__main__":
    main()
