"""One model worker per run; local HTTP or cached MLX, without service lifecycle changes."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import urlparse

from .core import Lab, append_json, digest, inspect_output, now, read_json, write_json


def cpu_seconds(value: str) -> float:
    days, _, rest = value.rpartition("-")
    parts = [float(v) for v in rest.split(":")]
    return (int(days) * 86400 if days else 0) + sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def process_sample(pid: int) -> dict | None:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "pid=,rss=,time="],
                            text=True, capture_output=True, timeout=2)
    fields = result.stdout.split()
    if len(fields) != 3 or int(fields[0]) != pid:
        return None
    return {"monotonic": time.perf_counter(), "rss_bytes": int(fields[1]) * 1024,
            "cpu_seconds": cpu_seconds(fields[2])}


class Meter:
    def __init__(self, pid: int | None):
        self.pid = pid
        self.samples = []
        self.stop = threading.Event()
        self.thread = None

    def take(self):
        if self.pid:
            try:
                value = process_sample(self.pid)
                if value:
                    self.samples.append(value)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass

    def loop(self):
        while not self.stop.wait(.2):
            self.take()

    def __enter__(self):
        self.take()
        if self.pid:
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.take()

    def metrics(self):
        samples = self.samples
        elapsed = samples[-1]["monotonic"] - samples[0]["monotonic"] if len(samples) > 1 else 0
        return {"metrics_pid": self.pid, "process_samples": len(samples),
                "cpu_percent": max(0, 100 * (samples[-1]["cpu_seconds"] - samples[0]["cpu_seconds"]) / elapsed)
                if elapsed else None,
                "peak_rss_bytes": max((s["rss_bytes"] for s in samples), default=None),
                "rss_before_bytes": samples[0]["rss_bytes"] if samples else None,
                "rss_after_bytes": samples[-1]["rss_bytes"] if samples else None}


def local_endpoint(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("HTTP試験はローカルのモデルサーバーに限定しています。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URLに認証情報やqueryを含めないでください。")
    return url.rstrip("/") + "/chat/completions"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("モデルAPIのリダイレクトは利用しません。")


@contextmanager
def deadline(seconds):
    def expired(*_):
        raise TimeoutError("HTTP生成が試験期限を超えました。")
    previous = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous)


class PartialGenerationError(ValueError):
    def __init__(self, message, raw, events):
        super().__init__(message)
        self.raw_output, self.raw_response = raw, {"stream_events": events}


def watch_parent():
    parent = os.environ.get("DOGIDO_LAB_PARENT_PID")
    if not parent:
        return
    def terminated(*_):
        raise SystemExit("試験の親プロセスが終了しました。")
    signal.signal(signal.SIGTERM, terminated)
    def monitor():
        while os.getppid() == int(parent):
            time.sleep(.2)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=monitor, daemon=True).start()


def http_generate(payload: dict, profile: dict, timeout: int) -> dict:
    endpoint = local_endpoint(profile["base_url"])
    headers = {"Content-Type": "application/json"}
    secret = os.environ.get(profile.get("api_key_env", "DOGIDO_LLM_API_KEY"))
    if secret:
        headers["Authorization"] = "Bearer " + secret
    request = urllib.request.Request(endpoint, json.dumps(payload).encode(), headers)
    start = time.perf_counter()
    first = None
    raw, finish, model, usage, events = "", None, None, {}, []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with deadline(timeout), opener.open(request, timeout=timeout) as response:
            if payload.get("stream"):
                complete = False
                for line in response:
                    if time.perf_counter() - start > timeout:
                        raise TimeoutError("HTTP生成が試験期限を超えました。")
                    if not line.startswith(b"data:"):
                        continue
                    text = line[5:].strip()
                    if text == b"[DONE]":
                        complete = True
                        break
                    event = json.loads(text)
                    events.append(event)
                    model = event.get("model", model)
                    usage = event.get("usage") or usage
                    for choice in event.get("choices", []):
                        piece = choice.get("delta", {}).get("content") or ""
                        if piece and first is None:
                            first = (time.perf_counter() - start) * 1000
                        raw += piece
                        finish = choice.get("finish_reason") or finish
                if not complete and not finish:
                    raise ValueError("streamが終了通知なしで切断されました。")
                body = {"stream_events": events}
            else:
                body = json.load(response)
                choice = body["choices"][0]
                raw = choice["message"].get("content") or ""
                finish, model, usage = choice.get("finish_reason"), body.get("model"), body.get("usage") or {}
    except urllib.error.HTTPError as exc:
        raise ValueError(f"モデルAPI HTTP {exc.code}（認証値・レスポンス本文はエラーログに残しません）") from None
    except Exception as exc:
        if payload.get("stream"):
            raise PartialGenerationError(str(exc), raw, events) from None
        raise
    elapsed = (time.perf_counter() - start) * 1000
    tokens = usage.get("completion_tokens")
    return {"raw_output": raw, "raw_response": body, "response_model": model, "finish_reason": finish,
            "metrics": {"elapsed_ms": elapsed, "ttft_ms": first, "completion_tokens": tokens,
                        "prompt_tokens": usage.get("prompt_tokens"), "peak_mlx_bytes": None,
                        "tokens_per_second": tokens / (elapsed / 1000) if tokens else None,
                        "rate_scope": "completion tokens / total request time (includes prefill)"}}


class MLXGenerator:
    def __init__(self, profile: dict):
        # An explicit local snapshot avoids downloads and prevents changing the resident server.
        from mlx_lm import load
        path = Path(profile.get("local_path") or "")
        if not path.is_absolute() or not (path / "config.json").is_file():
            raise ValueError("ダウンロード済みのモデルsnapshotが見つかりません。")
        self.model, self.tokenizer = load(str(path))

    def generate(self, payload: dict, timeout: int, cancelled) -> dict:
        import mlx.core as mx
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        if payload.get("seed") is not None:
            mx.random.seed(payload["seed"])
        prompt = self.tokenizer.apply_chat_template(payload["messages"], tokenize=True,
                    add_generation_prompt=True, **payload.get("chat_template_kwargs", {}))
        mx.reset_peak_memory()
        start, first, raw, final = time.perf_counter(), None, "", None
        for part in stream_generate(self.model, self.tokenizer, prompt=prompt,
                    max_tokens=payload["max_tokens"],
                    sampler=make_sampler(temp=payload["temperature"], top_p=payload.get("top_p", 1.0))):
            if cancelled():
                raise InterruptedError("試験停止を受け付けました。")
            if time.perf_counter() - start > timeout:
                raise TimeoutError("MLX生成が試験期限を超えました。")
            if part.text and first is None:
                first = (time.perf_counter() - start) * 1000
            raw += part.text
            final = part
        elapsed = (time.perf_counter() - start) * 1000
        if final is None:
            raise ValueError("MLXから生成結果が返りませんでした。")
        return {"raw_output": raw, "raw_response": None, "response_model": payload["model"],
                "finish_reason": final.finish_reason,
                "metrics": {"elapsed_ms": elapsed, "ttft_ms": first, "completion_tokens": final.generation_tokens,
                            "prompt_tokens": final.prompt_tokens, "peak_mlx_bytes": int(mx.get_peak_memory()),
                            "decode_tokens_per_second": final.generation_tps,
                            "prefill_tokens_per_second": final.prompt_tps,
                            "tokens_per_second": final.generation_tokens / (elapsed / 1000),
                            "rate_scope": "completion tokens / total request time (includes prefill)"}}


def execute_run(lab: Lab, run_id: str, cancel_path: Path):
    directory = lab.runs / run_id
    manifest = read_json(directory / "manifest.json")
    requests = read_json(directory / "requests.json")
    if manifest["status"] != "prepared" or (directory / "results.jsonl").exists():
        raise ValueError("実行済みの試験は上書きしません。新しい試験を作成してください。")
    if digest(requests) != manifest["requests_sha256"]:
        raise ValueError("準備後に送信内容が変更されました。新しい試験を作成してください。")
    manifest.update(status="running", started_at=now())
    write_json(directory / "manifest.json", manifest)
    try:
        profile, settings = manifest["model"], manifest["settings"]
        backend = profile["backend"]
        if backend not in ("http", "mlx"):
            raise ValueError("不明なbackendです。")
        pid = os.getpid() if backend == "mlx" else profile.get("metrics_pid")
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise ValueError("計測PIDは正の整数またはnullです。")
        manifest["measurement_scope"] = ("isolated MLX worker, fresh KV cache per case; CPU100%=one core; RSS and MLX allocations differ"
            if backend == "mlx" else "whole HTTP model-server process; cache/concurrent work may affect metrics; CPU100%=one core")
        generator = None
        started = time.perf_counter()
        with Meter(pid) as load_meter:
            if backend == "mlx":
                generator = MLXGenerator(profile)
        manifest["load_metrics"] = {**load_meter.metrics(), "elapsed_ms": (time.perf_counter() - started) * 1000}
        write_json(directory / "manifest.json", manifest)
        failed = 0
        for request in requests:
            if cancel_path.exists():
                break
            result = {k: request[k] for k in ("index", "case_id", "repeat", "label", "group", "request_sha256")}
            result["started_at"] = now()
            start = time.perf_counter()
            with Meter(pid) as meter:
                try:
                    generated = (generator.generate(request["payload"], settings["timeout_seconds"], cancel_path.exists)
                        if generator else http_generate(request["payload"], profile, settings["timeout_seconds"]))
                    result.update(generated)
                    result.update(inspect_output(generated["raw_output"], manifest["candidate"]["response_format"], settings["watch_terms"]))
                except Exception as exc:
                    failed += 1
                    result.update(error=f"{type(exc).__name__}: {exc}", metrics={"elapsed_ms": (time.perf_counter() - start) * 1000})
                    if isinstance(exc, PartialGenerationError):
                        result.update(raw_output=exc.raw_output, raw_response=exc.raw_response)
            result["metrics"].update(meter.metrics())
            append_json(directory / "results.jsonl", result)
            print(json.dumps({"run": run_id, "case": result["case_id"], "error": result.get("error")}, ensure_ascii=False), flush=True)
        manifest["status"] = "cancelled" if cancel_path.exists() else ("completed_with_errors" if failed else "completed")
    except BaseException as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        manifest["finished_at"] = now()
        write_json(directory / "manifest.json", manifest)
        (directory / "report.html").write_text(lab.export_html(run_id), encoding="utf-8")
