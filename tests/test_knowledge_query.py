"""明示知識質問の抽出、出典境界、fail-closed。"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_type_hints
import unittest
import unicodedata

from dogido_server.knowledge_query import (
    LocalKnowledgeProvider,
    extract_explicit_knowledge_query,
    render_knowledge_reply,
    render_knowledge_reply_plan,
    split_knowledge_speech,
)
from dogido_server.knowledge_subjects import (
    CURATED_JAPANESE_SUBJECTS,
    CURATED_POETRY_SUBJECTS,
    GRAMMAR_PATTERN_EXACT_SUBJECTS,
)
from dogido_server.minecraft_knowledge import DEFAULT_CACHE_ROOT
from dogido_server.player_input.types import PlayerInputContext


ROOT = Path(__file__).resolve().parents[1]


class ExplicitKnowledgeQueryTests(unittest.TestCase):
    def test_supported_questions_extract_only_spoken_subject(self) -> None:
        cases = {
            "枕詞って何？": ("japanese_language", "枕詞", "definition"),
            "川柳の決まりを教えて": ("poetry", "川柳", "rules"),
            "ソネットの形式は？": ("poetry", "ソネット", "rules"),
            "ソネットとは？": ("poetry", "ソネット", "definition"),
            "「一」は何年生で習う漢字？": ("japanese_language", "一", "grade"),
            "漢字の一は何年生で習うの？": ("japanese_language", "一", "grade"),
            "一二三の一は何年生で習うの？": ("japanese_language", "一", "grade"),
            "漢字の3は何年生で習うの？": ("japanese_language", "3", "grade"),
            "じゃあ数字の4は漢字で何年生で習うのかな": (
                "japanese_language",
                "4",
                "grade",
            ),
            "数字の4の漢字って": ("japanese_language", "4", "definition"),
            "一の読みを教えて": ("japanese_language", "一", "reading"),
            "doMobSpawningは1.21.11でどう変わった？": (
                "minecraft",
                "doMobSpawning",
                "change",
            ),
            "ダイヤモンドの剣の耐久値は？": (
                "minecraft",
                "ダイヤモンドの剣",
                "properties",
            ),
            "ダイヤモンドの剣のIDは？": (
                "minecraft",
                "ダイヤモンドの剣",
                "identifier",
            ),
            "～間の接続は？": ("japanese_language", "~間", "rules"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                query = extract_explicit_knowledge_query(text)
                self.assertIsNotNone(query)
                assert query is not None
                self.assertEqual(expected, (query.domain, query.subject, query.intent))
                normalized_subject = (
                    query.subject.casefold().replace("~", "～").strip("「」")
                )
                normalized_text = text.casefold().replace("〜", "～")
                self.assertIn(normalized_subject, normalized_text)

    def test_non_questions_commands_and_workshop_fragments_are_not_claimed(self) -> None:
        cases = (
            "枕詞ってええ響きやな",
            "川柳を作って",
            "doMobSpawningを設定した",
            "/say 枕詞って何？",
            "剣ある？",
            "今の音なに？",
            "敵何体？",
            "ドラゴンどこ？",
            "ダイヤモンドの剣に持ち替えて",
            "平べったって何だろうか",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNone(extract_explicit_knowledge_query(text))

    def test_general_colon_syntax_is_not_claimed_as_minecraft(self) -> None:
        for text in (
            "https://openai.comって何？",
            "12:30って何？",
            "A:Bって何？",
            "urn:isbn:123って何？",
            "examplemod:widgetって何？",
            "minecraft:/diamond_swordって何？",
        ):
            with self.subTest(text=text):
                self.assertIsNone(extract_explicit_knowledge_query(text))

        vanilla = extract_explicit_knowledge_query("minecraft:diamond_swordって何？")
        self.assertIsNotNone(vanilla)
        assert vanilla is not None
        self.assertEqual("minecraft", vanilla.domain)

        modded = extract_explicit_knowledge_query(
            "Minecraftでexamplemod:widgetって何？"
        )
        self.assertIsNotNone(modded)
        assert modded is not None
        self.assertEqual("minecraft", modded.domain)

    def test_domain_detection_does_not_steal_unrelated_explicit_questions(self) -> None:
        japanese = extract_explicit_knowledge_query("変体仮名はどう変わった？")
        poetry = extract_explicit_knowledge_query("ソネットはどう変わった？")

        self.assertIsNotNone(japanese)
        self.assertIsNotNone(poetry)
        assert japanese is not None
        assert poetry is not None
        self.assertEqual("japanese_language", japanese.domain)
        self.assertEqual("poetry", poetry.domain)
        self.assertIsNone(extract_explicit_knowledge_query("ChatGPTって何？"))

    def test_aspect_questions_extract_the_subject_without_the_aspect(self) -> None:
        cases = {
            "川柳の決まりって何？": ("川柳", "rules"),
            "枕詞の意味って何？": ("枕詞", "definition"),
            "ソネットの形式って何？": ("ソネット", "rules"),
            "doMobSpawningの変更点って何？": ("doMobSpawning", "change"),
            "ダイヤモンドの剣の耐久値って何？": ("ダイヤモンドの剣", "properties"),
            "川柳の決まりについて教えて": ("川柳", "rules"),
            "枕詞とは何か教えて": ("枕詞", "definition"),
            "枕詞ってどんな意味？": ("枕詞", "definition"),
            "川柳ってどういう決まり？": ("川柳", "rules"),
            "川柳にはどんな決まりがある？": ("川柳", "rules"),
            "俳句ではどういう規則があるの？": ("俳句", "rules"),
            "ソネットにおいてはどのような形式がありますか？": ("ソネット", "rules"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                query = extract_explicit_knowledge_query(text)
                self.assertIsNotNone(query)
                assert query is not None
                self.assertEqual(expected, (query.subject, query.intent))

    def test_incomplete_noun_phrases_do_not_trigger_a_lookup(self) -> None:
        for text in (
            "川柳の形式。",
            "川柳の形式は",
            "枕詞の意味を",
            "ダイヤモンドの剣の耐久値。",
            "～間の接続を",
            "川柳にはどんな決まりが",
        ):
            with self.subTest(text=text):
                self.assertIsNone(extract_explicit_knowledge_query(text))

    def test_all_curated_entry_titles_reach_the_expected_domain(self) -> None:
        datasets = {
            "japanese_grammar.json": "japanese_language",
            "historical_kana_and_scripts.json": "japanese_language",
            "makurakotoba.json": "japanese_language",
            "japanese_poetry_forms.json": "poetry",
            "world_poetry.json": "poetry",
        }
        base = ROOT / "reference" / "language_education_and_poetry"
        provider = LocalKnowledgeProvider()
        japanese_subjects: set[str] = set()
        poetry_subjects: set[str] = set()
        for filename, expected_domain in datasets.items():
            payload = json.loads((base / filename).read_text(encoding="utf-8"))
            for entry in payload["entries"]:
                subjects = [entry["title_ja"], *entry.get("aliases", [])]
                target = (
                    japanese_subjects
                    if expected_domain == "japanese_language"
                    else poetry_subjects
                )
                target.update(subjects)
                for subject in subjects:
                    with self.subTest(filename=filename, subject=subject):
                        query = extract_explicit_knowledge_query(f"{subject}って何？")
                        self.assertIsNotNone(query)
                        assert query is not None
                        self.assertEqual(expected_domain, query.domain)
                        result = provider.lookup(query)
                        if subject == "俳諧の連歌":
                            self.assertEqual("not_found", result.status)
                        else:
                            self.assertEqual("found", result.status)
        self.assertEqual(japanese_subjects, set(CURATED_JAPANESE_SUBJECTS))
        self.assertEqual(poetry_subjects, set(CURATED_POETRY_SUBJECTS))

    def test_curated_terms_do_not_match_inside_unrelated_subjects(self) -> None:
        for text in (
            "ChatGPTの活用って何？",
            "AIの活用って何？",
            "リリックビデオって何？",
            "自由詩人って何？",
            "短歌大会って何？",
            "エピックゲームズって何？",
        ):
            with self.subTest(text=text):
                self.assertIsNone(extract_explicit_knowledge_query(text))

    def test_all_grammar_pattern_titles_are_preserved_exactly(self) -> None:
        path = (
            ROOT
            / "reference"
            / "language_education_and_poetry"
            / "data"
            / "normalized"
            / "grammar_patterns.jsonl"
        )
        titles = [
            json.loads(line)["title_ja"]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(800, len(titles))
        self.assertEqual(
            set(GRAMMAR_PATTERN_EXACT_SUBJECTS),
            {
                title
                for title in titles
                if not title.startswith(("～", "〜", "~"))
            },
        )

        def compact(value: str) -> str:
            return "".join(
                unicodedata.normalize("NFKC", value)
                .replace("～", "~")
                .replace("〜", "~")
                .casefold()
                .split()
            )

        for title in titles:
            with self.subTest(title=title):
                query = extract_explicit_knowledge_query(f"{title}の接続は？")
                self.assertIsNotNone(query)
                assert query is not None
                self.assertEqual("japanese_language", query.domain)
                self.assertEqual(compact(title), compact(query.subject))

    def test_player_input_type_hints_resolve_at_runtime(self) -> None:
        hints = get_type_hints(PlayerInputContext)
        self.assertIn("knowledge_query", hints)


class LocalKnowledgeProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = LocalKnowledgeProvider()

    def lookup(self, text: str):  # type: ignore[no-untyped-def]
        query = extract_explicit_knowledge_query(text)
        self.assertIsNotNone(query)
        assert query is not None
        return self.provider.lookup(query, limit=3)

    def test_definition_prefers_the_main_record_over_term_only_examples(self) -> None:
        result = self.lookup("枕詞って何？")
        self.assertEqual("found", result.status)
        self.assertEqual(1, len(result.facts))
        fact = result.facts[0]
        self.assertEqual("knowledge.rhetoric.makurakotoba", fact.record_id)
        self.assertEqual("editorial_synthesis", fact.claim_status)
        self.assertIn("特定の語を導く", fact.text_ja)
        self.assertEqual(["文部科学省"], [source.citation_label_ja for source in fact.sources])
        self.assertTrue(fact.sources[0].url.startswith("https://www.mext.go.jp/"))

    def test_senryu_rules_keep_source_and_editorial_boundaries(self) -> None:
        result = self.lookup("川柳の決まりを教えて")
        self.assertEqual("found", result.status)
        self.assertEqual(3, len(result.facts))
        self.assertEqual(
            ["source_stated", "editorial_guardrail", "editorial_guardrail"],
            [fact.claim_status for fact in result.facts],
        )
        rendered = render_knowledge_reply(result)
        self.assertIn("五・七・五の十七拍", rendered)
        self.assertIn("資料を基にした注意点として", rendered)
        self.assertNotIn("国文学研究資料館", rendered)
        plan = render_knowledge_reply_plan(result)
        self.assertIn(
            "国文学研究資料館",
            [source.citation_label_ja for source in plan.references],
        )

    def test_render_plan_keeps_the_canonical_text_exactly(self) -> None:
        result = self.lookup("枕詞って何？")
        plan = render_knowledge_reply_plan(result)

        self.assertEqual(render_knowledge_reply(result), plan.text)
        self.assertEqual(plan.text, "".join(plan.speech_segments))
        self.assertEqual(
            "枕詞かいな。枕詞っちゅうのは和歌で特定の言葉につながる定型的な言葉のことやで。"
            "教科書や資料集に色々書いてあるで！気になったらみてみよか！",
            plan.text,
        )
        self.assertEqual(4, len(plan.speech_segments))
        self.assertNotIn("資料を基に整理すると", plan.text)
        self.assertNotIn("五音", plan.text)
        self.assertNotIn("参照資料は", plan.text)
        self.assertEqual(["文部科学省"], [source.citation_label_ja for source in plan.references])
        self.assertNotIn("文部科学省", plan.text)
        self.assertEqual(("句点がない回答",), split_knowledge_speech("句点がない回答"))

    def test_kanji_grade_and_reading_keep_separate_official_tables(self) -> None:
        grade = self.lookup("「一」は何年生で習う漢字？")
        reading = self.lookup("一の読みを教えて")
        self.assertEqual("found", grade.status)
        self.assertEqual("found", reading.status)
        self.assertIn("「一」は小学校第1学年の配当漢字", grade.facts[0].text_ja)
        self.assertEqual("文部科学省", grade.facts[0].sources[0].citation_label_ja)
        self.assertEqual("文化庁", reading.facts[0].sources[0].citation_label_ja)
        self.assertNotEqual(
            grade.facts[0].sources[0].source_id,
            reading.facts[0].sources[0].source_id,
        )

    def test_numeric_kanji_grade_query_asks_for_clarification(self) -> None:
        for text, digit in (
            ("漢字の3は何年生で習うの？", "3"),
            ("じゃあ数字の4は漢字で何年生で習うのかな", "4"),
            ("数字の4の漢字って", "4"),
        ):
            with self.subTest(text=text):
                result = self.lookup(text)
                self.assertEqual("not_found", result.status)
                self.assertEqual("ambiguous_kanji_numeric_notation", result.error_code)
                self.assertEqual(
                    f"「{digit}」だけやと、どの漢字か決められへんわ。"
                    "「一二三の一」みたいに言うてみてな。",
                    render_knowledge_reply(result),
                )

    def test_kanji_reading_does_not_silently_drop_official_readings(self) -> None:
        result = self.lookup("生の読みを教えて")
        self.assertEqual("found", result.status)
        self.assertIn("き・なま", result.facts[0].text_ja)

    def test_historical_kana_reading_uses_historical_kana_records(self) -> None:
        result = self.lookup("ゐの読みは？")
        self.assertEqual("found", result.status)
        self.assertIn("現代語の検索候補として『い』", result.facts[0].text_ja)

        for question in ("ワ行イ段って何？", "わぎょういだんって何？"):
            with self.subTest(question=question):
                spoken = self.lookup(question)
                self.assertEqual("found", spoken.status)
                self.assertIn("原文の『ゐ』は保存", spoken.facts[0].text_ja)

    def test_official_kanji_table_definitions_are_queryable(self) -> None:
        joyo = self.lookup("常用漢字表って何？")
        allocation = self.lookup("学年別漢字配当表って何？")
        self.assertEqual("found", joyo.status)
        joyo_plan = render_knowledge_reply_plan(joyo)
        allocation_plan = render_knowledge_reply_plan(allocation)
        self.assertNotIn("文化庁", joyo_plan.text)
        self.assertNotIn("文部科学省", allocation_plan.text)
        self.assertEqual("文化庁", joyo_plan.references[0].citation_label_ja)
        self.assertEqual("文部科学省", allocation_plan.references[0].citation_label_ja)
        self.assertEqual("found", allocation.status)
        self.assertIn("2,136字", joyo.facts[0].text_ja)
        self.assertIn("文化庁", joyo.facts[0].sources[0].citation_label_ja)
        self.assertIn("1,026字", allocation.facts[0].text_ja)
        self.assertIn("文部科学省", allocation.facts[0].sources[0].citation_label_ja)

    def test_world_poetry_classification_uses_formal_japanese_labels(self) -> None:
        result = self.lookup("ソネットの分類を教えて")
        self.assertEqual("found", result.status)
        self.assertEqual(1, len(result.facts))
        text = result.facts[0].text_ja
        self.assertIn("分類単位は詩形", text)
        self.assertIn("表現様式は抒情", text)
        self.assertIn("形式上の区分は定型", text)
        self.assertNotIn("expression_modes", text)

    def test_every_world_poetry_title_reaches_its_classification_record(self) -> None:
        payload = json.loads(
            (
                ROOT
                / "reference"
                / "language_education_and_poetry"
                / "world_poetry.json"
            ).read_text(encoding="utf-8")
        )
        for entry in payload["entries"]:
            with self.subTest(title=entry["title_ja"]):
                result = self.lookup(f"{entry['title_ja']}の分類を教えて")
                self.assertEqual("found", result.status)
                self.assertEqual(entry["id"], result.facts[0].record_id)

    def test_grammar_pattern_is_compact_and_does_not_include_examples(self) -> None:
        result = self.lookup("～間の接続は？")
        self.assertEqual("found", result.status)
        self.assertEqual(1, len(result.facts))
        text = result.facts[0].text_ja
        self.assertIn("接続は", text)
        self.assertLess(len(text), 220)
        self.assertNotIn("シュミット", text)

    def test_grammar_definition_uses_official_usage_without_examples(self) -> None:
        for pattern in (
            "いたします",
            "可能の形  （～れる・～られる）",
            "～たら？",
            "～について",
            "～に関して",
            "～ば？",
        ):
            with self.subTest(pattern=pattern):
                result = self.lookup(f"{pattern}って何？")
                self.assertEqual("found", result.status)
                self.assertIn("意味・用法は", result.facts[0].text_ja)
                self.assertEqual(pattern.replace("〜", "～"), result.facts[0].title_ja.replace("〜", "～"))
                self.assertNotIn("シュミット", result.facts[0].text_ja)

    def test_duplicate_grammar_titles_return_distinct_official_usages(self) -> None:
        result = self.lookup("～そうだって何？")
        self.assertEqual("found", result.status)
        self.assertEqual(2, len(result.facts))
        self.assertEqual(2, len({fact.record_id for fact in result.facts}))
        combined = "".join(fact.text_ja for fact in result.facts)
        self.assertIn("伝聞", combined)
        self.assertIn("外見", combined)

        for title in ("～のに", "～ために"):
            with self.subTest(title=title):
                same_title = self.lookup(f"{title}って何？")
                self.assertEqual("found", same_title.status)
                self.assertTrue(same_title.facts)
                self.assertTrue(
                    all(fact.title_ja == title for fact in same_title.facts)
                )

    def test_grammar_wave_dash_variants_resolve_to_the_same_records(self) -> None:
        for title in ("～そうだ", "～のに", "～あげく"):
            with self.subTest(title=title):
                record_ids = []
                for wave in ("~", "～", "〜"):
                    result = self.lookup(f"{title.replace('～', wave)}って何？")
                    self.assertEqual("found", result.status)
                    self.assertTrue(result.facts)
                    record_ids.append(tuple(fact.record_id for fact in result.facts))
                self.assertEqual(record_ids[0], record_ids[1])
                self.assertEqual(record_ids[0], record_ids[2])

    def test_long_grammar_connections_are_never_silently_removed(self) -> None:
        for pattern in (
            "～も～ば～も",
            "～です",
            "～だろうと～うと",
            "～だろうが～うが",
            "〜だの～だの",
            "～ことは〜",
        ):
            with self.subTest(pattern=pattern):
                result = self.lookup(f"{pattern}の接続は？")
                self.assertEqual("found", result.status)
                self.assertIn("接続は", result.facts[0].text_ja)
                self.assertLessEqual(len(result.facts[0].text_ja), 220)

    @unittest.skipUnless(
        (DEFAULT_CACHE_ROOT / "current.json").is_file(),
        "ローカルMinecraft公式DBがない環境では省略",
    )
    def test_minecraft_facts_always_state_version_and_recipe_scope(self) -> None:
        for text in (
            "doMobSpawningって何？",
            "minecraft:attack_rangeはどう変わった？",
            "ダイヤモンドの剣の耐久値は？",
            "ダイヤモンドの剣のIDは？",
        ):
            with self.subTest(text=text):
                result = self.lookup(text)
                self.assertEqual("found", result.status)
                self.assertTrue(all("1.21.11" in fact.text_ja for fact in result.facts))

        change = render_knowledge_reply(
            self.lookup("minecraft:attack_rangeはどう変わった？")
        )
        self.assertIn("公式リリースノートを要約すると", change)
        self.assertIn("主要変更の抜粋", change)

        recipe = self.lookup("minecraft:diamond_swordの作り方を教えて")
        self.assertEqual("found", recipe.status)
        self.assertEqual(1, len(recipe.facts))
        self.assertIn(".recipe.", recipe.facts[0].record_id)
        self.assertNotIn("advancement", recipe.facts[0].record_id)
        rendered = render_knowledge_reply(recipe)
        self.assertIn("配置の詳細は縮約データにないため推測しません", rendered)
        self.assertLessEqual(len(rendered), 420)

        for ingredient in (
            "minecraft:sugar",
            "minecraft:flint",
            "minecraft:amethyst_shard",
        ):
            with self.subTest(ingredient=ingredient):
                self.assertEqual(
                    "not_found",
                    self.lookup(f"{ingredient}の作り方を教えて").status,
                )

        for versioned_subject in (
            "MinecraftでData Pack 94.1って何？",
            "MinecraftでResource Pack 75.0って何？",
            "Minecraft Server Management Protocol 2.0.0って何？",
        ):
            with self.subTest(versioned_subject=versioned_subject):
                self.assertEqual("found", self.lookup(versioned_subject).status)

    def test_explicit_unsupported_minecraft_version_fails_closed(self) -> None:
        for text in (
            "doMobSpawningは1.20.1でどう変わった？",
            "doMobSpawningは1.20.1版でどう変わった？",
            "1.20.1版のdoMobSpawningはどう変わった？",
            "Minecraft 1.21.10でdoMobSpawningはどう変わった？",
            "Java Edition 1.20.6でdoMobSpawningはどう変わった？",
        ):
            with self.subTest(text=text):
                result = self.lookup(text)
                self.assertEqual("not_found", result.status)
                self.assertEqual("unsupported_minecraft_version", result.error_code)
                self.assertIn("Java Edition 1.21.11専用", render_knowledge_reply(result))

        current = extract_explicit_knowledge_query(
            "Minecraft 1.21.11版でdoMobSpawningはどう変わった？"
        )
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual("doMobSpawning", current.subject)

        for artifact_version in (
            "MinecraftのData Packは94.1でどう変わった？",
            "MinecraftのResource Packは75.0でどう変わった？",
            "Minecraftの管理プロトコルは2.0.0でどう変わった？",
        ):
            with self.subTest(artifact_version=artifact_version):
                query = extract_explicit_knowledge_query(artifact_version)
                self.assertIsNotNone(query)
                assert query is not None
                result = self.provider.lookup(query)
                self.assertNotEqual(
                    "unsupported_minecraft_version",
                    result.error_code,
                )

    def test_missing_data_is_unavailable_and_never_guessed(self) -> None:
        with TemporaryDirectory() as temporary:
            provider = LocalKnowledgeProvider(
                language_reference_dir=Path(temporary) / "missing"
            )
            query = extract_explicit_knowledge_query("枕詞って何？")
            assert query is not None
            result = provider.lookup(query)
        self.assertEqual("unavailable", result.status)
        self.assertEqual((), result.facts)
        rendered = render_knowledge_reply(result)
        self.assertIn("公式資料を今は読めへん", rendered)
        self.assertIn("推測では答えん", rendered)

    def test_unknown_explicit_subject_is_not_found_and_never_guessed(self) -> None:
        result = self.lookup("国語で幻の枕詞って何？")
        self.assertEqual("not_found", result.status)
        self.assertEqual((), result.facts)
        rendered = render_knowledge_reply(result)
        self.assertIn("確認できへんかった", rendered)
        self.assertIn("推測はせん", rendered)


if __name__ == "__main__":
    unittest.main()
