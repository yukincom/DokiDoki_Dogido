#!/usr/bin/env python3
"""明示された身体視点撮影を受信し、Modが作った同frameの192px画像を検証する独立診断。

watchは完成画像の検証・記録だけ。sendは保存済みの一枚を既存のローカル画像診断通信で一回送る。
身体制御・本体会話・音声・ゲーム起動には接続しない。画像はMinecraftの専用期限管理下に置く。
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import time

BUILD = "2026-10-21-chat-image-v1"
QUESTION = "これはマインクラフトのスクリーンショットや。プレイヤーと村人を見分けて、それぞれどこにいるか、見た目の何で区別したかを短く教えて。分からんところは分からんと言ってな。"
ID = re.compile(r"view_[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\Z")
IMAGE_NAMES = {"frame.png", "player-192.png"}
REPO = Path(__file__).resolve().parents[2]


def now():
    return datetime.now(timezone.utc)


def date(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone_missing")
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("invalid_record_file")
    return json.loads(path.read_text())


@contextmanager
def retention_lock(directory):
    """Javaの削除処理と同じmkdir排他。残留lockを推測で解除しない。"""
    lock = directory / "retention.lock"
    lock.mkdir()
    try:
        yield
    finally:
        lock.rmdir()


def capture_dir(game, capture_id):
    if not ID.fullmatch(capture_id):
        raise ValueError("invalid_capture_id")
    directory = game / "dogido-vision" / "captures" / capture_id
    for parent in (directory, directory.parent, directory.parent.parent):
        if parent.is_symlink():
            raise ValueError("symlink_capture_directory")
    if not directory.is_dir():
        raise ValueError("missing_capture_directory")
    return directory


def retained(directory):
    """lock内で保持期限と全画像hashを確認。本文記録は画像削除後も残す。"""
    data = read(directory / "retention.json")
    if data.get("schema_version") != 1 or data.get("deleted_at") or date(data["expires_at"]) <= now():
        raise ValueError("expired_image")
    images = data["images"]
    if not 1 <= len(images) <= 2 or len({row["filename"] for row in images}) != len(images):
        raise ValueError("invalid_image_manifest")
    for row in images:
        if row["filename"] not in IMAGE_NAMES:
            raise ValueError("unmanaged_image")
        path = directory / row["filename"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024 or digest(path) != row["sha256"]:
            raise ValueError("image_hash_mismatch")
    return data


def used(directory, data):
    stamp = now()
    data["last_used_at"] = stamp.isoformat()
    data["expires_at"] = (stamp + timedelta(hours=24)).isoformat()
    save(directory / "retention.json", data)


def ready(game, started):
    data = read(game / "dogido-vision" / "ready.json")
    stamp = date(data["updated_at"])
    if data.get("schema_version") != 1 or data.get("adapter_build") != BUILD or data.get("status") != "ready":
        raise ValueError("waiting_for_body_prepare")
    if stamp < started or not -2 <= (now() - stamp).total_seconds() <= 5:
        raise ValueError("stale_ready_record")
    if not data.get("boot_id") or not data.get("context"):
        raise ValueError("missing_capture_context")
    return data


def validate(data, capture_id, expected=None, started=None):
    if data.get("schema_version") != 1 or data.get("capture_id") != capture_id or data.get("adapter_build") != BUILD:
        raise ValueError("capture_contract_mismatch")
    if data.get("viewpoint") != "dogido_client_render_eye" or data.get("camera_restored") is not True:
        raise ValueError("camera_not_verified")
    if expected and (data.get("boot_id") != expected["boot_id"] or data.get("context") != expected["context"]):
        raise ValueError("capture_context_mismatch")
    stamp = date(data["captured_at"])
    if (started and stamp < started) or stamp > now() + timedelta(seconds=2):
        raise ValueError("capture_time_mismatch")
    if data.get("annotation_status") != "available":
        raise ValueError("annotation_unavailable:" + str(data.get("annotation_status")))
    width, height = data["width"], data["height"]
    if type(width) is not int or type(height) is not int or min(width, height) <= 0 or width * height > 16777216:
        raise ValueError("invalid_dimensions")
    box = data["player_box"]
    coords = [box[k] for k in ("left", "top", "right", "bottom")]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in coords):
        raise ValueError("invalid_player_box")
    left, top, right, bottom = coords
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("invalid_player_box")


def annotate(game, capture_id, expected=None, started=None):
    """Modが同じframeから作った192px画像を検証して読む。ここでは再描画しない。"""
    from PIL import Image
    directory = capture_dir(game, capture_id)
    with retention_lock(directory):
        data = read(directory / "frame.json")
        validate(data, capture_id, expected, started)
        retention = retained(directory)
        source = directory / "frame.png"
        if digest(source) != data["image_sha256"]:
            raise ValueError("frame_hash_mismatch")
        if data.get("annotated_image") != "player-192.png":
            raise ValueError("native_thumbnail_missing")
        target = directory / "player-192.png"
        image_hash = data.get("annotated_sha256")
        if not any(row["filename"] == target.name and row["sha256"] == image_hash for row in retention["images"]):
            raise ValueError("native_thumbnail_not_retained")
        if digest(target) != image_hash:
            raise ValueError("annotation_hash_mismatch")
        with Image.open(source) as opened:
            if list(opened.size) != [data["width"], data["height"]]:
                raise ValueError("image_dimensions_mismatch")
        with Image.open(target) as image:
            size = list(image.size)
            if size != [data.get("annotated_width"), data.get("annotated_height")] or min(size) <= 0 or max(size) > 192:
                raise ValueError("annotation_dimensions_mismatch")
        sx, sy = size[0] / data["width"], size[1] / data["height"]
        b = data["player_box"]
        box = [max(0, math.floor(b["left"] * sx)), max(0, math.floor(b["top"] * sy)),
               min(size[0] - 1, math.ceil(b["right"] * sx)), min(size[1] - 1, math.ceil(b["bottom"] * sy))]
        used(directory, retention)
        return {"capture": data, "annotated": {"path": str(target), "sha256": image_hash,
                "size": size, "player_box": box, "scale": [sx, sy],
                "label": "PLAYER", "resampling": "Mod Lanczos3", "line_width": 1}}


def watch(args, game, output):
    output.mkdir(parents=True, exist_ok=False)
    (output / "received").mkdir()
    started = now()
    state = {"schema_version": 1, "pid": os.getpid(), "started_at": started.isoformat(),
             "game_dir": str(game), "expected_adapter_build": BUILD, "status": "waiting_for_minecraft"}
    seen = set()
    captures = game / "dogido-vision" / "captures"
    if captures.is_dir():
        seen = {p.name for p in captures.iterdir()}
    deadline = time.monotonic() + args.timeout
    previous = None
    try:
        while time.monotonic() < deadline:
            try:
                expected = ready(game, started)
                state.update(status="ready_to_capture", native=expected)
            except (OSError, ValueError, KeyError, TypeError) as error:
                expected = None
                state.update(status="waiting_for_minecraft", reason=str(error))
            state["updated_at"] = now().isoformat()
            save(output / "watcher.json", state)
            if state["status"] != previous:
                print(json.dumps(state, ensure_ascii=False), flush=True)
                previous = state["status"]
            if expected and captures.is_dir():
                for item in sorted(captures.iterdir()):
                    if item.name in seen or not ID.fullmatch(item.name) or not (item / "frame.json").exists():
                        continue
                    try:
                        result = annotate(game, item.name, expected, started)
                    except FileExistsError:
                        continue  # Java削除処理中なら次周で再確認。
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        result = {"capture_id": item.name, "error": str(error)}
                    seen.add(item.name)
                    save(output / "received" / (item.name + ".json"), result)
                    with (output / "events.jsonl").open("a") as handle:
                        handle.write(json.dumps({"received_at": now().isoformat(), **result}, ensure_ascii=False) + "\n")
                    print(json.dumps({"received": item.name, "error": result.get("error"),
                                      "annotated": result.get("annotated")}, ensure_ascii=False), flush=True)
            time.sleep(.5)
    finally:
        state.update(status="stopped", updated_at=now().isoformat())
        save(output / "watcher.json", state)


def send(args, game, output):
    capture_id = args.capture
    directory = capture_dir(game, capture_id)
    record = read(output / "received" / (capture_id + ".json"))
    validate(record["capture"], capture_id)
    result_path = output / (capture_id + "-result.json")
    if result_path.exists():
        raise ValueError("capture_already_sent")
    # 過去の解像度試験と同じ通信処理を使い、新たなprovider・retry・自動生成経路は作らない。
    spec = importlib.util.spec_from_file_location("image_experiment", REPO / "docs/research/2026-10-20-vision-resolution/run_experiment.py")
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    base = json.loads((REPO / "dogido_server/llm/companion_prompts.json").read_text())["base"]
    parameters = {"model": "default_model", "temperature": 0, "seed": 42, "max_tokens": 400,
                  "stream": False, "cache_prompt": False, "chat_template_kwargs": {"enable_thinking": False}}
    request_record = {"capture_id": capture_id, "system": base, "question": QUESTION, "parameters": parameters,
                      "annotated": record["annotated"], "started_at": now().isoformat()}
    # 送信中は同じlockを保持し、利用前後に24時間期限を延長する。
    with retention_lock(directory):
        retention = retained(directory)
        path = directory / "player-192.png"
        if digest(path) != record["annotated"]["sha256"]:
            raise ValueError("annotation_changed")
        used(directory, retention)
        payload = dict(parameters)
        payload["messages"] = [{"role": "system", "content": base}, {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()}},
            {"type": "text", "text": QUESTION}]}]
        save(output / (capture_id + "-request.json"), request_record)
        try:
            result, elapsed = transport.send_payload("http://127.0.0.1:8080/v1/chat/completions", payload)
            row = {"capture_id": capture_id, "completed_at": now().isoformat(), "elapsed_seconds": elapsed,
                   "model": result.get("model"), "usage": result.get("usage"), "timings": result.get("timings"),
                   "id": result.get("id"), "finish_reason": result["choices"][0].get("finish_reason"),
                   "content": result["choices"][0]["message"].get("content", "")}
            save(result_path, row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        except Exception as error:
            save(result_path, {"capture_id": capture_id, "error": str(error), "completed_at": now().isoformat()})
            raise
        finally:
            used(directory, retention)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["watch", "send"])
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--capture")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 7200 or (args.mode == "send" and not args.capture):
        parser.error("timeoutは1〜7200秒、sendにはcapture IDが必要です")
    {"watch": watch, "send": send}[args.mode](args, args.game_dir.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
