"""独立音声試験の短い会話ターン正本と、直列の生成ワーカー。"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from queue import Empty, Full, Queue
import threading
import time
from typing import Callable


FINAL_PLAYBACK_STATUSES = {"completed", "failed", "cancelled"}
PENDING_PLAYBACK_STATUSES = {
    "not_selected",
    "selected",
    "dispatched",
    "queued",
    "started",
}
GENERATION_PLAYBACK_STATUSES = {"not_selected", "selected"}


@dataclass(slots=True)
class ConversationTurn:
    turn_id: str
    epoch: int
    raw_text: str
    semantic_text: str
    source: str = "text"
    route: str = "pending"
    routing_status: str = "pending"
    selected_reply: str = ""
    utterance_id: str = ""
    playback_status: str = "not_selected"
    accepted_at: float = 0.0
    completed_at: float | None = None
    resolution: str = ""

    def snapshot(self) -> dict:
        return asdict(self)


class TurnLedger:
    """実際に受理した入力と、再生結果までを対応付ける短期台帳。

    prompt_history() に載る assistant 発話は completed だけ。受理済みのuser発話は
    TTS失敗時にも残すが、明示cancel/interruptで閉じたターンは次へ持ち越さない。
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = 300,
        max_turns: int = 64,
        max_exchanges: int = 5,
        max_prompt_chars: int = 80,
    ):
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self.max_exchanges = max_exchanges
        self.max_prompt_chars = max_prompt_chars
        self._turns: deque[ConversationTurn] = deque()
        self._by_turn: dict[str, ConversationTurn] = {}
        self._by_utterance: dict[str, ConversationTurn] = {}
        self._lock = threading.RLock()

    def begin(
        self,
        turn_id: str,
        *,
        epoch: int,
        raw_text: str,
        semantic_text: str,
        source: str = "text",
    ) -> dict:
        with self._lock:
            if turn_id in self._by_turn:
                return self._by_turn[turn_id].snapshot()
            turn = ConversationTurn(
                turn_id=turn_id,
                epoch=epoch,
                raw_text=raw_text[:1000],
                semantic_text=semantic_text[:1000],
                source="voice" if source == "voice" else "text",
                accepted_at=self.clock(),
            )
            self._turns.append(turn)
            self._by_turn[turn_id] = turn
            self._trim()
            return turn.snapshot()

    def routed(self, turn_id: str, *, route: str, status: str) -> dict | None:
        with self._lock:
            turn = self._by_turn.get(turn_id)
            if turn is None:
                return None
            turn.route = route[:80] or "unknown"
            turn.routing_status = status[:80] or "unknown"
            return turn.snapshot()

    def select_reply(self, turn_id: str, *, reply: str, utterance_id: str) -> dict | None:
        with self._lock:
            turn = self._by_turn.get(turn_id)
            if turn is None:
                return None
            turn.selected_reply = reply[:1000]
            turn.utterance_id = utterance_id[:180]
            turn.playback_status = "selected"
            turn.completed_at = None
            self._by_utterance[turn.utterance_id] = turn
            return turn.snapshot()

    def queued(self, utterance_id: str) -> dict | None:
        return self._playback_transition(utterance_id, "queued")

    def started(self, utterance_id: str) -> dict | None:
        return self._playback_transition(utterance_id, "started")

    def dispatched(self, utterance_id: str) -> dict | None:
        """dispatcherへ所有権を渡した事実を、callbackより先に記録する。"""

        return self._playback_transition(utterance_id, "dispatched")

    def resolve(self, utterance_id: str, status: str, *, resolution: str = "") -> dict | None:
        if status not in FINAL_PLAYBACK_STATUSES:
            raise ValueError("再生結果は completed/failed/cancelled のいずれか")
        with self._lock:
            turn = self._by_utterance.get(utterance_id)
            if turn is None or turn.playback_status in FINAL_PLAYBACK_STATUSES:
                return None
            turn.playback_status = status
            turn.completed_at = self.clock()
            turn.resolution = resolution[:120]
            return turn.snapshot()

    def finish_without_reply(self, turn_id: str, *, resolution: str = "") -> dict | None:
        with self._lock:
            turn = self._by_turn.get(turn_id)
            if turn is None:
                return None
            turn.playback_status = "not_applicable"
            turn.completed_at = self.clock()
            turn.resolution = resolution[:120]
            return turn.snapshot()

    def cancel_pending(self, *, resolution: str) -> list[dict]:
        """未生成・未配送のturnだけを失効する。

        ``dispatched`` 以降は実際のdispatcher terminalを正とする。生成epochの
        変更だけで、再生中の発話を先回りしてcancelledにしない。
        """

        changed: list[dict] = []
        with self._lock:
            for turn in self._turns:
                if turn.playback_status not in GENERATION_PLAYBACK_STATUSES:
                    continue
                turn.playback_status = "cancelled"
                turn.completed_at = self.clock()
                turn.resolution = resolution[:120]
                changed.append(turn.snapshot())
        return changed

    def cancel_turn(self, turn_id: str, *, resolution: str) -> dict | None:
        """hostがまだdispatcherへ渡していない1件だけを明示終了する。"""

        with self._lock:
            turn = self._by_turn.get(turn_id)
            if turn is None or turn.playback_status not in GENERATION_PLAYBACK_STATUSES:
                return None
            turn.playback_status = "cancelled"
            turn.completed_at = self.clock()
            turn.resolution = resolution[:120]
            return turn.snapshot()

    def get(self, turn_id: str) -> dict | None:
        with self._lock:
            turn = self._by_turn.get(turn_id)
            return turn.snapshot() if turn is not None else None

    def prompt_turns(self, *, now: float | None = None) -> list[dict[str, str]]:
        """解釈器と本文生成が共有する、ID付きの短期履歴を返す。"""

        with self._lock:
            cutoff_now = self.clock() if now is None else now
            eligible = [
                turn
                for turn in self._turns
                if turn.completed_at is not None
                and turn.playback_status in FINAL_PLAYBACK_STATUSES | {"not_applicable"}
                and cutoff_now - turn.completed_at < self.ttl_seconds
                and turn.routing_status not in {"awaiting_address", "expired"}
                and turn.route not in {"address_repair", "address_confirmation"}
                and turn.resolution
                not in {
                    "cancel",
                    "interrupt",
                    "listen",
                    "quit",
                    "combat",
                    "expired_unaddressed",
                    "replaced_unaddressed",
                    "declined_unaddressed",
                    "attention_interrupted",
                    "host_chat_queue_full",
                    "worker_busy",
                }
            ][-self.max_exchanges :]
            rows: list[dict[str, str]] = []
            for turn in eligible:
                user = self._clip(turn.semantic_text)
                assistant = self._clip(turn.selected_reply)
                if user:
                    rows.append(
                        {
                            "turn_id": turn.turn_id,
                            "role": "user",
                            "text": user,
                            "source": turn.source,
                        }
                    )
                if turn.playback_status == "completed" and assistant:
                    rows.append(
                        {
                            "turn_id": f"{turn.turn_id}:reply",
                            "role": "assistant",
                            "text": assistant,
                        }
                    )
            return rows

    def prompt_history(self, *, now: float | None = None) -> str:
        lines: list[str] = []
        for row in self.prompt_turns(now=now):
            speaker = "プレイヤー" if row["role"] == "user" else "ドギド"
            lines.append(f"{speaker}: {row['text']}")
        return "\n".join(lines)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [turn.snapshot() for turn in self._turns]

    def _playback_transition(self, utterance_id: str, status: str) -> dict | None:
        with self._lock:
            turn = self._by_utterance.get(utterance_id)
            if turn is None or turn.playback_status in FINAL_PLAYBACK_STATUSES:
                return None
            order = {"selected": 0, "dispatched": 1, "queued": 2, "started": 3}
            if order.get(status, -1) <= order.get(turn.playback_status, -1):
                return turn.snapshot()
            turn.playback_status = status
            return turn.snapshot()

    def _clip(self, text: str) -> str:
        cleaned = " ".join((text or "").replace("\n", " ").split())
        if len(cleaned) <= self.max_prompt_chars:
            return cleaned
        return cleaned[: self.max_prompt_chars - 1] + "…"

    def _trim(self) -> None:
        while len(self._turns) > self.max_turns:
            old = self._turns.popleft()
            self._by_turn.pop(old.turn_id, None)
            if old.utterance_id:
                self._by_utterance.pop(old.utterance_id, None)


