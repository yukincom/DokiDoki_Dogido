"""AI概要と検索紹介文の受取・対話配線。意味の品質は実モデルで別途読む。"""

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from dogido_server.language_dialogue.controller import LanguageDialogue
from dogido_server.language_dialogue.google_overview import GoogleOverviewResearch, HANDOFF_TEMPLATE, same_search
from dogido_server.language_dialogue.prompts import build_research_reading_messages
from test_language_web_research import LLM, Search, interpretation, local_reply, research_turn, turn_with_web_permission


SUMMARY = "試験用のAI概要。金属をたたいて形を整えるための台を金床と呼び、かなとこと読みます。"
QUOTE = "金属をたたいて形を整えるための台を金床と呼び"
SNIPPET = "これは紹介文だけで、実ページ本文ではありません。"


class OverviewClient:
    def __init__(self, status="complete", *, visible=True, results=True):
        self.status, self.visible, self.results = status, visible, results
        self.calls = []

    def call(self, name, arguments, **kwargs):
        self.calls.append((name, deepcopy(arguments)))
        return {
            "success": self.status != "challenge",
            "captcha_required": self.status == "challenge",
            "data": {
                "final_url": arguments["url"],
                "ai_overview": {
                    "status": self.status,
                    "text": SUMMARY if self.status == "complete" else "生成できませんでした（エラー本文）",
                    "page_visibility": "visible" if self.visible else "hidden",
                    "links": [{"url": "https://example.org/not-read", "title": "リンク先"}],
                },
                "web": [{"url": "https://example.org/dictionary", "title": "用語辞典", "description": SNIPPET}]
                       if self.results else [],
            },
        }


def reading(request):
    page = request.details["research"]["pages"][0]
    return {"perspective": "金属をたたくときに使う台のことやね。",
            "quotes": [{"page_id": page["id"], "quote": QUOTE}]}


def test_normal_search_only_once_without_prefetch_or_full_utterance():
    client = OverviewClient()
    result = GoogleOverviewResearch(client).search("金床", ["金床", "語源"], "etymology",
                                                  known_urls=["https://www.mext.go.jp/unused"])
    assert result.status == "read" and result.child_status == "opened"
    assert len(client.calls) == 1
    name, args = client.calls[0]
    assert name == "fetch_url" and args["wait_for_ai_overview"] is True
    params = parse_qs(urlsplit(args["url"]).query)
    assert not ({"udm", "tbm"} & params.keys())
    assert "site:" not in params["q"][0]
    assert "語源" in params["q"][0]
    assert result.pages[0]["text_ja"] == SUMMARY
    assert result.pages[0]["claim_status"] == "retrieved_ai_summary_not_verified_fact"
    assert result.search_results[0]["claim_status"] == "search_snippet_not_page_body"


def test_web_query_not_local_index_phrases_and_truncation_preserved():
    class Cut(OverviewClient):
        def call(self, *args, **kwargs):
            payload = super().call(*args, **kwargs)
            payload["data"]["ai_overview"]["truncated"] = True
            return payload

    client = Cut()
    result = GoogleOverviewResearch(client).search(
        "TNT", ["TNT 原稿用紙 書き方", "作文 原稿用紙 大文字"], "spelling",
        web_query="原稿用紙にアルファベットの大文字を書く方法",
    )
    assert result.query == HANDOFF_TEMPLATE.format(question="原稿用紙にアルファベットの大文字を書く方法")
    assert result.pages[0]["truncated"] is True


@pytest.mark.parametrize("query", ["https://example.org/private", "name@example.org", "090-1234-5678", "x" * 141])
def test_invalid_web_query_never_sent(query):
    client = OverviewClient()
    assert GoogleOverviewResearch(client).search("語", [], "meaning", web_query=query).status == "invalid_query"
    assert not client.calls


