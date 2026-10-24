"""採用記録とローカル重みを検証し、ModernBERTの数値推論だけを行うstdio補助。

入力の整形・モード選択・結果の用途と取消はRust所有。stdoutはJSONL応答専用。
一つのworkerは通常16分類または川柳8分類の一方だけを読み込み、途中で切り替えない。
推論依存は本体のrequest-classifier extraで準備し、モデルや依存を自動取得しない。
"""
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

NORMAL_LABELS = [
    "not_target", "enemy_information", "equip_sword", "recall_mount", "dogido_recall",
    "look_direction", "look_relative_right", "look_target", "look_player",
    "look_relative_front", "look_relative_back", "look_relative_left",
    "look_relative_front_left", "look_relative_front_right",
    "look_relative_back_left", "look_relative_back_right",
]
SENRYU_LABELS = [
    "not_target", "senryu_line_upper", "senryu_line_middle", "senryu_line_lower",
    "senryu_change_upper", "senryu_change_middle", "senryu_change_lower", "senryu_end",
]
LABELS_BY_MODE = {"normal": NORMAL_LABELS, "senryu": SENRYU_LABELS}
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


def labels_for_mode(mode):
    """受付が指定した一方の契約だけを返す。不明なmodeを通常側へ読み替えない。"""
    require(isinstance(mode, str) and mode in LABELS_BY_MODE, "invalid classifier mode")
    return LABELS_BY_MODE[mode]


def read_spec(frame):
    """指定modeの採用記録・ラベル順・評価上限・全hashを照合して読込先を返す。

    checkpointはモデル保管先のrepository_root基準。別modeや旧23分類の重みを
    読み込む前に止めるため、呼出側と採用記録とconfigの対応をここで確認する。
    """
    labels = labels_for_mode(frame.get("mode"))
    path = Path(frame["manifest_path"]).resolve(strict=True)
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == frame["manifest_sha256"], "manifest hash mismatch")
    spec = json.loads(raw)
    require(spec["schema_version"] == 1 and spec["path_base"] == "repository_root", "unsupported manifest")
    require(spec.get("classifier_mode") == frame["mode"], "manifest classifier mode mismatch")
    require(spec.get("label_to_id") == {label: i for i, label in enumerate(labels)},
            "manifest label mapping mismatch")
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
    require(config["id2label"] == {str(i): label for i, label in enumerate(labels)}
            and config["label2id"] == {label: i for i, label in enumerate(labels)}, "model label mapping mismatch")
    require(limits["max_length"] <= config["max_position_embeddings"], "token limit exceeds model")
    return checkpoint, spec, {key: limits[key] for key in ("max_history_utterances", "max_length", "truncation")}


def verify_files(checkpoint, hashes):
    for name, expected in hashes.items():
        require(Path(name).name == name and name not in (".", ".."), "invalid checkpoint filename")
        path = (checkpoint / name).resolve(strict=True)
        require(path.is_relative_to(checkpoint), "checkpoint file outside directory")
        if name == "model.safetensors":
            with path.open("rb") as stream:
                require(not stream.read(128).startswith(b"version https://git-lfs.github.com/spec/v1"),
                        "model is a Git LFS pointer; run git lfs pull")
        require(digest(path) == expected, f"checkpoint hash mismatch: {name}")


class Worker:
    """一方のmodeのCPUモデルを保持し、同じmodeの一件だけを分類する。

    modeの切替・二つのworkerを合わせた排他制御はRustの共通受付が担当する。
    確率の最大値を返し、対象外や失敗をもう一方へ転送しない。
    """
    def __init__(self):
        self.model = None

    def handle(self, frame):
        require(frame.get("schema_version") == 1, "unsupported protocol")
        require(isinstance(frame.get("request_id"), str) and frame["request_id"], "missing request id")
        labels = labels_for_mode(frame.get("mode"))
        identity = {key: frame[key] for key in ("schema_version", "request_id", "manifest_sha256", "mode")}
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
            require(model.config.id2label == dict(enumerate(labels))
                    and model.config.label2id == {label: i for i, label in enumerate(labels)},
                    "loaded label mapping mismatch")
            self.torch, self.tokenizer, self.model = torch, tokenizer, model
            self.manifest_sha256, self.limits, self.mode = frame["manifest_sha256"], limits, frame["mode"]
            return {**identity, "ready": True, "labels": labels, "limits": limits}
        require(frame["op"] == "classify" and self.model is not None, "classifier is not loaded or unknown operation")
        require(frame["mode"] == self.mode, "classifier mode mismatch")
        require(frame["manifest_sha256"] == self.manifest_sha256, "model identity mismatch")
        require(isinstance(frame["text"], str) and frame["text"].strip(), "empty model input")
        encoded = self.tokenizer(frame["text"], return_tensors="pt", truncation=False)
        count = encoded["input_ids"].shape[1]
        require(0 < count <= self.limits["max_length"], "input exceeds evaluated token limit")
        started = time.perf_counter()
        with self.torch.inference_mode():
            logits = self.model(**encoded).logits[0]
            probabilities = self.torch.softmax(logits, dim=-1).tolist()
        require(len(probabilities) == len(labels) and all(math.isfinite(p) for p in probabilities), "invalid model probabilities")
        best = max(range(len(labels)), key=probabilities.__getitem__)
        return {**identity, "prediction": {"label": labels[best], "probabilities": probabilities,
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
