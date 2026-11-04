"""見込み: 同じ教材と選択記録に結び付ければ、評価の再実行で学習成果を変えない。
合格: 改変・出力先混用を拒否。小型CPU実モデルで学習→評価→再評価→ZIPが成立する。
範囲: Colab接続とT4、公開baseの精度、実ASR・Minecraftは含まない。
"""
import importlib.util
import json
import os
import sys
import types
from unittest.mock import patch
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "apps/dogido-training-builder"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load("test_span_colab_runtime", SOURCE / "span_train_colab.py")
controls = load("test_span_colab_controls", SOURCE / "span_notebook_controls.py")


def make_row(identifier, text, before="", after=""):
    spans = []
    for role, word in (("from", before), ("to", after)):
        if word:
            start = text.index(word)
            spans.append({"role": role, "start": start, "end": start + len(word), "text": word})
    return {"id": identifier, "text": text, "current_verse": "丘の上\n風が吹く\n遠い山", "spans": spans,
            "group_id": identifier, "form": "real"}


def materialize(root):
    (root / "shared").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "sources").mkdir()
    for name in ("train.py", "span_model.py", "early_stopping.py", "download_base.py"):
        shutil.copyfile(REPO / "dev_tools/edit_span_extractor" / name, root / "shared" / name)
    rows = {
        "train": [make_row("a", "春の空を秋の空に変えて", "春の空", "秋の空"),
                  make_row("b", "川の音に変えよう", after="川の音"), make_row("c", "今のままでいい")],
        "dev": [make_row("d", "青い海を青い山に変更して", "青い海", "青い山"), make_row("e", "光の道にして", after="光の道")],
        "test": [make_row("f", "白い雲を雪の雲にして", "白い雲", "雪の雲"), make_row("g", "そのままで")],
        "regression": [make_row("h", "月の川から月の空に変えよう", "月の川", "月の空")],
    }
    for split, items in rows.items():
        (root / "data" / (split + ".jsonl")).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in items), encoding="utf-8")
    runtime.save(root / "data/dataset-status.json", {"training_allowed": True})
    (root / "sources/01.csv").write_text("発話,変更元,変更先\n試験用の原文,,\n", encoding="utf-8-sig")
    protocol = {"schema": "edit_span_csv_colab.v1", "project": {"id": "smoke", "name": "接続試験", "input_format": runtime.INPUT_FORMAT},
                "profiles": {"span": {"counts": {split: len(items) for split, items in rows.items()}}},
                "training": {"model": runtime.MODEL, "revision": runtime.REVISION, "seed": 42, "epochs": 4,
                             "batch_size": 2, "learning_rate": 1e-4, "early_stopping_patience": 3}}
    runtime.save(root / "training-protocol.json", protocol)
    runtime.save(root / "input-sha256.json", {str(p.relative_to(root)): runtime.digest(p) for p in root.rglob("*") if p.is_file()})
    return rows


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="span-colab-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "materials"
        self.rows = materialize(self.root)

    # 見込み: 入力hashと出力先bindingを照合すれば、別の教材で古い成果を再利用しない。
    # 合格: 正常教材だけ受理し、同じoutputへの異なる条件・CSV改変・上位pathを拒否する。
    def test_bundle_and_output_binding_reject_drift(self):
        _, identity = runtime.bundle(self.root)
        output = self.directory / "output"
        runtime.bind(output, identity, create=True)
        runtime.bind(output, identity)
        with self.assertRaisesRegex(ValueError, "別の教材"):
            runtime.bind(output, {**identity, "protocol_sha256": "changed"})
        with self.assertRaisesRegex(ValueError, "保存先"):
            runtime.inside(self.root, "../outside.csv")
        with (self.root / "sources/01.csv").open("a") as stream:
            stream.write("changed\n")
        with self.assertRaisesRegex(ValueError, "変更されています"):
            runtime.bundle(self.root)

    # 見込み: 未完試行と完成モデルを分ければ、中断された学習をテスト済みと誤認しない。
    # 合格: 未完はcompletedで拒否し、新しい番号を選んでも古いファイルは保持する。
    def test_incomplete_run_is_preserved_and_not_evaluated(self):
        _, identity = runtime.bundle(self.root)
        output = self.directory / "output"
        runtime.bind(output, identity, create=True)
        old = runtime.new_directory(output / "attempts", "attempt")
        (old / "partial.txt").write_text("keep")
        new = runtime.new_directory(output / "attempts", "attempt")
        self.assertNotEqual(old, new)
        self.assertEqual((old / "partial.txt").read_text(), "keep")
        with self.assertRaisesRegex(ValueError, "まだ学習が完了"):
            runtime.completed(self.root, output)

    # 見込み: 原文範囲をrole込みで比較すれば、余計な変更元や末尾の欠落を成績へ反映できる。
    # 合格: 2例中1例の完全一致、from適合率0、to完全一致。HTMLは原文タグを実行しない。
    def test_role_metrics_and_failure_html_preserve_original_text(self):
        shared = runtime.span_module(self.root)
        good = make_row("good", "<風>に変更", after="<風>")
        bad = make_row("bad", "<script>空に変更", after="空")
        rows = [{**good, "predicted": good["spans"], "exact": True},
                {**bad, "predicted": [{"role": "from", "start": 0, "end": 8, "text": "<script>"}, *bad["spans"]], "exact": False}]
        result = runtime.summary_metrics(rows, shared)
        self.assertEqual(result["all"]["exact"], 1)
        self.assertEqual(result["by_role"]["from"]["span_precision"], 0)
        self.assertEqual(result["by_role"]["to"]["exact"], 2)
        output = self.directory / "report"
        evaluation = output / "evaluation"
        evaluation.mkdir(parents=True)
        (evaluation / "test-predictions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        runtime.save(output / "evaluation-summary.json", {"evaluation": "evaluation", "selected_epoch": 4,
                     "training_complete": {"epochs_completed": 7}, "metrics": {"test": result}})
        rendered = controls.result_html(output)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("変更元", rendered)
        self.assertIn("1/2", rendered)


    # 見込み: Colab callbackで失敗しても、ボタンを戻せば再試行と入力訂正を続けられる。
    # 合格: 模擬widget/子processで失敗ログ、未学習時の禁止、完了後の試行、HTML escapeを確認。
    def test_widget_failure_recovery_and_single_prediction(self):
        class Widget:
            def __init__(self, *args, **kwargs):
                self.value = kwargs.get("value", "")
                self.disabled = False
                self.messages = []
            def on_click(self, callback): self.callback = callback
            def click(self): self.callback(self)
            def clear_output(self): self.messages.clear()
            def append_stdout(self, value): self.messages.append(value)
            def append_stderr(self, value): self.messages.append(value)
        class Process:
            def __init__(self, code): self.stdout = ["process log\n"]; self.code = code
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def wait(self): return self.code
        widget_module = types.SimpleNamespace(**{name: Widget for name in ("Button", "Textarea", "Layout", "Output", "HTML", "HBox")})
        fake_modules = {"ipywidgets": widget_module,
                        "IPython.display": types.SimpleNamespace(display=lambda *args: None),
                        "google.colab": types.SimpleNamespace(files=types.SimpleNamespace(download=lambda path: None)),
                        "span_train_colab": runtime}
        output = self.directory / "widgets"
        output.mkdir()
        with patch.dict(sys.modules, fake_modules):
            ui = controls.show_controls(self.root, output)
        self.assertTrue(ui["test"].disabled)
        self.assertTrue(ui["try_one"].disabled)
        with patch.object(controls.subprocess, "Popen", return_value=Process(1)):
            ui["train"].click()
        self.assertFalse(ui["train"].disabled)
        self.assertTrue(ui["test"].disabled)
        self.assertIn("失敗", "".join(ui["log"].messages))
        (output / "active-run.json").write_text("{}")
        with patch.object(controls.subprocess, "Popen", return_value=Process(0)):
            ui["train"].click()
        self.assertFalse(ui["test"].disabled)
        self.assertFalse(ui["try_one"].disabled)
        ui["utterance"].value = "<空>に変えて"
        response = {"predicted": [{"role": "to", "text": "<空>", "start": 0, "end": 3}]}
        with patch.object(controls.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")) as run:
            ui["try_one"].click()
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["text"], "<空>に変えて")
        self.assertIn("&lt;空&gt;", ui["single_result"].value)
        self.assertFalse(ui["try_one"].disabled)
        self.assertFalse(ui["train"].disabled)


