"""仮想Webと前面復帰イベントの独立試験。OS・ネット・音声は使わない。"""

from dataclasses import asdict
import json
import threading

import pytest

from dogido_server.language_dialogue.__main__ import DEFAULT_VIRTUAL_WEB, read_cases, run_cases
from dogido_server.language_dialogue.browser_visit import WELCOME_BACK
from dogido_server.language_dialogue.controller import LanguageDialogue
from dogido_server.language_dialogue.google_overview import GoogleOverviewResearch
from dogido_server.language_dialogue.virtual_overview import VirtualOverviewResearch
from test_language_google_overview import OverviewClient
from test_language_web_research import LLM, Search, interpretation, research_turn, turn_with_web_permission


QUESTION = "金床の読み方を調べて"
QUOTE = "かな（金） ＝ 鉄や金属のこと"


def first_interpretation():
    value = interpretation(QUESTION)
    value.update(target="金床", facet="reading", search_terms=["金床"], lookup_requested=True)
    return value


def reflect(request):
    page = request.details["research"]["pages"][0]
    assert page["simulation"] is True
    assert page["claim_status"] == "simulated_replay_of_ai_summary_not_verified_fact"
    return {"perspective": "概要では、かなは鉄や金属のことやと説明されてるな。",
            "quotes": [{"page_id": page["id"], "quote": QUOTE}]}


def started(*replies, **kwargs):
    llm = LLM(first_interpretation(), *replies)
    web = kwargs.pop("web", None) or VirtualOverviewResearch(DEFAULT_VIRTUAL_WEB)
    dialogue = LanguageDialogue(llm, Search(found=False), web=web, **kwargs)
    row = turn_with_web_permission(dialogue, QUESTION, turn_id="t1")
    assert row["status"] == "awaiting_report"
    assert row["web"]["timing"] == {"kind": "simulated_no_wait"}
    assert not dialogue.research.pages
    return dialogue, llm, web


def come_back(dialogue):
    assert dialogue.observe_minecraft_focus(False, event_id="away")["status"] == "observed"
    return dialogue.observe_minecraft_focus(True, event_id="back")


def test_welcome_immediate_then_exact_snapshot_enters_next_reading():
    dialogue, llm, web = started(research_turn("report", "金属なんや"), reflect,
                                 research_turn("return", "冒険に戻る"))
    welcome = come_back(dialogue)
    assert welcome["status"] == "welcome_back" and welcome["reply"] == WELCOME_BACK
    assert len(llm.requests) == 1 and not dialogue.research.pages and web._pending
    filled = dialogue.refresh_after_return(welcome["refresh_token"])
    assert filled["status"] == "context_ready" and filled["reply"] == ""
    expected = json.loads(DEFAULT_VIRTUAL_WEB.read_text())["entries"][0]["page"]
    assert dialogue.research.pages[0]["text_ja"] == expected["text_ja"]
    assert dialogue.research.pages[0]["sha256"] == expected["sha256"]
    assert len(llm.requests) == 1 and not web._pending
    row = dialogue.turn("金属なんや", turn_id="t2")
    assert row["status"] == "research_reflection" and row["quote_validation"] == "matched"
    assert "timing" not in llm.requests[-1].details["research"]
    assert "pages" not in llm.requests[-2].details["research"]
    row = dialogue.turn("冒険に戻る", turn_id="t3")
    assert row["status"] == "handoff"
    assert dialogue.return_context() == {"researched_topic": QUESTION}
    assert dialogue.research is None and QUOTE not in str(dialogue.history)
    assert all(t["text"] == "よし、冒険にもどろか！" for t in dialogue.history)
    assert dialogue.refresh_after_return(welcome["refresh_token"])["status"] == "stale_return"


def test_startup_active_repeated_active_and_no_research_are_silent():
    idle = LanguageDialogue(LLM())
    assert idle.observe_minecraft_focus(True, event_id="startup")["reply"] == ""
    idle.observe_minecraft_focus(False, event_id="other_app")
    assert idle.observe_minecraft_focus(True, event_id="not_research")["reply"] == ""
    dialogue, llm, _ = started()
    for index in range(3):
        row = dialogue.observe_minecraft_focus(True, event_id=f"active{index}")
        assert row["reply"] == "" and row["status"] == "observed"
    assert len(llm.requests) == 1


def test_one_welcome_and_one_refresh_per_visit_even_after_another_app_switch():
    dialogue, _, _ = started()
    welcome = come_back(dialogue)
    assert dialogue.observe_minecraft_focus(False, event_id="away")["status"] == "duplicate"
    assert dialogue._minecraft_active is True  # 重複した古いawayで戻さない。
    assert dialogue.observe_minecraft_focus(True, event_id="back")["status"] == "duplicate"
    token = welcome["refresh_token"]
    assert dialogue.refresh_after_return(token)["status"] == "context_ready"
    assert dialogue.refresh_after_return(token)["status"] == "already_refreshed"
    dialogue.observe_minecraft_focus(False, event_id="another-away")
    assert dialogue.observe_minecraft_focus(True, event_id="another-back")["reply"] == ""


