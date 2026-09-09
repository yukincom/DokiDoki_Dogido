"""Web起動の同意→案内音声の正常終了→一度だけ起動。実音声の観測は呼出元。"""

from dataclasses import asdict, dataclass
import unicodedata
from uuid import uuid4

from dogido_server.llm.types import StructuredGenerationRequest
from .browser_visit import BrowserVisit
from .contracts import Interpretation, WebConsent
from .web_research import REFERENCE_ONLY_NOTICE, ResearchContext


WEB_PERMISSION_PROMPT = "うーん。ちょっと俺にはわからへんな。この質問をウェブで調べるため、新しい検索ページを開いてもええか？"
WEB_DEPARTURE = "ほな一緒にいこか！"
WEB_DECLINED = "わかった。この質問では新しい検索はせえへんで。"
WEB_PERMISSION_AGAIN = "この質問で新しい検索をしてもええか、教えてくれる？"


@dataclass
class WebHandoff:
    interpretation: Interpretation
    trigger_reason: str
    known_urls: list[str]
    phase: str = "awaiting_consent"
    utterance_id: str = ""


def representative_consent(text):
    """現在の一案への完全一致だけ。部分一致・引用文・条件つきへ広げない。"""
    word = unicodedata.normalize("NFKC", text).strip().rstrip("。.!！")
    if word in {"うん", "はい", "ええで", "ええよ", "いいよ", "お願い", "開いて"}:
        return WebConsent(intent="accept", evidence=text, confidence=1.0)
    if word in {"いや", "いいえ", "やめとく", "やめて", "開かないで", "今はやめとく"}:
        return WebConsent(intent="decline", evidence=text, confidence=1.0)
    return None


