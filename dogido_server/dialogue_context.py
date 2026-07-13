"""player_chat 用の短い対話履歴・出来事ダイジェスト。

- 会話: 直近 5 往復（最大 10 発話）
- 出来事: 状態機械が積んだ粗いメモ（撃破・見たモブ・入手など）
LLM には短い自然文で渡す。生イベントは載せない。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class DialogueUtterance:
    role: str  # "player" | "dogido"
    text: str
    at: datetime | None = None


@dataclass(slots=True)
class DigestNote:
    kind: str  # combat | ambient | loot | other
    text: str
    at: datetime | None = None


@dataclass
class DialogueContext:
    max_utterances: int = 10  # 5 往復
    max_digest_notes: int = 8
    max_text_chars: int = 80
    _utterances: deque[DialogueUtterance] = field(default_factory=lambda: deque(maxlen=10))
    _digest: deque[DigestNote] = field(default_factory=lambda: deque(maxlen=8))

    def __post_init__(self) -> None:
        self._utterances = deque(maxlen=self.max_utterances)
        self._digest = deque(maxlen=self.max_digest_notes)

    def add_player(self, text: str, at: datetime | None = None) -> None:
        cleaned = self._clip(text)
        if not cleaned:
            return
        self._utterances.append(DialogueUtterance(role="player", text=cleaned, at=at))

    def add_dogido(self, text: str, at: datetime | None = None) -> None:
        cleaned = self._clip(text)
        if not cleaned:
            return
        # cue 用の短い擬音だけは履歴に残さない
        if cleaned in {"ハッ", "ハァハァ……", "ハァハァ"}:
            return
        self._utterances.append(DialogueUtterance(role="dogido", text=cleaned, at=at))

    def add_digest(self, kind: str, text: str, at: datetime | None = None) -> None:
        cleaned = self._clip(text, limit=60)
        if not cleaned:
            return
        # 直前と全く同じメモは重ねない
        if self._digest and self._digest[-1].text == cleaned:
            return
        self._digest.append(DigestNote(kind=kind, text=cleaned, at=at))

    def extend_digest(self, notes: list[str], *, kind: str = "other", at: datetime | None = None) -> None:
        for note in notes:
            self.add_digest(kind, note, at=at)

    def conversation_lines(self) -> list[str]:
        lines: list[str] = []
        for item in self._utterances:
            prefix = "プレイヤー" if item.role == "player" else "ドギド"
            lines.append(f"{prefix}: {item.text}")
        return lines

    def digest_lines(self) -> list[str]:
        return [f"- {note.text}" for note in self._digest]

    def prompt_blocks(self) -> dict[str, str]:
        conversation = self.conversation_lines()
        digest = self.digest_lines()
        return {
            "conversation_history": "\n".join(conversation) if conversation else "",
            "event_digest": "\n".join(digest) if digest else "",
        }

    def _clip(self, text: str, limit: int | None = None) -> str:
        cleaned = " ".join((text or "").replace("\n", " ").split())
        if not cleaned:
            return ""
        cap = limit if limit is not None else self.max_text_chars
        if len(cleaned) <= cap:
            return cleaned
        return cleaned[: cap - 1] + "…"