@pytest.mark.parametrize("active", [None, 1, "true"])
def test_focus_requires_explicit_boolean(active):
    with pytest.raises(ValueError):
        LanguageDialogue(LLM()).observe_minecraft_focus(active, event_id="invalid")


@pytest.mark.parametrize("blocked", ["paused", "busy"])
def test_return_during_blocked_state_is_deferred_and_release_does_not_speak(blocked):
    dialogue, llm, _ = started()
    if blocked == "paused":
        dialogue.interrupt()
    else:
        dialogue._busy = True
    assert come_back(dialogue)["status"] == "deferred"
    assert "reply" not in dialogue.release()
    dialogue._busy = False
    assert len(llm.requests) == 1 and not dialogue.research.pages
    row = dialogue.observe_minecraft_focus(True, event_id="safe-active")
    assert row["status"] == "welcome_back"
    assert dialogue.refresh_after_return(row["refresh_token"])["status"] == "context_ready"


def test_refresh_deferred_if_focus_moves_away_between_welcome_and_read():
    dialogue, _, _ = started()
    token = come_back(dialogue)["refresh_token"]
    dialogue.observe_minecraft_focus(False, event_id="away-again")
    assert dialogue.refresh_after_return(token)["status"] == "deferred"
    assert not dialogue.research.pages
    dialogue.observe_minecraft_focus(True, event_id="back-again")
    assert dialogue.refresh_after_return(token)["status"] == "context_ready"


@pytest.mark.parametrize("after_welcome", [False, True])
def test_expiry_discards_visit_and_summary(after_welcome):
    now = [0]
    dialogue, _, _ = started(clock=lambda: now[0], research_ttl_seconds=10)
    token = come_back(dialogue)["refresh_token"] if after_welcome else "unused"
    now[0] = 10
    row = (dialogue.refresh_after_return(token) if after_welcome
           else dialogue.observe_minecraft_focus(False, event_id="late"))
    assert row["status"] == "context_expired" and row["reply"] == ""
    assert dialogue.research is None and dialogue._visit is None


def test_cancellation_before_return_never_greets():
    dialogue, _, _ = started()
    dialogue.observe_minecraft_focus(False, event_id="away")
    dialogue.cancel()
    assert dialogue.observe_minecraft_focus(True, event_id="back")["status"] == "no_browser_visit"


@pytest.mark.parametrize("action", ["cancel", "interrupt"])
def test_inflight_refresh_cannot_inject_after_cancellation_or_interrupt(action):
    entered, proceed = threading.Event(), threading.Event()

    class Slow(VirtualOverviewResearch):
        def refresh(self, *args, **kwargs):
            entered.set()
            assert proceed.wait(3)
            return super().refresh(*args, **kwargs)

    dialogue, _, _ = started(web=Slow(DEFAULT_VIRTUAL_WEB))
    token = come_back(dialogue)["refresh_token"]
    result = []
    worker = threading.Thread(target=lambda: result.append(dialogue.refresh_after_return(token)))
    worker.start()
    assert entered.wait(3)
    getattr(dialogue, action)()
    proceed.set()
    worker.join(3)
    assert not worker.is_alive() and result[0]["status"] == "interrupted"
    assert dialogue.research is None or not dialogue.research.pages
    assert dialogue._busy is False


def test_old_return_token_cannot_update_new_question():
    dialogue, _, _ = started()
    token = come_back(dialogue)["refresh_token"]
    dialogue.cancel()
    new_interpretation = first_interpretation()
    new_interpretation["evidence"][0]["turn_id"] = "t-new"
    dialogue.llm = LLM(new_interpretation)
    assert turn_with_web_permission(dialogue, QUESTION, turn_id="t-new")["status"] == "awaiting_report"
    assert dialogue.refresh_after_return(token)["status"] == "stale_return"
    assert not dialogue.research.pages


def test_focus_expiry_invalidates_inflight_reading_instead_of_resurrecting_context():
    now = [0]
    dialogue, _, _ = started(clock=lambda: now[0], research_ttl_seconds=10)

    def expire_during_intent(request):
        now[0] = 11
        row = dialogue.observe_minecraft_focus(True, event_id="expired")
        assert row["status"] == "context_expired"
        return research_turn("continue", "続けたい")

    # Callableの自動report補助を通さず、この競合だけの固定LLMを使う。
    class ExpiringLLM:
        generate_structured_json = staticmethod(expire_during_intent)

    dialogue.llm = ExpiringLLM()
    now[0] = 9
    row = dialogue.turn("続けたい", turn_id="t2")
    assert row["status"] == "interrupted" and row["reply"] == ""
    assert dialogue.research is None and dialogue.mode == "normal"


