"""公開Web経路の配線試験。固定モデルで意味理解の品質を証明しない。"""

from copy import deepcopy
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import anyio

import pytest

from dogido_server.language_dialogue.__main__ import read_cases, run_cases
from dogido_server.language_dialogue.chrome_web import ChromeWebClient
from dogido_server.language_dialogue.child_resources import ChildResources
from dogido_server.language_dialogue.controller import LanguageDialogue
from dogido_server.language_dialogue.retrieval import SearchResult
from dogido_server.language_dialogue.web_research import (
    RETURN_INVITATION,
    TEACHER_SUGGESTION,
    WebResearch,
    approved_url,
)


URL = "https://www2.ninjal.ac.jp/Onomatope/column/nihongo_1.html"
# 配線用の人為的な資料。実ページの取得・内容評価は別の実モデル試験。
BODY = "配線試験用。人や動物の声を表す語を擬声語と呼ぶ分類があります。わんわんなどが例です。" * 3
QUOTE = "人や動物の声を表す語を擬声語と呼ぶ分類があります。"
QUESTION = "擬声語って何？"


def turn_with_web_permission(dialogue, text, **kwargs):
    """従来の取得後試験用。実際の同意入力と明示的な模擬再生完了を必ず通す。"""
    row = dialogue.turn(text, **kwargs)
    if row["status"] != "web_consent_requested":
        return row
    assert "web" not in row and dialogue.research is None
    consent = dialogue.turn("ええで", turn_id=kwargs["turn_id"] + "-consent")
    assert consent["status"] == "web_waiting_playback" and dialogue.research is None
    played = dialogue.on_speech_playback_result(
        consent["speech"]["utterance_id"], status="completed", event_id=kwargs["turn_id"] + "-played",
    )
    return {**row, **played}


class LLM:
    def __init__(self, *values):
        self.values = list(values)
        self.requests = []

    def generate_structured_json(self, request):
        self.requests.append(deepcopy(request))
        if request.kind == "language_research_intent" and callable(self.values[0]):
            return research_turn("report", request.details["current"]["text"])
        value = self.values.pop(0)
        return value(request) if callable(value) else value


def interpretation(text=QUESTION, **changes):
    return dict(
        topic="language",
        relation="new",
        question=text,
        target="擬声語",
        facet="classification",
        target_status="explicit",
        alternatives=[],
        evidence=[{"turn_id": "t1", "quote": text}],
        search_terms=["擬声語"],
        clarification="",
        lookup_requested=False,
        **changes,
    )


def local_reply(status="answer", **changes):
    return {
        "status": status,
        "text": "声を表す語やで。",
        "fact_ids": ["local:1"],
        "application": "分類の説明",
        "missing": "",
        **changes,
    }


class Search:
    def __init__(self, *, found=True):
        self.calls = []
        self.facts = (
            [
                {
                    "id": "local:1",
                    "text_ja": "声を表す語。",
                    "sources": [{"title_ja": "資料", "url": URL}],
                }
            ]
            if found
            else []
        )

    def search(self, terms, **kwargs):
        self.calls.append((terms, kwargs))
        return SearchResult(terms, deepcopy(self.facts), "searched")


class Client:
    def __init__(self, *, search=None, fetch=None):
        self.calls = []
        self.search = (
            search if search is not None else {"success": True, "data": {"web": [{"url": URL}]}}
        )
        self.fetch = (
            fetch
            if fetch is not None
            else {"success": True, "data": {"final_url": URL, "title": "分類", "markdown": BODY}}
        )

    def call(self, name, arguments, **kwargs):
        self.calls.append((name, deepcopy(arguments)))
        return deepcopy(self.search if name == "google_search" else self.fetch)


def research_turn(intent, text, **changes):
    return {"intent": intent, "evidence": text, **changes}


def reflection(request):
    page = request.details["research"]["pages"][0]
    return {
        "perspective": "わんわんは犬の声を表す言葉や",
        "quotes": [{"page_id": page["id"], "quote": QUOTE}],
    }


@pytest.mark.parametrize(
    "url, expected",
    [
        (URL, True),
        ("https://www.mext.go.jp/a.html", True),
        ("https://www.nao.ac.jp/", True),
        ("https://a.ninjal.ac.jp/", True),
        ("https://ninjal.ac.jp.evil.example/", False),
        ("https://person.example/my-database", False),
        ("https://user@ninjal.ac.jp/", False),
        ("http://127.0.0.1/", False),
        ("file:///tmp/test", False),
        ("https://ninjal.ac.jp:xyz/", False),
        ("https://ninjal.ac.jp:5055/", False),
    ],
)
def test_source_authority(url, expected):
    assert approved_url(url) is expected


