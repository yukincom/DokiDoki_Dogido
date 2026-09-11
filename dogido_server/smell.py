"""スメル観測の互換解決と、コード固定の発話表現。

adapterが距離・温度・雨上がり・同点を解決し、serverは閉じた結果だけを使う。
通常の会話モデルへ嗅覚観測を生成させない。
"""

from __future__ import annotations

from dataclasses import dataclass

from dogido_server.models import GameEvent, SmellObservation
from dogido_server.state_machine.fallback_catalog import fallback_text


@dataclass(frozen=True, slots=True)
class SmellSpeech:
    text: str
    cue_id: str
    speech_profile: str


def event_smell_observation(event: GameEvent) -> SmellObservation | None:
    """新しい明示状態を優先し、旧ゾンビclueだけを限定的に読み替える。"""

    if event.smell_observation is not None:
        return event.smell_observation
    if not event.zombie_scent_clues:
        return None
    first = event.zombie_scent_clues[0]
    return SmellObservation(
        status="present",
        smell_id="zombie",
        category="decay",
        valence="unpleasant",
        source_kind="entity",
        specificity="source",
        effective_strength=8,
        temperature_modifier=0,
    )


def smell_signature(observation: SmellObservation | None) -> str | None:
    if observation is None or observation.status != "present":
        return None
    return ":".join(
        (
            str(observation.specificity),
            str(observation.smell_id),
            str(observation.category),
            str(observation.valence),
        )
    )


def smell_speech(observation: SmellObservation | None) -> SmellSpeech:
    """現在の閉じた観測を、生成なしの一言へ変える。"""

    if observation is None:
        return SmellSpeech(
            fallback_text("general", "chat", "no_scent_evidence"),
            "smell_unsupported",
            "peace",
        )
    if observation.status == "none":
        return SmellSpeech(
            fallback_text("general", "smell", "none"),
            "smell_none",
            "peace",
        )
    if observation.status == "suppressed":
        key = (
            "suppressed_submerged"
            if observation.suppression_reason == "submerged"
            else "suppressed_weather"
        )
        return SmellSpeech(
            fallback_text("general", "smell", key),
            f"smell_{key}",
            "peace",
        )

    smell_id = observation.smell_id or "mixed"
    if smell_id == "zombie":
        return SmellSpeech(
            fallback_text("general", "combat", "zombie_scent_nearby"),
            "zombie_scent_warning",
            "battle",
        )

    direct_keys = {
        "rotten_flesh",
        "composter",
        "brewing_stand",
        "swamp",
        "raw_meat",
        "raw_fish",
        "cooked_meat",
        "cooked_fish",
        "cooking_meat",
        "cooking_fish",
        "soup",
        "cookie",
        "cake",
        "bread",
        "ink_sac",
        "rain_after",
        "decay",
        "food",
        "mixed",
    }
    if smell_id in direct_keys:
        key = smell_id
    elif observation.category == "flower":
        key = f"flower_{observation.valence or 'mixed'}"
    elif observation.category in {"decay", "food", "rain_after"}:
        key = observation.category
    else:
        key = "mixed"
    return SmellSpeech(
        fallback_text("general", "smell", key),
        f"smell_{key}",
        "peace",
    )


def observation_is_legacy_zombie(event: GameEvent) -> bool:
    return event.smell_observation is None and bool(event.zombie_scent_clues)


__all__ = [
    "SmellSpeech",
    "event_smell_observation",
    "observation_is_legacy_zombie",
    "smell_signature",
    "smell_speech",
]
