"""見込み: CSV追加を独立教材に閉じ、原文・固定評価集合・Notebookの組を保つ。
合格: 正例の往復、混入拒否、ラベル拡張、ZIP照合が成立。T4実機学習は範囲外。
"""
import ast
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
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


builder = load("csv_builder", SOURCE / "builder.py")
packager = load("csv_app_packager", SOURCE / "Tools/build_app.py")


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
            writer.writerows(rows)
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
        self.assertEqual(len({cell["id"] for cell in notebook["cells"]}), len(notebook["cells"]))
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                code = "".join(cell["source"])
                if not code.startswith("%pip"):
                    ast.parse(code)
        digest = hashlib.sha256((result / "教材.zip").read_bytes()).hexdigest()
        self.assertIn(digest, json.dumps(notebook))

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
    # 合格: best保存・test予測・完了記録・再実行時の完了保持が成立。T4や実モデル品質は未確認。
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
                (data / f"{split}.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
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
            result = runtime.train_profile(root, output, "senryu", config, device="cpu")
            self.assertEqual(result["metrics"]["test"]["n"], 8)
            self.assertTrue((output / "senryu/best/model.safetensors").is_file())
            history = (output / "senryu/trainer-history.json").read_bytes()
            repeated = runtime.train_profile(root, output, "senryu", config, device="cpu")
            self.assertEqual(repeated, result)
            self.assertEqual((output / "senryu/trainer-history.json").read_bytes(), history)


if __name__ == "__main__":
    unittest.main()