@pytest.mark.parametrize("trigger", ["no_local_facts", "uncertain_reply", "explicit_request"])
def test_search_trigger_and_body_handoff(trigger):
    first = interpretation()
    first["lookup_requested"] = trigger == "explicit_request"
    values = [first]
    if trigger == "uncertain_reply":
        values.append(local_reply("partial", missing="今の資料では確かめられない部分", missing_kind="evidence"))
    llm = LLM(*values, reflection)
    client = Client()
    events = []
    dialogue = LanguageDialogue(
        llm,
        Search(found=trigger != "no_local_facts"),
        web=WebResearch(client),
        on_event=events.append,
    )
    first_result = turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    assert first_result["status"] == "reference_only"
    assert first_result["web"]["trigger_reason"] == trigger
    assert first_result["web"]["search_status"] == "searched"
    assert first_result["web"]["context_page_ids"] == [dialogue.research.pages[0]["id"]]
    assert [c[0] for c in client.calls] == ["google_search", "fetch_url"]
    assert QUESTION not in client.calls[0][1]["query"]
    assert events[0]["text"] == "ちょっと調べてみよか！"
    second = dialogue.turn("わんわんは犬の言葉だから、まねじゃないんだね", turn_id="t2")
    assert second["status"] == "research_reflection"
    assert second["quote_validation"] == "matched"
    assert llm.requests[-1].details["research"]["pages"][0]["text_ja"] == BODY
    assert "pages" not in llm.requests[-2].details["research"]
    assert second["source_quotes"][0]["quote"] == QUOTE


@pytest.mark.parametrize("response", [local_reply(), {}, local_reply(fact_ids=["invented"])])
def test_known_or_malformed_answer_not_arbitrary_search_trigger(response):
    client = Client()
    dialogue = LanguageDialogue(LLM(interpretation(), response), Search(), web=WebResearch(client))
    turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    assert not client.calls


def test_no_web_by_default_even_when_local_facts_missing():
    dialogue = LanguageDialogue(LLM(interpretation()), Search(found=False))
    row = turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    assert row["status"] == "unsupported" and "web" not in row


@pytest.mark.parametrize("kind", ["none", "context", "evidence"])
def test_gap_kind_separates_context_confirmation_from_web(kind):
    client = Client()
    question = "その言葉が出てくる文を教えてくれる？"
    response = local_reply("partial", missing="使われている文が必要", missing_kind=kind,
                           clarification=question if kind == "context" else "")
    dialogue = LanguageDialogue(LLM(interpretation(), response), Search(), web=WebResearch(client))
    row = turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    assert bool(client.calls) == (kind == "evidence")
    if kind == "context":
        assert row["status"] == "clarify" and row["reply"] == question
        assert dialogue.focus.clarification == question


@pytest.mark.parametrize("clarification", ["その文を見せてくれる？", ""])
def test_context_confirmation_needs_no_answer_citation(clarification):
    client = Client()
    response = local_reply("unsupported", fact_ids=[], missing_kind="context",
                           missing="文脈", clarification=clarification)
    row = LanguageDialogue(LLM(interpretation(), response), Search(), web=WebResearch(client)).turn(
        QUESTION, turn_id="t1")
    assert row["status"] == "clarify" and row["reply"]
    assert not client.calls


def test_legacy_partial_reply_does_not_implicitly_request_web():
    client = Client()
    response = local_reply("partial", missing="詳しい補足はない")
    LanguageDialogue(LLM(interpretation(), response), Search(), web=WebResearch(client)).turn(
        QUESTION, turn_id="t1")
    assert not client.calls


def test_explicit_web_request_is_not_replaced_by_computed_answer():
    text = "『きゃんぷ』の音数をネットで調べて"
    first = interpretation(text)
    first.update(target="きゃんぷ", facet="mora_count", search_terms=["きゃんぷ"], lookup_requested=True)
    client, search, llm = Client(), Search(), LLM(first)
    row = turn_with_web_permission(LanguageDialogue(llm, search, web=WebResearch(client)), text, turn_id="t1")
    assert row["web"]["trigger_reason"] == "explicit_request"
    assert client.calls and not search.calls
    assert len(llm.requests) == 1 and "answer_origin" not in row


