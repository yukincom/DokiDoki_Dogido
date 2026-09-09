"""配線・状態・出典IDの試験。固定応答でモデルの意味理解を証明しない。"""

from copy import deepcopy
import json
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest

from dogido_server.language_dialogue.__main__ import DEFAULT_CASES, read_cases, run_cases
from dogido_server.language_dialogue.controller import KANJI_GRADE_CONFIRMATION, LanguageDialogue
from dogido_server.language_dialogue.retrieval import LocalDialogueSearch, SearchResult


def interpretation(text="漢字の三", **changes):
    return {
        "dialogue_act": "information_request",
        "topic": "language",
        "relation": "new",
        "question": "漢字の三の学年は？",
        "target": "三",
        "facet": "grade",
        "target_status": "explicit",
        "alternatives": [],
        "evidence": [{"turn_id": "t1", "quote": text}],
        "search_terms": ["三"],
        "clarification": "",
        **changes,
    }


def reply(**changes):
    return {
        "status": "answer",
        "text": "小学1年生やで。",
        "fact_ids": ["grade:三"],
        "application": "三という字の配当を説明",
        "missing": "",
        **changes,
    }


class ScriptedLLM:
    def __init__(self, *values, leaf_values=()):
        self.values = list(values)
        self.requests = []
        self.leaf_values = list(leaf_values)
        self.leaf_requests = []

    def generate_structured_json(self, request):
        self.requests.append(deepcopy(request))
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def generate_leaf_text(self, request):
        self.leaf_requests.append(deepcopy(request))
        return self.leaf_values.pop(0) if self.leaf_values else request.fallback_text


class SearchSpy:
    def __init__(self):
        self.calls = []

    def search(self, terms, **kwargs):
        self.calls.append((terms, kwargs))
        return SearchResult(
            terms,
            [
                {
                    "id": "grade:三",
                    "text_ja": "小学1年生",
                    "sources": [{"url": "https://www.mext.go.jp/"}],
                }
            ],
        )


def test_explicit_question_search_reply_and_raw_separated():
    search = SearchSpy()
    llm = ScriptedLLM(interpretation(), reply())
    dialogue = LanguageDialogue(llm, search)
    result = dialogue.turn("漢字の三", turn_id="t1", source="voice")
    assert result["raw_text"] == "漢字の三"
    assert result["status"] == "answer"
    assert result["mode_after"] == "language"
    assert search.calls == [(["三"], {"facet": "grade", "target": "三"})]
    assert result["references"][0]["id"] == "grade:三"


@pytest.mark.parametrize("text", ["おはようございます", "おはよう！", "ありがとう。"])
def test_representative_casual_turn_never_calls_model_search_or_web(text):
    llm, search, web = ScriptedLLM(), SearchSpy(), Mock()
    row = LanguageDialogue(llm, search, web=web).turn(text, turn_id="casual")
    assert row["status"] == "casual" and row["reply"]
    assert row["dialogue_act"] == "casual" and row["mode_after"] == "normal"
    assert not llm.requests and not search.calls
    web.search.assert_not_called()


def test_model_classified_casual_or_general_meta_turn_uses_existing_player_chat_leaf():
    for text, act in [("今日は楽しかった", "casual"), ("別の質問でもいい？", "other")]:
        value = interpretation(
            text, dialogue_act=act, topic="general", facet="other", target="",
            question=text, search_terms=[], evidence=[{"turn_id": "t1", "quote": text}],
        )
        llm, search, web = ScriptedLLM(value, leaf_values=("ええやん。続き聞かせてや。",)), SearchSpy(), Mock()
        row = LanguageDialogue(llm, search, web=web).turn(text, turn_id="t1")
        assert row["status"] == "player_chat" and "web_proposal" not in row
        assert row["reply"] == "ええやん。続き聞かせてや。"
        assert row["route_owner"] == "player_chat"
        assert llm.leaf_requests[0].kind == "player_chat"
        assert llm.leaf_requests[0].details["world_observation_available"] is False
        assert not search.calls
        web.search.assert_not_called()


def test_current_target_not_rejected_for_contextual_label_alone():
    llm = ScriptedLLM(interpretation(target_status="contextual"), reply())
    row = LanguageDialogue(llm, SearchSpy()).turn("漢字の三", turn_id="t1")
    assert row["status"] == "answer"


