#!/usr/bin/env python3
"""既存の模擬発句で採取フラグ・応答ID・保存・取消を検査する。実モデルは呼ばない。"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import uuid

from check_dialogue import register, wait_for
from check_haiku_runtime import fixture

KINDS = {.15: "haiku_irony", .2: "haiku_scene", .6: "haiku_draft", .3: "haiku_line_regeneration"}


@contextmanager
def captured(*, modes=None, **settings):
    with tempfile.TemporaryDirectory(prefix="dogido-capture-artifacts-") as tmp:
        root = Path(tmp) / "raw"
        root.mkdir()
        with fixture(internal_capture_enabled=True, capture_dir=str(root), **settings) as environment:
            control = environment[3]
            original = control["structured_handler"]
            outputs = {}
            calls = []

            def structured(incoming):
                value = original(incoming)
                outputs[id(incoming)] = value
                return value

            def response(incoming):
                content = json.dumps(outputs.pop(id(incoming)), ensure_ascii=False)
                response_id = "capture_" + uuid.uuid4().hex
                kind = KINDS.get(incoming["temperature"])
                mode = (modes or {}).get(kind, "complete")
                calls.append({"id": response_id, "kind": kind, "request": incoming})
                if incoming.get("dogido_capture") is True and mode != "missing":
                    folder = root / response_id
                    folder.mkdir()
                    raw = struct.pack("<8f", *range(8))
                    (folder / "hidden.f32").write_bytes(raw)
                    manifest = {
                        "schema": "dogido.internal_capture.v1", "response_id": response_id,
                        "choice_index": 0, "backend": "mlx-lm" if len(calls) % 2 else "llama.cpp",
                        "status": "complete" if mode == "invalid" else mode,
                        "boundary": "decoder_layer_output_pre_final_norm", "dtype": "float32-le",
                        "shape": [2, 2, 2], "hidden_file": "hidden.f32", "hidden_bytes": len(raw),
                        "hidden_sha256": "wrong" if mode == "invalid" else hashlib.sha256(raw).hexdigest(),
                        "prompt_token_ids": [10, 11, 12], "predicted_token_ids": [20, 1],
                        "positions": [{"generation_index": 0, "input_position": 2, "predicted_token_id": 20},
                                      {"generation_index": 1, "input_position": 3, "predicted_token_id": 1}],
                        "model": {"name": "mock-model"}, "identity": {"model": {"sha256": "fixture"}},
                        "total_generated_tokens": 4 if mode == "partial" else 2,
                        # native chat parserの前後で本文が異なっても、同じ応答IDを結合根拠にする。
                        "rendered_text": "<channel|>" + content, "finish_reason": "stop",
                    }
                    (folder / "manifest.json").write_text(json.dumps(manifest))
                return {"id": response_id, "object": "chat.completion", "created": 0, "model": "mock-model",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}

            control["structured_handler"] = structured
            control["completion_override"] = response
            yield environment, root, calls


def main():
    passed = []
    with fixture() as (base, _, _, _, seen, _, _, _, send, _, _, stored, _):
        sid = register(base, preview=False, body=True)
        send(sid)
        entry = wait_for(lambda: stored(sid))[0]
        assert "internal_capture" not in entry
        assert all("dogido_capture" not in call["body"] for call in seen if call["path"] == "/v1/chat/completions")
    passed.append("default_off_keeps_the_existing_requests_and_records")

    with captured(initial_lines=["あ", "い", "う"]) as (env, root, calls):
        base, _, _, _, _, _, _, _, send, _, _, stored, _ = env
        sid = register(base, preview=False, body=True)
        send(sid)
        entry = wait_for(lambda: stored(sid))[0]
        capture = entry["internal_capture"]
        assert capture["session_id"] == sid
        assert capture["token_scope"] == "raw_generation_before_domain_parse_and_reading_normalization"
        assert "internal_capture" not in entry["materials_snapshot"]
        assert [call["kind"] for call in calls] == list(KINDS.values())
        assert len(capture["requests"]) == len(calls) == 4
        for index, (row, call) in enumerate(zip(capture["requests"], calls), 1):
            assert call["request"]["dogido_capture"] is True
            assert row["request_index"] == index and row["kind"] == call["kind"]
            assert row["request_id"] == capture["job_id"] + f":request:{index}"
            assert row["response_id"] == call["id"]
            assert row["status"] == "complete" and row["artifact"]["hidden_verified"]
            assert row["artifact"]["api_text_matches_rendered"] is False
            assert (root / row["artifact"]["manifest"]).is_file()
            assert row["api_output"]["text"]
    passed.append("all_auto_haiku_stages_keep_actual_request_order_and_response_artifacts_in_existing_save")

    with captured(modes={"haiku_irony": "missing", "haiku_scene": "invalid", "haiku_draft": "partial"}) as (env, _, _):
        base, _, _, _, _, _, _, _, send, _, _, stored, _ = env
        sid = register(base, preview=False, body=True)
        send(sid)
        rows = wait_for(lambda: stored(sid))[0]["internal_capture"]["requests"]
        assert [row["status"] for row in rows] == ["missing", "invalid", "partial"]
        assert rows[0]["artifact"] is None and rows[1]["artifact"] is None
        assert rows[2]["artifact"]["hidden_verified"]
    passed.append("missing_and_invalid_capture_do_not_break_generation_or_claim_success")

    with captured() as (env, _, calls):
        base, _, log, _, _, gate, drafting, _, send, hud, _, stored, _ = env
        gate.clear()
        sid = register(base, preview=False, body=True)
        send(sid)
        wait_for(drafting.is_set)
        send(sid, world={"game_paused": True})
        wait_for(lambda: "python_worker_stopped" in log.read_text())
        gate.set()
        wait_for(lambda: len(calls) >= 3)
        old_ids = {call["id"] for call in calls}
        assert not stored(sid) and hud(sid)["state"] == "closed"
        send(sid)
        entry = wait_for(lambda: stored(sid))[0]
        assert old_ids.isdisjoint(row["response_id"] for row in entry["internal_capture"]["requests"])
    passed.append("cancelled_job_artifacts_never_bind_to_the_next_saved_poem")

    with captured(job_timeout_ms=1400) as (env, _, calls):
        base, _, log, _, _, gate, drafting, _, send, hud, _, stored, _ = env
        gate.clear()
        sid = register(base, preview=False, body=True)
        send(sid)
        wait_for(drafting.is_set)
        wait_for(lambda: "python_worker_stopped" in log.read_text())
        assert not stored(sid) and hud(sid)["state"] == "closed"
        gate.set()
    passed.append("expired_generation_does_not_save_capture_associations")

    with captured() as (env, root, calls):
        base, _, log, _, _, _, _, _, send, _, _, _, folder = env
        sid = register(base, preview=False, body=True)
        path = folder / "memory/sessions" / sid / "long_term/haiku_entries.jsonl"
        path.mkdir(parents=True)
        send(sid)
        wait_for(lambda: "haiku_save_failed" in log.read_text())
        assert path.is_dir() and len(calls) == 3
        assert len(list(root.glob("*/manifest.json"))) == 3
    passed.append("failed_canonical_save_leaves_raw_artifacts_unbound")

    print(json.dumps({"passed": len(passed), "checks": passed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
