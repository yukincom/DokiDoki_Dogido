"""重み読込前の検証と入力上限を、モデル依存なしで検査する。精度検証は別途行う。"""
import hashlib
import json
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import request_classifier_worker as worker


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkpoint = self.root / "weights"
        self.checkpoint.mkdir()
        self.mode = "normal"
        self.config = {"model_type": "modernbert", "max_position_embeddings": 8192,
                       "id2label": {str(i): s for i, s in enumerate(worker.NORMAL_LABELS)},
                       "label2id": {s: i for i, s in enumerate(worker.NORMAL_LABELS)}}
        for name in worker.REQUIRED_FILES:
            (self.checkpoint / name).write_text(json.dumps(self.config) if name == "config.json" else "fixture")
        self.spec = {"schema_version": 1, "path_base": "repository_root", "checkpoint_path": "weights",
                     "classifier_mode": self.mode, "label_to_id": self.config["label2id"],
                     "evaluated_input": {"max_history_utterances": 0, "max_length": 128, "truncation": False},
                     "checkpoint_files_sha256": {name: worker.digest(self.checkpoint / name) for name in worker.REQUIRED_FILES},
                     "extra_selection_metadata": {"ignored": True}}

    def frame(self):
        path = self.root / "adopted-model.json"
        raw = json.dumps(self.spec).encode()
        path.write_bytes(raw)
        return {"manifest_path": str(path), "model_root": str(self.root), "mode": self.mode,
                "manifest_sha256": hashlib.sha256(raw).hexdigest()}

    def select_mode(self, mode):
        labels = worker.LABELS_BY_MODE[mode]
        self.mode = mode
        self.spec["classifier_mode"] = mode
        self.spec["label_to_id"] = {label: i for i, label in enumerate(labels)}
        self.config["id2label"] = {str(i): label for i, label in enumerate(labels)}
        self.config["label2id"] = dict(self.spec["label_to_id"])
        (self.checkpoint / "config.json").write_text(json.dumps(self.config))
        self.spec["checkpoint_files_sha256"]["config.json"] = worker.digest(self.checkpoint / "config.json")

    def test_declared_limits_preserved_for_both_dedicated_models(self):
        for mode in worker.LABELS_BY_MODE:
            self.select_mode(mode)
            for size in (128, 256):
                with self.subTest(mode=mode, size=size):
                    self.spec["evaluated_input"]["max_length"] = size
                    checkpoint, spec, limits = worker.read_spec(self.frame())
                    self.assertEqual(checkpoint, self.checkpoint.resolve())
                    self.assertEqual(spec["classifier_mode"], mode)
                    self.assertEqual(limits["max_length"], size)

    def test_unknown_or_missing_mode_is_rejected_without_defaulting(self):
        for mode in (None, "", "other", []):
            with self.subTest(mode=mode):
                frame = {**self.frame(), "mode": mode}
                with self.assertRaisesRegex(worker.ContractError, "invalid classifier mode"):
                    worker.read_spec(frame)
        frame = self.frame()
        del frame["mode"]
        with self.assertRaisesRegex(worker.ContractError, "invalid classifier mode"):
            worker.read_spec(frame)

    def test_wrong_or_missing_manifest_mode_is_rejected(self):
        for mode in worker.LABELS_BY_MODE:
            self.select_mode(mode)
            self.spec["classifier_mode"] = "senryu" if mode == "normal" else "normal"
            with self.assertRaisesRegex(worker.ContractError, "manifest classifier mode mismatch"):
                worker.read_spec(self.frame())
            del self.spec["classifier_mode"]
            with self.assertRaisesRegex(worker.ContractError, "manifest classifier mode mismatch"):
                worker.read_spec(self.frame())

    def test_manifest_label_order_must_match_selected_mode(self):
        for mode, labels in worker.LABELS_BY_MODE.items():
            self.select_mode(mode)
            self.spec["label_to_id"] = {label: i for i, label in enumerate(reversed(labels))}
            with self.assertRaisesRegex(worker.ContractError, "manifest label mapping mismatch"):
                worker.read_spec(self.frame())
            del self.spec["label_to_id"]
            with self.assertRaisesRegex(worker.ContractError, "manifest label mapping mismatch"):
                worker.read_spec(self.frame())

    def test_legacy_23_label_checkpoint_is_not_a_dedicated_model(self):
        old_labels = [
            "not_target", "enemy_information", "equip_sword", "recall_mount", "dogido_recall",
            "senryu_line_upper", "senryu_line_middle", "senryu_line_lower",
            "senryu_change_upper", "senryu_change_middle", "senryu_change_lower",
            "look_direction", "senryu_end", "look_relative_right", "look_target", "look_player",
            "look_relative_front", "look_relative_back", "look_relative_left",
            "look_relative_front_left", "look_relative_front_right", "look_relative_back_left", "look_relative_back_right",
        ]
        for mode in worker.LABELS_BY_MODE:
            self.select_mode(mode)
            config = {**self.config,
                      "id2label": {str(i): label for i, label in enumerate(old_labels)},
                      "label2id": {label: i for i, label in enumerate(old_labels)}}
            (self.checkpoint / "config.json").write_text(json.dumps(config))
            self.spec["checkpoint_files_sha256"]["config.json"] = worker.digest(self.checkpoint / "config.json")
            with self.assertRaisesRegex(worker.ContractError, "model label mapping mismatch"):
                worker.read_spec(self.frame())

    def test_missing_or_corrupt_weight_hash_rejected(self):
        del self.spec["checkpoint_files_sha256"]["model.safetensors"]
        with self.assertRaisesRegex(worker.ContractError, "missing checkpoint hashes"):
            worker.read_spec(self.frame())
        self.spec["checkpoint_files_sha256"]["model.safetensors"] = "0" * 64
        with self.assertRaisesRegex(worker.ContractError, "hash mismatch"):
            worker.read_spec(self.frame())

    def test_lfs_pointer_reports_required_weight_download(self):
        (self.checkpoint / "model.safetensors").write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 123\n")
        with self.assertRaisesRegex(worker.ContractError, "git lfs pull"):
            worker.read_spec(self.frame())

    def test_manifest_changed_after_rust_read_rejected(self):
        frame = self.frame()
        Path(frame["manifest_path"]).write_text("{}")
        with self.assertRaisesRegex(worker.ContractError, "manifest hash mismatch"):
            worker.read_spec(frame)

    def test_label_order_and_model_family_rejected(self):
        for mode, labels in worker.LABELS_BY_MODE.items():
            self.select_mode(mode)
            for key, value in (("id2label", {str(i): s for i, s in enumerate(reversed(labels))}),
                               ("label2id", {s: i for i, s in enumerate(reversed(labels))}),
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

    def test_load_and_classify_keep_mode_identity_and_dedicated_probabilities(self):
        # 数値ライブラリだけを模擬し、採用記録・hash検査とworkerの実際の配線を通す。
        for mode, labels in worker.LABELS_BY_MODE.items():
            with self.subTest(mode=mode):
                self.select_mode(mode)
                model = mock.Mock()
                model.to.return_value = model
                model.eval.return_value = model
                model.config = SimpleNamespace(id2label=dict(enumerate(labels)),
                                               label2id={label: i for i, label in enumerate(labels)})
                model.return_value = SimpleNamespace(logits=["logits"])
                tokenizer = mock.Mock(return_value={"input_ids": SimpleNamespace(shape=(1, 20))})
                probabilities = [float(i == len(labels) - 1) for i in range(len(labels))]
                torch = SimpleNamespace(
                    set_num_threads=mock.Mock(), set_num_interop_threads=mock.Mock(), float32="float32",
                    inference_mode=contextlib.nullcontext,
                    softmax=mock.Mock(return_value=SimpleNamespace(tolist=lambda: probabilities)),
                )
                transformers = SimpleNamespace(
                    AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=mock.Mock(return_value=model)),
                    AutoTokenizer=SimpleNamespace(from_pretrained=mock.Mock(return_value=tokenizer)),
                )
                instance = worker.Worker()
                frame = {**self.frame(), "schema_version": 1, "request_id": "load", "op": "load"}
                with mock.patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), mock.patch.dict(os.environ):
                    loaded = instance.handle(frame)
                    self.assertTrue(loaded["ready"])
                    self.assertEqual(loaded["mode"], mode)
                    self.assertEqual(loaded["labels"], labels)
                    for operation in ("classify-first", "classify-second"):
                        result = instance.handle({**frame, "op": "classify", "request_id": operation, "text": "原文"})
                        self.assertEqual(result["request_id"], operation)
                        self.assertEqual(result["manifest_sha256"], frame["manifest_sha256"])
                        self.assertEqual(result["mode"], mode)
                        self.assertEqual(result["prediction"]["label"], labels[-1])
                        self.assertEqual(result["prediction"]["probabilities"], probabilities)
                    self.assertEqual(model.call_count, 2)
                    self.assertEqual(transformers.AutoModelForSequenceClassification.from_pretrained.call_count, 1)
                    self.assertEqual(tokenizer.call_args.kwargs, {"return_tensors": "pt", "truncation": False})


