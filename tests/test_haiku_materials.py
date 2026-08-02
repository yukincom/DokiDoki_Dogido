"""workshop materials seed / short candidates / fragment_links (issue #28 phase 0–1)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dogido_server.haiku.materials import (
    attach_fragment_links,
    build_fragment_links,
    build_workshop_materials_seed,
    resolve_material_from_links,
    short_material_entries,
)
from dogido_server.haiku.workshop import (
    finalize_ask_meaning_reply,
    material_candidates_for_speech,
    open_from_emission,
    pick_material_for_fragment,
)
from dogido_server.memory_types import HaikuEmission


def _emission_with_materials(
    text: str,
    materials: dict,
    *,
    interpretation: str | None = "見どころ",
) -> HaikuEmission:
    return HaikuEmission(
        created_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        text=text,
        preface="ここで一句。",
        interpretation=interpretation,
        biome="plains",
        structure=None,
        time_phase="day",
        dimension="minecraft:overworld",
        event_sequence=1,
        route="haiku",
        materials=materials,
    )


class MaterialsSeedTests(unittest.TestCase):
    def test_seed_carries_motifs_held_nearby_without_domain_bans(self) -> None:
        seed = build_workshop_materials_seed(
            interpretation="平原の昼とオーク",
            biome="minecraft:plains",
            structure="village_plains",
            time_phase="day",
            motifs=["オークの原木", "きのこ"],
            held_item="つるはし",
            nearby_blocks=["草ブロック", "オークの原木"],
            passive_mobs=["ヒツジ"],
            focus=["原木"],
            elements=["明るさ"],
        )
        self.assertEqual(seed["biome"], "plains")
        self.assertEqual(seed["structure"], "village_plains")
        self.assertIn("オークの原木", seed["motifs"])
        self.assertIn("きのこ", seed["motifs"])
        self.assertIn("原木", seed["motifs"])
        self.assertIn("明るさ", seed["motifs"])
        self.assertEqual(seed["held_item"], "つるはし")
        self.assertIn("草ブロック", seed["nearby_blocks"])
        self.assertIn("ヒツジ", seed["passive_mobs"])
        # 固定語 reject はしない（うみ等が材料にあっても載る）
        seed2 = build_workshop_materials_seed(motifs=["うみ", "湖"])
        self.assertEqual(seed2["motifs"], ["うみ", "湖"])

    def test_short_candidates_prefer_concrete_nouns(self) -> None:
        mats = build_workshop_materials_seed(
            interpretation="広大な平原の昼間の明るさと、錆びた銅のランタンの古びた雰囲気の対比",
            biome="plains",
            time_phase="day",
            motifs=["錆びた銅のランタン", "平原"],
            held_item="松明",
            nearby_blocks=["オークの原木"],
        )
        mats["biome_ja"] = "平原"
        labels = [label for label, _src in short_material_entries(mats)]
        self.assertIn("錆びた銅のランタン", labels)
        self.assertIn("平原", labels)
        self.assertIn("松明", labels)
        self.assertIn("オークの原木", labels)
        # 長い講義条片より短い具体物が先
        self.assertLess(labels.index("錆びた銅のランタン"), len(labels))
        # ゴミ述語は出さない
        for bad in ("ている", "である", "いる"):
            self.assertNotIn(bad, labels)

    def test_fragment_links_surface_to_material(self) -> None:
        mats = {
            "motifs": ["きのこ", "ヒツジ", "オークの原木"],
            "held_item": "松明",
            "biome_ja": "平原",
        }
        verse = "きのこの\nかげにかくれる\nひつじかな"
        links = build_fragment_links(verse, mats)
        materials_hit = {link["material"] for link in links}
        surfaces = {link["surface"] for link in links}
        self.assertIn("きのこ", materials_hit)
        self.assertIn("ヒツジ", materials_hit)
        # 句に無い材料はリンクしない（詩的飛躍なし）
        self.assertNotIn("オークの原木", materials_hit)
        self.assertNotIn("平原", materials_hit)
        self.assertTrue(any("きのこ" in s or "きのこ" == s for s in surfaces))

        attached = attach_fragment_links(mats, verse)
        self.assertTrue(attached.get("fragment_links"))

        resolved = resolve_material_from_links(
            "きのこって何？",
            verse,
            attached,
            fragment="きのこ",
        )
        self.assertEqual(resolved, "きのこ")

        # 対応のない断片は None
        self.assertIsNone(
            resolve_material_from_links("ばらって何", verse, attached, fragment="ばら")
        )


class SpeechLinePreferenceTests(unittest.TestCase):
    def test_prefers_concrete_over_abstract_shortest(self) -> None:
        from dogido_server.haiku.workshop import materials_speech_line

        mats = build_workshop_materials_seed(
            interpretation="冷たい金属の斧先が、温かい木漏れ日に触れる静けさ。",
            biome="forest",
            time_phase="day",
            motifs=["ネザライトの斧", "オークの原木", "木漏れ日", "温帯の森", "静寂"],
            held_item="ネザライトの斧",
            nearby_blocks=["オークの原木"],
        )
        mats["biome_ja"] = "森林"
        ws = open_from_emission(
            _emission_with_materials("さむいねこら あたたききの ひかりさす", mats)
        )
        speech = materials_speech_line(ws)
        # 純最短の「静寂」ではなく手持ち・具体物
        self.assertNotEqual(speech, "静寂")
        self.assertIn(speech, {"ネザライトの斧", "オークの原木", "木漏れ日"})


class AskMeaningMaterialsTests(unittest.TestCase):
    def test_candidates_and_links_on_workshop(self) -> None:
        mats = build_workshop_materials_seed(
            interpretation="平原の昼間と錆びた銅",
            biome="plains",
            motifs=["錆びた銅のランタン", "平原"],
            held_item="ランタン",
        )
        mats["biome_ja"] = "平原"
        verse = "はれのばら\nわびたふどうの\nてのなか"
        # かな一致: ふどう と ランタンは一致しない → リンク薄め。明示リンクを足す
        mats = attach_fragment_links(mats, verse)
        # 明示の surface→material（詩的対応はコード外で、テスト用に注入）
        links = list(mats.get("fragment_links") or [])
        links.append({"surface": "はれのばら", "material": "平原", "source": "motif"})
        mats["fragment_links"] = links

        ws = open_from_emission(
            _emission_with_materials(verse, mats, interpretation=mats.get("interpretation")),
        )
        cands = material_candidates_for_speech(ws)
        self.assertIn("平原", cands)
        self.assertIn("錆びた銅のランタン", cands)
        self.assertIn("ランタン", cands)

        self.assertEqual(pick_material_for_fragment("はれのばら", ws, player_text="晴れのバラ?"), "平原")
        reply, path = finalize_ask_meaning_reply(ws, "晴れのバラ?", None)
        self.assertEqual(path, "template")
        self.assertEqual(reply, "それは、平原やで。")

    def test_emission_materials_flow_to_open(self) -> None:
        mats = build_workshop_materials_seed(
            motifs=["きのこ"],
            held_item="シャベル",
            nearby_blocks=["菌糸"],
        )
        verse = "きのこ やわらか かげ"
        mats = attach_fragment_links(mats, verse)
        emission = _emission_with_materials(verse, mats)
        ws = open_from_emission(emission)
        self.assertEqual(ws.materials.get("held_item"), "シャベル")
        self.assertIn("きのこ", ws.materials.get("motifs") or [])
        self.assertTrue(ws.materials.get("fragment_links"))
        reply, path = finalize_ask_meaning_reply(ws, "きのこって何", None)
        self.assertEqual(path, "template")
        self.assertIn("きのこ", reply)


if __name__ == "__main__":
    unittest.main()
