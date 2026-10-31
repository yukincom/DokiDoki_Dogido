#!/usr/bin/env python3
"""身体依頼のBERT→HTTP命令→結果ACKを模擬Minecraftで確認する。

実body・Minecraft・モデルは使わない。lookは向き変更であり、座標移動ではない。
現在のdebug本体、既存のHTTP/TTS/player fixture、固定ラベルの分類workerを使う。
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from body_senses_fixture import OWNER_ID, attach_body
from check_dialogue import dependencies, request, running, snapshot, wait_for
from request_classifier_fixture import mock_request_classifier, resolve_input_response

ROOT = Path(__file__).resolve().parents[2]
DIMENSION = "minecraft:overworld"
RECALL = "ドギド、こっちへ来て！"
RIGHT = "右を向いて"
TARGET = "これを見て"
MISSING_TARGET = "向こうを見て"
STALE_TARGET = "今いる相手を見て"
NEW_RIGHT = "もう一度、右を向いて"
LABELS = {
    RECALL: "dogido_recall",
    RIGHT: "look_relative_right",
    TARGET: "look_target",
    MISSING_TARGET: "look_target",
    STALE_TARGET: "look_target",
    NEW_RIGHT: "look_relative_right",
}
SUCCESS_TEXT = {"recall": "来たで、どうしたん？", "look": "そっち向いたで。"}


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-body-request-") as tmp, \
            dependencies() as (dep, controls, seen), \
            mock_request_classifier(LABELS) as (classifier_args, classifier_dir):
        controls["leaf"] = SUCCESS_TEXT["recall"]
        folder = Path(tmp)
        with running(ROOT / "target/debug/dogido-rust", folder, dep,
                     haiku_settings={"enabled": False, "memory_enabled": False},
                     extra_args=classifier_args) as (base, _, _):
            # 観測capabilitiesと実行capabilitiesは別。通常のMinecraft接続で試す。
            sid = request(base, "/api/v1/adapter-sessions", {
                "adapter_name": "fabric-body-request-fixture", "adapter_version": "test",
                "game": "minecraft-java", "schema_version": "2026-05-24",
                "player_name": "試験", "player_uuid": OWNER_ID,
                "capabilities": ["dialogue_text.v1"], "voice_enabled": True,
                "execution_capabilities": ["body.request.v1"],
            })["session_id"]
            sequence = 0
            requests_sent = []

            def event(*, bound=True, results=()):
                nonlocal sequence
                sequence += 1
                value = {
                    "schema_version": "2026-05-24", "adapter": "fixture",
                    "sequence": sequence, "observed_at": now(),
                    "event": {"name": "status_snapshot", "source_kind": "system",
                              "priority_hint": "background", "certainty": "high"},
                    "player": {"name": "試験", "dimension": DIMENSION, "health": 20},
                    "world": {"time_phase": "day", "biome": "plains", "sky_visible": True,
                              "local_light": 15, "danger_darkness_score": 0, "game_paused": False},
                    "combat": {"combat_active_hint": False, "hostiles_within_scan_ground": 0},
                    "visual_threats": [], "auditory_threats": [],
                    "command_results": list(results),
                }
                if bound:
                    return attach_body(value)
                value["body_observation"] = {
                    "version": 3, "status": "unavailable", "reason": "body_absent"}
                return value

            def post(value):
                result = request(base, "/api/v1/game-events", value, sid=sid)
                assert result["accepted"], result
                return result

            def send(**kwargs):
                return post(event(**kwargs))

            def rows():
                return [r for r in snapshot(base)["utterances"] if r["session_id"] == sid]

            def body_row(turn):
                return next((r for r in rows() if r["turn_id"] == turn), None)

            def feedback(text):
                return [r for r in rows() if r.get("player_input_text") == text
                        and r.get("text") and r.get("category") != "body_request"]

            def completed_feedback(text, expected):
                found = [r for r in feedback(text) if r["playback_status"] == "completed"]
                if not found:
                    return None
                assert len(found) == 1 and found[0]["text"] == expected, found
                if text == RECALL:
                    assert [report["kind"] for report in found[0].get("llm_reports", [])] == ["body_recall"], found[0]
                else:
                    assert not found[0].get("llm_reports"), found[0]
                return found[0]

            def body_commands(response):
                return [c for c in response["commands"] if c.get("type") == "body_request"]

            def classified_command(response, text, mode, *, bound=True):
                assert response["accepted"] and response["reason"] == "request_classification", response
                routed = resolve_input_response(base, response)
                assert routed["reason"] == "body_request" and routed["body_request_status"] == "queued", routed
                item = wait_for(lambda: body_row(routed["turn_id"]))
                assert item["category"] == "body_request" and item["playback_status"] == "quiet", item
                assert item["player_input_text"] == text and item["source"] == "text", item
                classified = item["request_classification"]
                assert classified["text"] == text and classified["source"] == "text", classified
                assert classified["prediction"]["label"] == LABELS[text], classified
                response = send(bound=bound)
                commands = body_commands(response)
                assert len(commands) == 1, response
                command = commands[0]
                assert command["command_id"] == routed["command_id"] == item["body_request_command_id"], command
                assert command["mode"] == mode == item["body_request_mode"], (command, item)
                assert command["input_generation"] == routed["body_request_generation"] \
                    == item["input_generation"] == response["body_request_generation"], (command, routed, item, response)
                assert command["owner_id"] == OWNER_ID and command["dimension"] == DIMENSION, command
                assert set(command) == {"type", "command_id", "issued_at", "expires_at",
                                        "input_generation", "owner_id", "dimension", "mode", "context"}, command
                issued = datetime.fromisoformat(command["issued_at"].replace("Z", "+00:00"))
                expires = datetime.fromisoformat(command["expires_at"].replace("Z", "+00:00"))
                assert (expires - issued).total_seconds() == 5, command
                assert (command["context"] is not None) == bound, command
                if bound:
                    assert command["context"] == context, command
                if mode == "recall":
                    # 受付時の呼びかけ返答は許可済み。到着はACK前に確定せず、返事だけを一度生成する。
                    wait_for(lambda: completed_feedback(text, SUCCESS_TEXT["recall"]))
                else:
                    assert not feedback(text), feedback(text)
                assert item["body_request_status"] == "queued" and "body_request_result" not in item, item
                # 同じ依頼の再配送は同じID。Rustはlookの座標や移動を作らない。
                assert body_commands(send(bound=bound)) == [command]
                requests_sent.append(text)
                return routed, command

            def issue(text, mode, *, bound=True):
                send(bound=bound)
                response = request(base, "/api/v1/player-input", {
                    "session_id": sid, "source": "text", "text": text})
                return classified_command(response, text, mode, bound=bound)

            def receipt(command, *, status="succeeded", detail=None):
                body = command["context"] or context
                return {
                    "command_id": command["command_id"], "command_type": "body_request",
                    "status": status, "executed_at": now(),
                    "detail_code": detail or ("arrived" if command["mode"] == "recall" else "facing_target"),
                    "input_generation": command["input_generation"],
                    "owner_id": command["owner_id"], "dimension": command["dimension"],
                    "body_id": body["body_id"], "epoch": body["epoch"],
                }

            def accept_success(routed, command, text, *, duplicate=False):
                result = receipt(command)
                frame = event(results=[result])
                response = post(frame)
                assert command["command_id"] in response["acknowledged_command_ids"], response
                assert not body_commands(response), response
                item = body_row(routed["turn_id"])
                assert item["body_request_status"] == "succeeded", item
                expected = {"command_id": command["command_id"], "mode": command["mode"],
                            "input_generation": command["input_generation"],
                            "status": "succeeded", "detail_code": result["detail_code"]}
                assert item["body_request_result"] == expected, item
                spoken = SUCCESS_TEXT["recall" if command["mode"] == "recall" else "look"]
                wait_for(lambda: completed_feedback(text, spoken))
                if duplicate:
                    # 同一sequenceと、新しいsequenceでの結果再送をそれぞれ確認する。
                    again = post(deepcopy(frame))
                    assert again["deduplicated"] and command["command_id"] in again["acknowledged_command_ids"], again
                    newer = send(results=[result])
                    assert not newer["deduplicated"] and command["command_id"] in newer["acknowledged_command_ids"], newer
                    assert not body_commands(again) and not body_commands(newer)
                    assert body_row(routed["turn_id"])["body_request_result"] == expected
                    assert len(feedback(text)) == 1, feedback(text)

            # 身体UUIDは既存の観測fixtureから取得する。登録はownerだけで開始する。
            context = attach_body({"sequence": 0, "player": {"dimension": DIMENSION}})["body_observation"]["sample"]["context"]
            routed, command = issue(RECALL, "recall", bound=False)
            assert command["context"] is None
            accept_success(routed, command, RECALL, duplicate=True)
            passed.append("unbound_recall_replies_before_arrival_and_duplicate_ack_does_not_repeat")

            routed, command = issue(RIGHT, "look_relative_right")
            accept_success(routed, command, RIGHT)
            passed.append("relative_right_is_orientation_only_with_bound_context")

            routed, command = issue(TARGET, "look_target")
            # 共有ACKは受信確認。入力世代・所有者・身体が違う結果を成功へ昇格させない。
            for field, wrong in [
                ("input_generation", command["input_generation"] + 1),
                ("owner_id", "99999999-9999-4999-8999-999999999999"),
                ("body_id", "88888888-8888-4888-8888-888888888888"),
            ]:
                bad = receipt(command)
                bad[field] = wrong
                response = send(results=[bad])
                assert command["command_id"] in response["acknowledged_command_ids"], response
                assert body_commands(response) == [command], response
                assert body_row(routed["turn_id"])["body_request_status"] == "queued"
                assert not feedback(TARGET), feedback(TARGET)
            accept_success(routed, command, TARGET, duplicate=True)
            passed.append("look_target_requires_matching_generation_owner_body_then_success_ack")

            routed, command = issue(MISSING_TARGET, "look_target")
            rejected = receipt(command, status="rejected", detail="target_unavailable")
            response = send(results=[rejected])
            assert command["command_id"] in response["acknowledged_command_ids"] and not body_commands(response), response
            assert body_row(routed["turn_id"])["body_request_status"] == "rejected"
            wait_for(lambda: completed_feedback(MISSING_TARGET, "今はそっちを向けへんかったわ。"))
            assert not any(r["text"] in SUCCESS_TEXT.values() for r in feedback(MISSING_TARGET))
            passed.append("rejected_target_ack_never_claims_success")

            old_routed, old_command = issue(STALE_TARGET, "look_target")
            # 専用入力で世代を進めてから、古い操作の成功結果を受け取る。
            new_input = request(base, "/api/v1/player-input", {"session_id": sid, "text": NEW_RIGHT, "source": "text"})
            # 分類完了を待って新しい命令を取得し、古いACKと同じ応答でも配送を続ける。
            routed, command = classified_command(new_input, NEW_RIGHT, "look_relative_right")
            response = send(results=[receipt(old_command)])
            assert old_command["command_id"] in response["acknowledged_command_ids"], response
            assert body_commands(response) == [command], response
            assert response["body_request_generation"] > old_command["input_generation"], response
            old = body_row(old_routed["turn_id"])
            assert old["body_request_status"] == "cancelled", old
            assert old["body_request_result"]["input_generation"] == old_command["input_generation"], old
            assert old["body_request_result"]["detail_code"] == "input_or_session_changed", old
            assert not feedback(STALE_TARGET), feedback(STALE_TARGET)
            assert command["input_generation"] == response["body_request_generation"], (command, response)
            assert command["command_id"] != old_command["command_id"]
            again = send(results=[receipt(old_command)])
            assert body_commands(again) == [command] and not feedback(STALE_TARGET), again
            assert body_row(old_routed["turn_id"])["body_request_status"] == "cancelled"
            accept_success(routed, command, NEW_RIGHT)
            passed.append("new_input_cancels_old_ack_and_keeps_new_raw_generation")

            classified_requests = [json.loads(line) for line in
                                   (classifier_dir / "requests.jsonl").read_text().splitlines()]
            classified_texts = [frame["text"].split("現在のユーザー発話:\n", 1)[1]
                                for frame in classified_requests]
            assert classified_texts == requests_sent, (classified_texts, requests_sent)
            generated = [r for r in seen if r["path"] == "/v1/chat/completions"]
            assert len(generated) == 1, generated
            recall_context = json.loads(generated[0]["body"]["messages"][1]["content"])
            assert recall_context["event"] == "body_recall" and recall_context["player_input"] == RECALL, recall_context
            passed.append("each_raw_input_classified_once_and_only_recall_reply_is_generated")
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
    print(json.dumps({"phase": "body_requests_http", "passed": passed, "count": len(passed),
                      "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
