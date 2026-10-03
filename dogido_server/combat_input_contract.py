"""端末AIの戦闘中断5分類だけのIPC契約。状態と根拠の採否はRustが検査する。"""
from dataclasses import dataclass, field
import math
from typing import Any, Protocol

STRUCTURED_STATUS_KEY = "__dogido_status"
ACTIONS = ("resume_workshop", "workshop_input", "close_workshop", "unrelated", "uncertain")

@dataclass(slots=True)
class CombatInputRequest:
    kind: str = "haiku_workshop_combat_input"
    details: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, str]] = field(default_factory=list)
    temperature: float = 0.0
    max_tokens: int = 120

class ChatFallback(Protocol):
    def generate_structured_json(self, request: CombatInputRequest) -> dict[str, Any]: ...


# SDK応答の外形と0..1のconfidenceだけを検査し、不正ならprovider切替へ返す。
# RustのAnalysis::parseが0.75以上・原文中evidenceを検査し、別の行為ガードと現在状態で
# 再開・終了の可否を決める。Pythonの外形合格は行為の採用を意味しない。
def validate_payload(request, payload):
    if request.kind != "haiku_workshop_combat_input":
        raise ValueError("unsupported platform AI task")
    if not isinstance(payload, dict) or set(payload) != {"action", "confidence", "evidence"}:
        raise ValueError("invalid combat input object")
    confidence = payload["confidence"]
    if (payload["action"] not in ACTIONS
        or payload["action"] not in request.details.get("allowed_actions", ACTIONS)
        or type(confidence) not in (int, float) or not math.isfinite(confidence)
        or not 0 <= confidence <= 1 or not isinstance(payload["evidence"], str)):
        raise ValueError("invalid combat input fields")