@pytest.mark.parametrize("kind", ["ambiguous", "minecraft"])
def test_unclear_question_or_game_does_not_search(kind):
    first = interpretation("三は何年生？" if kind == "ambiguous" else "冒険に戻ろう")
    if kind == "ambiguous":
        first.update(facet="grade", target="三")
    else:
        first.update(topic="minecraft", facet="other", relation="end")
    client, search = Client(), Search()
    dialogue = LanguageDialogue(LLM(first), search, web=WebResearch(client))
    row = dialogue.turn(first["question"], turn_id="t1")
    assert row["status"] == ("clarify" if kind == "ambiguous" else "handoff")
    assert not client.calls and not search.calls


@pytest.mark.parametrize(
    "fetch, reason",
    [
        (
            {"success": True, "data": {"final_url": "https://example.com/", "markdown": BODY}},
            "source_not_approved",
        ),
        ({"success": True, "data": {"final_url": URL, "markdown": ""}}, "empty_page_body"),
        ({"success": False}, "fetch_failed"),
    ],
)
def test_no_page_context_without_approved_body(fetch, reason):
    result = WebResearch(Client(fetch=fetch)).search("擬声語", [], "classification")
    assert not result.pages and result.status == "no_readable_source"
    assert result.events[-1]["reason"] == reason


def test_captcha_is_not_read_success_and_is_not_retried():
    client = Client(search={"success": False, "captcha_required": True})
    web = WebResearch(client)
    first = web.search("擬声語", [], "classification")
    assert first.status == "captcha" and not first.pages
    second = web.search("擬声語", [], "classification", known_urls=[URL])
    assert second.status == "read" and second.search_status == "captcha"
    assert [c[0] for c in client.calls].count("google_search") == 1
    assert second.pages[0]["text_ja"] == BODY


def test_diagnostic_known_source_mode_never_calls_google():
    client = Client()
    result = WebResearch(client, known_sources_only=True).search(
        "擬声語", [], "classification", known_urls=[URL]
    )
    assert result.status == "read" and result.search_status == "known_sources_only"
    assert [c[0] for c in client.calls] == ["fetch_url"]


def test_search_snippet_is_not_substituted_for_body_and_pdf_not_read():
    client = Client(
        search={
            "success": True,
            "data": {
                "web": [
                    {"url": URL + ".pdf", "description": BODY},
                    {"url": "https://example.com/", "description": BODY},
                ]
            },
        }
    )
    result = WebResearch(client).search("擬声語", [], "classification")
    assert result.status == "no_readable_source" and not result.pages
    assert len(client.calls) == 1


@pytest.mark.parametrize("quote_change", ["wrong_id", "fabricated", "whitespace"])
def test_unsupported_reflection_is_not_spoken_as_page_content(quote_change):
    def invalid(request):
        value = reflection(request)
        value["quotes"][0]["page_id" if quote_change == "wrong_id" else "quote"] = (
            " \n" * 10 if quote_change == "whitespace" else "存在しない資料の文章です"
        )
        return value

    dialogue = LanguageDialogue(
        LLM(interpretation(), invalid), Search(found=False), web=WebResearch(Client())
    )
    turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    row = dialogue.turn("まねじゃないんだね", turn_id="t2")
    assert row["status"] == "research_uncertain"
    assert "犬の声を表す" not in row["reply"]
    assert not row["references"]


def test_teacher_offer_continuation_and_return_clear_context():
    values = [
        interpretation(),
        reflection,
        research_turn("uncertain", "モヤっとする"),
        research_turn("continue", "まだ冒険に戻らない"),
        research_turn("return", "今度は冒険しよう"),
    ]
    llm = LLM(*values)
    dialogue = LanguageDialogue(llm, Search(found=False), web=WebResearch(Client()))
    turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    dialogue.turn("まねじゃないんだね", turn_id="t2")
    row = dialogue.turn("モヤっとする", turn_id="t3")
    assert row["reply"] == TEACHER_SUGGESTION + RETURN_INVITATION
    assert row["status"] == "return_offered" and row["mode_after"] == "language"
    row = dialogue.turn("まだ冒険に戻らない", turn_id="t4")
    assert row["status"] == "research_continue" and dialogue.research is not None
    row = dialogue.turn("今度は冒険しよう", turn_id="t5")
    assert row["status"] == "handoff" and row["mode_after"] == "normal"
    assert dialogue.research is None and not dialogue.focus.target
    assert "text_ja" not in str(list(dialogue.history))


