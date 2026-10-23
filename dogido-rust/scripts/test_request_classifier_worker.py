"""重み読込前の検証と入力上限を、モデル依存なしで検査する。精度検証は別途行う。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import request_classifier_worker as worker


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkpoint = self.root / "weights"
        self.checkpoint.mkdir()
        self.config = {"model_type": "modernbert", "max_position_embeddings": 8192,
                       "id2label": {str(i): s for i, s in enumerate(worker.LABELS)},
                       "label2id": {s: i for i, s in enumerate(worker.LABELS)}}
        for name in worker.REQUIRED_FILES:
            (self.checkpoint / name).write_text(json.dumps(self.config) if name == "config.json" else "fixture")
        self.spec = {"schema_version": 1, "path_base": "repository_root", "checkpoint_path": "weights",
                     "evaluated_input": {"max_history_utterances": 0, "max_length": 128, "truncation": False},
                     "checkpoint_files_sha256": {name: worker.digest(self.checkpoint / name) for name in worker.REQUIRED_FILES},
                     "extra_selection_metadata": {"ignored": True}}

    def frame(self):
        path = self.root / "adopted-model.json"
        raw = json.dumps(self.spec).encode()
        path.write_bytes(raw)
        return {"manifest_path": str(path), "model_root": str(self.root),
                "manifest_sha256": hashlib.sha256(raw).hexdigest()}

    def test_declared_limits_preserved_for_23_class_model(self):
        for size in (128, 256):
            self.spec["evaluated_input"]["max_length"] = size
            checkpoint, _, limits = worker.read_spec(self.frame())
            self.assertEqual(checkpoint, self.checkpoint.resolve())
            self.assertEqual(limits["max_length"], size)

    def test_missing_or_corrupt_weight_hash_rejected(self):
        del self.spec["checkpoint_files_sha256"]["model.safetensors"]
        with self.assertRaisesRegex(worker.ContractError, "missing checkpoint hashes"):
            worker.read_spec(self.frame())
        self.spec["checkpoint_files_sha256"]["model.safetensors"] = "0" * 64
        with self.assertRaisesRegex(worker.ContractError, "hash mismatch"):
            worker.read_spec(self.frame())

    def test_manifest_changed_after_rust_read_rejected(self):
        frame = self.frame()
        Path(frame["manifest_path"]).write_text("{}")
        with self.assertRaisesRegex(worker.ContractError, "manifest hash mismatch"):
            worker.read_spec(frame)

    def test_label_order_and_model_family_rejected(self):
        for key, value in (("id2label", {str(i): s for i, s in enumerate(reversed(worker.LABELS))}),
                           ("model_type", "bert")):
            config = {**self.config, key: value}
            (self.checkpoint / "config.json").write_text(json.dumps(config))
            self.spec["checkpoint_files_sha256"]["config.json"] = worker.digest(self.checkpoint / "config.json")
            with self.assertRaises(worker.ContractError):
                worker.read_spec(self.frame())

    def test_truncation_and_excessive_limits_rejected(self):
        for limits in ({"truncation": True}, {"max_length": 9000}, {"max_history_utterances": 11}):
            original = dict(self.spec["evaluated_input"])
            self.spec["evaluated_input"].update(limits)
            with self.assertRaises(worker.ContractError):
                worker.read_spec(self.frame())
            self.spec["evaluated_input"] = original

    def test_checkpoint_escape_rejected(self):
        self.spec["checkpoint_path"] = ".."
        with self.assertRaisesRegex(worker.ContractError, "outside model root"):
            worker.read_spec(self.frame())

    def test_file_escape_rejected(self):
        self.spec["checkpoint_files_sha256"]["../secret"] = "0" * 64
        with self.assertRaisesRegex(worker.ContractError, "invalid checkpoint filename"):
            worker.read_spec(self.frame())


class ProtocolTests(unittest.TestCase):
    def test_unloaded_model_is_not_a_prediction(self):
        with self.assertRaisesRegex(worker.ContractError, "not loaded"):
            worker.Worker().handle({"schema_version": 1, "request_id": "test", "manifest_sha256": "x", "op": "classify"})

    def test_token_overflow_prevents_model_forward(self):
        instance = worker.Worker()
        instance.model = object()  # 呼べば失敗する。上限検証が先に止めることを確認。
        instance.manifest_sha256 = "x"
        instance.limits = {"max_length": 256}
        class Tensor:
            shape = (1, 257)
        def tokenize(text, **kwargs):
            self.assertFalse(kwargs["truncation"])
            return {"input_ids": Tensor()}
        instance.tokenizer = tokenize
        with self.assertRaisesRegex(worker.ContractError, "token limit"):
            instance.handle({"schema_version": 1, "request_id": "test", "manifest_sha256": "x", "op": "classify", "text": "原文"})

    def test_bad_json_and_oversized_frames_close_stdio(self):
        for data in (b"not json\n", b"[1]\n", b" " * worker.FRAME_LIMIT + b"\n"):
            result = subprocess.run([sys.executable, str(Path(worker.__file__))], input=data, capture_output=True, timeout=5, check=True)
            lines = result.stdout.splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("error", json.loads(lines[0]))


if __name__ == "__main__":
    unittest.main()
