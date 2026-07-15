"""player_chat の答え方スタンス（状態機械が決める骨格）。

プロンプト長文に方針を積まず、ここで saw / hypothesis / clarify / none を決める。
"""

from __future__ import annotations

from typing import Any, Literal

ReplyStance = Literal["saw", "hypothesis", "clarify", "none"]

_POLICY_LINES: dict[ReplyStance, str] = {
    "saw": (
        "脅威メモの視認を優先してよい。"
        "プレイヤーの話と一致するなら種名を言ってよい。"
        "メモ・話題ヒントに無い種族やNPCは作らない。"
    ),
    "hypothesis": (
        "自分は視認できていない。『見えてへん』はよいが、"
        "『おらへん』『気のせい』でプレイヤーを否定しない。"
        "話題ヒントの候補だけを『かもしれん』と弱く触れてよい。"
        "ヒント外の種族・NPCは禁止。"
    ),
    "clarify": (
        "種名を当てず、色・形・動きなど特徴を聞き返してよい。"
        "カタログに無い名前を作らない。"
        "プレイヤーの『いる／見えてる』を否定しない。"
    ),
    "none": (
        "雑談として自然に返す。"
        "根拠のない種名・敵・音の捏造はしない。"
    ),
}

# 「あれ何？」系で topic も視認も無いときの clarify ヒント（粗い）
_CLARIFY_HINTS = (
    "何",
    "なに",
    "なんや",
    "だれ",
    "誰",
    "あいつ",
    "あれ",
    "それ",
    "どれ",
    "いる",
    "おる",
    "見",
    "みて",
)


def resolve_reply_stance(
    *,
    has_visual_threats: bool,
    topic_hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    threat_summary: str = "",
    user_text: str = "",
) -> ReplyStance:
    """観測とトピック候補から reply_stance を決める。"""
    threat = (threat_summary or "").strip()
    if has_visual_threats or "視認" in threat:
        return "saw"
    hits = list(topic_hits or ())
    if hits:
        return "hypothesis"
    text = (user_text or "").strip()
    if text and any(hint in text for hint in _CLARIFY_HINTS):
        return "clarify"
    return "none"


def reply_policy_line(stance: ReplyStance | str) -> str:
    key = str(stance or "none").strip().lower()
    if key not in _POLICY_LINES:
        key = "none"
    return _POLICY_LINES[key]  # type: ignore[index]