@pytest.mark.parametrize("target, grade", [("泳", 3), ("海", 2), ("3", 1), ("３", 1)])
def test_grade_answer_reuses_one_actual_lookup_without_answer_generation(monkeypatch, target, grade):
    from dogido_server.language_dialogue import retrieval

    calls = []
    original = retrieval.get_kanji_profile

    def tracked(character, **kwargs):
        calls.append(character)
        return original(character, **kwargs)

    monkeypatch.setattr(retrieval, "get_kanji_profile", tracked)
    text = f"漢字の{target}は何年生？"
    llm = ScriptedLLM(interpretation(text, target=target, search_terms=[target]))
    dialogue = LanguageDialogue(llm)
    row = dialogue.turn(text, turn_id="t1")
    char = "三" if target in {"3", "３"} else target
    assert calls == [char]
    assert row["reply"] == f"「{char}」は小学{grade}年生で習う漢字やで。"
    assert row["answer_origin"] == "code"
    assert row["references"][0]["allocation"]["school_grade"] == grade
    assert row["references"][0]["sources"]
    assert len(llm.requests) == 1
    assert dialogue.turn(text, turn_id="t1")["status"] == "duplicate"
    assert len(calls) == 1


@pytest.mark.parametrize("target, count", [("きゃんぷ", 3), ("コーヒー", 4), ("きって", 3), ("あいうえお", 5)])
def test_mora_reuses_counter_without_search_or_answer_generation(monkeypatch, target, count):
    from dogido_server.language_dialogue import verified_answers

    original = verified_answers.count_japanese_sounds
    counted = []

    def tracked(reading):
        counted.append(reading)
        return original(reading)

    monkeypatch.setattr(verified_answers, "count_japanese_sounds", tracked)
    text = f"『{target}』は何音？"
    search = SearchSpy()
    llm = ScriptedLLM(interpretation(text, target=target, facet="mora_count", search_terms=[target]))
    row = LanguageDialogue(llm, search).turn(text, turn_id="t1")
    assert row["reply"] == f"「{target}」は{count}音やで。"
    assert row["answer_origin"] == "code" and row["search"]["status"] == "computed"
    assert len(counted) == len(llm.requests) == 1
    assert not search.calls


@pytest.mark.parametrize("target, quoted", [
    ("きゃんぷ", "別の言葉"), ("学校", "学校"), ("きゃ/ん/ぷ", "きゃ/ん/ぷ"),
    ("ゃん", "ゃん"), ("ゑ", "ゑ"), ("くゝ", "くゝ"),
])
def test_uncertain_reading_is_confirmed_before_computing_or_searching(target, quoted):
    search = SearchSpy()
    text = f"『{quoted}』は何音？"
    llm = ScriptedLLM(interpretation(text, target=target, facet="mora_count", search_terms=[target]))
    row = LanguageDialogue(llm, search).turn(text, turn_id="t1")
    assert row["status"] == "clarify"
    assert not search.calls and len(llm.requests) == 1


def test_compound_grade_is_not_replaced_by_one_character_answer():
    from dogido_server.language_dialogue.contracts import Interpretation
    from dogido_server.language_dialogue.verified_answers import verified_reply

    facts = LocalDialogueSearch().search(["海", "水"], facet="grade", target="海水").facts
    assert verified_reply(Interpretation.model_validate(interpretation(target="海水")), facts) is None


def test_computed_reply_uses_existing_interruption_boundary(monkeypatch):
    from dogido_server.language_dialogue import verified_answers

    text = "『きゃんぷ』は何音？"
    llm = ScriptedLLM(interpretation(text, facet="mora_count", target="きゃんぷ"))
    dialogue = LanguageDialogue(llm, SearchSpy())
    original = verified_answers.count_japanese_sounds

    def interrupted(reading):
        result = original(reading)
        dialogue.interrupt()
        return result

    monkeypatch.setattr(verified_answers, "count_japanese_sounds", interrupted)
    row = dialogue.turn(text, turn_id="t1")
    assert row["status"] == "interrupted" and not row["reply"]
    assert not any(t["role"] == "assistant" for t in dialogue.history)
    assert len(llm.requests) == 1


