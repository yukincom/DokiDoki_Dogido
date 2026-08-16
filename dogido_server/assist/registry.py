from __future__ import annotations

from dogido_server.assist.gate import ActionGate
from dogido_server.assist.select_sword import build_select_sword_spec
from dogido_server.assist.types import ActionContext, ActionDispatch, ActionName, ActionSpec


class AssistRegistry:
    """少数アクションの明示リスト。発見・plugin・TTL cacheは持たない。"""

    def __init__(self, specs: tuple[ActionSpec, ...], *, gate: ActionGate | None = None) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._gate = gate or ActionGate()

    def propose(self, name: ActionName, context: ActionContext) -> ActionDispatch:
        spec = self._specs.get(name)
        if spec is None:
            return ActionDispatch(name, "denied", detail_code="unknown_action")
        return self._gate.dispatch(spec, context)


def build_assist_registry() -> AssistRegistry:
    return AssistRegistry((build_select_sword_spec(),))
