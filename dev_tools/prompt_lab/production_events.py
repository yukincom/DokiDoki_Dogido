#!/usr/bin/env python3
"""Replay Fabric-shaped encounter fixtures through the existing Rust executable.

No player input, prompt construction, catalogue injection, model loading, or UI.
The default is an offline mock preflight. --live forwards native LLM requests
unchanged to the model server already running at the configured localhost URL.
Each case gets a fresh session. Audio playback is disabled; text is inspected.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import urllib.request
from urllib.parse import urlsplit

CASES = [
    ("cow", "ウシ", "passive", None),
    ("sheep", "ヒツジ", "passive", None),
    ("pig", "ブタ", "passive", None),
    ("chicken", "ニワトリ", "passive", None),
    ("rabbit", "ウサギ", "passive", None),
    ("wolf", "オオカミ", "neutral", "retaliates"),
    ("bee", "ハチ", "neutral", "swarm"),
    ("iron_golem", "アイアンゴーレム", "neutral", "village_guard"),
    ("enderman", "エンダーマン", "neutral", None),
    ("zombified_piglin", "ゾンビピグリン", "neutral", "provoked_only"),
    ("zombie", "ゾンビ", "hostile", None),
    ("skeleton", "スケルトン", "hostile", None),
    ("creeper", "クリーパー", "hostile", None),
    ("witch", "ウィッチ", "hostile", None),
    ("pillager", "ピリジャー", "hostile", None),
]
TERMINAL = {"completed", "failed", "cancelled", "audio_disabled", "not_selected", "quiet", "unsupported"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def fixture(kind, temperament, caution):
    # These are scenario assumptions at the adapter boundary, not recorded play.
    # Names and caution reasons follow DogidoClientAdapter.classifyMobDisposition.
    event = {
        "schema_version": "2026-05-24", "adapter": "prompt-lab-fabric-fixture",
        "sequence": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "event": {"name": "status_snapshot", "source_kind": "system",
                  "priority_hint": "background", "certainty": "high"},
        "player": {"name": "試験", "dimension": "minecraft:overworld"},
        "world": {"weather": "clear", "biome": "plains", "sky_visible": True,
                  "ceiling_height": 20, "local_light": 15, "time_phase": "day"},
    }
    mob = {"type": kind, "distance": 3,
           "direction": {"horizontal": "right", "vertical": "same"}, "certainty": "high"}
    if temperament == "hostile":
        mob.update(entity_id="fixture-" + kind, approaching=False, on_fire=False, in_water=False)
        if kind == "creeper":
            mob["fuse_active"] = False
        event["visual_threats"] = [mob]
    else:
        mob["temperament"] = temperament
        if caution:
            mob["caution_reason"] = caution
        event["passive_mobs"] = [mob]
    return event


def launch_configuration(root):
    """Use the actual launcher to resolve settings, without starting its server."""
    sys.path.insert(0, str(root / "dogido-rust/scripts"))
    sys.path.insert(0, str(root))
    import launch_dialogue
    captured = {}

    class Captured(Exception):
        pass

    def capture(binary, command, env):
        captured.update(binary=str(binary), command=command, env=env)
        raise Captured()

    old_cwd = Path.cwd()
    try:
        with patch.object(sys, "argv", ["launch_dialogue.py", "--settings-dir", str(root)]), \
                patch.object(os, "execve", capture), redirect_stdout(io.StringIO()):
            try:
                launch_dialogue.main()
            except Captured:
                pass
    finally:
        os.chdir(old_cwd)
    if not captured:
        raise RuntimeError("Production launch configuration was not captured")
    return captured


def argument(command, name):
    return command[command.index(name) + 1]


def replace(command, name, value):
    command[command.index(name) + 1] = str(value)


def server_identity():
    pids = subprocess.check_output(
        ["lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN", "-t"], text=True).split()
    pids = sorted(set(pids))
    if len(pids) != 1:
        raise RuntimeError("Expected exactly one existing server on port 8080")
    command = subprocess.check_output(["ps", "-p", pids[0], "-o", "command="], text=True).strip()
    return {"pid": int(pids[0]), "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "command": command}


def run(args):
    root, output = args.root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    launch = launch_configuration(root)
    command, env = list(launch["command"]), dict(launch["env"])
    upstream = argument(command, "--base-url").rstrip("/")
    if urlsplit(upstream).hostname not in {"127.0.0.1", "localhost"} or urlsplit(upstream).port != 8080:
        raise RuntimeError("This comparison requires the existing app server on localhost:8080")
    if "--no-llm" in command:
        raise RuntimeError("Production configuration has LLM disabled")
    max_tokens = int(argument(command, "--max-tokens"))
    timeout = int(argument(command, "--timeout-ms")) / 1000
    identity = server_identity() if args.live else None
    prompt_path = root / "dogido_server/llm/companion_prompts.json"
    prompts = json.loads(prompt_path.read_text())
    expected_system = "\n".join([prompts["base"], prompts["modes"]["normal"], prompts["grounding"],
                                 prompts["dialogue"], prompts["choice_contract"]])
    sources = [prompt_path, root / "dogido-rust/src/environment/reaction.rs",
               root / "dogido-rust/src/environment/ambient/mobs.rs", root / "dogido-rust/src/llm.rs",
               root / "adapter/minecraft-fabric/src/main/java/dogido/fabric/DogidoClientAdapter.java"]
    manifest = {
        "mode": "live-existing-app-server" if args.live else "mock-preflight",
        "input": "synthetic Fabric-shaped events; not live Minecraft capture",
        "production_root": str(root), "binary": launch["binary"], "binary_sha256": sha(launch["binary"]),
        "sources": {str(p.relative_to(root)): sha(p) for p in sources},
        "harness_sha256": sha(__file__), "server_before": identity,
        "model": argument(command, "--model"), "upstream": upstream,
        "max_tokens": max_tokens, "timeout_seconds": timeout,
        "test_overrides": ["ephemeral loopback port", "isolated memory and audio directory",
                           "audio disabled", "local transport capture forwarding identical request bytes",
                           "probe listener auth disabled; upstream model auth preserved"],
        "player_inputs": 0,
    }
    save(output / "manifest.json", manifest)
    from check_dialogue import register, request, snapshot
    from dev_tools.prompt_lab.worker import Meter
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    calls, errors = [], []
    active_case = None

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            incoming = json.loads(body)
            record = {"case_id": active_case, "path": self.path, "request": incoming,
                      "request_bytes_sha256": hashlib.sha256(body).hexdigest()}
            index = len(calls)
            calls.append(record)
            (output / f"request-{index:02d}.json").write_bytes(body)
            status = 200
            started = time.monotonic()
            try:
                assert self.path == "/v1/chat/completions", self.path
                assert incoming["messages"][0]["content"] == expected_system, "Embedded prompt differs from current source"
                assert incoming["max_tokens"] == max_tokens
                context = json.loads(incoming["messages"][1]["content"])
                assert context["topic"] == "mob", context
                assert context["recent"] in ([], {}, {"conversation": []}), context["recent"]
                assert "user_text" not in context and "catalog_topic_hints" not in context
                if args.live:
                    headers = {"Content-Type": "application/json"}
                    if env.get("DOGIDO_LLM_API_KEY"):
                        headers["Authorization"] = "Bearer " + env["DOGIDO_LLM_API_KEY"]
                    req = urllib.request.Request(upstream + "/chat/completions", body, headers)
                    with Meter(identity["pid"]) as meter:
                        with urllib.request.urlopen(req, timeout=timeout) as response:
                            raw = response.read()
                    record["process_metrics"] = meter.metrics()
                    record["process_samples"] = meter.samples
                else:
                    raw = json.dumps({"id": "mock-preflight", "object": "chat.completion", "created": 0,
                        "model": "mock", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                            "role": "assistant", "content": '{"action":"speak","speech":"お、そこにおるやん。"}'}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
                record["response"] = json.loads(raw)
            except Exception as error:
                errors.append(str(error))
                record["error"] = str(error)
                status, raw = 502, json.dumps({"error": "Probe transport failed; inspect local record"}).encode()
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
            usage = record.get("response", {}).get("usage", {})
            tokens = usage.get("completion_tokens")
            record["speed"] = {"completion_tokens": tokens, "prompt_tokens": usage.get("prompt_tokens"),
                               "tokens_per_second_including_prompt": tokens / (record["elapsed_ms"] / 1000)
                               if tokens and record["elapsed_ms"] else None,
                               "ttft_ms": None, "decode_tokens_per_second": None}
            (output / f"response-{index:02d}.json").write_bytes(raw)
            save(output / "model-calls.json", calls)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    proxy.daemon_threads = False
    thread = threading.Thread(target=proxy.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    replace(command, "--base-url", f"http://127.0.0.1:{proxy.server_port}/v1")
    replace(command, "--listen", "127.0.0.1:0")
    replace(command, "--audio-dir", output / "audio")
    haiku = json.loads(argument(command, "--haiku-settings"))
    haiku["memory_dir"] = str(output / "memory")
    replace(command, "--haiku-settings", json.dumps(haiku))
    if "--no-audio" not in command:
        command.append("--no-audio")
    env.pop("DOGIDO_AUTH_TOKEN", None)
    results, process = [], None
    save(output / "native-command.json", command)
    try:
        with (output / "native-runtime.log").open("w") as log:
            process = subprocess.Popen(command, env=env, cwd=root, stdout=subprocess.PIPE,
                                       stderr=log, text=True)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=10):
                    raise RuntimeError("Native startup timed out")
                ready = json.loads(process.stdout.readline())
            base = "http://" + ready["address"]
            for kind, label, temperament, caution in CASES:
                active_case = kind
                sid = register(base, preview=False)
                event = fixture(kind, temperament, caution)
                save(output / f"event-{kind}.json", event)
                before = len(calls)
                receipt = request(base, "/api/v1/game-events", event, sid=sid)
                deadline, rows = time.monotonic() + timeout + 10, []
                while time.monotonic() < deadline:
                    rows = [r for r in snapshot(base)["utterances"] if r["session_id"] == sid]
                    if rows and all(r["playback_status"] in TERMINAL for r in rows):
                        break
                    if not rows and time.monotonic() > deadline - timeout - 9:
                        break  # no selected speech is a valid native decision
                    time.sleep(0.05)
                else:
                    raise RuntimeError(f"Native case did not settle: {kind}: {rows}")
                result = {"id": kind, "label": label, "session_id": sid, "event": event,
                          "receipt": receipt, "rows": rows, "model_calls": calls[before:]}
                results.append(result)
                save(output / "results.json", results)
                print(json.dumps({"id": kind, "label": label, "model_calls": len(calls) - before,
                                  "outputs": [{"text": r.get("text"), "status": r.get("playback_status"),
                                  "outcome": r.get("environment_reaction_outcome")} for r in rows]},
                                 ensure_ascii=False), flush=True)
                request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
                if errors:
                    raise RuntimeError("Native request verification/transport failed: " + "; ".join(errors))
    finally:
        if process is not None:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=3)
    manifest["server_after"] = server_identity() if args.live else None
    if args.live and manifest["server_after"] != identity:
        raise RuntimeError("Existing model server identity changed during comparison")
    manifest.update(completed=len(results) == len(CASES), model_calls=len(calls),
                    native_returncode=process.returncode, source_hashes_unchanged=all(
                        sha(root / name) == digest for name, digest in manifest["sources"].items()))
    save(output / "manifest.json", manifest)
    assert process.returncode == 0 and not errors and manifest["source_hashes_unchanged"]
    print(json.dumps({"output": str(output), "cases": len(results), "model_calls": len(calls)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    run(parser.parse_args())
