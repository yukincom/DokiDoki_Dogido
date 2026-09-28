#!/usr/bin/env python3
"""Python正本の候補作成・音近傍補正を固定fixtureへ。LLMや音声機器は使わない。"""
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.player_input.contextual_asr import (
    ContextualASRCandidate as Candidate, apply_candidate_asr_fixes,
    normalize_spoken_kana, workshop_asr_candidates,
)


def main():
    verse = "さくらのは\nくろいおのへと\nあさのいろ"
    material_sets = [None, {}, *({"time_phase": phase} for phase in ["morning", "day", "evening", "night", " EVENING ", "unknown"]),
        {"catalog_sources": [None, {"label": "草ブロック", "reading": "くさぶろっく", "source_ref": "item:grass_block"},
                             {"label": "サクラの葉", "reading": "さくらのは"}],
         "source_atoms": [False, {"kind": "catalog_label", "text": "草のブロック", "source_ref": "item:grass_block"},
                          {"kind": "observed", "text": "クリーパーがいる"}]},
        {"asr_context_terms": [None, {"surface": "夕暮れ", "readings": ["ゆうぐれ", " ユウグレ ", "", None]},
                               {"surface": "優暮れ", "reading": "ゆうぐれ", "source": "ambiguous"}]},
        {"catalog_sources": [{"label": "夕暮れ", "reading": "ゆうぐれ", "source_ref": "first"},
                             {"label": "夕暮れ", "reading": "ゆうぐれ", "source_ref": "second"}],
         "source_atoms": [{"text": " ユーグレイ ", "kind": "unknown"}], "time_phase": "evening"},
        {"catalog_sources": "wrong", "source_atoms": {}, "asr_context_terms": False}]
    builders = []
    for text, materials in [(verse, m) for m in material_sets] + [
        ("", {}), ("\n", {}), ("さくらのは\r\n\r\nあさのいろ\r\n", {}),
        ("\u001cさくらのは\u0085くろいおのへと\u2028あさのいろ\u2029", {}),
        ("桜の葉\n　くろい／おのへと　\n朝の色", {}), ("カタカナ\vまじり\fかたかな\x1eしろいくも", {})]:
        builders.append({"verse": text, "materials": materials,
                         "expected": [asdict(c) for c in workshop_asr_candidates(verse=text, materials=materials)]})
    texts = ["", "それだったらユーグレイヤの方がいいんじゃない?", "自然な子供になるんじゃないですか",
             "サクラノバって何？", "『サクラノハ』と『アサノイロ』と『クロイオノヘト』", "さくらのは",
             "ユウグレ", "ゆうぐれ", "ユーグレイ", "ゆーぐれ", "ユーグレー", "ﾕｰｸﾞﾚ", "あさ", "アサ",
             "🐕：ユーグレイヤ。\nユウグレ！", "あーアアアーああああ", "さくらのぱって何？", "終了しないで"]
    for reading in ["ユウグレ", "サクラノハ", "ネザライトノオノ"]:
        for i in range(len(reading)):
            texts.extend([reading[:i] + "バ" + reading[i+1:], reading[:i] + reading[i+1:], reading[:i] + "イ" + reading[i:]])
    candidate_sets = [[], [Candidate("夕暮れ", ("ゆうぐれ",), "time_phase:evening")],
        [Candidate("夕暮れ", ("ゆうぐれ",), "a"), Candidate("優暮れ", ("ゆうぐれ",), "b")],
        [Candidate("夕暮れ", ("ゆうぐれ", "ユウグレ"), "first"), Candidate("夕暮れ", ("ゆうぐれ",), "duplicate")],
        [Candidate(" 夕暮れ ", ("ゆうぐれ",), "untrimmed"), Candidate("　", ("ゆうぐれ",), "empty")],
        [Candidate("ネザライトの斧", ("ねざらいとのおの",), "item:axe")],
        list(workshop_asr_candidates(verse=verse, materials={"time_phase":"evening"}))]
    corrections = []
    for index, cs in enumerate(candidate_sets):
        for text in dict.fromkeys(texts):
            fixed, applied = apply_candidate_asr_fixes(text, cs)
            corrections.append({"text":text, "candidate_set":index, "maximum":2,
                                "expected":fixed, "corrections":[asdict(c) for c in applied]})
    for maximum in [0, 1, 3]:
        text = "ユウグレ、ユウグレ、ユウグレ"
        cs = candidate_sets[1]
        fixed, applied = apply_candidate_asr_fixes(text, cs, max_corrections=maximum)
        corrections.append({"text":text, "candidate_set":1, "maximum":maximum,
                            "expected":fixed, "corrections":[asdict(c) for c in applied]})
    normalizations = [{"text":t, "expected":normalize_spoken_kana(t)} for t in
        [*texts, "ぁぃぅぇぉゎゃゅょっゕゖ", "ァィゥェォヮャュョッヵヶヷヸヹヺ", "か\u3099き\u3099く\u3099", "ーんーっー", "ｳﾞｧﾘｱﾝﾄ", "㍍㌔ABC"]]
    path = ROOT / "dogido-rust/fixtures/contextual-asr.json"
    payload = {"candidate_sets":[[asdict(c) for c in cs] for cs in candidate_sets],
               "builders":builders, "corrections":corrections, "normalizations":normalizations}
    # 1ケース1行にし、候補配列は共有して差分を読みやすくする。
    path.write_text("{\n" + ",\n".join(json.dumps(key)+": [\n" + ",\n".join(
        json.dumps(row, ensure_ascii=False) for row in rows) + "\n]" for key, rows in payload.items()) + "\n}\n")
    print(f"{len(builders)} builders / {len(corrections)} corrections / {len(normalizations)} normalizations")


if __name__ == "__main__":
    main()
