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
    turn_id: str = ""


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
    _danger_retained: tuple[DialogueUtterance, ...] = ()
    _danger_active: bool = False
    _post_danger_player_turns_remaining: int = 0
    _prompt_overlay: str = ""

    def __post_init__(self) -> None:
        self._utterances = deque(maxlen=self.max_utterances)
        self._digest = deque(maxlen=self.max_digest_notes)

    def add_player(
        self,
        text: str,
        at: datetime | None = None,
        *,
        turn_id: str = "",
    ) -> bool:
        cleaned = self._clip(text)
        if not cleaned:
            return False
        clean_turn_id = self._clip_id(turn_id)
        if clean_turn_id and any(
            row.role == "player" and row.turn_id == clean_turn_id
            for row in self._utterances
        ):
            return False
        self._utterances.append(
            DialogueUtterance(
                role="player",
                text=cleaned,
                at=at,
                turn_id=clean_turn_id,
            )
        )
        if not self._danger_active and self._post_danger_player_turns_remaining > 0:
            self._post_danger_player_turns_remaining -= 1
            if self._post_danger_player_turns_remaining <= 0:
                self._danger_retained = ()
        return True

    def add_dogido(
        self,
        text: str,
        at: datetime | None = None,
        *,
        turn_id: str = "",
    ) -> bool:
        cleaned = self._clip(text)
        if not cleaned:
            return False
        # cue 用の短い擬音だけは履歴に残さない
        if cleaned in {"ハッ", "ハァハァ……", "ハァハァ"}:
            return False
        clean_turn_id = self._clip_id(turn_id)
        if clean_turn_id and any(
            row.role == "dogido" and row.turn_id == clean_turn_id
            for row in self._utterances
        ):
            return False
        self._utterances.append(
            DialogueUtterance(
                role="dogido",
                text=cleaned,
                at=at,
                turn_id=clean_turn_id,
            )
        )
        return True

    def begin_danger_retention(self) -> None:
        """危険前の通常5往復を、戦況発話で押し出されないよう一度だけ保持する。"""

        if not self._danger_active:
            # 前の危険エピソードの叫声代替メモを、次の無関係な
            # 戦闘へ誤って持ち越さない。通常の出来事メモは維持する。
            self._digest = deque(
                (note for note in self._digest if note.kind != "situation"),
                maxlen=self.max_digest_notes,
            )
            if not self._danger_retained:
                self._danger_retained = tuple(self._utterances)
        self._danger_active = True
        self._post_danger_player_turns_remaining = 0

    def end_danger_retention(self, *, player_turns: int = 3) -> None:
        if not self._danger_active:
            return
        self._danger_active = False
        self._post_danger_player_turns_remaining = max(0, int(player_turns))
        if self._post_danger_player_turns_remaining == 0:
            self._danger_retained = ()

    def set_prompt_overlay(self, text: str) -> None:
        """現在の一回の生成だけに渡す、保留話題などのコード由来メモ。"""

        self._prompt_overlay = self._clip(text, limit=160)

    def clear_prompt_overlay(self) -> None:
        self._prompt_overlay = ""

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
        for item in self._prompt_utterances():
            prefix = "プレイヤー" if item.role == "player" else "ドギド"
            lines.append(f"{prefix}: {item.text}")
        return lines

    def prompt_turns(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for index, item in enumerate(self._prompt_utterances()):
            role = "user" if item.role == "player" else "assistant"
            turn_id = item.turn_id or f"dialogue-context:{index}:{role}"
            if role == "assistant" and not turn_id.endswith(":reply"):
                turn_id += ":reply"
            row = {"turn_id": turn_id, "role": role, "text": item.text}
            if item.at is not None:
                row["observed_at"] = item.at.isoformat()
            rows.append(row)
        return rows

    def digest_lines(self) -> list[str]:
        lines = [f"- {note.text}" for note in self._digest]
        if self._prompt_overlay:
            lines.append(f"- {self._prompt_overlay}")
        return lines

    def situation_lines(self) -> list[str]:
        """叫声の代替として作ったコード観測メモだけを限定対話へ渡す。"""

        if not self._danger_active and self._post_danger_player_turns_remaining <= 0:
            return []
        return [note.text for note in self._digest if note.kind == "situation"]

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

    @staticmethod
    def _clip_id(value: str) -> str:
        return " ".join((value or "").replace("\n", " ").split())[:180]

    def _prompt_utterances(self) -> list[DialogueUtterance]:
        """危険前保持と現在履歴をturn IDで重ねず投影する。"""

        rows = [*self._danger_retained, *self._utterances]
        selected: list[DialogueUtterance] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            # turn ID導入前の行は、同じ発言の繰り返しを潰さず、retainedと
            # 現dequeが共有している同一オブジェクトだけを重複除去する。
            legacy_key = f"legacy-object:{id(row)}"
            key = (row.role, row.turn_id or legacy_key)
            if key in seen:
                continue
            seen.add(key)
            selected.append(row)
        return selected
