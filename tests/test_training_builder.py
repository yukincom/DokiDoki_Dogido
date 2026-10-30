"""見込み: CSV追加を独立教材に閉じ、原文・固定評価集合・Notebookの組を保つ。
合格: 正例の往復、混入拒否、ラベル拡張、ZIP照合が成立。T4実機学習は範囲外。
"""
import ast
import csv
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch
import zipfile

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "apps/dogido-training-builder"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load("csv_builder", SOURCE / "builder.py")
packager = load("csv_app_packager", SOURCE / "Tools/build_app.py")
if os.environ.get("DOGIDO_WIDGET_CHECK_PATH"):
    sys.path.insert(0, os.environ["DOGIDO_WIDGET_CHECK_PATH"])


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dogido-csv-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.assets = self.root / "assets"
        packager.prepare_assets(REPO, self.assets)

    def csv(self, rows):
        path = self.root / "日本語 入力.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(builder.HEADERS)
            writer.writerows(row + [""] if len(row) == 6 else row for row in rows)
        return path

    # 見込み: 引用符・カンマ・改行をCSVとして読み、正解メモを学習本文へ混ぜない。
    # 合格: 元本文の完全一致、dev/testのbyte不変、Notebookの構文とZIP hashの一致。
    def test_csv_notebook_and_frozen_evaluation_round_trip(self):
        text = 'CSV検査の「音,風」を聞く\n続きもそのまま'
        path = self.csv([["はい", "通常", text, "対象外", "この説明は教師ラベルの理由", ""]])
        result = builder.build([path], self.assets)
        notebook = json.loads((result / "ドギド_T4学習.ipynb").read_text())
        with zipfile.ZipFile(result / "教材.zip") as archive:
            training = [json.loads(line) for line in archive.read("data/normal/train.jsonl").splitlines()]
            row = next(r for r in training if r["text"] == text)
            self.assertEqual(row["label"], "not_target")
            self.assertEqual(row["context"], [])
            self.assertNotIn("この説明", row["text"])
            self.assertEqual(archive.read("sources/01.csv"), path.read_bytes())
            for split in ("dev", "test"):
                self.assertEqual(archive.read(f"data/normal/{split}.jsonl"),
                                 (self.assets / "baseline/normal" / f"{split}.jsonl").read_bytes())
            for name, expected in json.loads(archive.read("input-sha256.json")).items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
            profile = json.loads(archive.read("training-protocol.json"))["profiles"]["normal"]
            self.assertEqual(profile["baseline_counts"], {"train": 818, "dev": 46, "test": 50})
            self.assertEqual(profile["counts"], {"train": 819, "dev": 46, "test": 50})
            self.assertEqual(profile["added_counts"], {"train": 1, "dev": 0, "test": 0})
            self.assertEqual(profile["split_integrity"], {"normalized_text_overlap": 0, "group_overlap": 0, "id_overlap": 0})
            self.assertIn("notebook_controls.py", archive.namelist())
        with (result / "教材の中身/normal/学習.csv").open(encoding="utf-8-sig", newline="") as stream:
            exported = list(csv.DictReader(stream))
        self.assertEqual(len(exported), 819)
        self.assertIn(text, [r["発話"] for r in exported])
        self.assertEqual(len({cell["id"] for cell in notebook["cells"]}), len(notebook["cells"]))
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                code = "".join(cell["source"])
                if not code.startswith("%pip"):
                    ast.parse(code)
        digest = hashlib.sha256((result / "教材.zip").read_bytes()).hexdigest()
        self.assertIn(digest, json.dumps(notebook))

    # 見込み: 新規評価例は指定用途だけへ入り、旧教材を全件保持したまま分離できる。
    # 合格: 新規train/dev/test各1件の所属が一致し、元dev/testは内容・順序・正解を保持。
    def test_explicit_split_additions_keep_all_baseline_rows(self):
        path = self.csv([["はい", "川柳", "CSV用途検査の" + split, "対象外", "", "group_" + split, split]
                         for split in ("学習", "調整", "テスト")])
        result = builder.build([path], self.assets)
        with zipfile.ZipFile(result / "教材.zip") as archive:
            for split, title, count in (("train", "学習", 350), ("dev", "調整", 40), ("test", "テスト", 37)):
                rows = [json.loads(line) for line in archive.read(f"data/senryu/{split}.jsonl").splitlines()]
                baseline = [json.loads(line) for line in (self.assets / f"baseline/senryu/{split}.jsonl").read_text().splitlines()]
                self.assertEqual(rows[:-1], baseline)
                self.assertEqual(len(rows), count)
                self.assertEqual(rows[-1]["text"], "CSV用途検査の" + title)

    # 見込み: CSVから用途を指定しても、同じ発話・同じ会話を分けて成績を水増ししない。
    # 合格: 表記揺れの発話と共通groupのtrain/test混入を、それぞれ生成前に拒否。
    def test_csv_text_and_group_cannot_cross_splits(self):
        first = ["はい", "通常", "CSV用途混入検査です", "対象外", "", "同じ会話", "学習"]
        other = ["はい", "通常", "ＣＳＶ用途混入検査です。", "対象外", "", "別の会話", "テスト"]
        path = self.csv([first, other])
        with self.assertRaisesRegex(ValueError, "異なる用途"):
            builder.build([path], self.assets)
        other[2], other[5] = "異なるけれど同じ会話の発話です", "同じ会話"
        path = self.csv([first, other])
        with self.assertRaisesRegex(ValueError, "グループ"):
            builder.build([path], self.assets)

    # 見込み: 既存評価集合を学習へ混ぜる入力は、同じ表記の揺れでも止める。
    # 合格: dev発話とdev groupを別々に与えてどちらも拒否。元教材は不変。
    def test_evaluation_text_and_group_never_enter_train(self):
        dev = json.loads((self.assets / "baseline/senryu/dev.jsonl").read_text().splitlines()[0])
        path = self.csv([["はい", "川柳", dev["text"], dev["label"], "", ""]])
        with self.assertRaisesRegex(ValueError, "評価用データ"):
            builder.build([path], self.assets)
        path = self.csv([["はい", "川柳", "別のCSV検査発話です", "対象外", "", dev["group_id"]]])
        with self.assertRaisesRegex(ValueError, "評価用データ"):
            builder.build([path], self.assets)

    # 見込み: 同じCSV入力の重複は増幅せず、矛盾する教師値は人へ返す。
    # 合格: 同ラベル2行が1候補・1重複になり、別ラベルの重複はエラー。
    def test_duplicate_and_conflicting_labels(self):
        first = ["はい", "通常", "CSVの重複確認用発話です", "対象外", "", ""]
        path = self.csv([first, first])
        rows, skipped, _ = builder.read_csv([path])
        self.assertEqual((len(rows), len(skipped)), (1, 1))
        other = first.copy()
        other[3] = "ドギドの呼び寄せ"
        path = self.csv([first, other])
        with self.assertRaisesRegex(ValueError, "異なる正解"):
            builder.read_csv([path])

    # 見込み: モードが違うラベル・入力列の破損を、別の分類で補わない。
    # 合格: 川柳へ通常ラベルを渡した場合と、列不足のCSVは拒否。
    def test_invalid_mode_label_and_columns(self):
        path = self.csv([["はい", "川柳", "CSV検査の誤ラベルです", "ドギドの呼び寄せ", "", ""]])
        with self.assertRaisesRegex(ValueError, "使えない分類"):
            builder.build([path], self.assets)
        path.write_text("モード,発話,正解分類\n通常,欠けた列\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "列数"):
            builder.read_csv([path])

    # 見込み: 上下を要求した場合は既存IDを保ち、両クラスに教材を要求する。
    # 合格: 上だけは拒否、上下ならID16/17を追加。既存dev/testはそのまま。
    def test_vertical_extension_requires_both_classes(self):
        up = ["はい", "通常", "CSV検査用に真上を向いて", "上を向く", "", ""]
        path = self.csv([up])
        with self.assertRaisesRegex(ValueError, "学習例が0件"):
            builder.build([path], self.assets)
        path = self.csv([up, ["はい", "通常", "CSV検査用に真下を向いて", "下を向く", "", ""]])
        result = builder.build([path], self.assets)
        with zipfile.ZipFile(result / "教材.zip") as archive:
            labels = json.loads(archive.read("data/normal/labels.json"))
            original = json.loads((self.assets / "baseline/normal/labels.json").read_text())
            self.assertEqual({key: labels[key] for key in original}, original)
            self.assertEqual((labels["look_relative_up"], labels["look_relative_down"]), (16, 17))
            protocol = json.loads(archive.read("training-protocol.json"))
            self.assertEqual(protocol["profiles"]["normal"]["labels_without_test_examples"], ["look_relative_down", "look_relative_up"])

    # 見込み: 既存trainの正解訂正は出力教材だけへ適用し、固定元データを守る。
    # 合格: 元ファイルのhashが同じで、出力側の対象ラベルだけが変わる。
    def test_existing_train_correction_preserves_source(self):
        original_file = self.assets / "baseline/senryu/train.jsonl"
        raw = original_file.read_bytes()
        original = json.loads(raw.splitlines()[0])
        label = "senryu_line_upper" if original["label"] != "senryu_line_upper" else "not_target"
        path = self.csv([["はい", "川柳", original["text"], label, "明示訂正", ""]])
        result = builder.build([path], self.assets)
        self.assertEqual(original_file.read_bytes(), raw)
        with zipfile.ZipFile(result / "教材.zip") as archive:
            rows = [json.loads(line) for line in archive.read("data/senryu/train.jsonl").splitlines()]
            self.assertEqual(next(r["label"] for r in rows if r["id"] == original["id"]), label)
            self.assertEqual(len(rows), len(raw.splitlines()))

    # 見込み: 出力は追加先CSVの隣に新規作成され、再生成で前の結果を消さない。
    # 合格: 同じCSVの2回の生成先が異なり、最初のNotebookが保持される。
    def test_repeated_generation_keeps_previous_artifact(self):
        path = self.csv([["はい", "川柳", "CSV再生成用の発話です", "対象外", "", ""]])
        first = builder.build([path], self.assets)
        old = (first / "ドギド_T4学習.ipynb").read_bytes()
        second = builder.build([path], self.assets)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "ドギド_T4学習.ipynb").read_bytes(), old)


