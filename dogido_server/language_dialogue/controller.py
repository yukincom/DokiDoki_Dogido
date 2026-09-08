"""入力→解釈→確認または検索→返答。ゲーム・TTS・永続記憶を操作しない。"""

from collections import deque
from dataclasses import asdict, dataclass, field
import threading
import time
import unicodedata
import re

from pydantic import ValidationError

from dogido_server.llm.types import LLMFrontend, StructuredGenerationRequest
from .contracts import GroundedReply, Interpretation, ResearchIntent, ResearchReading
from .browser_visit import WELCOME_BACK
from .web_handoff import WebHandoff, WebHandoffMixin, WEB_PERMISSION_PROMPT
from .retrieval import LocalDialogueSearch, SearchResult
from .verified_answers import count_explicit_kana, verified_reply
from .web_research import (
    RETURN_INVITATION, TEACHER_SUGGESTION,
    ResearchContext, checked_quotes,
)


KANJI_GRADE_CONFIRMATION = "それって、漢字を習う学年のこと？"


def mentions_writing(text):
    return any(word in text for word in ("漢字", "文字", "かんじ")) or bool(
        re.search(r"(?<!数)字", text)
    )


@dataclass
class Focus:
    question: str = ""
    target: str = ""
    clarification: str = ""
    alternatives: list[str] = field(default_factory=list)


