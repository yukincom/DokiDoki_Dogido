"""入力→解釈→確認または検索→返答。ゲーム・TTS・永続記憶を操作しない。"""

from collections import deque
from dataclasses import asdict, dataclass, field
import threading
import time
import unicodedata
import re

from pydantic import ValidationError

from dogido_server.llm.types import LLMFrontend, StructuredGenerationRequest
from .contracts import GroundedReply, Interpretation
from .retrieval import LocalDialogueSearch, SearchResult


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


class LanguageDialogue:
    """一つの会話用。モデル呼出し中も interrupt() で古い返答を無効化できる。"""

    def __init__(self, llm: LLMFrontend, search=None, *, clock=time.monotonic, ttl_seconds=300):
        self.llm = llm
        self.search = search or LocalDialogueSearch()
        self.clock = clock
        self.ttl_seconds = ttl_seconds
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

    def _clear_focus(self):
        self.mode = "normal"
        self.focus = Focus()
        self.history.clear()
        self._kanji_scope_confirmed = False

    def interrupt(self):
        with self._lock:
            self._epoch += 1
            self.paused = True
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
            if started - self.last_activity >= self.ttl_seconds:
                self._clear_focus()
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
            # 受信した質問は中断しても残す。まだ生成していない返事は残さない。
            self.history.append(current)
            self._seen.append(turn_id)
            self.focus = Focus(question=text)
        try:
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
                    # 続きの対象が実際の会話にあれば、過去発話の引用省略だけで捨てない。
                    known_target = bool(interpretation.target) and any(
                        interpretation.target in t["text"] for t in history
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
                    record["effective_interpretation"] = interpretation.model_dump()
            if interpretation is None:
                record.update(
                    status="clarify",
                    reply="ごめん、何のことを聞きたいか、もうちょっと教えてくれる？",
                )
                next_focus = None
            elif (
                interpretation.topic not in {"language", "unclear"}
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
                lookup = self.search.search(
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
                if not lookup.facts:
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
            with self._lock:
                if epoch != self._epoch:
                    return dict(
                        record, status="interrupted", reply="", references=[], mode_after=self.mode
                    )
                if record["status"] == "handoff":
                    self._clear_focus()
                    self.history.append(current)
                elif next_focus is not None:
                    self.mode = "language"
                    self.focus = next_focus
                    self._kanji_scope_confirmed = (
                        interpretation is not None
                        and interpretation.facet == "grade"
                        and record["status"] != "clarify"
                    )
                if record["reply"]:
                    self.history.append(
                        {
                            "turn_id": f"{turn_id}:reply",
                            "role": "assistant",
                            "text": record["reply"],
                        }
                    )
                self.last_activity = self.clock()
                record.update(
                    mode_after=self.mode, duration_ms=round((self.clock() - started) * 1000)
                )
                return record
        finally:
            with self._lock:
                self._busy = False
