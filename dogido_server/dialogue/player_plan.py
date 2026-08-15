"""現在の player_chat ターンだけで使う、明示された行動予定。

長期・短期メモリには保存しない。プレイヤー本人が現在の発話で明言した
予定だけを閉じた action にして、同じターンの返答を観測材料より優先させる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

PlayerTurnPlanKind = Literal["none", "return_home"]


@dataclass(frozen=True, slots=True)
class PlayerTurnPlan:
    action: PlayerTurnPlanKind = "none"
    evidence: str = ""


_RETURN_OBLIGATION_PATTERNS = (
    re.compile(
        r"(?:帰|かえ)ら(?:なくちゃ|なきゃ|なあかん|(?:ないと|んと)(?!思|考|言))"
    ),
    re.compile(
        r"(?:戻|もど)ら(?:なくちゃ|なきゃ|なあかん|(?:ないと|んと)(?!思|考|言))"
    ),
)

_HOME_TARGET = r"(?:お?家|うち|自宅|拠点|リスポーン地点|ベッド)"
_RETURN_ACTION = (
    r"(?:帰ろう|帰ろ|帰る(?:わ|で|ね|よ)?|帰ります|"
    r"戻ろう|戻ろ|戻る(?:わ|で|ね|よ)?|戻ります|"
    r"向かおう|向かう|向かいます)"
)
_EXPLICIT_HOME_RETURN_PATTERNS = (
    re.compile(rf"{_HOME_TARGET}.{{0,8}}?{_RETURN_ACTION}"),
    re.compile(rf"{_RETURN_ACTION}.{{0,8}}?{_HOME_TARGET}"),
)

_EXPLICIT_RETURN_NEGATION = re.compile(
    r"(?:帰らない(?:よ|で|つもり)|帰りたくない|帰らん(?:で|つもり)|"
    r"戻らない(?:よ|で|つもり)|戻りたくない|戻らん(?:で|つもり))"
)

_UNSAFE_OUTWARD_SUGGESTION = re.compile(
    r"(?:遠出(?:し|する|して|せえ)|遠くまで(?:行|い)|"
    r"もう少し(?:先|遠く).{0,8}(?:行|進)|"
    r"(?:冒険|探索)(?:しよ|しよう|続けよ|続けよう|行こ|行こう)|"
    r"先へ(?:行|進)|出かけ(?:よ|よう|へんか))"
)
_SAFE_NEGATION_OR_RETURN = re.compile(
    r"(?:遠出.{0,6}(?:やめ|控え|せん)|遠くまで行か(?:ん|ず)|先へ行か(?:ん|ず)|"
    r"探索.{0,6}(?:やめ|控え|せん)|冒険.{0,6}(?:やめ|控え|せん))"
)


def extract_player_turn_plan(user_text: str | None) -> PlayerTurnPlan:
    """発話中に明記された帰宅予定だけを返す。推測や行動ログは使わない。"""

    text = "".join(str(user_text or "").split())
    if not text:
        return PlayerTurnPlan()

    # 「帰らないと」「帰らなくちゃ」は否定形に見えるが、帰宅義務の明言。
    for pattern in _RETURN_OBLIGATION_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return PlayerTurnPlan(action="return_home", evidence=match.group(0))

    if _EXPLICIT_RETURN_NEGATION.search(text):
        return PlayerTurnPlan()

    for pattern in _EXPLICIT_HOME_RETURN_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return PlayerTurnPlan(action="return_home", evidence=match.group(0))
    return PlayerTurnPlan()


def conflicts_with_player_travel_guidance(
    text: str | None,
    *,
    player_turn_plan: str | None,
    safety_priority: str | None,
) -> bool:
    """帰宅・避難優先のターンで、追加の遠出を勧める文だけを検出する。"""

    if player_turn_plan != "return_home" and safety_priority != "seek_safe_place":
        return False
    candidate = "".join(str(text or "").split())
    if not candidate or not _UNSAFE_OUTWARD_SUGGESTION.search(candidate):
        return False
    return _SAFE_NEGATION_OR_RETURN.search(candidate) is None
