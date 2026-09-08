"""同じ可視検索タブのAI概要を受け取る。本文の先読み・再検索はしない。"""

import hashlib
import ipaddress
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

from .web_research import FACET_LABELS, SEARCH_NOTICE, WebResult


HANDOFF_TEMPLATE = (
    "「{question}」ってキッズに聞かれてんねん。俺にはよーわからん。"
    "geminiはん、ちょっと代わりに関西弁で教えてやってくれへんか？"
)


def public_link(url):
    """検索結果メタデータのURL。リンク先を取得する許可ではない。"""
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower().rstrip(".")
        if p.scheme != "https" or p.username or p.password or p.port not in {None, 443}:
            return False
        if not host or "." not in host or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except (ValueError, TypeError):
        return False


def same_search(url, query):
    try:
        p = urlsplit(url)
        params = parse_qs(p.query)
        return (p.scheme == "https" and p.hostname == "www.google.com"
                and not p.username and not p.password and p.port in {None, 443}
                and p.path == "/search" and params.get("q") == [query]
                and not params.get("udm") and not params.get("tbm"))
    except (ValueError, TypeError):
        return False


class GoogleOverviewResearch:
    """表示用の専用Chromeを使用。AI生成文を公式資料や検証済み事実へ昇格しない。"""

    def __init__(self, client):
        self.client = client
        self._captcha = False
        self._pending_tabs = {}

    def refresh(self, context, *, cancelled=lambda: False, emit=lambda event: None):
        """Minecraft復帰後（非対応ホストは次の発話時）に一度だけ同じタブを再読。"""
        tab = self._pending_tabs.pop(context.search_url, None)
        if context.pages or not tab:
            return None
        query, tab_id = tab
        return self._read(query, context.search_url, cancelled=cancelled, emit=emit,
                          existing_tab_id=tab_id)

    def search(self, target, terms, facet, *, known_urls=(), web_query="",
               cancelled=lambda: False, emit=lambda event: None):
        # 発話全文や履歴ではなく、抽出済みの対象・観点だけを公開検索へ送る。
        pieces = [target, *terms][:5]
        if any(not isinstance(t, str) or len(t) > 80 for t in pieces):
            return WebResult(status="invalid_query")
        if not isinstance(web_query, str) or len(web_query) > 140:
            return WebResult(status="invalid_query")
        # 旧呼出元の索引語も空白単位で重複を外す。意味を推測して例外表を作らない。
        query = web_query.strip() or " ".join(dict.fromkeys(
            " ".join([*pieces, FACET_LABELS.get(facet, "")]).split()
        ))[:160]
        if not query or re.search(r"https?://|@|\d{2,4}[-－]\d{2,4}[-－]\d{3,4}", query):
            return WebResult(status="invalid_query")
        query = HANDOFF_TEMPLATE.format(question=query)
        url = "https://www.google.com/search?" + urlencode({"q": query, "hl": "ja"})
        return self._read(query, url, cancelled=cancelled, emit=emit)

    def _read(self, query, url, *, cancelled, emit, existing_tab_id=None):
        result = WebResult(query=query, search_url=url)

        def event(kind, **values):
            row = {"event": kind, **values}
            result.events.append(row)
            emit(row)

        if cancelled():
            result.status = "interrupted"
            return result
        if self._captcha:
            result.status = result.search_status = "captcha"
            event("web_search_skipped", reason="captcha")
            return result
        try:
            if existing_tab_id:
                event("web_overview_reread_started", url=url)
            else:
                event("web_search_started", query=query, text=SEARCH_NOTICE)
            arguments = {
                "url": url, "wait_for_ai_overview": True, "char_limit": 15000,
            }
            if existing_tab_id:
                arguments["existing_tab_id"] = existing_tab_id
            started = time.monotonic()
            try:
                payload = self.client.call("fetch_url", arguments, cancelled=cancelled)
            finally:
                result.timing["call_ms"] = round((time.monotonic() - started) * 1000)
            if cancelled():
                result.status = "interrupted"
                return result
            if payload.get("captcha_required"):
                self._captcha = True
                result.status = result.search_status = "captcha"
                event("web_search_failed", reason="captcha")
                return result
            data = payload.get("data", {})
            overview = data.get("ai_overview", {})
            for key in ("waited_ms", "samples"):
                value = overview.get(key)
                if type(value) in {int, float} and 0 <= value < 1_000_000:
                    result.timing[key] = value
            result.search_status = str(overview.get("status", "missing"))
            final_url = data.get("final_url", data.get("url", ""))
            tab_id = data.get("tab_id", "")
            valid_tab = isinstance(tab_id, str) and bool(re.fullmatch(r"[A-Fa-f0-9]{32}", tab_id))
            open_pending = (valid_tab and overview.get("page_visibility") == "visible"
                            and result.search_status in {"timeout", "not_found", "unavailable"})
            if (not payload.get("success") and not open_pending) or not same_search(final_url, query):
                result.status = "unavailable"
                event("web_search_failed", reason="unread_or_changed_search")
                return result
            result.child_status = "opened" if overview.get("page_visibility") == "visible" else "not_visible"
            for item in data.get("web", [])[:5]:
                if not isinstance(item, dict) or not public_link(item.get("url", "")):
                    continue
                result.search_results.append({
                    "title": str(item.get("title", ""))[:300],
                    "url": item["url"],
                    "description": str(item.get("description", ""))[:1000],
                    "claim_status": "search_snippet_not_page_body",
                })
            body = overview.get("text", "")
            if result.search_status == "complete" and isinstance(body, str) and body.strip():
                body = body[:15000]
                digest = hashlib.sha256(body.encode()).hexdigest()
                page = {
                    "id": "overview:" + hashlib.sha256((url + digest).encode()).hexdigest()[:16],
                    "url": final_url, "title_ja": "GoogleのAIによる概要", "text_ja": body,
                    "sha256": digest, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "truncated": bool(overview.get("truncated")) or len(overview["text"]) > 15000,
                    "use": "google_ai_overview", "coverage": "ai_generated_summary",
                    "claim_status": "retrieved_ai_summary_not_verified_fact",
                    "sources": [{"title_ja": "GoogleのAIによる概要", "url": final_url}],
                }
                result.pages.append(page)
                event("web_overview_read", page_id=page["id"], chars=len(body), sha256=digest)
            result.status = ("read" if result.pages else "results_only" if result.search_results
                             else "page_opened" if open_pending else "unavailable")
            if not existing_tab_id and result.status in {"results_only", "page_opened"} and valid_tab:
                self._pending_tabs[url] = (query, tab_id)
                while len(self._pending_tabs) > 8:
                    self._pending_tabs.pop(next(iter(self._pending_tabs)))
            event("web_search_finished", status=result.status, result_count=len(result.search_results),
                  child_status=result.child_status)
        except Exception as exc:
            result.status = "interrupted" if cancelled() else "unavailable"
            result.pages.clear()
            result.search_results.clear()
            event("web_search_failed", reason=type(exc).__name__)
        return result