class WebHandoffMixin:
    """LanguageDialogueのロック・epochで守る、Web起動だけの小さな状態遷移。"""

    def _handle_web_consent(self, details, record, pending, epoch):
        text = details["current"]["text"]
        outcome = representative_consent(text)
        status = "representative_phrase"
        if outcome is None:
            outcome, status = self._generate(StructuredGenerationRequest(
                kind="language_web_consent", fallback_value={}, route="chat",
                temperature=0.0, max_tokens=300,
                details={"current": details["current"], "question": pending.interpretation.question,
                         "permission_prompt": WEB_PERMISSION_PROMPT, "phase": pending.phase},
            ), WebConsent)
        normalize = lambda s: unicodedata.normalize("NFKC", s)
        if outcome and (outcome.confidence < .85 or not outcome.evidence.strip()
                        or normalize(outcome.evidence) not in normalize(text)):
            outcome, status = None, "unconfirmed_consent"
        with self._lock:
            if epoch != self._epoch or self._pending_web is not pending:
                record.update(status="web_handoff_cancelled", reply="")
                return True
            record["web_consent_status"] = status
            if outcome:
                record["web_consent"] = outcome.model_dump()
            if outcome and outcome.intent == "new_question":
                self._pending_web = None
                return False
            if outcome and outcome.intent == "decline":
                self._clear_focus()
                record.update(status="web_declined", reply=WEB_DECLINED)
            elif outcome and outcome.intent == "accept":
                if pending.phase == "awaiting_playback":
                    record.update(status="web_waiting_playback", reply="")
                else:
                    pending.phase = "awaiting_playback"
                    pending.utterance_id = "web-departure:" + uuid4().hex
                    record.update(status="web_waiting_playback", reply=WEB_DEPARTURE,
                                  speech={"utterance_id": pending.utterance_id, "text": WEB_DEPARTURE,
                                          "purpose": "web_departure", "completion_required": True})
            else:
                # ためらい・不正抽出が入ったら、既存の音声完了権限も撤回する。
                pending.phase, pending.utterance_id = "awaiting_consent", ""
                record.update(status="web_consent_requested", reply=WEB_PERMISSION_AGAIN)
            return True

    def on_speech_playback_result(self, utterance_id: str, *, status: str, event_id: str, cancelled=None):
        """対象案内の再生プロセス正常終了だけを受ける。生成完了・推定秒数は不可。

        busy時はdeferredを返し未消費。ホストが安全時に同じ通知を再配送できる。
        テキストCLIは明示模擬、独立音声試験は実再生プロセス終了。本体音声には未接続。
        """
        if status not in {"completed", "cancelled", "failed"}:
            raise ValueError("音声再生のcompleted/cancelled/failedが必要")
        if not all(isinstance(v, str) and 0 < len(v) <= 160 for v in (utterance_id, event_id)):
            raise ValueError("対象utterance_idとevent_idが必要")
        with self._lock:
            row = {"control": "speech_playback_result", "utterance_id": utterance_id,
                   "event_id": event_id, "playback_status": status, "reply": "", "references": []}
            if cancelled is not None and cancelled():
                return dict(row, status="interrupted")
            pending = self._pending_web
            if (not pending or pending.phase != "awaiting_playback"
                    or pending.utterance_id != utterance_id):
                return dict(row, status="stale_playback")
            if self.clock() - self.last_activity >= self.ttl_seconds:
                self._pending_web = None
                return dict(row, status="context_expired")
            if status != "completed":
                self._pending_web = None
                return dict(row, status="web_handoff_cancelled")
            if self.paused or self._busy:
                return dict(row, status="deferred")
            self._busy = True
            self._pending_web = None  # 読み取り開始前に消費。重複通知では起動しない。
            epoch = self._epoch
        try:
            self._search_after_speech(pending, row, epoch, host_cancelled=cancelled)
            with self._lock:
                if epoch != self._epoch or (cancelled is not None and cancelled()):
                    return dict(row, status="interrupted", reply="", references=[])
                self._remember_reply(row, event_id)
                self.last_activity = self.clock()
                return dict(row, mode_after=self.mode)
        except Exception as exc:
            return dict(row, status="web_unavailable", error=type(exc).__name__)
        finally:
            with self._lock:
                self._busy = False

    def _search_after_speech(self, pending, record, epoch, *, host_cancelled=None):
        def cancelled():
            with self._lock:
                return epoch != self._epoch or (host_cancelled is not None and host_cancelled())

        if cancelled():
            return
        i = pending.interpretation
        result = self.web.search(i.target, i.search_terms, i.facet, web_query=i.web_query,
                                 known_urls=pending.known_urls, cancelled=cancelled, emit=self.on_event)
        record["web"] = {"trigger_reason": pending.trigger_reason, **asdict(result)}
        with self._lock:
            if cancelled():
                return
            if ((result.status == "read" and result.pages)
                    or (result.status == "results_only" and result.search_results)
                    or (result.status == "page_opened" and result.search_url)):
                opened = result.child_status == "opened"
                phase = "awaiting_report" if opened else "reference_only"
                self.research = ResearchContext(i.question, i.target, result.pages, phase,
                                                result.search_results, result.search_url)
                if opened:
                    self._visit_number += 1
                    self._visit = BrowserVisit(f"{self._epoch}:{self._visit_number}",
                                               away=self._minecraft_active is False)
                # ページを読む本人の集中を切らない。起動・取得結果はstatus/webへ
                # 残すが、可視ページを開けた後には追加の音声を重ねない。
                record.update(status=phase, reply="" if opened else REFERENCE_ONLY_NOTICE,
                              references=result.pages, research_phase=phase)
                if result.search_url and not opened:
                    record["reply"] = "検索の内容は受け取れたで。気になってるところ、一緒に見てみよか。"
                record["web"]["context_page_ids"] = [p["id"] for p in self.research.pages]
            else:
                record.update(status="web_unavailable", references=[],
                              reply="今はページをうまく読めへんかった。教科書や資料集でも確かめてみよか。")
