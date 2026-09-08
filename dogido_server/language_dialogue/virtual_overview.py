"""外部通信なしの概要リプレイ。実ページの取得や生成時間の測定ではない。"""

from copy import deepcopy
import hashlib
import json

from .web_research import WebResult


class VirtualOverviewResearch:
    """明示した対象・観点だけを再生。初回は空、復帰後の再読で記録済み本文を返す。"""

    def __init__(self, fixture_path):
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("仮想Web fixtureの版が不正")
        self.entries = data["entries"]
        self._pending = {}
        self._sequence = 0
        for item in self.entries:
            page = item["page"]
            body = page["text_ja"]
            if (not isinstance(body, str) or not body.strip() or len(body) > 15000
                    or hashlib.sha256(body.encode()).hexdigest() != page["sha256"]):
                raise ValueError("仮想Web本文のハッシュまたは長さが不正")

    def search(self, target, terms, facet, *, known_urls=(), web_query="",
               cancelled=lambda: False, emit=lambda event: None):
        if cancelled():
            return WebResult(status="interrupted")
        matches = [e for e in self.entries if e["target"] == target and facet in e["facets"]]
        if len(matches) != 1:
            return WebResult(status="virtual_fixture_missing", search_status="not_requested",
                             timing={"kind": "simulated_no_wait"})
        self._sequence += 1
        url = f"virtual-web://visit/{self._sequence}"
        self._pending[url] = deepcopy(matches[0]["page"])
        while len(self._pending) > 8:
            self._pending.pop(next(iter(self._pending)))
        event = {"event": "virtual_web_opened", "target": target, "simulation": True}
        emit(event)
        return WebResult(query=web_query or target, status="page_opened", events=[event],
                         search_status="simulated_pending", child_status="opened", search_url=url,
                         timing={"kind": "simulated_no_wait"})

    def refresh(self, context, *, cancelled=lambda: False, emit=lambda event: None):
        page = self._pending.pop(context.search_url, None)
        if cancelled():
            return WebResult(status="interrupted")
        if context.pages or page is None:
            return None
        page["id"] = "virtual:" + page["sha256"][:16]
        page["simulation"] = True
        page["claim_status"] = "simulated_replay_of_ai_summary_not_verified_fact"
        if not page.get("sources"):
            page["sources"] = [{"title_ja": page["title_ja"], "url": page["url"]}]
        event = {"event": "virtual_overview_replayed", "page_id": page["id"], "simulation": True}
        emit(event)
        return WebResult(query=context.question, status="read", pages=[page], events=[event],
                         search_status="simulated_complete", child_status="opened",
                         search_url=context.search_url, timing={"kind": "simulated_no_wait"})
