"""独立抽出器の文字位置・出典分割を検証する。

見込み: 文脈をラベル対象外にし原文位置を維持すれば、依頼語と句末助詞を分離できる。
合格: 指定範囲の往復と同一句/revisionの統合が一致。これはモデル精度の試験ではない。
"""
from pathlib import Path
import sys
import json
import hashlib
import random
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev_tools" / "edit_span_extractor"))
from span_model import CharacterCodec, decode_spans, span_key, validate_spans
from build_dataset import group_poems, render, TEMPLATES
from augmentation import kana_template, prepare
from compare_seeds import select_seed


class SpanContractTests(unittest.TestCase):
    def test_context_and_particles_preserve_original_offsets(self):
        # 見込み: 句末の「に」と依頼の「に」が隣接しても、原文範囲を保つ。
        # 合格: 文脈はignore、句の「に」はto内、依頼の「に」はO。モデルは模擬ラベル。
        text, spans = render("上の句の『{old}』を『{new}』にして", {"old": "夕方に", "new": "朝に"})
        row = {"id": "boundary", "current_verse": "夕方に\n草を眺めて\n帰り道", "text": text, "spans": spans}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vocab.txt"
            path.write_text("\n".join(["[PAD]", "[UNK]", "[CLS]", "[SEP]"] + sorted(set(text + row["current_verse"]))) + "\n")
            codec = CharacterCodec(path)
            encoded = codec.encode(row, with_labels=True)
        start = encoded["text_offset"]
        self.assertTrue(all(label == -100 for label in encoded["labels"][:start]))
        predicted = decode_spans(text, encoded["labels"][start:-1])
        self.assertEqual(span_key(predicted), span_key(spans))
        self.assertEqual(predicted[-1]["text"], "朝に")
        self.assertEqual(encoded["labels"][start + text.index("にして")], 0)

    def test_repeated_words_are_distinct_occurrences(self):
        # 見込み: 同じ文字列を2回指定しても、挿入位置を記録すれば取り違えない。
        # 合格: 最初だけfrom、二番目だけtoとなり、矢印・空白は含まれない。
        text, spans = render("{old} -> {new}", {"old": "夜が来る", "new": "夜が来る"})
        self.assertEqual(spans, [{"role": "from", "start": 0, "end": 4, "text": "夜が来る"},
                                 {"role": "to", "start": 8, "end": 12, "text": "夜が来る"}])
        validate_spans({"text": text, "spans": spans})

    def test_wrong_offsets_and_overlapping_labels_fail(self):
        # 見込み: 誤った注釈を黙って学習しない。合格: 原文不一致と重複を例外にする。
        with self.assertRaises(ValueError):
            validate_spans({"text": "雨から晴れ", "spans": [{"role": "to", "start": 3, "end": 5, "text": "晴れに"}]})
        with self.assertRaises(ValueError):
            validate_spans({"text": "雨", "spans": [{"role": role, "start": 0, "end": 1, "text": "雨"} for role in ["from", "to"]]})

    def test_duplicate_verse_and_revision_stay_in_one_group(self):
        # 見込み: revision経由で同じ句となる別IDもまとめれば分割漏れを防げる。
        # 合格: aの修正句とbの元句が同じなら同群、無関係なcは別群。実SSDは使わない。
        def entry(key, text):
            return {"id": key, "text": text, "source_file": "fixture", "source_line": 1}
        rows = {"entries": [entry("a", "雨\n道\n朝"), entry("b", "晴れ\n道\n朝"), entry("c", "雪\n空\n夜")],
                "revisions": [{"id": "r", "haiku_id": "a", "revised_text": "晴れ\n道\n朝", "source_file": "fixture", "source_line": 2}]}
        groups = group_poems(rows)
        members = [set(p["id"] for p in group) for group in groups.values()]
        self.assertIn({"a", "b"}, members)
        self.assertIn({"c"}, members)
        self.assertEqual(len(groups), 2)


    def test_noisy_labels_keep_the_actual_damaged_string(self):
        # 見込み: 変形を挿入前に行えば、脱落/重複しても原文にない補正語を教師にしない。
        # 合格: 100条件でfrom/toが変形後の語に一致し、依頼動詞を含まない。合成器のみ。
        changed = False
        for seed in range(100):
            template, values, prefix, suffix = prepare(
                "{line}の{old}を{new}に変更してください", {"line": "かみのく", "old": "あめのあさ", "new": "ゆきのよる"},
                "そうだな、", "", "asr_noise", random.Random(seed))
            text, spans = render(template, values, prefix, suffix)
            validate_spans({"text": text, "spans": spans})
            self.assertEqual([span["text"] for span in spans], [values["old"], values["new"]])
            self.assertNotIn("へんこ", spans[-1]["text"])
            changed |= values["old"] != "あめのあさ" or values["new"] != "ゆきのよる"
        self.assertTrue(changed)

    def test_reading_conversion_does_not_merge_split_templates(self):
        # 見込み: 漢字だけで違う同じ文型を別分割へ入れない。
        # 合格: ひらがな化後もtrain/dev/testの基底文型が互いに非重複。モデルは使わない。
        sets = {split: {kana_template(t) for values in modes.values() for t in values}
                for split, modes in TEMPLATES.items()}
        for a, b in [("train", "dev"), ("train", "test"), ("dev", "test")]:
            self.assertFalse(sets[a] & sets[b])

    def test_seed_selection_uses_dev_and_rejects_dataset_drift(self):
        # 見込み: 評価を見る前にdev順で固定し、違う教材のrunを比較から除く。
        # 合格: testに有利なseed42でもdevが良い43を選び、教材変更後は例外。模擬選択情報のみ。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            test = data / "test.jsonl"
            test.write_text('{"preferred_seed":42}\n')
            hashes = {test.name: hashlib.sha256(test.read_bytes()).hexdigest()}
            runs = []
            for seed, exact in [(42, 80), (43, 90)]:
                run = root / f"seed-{seed}"
                run.mkdir()
                (run / "manifest.json").write_text(json.dumps({"arguments": {"seed": seed}, "dataset_sha256": hashes}))
                (run / "training-complete.json").write_text('{}')
                (run / "selection.json").write_text(json.dumps({"epoch": 3, "dev_metrics": {"all": {"exact": exact, "span_f1": .9}}}))
                runs.append(run)
            self.assertEqual(select_seed(runs, data)["selected_seed"], 43)
            test.write_text('{}\n')
            with self.assertRaises(ValueError):
                select_seed(runs, data)


if __name__ == "__main__":
    unittest.main()
