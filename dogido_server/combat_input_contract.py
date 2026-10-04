"""端末AIの戦闘中断5分類だけのIPC契約。状態と根拠の採否はRustが検査する。"""
from dataclasses import dataclass, field
import math
from typing import Any, Protocol

STRUCTURED_STATUS_KEY = "__dogido_status"

@dataclass(slots=True)
class CombatInputRequest:
    kind: str = "haiku_workshop_combat_input"
    details: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, str]] = field(default_factory=list)
    temperature: float = 0.0
    max_tokens: int = 120

class ChatFallback(Protocol):
    def generate_structured_json(self, request: CombatInputRequest) -> dict[str, Any]: ...


def allowed_actions(request: CombatInputRequest) -> list[str]:
    """Rustから受けた許可集合の外形を検査し、順序を保って返す。値は補わない。"""
    if request.kind != "haiku_workshop_combat_input":
        raise ValueError("unsupported platform AI task")
    actions = request.details.get("allowed_actions") if isinstance(request.details, dict) else None
    if (not isinstance(actions, list) or not actions
        or any(not isinstance(action, str) or not action or action != action.strip() for action in actions)
        or len(set(actions)) != len(actions)):
        raise ValueError("invalid combat allowed_actions")
    return list(actions)


# SDK応答の外形と0..1のconfidenceだけを検査し、不正ならprovider切替へ返す。
# RustのAnalysis::parseが0.75以上・原文中evidenceを検査し、別の行為ガードと現在状態で
# 再開・終了の可否を決める。Pythonの外形合格は行為の採用を意味しない。
def validate_payload(request, payload):
    actions = allowed_actions(request)
    if not isinstance(payload, dict) or set(payload) != {"action", "confidence", "evidence"}:
        raise ValueError("invalid combat input object")
    confidence = payload["confidence"]
    if (not isinstance(payload["action"], str) or payload["action"] not in actions
        or type(confidence) not in (int, float) or not math.isfinite(confidence)
        or not 0 <= confidence <= 1 or not isinstance(payload["evidence"], str)):
        raise ValueError("invalid combat input fields")
