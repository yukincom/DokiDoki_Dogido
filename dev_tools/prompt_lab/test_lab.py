"""Tool integrity checks. Uses a fake local model, never a real LLM."""
import copy
import json
import shutil
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dev_tools.prompt_lab.core import Lab, digest, exclusive, read_json, read_jsonl, write_json
from dev_tools.prompt_lab.worker import PartialGenerationError, cpu_seconds, deadline, execute_run, http_generate, local_endpoint

HERE = Path(__file__).parent


@pytest.fixture
def lab(tmp_path):
    for name in ("candidates", "suites"):
        shutil.copytree(HERE / name, tmp_path / name)
    shutil.copy(HERE / "models.json", tmp_path / "models.json")
    # Source remains read-only; experiment output is under pytest's temporary directory.
    import dogido_server
    return Lab(Path(dogido_server.__file__).parent.parent, tmp_path)


def spec():
    return {"candidate_ids": ["previous-probe"], "model_ids": ["http-current"],
            "suite_id": "mobs15"}








def test_prepare_is_frozen_and_candidate_edits_detect_conflicts(lab):
    batch = lab.prepare(spec())
    path = lab.runs / batch["runs"][0] / "requests.json"
    before = path.read_bytes()
    c = lab.candidate("previous-probe")
    changed = copy.deepcopy(c); changed["components"]["base"] = "new candidate"
    lab.save_candidate(changed, digest(c))
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="更新"):
        lab.save_candidate(c, digest(c))
    with pytest.raises(ValueError, match="正本"):
        lab.save_candidate(lab.canonical())


def test_shared_lock_rejects_overlap(lab):
    with exclusive(lab.runs / ".execution.lock"):
        with pytest.raises(ValueError, match="実行中"):
            with exclusive(lab.runs / ".execution.lock"):
                pass


def test_modified_request_is_not_executed(lab):
    batch = lab.prepare(spec()); rid = batch["runs"][0]
    write_json(lab.runs / rid / "requests.json", [])
    with pytest.raises(ValueError, match="変更"):
        execute_run(lab, rid, lab.runs / "cancel")
    assert not (lab.runs / rid / "results.jsonl").exists()


@pytest.fixture
def fake_api():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            data = {"model":"fake-test-only", "choices":[{"message":{"content":'{"action":"speak","speech":"お、ウシやん！"}'},"finish_reason":"stop"}],"usage":{"completion_tokens":12,"prompt_tokens":60}}
            raw = json.dumps(data).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield server.server_port, seen
    server.shutdown(); server.server_close(); thread.join()


def test_mock_http_saves_exact_raw_and_missing_metrics(lab, fake_api):
    port, seen = fake_api
    batch = lab.prepare({**spec(), "case_ids":["cow"], "model_overrides":{"http-current":{"base_url":f"http://127.0.0.1:{port}/v1"}}})
    rid = batch["runs"][0]; execute_run(lab, rid, lab.runs / "cancel")
    result = lab.run(rid)
    assert result["manifest"]["status"] == "completed"
    assert result["results"][0]["format_valid"] is True
    assert result["results"][0]["raw_output"] == '{"action":"speak","speech":"お、ウシやん！"}'
    assert result["results"][0]["metrics"]["cpu_percent"] is None
    assert result["results"][0]["metrics"]["ttft_ms"] is None
    assert len(seen) == 1
    with pytest.raises(ValueError, match="上書き"):
        execute_run(lab, rid, lab.runs / "cancel")


def test_cancel_before_first_case_sends_nothing(lab, fake_api):
    port, seen = fake_api
    batch = lab.prepare({**spec(), "model_overrides":{"http-current":{"base_url":f"http://127.0.0.1:{port}/v1"}}})
    cancel = lab.runs / "cancel"; cancel.touch()
    execute_run(lab, batch["runs"][0], cancel)
    assert not seen
    assert lab.run(batch["runs"][0])["manifest"]["status"] == "cancelled"


def test_endpoint_and_process_time():
    assert cpu_seconds("01:02.50") == 62.5
    assert cpu_seconds("1-02:03:04") == 93784
    assert local_endpoint("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080/v1/chat/completions"
    with pytest.raises(ValueError): local_endpoint("https://example.com/v1")
    with pytest.raises(ValueError): local_endpoint("http://secret@localhost:8080/v1")


def test_notes_and_html_escape(lab):
    note = lab.add_note({"author":"Gemini", "body":"<script>bad()</script>"})
    assert lab.notes() == [note]
    batch = lab.prepare(spec()); rid = batch["runs"][0]
    manifest = read_json(lab.runs / rid / "manifest.json")
    manifest["candidate"]["title"] = "<script>bad()</script>"
    write_json(lab.runs / rid / "manifest.json", manifest)
    page = lab.export_html(rid)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_total_deadline_interrupts_slow_read():
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        with deadline(.03):
            time.sleep(1)
    assert time.perf_counter() - started < .5


def test_stream_eof_preserves_partial_output_as_error(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n'
    class Opener:
        def open(self, *args, **kwargs): return Response()
    import urllib.request
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(PartialGenerationError, match="終了通知") as exc:
        http_generate({"stream":True}, {"base_url":"http://127.0.0.1:1/v1"}, 1)
    assert exc.value.raw_output == "partial"
