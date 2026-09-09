"""本体service用の非同期・限定国語対話ランタイム。

ゲームイベント直列workerをLLM/Web待ちで塞がない。結果は次の安全な
ゲームイベントで回収し、音声完了後だけassistant履歴へ確定する。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
import logging
import threading
from uuid import uuid4

from dogido_server.dialogue.foreground import ForegroundDialogue
from dogido_server.language_dialogue.controller import LanguageDialogue
from dogido_server.language_dialogue.conversation_turns import DialogueWorker, TurnLedger

LOGGER = logging.getLogger("uvicorn.error")
_MAX_RESULTS = 16


class MainLanguageRuntime:
    def __init__(
        self,
        llm,
        *,
        ledger: TurnLedger,
        foreground: ForegroundDialogue,
        web=None,
    ) -> None:
        self.ledger = ledger
        self.foreground = foreground
        self.dialogue = LanguageDialogue(llm, web=web)
        self._lock = threading.RLock()
        # maxlen付きdequeの暗黙破棄は使わず、古いturnを台帳上も明示終了する。
        self._results: deque[dict] = deque()
        self._epoch = 0
        self._pending_turn_ids: set[str] = set()
        self._closed = False
        self.worker = DialogueWorker(self._emit, max_pending=1)

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def submit_turn(
        self,
        text: str,
        *,
        source: str,
        observed_at: datetime,
    ) -> tuple[bool, str]:
        turn_id = "main-language:" + uuid4().hex
        with self._lock:
            if self._closed:
                return False, turn_id
            epoch = self._epoch
            self.ledger.begin(
                turn_id,
                epoch=epoch,
                raw_text=text,
                semantic_text=text,
            )
            history = self.ledger.prompt_history()
            self._pending_turn_ids.add(turn_id)

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
                defer_reply_history=True,
            ),
        )
        if not accepted:
            with self._lock:
                self._pending_turn_ids.discard(turn_id)
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
        if error or not result:
            self.ledger.routed(turn_id, route="learning", status=error or "empty_result")
            self.ledger.finish_without_reply(turn_id, resolution=error or "empty_result")
            self.dialogue.discard_deferred_reply(turn_id)
            return {"status": "failed", "turn_id": turn_id, "reply": ""}
        status = str(result.get("status") or "unknown")
        route = self._route_for_result(result)
        self.ledger.routed(turn_id, route=route, status=status)
        if route == "none":
            self.foreground.clear()
        else:
            self.foreground.activate(route, now=observed_at)
        reply = str(result.get("reply") or "").strip()
        if not reply:
            self.ledger.finish_without_reply(turn_id, resolution=status)
            self.dialogue.discard_deferred_reply(turn_id)
            return {**result, "turn_id": turn_id, "reply": "", "foreground_route": route}
        speech = result.get("speech") if isinstance(result.get("speech"), dict) else {}
        utterance_id = str(speech.get("utterance_id") or ("main-dialogue:" + uuid4().hex))
        self.ledger.select_reply(turn_id, reply=reply, utterance_id=utterance_id)
        return {
            **result,
            "turn_id": turn_id,
            "reply": reply,
            "utterance_id": utterance_id,
            "foreground_route": route,
        }

    def playback_event(self, event: dict[str, str], *, observed_at: datetime) -> None:
        utterance_id = event.get("utterance_id", "")
        status = event.get("status", "")
        if status == "queued":
            self.ledger.queued(utterance_id)
            return
        if status == "started":
            self.ledger.started(utterance_id)
            return
        if status not in {"completed", "failed", "cancelled"}:
            return
        row = self.ledger.resolve(
            utterance_id,
            status,
            resolution=event.get("resolution", ""),
        )
        if row is None:
            return
        turn_id = str(row.get("turn_id") or "")
        reply = str(row.get("selected_reply") or "")
        if status == "completed":
            self.dialogue.confirm_delivered_reply(turn_id, reply)
            self.foreground.note_completed_turn(
                turn_id,
                str(row.get("semantic_text") or ""),
                reply,
                route=str(row.get("route") or "learning"),
            )
            if utterance_id.startswith("web-departure:"):
                self._submit_playback_control(utterance_id, observed_at=observed_at)
        else:
            self.dialogue.discard_deferred_reply(turn_id)

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

    def interrupt_for_combat(self) -> None:
        with self._lock:
            self._epoch += 1
            self._pending_turn_ids.clear()
            self._results.clear()
        self.dialogue.interrupt()
        self.ledger.cancel_pending(resolution="combat")

    def release_after_combat(self) -> None:
        self.dialogue.release()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._epoch += 1
        self.dialogue.cancel()
        self.ledger.cancel_pending(resolution="quit")
        self.worker.close()
        web = getattr(self.dialogue, "web", None)
        client = getattr(web, "client", None)
        if client is not None and callable(getattr(client, "close", None)):
            client.close()

    def _submit_playback_control(self, utterance_id: str, *, observed_at: datetime) -> None:
        del observed_at
        with self._lock:
            if self._closed:
                return
            epoch = self._epoch

        def cancelled() -> bool:
            with self._lock:
                return self._closed or epoch != self._epoch

        self.worker.submit(
            work_id="playback:" + uuid4().hex,
            work_kind="playback_control",
            session_epoch=epoch,
            run=lambda: self.dialogue.on_speech_playback_result(
                utterance_id,
                status="completed",
                event_id="main-playback:" + uuid4().hex,
                cancelled=cancelled,
            ),
        )

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
        if status == "handoff":
            return "none"
        if (
            status.startswith("web_")
            or status in {"awaiting_report", "research_continue", "research_reflection"}
            or result.get("web")
        ):
            return "web"
        if result.get("mode_after") == "language":
            return "learning"
        if result.get("route_owner") in {"player_chat", "host_code"}:
            return "casual"
        return "learning"
