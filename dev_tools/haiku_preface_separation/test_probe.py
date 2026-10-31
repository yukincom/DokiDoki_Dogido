"""見込み: 元材料と前置きを分離し、別々の3材料を各行へ渡せる。
合格: 候補の由来・件数・部分集合・行への対応を検査できる。
範囲: 固定応答による試験用コードの検査。実モデルが意味を守るかは5ケースの内容レビューで別途判定。
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import probe


class IngredientBoundaryTests(unittest.TestCase):
    def atom(self, ref, kind="catalog_label", basis=None):
        return {"source_ref": ref, "kind": kind, "basis_atom_ids": basis or [],
                "text": ref, "claim_class": "factual", "claim_scopes": ["identity_only"]}

    def materials(self):
        return probe.ingredients([self.atom("item:a"), self.atom("item:b"),
                                  self.atom("item:c"), self.atom("item:d")])

    def assignment(self, ids=("m01", "m02", "m03")):
        return {"line_materials": [{"line_index": i, "material_id": ident} for i, ident in enumerate(ids)]}

    # 見込み: 前置きから作った派生文章を第三の材料として使わない。
    # 合格: 元資料一つだけが残る。範囲: 固定atomの候補作成。生成句の意味は検査しない。
    def test_preface_never_enters_ingredient_pool(self):
        atoms = [self.atom("item:dandelion"), self.atom("preface:spoken", "poetic_interpretation"),
                 self.atom("other:preface", "preface_clause"), self.atom("alias", basis=["item:dandelion"]),
                 self.atom("preface:unexpected")]
        self.assertEqual([m["source_ref"] for m in probe.ingredients(atoms)], ["item:dandelion"])

    # 見込み: 時計の名称と説明を二材料と数えない。
    # 合格: 二つの異なる説明を持つ一材料になる。範囲: 同一source_refのまとめだけ。
    def test_same_source_name_and_description_remain_one_ingredient(self):
        a = self.atom("item:clock")
        b = {**a, "text": "太陽と月の絵が描かれた金時計。", "kind": "catalog_fact"}
        result = probe.ingredients([a, b, a])
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["facts"]), 2)

    # 見込み: 前置きの選択が2材料のままなら、三行用の材料が足りないと分かる。
    # 合格: 2材料を拒否し、3材料と4材料は通す。範囲: 固定選択IDの件数のみ。言及内容は別確認。
    def test_preface_requires_at_least_three_materials(self):
        with self.assertRaisesRegex(ValueError, "at least three"):
            probe.preface_materials(self.materials(), {"selected_material_ids": ["m01", "m02"]})
        for ids in [["m01", "m02", "m03"], ["m01", "m02", "m03", "m04"]]:
            self.assertEqual(len(probe.preface_materials(self.materials(), {"selected_material_ids": ids})), len(ids))

    # 見込み: 別IDにした同じ元材料で3材料を装うことはできない。
    # 合格: 3IDでも元出典が2つなら前置き選択を拒否。範囲: source_ref一致による構造検査。
    def test_alias_ids_do_not_inflate_preface_material_count(self):
        materials = self.materials()
        materials[2]["source_ref"] = materials[1]["source_ref"]
        with self.assertRaisesRegex(ValueError, "at least three"):
            probe.preface_materials(materials, {"selected_material_ids": ["m01", "m02", "m03"]})

    # 見込み: JSONの返却順が違っても、モデルが指定した行番号を守る。
    # 合格: 上五=m03、中七=m01、下五=m02の対応が維持される。範囲: 固定応答の整形のみ。
    def test_assignment_preserves_model_chosen_line_positions(self):
        response = self.assignment(("m03", "m01", "m02"))
        response["line_materials"].reverse()
        rows = probe.assign_lines(self.materials(), response)
        self.assertEqual([(r["position"], r["material"]["material_id"]) for r in rows],
                         [("上五", "m03"), ("中七", "m01"), ("下五", "m02")])

    # 見込み: 同じ材料を二行へ割り当てる応答は、3材料条件の成立に数えない。
    # 合格: 重複IDを拒否。範囲: 固定応答。異なる資料の意味的な重複は内容レビューで確認。
    def test_same_material_cannot_fill_two_lines(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            probe.assign_lines(self.materials(), self.assignment(("m01", "m02", "m01")))

    # 見込み: 上五・中七・下五が揃わない配分を成功と扱わない。
    # 合格: 行数不足、重複行、範囲外行、bool行番号を拒否。範囲: 固定JSONの対応検査。
    def test_three_line_positions_are_required(self):
        samples = [self.assignment(("m01", "m02"))]
        for indices in [[0, 0, 2], [0, 1, 3], [False, 1, 2]]:
            r = self.assignment()
            for row, index in zip(r["line_materials"], indices):
                row["line_index"] = index
            samples.append(r)
        for response in samples:
            with self.assertRaises(ValueError):
                probe.assign_lines(self.materials(), response)

    # 見込み: 前置きで選ばなかった材料を後段が勝手に復活させない。
    # 合格: 前置きの3材料にないm04への割当を拒否。範囲: 部分集合の照合のみ。
    def test_later_selection_cannot_restore_an_omitted_ingredient(self):
        selected = probe.preface_materials(self.materials(), {"selected_material_ids": ["m01", "m02", "m03"]})
        with self.assertRaisesRegex(ValueError, "subset"):
            probe.assign_lines(selected, self.assignment(("m01", "m02", "m04")))

    # 見込み: IDの分離を意味の分離の証明と取り違えない。
    # 合格: 朝の観測と朝の会話は構造上通る。この結果を内容合格としない。範囲: 意味を再審査しない境界。
    def test_structural_pass_does_not_claim_semantic_distinctness(self):
        materials = probe.ingredients([self.atom("observation:morning"), self.atom("dialogue:morning"), self.atom("item:torch")])
        self.assertEqual(len(probe.assign_lines(materials, self.assignment())), 3)

    # 見込み: 前置きは材料選択へ漏れず、句生成の調理方針だけに入る。
    # 合格: 選択要求に本文なし。句要求に3つの行別材料と別欄の本文がある。範囲: 前段を固定した要求生成。
    def test_prompts_keep_preface_separate_and_deliver_three_assignments(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            probe.write(out / "source.json", {"experiment": probe.EXPERIMENT, "base": "共有人格",
                        "templates": {"select": probe.SELECT, "poem": probe.POEM},
                        "sampling": {"select": [0.2, 256], "poem": [0.6, 192]}})
            probe.write(out / "cases.json", [{"id": "test", "label": "固定ケース", "materials": self.materials()}])
            preface = "前置き固有の調理方針"
            probe.write(out / "responses/preface/test.json", {"status": "complete", "parsed":
                        {"description": preface, "selected_material_ids": ["m01", "m02", "m03", "m04"]}})
            probe.write(out / "responses/select/test.json", {"status": "complete", "parsed": self.assignment(("m03", "m01", "m02"))})
            with contextlib.redirect_stdout(io.StringIO()):
                probe.prepare(out, "select", "test-model")
                probe.prepare(out, "poem", "test-model")
            selection = probe.read(out / "requests/select/test.json")
            poem = probe.read(out / "requests/poem/test.json")
            self.assertNotIn(preface, selection["payload"]["messages"][1]["content"])
            self.assertIsNone(selection["poetic_direction"])
            self.assertEqual(poem["poetic_direction"], preface)
            self.assertEqual([r["material"]["material_id"] for r in poem["line_assignments"]], ["m03", "m01", "m02"])
            self.assertEqual(len(poem["offered_materials"]), 3)
            self.assertNotIn(preface, probe.canonical(poem["offered_materials"]))
            self.assertIn("【材料の調理方針となる前置き】\n" + preface, poem["payload"]["messages"][1]["content"])

    # 見込み: 2材料しか返さなかったケースの失敗を残し、条件を満たす他ケースは進められる。
    # 合格: 失敗ケースを再実行・補完せず除外し、成功ケースだけ次段へ渡す。範囲: 固定状態の試験進行。
    def test_failed_case_does_not_advance_or_block_other_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            probe.write(out / "cases.json", [{"id": "bad"}, {"id": "good"}])
            probe.write(out / "responses/preface/bad.json", {"status": "failed"})
            probe.write(out / "responses/preface/good.json", {"status": "complete"})
            probe.write(out / "responses/select/good.json", {"status": "complete"})
            self.assertEqual([c["id"] for c in probe.cases_for_stage(out, "preface")], ["bad", "good"])
            self.assertEqual([c["id"] for c in probe.cases_for_stage(out, "select")], ["good"])
            self.assertEqual([c["id"] for c in probe.cases_for_stage(out, "poem")], ["good"])
            self.assertEqual(probe.read(out / "responses/preface/bad.json"), {"status": "failed"})

    # 見込み: 音数が整うことと内容の重複がないことは別である。
    # 合格: 旧反復例にも5/7/6と診断するだけ。範囲: かな音数。自然さや採否の判定はしない。
    def test_kana_counts_are_diagnostic(self):
        self.assertEqual(probe.mora_counts(["あさがくる", "はれのすなはま", "あさのひかり"]), [5, 7, 6])


if __name__ == "__main__":
    unittest.main()
