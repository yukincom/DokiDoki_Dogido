"""観測値から、対話と川柳で共有する現在地の投影を作る。

地表バイオームやワールド天気は、空が見えない場所でもゲーム内部では取得できる。
ここでは「取得できる情報」と「プレイヤーが現在の場面として共有できる情報」を分ける。
採掘中も単一の静止画的条件では断定せず、直近の破壊実績を主根拠にする。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dogido_server.models import GameEvent


MiningState = Literal["active", "likely_place", "none"]


@dataclass(frozen=True, slots=True)
class EnvironmentProjection:
    include_sky_context: bool
    include_biome_context: bool
    cave_biome: bool
    mining_state: MiningState
    mining_label: str
    evidence: tuple[str, ...]


def project_environment(event: GameEvent) -> EnvironmentProjection:
    """現在地について、外部へ出してよい背景と採掘文脈をコードで確定する。"""

    biome = str(event.world.biome or "").removeprefix("minecraft:").strip().lower()
    cave_biome = biome == "deep_dark" or biome.endswith("_caves")
    include_sky_context = event.world.sky_visible is True
    include_biome_context = include_sky_context or cave_biome

    if event.world.sky_visible is not False or bool(event.world.is_submerged):
        return EnvironmentProjection(
            include_sky_context=include_sky_context,
            include_biome_context=include_biome_context,
            cave_biome=cave_biome,
            mining_state="none",
            mining_label="",
            evidence=(),
        )

    evidence: list[str] = ["sky_hidden"]
    held = str(event.player.held_item or "").removeprefix("minecraft:").lower()
    mining_tool = held.endswith(("_pickaxe", "_shovel"))
    if mining_tool:
        evidence.append("mining_tool_held")

    fresh_breaks = [
        broken
        for broken in event.recent_block_breaks
        if broken.age_ms <= 10_000 and broken.material in {"stone", "earth", "ore"}
    ]
    if fresh_breaks:
        evidence.append(f"recent_terrain_breaks:{len(fresh_breaks)}")

    fresh_drops = [
        dropped
        for dropped in event.dropped_items
        if dropped.mining_related and (dropped.age_ms is None or dropped.age_ms <= 15_000)
    ]
    if fresh_drops:
        evidence.append(f"fresh_mining_drops:{len(fresh_drops)}")

    actively_breaking = event.player.block_breaking_active is True
    if actively_breaking:
        evidence.append("block_breaking_active")

    active = mining_tool and (
        len(fresh_breaks) >= 2
        or (len(fresh_breaks) >= 1 and actively_breaking)
        or (len(fresh_breaks) >= 1 and bool(fresh_drops))
    )
    if active:
        return EnvironmentProjection(
            include_sky_context=include_sky_context,
            include_biome_context=include_biome_context,
            cave_biome=cave_biome,
            mining_state="active",
            mining_label="プレイヤーは地下で採掘している",
            evidence=tuple(evidence),
        )

    cover = str(event.world.overhead_cover_type or "").strip().lower()
    natural_cover = cover in {"stone", "earth", "ore"}
    if natural_cover:
        evidence.append(f"natural_overhead:{cover}")

    depth = event.world.depth_below_surface
    player_y = event.player.position.y
    depth_signal = (depth is not None and depth >= 6) or (
        depth is None and player_y is not None and player_y <= 48
    )
    if depth_signal:
        evidence.append("below_surface")

    interior_signal = bool(
        event.world.safe_zone_with_door
        or (event.world.nearby_door_count or 0) > 0
        or (event.world.nearby_bed_count or 0) > 0
        or event.world.nearby_window_present
    )
    if interior_signal:
        evidence.append("interior_fixture_observed")

    if natural_cover and depth_signal and not interior_signal:
        return EnvironmentProjection(
            include_sky_context=include_sky_context,
            include_biome_context=include_biome_context,
            cave_biome=cave_biome,
            mining_state="likely_place",
            mining_label="坑道らしい地下空間にいる",
            evidence=tuple(evidence),
        )

    return EnvironmentProjection(
        include_sky_context=include_sky_context,
        include_biome_context=include_biome_context,
        cave_biome=cave_biome,
        mining_state="none",
        mining_label="",
        evidence=tuple(evidence),
    )
