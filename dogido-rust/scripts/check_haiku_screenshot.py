#!/usr/bin/env python3
"""前置き前の撮影待ち→生成→同じ句の実保存→本棚登録を模擬HTTPで検証。実ゲームは操作しない。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

from check_haiku_runtime import fixture
from check_dialogue import request, wait_for
from body_senses_fixture import attach_body


def register(base, capability):
    return request(base, "/api/v1/adapter-sessions", {
        "adapter_name": "fabric-test", "adapter_version": "test", "game": "minecraft-java",
        "schema_version": "2026-05-24", "player_name": "試験",
        "player_uuid": "11111111-1111-4111-8111-111111111111",
        "execution_capabilities": ["client.screenshot.body_view.v1"] if capability else [],
    })["session_id"]


def mock_result(c, screenshots):
    data = b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR" + (2).to_bytes(4, "big") + (3).to_bytes(4, "big")
    image = screenshots / "dogido_2026-10-20_12.00.00.png"
    image.write_bytes(data)
    at = datetime.now(timezone.utc).isoformat()
    result = {"command_id": c["command_id"], "command_type": c["type"], "status": "succeeded",
              "executed_at": at, "captured_at": at, "session_id": c["session_id"], "poem_id": c["poem_id"],
              "directory": str(screenshots), "filename": image.name,
              "sha256": hashlib.sha256(data).hexdigest(), "detail_code": "saved",
              "capture_view": {"viewpoint": "dogido_client_render_eye", "camera_restored": True,
                               **{key: c[key] for key in ("body_entity_uuid", "body_epoch", "owner_uuid", "dimension")}}}
    return result, image, data


def main():
    with tempfile.TemporaryDirectory(prefix="dogido-screenshot-fixture-") as tmp:
        screenshots = Path(tmp).resolve()
        with fixture(screenshot_dir=str(screenshots)) as values:
            base, _, log, _, seen, gate, drafting, _, send, _, rows, stored, folder = values
            gate.clear()
            sid = register(base, True)
            missing = send(sid, body_state="missing")
            assert not any(c["type"] == "capture_screenshot" for c in missing["commands"])
            send(sid)
            def commands():
                return [c for c in send(sid)["commands"] if c["type"] == "capture_screenshot"]
            c = wait_for(commands)[0]
            assert c["poem_id"].startswith(f"session:{sid}:capture:"), c
            context = attach_body({"sequence": 1, "player": {}})["body_observation"]["sample"]["context"]
            assert {key: c[key] for key in ("body_entity_uuid", "body_epoch", "owner_uuid", "dimension")} == {
                "body_entity_uuid": context["body_id"], "body_epoch": context["epoch"],
                "owner_uuid": context["owner_id"], "dimension": context["dimension"]}, c
            assert commands()[0] == c
            assert not rows(sid), "preface output started before the capture result"
            assert not any(row["path"] == "/v1/chat/completions" for row in seen), "haiku model started before capture settled"
            result, image, data = mock_result(c, screenshots)
            first = send(sid, command_results=[result])
            assert c["command_id"] not in first["acknowledged_command_ids"]
            wait_for(drafting.is_set)
            assert not stored(sid), "capture waited for completed poem"
            state_file = folder / "memory/bookshelf/state.json"
            assert not state_file.exists(), "uncommitted generation was registered"
            assert not commands(), "successful early capture was reissued"
            gate.set()
            entry = wait_for(lambda: (send(sid, command_results=[result]), stored(sid))[1])[0]
            poem_id = f"session:{sid}:{entry['id']}"
            wait_for(lambda: c["command_id"] in send(sid, command_results=[result])["acknowledged_command_ids"])
            state = json.loads(state_file.read_text())
            key = "minecraft-" + c["command_id"]
            assert state["placements"]["poems:" + poem_id] == [key, None, None]
            assert state["minecraftPhotos"][key]["sha256"] == result["sha256"]
            assert state["minecraftPhotos"][key]["poemId"] == poem_id
            assert state["minecraftPhotos"][key]["filename"].startswith("dogido_")
            assert state["minecraftPhotos"][key]["receipt"]["capture_view"] == result["capture_view"]
            assert datetime.fromisoformat(result["captured_at"]) < datetime.fromisoformat(entry["created_at"])
            send(sid, command_results=[result])
            assert json.loads(state_file.read_text()) == state
            assert image.read_bytes() == data
            assert all(isinstance(message.get("content"), str) for row in seen
                       if row["path"] == "/v1/chat/completions" for message in row["body"]["messages"]), "record-only photo reached model input"
        with fixture(screenshot_dir=str(screenshots)) as values:
            base, _, _, _, _, gate, drafting, _, send, _, _, stored, folder = values
            gate.clear(); sid = register(base, True); send(sid)
            c = wait_for(lambda: [c for c in send(sid)["commands"] if c["type"] == "capture_screenshot"])[0]
            result, image, data = mock_result(c, screenshots)
            send(sid, command_results=[result]); wait_for(drafting.is_set)
            send(sid, world={"game_paused": True})
            wait_for(lambda: c["command_id"] in send(sid, world={"game_paused": True},
                     command_results=[result])["acknowledged_command_ids"])
            assert not stored(sid) and not (folder / "memory/bookshelf/state.json").exists()
            assert image.read_bytes() == data
            gate.set()
            # 次の撮影には結果を返さず、10秒の開始期限後も句生成が進むことを確認する。
            wait_for(lambda: (send(sid), stored(sid))[1], timeout=20)
            # 次の発句が完成しても、取消した生成の画像は登録されない。
            send(sid, command_results=[result])
            assert not (folder / "memory/bookshelf/state.json").exists()
        with fixture(screenshot_dir=str(screenshots)) as values:
            base, _, log, _, _, gate, drafting, _, send, _, _, stored, folder = values
            gate.clear(); sid = register(base, True); send(sid)
            c = wait_for(lambda: [c for c in send(sid)["commands"] if c["type"] == "capture_screenshot"])[0]
            result, image, data = mock_result(c, screenshots)
            send(sid, command_results=[result]); wait_for(drafting.is_set)
            # 新しい句の保存だけを失敗させる。一時fixture以外には触れない。
            (folder / "memory/sessions" / sid / "long_term/haiku_entries.jsonl").mkdir(parents=True)
            gate.set(); wait_for(lambda: "haiku_save_failed" in log.read_text())
            wait_for(lambda: c["command_id"] in send(sid, command_results=[result])["acknowledged_command_ids"])
            assert not (folder / "memory/bookshelf/state.json").exists()
            assert image.read_bytes() == data
        with fixture() as values:
            base, _, _, _, _, _, _, _, send, _, _, stored, _ = values
            sid = register(base, False); send(sid)
            wait_for(lambda: (send(sid), stored(sid))[1])
            assert not any(c["type"] == "capture_screenshot" for c in send(sid)["commands"])
        with fixture(memory_enabled=False) as values:
            base, _, _, _, _, _, drafting, _, send, _, _, _, _ = values
            sid = register(base, True); send(sid); wait_for(drafting.is_set)
            assert not any(c["type"] == "capture_screenshot" for c in send(sid)["commands"])
    print(json.dumps({"passed": ["capture_before_poem_generation_completes", "same_id_redelivery",
        "registration_waits_for_same_saved_poem", "durable_registration_before_ack", "registered_sha256",
        "duplicate_result_idempotency", "original_unchanged", "cancelled_capture_not_attached_to_next_poem",
        "save_failure_not_registered", "capability_absent_no_capture", "memory_disabled_no_capture",
        "missing_body_no_capture", "materials_body_context", "dogido_filename_and_capture_view_stored",
        "capture_before_preface_model_and_output", "record_only_photo_not_sent_to_model", "capture_timeout_allows_next_poem"]}))


if __name__ == "__main__":
    main()