def test_core_rules_and_application_limits_reach_reply_without_extra_search(monkeypatch):
    from dogido_server.language_dialogue import retrieval

    original = retrieval.search_japanese_knowledge
    records, calls = [], []

    def tracked(term, **kwargs):
        found = original(term, **kwargs)
        calls.append(term)
        records.extend(deepcopy(found))
        return found

    monkeypatch.setattr(retrieval, "search_japanese_knowledge", tracked)
    llm = ScriptedLLM(interpretation("短歌の決まりは？", target="短歌", facet="classification", search_terms=["短歌"]), {})
    LanguageDialogue(llm).turn("短歌の決まりは？", turn_id="t1")
    facts = {f["id"]: f for f in llm.requests[1].details["facts"]}
    assert calls == ["短歌"]
    checked = 0
    for record in records:
        if record.get("rules") and record["id"] in facts:
            fact = facts[record["id"]]
            assert fact["rules"] == record["rules"]
            assert fact["machine_use"] == record["machine_use"]
            checked += 1
    assert checked > 0


def test_clarification_precedes_search_and_yes_uses_actual_question():
    first = interpretation(
        "三は",
        target_status="ambiguous",
        alternatives=["漢字", "数"],
        clarification="漢字の三のこと？",
    )
    second = interpretation(
        "うん",
        relation="continue",
        target_status="contextual",
        evidence=[
            {"turn_id": "t2", "quote": "うん"},
            {"turn_id": "t1:reply", "quote": KANJI_GRADE_CONFIRMATION},
        ],
    )
    search = SearchSpy()
    llm = ScriptedLLM(first, second, reply())
    dialogue = LanguageDialogue(llm, search)
    assert dialogue.turn("三は何年生？", turn_id="t1")["status"] == "clarify"
    assert not search.calls
    assert dialogue.turn("うん", turn_id="t2")["status"] == "answer"
    assert llm.requests[1].details["focus"]["clarification"] == KANJI_GRADE_CONFIRMATION
    assert len(search.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence": []},
        {"evidence": [{"turn_id": "t1", "quote": "発話にない"}]},
        {"evidence": [{"turn_id": "future", "quote": "漢字の三"}]},
        {"target_status": "contextual", "target": "海"},
        {"__dogido_status": "schema_contract_error"},
        {"unexpected": True},
    ],
)
def test_invalid_interpretation_never_searches(changes):
    search = SearchSpy()
    result = LanguageDialogue(ScriptedLLM(interpretation(**changes)), search).turn(
        "漢字の三", turn_id="t1"
    )
    assert result["status"] == "clarify"
    assert not search.calls


@pytest.mark.parametrize(
    "value",
    [
        reply(fact_ids=["invented"]),
        reply(fact_ids=[]),
        reply(__dogido_status="generation_error"),
        {},
        RuntimeError("test"),
    ],
)
def test_invalid_reply_not_delivered_as_answer(value):
    result = LanguageDialogue(ScriptedLLM(interpretation(), value), SearchSpy()).turn(
        "漢字の三", turn_id="t1"
    )
    assert result["status"] == "unsupported"
    assert result["references"] == []


def test_direct_character_comparison_does_not_require_external_claim():
    llm = ScriptedLLM(
        interpretation("『月』と『月』", facet="comparison", target="月"),
        reply(
            text="同じ『月』やで。",
            fact_ids=["input-character:6708"],
            application="入力の同じ文字を比較",
        ),
    )
    assert (
        LanguageDialogue(llm, SearchSpy()).turn("『月』と『月』", turn_id="t1")["status"]
        == "answer"
    )


def test_semantic_comparison_requires_sources_even_with_application():
    llm = ScriptedLLM(
        interpretation(facet="comparison"), reply(fact_ids=[], application="比較した")
    )
    assert (
        LanguageDialogue(llm, SearchSpy()).turn("漢字の三", turn_id="t1")["status"] == "unsupported"
    )


