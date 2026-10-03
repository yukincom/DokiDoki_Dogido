"""Serialized batches shared between CLI and browser clients."""
import os
import subprocess
import sys
import threading
from pathlib import Path

from .core import Lab, exclusive, now, read_json, safe_id, write_json


def execute_batch(lab: Lab, batch_id: str):
    batch_id = safe_id(batch_id)
    batch_path = lab.runs / (batch_id + ".batch.json")
    cancel = lab.runs / (batch_id + ".cancel")
    with exclusive(lab.runs / ".execution.lock") as execution_lock:
        batch = read_json(batch_path)
        if batch.get("status"):
            raise ValueError("このバッチは実行済みです。新しい試験を準備してください。")
        batch.update(status="running", started_at=now())
        write_json(batch_path, batch)
        failed = False
        try:
            for run_id in batch["runs"]:
                if cancel.exists():
                    break
                manifest = read_json(lab.runs / run_id / "manifest.json")
                python = manifest["model"].get("python") or sys.executable
                env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
                           DOGIDO_LAB_PARENT_PID=str(os.getpid()))
                with (lab.runs / run_id / "worker.log").open("w") as log:
                    child = subprocess.Popen([python, "-m", "dev_tools.prompt_lab", "--repo", str(lab.repo),
                            "--directory", str(lab.directory), "worker", run_id, "--cancel", str(cancel)],
                            cwd=lab.repo, env=env, stdout=log, stderr=subprocess.STDOUT,
                            pass_fds=(execution_lock.fileno(),))
                    limit = 300 + manifest["case_count"] * (manifest["settings"]["timeout_seconds"] + 10)
                    try:
                        code = child.wait(timeout=limit)
                    except subprocess.TimeoutExpired:
                        child.terminate()
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            child.kill(); child.wait()
                        code = -1
                    except BaseException:
                        child.terminate()
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            child.kill(); child.wait()
                        raise
                    if code:
                        failed = True
                        current = read_json(lab.runs / run_id / "manifest.json")
                        if current["status"] in ("prepared", "running"):
                            current.update(status="failed", error=f"worker exited ({code}); worker.log参照", finished_at=now())
                            write_json(lab.runs / run_id / "manifest.json", current)
                    elif read_json(lab.runs / run_id / "manifest.json")["status"] in ("failed", "completed_with_errors"):
                        failed = True
            batch["status"] = "cancelled" if cancel.exists() else ("completed_with_errors" if failed else "completed")
        except Exception as exc:
            batch.update(status="failed", error=str(exc))
            raise
        finally:
            for run_id in batch["runs"]:
                path = lab.runs / run_id / "manifest.json"
                manifest = read_json(path)
                if manifest["status"] == "prepared":
                    manifest.update(status="cancelled", finished_at=now())
                    write_json(path, manifest)
            batch["finished_at"] = now()
            write_json(batch_path, batch)


class Manager:
    def __init__(self, lab):
        self.lab, self.thread, self.batch_id, self.error = lab, None, None, None
        self.lock = threading.Lock()

    def state(self):
        return {"running": bool(self.thread and self.thread.is_alive()), "batch_id": self.batch_id, "error": self.error}

    def start(self, batch_id):
        with self.lock:
            if self.state()["running"]:
                raise ValueError("試験中です。完了後に実行してください。")
            self.batch_id, self.error = safe_id(batch_id), None
            read_json(self.lab.runs / (self.batch_id + ".batch.json"))
            def work():
                try:
                    execute_batch(self.lab, self.batch_id)
                except Exception as exc:
                    self.error = str(exc)
            self.thread = threading.Thread(target=work, daemon=True)
            self.thread.start()
        return self.state()

    def cancel(self):
        if self.state()["running"]:
            (self.lab.runs / (self.batch_id + ".cancel")).touch()
        return {**self.state(), "message": "HTTPは現在の1件が終了後、MLXは次の生成トークンで停止します。"}
