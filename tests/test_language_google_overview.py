"""専用Chrome補助からのAI概要・検索紹介文の受取。会話状態と採否はRustが所有する。"""

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from dogido_server.language_dialogue.google_overview import GoogleOverviewResearch, HANDOFF_TEMPLATE, same_search


SUMMARY = "試験用のAI概要。金属をたたいて形を整えるための台を金床と呼び、かなとこと読みます。"
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


def test_hidden_search_is_reported_as_not_visible():
    client = OverviewClient(visible=False)
    result = GoogleOverviewResearch(client).search("金床", [], "meaning")
    assert result.child_status == "not_visible"
    assert result.pages[0]["claim_status"] == "retrieved_ai_summary_not_verified_fact"
    assert len(client.calls) == 1


@pytest.mark.parametrize("results", [True, False])
def test_incomplete_overview_keeps_visible_tab_without_second_fetch(results):
    class Delayed(OverviewClient):
        def call(self, *args, **kwargs):
            self.status = "timeout"
            payload = super().call(*args, **kwargs)
            payload["data"]["tab_id"] = "A" * 32
            payload["success"] = bool(self.results)
            return payload

    client = Delayed(results=results)
    result = GoogleOverviewResearch(client).search("金床", [], "meaning")
    assert result.status == ("results_only" if results else "page_opened")
    assert result.child_status == "opened" and not result.pages
    assert bool(result.search_results) is results
    assert "エラー本文" not in str(result)
    assert len(client.calls) == 1
    assert "existing_tab_id" not in client.calls[0][1]
