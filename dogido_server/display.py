"""ゲーム外の読み取り専用画面へ、確定した発言をミラーする。

会話記憶や評価ログとは別の、プロセス内だけの上限付き履歴である。
座標、体力、内部プロンプトは保存しない。音声再生の完了記録でもなく、
状態機械とserviceが発言として確定した本文と、その返答対象の入力を残す。
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sys
from threading import RLock
from uuid import uuid4

from dogido_server.state_machine.types import AudioAction, SpeechReference


DISPLAY_SCHEMA_VERSION = 1


def _source_label(source_root: Path) -> tuple[str, str]:
    """絶対パスを公開せず、正本とCodex作業ツリーを見分ける。"""

    parts = source_root.parts
    try:
        worktrees_index = parts.index("worktrees")
    except ValueError:
        return "checkout", "通常チェックアウト"
    if worktrees_index > 0 and parts[worktrees_index - 1] == ".codex":
        worktree_name = (
            parts[worktrees_index + 1]
            if worktrees_index + 1 < len(parts)
            else "不明"
        )
        return "codex_worktree", f"Codex作業ツリー {worktree_name}"
    return "checkout", "通常チェックアウト"


@dataclass(frozen=True, slots=True)
class _AdapterPresence:
    adapter_name: str
    adapter_version: str
    last_seen_at: datetime


class RuntimeStatus:
    """画面から起動元とMinecraft接続を区別できる、スレッド安全な状態。"""

    def __init__(
        self,
        *,
        heartbeat_interval_ms: int,
        source_root: Path | None = None,
        python_prefix: Path | None = None,
        python_base_prefix: Path | None = None,
    ) -> None:
        resolved_root = source_root or Path(__file__).resolve().parents[1]
        resolved_prefix = python_prefix or Path(sys.prefix)
        resolved_base_prefix = python_base_prefix or Path(sys.base_prefix)
        source_kind, source_label_ja = _source_label(resolved_root)
        self._runtime = {
            "instance_id": f"run_{uuid4().hex[:12]}",
            "started_at": datetime.now().astimezone().isoformat(),
            "source_kind": source_kind,
            "source_label_ja": source_label_ja,
            "python_environment": resolved_prefix.name,
            "virtual_environment": resolved_prefix != resolved_base_prefix,
            "process_id": os.getpid(),
        }
        # heartbeat 3回分、ただし最低15秒までは接続中として扱う。
        self._freshness = timedelta(
            seconds=max(15.0, (max(1, heartbeat_interval_ms) * 3) / 1000.0)
        )
        self._sessions: dict[str, _AdapterPresence] = {}
        self._revision = 0
        self._lock = RLock()

    def adapter_seen(
        self,
        session_id: str,
        *,
        adapter_name: str,
        adapter_version: str,
        seen_at: datetime | None = None,
    ) -> None:
        with self._lock:
            self._sessions[session_id] = _AdapterPresence(
                adapter_name=adapter_name,
                adapter_version=adapter_version,
                last_seen_at=seen_at or datetime.now().astimezone(),
            )
            self._revision += 1

    def adapter_closed(self, session_id: str) -> None:
        with self._lock:
            if self._sessions.pop(session_id, None) is not None:
                self._revision += 1

    def snapshot(self, *, now: datetime | None = None) -> dict[str, object]:
        current = now or datetime.now().astimezone()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        with self._lock:
            presences = tuple(self._sessions.values())
            fresh = tuple(
                presence
                for presence in presences
                if current - presence.last_seen_at <= self._freshness
            )
            latest = max(
                (presence.last_seen_at for presence in presences),
                default=None,
            )
            adapters = sorted(
                {
                    f"{presence.adapter_name} {presence.adapter_version}".strip()
                    for presence in fresh
                }
            )
            if fresh:
                state = "connected"
            elif presences:
                state = "stale"
            else:
                state = "not_connected"
            return {
                "revision": self._revision,
                "runtime": dict(self._runtime),
                "minecraft": {
                    "state": state,
                    "connected": bool(fresh),
                    "active_sessions": len(fresh),
                    "registered_sessions": len(presences),
                    "adapters": adapters,
                    "last_seen_at": latest.isoformat() if latest is not None else None,
                    "freshness_seconds": int(self._freshness.total_seconds()),
                },
            }


@dataclass(frozen=True, slots=True)
class DisplayUtterance:
    utterance_id: str
    session_id: str | None
    category: str
    text: str
    created_at: str
    reference_ids: tuple[str, ...]
    output_mode: str
    player_input_text: str

    def as_dict(self) -> dict[str, object]:
        return {
            "utterance_id": self.utterance_id,
            "session_id": self.session_id,
            "category": self.category,
            "text": self.text,
            "created_at": self.created_at,
            "reference_ids": list(self.reference_ids),
            "output_mode": self.output_mode,
            "player_input_text": self.player_input_text,
        }


@dataclass(slots=True)
class DisplayReference:
    reference_id: str
    source_id: str
    title_ja: str
    citation_label_ja: str
    locator: str
    url: str
    source_kind: str
    first_seen_at: str
    last_seen_at: str
    utterance_ids: list[str]

    def as_dict(self, *, visible_utterance_ids: set[str]) -> dict[str, object]:
        return {
            "reference_id": self.reference_id,
            "source_id": self.source_id,
            "title_ja": self.title_ja,
            "citation_label_ja": self.citation_label_ja,
            "locator": self.locator,
            "url": self.url,
            "source_kind": self.source_kind,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "utterance_ids": [
                utterance_id
                for utterance_id in self.utterance_ids
                if utterance_id in visible_utterance_ids
            ],
        }


class DisplayHistory:
    """発言本文と参考資料を保持する、スレッド安全な上限付き履歴。"""

    def __init__(self, *, max_entries: int = 200) -> None:
        self.max_entries = max(1, int(max_entries))
        self._utterances: deque[DisplayUtterance] = deque(maxlen=self.max_entries)
        # 一発言が複数資料を持てるため、資料側は発言数の3倍を上限とする。
        self._reference_limit = self.max_entries * 3
        self._references: OrderedDict[str, DisplayReference] = OrderedDict()
        self._revision = 0
        self._lock = RLock()

    @staticmethod
    def _reference_id(reference: SpeechReference) -> str:
        canonical = "\0".join(
            (
                reference.source_id,
                reference.locator,
                reference.url,
                reference.title_ja,
            )
        )
        digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:20]
        return f"ref_{digest}"

    @staticmethod
    def _category(action: AudioAction) -> str:
        if action.references:
            return "knowledge"
        if action.speech_profile == "haiku":
            return "haiku"
        if action.layer in {"callout", "panic_cue"}:
            return "warning"
        return "speech"

    def record_actions(
        self,
        actions: list[AudioAction],
        *,
        session_id: str | None,
        audio_requested: bool,
    ) -> tuple[str, ...]:
        """本文のあるactionだけを一発言ずつ記録する。"""

        recorded_ids: list[str] = []
        with self._lock:
            for action in actions:
                if action.text is None or not action.text.strip():
                    continue
                now = datetime.now().astimezone().isoformat()
                utterance_id = f"utt_{uuid4().hex[:20]}"
                reference_ids: list[str] = []
                for reference in action.references:
                    reference_id = self._reference_id(reference)
                    if reference_id not in reference_ids:
                        reference_ids.append(reference_id)
                    existing = self._references.get(reference_id)
                    if existing is None:
                        existing = DisplayReference(
                            reference_id=reference_id,
                            source_id=reference.source_id,
                            title_ja=reference.title_ja,
                            citation_label_ja=reference.citation_label_ja,
                            locator=reference.locator,
                            url=reference.url,
                            source_kind=reference.source_kind,
                            first_seen_at=now,
                            last_seen_at=now,
                            utterance_ids=[],
                        )
                        self._references[reference_id] = existing
                    else:
                        existing.last_seen_at = now
                        self._references.move_to_end(reference_id)
                    if utterance_id not in existing.utterance_ids:
                        existing.utterance_ids.append(utterance_id)
                        if len(existing.utterance_ids) > self.max_entries:
                            del existing.utterance_ids[:-self.max_entries]

                utterance = DisplayUtterance(
                    utterance_id=utterance_id,
                    session_id=session_id,
                    category=self._category(action),
                    text=action.text,
                    created_at=now,
                    reference_ids=tuple(reference_ids),
                    output_mode="audio_and_text" if audio_requested else "text_only",
                    player_input_text=action.display_player_input_text,
                )
                self._utterances.append(utterance)
                recorded_ids.append(utterance_id)
                self._revision += 1

                while len(self._references) > self._reference_limit:
                    self._references.popitem(last=False)
        return tuple(recorded_ids)

    def snapshot(self, *, session_id: str | None = None) -> dict[str, object]:
        """現在の履歴をJSON化する。取得はLLM/service直列workerを待たない。"""

        with self._lock:
            utterances = [
                utterance
                for utterance in self._utterances
                if session_id is None or utterance.session_id == session_id
            ]
            visible_utterance_ids = {
                utterance.utterance_id for utterance in utterances
            }
            visible_reference_ids = {
                reference_id
                for utterance in utterances
                for reference_id in utterance.reference_ids
            }
            references = [
                reference.as_dict(visible_utterance_ids=visible_utterance_ids)
                for reference_id, reference in reversed(self._references.items())
                if reference_id in visible_reference_ids
            ]
            return {
                "schema_version": DISPLAY_SCHEMA_VERSION,
                "revision": self._revision,
                "generated_at": datetime.now().astimezone().isoformat(),
                "session_id": session_id,
                "utterances": [utterance.as_dict() for utterance in utterances],
                "references": references,
                "retention": {
                    "storage": "process_memory",
                    "max_utterances": self.max_entries,
                    "cleared_on_restart": True,
                },
            }


__all__ = [
    "DISPLAY_SCHEMA_VERSION",
    "DisplayHistory",
    "DisplayReference",
    "DisplayUtterance",
    "RuntimeStatus",
]