@pytest.mark.parametrize("control", ["cancel", "interrupt"])
@pytest.mark.parametrize("stage", ["fetch", "reflection"])
def test_pending_work_invalidated_by_control(control, stage):
    entered, resume = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert resume.wait(5)

    class BlockingClient(Client):
        def call(self, name, arguments, **kwargs):
            if stage == "fetch" and name == "fetch_url":
                block()
            return super().call(name, arguments)

    def slow_reflection(request):
        block()
        return reflection(request)

    dialogue = LanguageDialogue(
        LLM(interpretation(), slow_reflection),
        Search(found=False),
        web=WebResearch(BlockingClient()),
    )
    if stage == "reflection":
        turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            turn_with_web_permission(dialogue,
                QUESTION if stage == "fetch" else "まねじゃないんだね",
                turn_id="t1" if stage == "fetch" else "t2",
            )
        )
    )
    worker.start()
    try:
        assert entered.wait(5)
        assert dialogue.turn("別の入力", turn_id="busy")["status"] == "busy"
        getattr(dialogue, control)()
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results[0]["status"] == "interrupted" and not results[0]["reply"]
    if stage == "fetch" or control == "cancel":
        assert dialogue.research is None
    if control == "cancel":
        assert dialogue.mode == "normal" and not dialogue.history
    else:
        assert dialogue.paused


def test_missing_install_does_not_launch_anything(tmp_path):
    with ChromeWebClient(command=tmp_path / "missing") as client:
        with pytest.raises(RuntimeError, match="not_installed"):
            client.call("health_check", {})
        assert client._thread is None


def test_later_fetch_error_keeps_body_already_read():
    second_url = "https://www.ninjal.ac.jp/second/"

    class PartialClient(Client):
        def call(self, name, arguments, **kwargs):
            if name == "fetch_url" and arguments["url"] == second_url:
                raise TimeoutError("not logged")
            return super().call(name, arguments)

    result = WebResearch(PartialClient()).search(
        "擬声語", [], "classification", known_urls=[second_url]
    )
    assert result.status == "read" and len(result.pages) == 1
    assert any(e.get("reason") == "TimeoutError" for e in result.events)


@pytest.mark.parametrize("stage", ["initialize", "call"])
@pytest.mark.parametrize("stop_by", ["timeout", "cancel", "close"])
def test_sdk_connection_closes_during_stalled_work_and_can_reconnect(monkeypatch, stage, stop_by):
    state = {"closed": 0, "stall": True}
    entered, cancelled = threading.Event(), threading.Event()

    @asynccontextmanager
    async def transport(_):
        try:
            yield None, None
        finally:
            state["closed"] += 1

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def initialize(self):
            if stage == "initialize" and state["stall"]:
                entered.set()
                await anyio.sleep_forever()

        async def call_tool(self, *_):
            if stage == "call" and state["stall"]:
                entered.set()
                await anyio.sleep_forever()
            return SimpleNamespace(
                isError=False, content=[SimpleNamespace(type="text", text='{"success": true}')]
            )

    monkeypatch.setitem(
        sys.modules,
        "mcp",
        SimpleNamespace(ClientSession=lambda *_: Session(), StdioServerParameters=lambda **kw: kw),
    )
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", SimpleNamespace(stdio_client=transport))
    client = ChromeWebClient(
        command=Path(sys.executable), timeout=0.3 if stop_by == "timeout" else 2
    )
    failures = []

    def run():
        try:
            client.call("health_check", {}, cancelled=cancelled.is_set)
        except (RuntimeError, TimeoutError) as exc:
            failures.append(type(exc).__name__)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(3)
        if stop_by == "cancel":
            cancelled.set()
        elif stop_by == "close":
            client.close()
        worker.join(4)
        assert not worker.is_alive() and failures
        assert state["closed"] == 1 and client._thread is None
        state["stall"] = False
        if stop_by == "close":
            with pytest.raises(RuntimeError, match="closed"):
                client.call("health_check", {})
            return
        assert client.call("health_check", {}) == {"success": True}
    finally:
        client.close()
        worker.join(4)
    assert state["closed"] == 2 and client._thread is None