@unittest.skipUnless(os.environ.get("DOGIDO_RUN_SPAN_TRAINING_SMOKE") == "1", "小型CPUの実学習は明示実行時だけ")
class TinyModelRuntimeTests(unittest.TestCase):
    # 見込み: 同じtrain.pyを小型実モデルで通せば、実行・選択・保存・再評価の接続を検証できる。
    # 合格: 学習完了、test/回帰成績と全予測、ZIP内の原本、再評価後のモデル不変。精度は判定しない。
    def test_real_cpu_training_evaluation_and_retest_preserve_model(self):
        import torch
        from transformers import BertConfig, BertForTokenClassification
        torch.set_num_threads(2)
        with tempfile.TemporaryDirectory(prefix="span-colab-real-smoke-") as temporary:
            directory = Path(temporary)
            root, output = directory / "materials", directory / "output"
            rows = materialize(root)
            base = directory / "tiny-base"
            base.mkdir()
            chars = sorted(set("".join(row["text"] + row["current_verse"] for values in rows.values() for row in values)) - {"\n", "\r"})
            vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", *chars]
            (base / "vocab.txt").write_text("\n".join(vocab) + "\n", encoding="utf-8")
            torch.manual_seed(42)
            model = BertForTokenClassification(BertConfig(vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1,
                                num_attention_heads=2, intermediate_size=32, num_labels=5, max_position_embeddings=512))
            model.save_pretrained(base)
            runtime.save(base / "provenance.json", {"scope": "random tiny CPU connection test; no public-model quality claim"})
            del model
            _, identity = runtime.bundle(root)
            runtime.bind(output, identity, create=True)
            interrupted = runtime.new_directory(output / "attempts", "attempt")
            (interrupted / "partial.txt").write_text("keep")
            attempt = runtime.train_bundle(root, output, device="cpu", _test_base=base)
            self.assertEqual(attempt.name, "attempt-0002")
            self.assertEqual((interrupted / "partial.txt").read_text(), "keep")
            self.assertEqual(runtime.train_bundle(root, output, device="cpu", _test_base=base), attempt)
            history = runtime.read(attempt / "run/training-history.json")
            self.assertGreater(len(history), 1)
            self.assertNotEqual(history[0]["train_loss"], history[-1]["train_loss"])
            before = runtime.model_hashes(attempt / "run/model")
            active = (output / "active-run.json").read_bytes()
            first = runtime.evaluate_bundle(root, output, device="cpu")
            second = runtime.evaluate_bundle(root, output, device="cpu")
            self.assertNotEqual(first["evaluation"], second["evaluation"])
            self.assertEqual(first["metrics"], second["metrics"])
            self.assertEqual(before, runtime.model_hashes(attempt / "run/model"))
            self.assertEqual(active, (output / "active-run.json").read_bytes())
            for split, values in rows.items():
                self.assertEqual(second["metrics"][split]["all"]["count"], len(values))
            archive = output / second["archive"]
            self.assertEqual(runtime.digest(archive), second["archive_sha256"])
            with zipfile.ZipFile(archive) as zipped:
                self.assertIn("model/model.safetensors", zipped.namelist())
                self.assertIn("model/input-format.json", zipped.namelist())
                self.assertIn("training/training-history.json", zipped.namelist())
                self.assertIn("evaluation/test-predictions.jsonl", zipped.namelist())
                self.assertEqual(zipped.read("materials/sources/01.csv"), (root / "sources/01.csv").read_bytes())
            result = runtime.predict_one(root, output, rows["test"][0]["text"], "", device="cpu")
            self.assertEqual(result["text"], rows["test"][0]["text"])
            self.assertIsInstance(result["predicted"], list)
            self.assertEqual(before, runtime.model_hashes(attempt / "run/model"))
            (attempt / "run/model/input-format.json").write_text("changed")
            with self.assertRaisesRegex(ValueError, "保存モデル"):
                runtime.completed(root, output)


if __name__ == "__main__":
    unittest.main()
