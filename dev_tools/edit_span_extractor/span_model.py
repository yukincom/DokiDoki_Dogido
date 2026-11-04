"""句の具体変更から原文の変更元・変更先を抜く、独立試作用の文字BERT。

現在句は文脈として読み、発話側の文字だけをBIO分類する。返すのは原文の
範囲であり、漢字変換・句の生成・変更意思の実行・本体への保存は行わない。
"""
from __future__ import annotations

import json
from pathlib import Path
import unicodedata

LABELS = ["O", "B-from", "I-from", "B-to", "I-to"]
LABEL_TO_ID = {name: i for i, name in enumerate(LABELS)}


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def validate_spans(row):
    occupied = set()
    for span in row["spans"]:
        start, end = span["start"], span["end"]
        if (span["role"] not in {"from", "to"} or not 0 <= start < end <= len(row["text"])
                or row["text"][start:end] != span["text"]):
            raise ValueError(f"Invalid original-text span: {row.get('id')}: {span}")
        positions = set(range(start, end))
        if occupied & positions:
            raise ValueError("Overlapping slots are outside this prototype's contract")
        occupied |= positions


class CharacterCodec:
    """既存文字vocabを使い、原文1文字とモデル1位置を対応させる。

    MeCabの空白除去を挟まず、オフセットを保存する。NFKCで1文字になる場合
    だけ入力IDに反映し、原文は変えない。複数文字化する記号は[UNK]にする。
    """
    def __init__(self, path):
        self.vocab = Path(path).read_text().splitlines()
        self.ids = {word: i for i, word in enumerate(self.vocab)}
        self.pad_id = self.ids["[PAD]"]

    def char_id(self, char):
        normalized = unicodedata.normalize("NFKC", char)
        return self.ids.get(normalized, self.ids["[UNK]"])

    def encode(self, row, with_labels=False):
        text = row["text"]
        context = row.get("current_verse") or ""
        if not isinstance(context, str):
            raise ValueError("current_verse must be a string")
        ids = [self.ids["[CLS]"]]
        ids += [self.ids["[SEP]"] if c == "\n" else self.char_id(c) for c in context]
        ids += [self.ids["[SEP]"]]
        offset = len(ids)
        ids += [self.char_id(c) for c in text] + [self.ids["[SEP]"]]
        if len(ids) > 512:
            raise ValueError("Input exceeds 512 characters; this prototype never silently truncates")
        item = {"input_ids": ids, "attention_mask": [1] * len(ids),
                "token_type_ids": [0] * offset + [1] * (len(text) + 1),
                "text_offset": offset, "text_length": len(text)}
        if with_labels:
            validate_spans(row)
            labels = [-100] * offset + [0] * len(text) + [-100]
            for span in row["spans"]:
                for j in range(span["start"], span["end"]):
                    labels[offset + j] = LABEL_TO_ID[("B-" if j == span["start"] else "I-") + span["role"]]
            item["labels"] = labels
        return item


def collate(items, pad_id, device):
    import torch
    width = max(len(item["input_ids"]) for item in items)
    batch = {}
    for key in ["input_ids", "attention_mask", "token_type_ids", "labels"]:
        if key not in items[0]:
            continue
        pad = -100 if key == "labels" else pad_id if key == "input_ids" else 0
        batch[key] = torch.tensor([item[key] + [pad] * (width - len(item[key])) for item in items],
                                  dtype=torch.long, device=device)
    return batch


def decode_spans(text, label_ids):
    """予測BIOを範囲へ変換するだけ。語句・助詞・読みで予測を修正しない。"""
    if len(text) != len(label_ids):
        raise ValueError("Prediction length differs from original text")
    spans = []
    active = None
    for i, label_id in enumerate(label_ids):
        name = LABELS[int(label_id)]
        role = None if name == "O" else name[2:]
        if active is not None and (role != active["role"] or name.startswith("B-")):
            active["text"] = text[active["start"]:active["end"]]
            spans.append(active)
            active = None
        if role is not None:
            if active is None:
                active = {"role": role, "start": i, "end": i + 1}
            else:
                active["end"] = i + 1
    if active is not None:
        active["text"] = text[active["start"]:active["end"]]
        spans.append(active)
    return spans


def span_key(spans):
    return sorted((s["role"], s["start"], s["end"], s["text"]) for s in spans)


def metrics(rows):
    def aggregate(group):
        tp = predicted = gold = 0
        for row in group:
            p, g = set(span_key(row["predicted"])), set(span_key(row["spans"]))
            tp += len(p & g)
            predicted += len(p)
            gold += len(g)
        return {"count": len(group), "exact": sum(r["exact"] for r in group),
                "exact_rate": sum(r["exact"] for r in group) / len(group) if group else None,
                "span_precision": tp / predicted if predicted else None,
                "span_recall": tp / gold if gold else None,
                "span_f1": 2 * tp / (predicted + gold) if predicted + gold else None}
    return {"all": aggregate(rows), "positive": aggregate([r for r in rows if r["spans"]]),
            "no_explicit_slot": aggregate([r for r in rows if not r["spans"]])}


def predict_rows(model, codec, rows, device, batch_size=32):
    import torch
    results = []
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(rows), batch_size):
            chunk = rows[start:start + batch_size]
            encoded = [codec.encode(row) for row in chunk]
            predictions = model(**collate(encoded, codec.pad_id, device)).logits.argmax(-1).cpu().tolist()
            for row, item, labels in zip(chunk, encoded, predictions, strict=True):
                offset = item["text_offset"]
                spans = decode_spans(row["text"], labels[offset:offset + item["text_length"]])
                result = {**row, "predicted": spans}
                if "spans" in row:
                    result["exact"] = span_key(spans) == span_key(row["spans"])
                results.append(result)
    return results