class LanguageDialogue(WebHandoffMixin):
    """一つの会話用。モデル呼出し中も interrupt() で古い返答を無効化できる。"""

    def __init__(self, llm: LLMFrontend, search=None, *, clock=time.monotonic, ttl_seconds=300,
                 web=None, on_event=None, research_ttl_seconds=1800):
        self.llm = llm
        self.search = search or LocalDialogueSearch()
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        self.research_ttl_seconds = research_ttl_seconds
        self.mode = "normal"
        self.focus = Focus()
        self.history = deque(maxlen=10)  # 5往復。学習記録には保存しない。
        self.last_activity = clock()
        self.paused = False
        self._epoch = 0
        self._lock = threading.RLock()
        self._busy = False
        self._seen = deque(maxlen=64)
        self._kanji_scope_confirmed = False
        self.web = web
        self.on_event = on_event or (lambda event: None)
        self.research = None
        self.last_research_topic = ""
        self._minecraft_active = None  # 発話内容から前面アプリを推定しない。
        self._focus_events = deque(maxlen=64)
        self._visit_number = 0
        self._visit = None
        self._pending_web = None

    def return_context(self):
        """本体側へ戻す情報は調べた話題1件だけ。本文・答え・理解度は含めない。"""
        return {"researched_topic": self.last_research_topic} if self.last_research_topic else {}

    def _clear_focus(self, *, remember_research=False):
        if remember_research and self.research:
            self.last_research_topic = self.research.question[:160]
        self.mode = "normal"
        self.focus = Focus()
        self.history.clear()
        self._kanji_scope_confirmed = False
        self.research = None
        self._visit = None
        self._pending_web = None

    def _expire_research(self):
        if self.research and self.clock() - self.last_activity >= self.research_ttl_seconds:
            self._epoch += 1  # 実行中の旧返答から失効文脈を復活させない。
            self._clear_focus(remember_research=True)
            return True
        return False

    def observe_minecraft_focus(self, active: bool, *, event_id: str):
        """実測または明示した模擬イベント。歓迎を即返し、Web/LLMは呼ばない。

        呼出元は歓迎を配送した後に refresh_after_return(token) を呼ぶ。
        中断・処理中なら次の安全なactive観測まで保留し、releaseでは自動発話しない。
        """
        if type(active) is not bool or not isinstance(event_id, str) or not event_id or len(event_id) > 160:
            raise ValueError("boolのactiveと1〜160字のevent_idが必要")
        with self._lock:
            row = {"control": "minecraft_focus", "active": active, "event_id": event_id,
                   "reply": "", "status": "observed"}
            if event_id in self._focus_events:
                return dict(row, status="duplicate")
            self._focus_events.append(event_id)
            self._minecraft_active = active
            if self._expire_research():
                return dict(row, status="context_expired")
            visit = self._visit
            if not visit or not self.research:
                return dict(row, status="no_browser_visit")
            visit.observe(active)
            if not active or not visit.returned or visit.greeted:
                return row
            if self.paused or self._busy:
                return dict(row, status="deferred")
            visit.greeted = True
            self.last_activity = self.clock()
            row.update(status="welcome_back", reply=WELCOME_BACK, refresh_token=visit.token)
            self._remember_reply(row, f"focus:{event_id}")
            return row

    def refresh_after_return(self, token: str):
        """歓迎配送後の一度だけの再読。説明を発話せず、一時文脈だけへ補充する。"""
        with self._lock:
            row = {"control": "return_context_refresh", "reply": "", "status": "skipped"}
            if self._expire_research():
                return dict(row, status="context_expired")
            visit, context = self._visit, self.research
            if not visit or visit.token != token or not visit.greeted or not context:
                return dict(row, status="stale_return")
            if visit.refreshed:
                return dict(row, status="already_refreshed")
            if self.paused or self._busy or self._minecraft_active is not True:
                return dict(row, status="deferred")
            refresh = getattr(self.web, "refresh", None)
            visit.refreshed = True
            if context.pages or not refresh:
                return dict(row, status="context_ready" if context.pages else "unavailable",
                            context_page_ids=[p["id"] for p in context.pages])
            self._busy = True
            epoch = self._epoch

        def cancelled():
            with self._lock:
                return epoch != self._epoch or self._visit is not visit

        try:
            updated = refresh(context, cancelled=cancelled, emit=self.on_event)
            with self._lock:
                if cancelled():
                    return dict(row, status="interrupted")
                if self._expire_research():
                    return dict(row, status="context_expired")
                if updated is not None:
                    row["web_refresh"] = asdict(updated)
                    if updated.pages:
                        context.pages = updated.pages
                        context.search_results = updated.search_results
                return dict(row, status="context_ready" if context.pages else "unavailable",
                            context_page_ids=[p["id"] for p in context.pages])
        except Exception as exc:
            return dict(row, status="unavailable", error=type(exc).__name__)
        finally:
            with self._lock:
                self._busy = False

    def cancel(self):
        """検索・生成中でも明示的に打ち切れる。完了待ちの結果は配送しない。"""
        with self._lock:
            self._epoch += 1
            self.paused = False
            self._clear_focus(remember_research=True)
            self.last_activity = self.clock()
            return {"control": "cancel", "mode": "normal", "paused": False,
                    "return_context": self.return_context()}

    def interrupt(self):
        with self._lock:
            self._epoch += 1
            self.paused = True
            self._pending_web = None  # 中断前の同意・案内音声から後で突然開かない。
            self.last_activity = self.clock()
            return {"control": "interrupt", "mode": self.mode, "paused": True}

    def release(self):
        with self._lock:
            self.paused = False
            # ここでは説明を自動再生しない。次の本人の発話を待つ。
            return {"control": "release", "mode": self.mode, "paused": False}

    def _generate(self, request, model):
        try:
            raw = self.llm.generate_structured_json(request)
            payload = dict(raw)
            status = payload.pop("__dogido_status", "accepted")
            if status != "accepted":
                return None, status
            return model.model_validate(payload, strict=True), "accepted"
        except (ValidationError, TypeError, ValueError):
            return None, "invalid_payload"
        except Exception as exc:
            return None, f"generation_error:{type(exc).__name__}"

    def turn(self, text: str, *, turn_id: str, source: str = "text") -> dict:
        if not turn_id or not text.strip() or len(text) > 1000 or source not in {"text", "voice"}:
            raise ValueError("turn_id、1〜1000字の発話、text/voiceが必要")
        started = self.clock()
        with self._lock:
            record = {
                "turn_id": turn_id,
                "raw_text": text,
                "source": source,
                "mode_before": self.mode,
                "search": asdict(SearchResult([], [], "not_requested")),
                "interpretation": None,
                "reply": "",
                "references": [],
                "status": "",
            }
            if turn_id in self._seen:
                return dict(record, status="duplicate", mode_after=self.mode)
            if self.paused or self._busy:
                return dict(
                    record, status="paused" if self.paused else "busy", mode_after=self.mode
                )
            ttl = self.research_ttl_seconds if self.research else self.ttl_seconds
            if started - self.last_activity >= ttl:
                self._clear_focus(remember_research=True)
                record["context_expired"] = True
            self._busy = True
            epoch = self._epoch
            history = list(self.history)
            current = {"turn_id": turn_id, "role": "user", "text": text, "source": source}
            details = {
                "current": current,
                "history": history,
                "mode": self.mode,
                "focus": asdict(self.focus),
            }
            if self.last_research_topic:
                details["recent_research"] = self.return_context()
            research = self.research
            pending_web = self._pending_web
            if research:
                details["research"] = research.snapshot()
            # 受信した質問は中断しても残す。まだ生成していない返事は残さない。
            self.history.append(current)
            self._seen.append(turn_id)
            self.focus = Focus(question=text)
        try:
            if pending_web:
                handled = self._handle_web_consent(details, record, pending_web, epoch)
                with self._lock:
                    if epoch != self._epoch:
                        return dict(record, status="interrupted", reply="", references=[], mode_after=self.mode)
                    if handled:
                        if self._pending_web:
                            i = self._pending_web.interpretation
                            self.focus = Focus(i.question, i.target)
                        self._remember_reply(record, turn_id)
                        self.last_activity = self.clock()
                        return dict(record, mode_after=self.mode,
                                    duration_ms=round((self.clock() - started) * 1000))
            if research:
                handled = self._research_reply(details, record, research, epoch)
                with self._lock:
                    if epoch != self._epoch:
                        return dict(record, status="interrupted", reply="", references=[], mode_after=self.mode)
                    if handled:
                        if record["status"] == "handoff":
                            self._clear_focus(remember_research=True)
                            record["return_context"] = self.return_context()
                        else:
                            self.research = handled
                            self.mode = "language"
                        self._remember_reply(record, turn_id)
                        self.last_activity = self.clock()
                        record.update(mode_after=self.mode, duration_ms=round((self.clock() - started) * 1000))
                        return record
                    self.last_research_topic = research.question[:160]
                    self.research = None  # 明示された別の問いは、新しい検索の単位。
                    self._visit = None
                    details.pop("research", None)
            interpretation, status = self._generate(
                StructuredGenerationRequest(
                    kind="language_dialogue_interpretation",
                    details=details,
                    fallback_value={},
                    route="chat",
                    temperature=0.0,
                    max_tokens=850,
                ),
                Interpretation,
            )
            record["interpretation_status"] = status
            computed_fact = None
            if interpretation:
                record["interpretation"] = interpretation.model_dump()
                turns = {t["turn_id"]: t["text"] for t in history + [current]}
                evidence = interpretation.evidence
                valid = bool(evidence) and any(e.turn_id == turn_id for e in evidence)
                normalize = lambda s: unicodedata.normalize("NFKC", s)
                valid = valid and all(
                    e.turn_id in turns and normalize(e.quote) in normalize(turns[e.turn_id])
                    for e in evidence
                )
                if interpretation.target_status == "contextual":
                    # 対象を最新発話で言い直した場合も、過去の引用省略だけで捨てない。
                    known_target = bool(interpretation.target) and any(
                        normalize(interpretation.target) in normalize(t["text"])
                        for t in history + [current]
                    )
                    switching_away = (
                        interpretation.facet == "other"
                        and interpretation.relation in {"switch", "end"}
                    )
                    valid = valid and (
                        known_target
                        or switching_away
                        or any(e.turn_id != turn_id for e in evidence)
                    )
                if not valid:
                    record["interpretation_status"] = "ungrounded_interpretation"
                    interpretation = None
                else:
                    # 題材と質問の観点は別。国語の観点を抽出済みなら題材名で外さない。
                    if interpretation.facet != "other":
                        interpretation.topic = "language"
                    if interpretation.facet == "grade":
                        writing_evidence = mentions_writing(text)
                        continued = (
                            interpretation.target_status == "contextual"
                            and interpretation.relation in {"continue", "resume", "correct"}
                            and (
                                self._kanji_scope_confirmed
                                or details["focus"]["clarification"] == KANJI_GRADE_CONFIRMATION
                                or any(
                                    e.turn_id != turn_id and mentions_writing(e.quote)
                                    for e in interpretation.evidence
                                )
                            )
                        )
                        if not writing_evidence and not continued:
                            interpretation.target_status = "ambiguous"
                            interpretation.clarification = KANJI_GRADE_CONFIRMATION
                            interpretation.alternatives = ["漢字の配当学年", "漢字以外の学習"]
                    if interpretation.facet == "mora_count" and interpretation.target_status != "ambiguous":
                        computed_fact = count_explicit_kana(
                            interpretation.target, [e.quote for e in evidence]
                        )
                        if computed_fact is None:
                            interpretation.target_status = "ambiguous"
                            interpretation.clarification = "数えたい言葉の読みを、ひらがなかカタカナで教えてくれる？"
                    record["effective_interpretation"] = interpretation.model_dump()
            if interpretation is None:
                record.update(
                    status="clarify",
                    reply="ごめん、何のことを聞きたいか、もうちょっと教えてくれる？",
                )
                next_focus = None
            elif (
                interpretation.topic not in ({"language", "unclear", "general"} if self.web else {"language", "unclear"})
                or interpretation.relation == "end"
            ):
                # 実際のMinecraft返答は本体側の担当。試験用に知識のない回答を作らない。
                record.update(status="handoff", handoff_topic=interpretation.topic)
                next_focus = Focus()
            elif interpretation.target_status == "ambiguous" or interpretation.topic == "unclear":
                question = interpretation.clarification or "どの言葉の、どんなことが知りたいん？"
                record.update(status="clarify", reply=question)
                next_focus = Focus(
                    interpretation.question,
                    interpretation.target,
                    question,
                    interpretation.alternatives,
                )
            else:
                lookup = SearchResult([], [computed_fact], "computed") if computed_fact else self.search.search(
                    interpretation.search_terms,
                    facet=interpretation.facet,
                    target=interpretation.target,
                )
                char = interpretation.target
                direct_comparison = (
                    interpretation.facet == "comparison"
                    and len(char) == 1
                    and text.count(char) >= 2
                )
                if direct_comparison:
                    lookup.facts.append(
                        {
                            "id": f"input-character:{ord(char):x}",
                            "title_ja": "入力文字の比較",
                            "text_ja": f"今回の入力に同一文字『{char}』が{str(text.count(char))}回ある。文字列比較の結果。",
                            "claim_status": "input_character_comparison",
                            "sources": [],
                        }
                    )
                record["search"] = asdict(lookup)
                # 中断後に二段目の生成を新たに始めない。
                with self._lock:
                    if epoch != self._epoch:
                        return dict(record, status="interrupted", mode_after=self.mode)
                fixed_reply = verified_reply(interpretation, lookup.facts)
                if self.web and interpretation.lookup_requested:
                    reply, status = None, "lookup_requested"
                elif fixed_reply:
                    reply, status = fixed_reply, "accepted"
                    record["answer_origin"] = "code"
                elif not lookup.facts:
                    record["reply_status"] = "no_evidence"
                    reply, status = None, "no_evidence"
                else:
                    reply, status = self._generate(
                        StructuredGenerationRequest(
                            kind="language_dialogue_reply",
                            fallback_value={},
                            details={
                                **details,
                                "interpretation": interpretation.model_dump(),
                                "facts": lookup.facts,
                                "search_status": lookup.status,
                            },
                            route="chat",
                            temperature=0.0,
                            max_tokens=700,
                        ),
                        GroundedReply,
                    )
                record["reply_status"] = status
                fact_map = {f["id"]: f for f in lookup.facts}
                if reply and any(fid not in fact_map for fid in reply.fact_ids):
                    reply = None
                    record["reply_status"] = "unknown_fact_id"
                if reply and reply.status == "answer" and not reply.fact_ids:
                    reply = None
                    record["reply_status"] = "missing_support"
                if reply:
                    record.update(
                        status=reply.status,
                        reply=reply.text,
                        reply_analysis=reply.model_dump(),
                        references=[fact_map[fid] for fid in dict.fromkeys(reply.fact_ids)],
                    )
                else:
                    record.update(
                        status="unsupported",
                        reply="ごめんな、今の資料からはうまく説明できへんかった。一緒に教科書や資料集で確かめよか。",
                    )
                    if status == "no_evidence":
                        record["reply"] = (
                            "その言葉のことは、今の資料では確かめられへんかった。教科書や辞書で一緒に見てみよか。"
                        )
                next_focus = Focus(interpretation.question, interpretation.target)
                needs_context = reply is not None and reply.missing_kind == "context"
                if needs_context:
                    question = reply.clarification.strip() or "その言葉が出てくる文や、使う場面を教えてくれる？"
                    record.update(status="clarify", reply=question)
                    next_focus.clarification = question
                web_reason = (
                    "explicit_request" if interpretation.lookup_requested
                    else "" if needs_context
                    else "no_local_facts" if not lookup.facts
                    else "uncertain_reply" if (
                        reply and reply.status in {"partial", "unsupported"}
                        and reply.missing_kind == "evidence" and reply.missing.strip()
                    )
                    else ""
                )
                if self.web and web_reason:
                    with self._lock:
                        if epoch != self._epoch:
                            return dict(record, status="interrupted", reply="", references=[], mode_after=self.mode)
                        self._pending_web = WebHandoff(
                            interpretation.model_copy(deep=True), web_reason,
                            [s["url"] for f in lookup.facts for s in f.get("sources", []) if s.get("url")],
                        )
                        record.update(status="web_consent_requested", reply=WEB_PERMISSION_PROMPT,
                                      references=[], web_proposal={"trigger_reason": web_reason})
            with self._lock:
                if epoch != self._epoch:
                    return dict(
                        record, status="interrupted", reply="", references=[], mode_after=self.mode
                    )
                if record["status"] == "handoff":
                    self._clear_focus(remember_research=True)
                    record["return_context"] = self.return_context()
                    self.history.append(current)
                elif next_focus is not None:
                    self.mode = "language"
                    self.focus = next_focus
                    self._kanji_scope_confirmed = (
                        interpretation is not None
                        and interpretation.facet == "grade"
                        and record["status"] != "clarify"
                    )
                self._remember_reply(record, turn_id)
                self.last_activity = self.clock()
                record.update(
                    mode_after=self.mode, duration_ms=round((self.clock() - started) * 1000)
                )
                return record
        finally:
            with self._lock:
                self._busy = False

    def _remember_reply(self, record, turn_id):
        if record["reply"]:
            self.history.append({"turn_id": f"{turn_id}:reply", "role": "assistant", "text": record["reply"]})

    def _research_reply(self, details, record, context, epoch):
        # 意図分類には本文を入れない。「取り違えた報告」を「困惑」と混同させない。
        intent_details = {k: v for k, v in details.items() if k != "research"}
        intent_details["research"] = {"question": context.question, "target": context.target, "phase": context.phase}
        outcome, status = self._generate(
            StructuredGenerationRequest(
                kind="language_research_intent", details=intent_details, fallback_value={},
                route="chat", temperature=0.0, max_tokens=350,
            ), ResearchIntent,
        )
        record["research_reply_status"] = status
        # 提案の根拠は今回の発話。Web本文や古い相槌を終了意思として使わない。
        if outcome and unicodedata.normalize("NFKC", outcome.evidence) not in unicodedata.normalize("NFKC", details["current"]["text"]):
            outcome = None
            record["research_reply_status"] = "ungrounded_intent"
        next_context = ResearchContext(context.question, context.target, context.pages, context.phase,
                                       context.search_results, context.search_url)
        if not outcome:
            record.update(status="research_unclear", reply="ごめん、もうちょっと聞かせてくれる？")
        elif outcome.intent == "new_question":
            return None
        elif outcome.intent == "return":
            record.update(status="handoff", handoff_topic="minecraft", reply="よし、冒険にもどろか！", research_phase="none")
        elif outcome.intent == "uncertain" and not context.search_url:
            next_context.phase = "return_offered"
            record.update(status="return_offered", reply=TEACHER_SUGGESTION + RETURN_INVITATION)
        elif outcome.intent == "continue":
            next_context.phase = "discussing"
            record.update(status="research_continue", reply="ええで、もうちょっと考えてみよか。気になってること、聞かせてや。")
        elif outcome.intent in {"report", "discuss", "uncertain"}:
            with self._lock:
                if epoch != self._epoch:
                    return next_context
            refresh = getattr(self.web, "refresh", None)
            # focusを送るホストでは歓迎後に再読する。非対応ホストだけ旧発話時fallback。
            if (refresh and not context.pages and context.search_url
                    and self._minecraft_active is None
                    and not (self._visit and self._visit.refreshed)):
                updated = refresh(context, cancelled=lambda: epoch != self._epoch, emit=self.on_event)
                if epoch != self._epoch:
                    return next_context
                if updated is not None:
                    record["web_refresh"] = asdict(updated)
                    if updated.pages:
                        next_context.pages = updated.pages
                        next_context.search_results = updated.search_results
                        context = next_context
                        details = {**details, "research": context.snapshot()}
            record["context_page_ids"] = [p["id"] for p in context.pages]
            reading, reading_status = self._generate(
                StructuredGenerationRequest(
                    kind="language_research_reading", details=details, fallback_value={},
                    route="chat", temperature=0.0, max_tokens=750,
                ), ResearchReading,
            )
            record["research_reading_status"] = reading_status
            if reading:
                record["research_reading"] = reading.model_dump()
            next_context.phase = "discussing"
            valid = reading and bool(reading.perspective.strip()) and checked_quotes(reading.quotes, context.pages)
            record["quote_validation"] = "matched" if valid else "unsupported"
            if valid:
                record.update(status="research_reflection",
                    reply=(reading.perspective if any(
                        p["use"] == "google_ai_overview" for p in context.pages
                    ) else f"オレにはこう読み取れたで。{reading.perspective} どう思う？"),
                    references=[p for p in context.pages if p["id"] in {q.page_id for q in reading.quotes}],
                    source_quotes=[q.model_dump() for q in reading.quotes])
            else:
                record.update(status="research_uncertain",
                    reply="そこまでは、オレには資料から確かめられへんかった。どのあたりでそう思ったん？")
                if context.search_url:
                    record["reply"] = (
                        "検索結果の紹介文までは受け取れたけど、リンク先の本文はまだ読めてへんねん。気になったところを教えてくれる？"
                        if not context.pages and context.search_results else
                        "今の検索ページからは、説明の本文を受け取れてへんねん。調べるのはいったんここまでにしよか。"
                        if not context.pages else
                        "そのところは、今の概要だけやとまだ分からへんな。気になる説明を一緒に見てみよか。"
                    )
        elif outcome.intent == "acknowledge" and context.phase == "discussing":
            next_context.phase = "return_offered"
            record.update(status="return_offered", reply=RETURN_INVITATION)
        else:
            record.update(status="awaiting_report", reply="どうやった？ 分かったこと、オレにも教えてや。")
        if outcome:
            record["research_interpretation"] = outcome.model_dump()
        if record["status"] != "handoff":
            record["research_phase"] = next_context.phase
        return next_context