@dataclass(slots=True)
class _WorkItem:
    work_id: str
    work_kind: str
    session_epoch: int
    turn_id: str
    run: Callable[[], dict]


class DialogueWorker:
    """独立試験と本体で共有する有界・直列worker。完了結果だけをhostへ戻す。"""

    def __init__(self, emit: Callable[[dict], None], *, max_pending: int = 1):
        self.emit = emit
        self.items: Queue[_WorkItem | None] = Queue(maxsize=max_pending)
        self.closed = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="language-dialogue-worker",
            daemon=True,
        )
        self.thread.start()

    def submit(
        self,
        *,
        work_id: str,
        work_kind: str,
        session_epoch: int,
        turn_id: str = "",
        run: Callable[[], dict],
    ) -> bool:
        if self.closed.is_set():
            return False
        try:
            self.items.put_nowait(_WorkItem(work_id, work_kind, session_epoch, turn_id, run))
            return True
        except Full:
            return False

    def close(self, *, timeout: float = 2) -> bool:
        if not self.closed.is_set():
            self.closed.set()
            while True:
                try:
                    queued = self.items.get_nowait()
                except Empty:
                    break
                if queued is not None:
                    self.items.task_done()
            try:
                self.items.put_nowait(None)
            except Full:
                pass
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()

    def _run(self) -> None:
        while True:
            try:
                item = self.items.get(timeout=.2)
            except Empty:
                if self.closed.is_set():
                    return
                continue
            if item is None:
                return
            result, error = {}, ""
            try:
                result = item.run()
            except Exception as exc:  # hostへ型だけ返す。本文や秘密を例外文字列へ出さない。
                error = type(exc).__name__
            if not self.closed.is_set():
                self.emit({
                    "kind": "dialogue_work_result",
                    "work_id": item.work_id,
                    "work_kind": item.work_kind,
                    "session_epoch": item.session_epoch,
                    "turn_id": item.turn_id,
                    "result": result,
                    "error": error,
                })
            self.items.task_done()