def test_topic_switch_clears_focus_no_world_action():
    llm = ScriptedLLM(
        interpretation(),
        reply(),
        interpretation(
            "木を集めよう",
            topic="minecraft",
            facet="other",
            relation="switch",
            evidence=[{"turn_id": "t2", "quote": "木を集めよう"}],
        ),
    )
    dialogue = LanguageDialogue(llm, SearchSpy())
    dialogue.turn("漢字の三", turn_id="t1")
    result = dialogue.turn("木を集めよう", turn_id="t2")
    assert result["status"] == "handoff" and result["mode_after"] == "normal"
    assert not dialogue.focus.question
    assert not result["reply"]
    assert len(llm.requests) == 3
    assert sum(row["turn_id"] == "t2" for row in dialogue.history) == 1


def test_duplicate_input_does_not_regenerate():
    llm = ScriptedLLM(interpretation(), reply())
    dialogue = LanguageDialogue(llm, SearchSpy())
    dialogue.turn("漢字の三", turn_id="t1")
    assert dialogue.turn("漢字の三", turn_id="t1")["status"] == "duplicate"
    assert len(llm.requests) == 2


def test_interruption_holds_focus_and_release_does_not_speak():
    llm = ScriptedLLM(interpretation(), reply())
    dialogue = LanguageDialogue(llm, SearchSpy())
    dialogue.turn("漢字の三", turn_id="t1")
    dialogue.interrupt()
    assert dialogue.turn("続き", turn_id="t2")["status"] == "paused"
    assert dialogue.focus.target == "三"
    assert dialogue.release() == {"control": "release", "mode": "language", "paused": False}
    assert len(llm.requests) == 2


def test_standalone_interrupt_discards_deferred_reply_ownership():
    llm = ScriptedLLM(interpretation(), reply())
    dialogue = LanguageDialogue(llm, SearchSpy())
    row = dialogue.turn(
        "漢字の三",
        turn_id="t1",
        defer_reply_history=True,
    )

    dialogue.interrupt()

    assert not dialogue.confirm_delivered_reply("t1", row["reply"])


@pytest.mark.parametrize("interrupt_at", [1, 2])
def test_inflight_reply_invalidated_and_concurrent_input_not_queued(interrupt_at):
    entered, resume = threading.Event(), threading.Event()

    class BlockingLLM(ScriptedLLM):
        def generate_structured_json(self, request):
            value = super().generate_structured_json(request)
            if len(self.requests) == interrupt_at:
                entered.set()
                assert resume.wait(5)
            return value

    llm = BlockingLLM(interpretation(), reply())
    dialogue = LanguageDialogue(llm, SearchSpy())
    results = []
    worker = threading.Thread(
        target=lambda: results.append(dialogue.turn("漢字の三", turn_id="t1"))
    )
    worker.start()
    try:
        assert entered.wait(5)
        assert dialogue.turn("次の入力", turn_id="t2")["status"] == "busy"
        dialogue.interrupt()
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results[0]["status"] == "interrupted" and results[0]["reply"] == ""
    assert list(dialogue.history) == [
        {"turn_id": "t1", "role": "user", "text": "漢字の三", "source": "text"}
    ]
    dialogue.release()
    llm.values = [
        interpretation(
            "続き",
            target_status="contextual",
            relation="resume",
            evidence=[
                {"turn_id": "t2", "quote": "続き"},
                {"turn_id": "t1", "quote": "漢字の三"},
            ],
        ),
        reply(),
    ]
    assert dialogue.turn("続き", turn_id="t2")["status"] == "answer"
    assert llm.requests[-2].details["history"][0]["text"] == "漢字の三"


def test_expired_context_not_reused():
    now = [0.0]
    llm = ScriptedLLM(
        interpretation(),
        reply(),
        interpretation(
            "それ", target_status="ambiguous", evidence=[{"turn_id": "t2", "quote": "それ"}]
        ),
    )
    dialogue = LanguageDialogue(llm, SearchSpy(), clock=lambda: now[0], ttl_seconds=10)
    dialogue.turn("漢字の三", turn_id="t1")
    now[0] = 11
    result = dialogue.turn("それ", turn_id="t2")
    assert result["context_expired"] is True
    assert llm.requests[2].details["history"] == []
    assert llm.requests[2].details["focus"]["target"] == ""


