from __future__ import annotations

from dogido_server.assist.types import ActionContext, ActionDispatch, ActionSpec, RiskPolicy


class ActionGate:
    """実行capability・現在snapshot・risk policyを毎回コード検証する。"""

    def dispatch(self, spec: ActionSpec, context: ActionContext) -> ActionDispatch:
        if spec.policy is RiskPolicy.DENY:
            return ActionDispatch(spec.name, "denied", detail_code="policy_denied")
        if spec.capability not in context.execution_capabilities:
            return ActionDispatch(spec.name, "unavailable", detail_code="capability_missing")
        if not spec.available(context):
            return ActionDispatch(spec.name, "unavailable", detail_code="no_candidate")
        if spec.policy is RiskPolicy.CONFIRM:
            return ActionDispatch(spec.name, "confirm_required", detail_code="confirmation_required")
        return ActionDispatch(spec.name, "command", command=spec.handler(context))
