"""採用記録とローカル重みを検証し、ModernBERTの数値推論だけを行うstdio補助。

入力の整形・履歴選択・結果の用途と取消はRust所有。stdoutはJSONL応答専用。
依存環境は学習用requirements-train.txtを利用し、モデルや依存を自動取得しない。
"""
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

LABELS = ["not_request", "enemy_information", "equip_sword", "recall_mount", "other_request"]
REQUIRED_FILES = {"config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json"}
FRAME_LIMIT = 1_000_000


class ContractError(ValueError):
    """入力原文や端末パスを含まない、固定の契約違反メッセージ。"""


def require(ok, message):
    if not ok:
        raise ContractError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_spec(frame):
    """モデル保管先のrepository_rootを基準に解決し、評価上限と全指定hashを検査する。"""
    path = Path(frame["manifest_path"]).resolve(strict=True)
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == frame["manifest_sha256"], "manifest hash mismatch")
    spec = json.loads(raw)
    require(spec["schema_version"] == 1 and spec["path_base"] == "repository_root", "unsupported manifest")
    root = Path(frame["model_root"]).resolve(strict=True)
    relative = Path(spec["checkpoint_path"])
    require(not relative.is_absolute(), "checkpoint_path must be relative")
    checkpoint = (root / relative).resolve(strict=True)
    require(checkpoint.is_dir() and checkpoint.is_relative_to(root), "checkpoint outside model root")
    limits = spec["evaluated_input"]
    require(type(limits["max_length"]) is int and 0 < limits["max_length"] <= 8192, "invalid token limit")
    require(type(limits["max_history_utterances"]) is int and 0 <= limits["max_history_utterances"] <= 10,
            "invalid history limit")
    require(limits["truncation"] is False, "truncation is unsupported")
    require(REQUIRED_FILES <= spec["checkpoint_files_sha256"].keys(), "missing checkpoint hashes")
    verify_files(checkpoint, spec["checkpoint_files_sha256"])
    config = json.loads((checkpoint / "config.json").read_text())
    require(config["model_type"] == "modernbert", "model must be ModernBERT")
    require(config["id2label"] == {str(i): label for i, label in enumerate(LABELS)}
            and config["label2id"] == {label: i for i, label in enumerate(LABELS)}, "model label mapping mismatch")
    require(limits["max_length"] <= config["max_position_embeddings"], "token limit exceeds model")
    return checkpoint, spec, {key: limits[key] for key in ("max_history_utterances", "max_length", "truncation")}


def verify_files(checkpoint, hashes):
    for name, expected in hashes.items():
        require(Path(name).name == name and name not in (".", ".."), "invalid checkpoint filename")
        path = (checkpoint / name).resolve(strict=True)
        require(path.is_relative_to(checkpoint), "checkpoint file outside directory")
        require(digest(path) == expected, f"checkpoint hash mismatch: {name}")


class Worker:
    """一つの検証済みCPUモデルを保持する。分類は確率の最大値をそのまま返す。"""
    def __init__(self):
        self.model = None

    def handle(self, frame):
        require(frame.get("schema_version") == 1, "unsupported protocol")
        require(isinstance(frame.get("request_id"), str) and frame["request_id"], "missing request id")
        identity = {key: frame[key] for key in ("schema_version", "request_id", "manifest_sha256")}
        if frame["op"] == "load":
            require(self.model is None, "model is already loaded")
            checkpoint, spec, limits = read_spec(frame)
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            torch.set_num_threads(4)
            torch.set_num_interop_threads(1)
            tokenizer = AutoTokenizer.from_pretrained(checkpoint, local_files_only=True, trust_remote_code=False)
            model = AutoModelForSequenceClassification.from_pretrained(
                checkpoint, local_files_only=True, trust_remote_code=False,
                use_safetensors=True, dtype=torch.float32, attn_implementation="sdpa",
            ).to("cpu").eval()
            verify_files(checkpoint, spec["checkpoint_files_sha256"])
            require(model.config.id2label == dict(enumerate(LABELS)), "loaded label mapping mismatch")
            self.torch, self.tokenizer, self.model = torch, tokenizer, model
            self.manifest_sha256, self.limits = frame["manifest_sha256"], limits
            return {**identity, "ready": True, "labels": LABELS, "limits": limits}
        require(frame["op"] == "classify" and self.model is not None, "classifier is not loaded or unknown operation")
        require(frame["manifest_sha256"] == self.manifest_sha256, "model identity mismatch")
        require(isinstance(frame["text"], str) and frame["text"].strip(), "empty model input")
        encoded = self.tokenizer(frame["text"], return_tensors="pt", truncation=False)
        count = encoded["input_ids"].shape[1]
        require(0 < count <= self.limits["max_length"], "input exceeds evaluated token limit")
        started = time.perf_counter()
        with self.torch.inference_mode():
            logits = self.model(**encoded).logits[0]
            probabilities = self.torch.softmax(logits, dim=-1).tolist()
        require(len(probabilities) == len(LABELS) and all(math.isfinite(p) for p in probabilities), "invalid model probabilities")
        best = max(range(len(LABELS)), key=probabilities.__getitem__)
        return {**identity, "prediction": {"label": LABELS[best], "probabilities": probabilities,
                "token_count": count, "inference_ms": (time.perf_counter() - started) * 1000}}


def main():
    worker = Worker()
    while True:
        line = sys.stdin.buffer.readline(FRAME_LIMIT + 1)
        if not line:
            return
        try:
            require(len(line) <= FRAME_LIMIT and line.endswith(b"\n"), "invalid or oversized frame")
            frame = json.loads(line)
            require(isinstance(frame, dict), "frame must be an object")
            with contextlib.redirect_stdout(sys.stderr):
                reply = worker.handle(frame)
            print(json.dumps(reply, ensure_ascii=False, allow_nan=False), flush=True)
        except Exception as error:
            # エラー本文や入力原文・ローカルパスを応答ログへ流さない。
            message = str(error) if isinstance(error, ContractError) else type(error).__name__
            print(json.dumps({"error": message}), flush=True)
            return


if __name__ == "__main__":
    main()
