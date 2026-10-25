"""生成時の内部表現を、応答IDと位置上限を付けて保存する。

入力は採取器のtoken・数値と運用者が設定した保存先。推論や句の保存は担当しない。
要求本文から保存先を選ばず、完了時にmanifestを確定する。
"""
from __future__ import annotations

from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import threading


class CaptureStore:
    """一応答の保存状態を所有し、上限後も生成tokenの総数だけを追跡する。"""
    def __init__(self, root, response_id, backend, max_positions=256, model=None):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", response_id):
            raise ValueError("invalid response id")
        if max_positions is not None and (type(max_positions) is not int or not 1 <= max_positions <= 4096):
            raise ValueError("capture position limit must be 1..4096 or None for full capture")
        self.directory = Path(root) / response_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.hidden = (self.directory / "hidden.f32").open("xb")
        self.lock = threading.RLock()
        self.max_positions = max_positions
        self.layers = self.width = 0
        self.digest = hashlib.sha256()
        self.bytes = 0
        self.finished = False
        self.error = None
        self.record = {
            "schema": "dogido.internal_capture.v1", "response_id": response_id,
            "choice_index": 0, "backend": backend,
            "boundary": "decoder_layer_output_pre_final_norm", "dtype": "float32-le",
            "hidden_file": "hidden.f32", "model": model or {},
            "prompt_token_ids": [], "predicted_token_ids": [], "positions": [],
            "total_generated_tokens": 0, "rendered_text": "", "finish_reason": None,
            "max_positions": max_positions,
        }

    def configure(self, layers, width, model):
        with self.lock:
            if self.record["positions"] or self.finished:
                raise ValueError("capture already started")
            if type(layers) is not int or type(width) is not int or min(layers, width) < 1:
                raise ValueError("invalid hidden dimensions")
            self.layers, self.width = layers, width
            self.record["model"] = model

    def set_prompt(self, tokens):
        values = list(tokens)
        if not values or any(type(t) is not int or t < 0 for t in values):
            raise ValueError("invalid prompt tokens")
        with self.lock:
            if self.record["prompt_token_ids"] and self.record["prompt_token_ids"] != values:
                raise ValueError("prompt changed during capture")
            self.record["prompt_token_ids"] = values

    @property
    def wants_frame(self):
        return not self.finished and self.error is None and (
            self.max_positions is None or self.record["total_generated_tokens"] < self.max_positions)

    def append_token(self, token, frame_bytes=None, input_position=None):
        with self.lock:
            if self.finished:
                return
            index = self.record["total_generated_tokens"]
            self.record["total_generated_tokens"] += 1
            if self.error is not None or (self.max_positions is not None and index >= self.max_positions):
                return
            if type(token) is not int or token < 0:
                raise ValueError("invalid predicted token")
            expected_position = len(self.record["prompt_token_ids"]) + index - 1
            if input_position != expected_position or expected_position < 0:
                raise ValueError("predictive position does not match prompt and token index")
            if not self.layers or not self.width or frame_bytes is None:
                raise ValueError("missing native decoder frame")
            if len(frame_bytes) != self.layers * self.width * 4:
                raise ValueError("hidden byte count does not match dimensions")
            values = array("f")
            values.frombytes(frame_bytes)
            if sys.byteorder != "little":
                values.byteswap()
            if not all(math.isfinite(v) for v in values):
                raise ValueError("hidden frame contains non-finite values")
            self.hidden.write(frame_bytes)
            self.digest.update(frame_bytes)
            self.bytes += len(frame_bytes)
            self.record["predicted_token_ids"].append(token)
            self.record["positions"].append({"generation_index": index,
                "input_position": input_position, "predicted_token_id": token})

    def fail(self, reason):
        with self.lock:
            if self.error is None:
                self.error = str(reason)

    def finish(self, text="", finish_reason=None, status=None):
        with self.lock:
            if self.finished:
                return
            self.hidden.flush()
            os.fsync(self.hidden.fileno())
            self.hidden.close()
            count = len(self.record["positions"])
            if self.error is not None:
                status = "failed"
            elif status is None:
                status = "partial" if self.record["total_generated_tokens"] > count else "complete"
            if status not in {"complete", "partial", "failed", "cancelled"}:
                raise ValueError("invalid capture status")
            self.record.update(status=status, shape=[count, self.layers, self.width],
                hidden_bytes=self.bytes, hidden_sha256=self.digest.hexdigest(),
                rendered_text=text, finish_reason=finish_reason)
            if self.error is not None:
                self.record["error"] = self.error
            temporary = self.directory / "manifest.json.tmp"
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(self.record, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.directory / "manifest.json")
            self.finished = True