def test_reading_context_outlives_short_dialogue_ttl_but_is_not_permanent():
    now = [0.0]
    llm = LLM(interpretation(), reflection)
    dialogue = LanguageDialogue(
        llm, Search(found=False), clock=lambda: now[0], web=WebResearch(Client())
    )
    turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    now[0] = 301
    assert dialogue.turn("犬の言葉なんだね", turn_id="t2")["status"] == "research_reflection"
    now[0] += 1801
    value = interpretation("それ")
    value.update(target_status="ambiguous", evidence=[{"turn_id": "t3", "quote": "それ"}])
    llm.values.append(value)
    row = dialogue.turn("それ", turn_id="t3")
    assert row["context_expired"] and dialogue.research is None
    assert "research" not in llm.requests[-1].details


def test_web_fixtures_are_four_separate_cases_and_future_turns_are_not_prompted():
    path = Path(__file__).parent / "fixtures/language_dialogue/web_cases.json"
    cases = read_cases(path)
    assert len(cases) == 4
    first = deepcopy(cases[0])
    first["turns"] = first["turns"][:3]
    query = first["turns"][0]["text"]
    value = interpretation(query)
    value["lookup_requested"] = True
    llm, rows = LLM(value), []
    run_cases(
        [first],
        llm,
        rows.append,
        dialogue_factory=lambda: LanguageDialogue(llm, Search(), web=WebResearch(Client())),
    )
    assert rows[0]["automatic_check"] == rows[2]["web_read_check"] == "pass"
    assert "web" not in rows[0] and "web" not in rows[1]
    assert "expect_web_read" not in str(llm.requests)
    assert cases[0]["turns"][3]["text"] not in str(llm.requests)


def test_reference_browser_is_hidden_child_browser_is_separate():
    import json

    reference, child = ChromeWebClient(), ChromeWebClient(child_view=True)
    assert reference.config != child.config
    assert json.loads(Path(reference.config).read_text())["show_browser"] is False
    assert json.loads(Path(child.config).read_text())["show_browser"] is True
    assert reference._thread is None and child._thread is None


def test_child_catalog_checks_grade_topic_and_question_facet():
    resources = ChildResources()
    assert (
        resources.select("方位", [], school_grade=3, facet="meaning")["id"]
        == "gsi.direction.grade3_4"
    )
    assert resources.select("方位", [], school_grade=2, facet="meaning") is None
    assert resources.select("方位", [], school_grade=3, facet="grade") is None
    assert resources.select("擬音語", [], school_grade=3, facet="meaning") is None
    assert resources.select("歌舞伎", [], school_grade=3, facet="meaning") is None
    assert not next(r for r in resources.records if r["id"] == "mext.learning_portal")["auto_open"]
    # 教育に使う資料でも、本人が調べる送り先としてユーザーが除外したものは候補に残さない。
    assert {"tokyo.basic_drill", "ninjal.child_pamphlet"}.isdisjoint(
        r["id"] for r in resources.records
    )


def test_adult_reference_is_never_substituted_for_child_page():
    child = Client()
    llm = LLM(interpretation())
    dialogue = LanguageDialogue(
        llm, Search(found=False), web=WebResearch(Client(), child_client=child)
    )
    row = turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    assert row["status"] == "reference_only"
    assert row["web"]["child_status"] == "no_matching_resource"
    assert "ページを開いた" not in row["reply"]
    assert not child.calls
    assert {p["use"] for p in dialogue.research.pages} == {"background_reference"}