@unittest.skipUnless(os.environ.get("DOGIDO_RUN_TRAINING_SMOKE") == "1", "明示したときだけ小型CPUモデルで学習部品を確認")
class TrainingSmokeTests(unittest.TestCase):
    # 見込み: Colabと同じ学習・評価部品が、小さいローカルModernBERTで最後まで動く。
    # 合格: 学習時はtest未評価、独立評価で予測保存、再学習なし・重み不変。T4や精度は範囲外。
    def test_tiny_cpu_training_and_completed_resume(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import ModernBertConfig, ModernBertForSequenceClassification, PreTrainedTokenizerFast
        import torch
        torch.set_num_threads(2)
        runtime = load("colab_runner_smoke", SOURCE / "train_colab.py")
        with tempfile.TemporaryDirectory(prefix="dogido-training-smoke-") as directory:
            root = Path(directory)
            assets = root / "assets"
            packager.prepare_assets(REPO, assets)
            (root / "shared").mkdir()
            import shutil
            for path in (assets / "shared").iterdir():
                shutil.copyfile(path, root / "shared" / path.name)
            labels = json.loads((assets / "baseline/senryu/labels.json").read_text())
            data = root / "data/senryu"
            data.mkdir(parents=True)
            runtime.save(data / "labels.json", labels)
            rows = [{"id": f"toy_{i}", "group_id": f"toy_{i}", "text": f"検査発話{i}", "context": [],
                     "label": name, "tags": ["fixture"], "source": "synthetic", "needs_clarification": False}
                    for i, name in enumerate(labels)]
            for split in ("train", "dev", "test"):
                (data / f"{split}.jsonl").write_text("".join(json.dumps({**row, "id": split + row['id'],
                    "group_id": split + row['group_id'], "text": split + row['text']}, ensure_ascii=False) + "\n" for row in rows))
            runtime.save(root / "input-sha256.json", {})
            model_root = root / "tiny-model"
            model_root.mkdir()
            tokenizer = Tokenizer(models.WordLevel({"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3}, unk_token="[UNK]"))
            tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
            PreTrainedTokenizerFast(tokenizer_object=tokenizer, pad_token="[PAD]", unk_token="[UNK]", cls_token="[CLS]", sep_token="[SEP]").save_pretrained(model_root)
            model = ModernBertForSequenceClassification(ModernBertConfig(vocab_size=4, hidden_size=16, intermediate_size=32,
                num_hidden_layers=1, num_attention_heads=2, max_position_embeddings=128, local_attention=32,
                global_attn_every_n_layers=1, pad_token_id=0, bos_token_id=2, eos_token_id=3,
                cls_token_id=2, sep_token_id=3, num_labels=8,
                label2id=labels, id2label={i: name for name, i in labels.items()}))
            model.save_pretrained(model_root)
            config = {"model": str(model_root), "revision": None, "seed": 42, "epochs": 1,
                      "learning_rate": 5e-5, "batch_size": 4, "max_length": 128}
            output = root / "output"
            with self.assertRaisesRegex(ValueError, "先に"):
                runtime.evaluate_profile(root, output, "senryu", config, device="cpu")
            result = runtime.train_profile(root, output, "senryu", config, device="cpu")
            self.assertFalse(result["test_evaluated"])
            self.assertFalse((output / "senryu/test-predictions.jsonl").exists())
            self.assertTrue((output / "senryu/best/model.safetensors").is_file())
            history = (output / "senryu/trainer-history.json").read_bytes()
            repeated = runtime.train_profile(root, output, "senryu", config, device="cpu")
            self.assertEqual(repeated, result)
            self.assertEqual((output / "senryu/trainer-history.json").read_bytes(), history)
            from transformers import Trainer
            with patch.object(Trainer, "train", side_effect=AssertionError("テストで再学習は禁止")):
                evaluated = runtime.evaluate_profile(root, output, "senryu", config, device="cpu")
                repeated = runtime.evaluate_profile(root, output, "senryu", config, device="cpu")
            self.assertEqual(evaluated, repeated)
            self.assertEqual(evaluated["metrics"]["test"]["n"], 8)
            self.assertEqual(runtime.completed(root, output, "senryu", config), result)
            self.assertEqual((output / "senryu/trainer-history.json").read_bytes(), history)
            weights = output / "senryu/best/model.safetensors"
            weights.write_bytes(weights.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "変更されています"):
                runtime.evaluate_profile(root, output, "senryu", config, device="cpu")


@unittest.skipUnless(os.environ.get("DOGIDO_WIDGET_CHECK_PATH"), "別途用意したipywidgets環境でのみ実行")
class NotebookButtonTests(unittest.TestCase):
    # 見込み: Colabのボタンが学習と評価を分け、評価の結果を画面とダウンロードへ接続する。
    # 合格: 実ipywidgetsのclickで評価専用引数・成績表示・ボタン状態を確認。subprocessとColabは模擬。
    def test_buttons_route_separate_evaluation_and_show_report(self):
        from unittest.mock import Mock
        controls_module = load("notebook_controls_check", SOURCE / "notebook_controls.py")
        with tempfile.TemporaryDirectory(prefix="dogido-widget-test-") as directory:
            root = Path(directory)
            output = root / "results"
            output.mkdir()
            (root / "training-protocol.json").write_text(json.dumps({"profiles": {"senryu": {}}}))
            commands, downloads = [], []
            colab = types.ModuleType("google.colab")
            colab.files = types.SimpleNamespace(download=downloads.append)

            def subprocess_fixture(command, **kwargs):
                commands.append(command)
                (output / "senryu").mkdir(exist_ok=True)
                if "--evaluate-only" in command:
                    metric = {"n": 1, "correct": 0, "accuracy": 0, "macro_f1": 0,
                              "per_class": {"not_target": {"support": 1, "precision": 0, "recall": 0, "f1": 0}}}
                    result = {"senryu": {"metrics": {split: metric for split in ("train", "dev", "test")}}}
                    (output / "evaluation-summary.json").write_text(json.dumps(result))
                    (output / "senryu/test-predictions.jsonl").write_text(json.dumps(
                        {"text": "<script>模擬失敗例</script>", "gold": "not_target", "predicted": "senryu_end", "correct": False}) + "\n")
                else:
                    (output / "senryu/done.json").write_text("{}")
                (output / "dogido-training-results.zip").write_bytes(b"fixture")
                process = Mock(stdout=io.StringIO("模擬実行ログ\n"))
                process.wait.return_value = 0
                context = Mock()
                context.__enter__ = Mock(return_value=process)
                context.__exit__ = Mock(return_value=False)
                return context

            with patch.dict(sys.modules, {"google.colab": colab}), patch("IPython.display.display"), \
                    patch.object(controls_module.subprocess, "Popen", side_effect=subprocess_fixture):
                controls = controls_module.show_controls(root, output)
                self.assertTrue(controls["test"].disabled)
                self.assertTrue(controls["download"].disabled)
                controls["train"].click()
                self.assertNotIn("--evaluate-only", commands[-1])
                self.assertFalse(controls["test"].disabled)
                self.assertTrue(controls["download"].disabled)
                controls["test"].click()
                self.assertIn("--evaluate-only", commands[-1])
                self.assertIn("0/1", controls["report"].value)
                self.assertIn("&lt;script&gt;", controls["report"].value)
                self.assertNotIn("<script>", controls["report"].value)
                self.assertFalse(controls["download"].disabled)
                controls["download"].click()
                self.assertEqual(downloads, [str(output / "dogido-training-results.zip")])
                with patch.object(controls_module.subprocess, "Popen", side_effect=OSError("模擬実行失敗")):
                    controls["test"].click()
                self.assertFalse(controls["train"].disabled)
                self.assertIn("模擬実行失敗", str(controls["log"].outputs))



if __name__ == "__main__":
    unittest.main()
