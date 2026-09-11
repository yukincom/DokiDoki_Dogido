"""通常 ``player_chat`` の会話焦点と、次に読む材料を決める小さな planner。

この層は発話も世界操作も行わない。直近の実再生済み会話と現在入力を読み、
会話の続きで答えるか、現在観測を照合するか、カタログ候補を読むかを一件だけ
選ぶ。観測結果の真偽と最終的な発話可否は状態機械側で確定する。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Literal

from dogido_server.llm.types import StructuredGenerationRequest


LOGGER = logging.getLogger("uvicorn.error")

PlayerChatPlanAction = Literal[
    "continue_conversation",
    "check_entity_presence",
    "identify_entity",
    "answer_observation",
    "clarify_reference",
    "correct_previous_reply",
]
PlayerChatEntityStatus = Literal[
    "not_applicable",
    "observed",
    "not_observed",
    "unknown",
    "ambiguous",
]

PLAYER_CHAT_PLAN_ACTIONS: tuple[PlayerChatPlanAction, ...] = (
    "continue_conversation",
    "check_entity_presence",
    "identify_entity",
    "answer_observation",
    "clarify_reference",
    "correct_previous_reply",
)

_ENTITY_ACTIONS = frozenset(
    {"check_entity_presence", "identify_entity", "correct_previous_reply"}
)
_MIN_MODEL_CONFIDENCE = 0.62
_MIN_ENTITY_CONFIDENCE = 0.78
_MIN_CORRECTION_CONFIDENCE = 0.82


@dataclass(frozen=True, slots=True)
class PlayerChatPlanEvidence:
    turn_id: str
    quote: str


@dataclass(frozen=True, slots=True)
class PlayerChatPlan:
    action: PlayerChatPlanAction
    focus: str
    entity_query: str
    evidence: tuple[PlayerChatPlanEvidence, ...]
    confidence: float
    source: Literal["model", "fallback"]
    status: str

    @property
    def requests_catalog(self) -> bool:
        return self.action in _ENTITY_ACTIONS and bool(self.entity_query)


@dataclass(frozen=True, slots=True)
class PlayerChatEntityGrounding:
    status: PlayerChatEntityStatus
    query: str
    candidate_ids: tuple[str, ...] = ()
    candidate_labels: tuple[str, ...] = ()
    observed_ids: tuple[str, ...] = ()
    observed_labels: tuple[str, ...] = ()


def ground_player_chat_entity(
    plan: PlayerChatPlan,
    *,
    topic_hits: list[dict[str, object]],
    observed_entities: list[dict[str, str]],
) -> PlayerChatEntityGrounding:
    """planner が求めた対象だけを、カタログ候補と現在観測へ照合する。"""

    if not plan.requests_catalog:
        return PlayerChatEntityGrounding(
            status="not_applicable",
            query="",
        )
    candidates: list[tuple[str, str, float]] = []
    seen_candidates: set[str] = set()
    for hit in topic_hits[:8]:
        entity_id = _clean_entity_id(hit.get("entry_id"))
        label = _clean_text(
            str(hit.get("label_ja") or hit.get("label") or ""),
            limit=80,
        )
        if not entity_id or entity_id in seen_candidates:
            continue
        try:
            score = float(hit.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        seen_candidates.add(entity_id)
        candidates.append((entity_id, label or entity_id, score))
    if not candidates:
        return PlayerChatEntityGrounding(
            status="unknown",
            query=plan.entity_query,
        )

    observed_by_id: dict[str, str] = {}
    for row in observed_entities:
        entity_id = _clean_entity_id(row.get("entity_id"))
        if not entity_id:
            continue
        label = _clean_text(str(row.get("label") or entity_id), limit=80)
        observed_by_id[entity_id] = label or entity_id
    # 在否・訂正は、質問に最も強く一致した一件だけを照合対象にする。
    # 例: 「前哨基地」は outpost と関連 mob の両方に当たり得るが、
    # ピリジャー視認だけで前哨基地を「確認済み」にしてはいけない。
    grounding_candidates = (
        candidates[:1]
        if plan.action in {"check_entity_presence", "correct_previous_reply"}
        else candidates
    )
    reported_candidates = grounding_candidates[:4]
    matched = [row for row in grounding_candidates if row[0] in observed_by_id]
    if matched:
        return PlayerChatEntityGrounding(
            status="observed",
            query=plan.entity_query,
            candidate_ids=tuple(row[0] for row in reported_candidates),
            candidate_labels=tuple(row[1] for row in reported_candidates),
            observed_ids=tuple(row[0] for row in matched),
            observed_labels=tuple(observed_by_id[row[0]] for row in matched),
        )

    if len(candidates) >= 2 and abs(candidates[0][2] - candidates[1][2]) < 0.01:
        status: PlayerChatEntityStatus = "ambiguous"
    else:
        status = "not_observed"
    return PlayerChatEntityGrounding(
        status=status,
        query=plan.entity_query,
        candidate_ids=tuple(row[0] for row in reported_candidates),
        candidate_labels=tuple(row[1] for row in reported_candidates),
    )


def fixed_grounded_player_chat_reply(
    plan: PlayerChatPlan,
    grounding: PlayerChatEntityGrounding,
) -> str:
    """誤った在否断言を生成へ戻さない、高リスク結果だけの固定返答。"""

    label = grounding.candidate_labels[0] if grounding.candidate_labels else "その対象"
    if plan.action == "correct_previous_reply":
        if grounding.status == "observed":
            return (
                "ごめん、さっきの言い方は断言しすぎたわ。"
                f"今の観測では{label}を確認できとる。"
            )
        if grounding.status == "not_observed":
            return (
                f"ごめん、今の観測では{label}は確認できてへん。"
                "さっきの言い方は断言しすぎたわ。"
            )
        return "ごめん、今の材料では確かめられへん。さっきは断言しすぎたわ。"
    if plan.action == "check_entity_presence":
        if grounding.status == "not_observed":
            return f"今の観測では、{label}は確認できてへんわ。"
        if grounding.status in {"unknown", "ambiguous"}:
            return "どれのことか、今の材料だけやと分からへんわ。"
    if plan.action == "identify_entity" and grounding.status in {"unknown", "ambiguous"}:
        return "オレにはまだ分からへんわ。もうちょい見た目を教えてくれる？"
    return ""


def plan_player_chat(
    llm: object | None,
    *,
    user_text: str,
    conversation_turns: object,
    observation_summary: str,
    observed_entities: list[dict[str, str]],
    look_target_label: str = "",
    hearing_summary: str = "",
    inventory_question: bool = False,
    sound_question: bool = False,
) -> PlayerChatPlan:
    """会話焦点と一件のread actionを選ぶ。失敗時は安全な閉じたfallback。"""

    current_text = _clean_text(user_text, limit=160)
    history = _normalize_history(conversation_turns)
    fallback = _fallback_plan(
        current_text,
        inventory_question=inventory_question,
        sound_question=sound_question,
    )
    plain_presence_report = _is_plain_presence_report(current_text)
    generate = getattr(llm, "generate_structured_json", None)
    if not current_text or not callable(generate):
        return fallback

    details: dict[str, Any] = {
        "allowed_actions": list(PLAYER_CHAT_PLAN_ACTIONS),
        "history": history,
        "observations": {
            "summary": _clean_text(observation_summary, limit=600),
            "observed_entities": _normalize_observed_entities(observed_entities),
            "look_target_label": _clean_text(look_target_label, limit=80),
            "hearing_summary": _clean_text(hearing_summary, limit=240),
        },
        "routing_hints": {
            "inventory_question": bool(inventory_question),
            "sound_question": bool(sound_question),
            "presence_question": fallback.action == "check_entity_presence",
            "plain_presence_report": plain_presence_report,
        },
        "current": {
            "turn_id": "current",
            "role": "user",
            "text": current_text,
        },
    }
    fallback_payload = {
        "action": fallback.action,
        "focus": fallback.focus,
        "entity_query": fallback.entity_query,
        "evidence": [
            {"turn_id": row.turn_id, "quote": row.quote}
            for row in fallback.evidence
        ],
        "confidence": 0.0,
    }
    try:
        payload = generate(
            StructuredGenerationRequest(
                kind="player_chat_plan",
                fallback_value=fallback_payload,
                details=details,
                temperature=0.0,
                route="chat",
                max_tokens=320,
            )
        )
    except Exception as exc:
        LOGGER.warning(
            "player_chat_plan result=fallback reason=generation_error detail=%s",
            str(exc)[:180],
        )
        return fallback

    parsed = _parse_model_plan(payload, details)
    if (
        parsed is not None
        and (inventory_question or sound_question)
        and parsed.action != "answer_observation"
    ):
        parsed = None
    if (
        parsed is not None
        and fallback.action == "check_entity_presence"
        and parsed.action != "check_entity_presence"
    ):
        parsed = None
    if parsed is not None and plain_presence_report and parsed.action in {
        "check_entity_presence",
        "identify_entity",
    }:
        parsed = None
    if parsed is None:
        status = (
            str(payload.get("__dogido_status") or "invalid_payload")
            if isinstance(payload, dict)
            else "invalid_payload"
        )
        LOGGER.warning("player_chat_plan result=fallback reason=%s", status)
        return fallback
    LOGGER.warning(
        "player_chat_plan result=accepted action=%s focus=%s entity=%s evidence=%s confidence=%.2f",
        parsed.action,
        parsed.focus[:80],
        parsed.entity_query or "-",
        ",".join(row.turn_id for row in parsed.evidence),
        parsed.confidence,
    )
    return parsed


def _parse_model_plan(payload: object, details: dict[str, Any]) -> PlayerChatPlan | None:
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("__dogido_status") or "accepted")
    if status != "accepted":
        return None
    action = str(payload.get("action") or "")
    if action not in PLAYER_CHAT_PLAN_ACTIONS:
        return None
    focus = _clean_text(str(payload.get("focus") or ""), limit=80)
    entity_query = _clean_text(str(payload.get("entity_query") or ""), limit=120)
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return None
    minimum_confidence = (
        _MIN_CORRECTION_CONFIDENCE
        if action == "correct_previous_reply"
        else _MIN_ENTITY_CONFIDENCE
        if action in _ENTITY_ACTIONS
        else _MIN_MODEL_CONFIDENCE
    )
    if not focus or confidence < minimum_confidence or confidence > 1.0:
        return None

    turn_rows = [*details.get("history", []), details.get("current", {})]
    turns = {
        str(row.get("turn_id") or ""): row
        for row in turn_rows
        if isinstance(row, dict) and str(row.get("turn_id") or "")
    }
    evidence_rows = payload.get("evidence")
    if not isinstance(evidence_rows, list) or not 1 <= len(evidence_rows) <= 3:
        return None
    evidence: list[PlayerChatPlanEvidence] = []
    seen_ids: set[str] = set()
    for row in evidence_rows:
        if not isinstance(row, dict):
            return None
        turn_id = _clean_text(str(row.get("turn_id") or ""), limit=180)
        quote = _clean_text(str(row.get("quote") or ""), limit=160)
        source = turns.get(turn_id)
        source_text = str(source.get("text") or "") if source else ""
        if not turn_id or turn_id in seen_ids or not quote or quote not in source_text:
            return None
        seen_ids.add(turn_id)
        evidence.append(PlayerChatPlanEvidence(turn_id=turn_id, quote=quote))

    if "current" not in seen_ids:
        return None
    if action in _ENTITY_ACTIONS:
        if not entity_query or not any(entity_query in row.quote for row in evidence):
            return None
    elif entity_query:
        return None
    if action == "correct_previous_reply" and not any(
        str(turns[row.turn_id].get("role") or "") == "assistant"
        for row in evidence
        if row.turn_id in turns
    ):
        return None

    return PlayerChatPlan(
        action=action,  # type: ignore[arg-type]
        focus=focus,
        entity_query=entity_query,
        evidence=tuple(evidence),
        confidence=confidence,
        source="model",
        status=status,
    )


def _fallback_plan(
    user_text: str,
    *,
    inventory_question: bool = False,
    sound_question: bool = False,
) -> PlayerChatPlan:
    from dogido_server.dialogue.chat_policy import has_identify_intent

    action: PlayerChatPlanAction = "continue_conversation"
    entity_query = ""
    focus = "直近の会話への返答"
    if inventory_question:
        action = "answer_observation"
        focus = "現在の所持品"
    elif sound_question:
        action = "answer_observation"
        focus = "現在または直近の音の観測"
    elif _has_explicit_presence_question(
        user_text
    ) or _has_exact_structure_presence_question(user_text):
        action = "check_entity_presence"
        entity_query = user_text
        focus = "現在の対象の在否"
    elif has_identify_intent(user_text):
        action = "identify_entity"
        entity_query = user_text
        focus = "プレイヤーが示した対象の同定"
    quote = user_text or "（入力なし）"
    return PlayerChatPlan(
        action=action,
        focus=focus,
        entity_query=entity_query,
        evidence=(PlayerChatPlanEvidence(turn_id="current", quote=quote),),
        confidence=0.0,
        source="fallback",
        status="fallback",
    )


def _normalize_history(value: object) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(value, list):
        return rows
    for index, raw in enumerate(value[-10:]):
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip()
        text = _clean_text(str(raw.get("text") or ""), limit=160)
        turn_id = _clean_text(
            str(raw.get("turn_id") or f"history:{index}:{role}"),
            limit=180,
        )
        if role not in {"user", "assistant"} or not text or not turn_id:
            continue
        rows.append({"turn_id": turn_id, "role": role, "text": text})
    return rows


def _normalize_observed_entities(value: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value[:16]:
        entity_id = _clean_text(str(raw.get("entity_id") or ""), limit=80).removeprefix(
            "minecraft:"
        ).lower()
        label = _clean_text(str(raw.get("label") or ""), limit=80)
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        rows.append({"entity_id": entity_id, "label": label or entity_id})
    return rows


def _clean_text(value: str, *, limit: int) -> str:
    text = " ".join((value or "").replace("\n", " ").split()).strip()
    return text[:limit]


def _clean_entity_id(value: object) -> str:
    return _clean_text(str(value or ""), limit=80).removeprefix("minecraft:").lower()


def _has_explicit_presence_question(user_text: str) -> bool:
    """音声の平叙報告を在否問いへ昇格させない、保守的fallback。"""

    text = (user_text or "").strip()
    if not text:
        return False
    presence = any(
        token in text
        for token in ("いる", "おる", "居る", "いない", "おらん", "気配")
    )
    if not presence:
        return False
    if any(token in text for token in ("？", "?")):
        return True
    question_endings = (
        "いるの",
        "おるの",
        "いるか",
        "おるか",
        "いるん",
        "おるん",
        "いないの",
        "おらんの",
        "いないか",
        "おらんか",
        "いるかな",
        "おるかな",
        "いないかな",
        "おらんかな",
    )
    if text.endswith(question_endings):
        return True
    if _has_plain_presence_grammar(text):
        return False
    return any(token in text for token in ("まだ", "今も", "いまも"))


def _has_plain_presence_grammar(user_text: str) -> bool:
    """主題の「が」に続く存在／不在の平叙報告を認識する。"""

    return bool(
        re.search(
            r"が(?:まだ|今も|いまも|もう)?(?:いる|居る|おる|いない|おらん|ある|ない)",
            user_text or "",
        )
    )


def _has_exact_structure_presence_question(user_text: str) -> bool:
    """明示構造物名+存在問いだけを、モデル不在時にもread actionへ送る。"""

    text = (user_text or "").strip()
    structure_question_endings = (
        "あるか",
        "あるの",
        "あるかな",
        "ないか",
        "ないの",
        "ないかな",
        "見えるか",
        "見えるの",
        "見えるかな",
    )
    if not text or not (
        any(token in text for token in ("？", "?"))
        or text.endswith(structure_question_endings)
    ):
        return False
    if not any(token in text for token in ("ある", "ない", "見える")):
        return False
    from dogido_server.dialogue.chat_policy import filter_usable_topic_hits
    from dogido_server.entry_catalog import find_catalog_topics

    hits = filter_usable_topic_hits(find_catalog_topics(text))
    return any(str(hit.get("kind") or "") == "structure" for hit in hits)


def _is_plain_presence_report(user_text: str) -> bool:
    """「ラバがいる」のような明示報告を在否問いに変えない。"""

    text = (user_text or "").strip()
    if (
        not text
        or _has_explicit_presence_question(text)
        or _has_exact_structure_presence_question(text)
    ):
        return False
    return _has_plain_presence_grammar(text)