@pytest.mark.parametrize("status", ["not_found", "timeout", "unavailable", "missing"])
def test_missing_overview_keeps_results_not_error_text(status):
    client = OverviewClient(status)
    result = GoogleOverviewResearch(client).search("金床", [], "meaning")
    assert result.status == "results_only" and not result.pages
    assert len(client.calls) == 1 and len(result.search_results) == 1
    assert "エラー本文" not in str(result)


def test_captcha_not_retried_or_replaced_by_other_search():
    client = OverviewClient("challenge")
    web = GoogleOverviewResearch(client)
    assert web.search("金床", [], "meaning").status == "captcha"
    assert web.search("金床", [], "meaning").status == "captcha"
    assert len(client.calls) == 1


def test_cancelled_read_has_no_context():
    client = OverviewClient()
    result = GoogleOverviewResearch(client).search("金床", [], "meaning", cancelled=lambda: bool(client.calls))
    assert result.status == "interrupted" and not result.pages and not result.search_results


@pytest.mark.parametrize("url", [
    "https://www.google.com/search?q=other",
    "https://www.google.com.evil.example/search?q=test",
    "https://www.google.com/search?q=test&udm=14",
    "https://www.google.com/search?q=test&tbm=isch",
    "https://user@www.google.com/search?q=test",
    "http://www.google.com/search?q=test",
])
def test_changed_search_rejected(url):
    assert not same_search(url, "test")


def test_redirected_payload_does_not_become_summary():
    class Redirect(OverviewClient):
        def call(self, *args, **kwargs):
            payload = super().call(*args, **kwargs)
            payload["data"]["final_url"] = "https://example.org/"
            return payload

    result = GoogleOverviewResearch(Redirect()).search("金床", [], "meaning")
    assert result.status == "unavailable" and not result.pages


@pytest.mark.parametrize("intent", ["report", "discuss", "uncertain"])
def test_exact_summary_reaches_followup_without_forced_quiz_or_return(intent):
    first = interpretation("金床を調べて")
    first.update(target="金床", search_terms=["金床"], lookup_requested=True)
    llm = LLM(first, research_turn(intent, "鉄をたたく台ってこと？"), reading,
              research_turn("return", "冒険に戻ろう"))
    client = OverviewClient()
    dialogue = LanguageDialogue(llm, Search(), web=GoogleOverviewResearch(client))
    row = turn_with_web_permission(dialogue, "金床を調べて", turn_id="t1")
    assert row["status"] == "awaiting_report" and len(llm.requests) == 1
    assert row["reply"] == ""  # 読み始めた本人へ追加音声を重ねない。
    row = dialogue.turn("鉄をたたく台ってこと？", turn_id="t2")
    assert row["status"] == "research_reflection" and row["quote_validation"] == "matched"
    assert "どう思う" not in row["reply"] and "先生" not in row["reply"]
    assert "pages" not in llm.requests[-2].details["research"]
    passed = llm.requests[-1].details["research"]
    assert passed["pages"][0]["text_ja"] == SUMMARY
    assert passed["search_results"][0]["description"] == SNIPPET
    messages = build_research_reading_messages(llm.requests[-1])
    assert "生成できません" not in str(messages)
    assert "公式資料・検証済み事実ではなく" in messages[0]["content"]
    row = dialogue.turn("冒険に戻ろう", turn_id="t3")
    assert row["status"] == "handoff" and dialogue.research is None
    assert SUMMARY not in str(dialogue.history) and SNIPPET not in str(dialogue.history)
    assert len(client.calls) == 1


def test_research_topic_switch_requires_confirmation_and_never_researches_twice():
    llm = LLM(
        interpretation(),
        research_turn("new_question", "大のことを、とこっていうの？"),
    )
    client = OverviewClient()
    dialogue = LanguageDialogue(llm, Search(found=False), web=GoogleOverviewResearch(client))
    turn_with_web_permission(dialogue, "擬声語って何？", turn_id="t1")
    row = dialogue.turn("大のことを、とこっていうの？", turn_id="t2")
    assert row["status"] == "research_topic_confirmation"
    assert row["research_interpretation"]["intent"] == "new_question"
    assert dialogue.research.phase == "confirming_topic_change"
    assert len(client.calls) == 1
    row = dialogue.turn("今の続き", turn_id="t3")
    assert row["status"] == "research_continue" and dialogue.research is not None
    assert len(client.calls) == 1


