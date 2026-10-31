#!/usr/bin/env python3
"""保存済みMinecraft画像の独立診断。本体・設定・原本・記憶は変更しない。

見込み: モブ名を教えず、同じ既存質問で画像の認識を比較できる。
合格条件: ケースの事前目視正解と返答の対象・種類を対応させ、捏造も記録する。
範囲: 既存localhost実モデルへの直接入力。索敵外実測や本体の戦闘動作は未検証。
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LEGACY = REPO / "docs/research/2026-10-20-vision-resolution/run_experiment.py"
ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def run():
    """凍結済み条件とhashを照合し一件ずつ送る。自動再試行や履歴の持越しはない。"""
    condition = json.loads((HERE / "conditions.json").read_text())
    assert condition["endpoint"] == ENDPOINT
    assert sha(LEGACY) == condition["sender_sha256"]
    spec = importlib.util.spec_from_file_location("existing_vision_probe", LEGACY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # 一覧取得だけで稼働モデルを再確認する。ロード・差替えAPIは使わない。
    with urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=10) as response:
        models = json.load(response)
    assert any(row["id"] == "default_model" for row in models["data"])
    write(HERE / "models-at-run.json", models)
    results = HERE / "results.jsonl"
    done = {json.loads(line)["request_id"] for line in results.read_text().splitlines()} if results.exists() else set()
    for req in condition["requests"]:
        if req["request_id"] in done:
            continue
        image_path = HERE / req["image"]
        assert sha(image_path) == req["image_sha256"]
        payload = dict(condition["parameters"])
        payload["messages"] = [
            {"role": "system", "content": condition["system"]},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode()}},
                {"type": "text", "text": condition["question"]},
            ]},
        ]
        started = datetime.now(timezone.utc).isoformat()
        response, seconds = module.send_payload(ENDPOINT, payload)
        row = {"request_id": req["request_id"], "started_at": started,
               "completed_at": datetime.now(timezone.utc).isoformat(),
               "elapsed_seconds": seconds, "image_sha256": req["image_sha256"],
               "response": response}
        with results.open("a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps({"request_id": req["request_id"], "seconds": seconds,
                          "content": response["choices"][0]["message"].get("content", "")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    parser.parse_args()
    run()
