"""プレイヤー主体の会話ルートと、一時中断した話題の小さな正本。

本文生成や意図分類は行わない。状態機械へ渡すのは、現在の所有者・
抑止条件・再生完了済み発話から作る短い川柳材料だけ。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
from typing import Literal


ForegroundRoute = Literal["none", "casual", "learning", "web", "haiku_workshop"]
_ACTIVE_ROUTES = {"casual", "learning", "web", "haiku_workshop"}
_HAIKU_BLOCKING_ROUTES = {"learning", "web"}

COMBAT_CHAT_ACK = "プレイヤー、余裕やな……。オレ、今ちょっと心の余裕ないわ……！"
COMBAT_CHAT_AFTERMATH = "さっきの話、何やったん？ オレ、戦うのでいっぱいいっぱいで覚えてへんねん……。"
COMBAT_FOCUSED_AFTERMATH = "さっきの話、まだ続きあるんやったら聞かせてや。"
CASUAL_HAIKU_PREFACE = "あっ……ちょっと待って。なんか、浮かんできたかもしれん……。"


@dataclass(frozen=True, slots=True)
class CompletedDialogueTurn:
    turn_id: str
    player_text: str
    dogido_text: str
    route: ForegroundRoute


@dataclass(slots=True)
class SuspendedTopic:
    route: ForegroundRoute
    summary: str
    motifs: tuple[str, ...]
    source_turn_ids: tuple[str, ...]
    remaining_player_turns: int = 10
    reason: str = "combat"

    def snapshot(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class ForegroundDialogue:
    """session内だけの会話所有権。永続記憶やLLM判断の権限は持たない。"""

    route: ForegroundRoute = "none"
    started_at: datetime | None = None
    last_player_at: datetime | None = None
    last_conversation_at: datetime | None = None
    completed_turns: list[CompletedDialogueTurn] = field(default_factory=list)
    suspended: SuspendedTopic | None = None
    combat_active: bool = False
    combat_chat_attempted: bool = False
    last_combat_chat_ack_at: datetime | None = None
    last_interrupt_reason: str = ""

    def activate(
        self,
        route: ForegroundRoute,
        *,
        now: datetime,
        player_text: str = "",
    ) -> None:
        if route not in _ACTIVE_ROUTES:
            raise ValueError("active route is required")
        if self.route != route:
            self.route = route
            self.started_at = now
            if route == "haiku_workshop":
                self.completed_turns.clear()
        if player_text.strip():
            self.last_player_at = now
            self.last_conversation_at = now

    def clear(self) -> None:
        self.route = "none"
        self.started_at = None
        self.last_player_at = None
        self.completed_turns.clear()

    def expire_if_idle(self, now: datetime, *, ttl_ms: int = 300000) -> bool:
        if self.route == "none" or self.last_player_at is None:
            return False
        if (now - self.last_player_at).total_seconds() * 1000 < ttl_ms:
            return False
        self.clear()
        return True

    def note_completed_turn(
        self,
        turn_id: str,
        player_text: str,
        dogido_text: str,
        *,
        route: ForegroundRoute,
        at: datetime | None = None,
    ) -> None:
        turn_id = _clean(turn_id, 160)
        player_text = _clean(player_text, 160)
        dogido_text = _clean(dogido_text, 160)
        if (
            not turn_id
            or not player_text
            or not dogido_text
            or route not in {"casual", "learning"}
        ):
            return
        self.completed_turns = [turn for turn in self.completed_turns if turn.turn_id != turn_id]
        self.completed_turns.append(
            CompletedDialogueTurn(turn_id, player_text, dogido_text, route)
        )
        self.completed_turns = self.completed_turns[-5:]
        if at is not None:
            self.last_conversation_at = at

    def suspend(self, reason: str, *, hold_player_turns: int = 10) -> bool:
        self.last_interrupt_reason = _clean(reason, 40)
        if self.route not in {"casual", "learning"}:
            return False
        material = self.haiku_material(route=self.route)
        self.suspended = SuspendedTopic(
            route=self.route,
            summary=str(material.get("summary") or ""),
            motifs=tuple(material.get("motifs") or ()),
            source_turn_ids=tuple(material.get("source_turn_ids") or ()),
            remaining_player_turns=max(1, int(hold_player_turns)),
            reason=self.last_interrupt_reason or "interruption",
        )
        self.route = "none"
        self.started_at = None
        return True

    def suspend_for_combat(self, *, hold_player_turns: int = 10) -> bool:
        self.combat_active = True
        self.combat_chat_attempted = False
        # 各戦闘の最初の話しかけには必ず一度返せるよう、前戦闘の
        # cooldown 起点を持ち越さない。
        self.last_combat_chat_ack_at = None
        return self.suspend("combat", hold_player_turns=hold_player_turns)

    def note_combat_chat_attempt(self, now: datetime, *, cooldown_ms: int = 30000) -> str:
        self.combat_chat_attempted = True
        if self.last_combat_chat_ack_at is not None:
            elapsed_ms = (now - self.last_combat_chat_ack_at).total_seconds() * 1000
            if elapsed_ms < cooldown_ms:
                return ""
        self.last_combat_chat_ack_at = now
        return COMBAT_CHAT_ACK

    def finish_combat(self) -> str:
        attempted = self.combat_chat_attempted
        has_combat_suspended_topic = bool(
            self.suspended is not None and self.suspended.reason == "combat"
        )
        self.combat_active = False
        self.combat_chat_attempted = False
        self.last_interrupt_reason = "combat_chat_attempt" if attempted else "combat"
        if attempted:
            return COMBAT_CHAT_AFTERMATH
        return COMBAT_FOCUSED_AFTERMATH if has_combat_suspended_topic else ""

    def consume_suspended_player_turn(self, *, resumes: bool, now: datetime) -> bool:
        """保留話題を再開、または別発話1件ぶんだけTTLを進める。"""

        topic = self.suspended
        if topic is None:
            return False
        if resumes:
            self.route = topic.route
            self.started_at = now
            self.suspended = None
            return True
        topic.remaining_player_turns -= 1
        if topic.remaining_player_turns <= 0:
            self.suspended = None
        return False

    def suspended_prompt(self, text: str) -> str:
        """明示再開の生成前だけ返す、10turn保留話題の短いコード由来メモ。"""

        topic = self.suspended
        if topic is None or not topic.summary or not _looks_like_resume(text):
            return ""
        return f"プレイヤーが明示的に再開した保留話題: {topic.summary}"

    def note_interrupt(self, reason: str) -> None:
        self.last_interrupt_reason = _clean(reason, 40)

    def blocks_ambient(self) -> bool:
        return self.route in _ACTIVE_ROUTES

    def blocks_new_haiku(self) -> bool:
        return self.route in _HAIKU_BLOCKING_ROUTES

    def casual_haiku_material(self) -> dict[str, object]:
        if self.route != "casual":
            return {}
        return self.haiku_material(route="casual")

    def haiku_material(
        self,
        *,
        route: ForegroundRoute | None = None,
    ) -> dict[str, object]:
        """再生完了済みの直近5件から、soft材料を80字以内で作る。"""

        target_route = route or self.route
        turns = [
            turn
            for turn in self.completed_turns
            if target_route not in {"casual", "learning"}
            or turn.route == target_route
        ][-3:]
        if not turns:
            return {}
        # 3件なら概ね40〜60字へ収まる長さ。1件しかない時に水増しはしない。
        player_phrases = [_clean(turn.player_text, 12) for turn in turns]
        player_phrases = [text for text in player_phrases if text]
        if not player_phrases:
            return {}
        joined = "、".join(f"『{text}』" for text in player_phrases)
        summary = _clean(f"プレイヤーと{joined}について話していた", 80)
        motifs: list[str] = []
        for phrase in reversed(player_phrases):
            for candidate in _motif_candidates(phrase):
                if candidate not in motifs:
                    motifs.append(candidate)
                if len(motifs) >= 3:
                    break
            if len(motifs) >= 3:
                break
        return {
            "summary": summary,
            "motifs": motifs,
            "source_turn_ids": [turn.turn_id for turn in turns],
            "attribution": "player_dialogue_soft_material",
        }

    def snapshot(self) -> dict[str, object]:
        return {
            "route": self.route,
            "active": self.route in _ACTIVE_ROUTES,
            "blocks_ambient": self.blocks_ambient(),
            "blocks_new_haiku": self.blocks_new_haiku(),
            "casual_haiku_material": self.casual_haiku_material(),
            "combat_active": self.combat_active,
            "combat_chat_attempted": self.combat_chat_attempted,
            "last_interrupt_reason": self.last_interrupt_reason,
            "last_conversation_at": (
                self.last_conversation_at.isoformat()
                if self.last_conversation_at is not None
                else None
            ),
            "suspended": self.suspended.snapshot() if self.suspended else None,
        }


def _clean(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").replace("\n", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "…"


def _motif_candidates(text: str) -> list[str]:
    pieces = re.split(r"[、。！？!?\s]+", text)
    result: list[str] = []
    for piece in pieces:
        cleaned = _clean(piece.strip("『』「」（）()"), 16)
        if len(cleaned) >= 2:
            result.append(cleaned)
    return result or [_clean(text, 16)]


def _looks_like_resume(text: str) -> bool:
    normalized = " ".join((text or "").replace("\n", " ").split())
    return any(
        marker in normalized
        for marker in (
            "さっきの話",
            "前の話",
            "話の続き",
            "続き話",
            "続きやけど",
            "続きを",
            "戻るけど",
            "戻ろ",
        )
    )
