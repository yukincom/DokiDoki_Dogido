#!/usr/bin/env python3
"""川柳移植中のprompt・辞書読み補助。生成、採否、HTTP、保存は実行しない。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.haiku.lexical_correction import correct_grounded_catalog_kana
from dogido_server.haiku.source_atoms import HaikuSourceAtom
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import StructuredGenerationRequest
from dogido_server.tts_reading import hiraganize_japanese_text
from haiku_grounding_prompt import build_haiku_line_grounding_messages
from tts_shared_tokens import handle as shared_tts_tokens

KINDS = {"haiku_irony", "haiku_scene", "haiku_draft", "haiku_line_grounding", "haiku_line_regeneration", "haiku_workshop_revision"}


def signature(text):
    chars = []
    for char in unicodedata.normalize("NFKC", text).casefold():
        if 0x30A1 <= ord(char) <= 0x30F6:
            char = chr(ord(char) - 0x60)
        if char.isalnum() or "ぁ" <= char <= "ゖ" or "一" <= char <= "鿿":
            chars.append(char)
    return "".join(chars)


def handle(frame):
    if frame["op"] == "tts_tokens":
        return shared_tts_tokens(frame)
    if frame["op"] == "prepare":
        request = StructuredGenerationRequest(**frame["request"])
        if request.kind not in KINDS:
            raise ValueError("unsupported haiku kind")
        messages = (build_haiku_line_grounding_messages(request.details)
                    if request.kind == "haiku_line_grounding" else build_messages(request))
        if request.kind == "haiku_workshop_revision":
            messages[-1]["content"] += ("\nexpected_textは元行の写し、replacement_textは必ず元行と違う表現にする。"
                "同じ行の再提出は修正にならず、検査で不合格になる。")
        return {"messages": messages}
    if frame["op"] == "transform":
        request = frame["request"]
        text = request["text"]
        if request["mode"] == "normalize":
            normalized = hiraganize_japanese_text(text).strip().strip("「」\"' ")
            if normalized and "\n" not in normalized and "\r" not in normalized:
                text = normalized
        elif request["mode"] == "correct":
            atoms = {row["atom_id"]: HaikuSourceAtom(**row) for row in request["source_atoms"]}
            correction = correct_grounded_catalog_kana(
                text, atom_ids=tuple(request["atom_ids"]), atom_by_id=atoms)
            if correction is not None:
                text = correction.corrected
        else:
            raise ValueError("unsupported transform")
        return {"text": text, "signature": signature(text)}
    raise ValueError("unsupported helper operation")


def main():
    from haiku_preparation import HaikuPreparation
    preparation = HaikuPreparation()
    # 一句の間だけ再利用。stdin EOFで終了し、子プロセスは生成しない。
    for line in sys.stdin:
        try:
            if len(line.encode("utf-8")) > 1_000_000:
                raise ValueError("helper frame too large")
            frame = json.loads(line)
            output = preparation.handle(frame) if frame.get("op", "").startswith("haiku_") else handle(frame)
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), flush=True)
            return 1
        print(json.dumps(output, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
