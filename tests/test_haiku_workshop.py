"""haiku workshop pin / open-close / intent."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dogido_server.config import Settings
from dogido_server.haiku.edit_contract import (
    LINE_EDIT_CONTRACT_VERSION,
    PLAYER_LINE_EDIT_CONTRACT_VERSION,
    line_edit_plan_applies,
)
from dogido_server.haiku.workshop import (
    PlayerLineReplacement,
    WorkshopAnalysis,
    WorkshopFinding,
    WorkshopLineProposal,
    advance_workshop_revision,
    build_player_line_revision,
    build_workshop_intent_llm_details,
    classify_workshop_intent,
    close_confirmation_decision,
    close_workshop,
    extract_player_line_replacement,
    finalize_pending_revision_payload,
    finalize_workshop_analysis_payload,
    is_open,
    is_meaning_acknowledgement,
    is_workshop_hard_off_topic,
    maybe_close_for_time,
    open_from_emission,
    record_drift,
    render_workshop_reply,
    pending_revision_decision,
    pending_revision_is_current,
    parse_player_line_replacement,
    repair_target_indices,
    should_handle_as_workshop,
    update_marked_workshop_line,
    workshop_open_intent,
    wants_show_workshop_verse,
)
from dogido_server.memory import MemoryStore
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import (
    AdapterSessionCreateRequest,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    Certainty,
    TimePhase,
    Weather,
    WorldState,
)
from dogido_server.service import DogidoService
from dogido_server.state_machine import AudioAction


def _emission(text: str = "あさひさす むらに あかがね", *, interpretation: str | None = None) -> HaikuEmission:
    return HaikuEmission(
        created_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        text=text,
        preface="ここで一句。",
        interpretation=interpretation or "平原の村の朝と銅のドア",
        biome="plains",
        structure=None,
        time_phase="day",
        dimension="minecraft:overworld",
        event_sequence=1,
        route="haiku",
    )


class WorkshopLifecycleTests(unittest.TestCase):
    def test_open_and_close(self) -> None:
        ws = open_from_emission(_emission())
        self.assertTrue(is_open(ws))
        self.assertIn("あさひさす", ws.surface_text)
        self.assertIn("平原", ws.materials.get("interpretation", ""))
        close_workshop(ws, reason="explicit")
        self.assertFalse(is_open(ws))
        self.assertEqual(ws.close_reason, "explicit")

    def test_drift_closes_after_two(self) -> None:
        ws = open_from_emission(_emission())
        now = ws.emitted_at
        record_drift(ws, now=now)
        self.assertTrue(is_open(ws))
        closed = record_drift(ws, now=now + timedelta(seconds=1))
        self.assertIsNotNone(closed)
        assert closed is not None
        self.assertFalse(closed.open)
        self.assertEqual(closed.close_reason, "drift")

    def test_timeout_idle(self) -> None:
        ws = open_from_emission(_emission())
        later = ws.emitted_at + timedelta(seconds=200)
        closed = maybe_close_for_time(ws, now=later, t_open=timedelta(seconds=300), t_idle=timedelta(seconds=90))
        self.assertIsNotNone(closed)
        assert closed is not None
        self.assertFalse(closed.open)
        self.assertEqual(closed.close_reason, "timeout_idle")

    def test_pending_revision_is_compare_and_swap_on_the_pinned_verse(self) -> None:
        ws = open_from_emission(_emission("はるのかぜ\nひつじがあるく\nよるのつき"))
        ws.pending_revision = "はるのかぜ\nあめつよくふる\nよるのつき"
        ws.pending_revision_base_text = "はるのかぜ\nひつじがあるく\nよるのつき"
        ws.pending_revision_edits = [{
            "line_index": 1,
            "expected_text": "ひつじがあるく",
            "replacement_text": "あめつよくふる",
            "atom_ids": ["observation:test:3"],
        }]
        ws.pending_revision_edit_contract = "line_compare_and_swap_v1"

        self.assertTrue(pending_revision_is_current(ws))
        ws.surface_text = "はるのかぜ\nべつのぎょうや\nよるのつき"
        self.assertFalse(pending_revision_is_current(ws))


class WorkshopIntentTests(unittest.TestCase):
    def test_classify(self) -> None:
        self.assertEqual(classify_workshop_intent("グーの木の水って何?"), "ask_meaning")
        self.assertEqual(classify_workshop_intent("木木って何ですか?"), "ask_meaning")
        self.assertEqual(classify_workshop_intent("平べったって何だろうか"), "ask_meaning")
        self.assertEqual(classify_workshop_intent("無理やり圧縮しすぎ"), "critique_forced")
        self.assertEqual(classify_workshop_intent("いい句やな"), "praise")
        self.assertEqual(classify_workshop_intent("もうええ"), "close")
        # 納得は ack（「意味」誤爆しない）
        self.assertEqual(classify_workshop_intent("なるほどねそういう意味か"), "ack")
        # 句断片 + 疑問（verse 渡し）
        verse = "はれのばら\nわびたふどうの\nてのなか"
        self.assertEqual(
            classify_workshop_intent("晴れのバラ?", verse=verse),
            "ask_meaning",
        )
        self.assertEqual(
            classify_workshop_intent("バラとは何でしょうか", verse=verse),
            "ask_meaning",
        )
        # 読みメタ + 好み（素材名に依存しない）
        self.assertEqual(
            classify_workshop_intent("こっちの読み方の方がよかったんじゃないかなと思う"),
            "other_haiku",
        )
        # 言い換えパターン（ドメイン語リストは使わない）
        self.assertEqual(
            classify_workshop_intent("AじゃなくてBの方がしっくりくる"),
            "other_haiku",
        )
        # 場面ずれも固有モチーフ名ではなくメタ表現
        self.assertEqual(classify_workshop_intent("場と違うやろ"), "critique_offscene")
        self.assertIsNone(classify_workshop_intent("おはよう"))
        self.assertIsNone(classify_workshop_intent("松明ある？"))
        # 固有モチーフ名だけでは hit しない（リストに載せていない）
        self.assertIsNone(classify_workshop_intent("海やな"))
        self.assertIsNone(classify_workshop_intent("村がある"))
        # Stage1: 実ログ由来の soft マーカー（メタ語のみ）
        self.assertEqual(
            classify_workshop_intent("なかなかいいねでも黒い石炭がちょっと長いかも"),
            "critique_forced",
        )
        self.assertEqual(
            classify_workshop_intent("黒石炭とかにしたらいいんじゃない?"),
            "other_haiku",
        )
        self.assertEqual(
            classify_workshop_intent("じゃあ覚えておいてください"),
            "other_haiku",
        )
        self.assertEqual(
            classify_workshop_intent("どこから来たのかな?"),
            "ask_meaning",
        )
        self.assertEqual(
            classify_workshop_intent("そっち抜くしだったら意味が通じるんじゃない"),
            "other_haiku",  # だったら + 通じる → soft suggest が先
        )
        self.assertEqual(
            classify_workshop_intent("土の抜くっていうのはちょっと日本語としておかしいんじゃないでしょうか"),
            "critique_gibberish",
        )

    def test_soft_default_and_hard_off_topic_gate(self) -> None:
        """Stage2: open 中は soft 既定。hard off-topic だけ chat/drift。"""
        verse = "そらまぶし\nくさむらにうかぶ\nくろいせきたん"
        # マーカー外でも open 中は soft_default
        self.assertEqual(
            workshop_open_intent("いや土ぬくしだよ", verse=verse),
            "soft_default",
        )
        self.assertTrue(should_handle_as_workshop("いや土ぬくしだよ", verse=verse))
        # 明確な別件
        self.assertTrue(is_workshop_hard_off_topic("松明ある？"))
        self.assertIsNone(workshop_open_intent("松明ある？", verse=verse))
        self.assertFalse(should_handle_as_workshop("松明ある？", verse=verse))
        self.assertIsNone(workshop_open_intent("おはよう", verse=verse))
        # マーカー hit は soft_default に落ちない
        self.assertEqual(
            workshop_open_intent("黒石炭とかにしたらいいんじゃない?", verse=verse),
            "other_haiku",
        )

    def test_explicit_state_change_variants(self) -> None:
        for text in (
            "もういいよ",
            "もうええよ",
            "わかったわ",
            "了解しました",
            "OKです",
            "これで終了で",
            "これで終わりにしよう",
            "今日はここまで",
        ):
            with self.subTest(kind="close", text=text):
                self.assertEqual(classify_workshop_intent(text), "close")
        for text in (
            "いい句だと思う",
            "この句はいい句だと思う",
            "好きだと思う",
            "ええ句やと思う",
            "すごくいい句やな",
            "うんいいと思う",
        ):
            with self.subTest(kind="praise", text=text):
                self.assertEqual(classify_workshop_intent(text), "praise")

    def test_llm_intent_payload_is_closed_and_confidence_gated(self) -> None:
        self.assertEqual(
            finalize_workshop_analysis_payload(
                {"intent": "critique_gibberish", "confidence": 0.91}
            ).intent,
            "critique_gibberish",
        )
        self.assertEqual(
            finalize_workshop_analysis_payload(
                {"intent": "critique_gibberish", "confidence": 0.5}
            ).intent,
            "soft_default",
        )
        self.assertEqual(
            finalize_workshop_analysis_payload(
                {"intent": "soft_default", "confidence": 0.95}
            ).intent,
            "soft_default",
        )
        # LLM から workshop 終了や lesson 解除はできない
        self.assertEqual(
            finalize_workshop_analysis_payload(
                {"intent": "close", "confidence": 1.0}
            ).intent,
            "soft_default",
        )
        self.assertEqual(
            finalize_workshop_analysis_payload(
                {"intent": "clear_lessons", "confidence": 1.0}
            ).intent,
            "soft_default",
        )
        self.assertEqual(
            finalize_workshop_analysis_payload(
                {"intent": "praise", "confidence": True}
            ).intent,
            "soft_default",
        )

    def test_analysis_infers_unique_fragment_but_not_an_unknown_line(self) -> None:
        analysis = finalize_workshop_analysis_payload(
            {
                "intent": "request_repair",
                "confidence": 0.95,
                "repair_requested": True,
                "findings": [{
                    "fragment": "くろいせきたん",
                    "problem": "unnatural_japanese",
                    "note": "言い方が不自然",
                    "confidence": 0.9,
                }],
            },
            verse_lines=["そらまぶし", "くさむらにうかぶ", "くろいせきたん"],
        )

        self.assertEqual(repair_target_indices(analysis.findings), (2,))
        self.assertTrue(analysis.repair_requested)

        mismatched = finalize_workshop_analysis_payload(
            {
                "intent": "request_repair",
                "confidence": 0.95,
                "repair_requested": True,
                "findings": [{
                    "line_index": 2,
                    "fragment": "くさむらにうかぶ",
                    "problem": "unnatural_japanese",
                    "note": "二行目の指摘",
                    "confidence": 0.9,
                }],
            },
            verse_lines=["そらまぶし", "くさむらにうかぶ", "くろいせきたん"],
        )
        self.assertEqual(repair_target_indices(mismatched.findings), (1,))

        missing_fragment = finalize_workshop_analysis_payload(
            {
                "intent": "request_repair",
                "confidence": 0.95,
                "repair_requested": True,
                "findings": [{
                    "line_index": 2,
                    "fragment": "",
                    "problem": "meter",
                    "note": "三行目が長い",
                    "confidence": 0.9,
                }],
            },
            verse_lines=["そらまぶし", "くさむらにうかぶ", "くろいせきたん"],
        )
        self.assertEqual(repair_target_indices(missing_fragment.findings), ())

        repeated_fragment = finalize_workshop_analysis_payload(
            {
                "intent": "request_repair",
                "confidence": 0.95,
                "repair_requested": True,
                "findings": [{
                    "line_index": 2,
                    "fragment": "ひかる",
                    "problem": "preference",
                    "note": "光るが重複",
                    "confidence": 0.9,
                }],
            },
            verse_lines=["ひかるあさ", "くさむらにうかぶ", "ひかるいし"],
        )
        self.assertEqual(repair_target_indices(repeated_fragment.findings), ())

    def test_pending_revision_requires_explicit_confirmation(self) -> None:
        self.assertEqual(pending_revision_decision("その案でいこう"), "accept")
        self.assertEqual(pending_revision_decision("やっぱり元のまま"), "reject")
        self.assertIsNone(pending_revision_decision("うん、なるほど"))
        self.assertIsNone(pending_revision_decision("その案ではまだだめ"))
        self.assertIsNone(pending_revision_decision("その案でいい？"))
        self.assertIsNone(pending_revision_decision("元のままじゃなくて、その案で"))
        self.assertIsNone(pending_revision_decision("元のままにする？"))

    def test_os_ai_pending_decision_requires_grounded_high_confidence_evidence(self) -> None:
        accepted = finalize_pending_revision_payload(
            {
                "action": "accept_pending",
                "confidence": 0.94,
                "evidence": "それで完成にしよう",
            },
            player_text="よし、それで完成にしよう",
        )
        self.assertEqual(accepted.action, "accept_pending")

        invented = finalize_pending_revision_payload(
            {
                "action": "accept_pending",
                "confidence": 0.99,
                "evidence": "その案で採用する",
            },
            player_text="まだ少し迷っている",
        )
        self.assertEqual(invented.action, "uncertain")

        low_confidence = finalize_pending_revision_payload(
            {
                "action": "reject_pending",
                "confidence": 0.55,
                "evidence": "元に戻そう",
            },
            player_text="元に戻そう",
        )
        self.assertEqual(low_confidence.action, "uncertain")

    def test_os_ai_line_proposal_must_quote_player_and_locate_current_line(self) -> None:
        player_text = "二行目はくさちひろがるにしてはどうですか"
        analysis = finalize_workshop_analysis_payload(
            {
                "intent": "propose_line_edit",
                "confidence": 0.94,
                "repair_requested": False,
                "findings": [],
                "line_proposal": {
                    "found": True,
                    "line_index": 1,
                    "target_fragment": "ひろがるくさち",
                    "replacement_text": "くさちひろがる",
                    "evidence": "くさちひろがるにしてはどうですか",
                    "confidence": 0.92,
                },
            },
            verse_lines=["つちのくさ", "ひろがるくさち", "ひるのそら"],
            player_text=player_text,
        )
        self.assertIsNotNone(analysis.line_proposal)
        assert analysis.line_proposal is not None
        self.assertEqual(analysis.line_proposal.line_index, 1)
        self.assertEqual(analysis.line_proposal.replacement_text, "くさちひろがる")

        invented = finalize_workshop_analysis_payload(
            {
                "intent": "propose_line_edit",
                "confidence": 0.99,
                "repair_requested": False,
                "findings": [],
                "line_proposal": {
                    "found": True,
                    "target_fragment": "ひろがるくさち",
                    "replacement_text": "みどりのはら",
                    "evidence": player_text,
                    "confidence": 0.99,
                },
            },
            verse_lines=["つちのくさ", "ひろがるくさち", "ひるのそら"],
            player_text=player_text,
        )
        self.assertIsNone(invented.line_proposal)

    def test_generated_and_player_edit_contracts_do_not_cross(self) -> None:
        original = "ゆうぐれの\nてのなかのくさ\nあめふりや"
        revised = "ゆうぐれや\nてのなかのくさ\nあめふりや"
        player_edit = [{
            "line_index": 0,
            "expected_text": "ゆうぐれの",
            "replacement_text": "ゆうぐれや",
            "provenance": "player_explicit",
        }]
        generated_edit = [{
            "line_index": 0,
            "expected_text": "ゆうぐれの",
            "replacement_text": "ゆうぐれや",
            "atom_ids": ["observation:test:0"],
        }]
        self.assertTrue(
            line_edit_plan_applies(
                original_text=original,
                revised_text=revised,
                edit_contract=PLAYER_LINE_EDIT_CONTRACT_VERSION,
                edits=player_edit,
            )
        )
        self.assertFalse(
            line_edit_plan_applies(
                original_text=original,
                revised_text=revised,
                edit_contract=LINE_EDIT_CONTRACT_VERSION,
                edits=player_edit,
            )
        )
        self.assertFalse(
            line_edit_plan_applies(
                original_text=original,
                revised_text=revised,
                edit_contract=PLAYER_LINE_EDIT_CONTRACT_VERSION,
                edits=generated_edit,
            )
        )

    def test_player_line_replacement_is_explicit_and_normalized_by_code(self) -> None:
        replacement = extract_player_line_replacement("上五を夕暮れやに変えた方がいい")
        self.assertEqual(
            replacement,
            PlayerLineReplacement(text="夕暮れや", explicit_line_index=0),
        )
        self.assertEqual(
            extract_player_line_replacement("夕暮れのより夕暮れやの方がいいんじゃないかな"),
            PlayerLineReplacement(text="夕暮れや"),
        )
        self.assertEqual(
            extract_player_line_replacement(
                "穏やかなじゃ4文字だからさ 穏やかなでいいんじゃない"
            ),
            PlayerLineReplacement(text="穏やかな"),
        )
        self.assertEqual(
            extract_player_line_replacement("上五は穏やかなでいいんじゃない"),
            PlayerLineReplacement(text="穏やかな", explicit_line_index=0),
        )
        self.assertIsNone(extract_player_line_replacement("夕暮れやの方がいいとは思わない"))
        self.assertIsNone(extract_player_line_replacement("穏やかなでいいとは思わない"))
        self.assertIsNone(extract_player_line_replacement("穏やかなでいいと言われた"))
        self.assertIsNone(extract_player_line_replacement("夕暮れやに変えてない"))
        self.assertIsNone(extract_player_line_replacement("夕暮れやに変えた方がいいと言われた"))
        self.assertIsNone(extract_player_line_replacement("上五と下五を夕暮れやに変えた方がいい"))
        self.assertEqual(
            parse_player_line_replacement("上五と下五を夕暮れやに変えた方がいい").status,
            "ambiguous",
        )

        ws = open_from_emission(
            _emission(text="ゆうぐれの\nてのなかのくさ\nあめふりや")
        )
        assert replacement is not None
        staged = build_player_line_revision(ws, replacement)
        self.assertEqual(
            staged.text,
            "ゆうぐれや\nてのなかのくさ\nあめふりや",
        )
        self.assertEqual(staged.edits[0]["provenance"], "player_explicit")
        self.assertNotIn("夕暮", staged.text or "")

    def test_player_line_revision_accumulates_other_lines_before_acceptance(self) -> None:
        ws = open_from_emission(
            _emission(text="ゆうぐれの\nてのなかのくさ\nあめふりや")
        )
        first = build_player_line_revision(
            ws,
            PlayerLineReplacement("夕暮れや", explicit_line_index=0),
        )
        assert first.text is not None
        ws.pending_revision = first.text
        ws.pending_revision_base_text = first.base_text
        ws.pending_revision_edits = [dict(edit) for edit in first.edits]
        ws.pending_revision_edit_contract = "player_line_compare_and_swap_v1"
        ws.pending_revision_source = "player_line_confirmed"

        second = build_player_line_revision(
            ws,
            PlayerLineReplacement("雨の夜", explicit_line_index=2),
        )
        self.assertEqual(
            second.text,
            "ゆうぐれや\nてのなかのくさ\nあめのよる",
        )
        self.assertEqual([edit["line_index"] for edit in second.edits], [0, 2])
        self.assertEqual(ws.display_line(), "ゆうぐれの\nてのなかのくさ\nあめふりや")

        ws.pending_revision = second.text
        ws.pending_revision_edits = [dict(edit) for edit in second.edits]
        advance_workshop_revision(ws, revision_id="rev_2")
        self.assertEqual(ws.display_line(), second.text)
        self.assertEqual(ws.current_revision_id, "rev_2")
        self.assertTrue(is_open(ws))
        self.assertIsNone(ws.pending_revision)

    def test_validated_finding_marks_the_next_player_replacement_line(self) -> None:
        ws = open_from_emission(
            _emission(text="ゆうぐれの\nてのなかのくさ\nあめふりや")
        )
        marked = update_marked_workshop_line(
            ws,
            findings=(
                WorkshopFinding(
                    line_index=1,
                    fragment="てのなかのくさ",
                    problem="preference",
                    note="中七を直したい",
                    confidence=0.95,
                ),
            ),
            player_text="真ん中がおかしい",
        )
        self.assertEqual(marked, 1)
        staged = build_player_line_revision(
            ws,
            PlayerLineReplacement("草を握って"),
        )
        self.assertEqual(
            staged.text,
            "ゆうぐれの\nくさをにぎって\nあめふりや",
        )

    def test_player_line_revision_rejects_non_exact_meter_and_ambiguous_target(self) -> None:
        ws = open_from_emission(
            _emission(text="ゆうぐれの\nてのなかのくさ\nあめふりや")
        )
        missing = build_player_line_revision(ws, PlayerLineReplacement("夕暮れや"))
        self.assertIn("missing_target", missing.failure_reasons)
        too_long = build_player_line_revision(
            ws,
            PlayerLineReplacement("夕暮れやん", explicit_line_index=0),
        )
        self.assertIn("meter_not_exact", too_long.failure_reasons)
        self.assertIsNone(too_long.text)

        four_lines = open_from_emission(
            _emission(text="ゆうぐれの\nてのなかのくさ\nあめふりや\nよけいなぎょう")
        )
        invalid = build_player_line_revision(
            four_lines,
            PlayerLineReplacement("夕暮れや", explicit_line_index=0),
        )
        self.assertIn("invalid_verse", invalid.failure_reasons)

        conflict = build_player_line_revision(
            ws,
            PlayerLineReplacement(
                "雨の夜",
                explicit_line_index=0,
                target_fragment="雨降りや",
            ),
        )
        self.assertIn("target_conflict", conflict.failure_reasons)

        repeated = open_from_emission(
            _emission(text="あめふりや\nてのなかのくさ\nあめふりや")
        )
        ambiguous_fragment = build_player_line_revision(
            repeated,
            PlayerLineReplacement("夕暮れや", target_fragment="雨降りや"),
        )
        self.assertIn("ambiguous_target_fragment", ambiguous_fragment.failure_reasons)

    def test_show_workshop_verse_is_a_closed_code_intent(self) -> None:
        self.assertTrue(wants_show_workshop_verse("全体はどうなった？"))
        self.assertTrue(wants_show_workshop_verse("今の句を読んで"))
        self.assertTrue(wants_show_workshop_verse("じゃあどんな句になりましたか？"))
        self.assertFalse(wants_show_workshop_verse("全体的にいい句やな"))

    def test_inflected_tte_is_not_a_meaning_question(self) -> None:
        text = "一の句と二の句が草でかぶってるので気になる"
        self.assertNotEqual(
            classify_workshop_intent(
                text,
                verse="つちのくさ\nひろがるくさち\nひるのそら",
            ),
            "ask_meaning",
        )

    def test_negative_praise_is_not_classified_as_praise(self) -> None:
        self.assertNotEqual(classify_workshop_intent("いい句じゃない"), "praise")
        self.assertNotEqual(classify_workshop_intent("これは好きじゃない"), "praise")
        for text in (
            "いい句？",
            "これはうまいとは思わない",
            "いい句とは思わん",
            "好きとは言えない",
            "好きとは言えん",
            "気に入ったわけじゃない",
            "そのままでいいとは思わない",
            "好き嫌いが分かれる句やな",
        ):
            with self.subTest(text=text):
                self.assertNotEqual(classify_workshop_intent(text), "praise")

    def test_close_requires_an_unnegated_statement(self) -> None:
        for text in (
            "まだわかった気がしない",
            "もういいとは思わない",
            "もういいとは思わん",
            "OKじゃない",
            "わかった？",
            "これで終了ではない",
            "これで終わり？",
        ):
            with self.subTest(text=text):
                self.assertNotEqual(classify_workshop_intent(text), "close")

    def test_meaning_ack_and_close_confirmation_fallbacks_are_closed(self) -> None:
        for text in ("そうなんだ", "そうなんやね", "なるほど", "わかったよ"):
            with self.subTest(text=text):
                self.assertTrue(is_meaning_acknowledgement(text))
        for text in ("そうなんだ？", "まだわからない", "そうなんだと言われた"):
            with self.subTest(text=text):
                self.assertFalse(is_meaning_acknowledgement(text))

        self.assertEqual(close_confirmation_decision("うん"), "accept")
        self.assertEqual(close_confirmation_decision("ここまでにしよう"), "accept")
        self.assertEqual(close_confirmation_decision("まだ続けたい"), "continue")
        self.assertEqual(close_confirmation_decision("もう少し"), "continue")
        self.assertIsNone(close_confirmation_decision("うん？"))
        self.assertIsNone(close_confirmation_decision("別の行も気になる"))

    def test_repair_requires_an_explicit_request(self) -> None:
        self.assertEqual(classify_workshop_intent("そこ直して"), "request_repair")
        self.assertEqual(classify_workshop_intent("三行目を直せる？"), "request_repair")
        self.assertNotEqual(classify_workshop_intent("直してほしいわけじゃない"), "request_repair")
        self.assertNotEqual(classify_workshop_intent("直してない"), "request_repair")

    def test_llm_intent_details_are_short_and_do_not_include_raw_materials(self) -> None:
        ws = open_from_emission(
            _emission(text="そらまぶし\nくさむらにうかぶ\nくろいせきたん"),
            materials={
                "biome": "plains",
                "biome_ja": "平原",
                "motifs": ["黒い石炭"],
                "secret_debug_key": "分類器へ渡さない",
            },
        )
        details = build_workshop_intent_llm_details(ws, "言葉を詰めすぎた感じがする")
        self.assertIn("そらまぶし", str(details["verse"]))
        self.assertEqual(details["player_text"], "言葉を詰めすぎた感じがする")
        self.assertNotIn("secret_debug_key", details)
        self.assertNotIn("分類器へ渡さない", str(details))
        self.assertEqual(details["conversation_stage"], "discussion")

        ws.awaiting_meaning_ack = True
        details = build_workshop_intent_llm_details(ws, "そうなんだ")
        self.assertEqual(details["conversation_stage"], "meaning_explained")

        ws.awaiting_close_confirmation = True
        details = build_workshop_intent_llm_details(ws, "うん")
        self.assertEqual(details["conversation_stage"], "close_confirmation")

    def test_llm_intent_prompt_is_registered_as_json_classifier(self) -> None:
        from dogido_server.llm import StructuredGenerationRequest
        from dogido_server.llm.prompts import build_messages

        messages = build_messages(
            StructuredGenerationRequest(
                kind="haiku_workshop_intent",
                fallback_value={"intent": "soft_default", "confidence": 0.0},
                details={
                    "verse": "そらまぶし くさむらにうかぶ くろいせきたん",
                    "materials_speech": "平原",
                    "player_text": "言葉を詰めすぎた感じがする",
                    "allowed_intents": ["critique_forced", "other_haiku"],
                },
            )
        )
        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn("JSON", messages[0]["content"])
        self.assertIn("critique_forced", messages[1]["content"])
        self.assertIn("close、lesson解除", messages[1]["content"])

    def test_pending_decision_prompt_is_registered_as_json_classifier(self) -> None:
        from dogido_server.llm import StructuredGenerationRequest
        from dogido_server.llm.prompts import build_messages

        messages = build_messages(
            StructuredGenerationRequest(
                kind="haiku_workshop_pending_decision",
                fallback_value={
                    "action": "uncertain",
                    "confidence": 0.0,
                    "evidence": "",
                },
                details={
                    "current_verse": "はるのかぜ\nひつじがあるく\nよるのつき",
                    "pending_verse": "はるのかぜ\nあめつよくふる\nよるのつき",
                    "player_text": "よし、それで完成にしよう",
                    "allowed_actions": ["accept_pending", "uncertain"],
                },
            )
        )

        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn("JSON", messages[0]["content"])
        self.assertIn("accept_pending", messages[1]["content"])
        self.assertIn("疑問、条件付き肯定", messages[1]["content"])

    def test_remember_template_reply(self) -> None:
        ws = open_from_emission(_emission())
        reply = render_workshop_reply(
            "other_haiku",
            ws,
            player_text="じゃあ覚えておいてください",
        )
        self.assertIn("覚え", reply)

    def test_reply_fragment_picks_material(self) -> None:
        from dogido_server.haiku.workshop import (
            finalize_ask_meaning_reply,
            material_candidates_for_speech,
            materials_speech_line,
            pick_material_for_fragment,
        )

        ws = open_from_emission(
            _emission(text="はれのばら\nわびたふどうの\nてのなか"),
            materials={
                "interpretation": "広大な平原の昼間の明るさと、錆びた銅のランタンの古びた雰囲気の対比",
                "biome": "plains",
                "biome_ja": "平原",
                "structure": "village_plains",
                "structure_ja": "平原の村",
                "motifs": ["錆びた銅のランタン", "平原", "昼"],
            },
        )

        speech = materials_speech_line(ws)
        self.assertNotIn("biome:", speech)
        self.assertNotIn("plains", speech)
        cands = material_candidates_for_speech(ws)
        self.assertIn("平原", cands)
        self.assertIn("錆びた銅のランタン", cands)

        # LLM 経路: pick + 柔軟な言い回し
        plains_idx = cands.index("平原")
        reply, path = finalize_ask_meaning_reply(
            ws,
            "晴れのバラ?",
            {"pick_index": plains_idx, "reply": "平原のことやで。"},
        )
        self.assertEqual(path, "llm")
        self.assertEqual(reply, "平原のことやで。")
        self.assertNotIn("biome", reply)

        # 言い回しが空でも pick があればテンプレ
        reply_t, path_t = finalize_ask_meaning_reply(
            ws,
            "バラとは何でしょうか",
            {"pick_index": plains_idx, "reply": ""},
        )
        self.assertEqual(path_t, "template")
        self.assertEqual(reply_t, "それは、平原やで。")

        # meta 漏れは落とす
        reply_bad, path_bad = finalize_ask_meaning_reply(
            ws,
            "晴れのバラ?",
            {"pick_index": plains_idx, "reply": "biome: plains やで"},
        )
        self.assertEqual(path_bad, "template")
        self.assertEqual(reply_bad, "それは、平原やで。")

        # 部分一致フォールバック（LLM なし）: 「平原」が fragment に含まれる場合のみ
        self.assertEqual(pick_material_for_fragment("平原", ws), "平原")
        # 詩的対応はコードでは当てない
        self.assertIsNone(pick_material_for_fragment("はれのばら", ws))

        soft, soft_path = finalize_ask_meaning_reply(ws, "はれのばらって何", None)
        self.assertEqual(soft_path, "soft_fail")
        self.assertIn("はれのばら", soft)

        ack = render_workshop_reply("ack", ws, player_text="なるほどねそういう意味か")
        self.assertLess(len(ack), 20)

    def test_reply_soft_tones(self) -> None:
        ws = open_from_emission(_emission())
        self.assertIn("余白", render_workshop_reply("critique_forced", ws))
        self.assertIn("ずれ", render_workshop_reply("critique_offscene", ws))
        praise = render_workshop_reply("praise", ws)
        self.assertIn("残しとく", praise)
        self.assertNotIn("緩める", praise)

    def test_conversational_revise_extract(self) -> None:
        from dogido_server.haiku.workshop import extract_conversational_revise

        self.assertEqual(
            extract_conversational_revise("こう直して: あさひさす / むらのどう / あかがね"),
            "あさひさす\nむらのどう\nあかがね",
        )
        self.assertIsNone(extract_conversational_revise("こう直してや"))

    def test_lessons_from_critique(self) -> None:
        from dogido_server.haiku.workshop import lessons_from_critique_kind

        lessons = lessons_from_critique_kind("forced_compress")
        self.assertTrue(lessons)
        self.assertIn("余白", lessons[0]["note"])
        self.assertEqual(lessons[0]["polarity"], "tighten")
        # praise / other は常駐 lesson を増やさない
        self.assertEqual(lessons_from_critique_kind("praise"), [])
        self.assertEqual(lessons_from_critique_kind("other", player_text="なんか微妙"), [])


    def test_lessons_list_soft_and_loosen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "mem")
            store.save_haiku_lesson(
                lesson_type="compress",
                note="要素を少し絞って余白を残すとよい",
                polarity="tighten",
            )
            store.save_haiku_lesson(
                lesson_type="readability",
                note="読みやすさを少し意識する",
                polarity="tighten",
            )
            # 同軸は最新1件
            store.save_haiku_lesson(
                lesson_type="compress",
                note="詰め込み注意（新しい方）",
                polarity="tighten",
            )
            listed = store.list_recent_haiku_lessons(limit=3)
            notes = [str(x.get("note")) for x in listed]
            self.assertIn("詰め込み注意（新しい方）", notes)
            self.assertNotIn("要素を少し絞って余白を残すとよい", notes)
            self.assertEqual(len(listed), 2)
            # 明示「気にせんで」相当 → 全軸 loosen（praise ではこれを書かない）
            store.save_haiku_lesson(lesson_type="*", note="", polarity="loosen", strength=0.0)
            self.assertEqual(store.list_recent_haiku_lessons(limit=3), [])

    def test_wants_clear_lessons_not_close(self) -> None:
        from dogido_server.haiku.workshop import wants_clear_haiku_lessons

        self.assertTrue(wants_clear_haiku_lessons("もう気にせんでええわ"))
        self.assertTrue(wants_clear_haiku_lessons("前の注意いらない"))
        self.assertTrue(wants_clear_haiku_lessons("気にせんでええよ"))
        self.assertTrue(wants_clear_haiku_lessons("もう気にしなくていいよ"))
        self.assertTrue(wants_clear_haiku_lessons("前の注意はいらないよ"))
        self.assertTrue(wants_clear_haiku_lessons("前の注意はもういらない"))
        self.assertFalse(wants_clear_haiku_lessons("もうええ"))
        self.assertFalse(wants_clear_haiku_lessons("気にせんでとは言ってない"))
        self.assertFalse(wants_clear_haiku_lessons("気にせんでとは言っとらん"))
        self.assertFalse(wants_clear_haiku_lessons("『気にせんで』と言われた"))
        self.assertFalse(wants_clear_haiku_lessons("前の注意いらないわけじゃない"))
        self.assertFalse(wants_clear_haiku_lessons("前の注意いらない？"))
        self.assertEqual(classify_workshop_intent("もう気にせんで"), "clear_lessons")
        self.assertEqual(classify_workshop_intent("もうええ"), "close")

    def test_lessons_ttl_by_age_and_emissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "mem")
            old = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
            now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
            store.save_haiku_lesson(
                lesson_type="compress",
                note="古い注意",
                polarity="tighten",
                observed_at=old,
            )
            # 14日超 → 出ない
            self.assertEqual(
                store.list_recent_haiku_lessons(limit=3, now=now, max_age_days=14),
                [],
            )
            recent = now - timedelta(days=1)
            store.save_haiku_lesson(
                lesson_type="compress",
                note="最近の注意",
                polarity="tighten",
                observed_at=recent,
            )
            listed = store.list_recent_haiku_lessons(limit=3, now=now, max_age_days=14)
            self.assertEqual(len(listed), 1)
            self.assertIn("最近", listed[0]["note"])
            # 発句を max 回積むと薄まる
            emission = _emission()
            for i in range(6):
                emission = HaikuEmission(
                    created_at=recent + timedelta(minutes=i + 1),
                    text=f"てすとく{i} あ い",
                    preface="ここで一句。",
                    interpretation="test",
                    biome="plains",
                    structure=None,
                    time_phase="day",
                    dimension="minecraft:overworld",
                    event_sequence=10 + i,
                    route="haiku",
                )
                store.save_agent_haiku(emission)
            self.assertEqual(
                store.list_recent_haiku_lessons(
                    limit=3, now=now, max_age_days=14, max_emissions_after=6
                ),
                [],
            )


class WorkshopServiceIntegrationTests(unittest.TestCase):
    def test_natural_praise_and_explicit_finish_close_the_workshop(self) -> None:
        from dogido_server.player_input.routing import route_player_input

        with tempfile.TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    memory_enabled=True,
                    memory_dir=Path(tmp) / "mem",
                )
            )
            response = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[response.session_id]
            emission = _emission(text="くさちはひる\nむぎのたねをまく\nあおいそら")
            session.last_haiku_emission = emission

            def event(sequence: int, text: str) -> GameEvent:
                return GameEvent(
                    schema_version="2026-05-24",
                    adapter="test",
                    observed_at=emission.created_at + timedelta(seconds=sequence),
                    sequence=sequence,
                    event=EventDescriptor(
                        name=EventName.STATUS_SNAPSHOT,
                        source_kind=SourceKind.SYSTEM,
                        priority_hint=PriorityHint.BACKGROUND,
                        certainty=Certainty.HIGH,
                    ),
                    player=PlayerState(name="p", dimension="minecraft:overworld"),
                    world=WorldState(
                        time_phase=TimePhase.DAY,
                        weather=Weather.CLEAR,
                        biome="meadow",
                        local_light=15,
                        sky_visible=True,
                    ),
                    meta=MetaState(user_text=text),
                )

            service._open_haiku_workshop(
                session,
                emission,
                entry_id="h_natural_praise",
                now=emission.created_at,
            )
            praise_text = "うんいいと思う"
            session.machine.player_input = route_player_input(praise_text)
            praise_actions = service._haiku_workshop_actions(session, event(1, praise_text))
            self.assertIsNone(session.haiku_workshop)
            self.assertIn("残しとく", praise_actions[0].text or "")

            service._open_haiku_workshop(
                session,
                emission,
                entry_id="h_explicit_finish",
                now=emission.created_at + timedelta(seconds=2),
            )
            finish_text = "これで終了で"
            session.machine.player_input = route_player_input(finish_text)
            finish_actions = service._haiku_workshop_actions(session, event(3, finish_text))
            self.assertIsNone(session.haiku_workshop)
            self.assertIn("ここまで", finish_actions[0].text or "")

    def test_player_line_edits_accumulate_and_continue_after_confirmation(self) -> None:
        from dogido_server.player_input.routing import route_player_input

        with tempfile.TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    memory_enabled=True,
                    memory_dir=Path(tmp) / "mem",
                )

            )
            response = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[response.session_id]
            emission = _emission(
                text="おだやかなる\nてのなかのくさ\nあめふりや"
            )
            session.last_haiku_emission = emission
            service._open_haiku_workshop(
                session,
                emission,
                entry_id="h_player_edit",
                now=emission.created_at,
            )

            def analyze(workshop: RecentHaikuWorkshop, player_text: str):
                if "手の中の草より" in player_text:
                    # OS AIが新語は取れても対象断片を空で返す場合がある。
                    # 発話中の現在句をコードで特定した結果は保持する。
                    return (
                        WorkshopAnalysis(
                            intent="propose_line_edit",
                            confidence=0.95,
                            line_proposal=WorkshopLineProposal(
                                replacement_text="草を握って",
                                target_fragment="",
                                line_index=None,
                                evidence=player_text,
                                confidence=0.95,
                            ),
                        ),
                        "test_os_ai",
                    )
                findings = (
                    (
                        WorkshopFinding(
                            line_index=0,
                            fragment="おだやかなる",
                            problem="meter",
                            note="語尾の一音が長い",
                            confidence=0.95,
                        ),
                    )
                    if "長い" in player_text
                    else ()
                )
                return (
                    WorkshopAnalysis(
                        intent="critique_forced",
                        confidence=1.0,
                        findings=findings,
                    ),
                    "test",
                )

            service._analyze_workshop_feedback = analyze  # type: ignore[method-assign]

            def event(sequence: int, text: str) -> GameEvent:
                return GameEvent(
                    schema_version="2026-05-24",
                    adapter="test",
                    observed_at=emission.created_at + timedelta(seconds=sequence * 5),
                    sequence=sequence,
                    event=EventDescriptor(
                        name=EventName.STATUS_SNAPSHOT,
                        source_kind=SourceKind.SYSTEM,
                        priority_hint=PriorityHint.BACKGROUND,
                        certainty=Certainty.HIGH,
                    ),
                    player=PlayerState(name="p", dimension="minecraft:overworld"),
                    world=WorldState(
                        time_phase=TimePhase.DAY,
                        weather=Weather.CLEAR,
                        biome="meadow",
                        local_light=15,
                        sky_visible=True,
                    ),
                    meta=MetaState(user_text=text),
                )

            def send(sequence: int, text: str) -> list[AudioAction]:
                session.machine.player_input = route_player_input(text)
                return service._haiku_workshop_actions(session, event(sequence, text))

            # 「下五」などの行名がSTTで崩れても、現在句の一行を旧句として
            # 読み上げれば、その一行だけをコードで置換対象に固定できる。
            with self.assertLogs("uvicorn.error", level="WARNING") as phrase_logs:
                phrase_edit = send(
                    0,
                    "手の中の草より草を握っての方がいい気がするね",
                )
            self.assertIn("くさをにぎって", phrase_edit[0].text or "")
            assert session.haiku_workshop is not None
            self.assertEqual(
                session.haiku_workshop.pending_revision,
                "おだやかなる\nくさをにぎって\nあめふりや",
            )
            joined_phrase_logs = "\n".join(phrase_logs.output)
            self.assertIn("target_fragment=てのなかのくさ", joined_phrase_logs)
            self.assertIn("target_line=1", joined_phrase_logs)

            # 以下の連続編集シナリオは、同じ発句を新しく開き直して確認する。
            service._open_haiku_workshop(
                session,
                emission,
                entry_id="h_player_edit",
                now=emission.created_at,
            )

            with self.assertLogs("uvicorn.error", level="WARNING") as locate_logs:
                send(1, "おだやかなるの『る』がちょっと長い")
            assert session.haiku_workshop is not None
            self.assertEqual(session.haiku_workshop.marked_line_index, 0)
            self.assertIn("haiku_workshop_locate", "\n".join(locate_logs.output))
            with self.assertLogs("uvicorn.error", level="WARNING") as captured:
                first = send(
                    2,
                    "穏やかなじゃ4文字だからさ 穏やかなでいいんじゃない",
                )
            self.assertIn("おだやかな", first[0].text or "")
            joined_logs = "\n".join(captured.output)
            self.assertIn("haiku_workshop_player_line_parse", joined_logs)
            self.assertIn("result=accepted", joined_logs)
            self.assertIn("haiku_workshop_player_line_edit", joined_logs)
            self.assertIn("result=staged", joined_logs)
            second = send(3, "下五を雨の夜に変えた方がいい")
            self.assertIn("あめのよる", second[0].text or "")
            assert session.haiku_workshop is not None
            self.assertEqual(
                session.haiku_workshop.pending_revision,
                "おだやかな\nてのなかのくさ\nあめのよる",
            )
            self.assertEqual(service.memory.list_haiku_revisions(), [])
            self.assertEqual(
                build_workshop_intent_llm_details(
                    session.haiku_workshop,
                    "この句どうかな",
                )["verse"],
                session.haiku_workshop.pending_revision,
            )

            ambiguous = send(4, "上五と下五を朝の雨に変えた方がいい")
            self.assertIn("一つ", ambiguous[0].text or "")
            self.assertEqual(
                session.haiku_workshop.pending_revision,
                "おだやかな\nてのなかのくさ\nあめのよる",
            )

            shown = send(5, "全体はどうなった？")
            self.assertIn(session.haiku_workshop.pending_revision, shown[0].text or "")

            accepted = send(6, "その案で")
            self.assertIn("まだ直したい行", accepted[0].text or "")
            assert session.haiku_workshop is not None
            self.assertTrue(is_open(session.haiku_workshop))
            self.assertEqual(
                session.haiku_workshop.display_line(),
                "おだやかな\nてのなかのくさ\nあめのよる",
            )
            first_revision = service.memory.list_haiku_revisions()[0]
            self.assertEqual(first_revision["source"], "player_line_confirmed")
            self.assertEqual(first_revision["base_text"], emission.text)
            self.assertEqual([edit["line_index"] for edit in first_revision["edits"]], [0, 2])
            self.assertNotRegex(first_revision["revised_text"], r"[一-龯ァ-ヶ]")

            third = send(7, "中七を草を握ってに変えた方がいい")
            self.assertIn("くさをにぎって", third[0].text or "")
            send(8, "その案で")
            assert session.haiku_workshop is not None
            revisions = service.memory.list_haiku_revisions()
            self.assertEqual(len(revisions), 2)
            self.assertEqual(revisions[1]["base_text"], revisions[0]["revised_text"])
            self.assertEqual(revisions[1]["parent_revision_id"], revisions[0]["id"])
            self.assertEqual(
                session.haiku_workshop.display_line(),
                "おだやかな\nくさをにぎって\nあめのよる",
            )
            with self.assertRaisesRegex(ValueError, "parent revision"):
                service.memory.save_haiku_feedback(
                    emission,
                    revised_text=revisions[1]["revised_text"],
                    source="player_line_confirmed",
                    revision_base_text=revisions[1]["base_text"],
                    parent_revision_id="rev_wrong_parent",
                    revision_edit_contract=revisions[1]["edit_contract"],
                    revision_edits=revisions[1]["edits"],
                    observed_at=emission.created_at + timedelta(seconds=60),
                )

    def test_os_ai_extracts_natural_line_proposal_and_semantic_acceptance(self) -> None:
        from dogido_server.player_input.routing import route_player_input

        class SemanticWorkshopLLM:
            def __init__(self) -> None:
                self.structured_kinds: list[str] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
                return request.fallback_text

            def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
                self.structured_kinds.append(request.kind)
                player_text = str(request.details.get("player_text") or "")
                if request.kind == "haiku_workshop_pending_decision":
                    return {
                        "action": "accept_pending",
                        "confidence": 0.96,
                        "evidence": player_text,
                    }
                if request.kind == "haiku_workshop_intent":
                    return {
                        "intent": "propose_line_edit",
                        "confidence": 0.95,
                        "repair_requested": False,
                        "findings": [],
                        "line_proposal": {
                            "found": True,
                            "line_index": 1,
                            "target_fragment": "ひろがるくさち",
                            "replacement_text": "くさちひろがる",
                            "evidence": "くさちひろがるにしてはどうですか",
                            "confidence": 0.94,
                        },
                    }
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            llm = SemanticWorkshopLLM()
            service = DogidoService(
                Settings(
                    llm_enabled=True,
                    audio_enabled=False,
                    memory_enabled=True,
                    memory_dir=Path(tmp) / "mem",
                    platform_ai_provider="chat",
                )
            )
            response = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[response.session_id]
            service.llm = llm
            emission = _emission(text="つちのくさ\nひろがるくさち\nひるのそら")
            session.last_haiku_emission = emission
            service._open_haiku_workshop(
                session,
                emission,
                entry_id="h_semantic_edit",
                now=emission.created_at,
            )

            def event(sequence: int, text: str) -> GameEvent:
                return GameEvent(
                    schema_version="2026-05-24",
                    adapter="test",
                    observed_at=emission.created_at + timedelta(seconds=sequence * 5),
                    sequence=sequence,
                    event=EventDescriptor(
                        name=EventName.STATUS_SNAPSHOT,
                        source_kind=SourceKind.SYSTEM,
                        priority_hint=PriorityHint.BACKGROUND,
                        certainty=Certainty.HIGH,
                    ),
                    player=PlayerState(name="p", dimension="minecraft:overworld"),
                    world=WorldState(
                        time_phase=TimePhase.DAY,
                        weather=Weather.CLEAR,
                        biome="meadow",
                        local_light=15,
                        sky_visible=True,
                    ),
                    meta=MetaState(user_text=text),
                )

            proposal_text = "二行目はくさちひろがるにしてはどうですか"
            session.machine.player_input = route_player_input(proposal_text)
            self.assertIsNone(session.machine.player_input.reading_correction)
            proposed = service._haiku_workshop_actions(session, event(1, proposal_text))
            self.assertIn("くさちひろがる", proposed[0].text or "")
            assert session.haiku_workshop is not None
            self.assertEqual(
                session.haiku_workshop.pending_revision,
                "つちのくさ\nくさちひろがる\nひるのそら",
            )
            self.assertEqual(service.memory.list_haiku_revisions(), [])

            accept_text = "よし、それで完成にしよう"
            session.machine.player_input = route_player_input(accept_text)
            accepted = service._haiku_workshop_actions(session, event(2, accept_text))
            self.assertIn("覚え", accepted[0].text or "")
            revisions = service.memory.list_haiku_revisions()
            self.assertEqual(len(revisions), 1)
            self.assertEqual(
                revisions[0]["revised_text"],
                "つちのくさ\nくさちひろがる\nひるのそら",
            )
            self.assertEqual(
                llm.structured_kinds,
                ["haiku_workshop_intent", "haiku_workshop_pending_decision"],
            )

    def test_full_conversational_revision_is_not_taken_as_a_line_replacement(self) -> None:
        from dogido_server.player_input.routing import route_player_input

        with tempfile.TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    memory_enabled=True,
                    memory_dir=Path(tmp) / "mem",
                )
            )
            response = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[response.session_id]
            emission = _emission(text="ゆうぐれの\nてのなかのくさ\nあめふりや")
            session.last_haiku_emission = emission
            service._open_haiku_workshop(session, emission, entry_id="h_full", now=emission.created_at)
            text = "こう直して: はるのかぜ / よるのつき / ゆきにかえて"
            session.machine.player_input = route_player_input(text)
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(name="p", dimension="minecraft:overworld"),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="meadow",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text=text),
            )
            reply = service._haiku_workshop_actions(session, event)
            self.assertIn("覚え", reply[0].text or "")
            self.assertIsNone(session.haiku_workshop)
            self.assertEqual(
                service.memory.list_haiku_revisions()[0]["revised_text"],
                "はるのかぜ\nよるのつき\nゆきにかえて",
            )

    def test_voice_input_uses_workshop_context_only_for_semantic_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    memory_enabled=False,
                    memory_dir=Path(tmp) / "mem",
                )
            )
            response = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[response.session_id]
            emission = _emission(text="さむいあめや\nまどのひかりを\nくさちのね")
            emission.materials = {"time_phase": "evening"}
            session.last_haiku_emission = emission
            service._open_haiku_workshop(
                session,
                emission,
                entry_id=None,
                now=emission.created_at,
            )
            service.push_player_input(
                "それだったらユーグレイヤの方がいいんじゃない?",
                source="voice",
            )
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=2),
                sequence=1,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.EVENING,
                    weather=Weather.CLEAR,
                    biome="meadow",
                    local_light=12,
                    sky_visible=True,
                ),
                meta=MetaState(),
            )

            service.process_event(event, session_id=response.session_id)

            self.assertEqual(
                session.machine.player_input.raw_text,
                "それだったらユーグレイヤの方がいいんじゃない?",
            )
            self.assertEqual(
                session.machine.player_input.semantic_text,
                "それだったら夕暮れやの方がいいんじゃない?",
            )

    def test_typed_input_does_not_use_phonetic_workshop_correction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(llm_enabled=False, audio_enabled=False, memory_enabled=False)
            )
            response = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[response.session_id]
            emission = _emission(text="さむいあめや\nまどのひかりを\nくさちのね")
            emission.materials = {"time_phase": "evening"}
            service._open_haiku_workshop(
                session,
                emission,
                entry_id=None,
                now=emission.created_at,
            )
            service.push_player_input("ユーグレイヤの方がいい", source="text")
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=2),
                sequence=1,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(name="p", dimension="minecraft:overworld"),
                world=WorldState(
                    time_phase=TimePhase.EVENING,
                    weather=Weather.CLEAR,
                    biome="meadow",
                    local_light=12,
                    sky_visible=True,
                ),
                meta=MetaState(),
            )

            service.process_event(event, session_id=response.session_id)

            self.assertEqual(session.machine.player_input.semantic_text, "ユーグレイヤの方がいい")

    def test_repair_is_proposed_then_saved_only_after_explicit_confirmation(self) -> None:
        class RepairLLM:
            def __init__(self) -> None:
                self.requests = []
                self.leaf_requests = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
                self.leaf_requests.append(request)
                if request.kind == "haiku_workshop_reply":
                    return "二行目を、雨の場面に寄せてみたで。"
                return request.fallback_text

            def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
                self.requests.append(request)
                if request.kind == "haiku_workshop_intent":
                    return {
                        "intent": "request_repair",
                        "confidence": 0.96,
                        "repair_requested": True,
                        "findings": [{
                            "line_index": 1,
                            "fragment": "ひつじがあるく",
                            "problem": "preference",
                            "note": "雨の場面を残したい",
                            "confidence": 0.92,
                        }],
                    }
                if request.kind == "haiku_workshop_revision":
                    return {"lines": [{
                        "line_index": 1,
                        "expected_text": "ひつじがあるく",
                        "replacement_text": "あめつよくふる",
                        "atom_ids": ["observation:test:3"],
                    }]}
                if request.kind == "haiku_line_grounding":
                    return {
                        "assessments": [{
                            "line_index": 1,
                            "atom_ids": ["observation:test:3"],
                            "meaning_retained": True,
                            "natural_japanese": True,
                        }]
                    }
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            from dogido_server.haiku.source_atoms import HaikuSourceAtom
            from dogido_server.player_input.routing import route_player_input

            atoms = [
                HaikuSourceAtom(
                    atom_id=f"observation:test:{index}",
                    text=text,
                    source_ref=f"observation:test:{index}",
                    field_path="observed_label",
                    observation_role="test",
                    kind="observation",
                    claim_class="factual",
                    claim_scopes=("observed_state",),
                )
                for index, text in enumerate(("春の風", "歩く羊", "夜の月", "強い雨"))
            ]
            lines = ["はるのかぜ", "ひつじがあるく", "よるのつき"]
            emission = _emission(text="\n".join(lines))
            emission.materials = {
                "source_atoms": [atom.to_prompt_dict() for atom in atoms],
                "line_sources": [
                    {"line_index": index, "text": lines[index], "atom_ids": [atoms[index].atom_id]}
                    for index in range(3)
                ],
            }
            llm = RepairLLM()
            service = DogidoService(
                Settings(
                    llm_enabled=True,
                    audio_enabled=False,
                    memory_enabled=True,
                    memory_dir=Path(tmp) / "mem",
                    platform_ai_provider="chat",
                )
            )
            response = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[response.session_id]
            service.llm = llm
            session.last_haiku_emission = emission
            service._open_haiku_workshop(session, emission, entry_id="h_repair", now=emission.created_at)

            def event(sequence: int, text: str) -> GameEvent:
                return GameEvent(
                    schema_version="2026-05-24",
                    adapter="test",
                    observed_at=emission.created_at + timedelta(seconds=sequence * 5),
                    sequence=sequence,
                    event=EventDescriptor(
                        name=EventName.STATUS_SNAPSHOT,
                        source_kind=SourceKind.SYSTEM,
                        priority_hint=PriorityHint.BACKGROUND,
                        certainty=Certainty.HIGH,
                    ),
                    player=PlayerState(
                        name="p",
                        position=Position(x=0, y=64, z=0),
                        dimension="minecraft:overworld",
                    ),
                    world=WorldState(
                        time_phase=TimePhase.DAY,
                        weather=Weather.CLEAR,
                        biome="plains",
                        local_light=15,
                        sky_visible=True,
                    ),
                    meta=MetaState(user_text=text),
                )

            session.machine.player_input = route_player_input("そこ直して")
            proposed = service._haiku_workshop_actions(session, event(2, "そこ直して"))
            self.assertIn("あめつよくふる", proposed[0].text or "")
            self.assertIn("雨の場面に寄せて", proposed[0].text or "")
            assert session.haiku_workshop is not None
            self.assertEqual(session.haiku_workshop.pending_revision, "はるのかぜ\nあめつよくふる\nよるのつき")
            self.assertEqual(service.memory.list_haiku_revisions(), [])
            revision_request = next(request for request in llm.requests if request.kind == "haiku_workshop_revision")
            self.assertEqual(revision_request.route, "haiku")
            proposal_leaf = next(request for request in llm.leaf_requests if request.kind == "haiku_workshop_reply")
            self.assertEqual(proposal_leaf.details["repair_state"], "proposed")
            self.assertEqual(
                proposal_leaf.details["proposed_revision"],
                "はるのかぜ\nあめつよくふる\nよるのつき",
            )
            from dogido_server.llm.prompts import build_messages

            proposal_prompt = build_messages(proposal_leaf)[1]["content"]
            self.assertIn("コード検証済みの修正案", proposal_prompt)
            self.assertIn("句本文を復唱", proposal_prompt)

            session.machine.player_input = route_player_input("その案で")
            saved = service._haiku_workshop_actions(session, event(3, "その案で"))
            self.assertIn("覚え", saved[0].text or "")
            self.assertIsNotNone(session.haiku_workshop)
            self.assertTrue(is_open(session.haiku_workshop))
            revisions = service.memory.list_haiku_revisions()
            self.assertEqual(revisions[0]["revised_text"], "はるのかぜ\nあめつよくふる\nよるのつき")
            self.assertEqual(revisions[0]["source"], "generated_confirmed")
            self.assertEqual(revisions[0]["line_sources"][1]["atom_ids"], ["observation:test:3"])
            self.assertEqual(revisions[0]["edit_contract"], "line_compare_and_swap_v1")
            self.assertEqual(revisions[0]["edits"][0]["expected_text"], "ひつじがあるく")
            self.assertEqual(revisions[0]["edits"][0]["replacement_text"], "あめつよくふる")

    def test_emit_opens_workshop_and_critique_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
            session = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            # session_id from response
            sid = session.session_id
            sess = service.sessions[sid]
            emission = _emission()
            service._open_haiku_workshop(sess, emission, entry_id="h_test", now=emission.created_at)
            self.assertTrue(is_open(sess.haiku_workshop))

            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="グーの木の水って何?"),
            )
            sess.machine.player_input = __import__(
                "dogido_server.player_input", fromlist=["route_player_input"]
            ).route_player_input("グーの木の水って何?")
            actions = service._haiku_workshop_actions(sess, event)
            self.assertEqual(1, len(actions))
            # 句に無い語への問い → 聞き返し（全文講義しない）
            self.assertTrue(
                "どの言葉" in (actions[0].text or "")
                or "読みにく" in (actions[0].text or ""),
                msg=actions[0].text,
            )
            self.assertNotIn("biome", actions[0].text or "")
            # critique was written via service.memory
            self.assertTrue((Path(tmp) / "mem" / "long_term" / "haiku_critiques.jsonl").exists())
            self.assertTrue((Path(tmp) / "mem" / "long_term" / "haiku_lessons.jsonl").exists())
            # list は wall-clock 基準の TTL があるので、発句時刻基準で見る（日付固定テストのゆらぎ防止）
            lessons = MemoryStore(Path(tmp) / "mem").list_recent_haiku_lessons(
                limit=3,
                now=event.observed_at,
            )
            self.assertTrue(any("読みやす" in str(x.get("note")) for x in lessons))
            # hard 合流用の fragments があっても soft のまま
            self.assertTrue(all(x.get("polarity") != "loosen" for x in lessons))

    def test_h7_lite_persists_code_validated_semantic_lesson(self) -> None:
        class IntentLLM:
            def __init__(self) -> None:
                self.structured_kinds: list[str] = []
                self.last_intent_request = None

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request) -> str:  # type: ignore[no-untyped-def]
                raise AssertionError(f"leaf should not run for {request.kind}")

            def generate_structured_json(self, request) -> dict[str, object]:  # type: ignore[no-untyped-def]
                self.structured_kinds.append(request.kind)
                if request.kind == "haiku_workshop_intent":
                    self.last_intent_request = request
                    return {"intent": "critique_forced", "confidence": 0.94}
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            llm = IntentLLM()
            settings = Settings(
                llm_enabled=True,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
                platform_ai_provider="chat",
            )
            service = DogidoService(settings)
            session = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            sess = service.sessions[session.session_id]
            service.llm = llm
            emission = _emission(text="そらまぶし\nくさむらにうかぶ\nくろいせきたん")
            service._open_haiku_workshop(sess, emission, entry_id="h_h7", now=emission.created_at)

            from dogido_server.player_input.routing import route_player_input

            player_text = "ちょっと言葉を詰めすぎた感じがする"
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text=player_text),
            )
            sess.machine.player_input = route_player_input(player_text)
            actions = service._haiku_workshop_actions(sess, event)

            self.assertEqual(len(actions), 1)
            self.assertIn("余白", actions[0].text or "")
            self.assertEqual(llm.structured_kinds, ["haiku_workshop_intent"])
            request = llm.last_intent_request
            self.assertIsNotNone(request)
            self.assertEqual(request.route, "chat")
            self.assertEqual(request.max_tokens, 320)
            self.assertEqual(request.details["player_text"], player_text)
            lessons = MemoryStore(Path(tmp) / "mem").list_recent_haiku_lessons(
                limit=3,
                now=event.observed_at,
            )
            self.assertTrue(any(x.get("lesson_type") == "compress" for x in lessons))
            self.assertTrue(is_open(sess.haiku_workshop))

    def test_h7_lite_extracts_findings_for_rule_intent_and_falls_back_on_error(self) -> None:
        class ControlledLLM:
            def __init__(self) -> None:
                self.structured_kinds: list[str] = []
                self.raise_intent = False
                self.intent = "close"

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request) -> str:  # type: ignore[no-untyped-def]
                return request.fallback_text

            def generate_structured_json(self, request) -> dict[str, object]:  # type: ignore[no-untyped-def]
                self.structured_kinds.append(request.kind)
                if self.raise_intent:
                    raise RuntimeError("local classifier unavailable")
                return {"intent": self.intent, "confidence": 1.0}

        with tempfile.TemporaryDirectory() as tmp:
            llm = ControlledLLM()
            service = DogidoService(
                Settings(
                    llm_enabled=True,
                    audio_enabled=False,
                    memory_enabled=True,
                    memory_dir=Path(tmp) / "mem",
                    platform_ai_provider="chat",
                )
            )
            session = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            sess = service.sessions[session.session_id]
            service.llm = llm
            emission = _emission()
            service._open_haiku_workshop(sess, emission, entry_id="h_safe", now=emission.created_at)
            assert sess.haiku_workshop is not None

            # 曖昧な発話では限定 intent を補助する。
            analysis, path = service._analyze_workshop_feedback(
                sess.haiku_workshop,
                "曖昧な句の話",
            )
            self.assertEqual(analysis.intent, "soft_default")  # close は許可 enum 外
            self.assertEqual(path, "soft_default")
            self.assertTrue(is_open(sess.haiku_workshop))

            llm.structured_kinds.clear()
            # 既知の講評では大分類をルールが守り、AIはfinding抽出だけに使う。
            from dogido_server.player_input.routing import route_player_input

            rule_text = "無理やり圧縮しすぎ"
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text=rule_text),
            )
            sess.machine.player_input = route_player_input(rule_text)
            actions = service._haiku_workshop_actions(sess, event)
            self.assertEqual(len(actions), 1)
            self.assertIn("余白", actions[0].text or "")
            self.assertEqual(llm.structured_kinds, ["haiku_workshop_intent"])

            # 曖昧文をAIだけが praise と見ても、コード上の明示 praise ではないため閉じない。
            llm.intent = "praise"
            ambiguous = "印象に残るなあ"
            sess.machine.player_input = route_player_input(ambiguous)
            praise_event = event.model_copy(
                update={
                    "sequence": 3,
                    "observed_at": emission.created_at + timedelta(seconds=7),
                    "meta": MetaState(user_text=ambiguous),
                }
            )
            service._haiku_workshop_actions(sess, praise_event)
            self.assertTrue(is_open(sess.haiku_workshop))

            # AIだけが修正要求と分類しても、明示ルールなしではpendingを作らない。
            llm.intent = "request_repair"
            repair_event = praise_event.model_copy(
                update={
                    "sequence": 4,
                    "observed_at": emission.created_at + timedelta(seconds=8),
                    "meta": MetaState(user_text="これどうかな"),
                }
            )
            sess.machine.player_input = route_player_input("これどうかな")
            service._haiku_workshop_actions(sess, repair_event)
            assert sess.haiku_workshop is not None
            self.assertIsNone(sess.haiku_workshop.pending_revision)
            self.assertNotIn("haiku_workshop_revision", llm.structured_kinds)

            # provider 例外も従来の soft_default に戻る
            llm.raise_intent = True
            analysis, path = service._analyze_workshop_feedback(
                sess.haiku_workshop,
                "曖昧な句の話",
            )
            self.assertEqual((analysis.intent, path), ("soft_default", "soft_default"))
            self.assertTrue(is_open(sess.haiku_workshop))

    def test_workshop_defers_night_warning_until_closed(self) -> None:
        """workshop open 中は夕方割り込みを出さず、閉じたあとに出せる。"""
        from dogido_server.haiku.workshop import close_workshop
        from dogido_server.player_input.routing import route_player_input
        from dogido_server.state_machine import DogidoStateMachine

        settings = Settings(
            llm_enabled=False,
            audio_enabled=False,
            decision_policy="py_trees",
            player_input_priority_cooldown_ms=20000,
        )
        machine = DogidoStateMachine(settings)
        emission = _emission()
        workshop = open_from_emission(emission, materials={"interpretation": "平原"}, now=emission.created_at)
        machine.haiku_workshop_provider = lambda: workshop

        evening = GameEvent(
            schema_version="2026-05-24",
            adapter="test",
            observed_at=emission.created_at + timedelta(seconds=10),
            sequence=2,
            event=EventDescriptor(
                name=EventName.STATUS_SNAPSHOT,
                source_kind=SourceKind.SYSTEM,
                priority_hint=PriorityHint.BACKGROUND,
                certainty=Certainty.HIGH,
            ),
            player=PlayerState(
                name="p",
                position=Position(x=0, y=64, z=0),
                dimension="minecraft:overworld",
            ),
            world=WorldState(
                time_phase=TimePhase.EVENING,
                weather=Weather.CLEAR,
                biome="plains",
                local_light=12,
                sky_visible=True,
                enclosure_score=0.05,
                ceiling_height=24.0,
                danger_darkness_score=0.2,
            ),
            meta=MetaState(user_text="平原と平場は結びつけない方がいい"),
        )
        # 入力優先を立てる（夜警告の注意喚起条件）
        machine.player_input = route_player_input(evening.meta.user_text or "")
        machine.state.last_player_input_at = evening.observed_at
        machine.state.night_warning_pending = True
        machine.state.night_warning_emitted_this_cycle = False

        during = machine.process(evening)
        speeches = [a.text for a in during.actions if a.layer == "speech" and a.text]
        self.assertFalse(
            any(t and ("夜" in t or "夕方" in t) for t in speeches),
            msg=speeches,
        )
        # pending は消費しない
        self.assertFalse(machine.state.night_warning_emitted_this_cycle)

        close_workshop(workshop, reason="explicit")
        later = evening.model_copy(
            update={
                "sequence": 3,
                "observed_at": evening.observed_at + timedelta(seconds=2),
                "meta": MetaState(user_text=None),
            }
        )
        machine.player_input = route_player_input(None)
        after = machine.process(later)
        after_speech = [a.text for a in after.actions if a.layer == "speech" and a.text]
        self.assertTrue(
            any(t and ("夜" in t or "夕方" in t) for t in after_speech),
            msg=after_speech,
        )

    def test_workshop_meaning_question_beats_player_chat(self) -> None:
        """workshop open 中の『〜って何』は player_chat ではなく workshop 返事だけ。"""

        class CaptureChatLLM:
            def __init__(self) -> None:
                self.kinds: list[str] = []
                self.structured_kinds: list[str] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request) -> str:  # type: ignore[no-untyped-def]
                self.kinds.append(request.kind)
                return "LLMが雑談で答えた文"

            def generate_structured_json(self, request) -> dict[str, object]:  # type: ignore[no-untyped-def]
                self.structured_kinds.append(request.kind)
                # 材料 pick は正直に soft_fail を返す想定
                return {
                    "pick_index": None,
                    "reply": "「ひらべった」の読みやね。ちょっと分かりにくかったかも。",
                }

        with tempfile.TemporaryDirectory() as tmp:
            llm = CaptureChatLLM()
            settings = Settings(
                llm_enabled=True,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
            session = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            sid = session.session_id
            sess = service.sessions[sid]
            sess.machine.llm = llm
            service.llm = llm  # ask_meaning は service.llm を使う
            emission = _emission(text="ひらべった てのきのき ひるのひ")
            service._open_haiku_workshop(sess, emission, entry_id="h_test", now=emission.created_at)

            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="平べったって何だろうか"),
            )
            result = service.process_event(event, session_id=sid)
            speeches = [a.text for a in result.actions if a.layer == "speech" and a.text]
            self.assertEqual(len(speeches), 1)
            self.assertNotIn("LLMが雑談で答えた文", speeches[0])
            self.assertTrue(
                "ひらべった" in (speeches[0] or "") or "読みにく" in (speeches[0] or ""),
                msg=speeches[0],
            )
            self.assertNotIn("player_chat", llm.kinds)
            self.assertIn("haiku_workshop_material_pick", llm.structured_kinds)

    def test_ask_meaning_llm_picks_material(self) -> None:
        """ask_meaning は候補を閉じて LLM に選ばせ、柔軟な reply を通す。"""

        class PickLLM:
            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request) -> str:  # type: ignore[no-untyped-def]
                raise AssertionError("leaf should not run")

            def generate_structured_json(self, request) -> dict[str, object]:  # type: ignore[no-untyped-def]
                self.last = request
                cands = list(request.details.get("candidates") or [])
                self.assert_plains = "平原" in cands
                idx = cands.index("平原") if "平原" in cands else 0
                return {"pick_index": idx, "reply": "平原のことやで。"}

        with tempfile.TemporaryDirectory() as tmp:
            llm = PickLLM()
            settings = Settings(
                llm_enabled=True,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
            session = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            sid = session.session_id
            sess = service.sessions[sid]
            service.llm = llm
            emission = _emission(text="はれのばら\nわびたふどうの\nてのなか")
            service._open_haiku_workshop(
                sess,
                emission,
                entry_id="h_pick",
                now=emission.created_at,
            )
            # open の materials を上書き（現実に近い候補）
            assert sess.haiku_workshop is not None
            sess.haiku_workshop.materials = {
                "interpretation": "広大な平原と錆びた銅のランタン",
                "biome": "plains",
                "biome_ja": "平原",
                "motifs": ["錆びた銅のランタン", "平原", "昼"],
            }
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="晴れのバラ?"),
            )
            result = service.process_event(event, session_id=sid)
            speeches = [a.text for a in result.actions if a.layer == "speech" and a.text]
            self.assertEqual(speeches, ["平原のことやで。"])
            self.assertEqual(getattr(llm, "last").kind, "haiku_workshop_material_pick")
            self.assertTrue(getattr(llm, "assert_plains"))

    def test_meaning_ack_moves_to_close_confirmation_without_new_critique(self) -> None:
        """説明への納得を別箇所の講評にせず、確認してからpinを閉じる。"""

        from dogido_server.player_input.routing import route_player_input

        class FollowupService(DogidoService):
            def __init__(self, settings: Settings) -> None:
                super().__init__(settings)
                self.stages: list[tuple[str, str]] = []

            def _analyze_workshop_feedback(  # type: ignore[override]
                self,
                workshop,
                player_text: str,
            ) -> tuple[WorkshopAnalysis, str]:
                details = build_workshop_intent_llm_details(workshop, player_text)
                self.stages.append((player_text, str(details["conversation_stage"])))
                if player_text == "腑に落ちたよ":
                    return WorkshopAnalysis(intent="ack", confidence=0.95), "apple_test"
                # 「そうなんだ」はOS AIが誤分類してもclosed fallbackで拾えることを守る。
                if "って何" in player_text:
                    return WorkshopAnalysis(intent="ask_meaning", confidence=0.95), "apple_test"
                if player_text == "そうなんだ":
                    return (
                        WorkshopAnalysis(
                            intent="other_haiku",
                            confidence=0.95,
                            findings=(
                                WorkshopFinding(
                                    line_index=2,
                                    fragment="ほねはくさち",
                                    problem="unnatural_japanese",
                                    note="別の行を誤って拾った",
                                    confidence=0.95,
                                ),
                            ),
                        ),
                        "apple_test",
                    )
                return WorkshopAnalysis(), "soft_default"

            def _ask_meaning_workshop_reply(  # type: ignore[override]
                self,
                workshop,
                player_text: str,
            ) -> tuple[str, str]:
                return "それやろ、オオカミの耳やで。", "test_explanation"

            def _collaborator_workshop_reply(self, *args, **kwargs):  # type: ignore[no-untyped-def, override]
                raise AssertionError("納得と終了確認を共同編集者leafへ渡してはいけない")

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "mem"
            service = FollowupService(
                Settings(
                    llm_enabled=False,
                    audio_enabled=False,
                    memory_enabled=True,
                    memory_dir=memory_dir,
                )
            )
            response = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            session = service.sessions[response.session_id]
            emission = _emission(text="はなのしろ\nみみふえてゆく\nほねはくさち")
            service._open_haiku_workshop(
                session,
                emission,
                entry_id="h_meaning_followup",
                now=emission.created_at,
            )

            def event(sequence: int, text: str) -> GameEvent:
                return GameEvent(
                    schema_version="2026-05-24",
                    adapter="test",
                    observed_at=emission.created_at + timedelta(seconds=sequence),
                    sequence=sequence,
                    event=EventDescriptor(
                        name=EventName.STATUS_SNAPSHOT,
                        source_kind=SourceKind.SYSTEM,
                        priority_hint=PriorityHint.BACKGROUND,
                        certainty=Certainty.HIGH,
                    ),
                    player=PlayerState(
                        name="p",
                        position=Position(x=0, y=64, z=0),
                        dimension="minecraft:overworld",
                    ),
                    world=WorldState(
                        time_phase=TimePhase.DAY,
                        weather=Weather.CLEAR,
                        biome="plains",
                        local_light=15,
                        sky_visible=True,
                    ),
                    meta=MetaState(user_text=text),
                )

            def send(sequence: int, text: str) -> str:
                session.machine.player_input = route_player_input(text)
                actions = service._haiku_workshop_actions(session, event(sequence, text))
                speeches = [action.text for action in actions if action.text]
                self.assertEqual(len(speeches), 1)
                return str(speeches[0])

            critique_path = memory_dir / "long_term" / "haiku_critiques.jsonl"

            self.assertIn("オオカミの耳", send(2, "みみふえての耳って何？"))
            assert session.haiku_workshop is not None
            self.assertTrue(session.haiku_workshop.awaiting_meaning_ack)
            critique_count = len(critique_path.read_text(encoding="utf-8").splitlines())

            self.assertIn("ここまででええ", send(3, "そうなんだ"))
            assert session.haiku_workshop is not None
            self.assertTrue(session.haiku_workshop.awaiting_close_confirmation)
            self.assertEqual(session.haiku_workshop.last_findings, [])
            self.assertEqual(
                len(critique_path.read_text(encoding="utf-8").splitlines()),
                critique_count,
            )
            self.assertEqual(service.stages[-1], ("そうなんだ", "meaning_explained"))

            self.assertIn("まだ続けよか", send(4, "まだ続けたい"))
            assert session.haiku_workshop is not None
            self.assertFalse(session.haiku_workshop.awaiting_close_confirmation)

            self.assertIn("オオカミの耳", send(5, "みみふえての耳って何？"))
            session.machine.player_input = route_player_input("松明ある？")
            self.assertEqual(
                service._haiku_workshop_actions(session, event(6, "松明ある？")),
                [],
            )
            assert session.haiku_workshop is not None
            self.assertFalse(session.haiku_workshop.awaiting_meaning_ack)

            self.assertIn("オオカミの耳", send(7, "みみふえての耳って何？"))
            self.assertIn("ここまででええ", send(8, "腑に落ちたよ"))
            self.assertEqual(service.stages[-1], ("腑に落ちたよ", "meaning_explained"))
            self.assertIn("ここまでや", send(9, "うん"))
            self.assertIsNone(session.haiku_workshop)

    def test_soft_critique_uses_collaborator_leaf_not_player_chat(self) -> None:
        """Stage2+4: 添削っぽい自然文は共同編集者leaf。chatに落ちずpinも維持。"""

        class CollaboratorLLM:
            def __init__(self) -> None:
                self.kinds: list[str] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request) -> str:  # type: ignore[no-untyped-def]
                self.kinds.append(request.kind)
                if request.kind == "haiku_workshop_reply":
                    return "せやな、くろいせきたんはちょっと長いかもな。"
                return "LLMが雑談で答えた文"

            def generate_structured_json(self, request) -> dict[str, object]:  # type: ignore[no-untyped-def]
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            llm = CollaboratorLLM()
            settings = Settings(
                llm_enabled=True,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
            session = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            sid = session.session_id
            sess = service.sessions[sid]
            sess.machine.llm = llm
            service.llm = llm
            emission = _emission(text="そらまぶし\nくさむらにうかぶ\nくろいせきたん")
            service._open_haiku_workshop(sess, emission, entry_id="h_soft", now=emission.created_at)

            def _event(seq: int, user_text: str, *, seconds: int = 5) -> GameEvent:
                return GameEvent(
                    schema_version="2026-05-24",
                    adapter="test",
                    observed_at=emission.created_at + timedelta(seconds=seconds),
                    sequence=seq,
                    event=EventDescriptor(
                        name=EventName.STATUS_SNAPSHOT,
                        source_kind=SourceKind.SYSTEM,
                        priority_hint=PriorityHint.BACKGROUND,
                        certainty=Certainty.HIGH,
                    ),
                    player=PlayerState(
                        name="p",
                        position=Position(x=0, y=64, z=0),
                        dimension="minecraft:overworld",
                    ),
                    world=WorldState(
                        time_phase=TimePhase.DAY,
                        weather=Weather.CLEAR,
                        biome="plains",
                        local_light=15,
                        sky_visible=True,
                    ),
                    meta=MetaState(user_text=user_text),
                )

            # Stage1 マーカー hitでも、共同編集者 leaf で短く受け止める。
            r1 = service.process_event(
                _event(2, "なかなかいいねでも黒い石炭がちょっと長いかも", seconds=5),
                session_id=sid,
            )
            speeches1 = [a.text for a in r1.actions if a.layer == "speech" and a.text]
            self.assertEqual(len(speeches1), 1)
            self.assertIn("くろいせきたん", speeches1[0])
            self.assertNotIn("player_chat", llm.kinds)
            self.assertTrue(is_open(sess.haiku_workshop))
            self.assertEqual(sess.haiku_workshop.drift_count if sess.haiku_workshop else -1, 0)

            # Stage4: 置換語は取れたが対象行が曖昧なら、コードが行を確認する。
            r2 = service.process_event(
                _event(3, "黒石炭とかにしたらいいんじゃない?", seconds=10),
                session_id=sid,
            )
            speeches2 = [a.text for a in r2.actions if a.layer == "speech" and a.text]
            self.assertEqual(len(speeches2), 1)
            self.assertIn("haiku_workshop_reply", llm.kinds)
            self.assertIn("元の一行", speeches2[0])
            self.assertTrue(is_open(sess.haiku_workshop), msg="2回添削でも drift close しない")
            self.assertNotIn("player_chat", llm.kinds)

            # soft_default（マーカー外の読み訂正）→ 共同編集者 leaf
            r3 = service.process_event(
                _event(4, "いや土ぬくしだよ", seconds=15),
                session_id=sid,
            )
            speeches3 = [a.text for a in r3.actions if a.layer == "speech" and a.text]
            self.assertEqual(len(speeches3), 1)
            self.assertEqual(llm.kinds.count("haiku_workshop_reply"), 2)
            self.assertTrue(is_open(sess.haiku_workshop))
            self.assertNotIn("player_chat", llm.kinds)

    def test_hard_off_topic_can_drift(self) -> None:
        """Stage2: 松明など明確な別件は chat 側で drift しうる。"""
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
            session = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            sid = session.session_id
            sess = service.sessions[sid]
            emission = _emission()
            service._open_haiku_workshop(sess, emission, entry_id="h_off", now=emission.created_at)
            from dogido_server.player_input.routing import route_player_input

            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=2,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="松明ある？"),
            )
            sess.machine.player_input = route_player_input("松明ある？")
            actions = service._haiku_workshop_actions(sess, event)
            self.assertEqual(actions, [])
            # miss 後に chat speech があると drift
            service._note_workshop_after_actions(
                sess,
                event,
                [AudioAction(layer="speech", interrupt=False, text="松明は持ってるで")],
            )
            self.assertTrue(is_open(sess.haiku_workshop))
            self.assertEqual(sess.haiku_workshop.drift_count if sess.haiku_workshop else -1, 1)

    def test_clear_lessons_without_workshop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
            session = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            sid = session.session_id
            sess = service.sessions[sid]
            store = MemoryStore(Path(tmp) / "mem")
            store.save_haiku_lesson(
                lesson_type="compress",
                note="要素を少し絞って余白を残すとよい",
                polarity="tighten",
            )
            self.assertTrue(store.list_recent_haiku_lessons(limit=3))
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
                sequence=4,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="もう気にせんで"),
            )
            sess.machine.player_input = __import__(
                "dogido_server.player_input", fromlist=["route_player_input"]
            ).route_player_input("もう気にせんで")
            actions = service._memory_input_actions(sess, event)
            self.assertEqual(1, len(actions))
            self.assertIn("気にせんでええ", actions[0].text)
            self.assertEqual(MemoryStore(Path(tmp) / "mem").list_recent_haiku_lessons(limit=3), [])

    def test_conversational_revise_closes_workshop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                llm_enabled=False,
                audio_enabled=False,
                decision_policy="py_trees",
                memory_enabled=True,
                memory_dir=Path(tmp) / "mem",
            )
            service = DogidoService(settings)
            session = service.create_session(
                AdapterSessionCreateRequest(
                    schema_version="2026-05-24",
                    adapter_name="test",
                    adapter_version="0",
                    game="minecraft",
                    player_name="p",
                    capabilities=[],
                )
            )
            sid = session.session_id
            sess = service.sessions[sid]
            emission = _emission()
            sess.last_haiku_emission = emission
            service._open_haiku_workshop(sess, emission, entry_id="h_test", now=emission.created_at)
            event = GameEvent(
                schema_version="2026-05-24",
                adapter="test",
                observed_at=emission.created_at + timedelta(seconds=5),
                sequence=3,
                event=EventDescriptor(
                    name=EventName.STATUS_SNAPSHOT,
                    source_kind=SourceKind.SYSTEM,
                    priority_hint=PriorityHint.BACKGROUND,
                    certainty=Certainty.HIGH,
                ),
                player=PlayerState(
                    name="p",
                    position=Position(x=0, y=64, z=0),
                    dimension="minecraft:overworld",
                ),
                world=WorldState(
                    time_phase=TimePhase.DAY,
                    weather=Weather.CLEAR,
                    biome="plains",
                    local_light=15,
                    sky_visible=True,
                ),
                meta=MetaState(user_text="こう直して: あさひさす / むらのどう / あかがね"),
            )
            sess.machine.player_input = __import__(
                "dogido_server.player_input", fromlist=["route_player_input"]
            ).route_player_input("こう直して: あさひさす / むらのどう / あかがね")
            actions = service._haiku_workshop_actions(sess, event)
            self.assertEqual(1, len(actions))
            self.assertIn("覚えといた", actions[0].text)
            self.assertIsNone(sess.haiku_workshop)


if __name__ == "__main__":
    unittest.main()
