from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Literal

from dogido_server.models import GameEvent, SelectHotbarCommand


class ActionName(str, Enum):
    SELECT_SWORD = "select_sword"


class RiskPolicy(str, Enum):
    AUTO = "auto"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ActionContext:
    event: GameEvent
    execution_capabilities: frozenset[str]
    now: datetime


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """LLMへ公開しない明示登録1件。"""

    name: ActionName
    capability: str
    policy: RiskPolicy
    available: Callable[[ActionContext], bool]
    handler: Callable[[ActionContext], SelectHotbarCommand]


@dataclass(frozen=True, slots=True)
class ActionDispatch:
    name: ActionName
    status: Literal["command", "unavailable", "confirm_required", "denied"]
    command: SelectHotbarCommand | None = None
    detail_code: str = ""
