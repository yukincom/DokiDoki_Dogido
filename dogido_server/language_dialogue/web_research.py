"""検索結果と読めた本文を分ける、公開資料の小さな読み取り経路。"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from urllib.parse import urlsplit

from .child_resources import ChildResources


SEARCH_NOTICE = "ちょっと調べてみよか！"
REFERENCE_ONLY_NOTICE = "オレが確かめる資料は読めたけど、一人で読みやすいページはまだ見つけられてへんねん。教科書や先生にも聞いてみよか。"
RETURN_INVITATION = "勉強も楽しいけど、そろそろ冒険にもどろか！"
TEACHER_SUGGESTION = "うーん、そこは学校で先生に聞いてみるといいかもしれんなー。"
SOURCE_FILTER = "(site:go.jp OR site:ninjal.ac.jp OR site:nao.ac.jp)"
FACET_LABELS = {
    "grade": "漢字 配当学年",
    "reading": "読み方",
    "meaning": "意味",
    "spelling": "表記",
    "grammar": "文法",
    "usage": "用法",
    "etymology": "語源",
    "translation": "翻訳",
    "comparison": "違い",
    "classification": "分類",
    "other": "",
}


def approved_url(url):
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower().rstrip(".")
        return (
            p.scheme in {"https", "http"}
            and not p.username
            and not p.password
            and p.port in {None, 80, 443}
            and (
                host.endswith(".go.jp")
                or any(
                    host == domain or host.endswith("." + domain)
                    for domain in ("ninjal.ac.jp", "nao.ac.jp")
                )
            )
        )
    except (ValueError, TypeError):
        return False


@dataclass
class WebResult:
    query: str = ""
    status: str = "not_requested"
    pages: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    search_status: str = "not_requested"
    child_status: str = "not_requested"
    search_results: list[dict] = field(default_factory=list)
    search_url: str = ""
    timing: dict = field(default_factory=dict)  # 診断専用。ResearchContextへ渡さない。


@dataclass
class ResearchContext:
    question: str
    target: str
    pages: list[dict]
    phase: str = "awaiting_report"
    search_results: list[dict] = field(default_factory=list)
    search_url: str = ""

    def snapshot(self):
        return asdict(self)


def page_snapshot(data, url, *, use):
    body = data.get("markdown", data.get("text", ""))
    if not isinstance(body, str) or len(body.strip()) < 80:
        raise ValueError("empty_page_body")
    final_url = data.get("final_url", data.get("url", ""))
    digest = hashlib.sha256(body[:15000].encode()).hexdigest()
    page_id = "web:" + hashlib.sha256((final_url + digest + use).encode()).hexdigest()[:16]
    return {
        "id": page_id,
        "requested_url": url,
        "url": final_url,
        "title_ja": str(data.get("title", ""))[:300],
        "text_ja": body[:15000],
        "sha256": digest,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "truncated": bool(data.get("truncated")) or len(body) > 15000,
        "claim_status": "retrieved_page_not_verified_answer",
        "use": use,
        "coverage": "extracted_text_only",
        "sources": [{"title_ja": str(data.get("title", ""))[:300], "url": final_url}],
    }


class WebResearch:
    def __init__(
        self,
        client,
        *,
        known_sources_only=False,
        child_client=None,
        child_resources=None,
        school_grade=3,
    ):
        self.client = client
        self.known_sources_only = known_sources_only
        self._captcha = False
        self.child_client = child_client
        self.child_resources = child_resources or ChildResources()
        self.school_grade = school_grade

    def search(
        self,
        target,
        terms,
        facet,
        *,
        known_urls=(),
        web_query="",
        cancelled=lambda: False,
        emit=lambda event: None,
    ):
        result = self._search_reference(
            target, terms, facet, known_urls=known_urls, cancelled=cancelled, emit=emit
        )
        if cancelled():
            result.status = "interrupted"
            result.pages.clear()
            return result
        resource = self.child_resources.select(
            target, terms, school_grade=self.school_grade, facet=facet
        )
        result.child_status = "no_matching_resource" if not resource else "not_configured"
        if not resource or not self.child_client:
            return result

        def event(kind, **values):
            row = {"event": kind, **values}
            result.events.append(row)
            emit(row)

        try:
            url = resource["url"]
            event("child_page_open_started", resource_id=resource["id"], url=url)
            payload = self.child_client.call(
                "fetch_url",
                {"url": url, "expected_url": url, "format": "markdown", "char_limit": 15000},
                cancelled=cancelled,
            )
            if cancelled():
                result.status = result.child_status = "interrupted"
                result.pages.clear()
                return result
            data = payload.get("data", {})
            final_url = data.get("final_url", data.get("url", ""))
            if (
                not payload.get("success")
                or data.get("expected_url_verified") is not True
                or not self.child_resources.same_page(final_url, resource)
            ):
                result.child_status = "unavailable"
                event("child_page_failed", reason="not_the_reviewed_page")
                return result
            page = page_snapshot(data, url, use="child_resource")
            page["resource_id"] = resource["id"]
            result.pages.append(page)
            result.child_status, result.status = "opened", "read"
            event(
                "child_page_read",
                page_id=page["id"],
                url=page["url"],
                chars=len(page["text_ja"]),
                sha256=page["sha256"],
                coverage=page["coverage"],
            )
        except Exception as exc:
            result.child_status = "unavailable"
            if cancelled():
                result.status = result.child_status = "interrupted"
                result.pages.clear()
            event("child_page_failed", reason=type(exc).__name__)
        return result

    def _search_reference(
        self,
        target,
        terms,
        facet,
        *,
        known_urls=(),
        cancelled=lambda: False,
        emit=lambda event: None,
    ):
        # 生の発話・履歴・プレイヤー名・位置・記憶は検索エンジンへ渡さない。
        pieces = list(dict.fromkeys([target] + list(terms)))[:4]
        if any(not isinstance(t, str) or len(t) > 80 for t in pieces):
            return WebResult(status="invalid_query")
        query = " ".join(t.strip() for t in pieces if t.strip())[:160]
        if not query or re.search(r"https?://|@|\d{2,4}[-－]\d{2,4}[-－]\d{3,4}", query):
            return WebResult(status="invalid_query")
        query = f"{query} {FACET_LABELS.get(facet, '')} {SOURCE_FILTER}".strip()
        result = WebResult(query=query)

        def event(kind, **values):
            row = {"event": kind, **values}
            result.events.append(row)
            emit(row)

        try:
            if cancelled():
                result.status = "interrupted"
                return result
            if self.known_sources_only or self._captcha:
                result.search_status = (
                    "known_sources_only" if self.known_sources_only else "captcha"
                )
                event("web_search_skipped", reason=result.search_status, text=SEARCH_NOTICE)
                response = {"success": False}
            else:
                event("web_search_started", query=query, text=SEARCH_NOTICE)
                response = self.client.call(
                    "google_search", {"query": query, "limit": 5}, cancelled=cancelled
                )
                result.search_status = "searched" if response.get("success") else "search_failed"
            if cancelled():
                result.status = "interrupted"
                return result
            if not response.get("success"):
                if response.get("captcha_required"):
                    self._captcha = True
                    result.search_status = "captcha"
                if result.search_status not in {"known_sources_only", "captcha"} or response.get(
                    "captcha_required"
                ):
                    event("web_search_failed", reason=result.search_status)
            candidates = list(response.get("data", {}).get("web", []))
            if response.get("success"):
                event("web_search_finished", result_count=len(candidates))
            # 検索失敗を隠さず、もともと出典が分かる資料は直接読める。
            # Gemini回答の代替取得やCAPTCHAの回避ではない。
            for url in dict.fromkeys(known_urls):
                if approved_url(url):
                    candidates.append({"url": url})
            if not candidates:
                result.status = (
                    result.search_status if not response.get("success") else "no_readable_source"
                )
                return result
            attempted = set()
            for candidate in candidates:
                if cancelled():
                    result.status = "interrupted"
                    result.pages.clear()
                    return result
                url = candidate.get("url", "")
                if not approved_url(url) or url in attempted:
                    continue
                if urlsplit(url).path.lower().endswith(".pdf"):
                    event("web_page_skipped", url=url, reason="pdf_not_supported")
                    continue
                attempted.add(url)
                event("web_fetch_started", url=url)
                try:
                    payload = self.client.call(
                        "fetch_url",
                        {"url": url, "format": "markdown", "char_limit": 15000},
                        cancelled=cancelled,
                    )
                except Exception as exc:
                    event("web_fetch_failed", url=url, reason=type(exc).__name__)
                    payload = {"success": False}
                if cancelled():
                    result.status = "interrupted"
                    result.pages.clear()
                    return result
                if not payload.get("success"):
                    event(
                        "web_fetch_failed",
                        url=url,
                        reason="captcha" if payload.get("captcha_required") else "fetch_failed",
                    )
                    if payload.get("captcha_required"):
                        result.status = "captcha"
                        return result
                else:
                    data = payload.get("data", {})
                    final_url = data.get("final_url", data.get("url", ""))
                    body = data.get("markdown", data.get("text", ""))
                    if not approved_url(final_url):
                        event("web_fetch_failed", url=url, reason="source_not_approved")
                    elif not isinstance(body, str) or len(body.strip()) < 80:
                        event("web_fetch_failed", url=url, reason="empty_page_body")
                    else:
                        page = page_snapshot(data, url, use="background_reference")
                        result.pages.append(page)
                        event(
                            "web_page_read",
                            page_id=page["id"],
                            url=final_url,
                            chars=len(page["text_ja"]),
                            sha256=page["sha256"],
                        )
                if len(attempted) >= 2:
                    break
            result.status = "read" if result.pages else "no_readable_source"
        except Exception as exc:
            result.status = "interrupted" if cancelled() else "unavailable"
            if cancelled():
                result.pages.clear()
            event("web_error", reason=type(exc).__name__)
        return result


def checked_quotes(quotes, pages):
    by_id = {p["id"]: p for p in pages}
    compact = lambda text: re.sub(r"\s+", "", text)
    return bool(quotes) and all(
        q.page_id in by_id
        and len(compact(q.quote)) >= 8
        and compact(q.quote) in compact(by_id[q.page_id]["text_ja"])
        for q in quotes
    )
