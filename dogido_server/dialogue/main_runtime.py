"""本体service用の非同期・限定国語対話ランタイム。

ゲームイベント直列workerをLLM/Web待ちで塞がない。結果は次の安全な
ゲームイベントで回収し、音声完了後だけassistant履歴へ確定する。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re
import threading
import unicodedata
from typing import Callable
from uuid import uuid4

from dogido_server.dialogue.foreground import ForegroundDialogue
from dogido_server.language_dialogue.controller import LanguageDialogue
from dogido_server.language_dialogue.conversation_turns import DialogueWorker, TurnLedger
from dogido_server.language_dialogue.participation import (
    corrects_false_suppression,
    direct_call_body,
    has_topic_shift_cue,
)

LOGGER = logging.getLogger("uvicorn.error")
_MAX_RESULTS = 16
ADDRESS_REPAIR_PREFIX = "あっ、ごめん！ オレに言うてたんやな。"


@dataclass(slots=True)
class PendingAddress:
    original_turn_id: str
    epoch: int
    text: str
    raw_text: str
    source: str
    held_at: datetime
    expires_at: datetime
    topic_summary: str
    phase: str = "awaiting_address"
    repair_turn_id: str = ""
    repair_utterance_id: str = ""
    repair_completed: bool = False


class MainLanguageRuntime:
    def __init__(
        self,
        llm,
        *,
        ledger: TurnLedger,
        foreground: ForegroundDialogue,
        web=None,
        history_provider: Callable[[], list[dict[str, str]]] | None = None,
        situation_provider: Callable[[], list[str]] | None = None,
        topic_fresh_ms: int = 120000,
        pending_address_ttl_ms: int = 300000,
    ) -> None:
        self.ledger = ledger
        self.foreground = foreground
        self.dialogue = LanguageDialogue(llm, web=web)
        self.history_provider = history_provider
        self.situation_provider = situation_provider
        self.topic_fresh_ms = max(0, int(topic_fresh_ms))
        self.pending_address_ttl_ms = max(1000, int(pending_address_ttl_ms))
        self._lock = threading.RLock()
        # maxlen付きdequeの暗黙破棄は使わず、古いturnを台帳上も明示終了する。
        self._results: deque[dict] = deque()
        self._epoch = 0
        self._pending_turn_ids: set[str] = set()
        self._submission_context: dict[str, dict[str, object]] = {}
        self._pending_address: PendingAddress | None = None
        self._closed = False
        self.worker = DialogueWorker(self._emit, max_pending=1)

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def active_research_ttl_ms(self) -> int | None:
        """可視Web閲覧中にforegroundを保持する既存の無活動期限。"""

        return self.dialogue.active_research_ttl_ms()

    def submit_turn(
        self,
        text: str,
        *,
        source: str,
        observed_at: datetime,
        raw_text: str = "",
    ) -> tuple[bool, str]:
        turn_id = "main-language:" + uuid4().hex
        with self._lock:
            if self._closed:
                return False, turn_id
            epoch = self._epoch
            previous_activity = self.foreground.last_conversation_at
            self.ledger.begin(
                turn_id,
                epoch=epoch,
                raw_text=raw_text or text,
                semantic_text=text,
                source=source,
            )
            history_turns = self._prompt_turns(observed_at)
            history = self._history_text(history_turns)
            situation_context = self._situation_context()
            self._pending_turn_ids.add(turn_id)
            self._submission_context[turn_id] = {
                "observed_at": observed_at,
                "previous_activity": previous_activity,
                "source": source,
                "explicit_transition": (
                    direct_call_body(text) is not None or has_topic_shift_cue(text)
                ),
            }

        def cancelled() -> bool:
            with self._lock:
                return self._closed or epoch != self._epoch

        accepted = self.worker.submit(
            work_id="language:" + uuid4().hex,
            work_kind="turn",
            session_epoch=epoch,
            turn_id=turn_id,
            run=lambda: self.dialogue.turn(
                text,
                turn_id=turn_id,
                source=source,
                cancelled=cancelled,
                conversation_history=history,
                conversation_turns=history_turns,
                situation_context=situation_context,
                defer_reply_history=True,
                host_mode=True,
            ),
        )
        if not accepted:
            with self._lock:
                self._pending_turn_ids.discard(turn_id)
                self._submission_context.pop(turn_id, None)
            self.ledger.finish_without_reply(turn_id, resolution="worker_busy")
        else:
            self.foreground.activate("learning", now=observed_at, player_text=text)
        return accepted, turn_id

    def poll(self) -> list[dict]:
        with self._lock:
            rows = list(self._results)
            self._results.clear()
            epoch = self._epoch
        accepted: list[dict] = []
        for envelope in rows:
            turn_id = str(envelope.get("turn_id") or "")
            if envelope.get("session_epoch") != epoch:
                if turn_id:
                    self.dialogue.discard_deferred_reply(turn_id)
                    with self._lock:
                        self._submission_context.pop(turn_id, None)
                continue
            if envelope.get("work_kind") == "turn":
                with self._lock:
                    self._pending_turn_ids.discard(turn_id)
            accepted.append(envelope)
        return accepted

    def accept_turn_result(self, envelope: dict, *, observed_at: datetime) -> dict:
        turn_id = str(envelope.get("turn_id") or "")
        result = dict(envelope.get("result") or {})
        error = str(envelope.get("error") or "")
        with self._lock:
            submitted = self._submission_context.pop(turn_id, {})
        if error or not result:
            self.ledger.routed(turn_id, route="learning", status=error or "empty_result")
            self.ledger.finish_without_reply(turn_id, resolution=error or "empty_result")
            self.dialogue.discard_deferred_reply(turn_id)
            return {"status": "failed", "turn_id": turn_id, "reply": ""}
        status = str(result.get("status") or "unknown")
        if self._is_host_chat_result(result):
            return self._accept_host_chat_result(
                turn_id,
                result,
                submitted=submitted,
                observed_at=observed_at,
            )
        route = self._route_for_result(result)
        ledger_row = self.ledger.get(turn_id) or {}
        reply = str(result.get("reply") or "").strip()
        history_route = (
            "learning"
            if route == "none" and reply and status in {"handoff", "web_declined"}
            else route
        )
        self.ledger.routed(turn_id, route=history_route, status=status)
        if route == "none":
            self.foreground.clear()
        else:
            self.foreground.activate(route, now=observed_at)
        if not reply:
            self.ledger.finish_without_reply(turn_id, resolution=status)
            self.dialogue.discard_deferred_reply(turn_id)
            return {
                **result,
                "turn_id": turn_id,
                "raw_text": str(ledger_row.get("raw_text") or result.get("raw_text") or ""),
                "semantic_text": str(
                    ledger_row.get("semantic_text") or result.get("raw_text") or ""
                ),
                "reply": "",
                "foreground_route": route,
            }
        speech = result.get("speech") if isinstance(result.get("speech"), dict) else {}
        utterance_id = str(speech.get("utterance_id") or ("main-dialogue:" + uuid4().hex))
        self.ledger.select_reply(turn_id, reply=reply, utterance_id=utterance_id)
        return {
            **result,
            "turn_id": turn_id,
            "raw_text": str(ledger_row.get("raw_text") or result.get("raw_text") or ""),
            "semantic_text": str(
                ledger_row.get("semantic_text") or result.get("raw_text") or ""
            ),
            "reply": reply,
            "utterance_id": utterance_id,
            "foreground_route": route,
        }

    def accept_control_result(self, envelope: dict, *, observed_at: datetime) -> dict:
        """音声完了後のWeb制御結果を、通常turnと混ぜずforegroundへ反映する。"""

        turn_id = str(envelope.get("turn_id") or "")
        result = dict(envelope.get("result") or {})
        error = str(envelope.get("error") or "")
        if error or not result:
            self.foreground.activate("learning", now=observed_at)
            return {
                "status": "failed",
                "control_status": error or "empty_result",
                "reply": "",
                "foreground_route": "learning",
            }
        route = self._route_for_result(result)
        if route == "none":
            self.foreground.clear()
        else:
            self.foreground.activate(route, now=observed_at)
        reply = str(result.get("reply") or "").strip()
        if not reply:
            return {**result, "foreground_route": route}

        # 検索失敗・参照のみの案内も、実際に再生された後だけ本体履歴へ入れる。
        # player入力を捏造せず、案内専用turnとして同じ短期台帳へ結ぶ。
        turn_id = turn_id or ("main-web-control:" + uuid4().hex)
        self.ledger.begin(
            turn_id,
            epoch=self.epoch,
            raw_text="",
            semantic_text="",
            source="text",
        )
        self.ledger.routed(turn_id, route="learning", status=str(result.get("status") or ""))
        utterance_id = "main-web-control-reply:" + uuid4().hex
        self.dialogue.defer_host_reply(turn_id)
        self.ledger.select_reply(turn_id, reply=reply, utterance_id=utterance_id)
        return {
            **result,
            "turn_id": turn_id,
            "raw_text": "",
            "semantic_text": "",
            "reply": reply,
            "utterance_id": utterance_id,
            "foreground_route": route,
        }

    def playback_event(
        self,
        event: dict[str, str],
        *,
        observed_at: datetime,
    ) -> dict | None:
        utterance_id = event.get("utterance_id", "")
        status = event.get("status", "")
        if status == "queued":
            self.ledger.queued(utterance_id)
            return None
        if status == "started":
            self.ledger.started(utterance_id)
            return None
        if status not in {"completed", "failed", "cancelled"}:
            return None
        row = self.ledger.resolve(
            utterance_id,
            status,
            resolution=event.get("resolution", ""),
        )
        if row is None:
            return None
        turn_id = str(row.get("turn_id") or "")
        reply = str(row.get("selected_reply") or "")
        row_epoch = row.get("epoch")
        current_web_departure = bool(
            utterance_id.startswith("web-departure:")
            and type(row_epoch) is int
            and row_epoch == self.epoch
        )
        if status == "completed":
            self.dialogue.confirm_delivered_reply(turn_id, reply)
            self.foreground.note_completed_turn(
                turn_id,
                str(row.get("semantic_text") or ""),
                reply,
                route=str(row.get("route") or "learning"),
                at=observed_at,
            )
            pending = self._pending_address
            if (
                pending is not None
                and pending.repair_turn_id == turn_id
                and pending.repair_utterance_id == utterance_id
            ):
                pending.repair_completed = True
            if current_web_departure:
                self._submit_playback_control(utterance_id, observed_at=observed_at)
        else:
            self.dialogue.discard_deferred_reply(turn_id)
            pending = self._pending_address
            if (
                pending is not None
                and pending.repair_turn_id == turn_id
                and pending.repair_utterance_id == utterance_id
            ):
                # 修復質問が実際には届かなかった場合、後の短い肯定だけで
                # 元入力を再生しない。もう一度の名指しからやり直せる状態へ戻す。
                pending.phase = "awaiting_address"
                pending.repair_turn_id = ""
                pending.repair_utterance_id = ""
                pending.repair_completed = False
            if current_web_departure:
                self.dialogue.on_speech_playback_result(
                    utterance_id,
                    status=status,
                    event_id="main-playback-terminal:" + uuid4().hex,
                )
                self.foreground.activate("learning", now=observed_at)
        return row

    def mark_dispatched(self, utterance_id: str) -> dict | None:
        return self.ledger.dispatched(utterance_id)

    def handle_pending_address_input(
        self,
        text: str,
        *,
        source: str,
        observed_at: datetime,
    ) -> dict[str, object] | None:
        """保留後の名指しと確認だけをコードで扱う。通常入力は変更しない。"""

        self.expire_pending_address(observed_at)
        pending = self._pending_address
        if pending is None:
            return None
        if pending.epoch != self.epoch:
            self._cancel_pending_address("attention_interrupted")
            return None

        normalized = unicodedata.normalize("NFKC", text).strip()
        if pending.phase == "awaiting_address":
            addressed = direct_call_body(normalized) is not None or corrects_false_suppression(
                normalized
            )
            if not addressed:
                return None
            turn_id = "main-address:" + uuid4().hex
            utterance_id = "main-address-repair:" + uuid4().hex
            reply = (
                f"{ADDRESS_REPAIR_PREFIX} さっきの『{pending.topic_summary}』のこと、"
                "今から聞いてええ？"
            )
            self.ledger.begin(
                turn_id,
                epoch=self.epoch,
                raw_text=text,
                semantic_text=text,
                source=source,
            )
            self.ledger.routed(turn_id, route="address_repair", status="confirmation_requested")
            self.ledger.select_reply(turn_id, reply=reply, utterance_id=utterance_id)
            pending.phase = "awaiting_confirmation"
            pending.repair_turn_id = turn_id
            pending.repair_utterance_id = utterance_id
            pending.repair_completed = False
            self.foreground.activate("learning", now=observed_at, player_text=text)
            return {
                "consumed": True,
                "status": "address_confirmation_requested",
                "turn_id": turn_id,
                "reply": reply,
                "utterance_id": utterance_id,
                "foreground_route": "learning",
                "raw_text": text,
            }

        confirmation_turn_id = "main-address-confirmation:" + uuid4().hex
        self.ledger.begin(
            confirmation_turn_id,
            epoch=self.epoch,
            raw_text=text,
            semantic_text=text,
            source=source,
        )
        self.ledger.routed(
            confirmation_turn_id,
            route="address_confirmation",
            status="checked",
        )
        decision = self._confirmation_decision(normalized)
        if decision == "accept" and pending.repair_completed:
            self.ledger.finish_without_reply(
                confirmation_turn_id,
                resolution="address_confirmed",
            )
            self.ledger.routed(
                pending.original_turn_id,
                route="casual",
                status="host_chat_confirmed",
            )
            request = self._host_chat_request(pending)
            self._pending_address = None
            return {
                "consumed": True,
                "status": "host_chat_ready",
                "host_chat_request": request,
            }
        if decision == "decline" and pending.repair_completed:
            self.ledger.finish_without_reply(
                confirmation_turn_id,
                resolution="address_declined",
            )
            self._cancel_pending_address("declined_unaddressed")
            return {"consumed": True, "status": "address_declined"}
        self.ledger.finish_without_reply(
            confirmation_turn_id,
            resolution=(
                "confirmation_before_repair_completed"
                if not pending.repair_completed
                else "ambiguous_confirmation"
            ),
        )
        return {"consumed": True, "status": "awaiting_confirmation"}

    def expire_pending_address(self, now: datetime) -> bool:
        pending = self._pending_address
        if pending is None or now < pending.expires_at:
            return False
        self.ledger.routed(pending.original_turn_id, route="casual", status="expired")
        self.ledger.cancel_turn(
            pending.original_turn_id,
            resolution="expired_unaddressed",
        )
        self.dialogue.discard_deferred_reply(pending.original_turn_id)
        self._pending_address = None
        return True

    def observe_external_user_turn(
        self,
        turn_id: str,
        text: str,
        *,
        source: str,
    ) -> bool:
        """状態機械所有の学習入力を同じ短期会話へ登録する。"""

        return self.dialogue.observe_external_user_turn(
            turn_id,
            text,
            source=source,
        )

    def discard_undelivered_result(
        self,
        result: dict[str, object],
        *,
        resolution: str,
    ) -> None:
        """service待ち列から外れた選択済み返答を、履歴へ入れず閉じる。"""

        turn_id = str(result.get("turn_id") or "")
        utterance_id = str(result.get("utterance_id") or "")
        if utterance_id:
            self.ledger.resolve(
                utterance_id,
                "cancelled",
                resolution=resolution,
            )
        elif turn_id:
            self.ledger.finish_without_reply(turn_id, resolution=resolution)
        if turn_id:
            self.dialogue.discard_deferred_reply(turn_id)

    def reject_host_chat_request(self, turn_id: str, *, resolution: str) -> None:
        """serviceの有界入力列へ戻せなかったhost handoffを明示終了する。"""

        if not turn_id:
            return
        self.ledger.routed(turn_id, route="casual", status="host_chat_rejected")
        self.ledger.cancel_turn(turn_id, resolution=resolution)
        self.dialogue.discard_deferred_reply(turn_id)

    def interrupt_for_combat(self) -> None:
        research_active = self.active_research_ttl_ms() is not None
        dropped_result_turn_ids: list[str] = []
        with self._lock:
            self._epoch += 1
            self._pending_turn_ids.clear()
            dropped_result_turn_ids = [
                str(row.get("turn_id") or "")
                for row in self._results
                if row.get("work_kind") == "turn" and row.get("turn_id")
            ]
            self._results.clear()
            self._submission_context.clear()
        for turn_id in dropped_result_turn_ids:
            self.dialogue.discard_deferred_reply(turn_id)
        self._cancel_pending_address("attention_interrupted")
        self.dialogue.interrupt(preserve_deferred_replies=True)
        self.ledger.cancel_pending(resolution="combat")
        if not research_active and self.foreground.route == "web":
            # 同意／出発待ちや起動途中は戦闘で失効する。既に読書中なら
            # researchを保持し、戦闘後も明示復帰までWeb所有を維持する。
            self.foreground.clear()

    def release_after_combat(self) -> None:
        self.dialogue.release()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._epoch += 1
            self._submission_context.clear()
        self._cancel_pending_address("quit")
        self.dialogue.cancel()
        self.ledger.cancel_pending(resolution="quit")
        self.worker.close()
        web = getattr(self.dialogue, "web", None)
        client = getattr(web, "client", None)
        if client is not None and callable(getattr(client, "close", None)):
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001 - 任意Web終了失敗でserver shutdownを止めない
                LOGGER.warning(
                    "main_language_web_close_failed error=%s",
                    type(exc).__name__,
                )

    def _submit_playback_control(self, utterance_id: str, *, observed_at: datetime) -> bool:
        with self._lock:
            if self._closed:
                return False
            epoch = self._epoch

        def cancelled() -> bool:
            with self._lock:
                return self._closed or epoch != self._epoch

        def run_control() -> dict:
            try:
                return self.dialogue.on_speech_playback_result(
                    utterance_id,
                    status="completed",
                    event_id="main-playback:" + uuid4().hex,
                    cancelled=cancelled,
                    defer_reply_history=True,
                )
            except Exception:
                # controllerが結果dictを返す前に落ちても、消費前の許可を
                # 残して次の短い肯定から突然開くことは許さない。
                self.dialogue.abandon_web_handoff(
                    utterance_id,
                    reason="playback_control_failed",
                )
                raise

        accepted = self.worker.submit(
            work_id="playback:" + uuid4().hex,
            work_kind="playback_control",
            session_epoch=epoch,
            turn_id="main-web-control:" + uuid4().hex,
            run=run_control,
        )
        if not accepted:
            self.dialogue.abandon_web_handoff(
                utterance_id,
                reason="playback_control_queue_full",
            )
            self.foreground.activate("learning", now=observed_at)
            LOGGER.warning(
                "main_language_playback_control_rejected utterance_id=%s",
                utterance_id[:160],
            )
        return accepted

    def _emit(self, event: dict) -> None:
        evicted: dict | None = None
        with self._lock:
            if self._closed:
                return
            if len(self._results) >= _MAX_RESULTS:
                evicted = self._results.popleft()
            self._results.append(event)
        if evicted is not None:
            turn_id = str(evicted.get("turn_id") or "")
            if evicted.get("work_kind") == "turn" and turn_id:
                with self._lock:
                    self._pending_turn_ids.discard(turn_id)
                self.ledger.finish_without_reply(
                    turn_id,
                    resolution="worker_result_queue_replaced",
                )
                self.dialogue.discard_deferred_reply(turn_id)
            LOGGER.warning(
                "main_language_worker_result_replaced work_kind=%s turn_id=%s",
                str(evicted.get("work_kind") or "")[:80],
                turn_id[:160],
            )

    @staticmethod
    def _route_for_result(result: dict) -> str:
        status = str(result.get("status") or "")
        if status in {"handoff", "web_declined", "context_expired"}:
            return "none"
        if status in {
            "web_consent_requested",
            "web_waiting_playback",
            "awaiting_report",
            "reference_only",
            "research_continue",
            "research_reflection",
            "research_unclear",
            "research_topic_confirmation",
            "return_offered",
        }:
            return "web"
        web = result.get("web")
        if isinstance(web, dict) and web.get("status") in {
            "read",
            "results_only",
            "page_opened",
        }:
            return "web"
        if result.get("mode_after") == "language":
            return "learning"
        if result.get("route_owner") in {"player_chat", "host_code"}:
            return "casual"
        return "learning"

    def _accept_host_chat_result(
        self,
        turn_id: str,
        result: dict,
        *,
        submitted: dict[str, object],
        observed_at: datetime,
    ) -> dict[str, object]:
        row = self.ledger.get(turn_id) or {}
        submitted_at = submitted.get("observed_at")
        if not isinstance(submitted_at, datetime):
            submitted_at = observed_at
        previous = submitted.get("previous_activity")
        elapsed_ms = (
            (submitted_at - previous).total_seconds() * 1000
            if isinstance(previous, datetime)
            else float("inf")
        )
        interpretation = result.get("effective_interpretation")
        if not isinstance(interpretation, dict):
            interpretation = result.get("interpretation")
        if not isinstance(interpretation, dict):
            interpretation = {}
        related = bool(
            interpretation.get("topic") == "language"
            and interpretation.get("relation") in {"continue", "resume", "correct"}
        )
        explicit = bool(submitted.get("explicit_transition"))
        self.ledger.routed(
            turn_id,
            route="casual",
            status="host_chat_ready" if related or explicit or elapsed_ms >= self.topic_fresh_ms else "awaiting_address",
        )
        if related or explicit or elapsed_ms >= self.topic_fresh_ms:
            return {
                **result,
                "status": "host_chat_ready",
                "turn_id": turn_id,
                "reply": "",
                "host_chat_request": {
                    "turn_id": turn_id,
                    "text": str(row.get("semantic_text") or result.get("raw_text") or ""),
                    "raw_text": str(row.get("raw_text") or result.get("raw_text") or ""),
                    "source": str(row.get("source") or submitted.get("source") or "text"),
                    "foreground_route": "casual",
                },
            }

        previous_pending = self._pending_address
        if previous_pending is not None:
            self.ledger.routed(
                previous_pending.original_turn_id,
                route="casual",
                status="replaced",
            )
            self.ledger.cancel_turn(
                previous_pending.original_turn_id,
                resolution="replaced_unaddressed",
            )
            self.dialogue.discard_deferred_reply(previous_pending.original_turn_id)
        held = PendingAddress(
            original_turn_id=turn_id,
            epoch=self.epoch,
            text=str(row.get("semantic_text") or result.get("raw_text") or ""),
            raw_text=str(row.get("raw_text") or result.get("raw_text") or ""),
            source=str(row.get("source") or submitted.get("source") or "text"),
            held_at=submitted_at,
            expires_at=submitted_at
            + timedelta(milliseconds=self.pending_address_ttl_ms),
            topic_summary=self._topic_summary(
                str(row.get("semantic_text") or result.get("raw_text") or "")
            ),
        )
        self._pending_address = held
        return {
            **result,
            "status": "awaiting_address",
            "turn_id": turn_id,
            "reply": "",
            "foreground_route": "learning",
        }

    @staticmethod
    def _is_host_chat_result(result: dict) -> bool:
        # research側の ``handoff`` は「冒険へ戻る」という固定返答を持つ。
        # host_modeが明示した一般会話返却だけを、本体player_chatへ渡す。
        return bool(result.get("host_chat_requested"))

    @staticmethod
    def _topic_summary(text: str) -> str:
        cleaned = " ".join((text or "").replace("\n", " ").split()).strip(
            "『』「」 。.!！?？"
        )
        if len(cleaned) <= 48:
            return cleaned
        return cleaned[:47] + "…"

    @staticmethod
    def _confirmation_decision(text: str) -> str:
        word = re.sub(r"[\s、,。.!！?？]+", "", text)
        if word in {"うん", "はい", "ええで", "いいよ", "聞いて", "お願い", "そうして"}:
            return "accept"
        if word in {"いや", "いいえ", "違う", "ちがう", "やめとく", "もういい"}:
            return "decline"
        return "uncertain"

    @staticmethod
    def _host_chat_request(pending: PendingAddress) -> dict[str, object]:
        return {
            "turn_id": pending.original_turn_id,
            "text": pending.text,
            "raw_text": pending.raw_text or pending.text,
            "source": pending.source,
            "foreground_route": "casual",
        }

    def _cancel_pending_address(self, resolution: str) -> None:
        pending = self._pending_address
        if pending is None:
            return
        self.ledger.routed(
            pending.original_turn_id,
            route="casual",
            status="cancelled",
        )
        self.ledger.cancel_turn(pending.original_turn_id, resolution=resolution)
        self.dialogue.discard_deferred_reply(pending.original_turn_id)
        self._pending_address = None

    def _prompt_turns(self, now: datetime) -> list[dict[str, str]]:
        groups: list[list[dict[str, str]]] = []
        if self.history_provider is not None:
            try:
                supplied = self.history_provider()
            except Exception:
                supplied = []
            if isinstance(supplied, list):
                groups.append(supplied)
        groups.append(self.ledger.prompt_turns())
        merged: list[dict[str, str]] = []
        positions: dict[tuple[str, str], int] = {}
        for group in groups:
            for row in group:
                if not isinstance(row, dict):
                    continue
                observed_at = row.get("observed_at")
                if isinstance(observed_at, str) and observed_at:
                    try:
                        age = (now - datetime.fromisoformat(observed_at)).total_seconds()
                    except (TypeError, ValueError):
                        age = 0.0
                    if age >= self.dialogue.ttl_seconds:
                        continue
                turn_id = str(row.get("turn_id") or "")[:180]
                role = str(row.get("role") or "")
                text = str(row.get("text") or "")[:1000]
                if not turn_id or role not in {"user", "assistant"} or not text:
                    continue
                key = (turn_id, role)
                normalized = {"turn_id": turn_id, "role": role, "text": text}
                if row.get("source") in {"text", "voice"}:
                    normalized["source"] = str(row["source"])
                if key in positions:
                    merged[positions[key]] = normalized
                else:
                    positions[key] = len(merged)
                    merged.append(normalized)
        return merged[-20:]

    def _situation_context(self) -> str:
        if self.situation_provider is None:
            return ""
        try:
            supplied = self.situation_provider()
        except Exception:
            return ""
        if not isinstance(supplied, list):
            return ""
        lines = [
            " ".join(str(item or "").replace("\n", " ").split())[:160]
            for item in supplied[-4:]
        ]
        return "\n".join(line for line in lines if line)[:640]

    @staticmethod
    def _history_text(rows: list[dict[str, str]]) -> str:
        return "\n".join(
            f"{'プレイヤー' if row['role'] == 'user' else 'ドギド'}: {row['text']}"
            for row in rows
        )
