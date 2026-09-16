"""発話内根拠に限定した会話修復（third-position repair）。

モデルは訂正先と本人の言い直しを指すだけ。原文・世界観測・操作は変更しない。
修復メモは既存の短期会話のplayer行に付属し、その行と一緒に寿命を終える。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


REPAIR_ACTIONS = frozenset({"repair_conversation", "clarify_repair"})
REPAIR_FIELDS = (
    "repair_action", "repair_target_turn_id", "repair_target_quote",
    "repair_signal_quote", "repair_replacement_quote",
)
# 意味分類はplannerが行う。コードは、曖昧な否定一語や単なるASRの音近傍から
# 新しい意味を確定させないため、本人の修復開始の発話根拠を要求する。
_SIGNAL = re.compile(
    r"違う|ちがう|ちゃう|じゃなく|ではなく|そうじゃない|そうやない|"
    r"そういう意味じゃない|そういう意味ではない|"
    r"言い間違|言いまちが|聞き間違|聞きまちが|聞き違|勘違い|"
    r"訂正|言い直|いいなお|のこと(?:だ|です|や|を言|って)|"
    r"(?:って|という)意味"
)
_BARE = re.compile(
    r"^(?:いや[、,\s]*)?(?:違う|ちがう|ちゃう|そうじゃない|そうやない|"
    r"そういう意味じゃない|そういう意味ではない)(?:よ|で|ね|です|んだ|って)*[。！!？?\s]*$"
)
_QUOTED = re.compile(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"]*"')


def has_repair_signal(raw: str) -> bool:
    """引用の外に修復開始の根拠がある場合だけ、限定抽出を許可する。"""
    return bool(_SIGNAL.search(_QUOTED.sub("", raw)))


class ConversationRepairPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_turn_id: str = Field(min_length=1, max_length=180)
    target_quote: str = Field(min_length=1, max_length=160)
    signal_quote: str = Field(max_length=160)
    replacement_quote: str = Field(max_length=160)


@dataclass(frozen=True, slots=True)
class ConversationRepair:
    action: Literal["repair_conversation", "clarify_repair"]
    target_turn_id: str
    target_quote: str
    signal_quote: str
    replacement_quote: str
    current_text: str

    def prompt_fields(self) -> dict[str, str]:
        return {
            "repair_action": self.action,
            "repair_target_turn_id": self.target_turn_id,
            "repair_target_quote": self.target_quote,
            "repair_signal_quote": self.signal_quote,
            "repair_replacement_quote": self.replacement_quote,
        }


def pending_repair(history: list[dict]) -> dict[str, str]:
    """最後に実再生された返答が、未解決修復の質問だった場合だけ引き継ぐ。"""
    if len(history) < 2:
        return {}
    player, reply = history[-2:]
    if not isinstance(player, dict) or not isinstance(reply, dict):
        return {}
    if (
        player.get("role") != "user"
        or reply.get("role") != "assistant"
        or reply.get("turn_id") != str(player.get("turn_id", "")) + ":reply"
        or player.get("repair_action") != "clarify_repair"
    ):
        return {}
    return {key: str(player.get(key) or "") for key in REPAIR_FIELDS}


def parse_conversation_repair(
    action: str, value: object, details: dict,
) -> ConversationRepair | None:
    """schemaとconsumerが共有する、引用・対象・原文の検証。"""
    if action not in REPAIR_ACTIONS or not isinstance(value, dict):
        return None
    try:
        payload = ConversationRepairPayload.model_validate(value)
    except ValueError:
        return None
    history = details.get("history")
    if not isinstance(history, list):
        return None
    current = details.get("current") or {}
    if not isinstance(current, dict):
        return None
    raw = str(current.get("raw_text", current.get("text", "")))
    target = next((row for row in history if isinstance(row, dict)
                   and row.get("turn_id") == payload.target_turn_id), None)
    if (
        target is None or payload.target_turn_id == "current"
        or target.get("role") not in {"user", "assistant"}
        or payload.target_quote not in str(target.get("text") or "")
    ):
        return None
    pending = pending_repair(history)
    is_followup = (
        pending.get("repair_target_turn_id") == payload.target_turn_id
        and pending.get("repair_target_quote") == payload.target_quote
    )
    if payload.signal_quote:
        # 「『違う』って言うキャラが好き」など、引用された訂正語だけでは
        # 本人の修復開始にならない。引用外の「じゃなく」等は通常どおり受ける。
        unquoted = _QUOTED.sub(lambda match: " " * len(match.group()), raw)
        if not any(
            _SIGNAL.search(unquoted[match.start():match.end()])
            for match in re.finditer(re.escape(payload.signal_quote), raw)
        ):
            return None
    elif not is_followup:
        return None
    if action == "repair_conversation":
        replacement = payload.replacement_quote
        if (
            not replacement.strip() or replacement not in raw
            or _BARE.fullmatch(replacement.strip())
            or replacement.strip("。！!？? ") in {"うん", "そう", "はい", "いいえ"}
        ):
            return None
    elif payload.replacement_quote:
        return None
    return ConversationRepair(
        action=action, target_turn_id=payload.target_turn_id,
        target_quote=payload.target_quote, signal_quote=payload.signal_quote,
        replacement_quote=payload.replacement_quote, current_text=raw,
    )


def repair_fallback(repair: ConversationRepair) -> str:
    if repair.action == "clarify_repair":
        # 不確かな語の候補を補作せず、対象の発言を指して本人の説明を待つ。
        return f"「{repair.target_quote[:40]}」のところ、どういう意味やった？"
    return "あ、そういうことやったんやな。取り違えてごめんな。"


def repair_note(fields: dict[str, str]) -> str:
    """保存した発話の書換えではなく、本人の訂正を添える読み取り用投影。"""
    target = fields.get("repair_target_quote", "")
    if fields.get("repair_action") == "repair_conversation":
        return f"本人による会話の訂正:「{target}」への言い直しは「{fields.get('repair_replacement_quote', '')}」。世界観測ではない。"
    return f"会話の修復待ち:「{target}」への異議。正しい意味はまだ未確定。"
