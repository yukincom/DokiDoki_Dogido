"""村人の日課・発話材料の解決（純関数・状態機械側）。

アダプタは profession / is_baby / time_of_day の事実だけ送る。
- 怪しい（profession 未取得）→ 表示は「村人」のみ。職は LLM に渡さない
- 明確（none/nitwit/就職職）→ 職ラベルも付けて渡す
LLM に判定させない。プロンプトに「断定するな」を書かない。
"""

from __future__ import annotations

from dataclasses import dataclass
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

# アダプタが返しうる明確な profession ID（これ以外・null は不明扱い）
_KNOWN_PROFESSIONS = frozenset(
    {
        "none",
        "nitwit",
        "armorer",
        "butcher",
        "cartographer",
        "cleric",
        "farmer",
        "fisherman",
        "fletcher",
        "leatherworker",
        "librarian",
        "mason",
        "shepherd",
        "toolsmith",
        "weaponsmith",
    }
)


@dataclass(frozen=True, slots=True)
class VillagerSpeechFacts:
    """SM が ambient/chat に渡す村人材料。"""

    label: str
    profession_known: bool
    profession: str | None  # 明確なときだけ。不明は None
    is_baby: bool
    schedule: VillagerActivity
    schedule_ja: str
    job_site: str | None = None

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


def normalize_villager_profession(profession: str | None) -> str | None:
    """正規化。空は None（不明）。明確な none は 'none' のまま残す。"""
    if profession is None:
        return None
    text = str(profession).strip().lower().removeprefix("minecraft:")
    if not text or text in {"unknown", "unregistered", "-"}:
        return None
    return text


def is_profession_known(profession: str | None) -> bool:
    """アダプタが明確に取れた profession か（SM 判定。プロンプトに頼らない）。"""
    prof = normalize_villager_profession(profession)
    return prof is not None and prof in _KNOWN_PROFESSIONS


def resolve_villager_role(*, is_baby: bool, profession: str | None) -> VillagerRole:
    if is_baby:
        return "child"
    prof = normalize_villager_profession(profession)
    # 不明は unemployed 帯（仕事枠なし）で安全側
    if prof is None or prof in {"none", "nitwit"}:
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


def project_villager_speech_facts(
    *,
    day_time: int | None,
    is_baby: bool = False,
    profession: str | None = None,
) -> VillagerSpeechFacts:
    """発話に載せる村人材料を SM 側で確定する。

    - profession 不明 → label=村人、profession は渡さない
    - profession 明確 → カタログラベル（求職者/ニート/農民…）+ profession
    - 日課は常にコードで解決（不明職は unemployed 帯）
    """
    from dogido_server.entry_catalog import resolve_mob_catalog_entry

    activity = resolve_villager_schedule(
        day_time, is_baby=is_baby, profession=profession
    )
    known = is_profession_known(profession) and not is_baby
    prof = normalize_villager_profession(profession) if known else None

    if is_baby:
        entry = resolve_mob_catalog_entry("villager", is_baby=True) or {}
        label = str(entry.get("label") or "子供")
        return VillagerSpeechFacts(
            label=label,
            profession_known=True,
            profession=None,
            is_baby=True,
            schedule=activity,
            schedule_ja=villager_schedule_ja(activity),
            job_site=None,
        )

    if known and prof is not None:
        entry = resolve_mob_catalog_entry("villager", profession=prof, is_baby=False) or {}
        label = str(entry.get("label") or "村人")
        job_site = entry.get("job_site")
        return VillagerSpeechFacts(
            label=label,
            profession_known=True,
            profession=prof,
            is_baby=False,
            schedule=activity,
            schedule_ja=villager_schedule_ja(activity),
            job_site=str(job_site) if job_site else None,
        )

    # 不明: 村人だけ
    entry = resolve_mob_catalog_entry("villager", profession=None, is_baby=False) or {}
    label = str(entry.get("label") or "村人")
    return VillagerSpeechFacts(
        label=label,
        profession_known=False,
        profession=None,
        is_baby=False,
        schedule=activity,
        schedule_ja=villager_schedule_ja(activity),
        job_site=None,
    )