def test_research_web_close_is_truthful_and_does_not_call_intent_or_web():
    llm = LLM(interpretation())
    client = OverviewClient()
    dialogue = LanguageDialogue(llm, Search(found=False), web=GoogleOverviewResearch(client))
    turn_with_web_permission(dialogue, "擬声語って何？", turn_id="t1")
    used = len(llm.requests)
    row = dialogue.turn("ウェブはもう閉じていいよ、分かったよ", turn_id="t2")
    assert row["status"] == "handoff" and "開いてへん" not in row["reply"]
    assert "閉じた" not in row["reply"] and dialogue.research is None
    assert len(llm.requests) == used and len(client.calls) == 1


def test_results_only_not_promoted_to_page_evidence():
    def fabricated(request):
        assert request.details["research"]["pages"] == []
        return {"perspective": "本文に載ってたで。", "quotes": [{"page_id": "invented", "quote": SNIPPET}]}

    llm = LLM(interpretation(), research_turn("discuss", "どういうこと？"), fabricated)
    dialogue = LanguageDialogue(llm, Search(found=False), web=GoogleOverviewResearch(OverviewClient("not_found")))
    assert turn_with_web_permission(dialogue, "擬声語って何？", turn_id="t1")["status"] == "awaiting_report"
    row = dialogue.turn("どういうこと？", turn_id="t2")
    assert row["status"] == "research_uncertain"
    assert "本文はまだ読めてへん" in row["reply"] and not row["references"]


def test_hidden_search_does_not_claim_visible_page():
    llm = LLM(interpretation())
    dialogue = LanguageDialogue(llm, Search(found=False), web=GoogleOverviewResearch(OverviewClient(visible=False)))
    row = turn_with_web_permission(dialogue, "擬声語って何？", turn_id="t1")
    assert row["status"] == "reference_only" and "開いた" not in row["reply"]


def test_local_answer_stays_local_but_needed_explanation_hands_off():
    for reply, expected in [(local_reply(), False),
                            (local_reply("partial", missing_kind="evidence", missing="読みの由来"), True)]:
        client = OverviewClient()
        dialogue = LanguageDialogue(LLM(interpretation(), reply), Search(), web=GoogleOverviewResearch(client))
        row = turn_with_web_permission(dialogue, "擬声語って何？", turn_id="t1")
        assert bool(client.calls) is expected
        assert ("web" in row) is expected


@pytest.mark.parametrize("results", [True, False])
def test_incomplete_initial_overview_is_never_fetched_again_on_followup(results):
    class Delayed(OverviewClient):
        def call(self, *args, **kwargs):
            self.status = "timeout"
            payload = super().call(*args, **kwargs)
            payload["data"]["tab_id"] = "A" * 32
            payload["success"] = bool(self.results)
            return payload

    def respond(request):
        assert not request.details["research"]["pages"]
        return {"perspective": "", "quotes": []}

    llm = LLM(interpretation(), research_turn("discuss", "どういう台なの？"), respond,
              research_turn("discuss", "まだ気になる"), respond)
    client = Delayed(results=results)
    dialogue = LanguageDialogue(llm, Search(found=False), web=GoogleOverviewResearch(client))
    assert turn_with_web_permission(dialogue, "擬声語って何？", turn_id="t1")["status"] == "awaiting_report"
    row = dialogue.turn("どういう台なの？", turn_id="t2")
    assert "web_refresh" not in row and row["status"] == "research_uncertain"
    if not results:
        assert "紹介文までは受け取れた" not in row["reply"]
    assert "existing_tab_id" not in client.calls[0][1]
    dialogue.turn("まだ気になる", turn_id="t3")
    assert len(client.calls) == 1
    assert "エラー本文" not in str(llm.requests)
