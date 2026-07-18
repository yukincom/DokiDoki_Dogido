"""村人の日課解決（純関数）。

アダプタは profession / is_baby / time_of_day の事実だけ送る。
活動帯はここで割り出し、LLM には短いラベルだけ渡す。
表は Java 版の一般的な帯（ユーザ提供の日程表）に合わせた近似。
"""

from __future__ import annotations

from typing import Literal

VillagerRole = Literal["child", "employed", "unemployed"]
VillagerActivity = Literal["wander", "work", "gather", "play", "sleep"]

# 表示用（フラット。ドラマ語にしない）
ACTIVITY_JA: dict[VillagerActivity, str] = {
    "wander": "散歩中",
    "work": "仕事中",
    "gather": "集会中",
    "play": "遊び中",
    "sleep": "睡眠中",
}

# day_time 0 = 6:00。境界は [start, end)
# 提供表: 0000散歩 → 0200仕事/遊び → 0600子供散歩 → 0900集会 → 1000子供遊び
#         → 1100散歩 → 1200睡眠
_EMPLOYED: tuple[tuple[int, VillagerActivity], ...] = (
    (0, "wander"),
    (2000, "work"),
    (9000, "gather"),
    (11000, "wander"),
    (12000, "sleep"),
)
_UNEMPLOYED: tuple[tuple[int, VillagerActivity], ...] = (
    (0, "wander"),
    (9000, "gather"),
    (11000, "wander"),
    (12000, "sleep"),
)
_CHILD: tuple[tuple[int, VillagerActivity], ...] = (
    (0, "wander"),
    (2000, "play"),
    (6000, "wander"),
    (10000, "play"),
    (11000, "wander"),
    (12000, "sleep"),
)


def normalize_villager_profession(profession: str | None) -> str:
    text = (profession or "none").strip().lower().removeprefix("minecraft:")
    return text or "none"


def resolve_villager_role(*, is_baby: bool, profession: str | None) -> VillagerRole:
    if is_baby:
        return "child"
    prof = normalize_villager_profession(profession)
    if prof in {"none", "nitwit"}:
        return "unemployed"
    return "employed"


def resolve_villager_schedule(
    day_time: int | None,
    *,
    is_baby: bool = False,
    profession: str | None = None,
) -> VillagerActivity:
    """time_of_day (0–23999) と属性から活動を返す。不明時刻は散歩。"""
    role = resolve_villager_role(is_baby=is_baby, profession=profession)
    if day_time is None:
        return "wander"
    tick = int(day_time) % 24000
    table = _CHILD if role == "child" else _EMPLOYED if role == "employed" else _UNEMPLOYED
    activity: VillagerActivity = table[0][1]
    for start, act in table:
        if tick >= start:
            activity = act
        else:
            break
    return activity


def villager_schedule_ja(activity: VillagerActivity | str) -> str:
    key = str(activity or "wander").strip().lower()
    return ACTIVITY_JA.get(key, "散歩中")  # type: ignore[arg-type]


def should_suppress_ambient_for_sleep(activity: VillagerActivity | str) -> bool:
    return str(activity or "").strip().lower() == "sleep"
