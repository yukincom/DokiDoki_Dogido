#!/usr/bin/env python3
"""承認済みの画像・質問で解像度を比較する独立診断。ゲーム・会話状態は変更しない。

prepareは画像コピーと固定条件を保存、runは一件ずつlocalhostへ送信する。
cleanupは最終利用から24時間経過し、manifestの画像hashが一致するときだけ削除する。
画像本体・base64は結果JSONへ保存しない。原本は読取り専用。
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.request


def now():
    return datetime.now(timezone.utc)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def prepare(args, root):
    from PIL import Image, ImageOps, __version__ as pillow_version
    root.mkdir(parents=True, exist_ok=False)
    (root / "images").mkdir()
    base = json.loads(Path(args.prompts).read_text())["base"]
    manifest = {
        "schema_version": 1, "created_at": now().isoformat(),
        "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
        "system": base,
        "question": "これはマインクラフトのスクリーンショットや。この画像で何が見える？ 見えるものと位置関係を短く教えて。分からんところは分からんと言ってな。",
        "parameters": {"model": "default_model", "temperature": 0, "seed": 42,
                       "max_tokens": 400, "stream": False, "cache_prompt": False,
                       "chat_template_kwargs": {"enable_thinking": False}},
        "resampling": "Pillow LANCZOS, RGB PNG, preserve aspect ratio, no upscale",
        "pillow_version": pillow_version, "history": "none; independent requests",
        "sources": [], "images": [], "last_used_at": None,
        "expires_at": (now() + timedelta(hours=24)).isoformat(),
    }
    for n, source in enumerate(args.sources, 1):
        source = Path(source).resolve()
        image = ImageOps.exif_transpose(Image.open(source)).convert("RGB")
        source_id = f"image_{n}"
        manifest["sources"].append({"id": source_id, "path": str(source),
                                    "sha256": digest(source), "size": list(image.size)})
        for edge in [0, 256, 192, 128, 96, 64]:
            label = "native" if edge == 0 else str(edge)
            copy = image.copy()
            if edge:
                copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            target = root / "images" / f"{source_id}_{label}.png"
            copy.save(target)
            manifest["images"].append({"id": f"{source_id}_{label}", "source_id": source_id,
                "edge_label": label, "path": str(target.relative_to(root)),
                "size": list(copy.size), "bytes": target.stat().st_size, "sha256": digest(target)})
    save(root / "manifest.json", manifest)
    print(json.dumps({"prepared": len(manifest["images"]), "output": str(root)}))


def send_payload(endpoint, payload):
    """画像診断で共用する一回の送信。再試行・会話履歴・ゲーム状態変更は行わない。"""
    request = urllib.request.Request(endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    begin = time.monotonic()
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    return result, round(time.monotonic() - begin, 3)


def run(args, root):
    manifest = json.loads((root / "manifest.json").read_text())
    condition = "human_question" if args.human_question else ("with_biome" if args.with_biome else "image_only")
    suffix = "-human-question" if args.human_question else ("-with-biome" if args.with_biome else "")
    result_path = root / ("results" + suffix + ".jsonl")
    biomes = {"image_1": "暖かい海", "image_2": "砂漠", "image_3": "砂漠"}
    done = {json.loads(line)["image_id"] for line in result_path.read_text().splitlines()} if result_path.exists() else set()
    count = 0
    for label in args.labels:
        for item in manifest["images"]:
            if item["edge_label"] != label or item["id"] in done:
                continue
            path = root / item["path"]
            assert digest(path) == item["sha256"]
            payload = dict(manifest["parameters"])
            question = manifest["question"]
            if args.human_question:
                question = "これはマインクラフトのスクリーンショットや。これはどう？ちょっと複雑だけど、人間をはんべつできるだろうか？"
            if args.with_biome:
                question += "\nこの場所のバイオームは「" + biomes[item["source_id"]] + "」や。"
            payload["messages"] = [
                {"role": "system", "content": manifest["system"]},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()}},
                    {"type": "text", "text": question}]}]
            # 失敗しても画像を送った可能性があるので、要求直前に保持期限を更新する。
            started = now()
            manifest["last_used_at"] = started.isoformat()
            manifest["expires_at"] = (started + timedelta(hours=24)).isoformat()
            save(root / "manifest.json", manifest)
            result, elapsed = send_payload(manifest["endpoint"], payload)
            ended = now()
            row = {"image_id": item["id"], "size": item["size"],
                   "condition": condition, "question": question,
                   "biome": biomes[item["source_id"]] if args.with_biome else None,
                   "started_at": started.isoformat(), "completed_at": ended.isoformat(),
                   "elapsed_seconds": elapsed,
                   "model": result.get("model"), "usage": result.get("usage"),
                   "timings": result.get("timings"), "id": result.get("id"),
                   "finish_reason": result["choices"][0].get("finish_reason"),
                   "content": result["choices"][0]["message"].get("content", "")}
            with result_path.open("a") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            manifest["last_used_at"] = ended.isoformat()
            manifest["expires_at"] = (ended + timedelta(hours=24)).isoformat()
            save(root / "manifest.json", manifest)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            count += 1
            if args.limit and count >= args.limit:
                return


def cleanup(args, root):
    manifest = json.loads((root / "manifest.json").read_text())
    if now() < datetime.fromisoformat(manifest["expires_at"]):
        print(json.dumps({"status": "not_due", "expires_at": manifest["expires_at"]}))
        return
    assert not (root / "images").is_symlink()
    candidates = []
    for item in manifest["images"]:
        path = root / item["path"]
        assert not path.is_symlink() and path.resolve().parent == (root / "images").resolve()
        if path.exists():
            assert digest(path) == item["sha256"], f"Changed image: {item['id']}"
            candidates.append(path)
    # 全対象の再照合を先に済ませ、原本・結果・他の画像には触れない。
    if args.delete:
        for path in candidates:
            path.unlink()
        manifest["deleted_at"] = now().isoformat()
        save(root / "manifest.json", manifest)
    print(json.dumps({"status": "deleted" if args.delete else "dry_run", "count": len(candidates)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "run", "cleanup"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--prompts")
    parser.add_argument("--sources", nargs="*")
    parser.add_argument("--labels", nargs="*", default=["native", "256", "128", "64"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--with-biome", action="store_true")
    parser.add_argument("--human-question", action="store_true")
    args = parser.parse_args()
    if args.with_biome and args.human_question:
        parser.error("バイオーム条件と人物質問条件は別々に実行してください")
    root = Path(args.output).resolve()
    {"prepare": prepare, "run": run, "cleanup": cleanup}[args.mode](args, root)


if __name__ == "__main__":
    main()