def test_missing_fixture_not_substituted_for_another_target_or_facet():
    web = VirtualOverviewResearch(DEFAULT_VIRTUAL_WEB)
    for target, facet in [("黒曜石", "meaning"), ("金床", "grade")]:
        result = web.search(target, [], facet)
        assert result.status == "virtual_fixture_missing" and not result.pages and not web._pending


@pytest.mark.parametrize("failure", ["empty", "exception"])
def test_failed_refresh_does_not_call_llm_or_speak_or_retry(failure):
    class Missing(VirtualOverviewResearch):
        calls = 0

        def refresh(self, *args, **kwargs):
            self.calls += 1
            if failure == "exception":
                raise RuntimeError("test only")
            return None

    web = Missing(DEFAULT_VIRTUAL_WEB)
    dialogue, llm, _ = started(web=web)
    token = come_back(dialogue)["refresh_token"]
    row = dialogue.refresh_after_return(token)
    assert row["status"] == "unavailable" and row["reply"] == ""
    assert dialogue.refresh_after_return(token)["status"] == "already_refreshed"
    assert web.calls == 1 and len(llm.requests) == 1 and not dialogue.research.pages


def test_focus_aware_host_never_refreshes_before_welcome_delivery():
    dialogue, _, web = started(research_turn("discuss", "気になる"),
                               {"perspective": "", "quotes": []})
    dialogue.observe_minecraft_focus(True, event_id="initial")
    row = dialogue.turn("気になる", turn_id="t2")
    assert "web_refresh" not in row and web._pending and not dialogue.research.pages


def test_cli_emits_welcome_before_refresh_and_logs_simulated_context():
    rows = []
    llm = LLM(first_interpretation())
    fixture = VirtualOverviewResearch(DEFAULT_VIRTUAL_WEB)
    dialogue = LanguageDialogue(llm, Search(found=False), web=fixture)

    def emit(row):
        if row.get("status") == "welcome_back":
            assert not dialogue.research.pages and fixture._pending
        rows.append(row)

    case = {"id": "ordering", "turns": [
        {"text": QUESTION}, {"text": "ええで"}, {"control": "speech_completed"},
        {"control": "minecraft_away"},
        {"control": "minecraft_active", "expect_status": ["welcome_back"],
         "expect_refresh_status": ["context_ready"]},
    ]}
    run_cases([case], llm, emit, dialogue_factory=lambda: dialogue)
    assert [r["status"] for r in rows] == ["web_consent_requested", "web_waiting_playback",
                                          "awaiting_report", "observed", "welcome_back", "context_ready"]
    assert rows[-2]["automatic_check"] == rows[-1]["automatic_check"] == "pass"
    assert rows[-1]["web_refresh"]["pages"][0]["simulation"] is True
    assert read_cases(DEFAULT_VIRTUAL_WEB.with_name("virtual_return_cases.json"))


def test_overview_call_timing_is_separate_from_dialogue_and_context():
    class Timed(OverviewClient):
        def call(self, *args, **kwargs):
            payload = super().call(*args, **kwargs)
            payload["data"]["ai_overview"].update(waited_ms=4500, samples=2, irrelevant="no")
            return payload

    result = GoogleOverviewResearch(Timed()).search("金床", [], "meaning")
    assert result.timing["waited_ms"] == 4500 and result.timing["samples"] == 2
    assert result.timing["call_ms"] >= 0 and "irrelevant" not in result.timing
    assert "timing" not in asdict(result)["pages"][0]


@pytest.mark.parametrize("completed", [True, False])
def test_focus_return_rereads_original_search_tab_once_without_waiting_to_greet(completed):
    class Delayed(OverviewClient):
        def call(self, *args, **kwargs):
            self.status = "complete" if self.calls and completed else "timeout"
            payload = super().call(*args, **kwargs)
            payload["data"]["tab_id"] = "B" * 32
            return payload

    client = Delayed(results=False)
    llm = LLM(first_interpretation())
    dialogue = LanguageDialogue(llm, Search(found=False), web=GoogleOverviewResearch(client))
    assert turn_with_web_permission(dialogue, QUESTION, turn_id="t1")["status"] == "awaiting_report"
    welcome = come_back(dialogue)
    assert welcome["reply"] == WELCOME_BACK and len(client.calls) == 1
    row = dialogue.refresh_after_return(welcome["refresh_token"])
    assert row["status"] == ("context_ready" if completed else "unavailable")
    assert client.calls[1][1]["url"] == client.calls[0][1]["url"]
    assert client.calls[1][1]["existing_tab_id"] == "B" * 32
    assert row["reply"] == "" and len(llm.requests) == 1
    dialogue.refresh_after_return(welcome["refresh_token"])
    assert len(client.calls) == 2