def test_kanji_allocation_from_correct_dataset():
    result = LocalDialogueSearch().search(["三"], facet="grade", target="三")
    assert result.status == "searched"
    assert any(
        f["id"] == "kanji.grade-allocation.u4e09" and "第1学年" in f["text_ja"]
        for f in result.facts
    )
    assert not any("allocation_level" in json.dumps(f) for f in result.facts)


def test_grade_search_does_not_pick_meta_characters_from_target_phrase():
    result = LocalDialogueSearch().search(["3 漢字 習う学年"], facet="grade", target="3の漢字")
    assert any(f["id"] == "kanji.grade-allocation.u4e09" for f in result.facts)
    assert not any(
        f["id"] in {"kanji.grade-allocation.u6f22", "kanji.grade-allocation.u5b57"}
        for f in result.facts
    )


def test_takumi_is_not_educational_vocabulary_homophone():
    result = LocalDialogueSearch().search(["たくみ"], facet="meaning", target="匠")
    assert any(f["id"] == "language.usage.takumi" for f in result.facts)
    assert not any("巧み" in f["text_ja"] for f in result.facts)


def test_missing_cards_report_unavailable(tmp_path):
    result = LocalDialogueSearch(cards_path=tmp_path / "absent").search(
        ["三"], facet="grade", target="三"
    )
    assert result.status == "unavailable"


def test_fixtures_preserve_user_extra_and_do_not_leak_review():
    cases = read_cases(DEFAULT_CASES)
    assert len([c for c in cases if c["origin"] == "user"]) == 15
    extra = next(c for c in cases if c["id"] == "boundary_14")
    assert extra["turns"][0]["text"] == "ツルハシって感じは何年生で習うの？"
    llm = ScriptedLLM(interpretation(extra["turns"][0]["text"], target_status="ambiguous"))
    records = []
    run_cases([extra], llm, records.append)
    serialized = json.dumps(llm.requests[0].details, ensure_ascii=False)
    assert "boundary_14" not in serialized and extra["review"] not in serialized
    assert records[0]["human_review"] == "pending"


def test_source_cards_have_unique_ids_sources_and_scopes():
    path = Path(__file__).resolve().parents[1] / "dogido_server/language_dialogue/source_cards.json"
    cards = json.loads(path.read_text())["records"]
    assert len(cards) == len({c["id"] for c in cards})
    assert all(
        c["sources"] and c["scope"] and c["claim_status"] and c["search_terms"] for c in cards
    )


def test_rephrase_uses_known_target_without_requiring_repeated_past_quote():
    llm = ScriptedLLM(
        interpretation(),
        reply(),
        interpretation(
            "もっと簡単に",
            relation="correct",
            target_status="contextual",
            evidence=[{"turn_id": "t2", "quote": "もっと簡単に"}],
        ),
        reply(),
    )
    dialogue = LanguageDialogue(llm, SearchSpy())
    dialogue.turn("漢字の三", turn_id="t1")
    assert dialogue.turn("もっと簡単に", turn_id="t2")["status"] == "answer"


def test_language_facet_is_not_overridden_by_game_subject():
    llm = ScriptedLLM(interpretation(topic="minecraft"), reply())
    result = LanguageDialogue(llm, SearchSpy()).turn("漢字の三", turn_id="t1")
    assert result["status"] == "answer"
    assert result["interpretation"]["topic"] == "minecraft"
    assert result["effective_interpretation"]["topic"] == "language"


def test_no_sources_does_not_call_reply_model():
    class EmptySearch:
        def search(self, terms, **kwargs):
            return SearchResult(terms, [])

    llm = ScriptedLLM(interpretation())
    result = LanguageDialogue(llm, EmptySearch()).turn("漢字の三", turn_id="t1")
    assert result["status"] == "unsupported" and result["reply_status"] == "no_evidence"
    assert len(llm.requests) == 1


@pytest.mark.parametrize(
    "text", ["三は何年生で習う？", "数字の三はいつ習う？", "ツルハシって感じは何年生で習うの？"]
)
def test_kanji_grade_requires_writing_scope_not_just_a_number_or_stt_spelling(text):
    search = SearchSpy()
    result = LanguageDialogue(ScriptedLLM(interpretation(text)), search).turn(text, turn_id="t1")
    assert result["status"] == "clarify" and not search.calls