def test_both_views_reach_reading_and_return_leaves_only_topic():
    child_url = "https://www.gsi.go.jp/KIDS/KIDS03.html"
    child_body = "方位の説明。方向を表すのに東西南北を用います。" * 5
    child = Client(
        fetch={
            "success": True,
            "data": {
                "final_url": child_url,
                "title": "方位",
                "markdown": child_body,
                "expected_url_verified": True,
            },
        }
    )
    first = interpretation("方位を調べたい")
    first.update(target="方位", search_terms=["方位"], facet="meaning", lookup_requested=True)
    llm = LLM(first, reflection, research_turn("return", "冒険に戻ろう"))
    dialogue = LanguageDialogue(llm, Search(), web=WebResearch(Client(), child_client=child))
    row = turn_with_web_permission(dialogue, "方位を調べたい", turn_id="t1")
    assert row["status"] == "awaiting_report" and row["web"]["child_status"] == "opened"
    assert child.calls == [
        (
            "fetch_url",
            {
                "url": child_url,
                "expected_url": child_url,
                "format": "markdown",
                "char_limit": 15000,
            },
        )
    ]
    dialogue.turn("分かったことを話すね", turn_id="t2")
    pages = llm.requests[-1].details["research"]["pages"]
    assert {p["use"] for p in pages} == {"background_reference", "child_resource"}
    assert {p["text_ja"] for p in pages} == {BODY, child_body}
    assert all(p["coverage"] == "extracted_text_only" for p in pages)
    row = dialogue.turn("冒険に戻ろう", turn_id="t3")
    assert row["mode_after"] == "normal"
    assert row["return_context"] == {"researched_topic": "方位を調べたい"}
    assert dialogue.research is None and not dialogue.focus.target
    assert BODY not in str(dialogue.history) and child_body not in str(dialogue.history)
    assert "犬の声" not in str(dialogue.history)
    next_turn = interpretation("木を集めよう")
    next_turn.update(
        topic="minecraft",
        facet="other",
        relation="switch",
        evidence=[{"turn_id": "t4", "quote": "木を集めよう"}],
    )
    llm.values.append(next_turn)
    dialogue.turn("木を集めよう", turn_id="t4")
    details = llm.requests[-1].details
    assert details["recent_research"] == {"researched_topic": "方位を調べたい"}
    assert "research" not in details
    assert BODY not in str(details) and child_body not in str(details)


def test_redirected_child_resource_not_treated_as_reviewed():
    child = Client(fetch={"success": True, "data": {"final_url": URL, "markdown": BODY}})
    result = WebResearch(Client(), child_client=child).search("方位", ["方位"], "meaning")
    assert result.child_status == "unavailable"
    assert result.status == "read"  # 裏で読んだ根拠まで失敗にはしない。
    assert all(p["use"] == "background_reference" for p in result.pages)


@pytest.mark.parametrize("method", ["cancel", "expiry"])
def test_topic_only_retained_on_other_returns(method):
    now = [0.0]
    dialogue = LanguageDialogue(
        LLM(interpretation()), Search(found=False), web=WebResearch(Client()), clock=lambda: now[0]
    )
    turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    if method == "cancel":
        dialogue.cancel()
    else:
        now[0] = 1801
        next_turn = interpretation("冒険に戻ろう")
        next_turn.update(
            topic="minecraft",
            facet="other",
            relation="end",
            evidence=[{"turn_id": "t2", "quote": "冒険に戻ろう"}],
        )
        dialogue.llm.values.append(next_turn)
        dialogue.turn("冒険に戻ろう", turn_id="t2")
    assert dialogue.return_context() == {"researched_topic": QUESTION}
    assert dialogue.research is None
    assert BODY not in str(dialogue.history)


def test_new_local_question_does_not_erase_previous_research_topic():
    next_question = interpretation("声を表す言葉を教えて")
    next_question.update(evidence=[{"turn_id": "t2", "quote": "声を表す言葉を教えて"}])
    llm = LLM(
        interpretation(),
        research_turn("new_question", "声を表す言葉を教えて"),
        next_question,
        local_reply(),
    )
    search = Search(found=False)
    dialogue = LanguageDialogue(llm, search, web=WebResearch(Client()))
    turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    search.facts = Search().facts
    row = dialogue.turn("声を表す言葉を教えて", turn_id="t2")
    assert row["status"] == "answer"
    assert dialogue.research is None
    dialogue.cancel()
    assert dialogue.return_context() == {"researched_topic": QUESTION}
    assert not dialogue.history


def test_child_client_requires_pre_display_url_check(monkeypatch):
    calls = []

    @asynccontextmanager
    async def transport(_):
        yield None, None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(
                tools=[SimpleNamespace(name="fetch_url", inputSchema={"properties": {"url": {}}})]
            )

        async def call_tool(self, *args):
            calls.append(args)

    monkeypatch.setitem(
        sys.modules,
        "mcp",
        SimpleNamespace(ClientSession=lambda *_: Session(), StdioServerParameters=lambda **kw: kw),
    )
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", SimpleNamespace(stdio_client=transport))
    with ChromeWebClient(command=Path(sys.executable), child_view=True) as client:
        with pytest.raises(RuntimeError, match="start_failed"):
            client.call("fetch_url", {"url": URL})
        assert not calls
        assert client._thread is None
