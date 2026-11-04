"""独立抽出器の文字位置・出典分割を検証する。

見込み: 文脈をラベル対象外にし原文位置を維持すれば、依頼語と句末助詞を分離できる。
合格: 指定範囲の往復と同一句/revisionの統合が一致。これはモデル精度の試験ではない。
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev_tools" / "edit_span_extractor"))
from span_model import CharacterCodec, decode_spans, span_key, validate_spans
from build_dataset import group_poems, render


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


if __name__ == "__main__":
    unittest.main()