class ProtocolTests(unittest.TestCase):
    def test_unloaded_model_is_not_a_prediction(self):
        with self.assertRaisesRegex(worker.ContractError, "not loaded"):
            worker.Worker().handle({"schema_version": 1, "request_id": "test", "manifest_sha256": "x", "mode": "normal", "op": "classify"})

    def test_mode_change_after_load_never_reaches_tokenization_or_inference(self):
        for mode in worker.LABELS_BY_MODE:
            instance = worker.Worker()
            instance.model = object()
            instance.mode = mode
            instance.manifest_sha256 = "x"
            instance.tokenizer = mock.Mock(side_effect=AssertionError("must not tokenize another mode"))
            other = "senryu" if mode == "normal" else "normal"
            with self.assertRaisesRegex(worker.ContractError, "classifier mode mismatch"):
                instance.handle({"schema_version": 1, "request_id": "test", "manifest_sha256": "x",
                                 "mode": other, "op": "classify", "text": "原文"})
            instance.tokenizer.assert_not_called()

    def test_token_overflow_prevents_model_forward(self):
        instance = worker.Worker()
        instance.model = object()  # 呼べば失敗する。上限検証が先に止めることを確認。
        instance.mode = "normal"
        instance.manifest_sha256 = "x"
        instance.limits = {"max_length": 256}
        class Tensor:
            shape = (1, 257)
        def tokenize(text, **kwargs):
            self.assertFalse(kwargs["truncation"])
            return {"input_ids": Tensor()}
        instance.tokenizer = tokenize
        with self.assertRaisesRegex(worker.ContractError, "token limit"):
            instance.handle({"schema_version": 1, "request_id": "test", "manifest_sha256": "x", "mode": "normal", "op": "classify", "text": "原文"})

    def test_bad_json_and_oversized_frames_close_stdio(self):
        for data in (b"not json\n", b"[1]\n", b" " * worker.FRAME_LIMIT + b"\n"):
            result = subprocess.run([sys.executable, str(Path(worker.__file__))], input=data, capture_output=True, timeout=5, check=True)
            lines = result.stdout.splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("error", json.loads(lines[0]))


if __name__ == "__main__":
    unittest.main()
