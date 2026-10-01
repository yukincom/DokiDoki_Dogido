#!/usr/bin/env python3
"""専用Chrome/MCPだけのstdioアダプタ。会話・同意・状態変更・モデルを所有しない。"""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.language_dialogue.main_web import build_main_web_research, inspect_main_web_availability


def serve(input_stream=sys.stdin, output_stream=sys.stdout, *, factory=build_main_web_research,
          inspect=inspect_main_web_availability):
    stopping = threading.Event()
    output_lock = threading.Lock()
    workers = []
    provider = None
    busy = False
    state_lock = threading.Lock()

    def send(request_id, **values):
        with output_lock:
            output_stream.write(json.dumps({"request_id": request_id, **values}, ensure_ascii=False) + "\n")
            output_stream.flush()

    def search(request_id, proposal):
        nonlocal provider, busy
        try:
            if stopping.is_set():
                return
            if provider is None:
                provider, availability = factory()
                if provider is None:
                    send(request_id, error=availability.reason)
                    return
            if stopping.is_set():
                return
            result = provider.search(
                proposal["target"], proposal["search_terms"], proposal["facet"],
                web_query=proposal["web_query"], known_urls=proposal["known_urls"],
                cancelled=stopping.is_set,
            )
            if not stopping.is_set():
                send(request_id, result=asdict(result))
        except Exception as exc:
            if not stopping.is_set():
                send(request_id, error=type(exc).__name__)
        finally:
            with state_lock:
                busy = False

    try:
        while True:
            line = input_stream.readline(1_000_001)
            if not line:
                break
            if len(line) > 1_000_000 or not line.endswith("\n"):
                raise ValueError("web_adapter_frame_too_large")
            frame = json.loads(line)
            if not isinstance(frame, dict) or not isinstance(frame.get("request_id"), str) or not 0 < len(frame["request_id"]) <= 160:
                raise ValueError("invalid_web_adapter_frame")
            request_id = frame["request_id"]
            if frame.get("op") == "close" and set(frame) == {"op", "request_id"}:
                break
            if frame.get("op") == "inspect" and set(frame) == {"op", "request_id"}:
                # ファイルとSDKの有無だけ。factoryもChromeも呼ばない。
                send(request_id, availability=asdict(inspect()))
                continue
            if frame.get("op") != "search" or set(frame) != {"op", "request_id", "proposal"}:
                raise ValueError("unsupported_web_adapter_operation")
            p = frame["proposal"]
            required = {"question", "target", "facet", "search_terms", "web_query", "trigger_reason", "known_urls"}
            if not isinstance(p, dict) or set(p) != required:
                raise ValueError("invalid_web_proposal")
            if any(not isinstance(p[k], str) for k in required - {"search_terms", "known_urls"}) or any(not isinstance(p[k], list) or any(not isinstance(v, str) for v in p[k]) for k in ("search_terms", "known_urls")):
                raise ValueError("invalid_web_proposal_type")
            with state_lock:
                if busy:
                    send(request_id, error="busy")
                    continue
                busy = True
            worker = threading.Thread(target=search, args=(request_id, p), daemon=True)
            workers.append(worker)
            worker.start()
    finally:
        stopping.set()
        # EOF/closeは検索中でも読める。検索workerのcancel predicateが所有SDKを終了する。
        for worker in workers:
            worker.join(timeout=12)
        if provider is not None:
            provider.client.close()
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError("web_adapter_shutdown_incomplete")


if __name__ == "__main__":
    serve()
