#!/usr/bin/env python3
"""既存モブ資料の候補選択を、名前だけ条件と対にして調べる独立診断。

見込み: 外見タグを含む既存資料が、画像の対象を候補へ対応づける手掛かりになる可能性。
合格: 正しい位置・画像の特徴と種類が対応し、陰性対照やプレイヤーを敵へ読み替えない。
範囲: 固定した保存画像とlocalhost実モデルのみ。本体の観測・戦闘・設定は変更しない。
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
REPO = HERE.parents[3]
ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def payload_for(conditions, request):
    """候補は全画像で固定。画像の正解・撮影metadata・ファイル名は送信しない。"""
    path = HERE / request["image"]
    assert sha(path) == request["image_sha256"]
    catalog = conditions["variants"][request["variant"]]
    prompt = conditions["question"] + "\n\n【対象のカタログ情報（一般的特徴・表現材料）】\n" + json.dumps(catalog, ensure_ascii=False)
    payload = dict(conditions["parameters"])
    payload["messages"] = [
        {"role": "system", "content": conditions["system"]},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()}},
            {"type": "text", "text": prompt},
        ]},
    ]
    return payload


def main(run):
    conditions = json.loads((HERE / "conditions.json").read_text())
    assert conditions["endpoint"] == ENDPOINT
    previews = []
    for request in conditions["requests"]:
        payload = payload_for(conditions, request)
        # 同じpayloadを再構成する画像hashを記録し、大きなbase64の重複保存を避ける。
        payload["messages"][1]["content"][0] = {"type": "image_reference", "path": request["image"], "sha256": request["image_sha256"]}
        previews.append({"request_id": request["request_id"], "payload_except_image_bytes": payload})
    save(HERE / "request-previews.json", previews)
    if not run:
        print(f"Prepared {len(previews)} payload previews; no network requests.")
        return
    sender = REPO / conditions["sender_source"]
    assert sha(sender) == conditions["sender_sha256"]
    spec = importlib.util.spec_from_file_location("existing_vision_sender", sender)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=10) as response:
        models = json.load(response)
    assert any(item["id"] == "default_model" for item in models["data"])
    save(HERE / "models-at-run.json", models)
    result_path = HERE / "results.jsonl"
    done = {json.loads(line)["request_id"] for line in result_path.read_text().splitlines()} if result_path.exists() else set()
    for request in conditions["requests"]:
        if request["request_id"] in done:
            continue
        payload = payload_for(conditions, request)
        started = datetime.now(timezone.utc).isoformat()
        response, elapsed = module.send_payload(ENDPOINT, payload)
        row = {"request_id": request["request_id"], "variant": request["variant"],
               "image_sha256": request["image_sha256"], "started_at": started,
               "completed_at": datetime.now(timezone.utc).isoformat(),
               "elapsed_seconds": elapsed, "response": response}
        with result_path.open("a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps({"request_id": request["request_id"], "seconds": elapsed,
                          "content": response["choices"][0]["message"].get("content", "")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preview", "run"])
    args = parser.parse_args()
    main(args.mode == "run")
