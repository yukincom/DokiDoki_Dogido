//! Current-frame scene visibility and mining evidence. No temporal state or policy changes.
use crate::events::{GameEvent, RecentBlockBreakMaterial};
use crate::knowledge::query::space;
use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MiningState {
    Active,
    LikelyPlace,
    None,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct EnvironmentProjection {
    pub include_sky_context: bool,
    pub include_biome_context: bool,
    pub cave_biome: bool,
    pub mining_state: MiningState,
    pub mining_label: String,
    pub evidence: Vec<String>,
}

/// Mirrors environment_context.project_environment; raw values are never rewritten.
pub fn project_environment(event: &GameEvent) -> EnvironmentProjection {
    let biome = event.world.biome.as_deref().unwrap_or("");
    // Prefix removal intentionally precedes trim/lower, exactly as in Python.
    let biome = biome
        .strip_prefix("minecraft:")
        .unwrap_or(biome)
        .trim_matches(space)
        .to_lowercase();
    let cave_biome = biome == "deep_dark" || biome.ends_with("_caves");
    let mut result = EnvironmentProjection {
        include_sky_context: event.world.sky_visible == Some(true),
        include_biome_context: event.world.sky_visible == Some(true) || cave_biome,
        cave_biome,
        mining_state: MiningState::None,
        mining_label: String::new(),
        evidence: Vec::new(),
    };
    if event.world.sky_visible != Some(false) || event.world.is_submerged == Some(true) {
        return result;
    }
    result.evidence.push("sky_hidden".into());
    let held = event.player.held_item.as_deref().unwrap_or("");
    let held = held
        .strip_prefix("minecraft:")
        .unwrap_or(held)
        .to_lowercase();
    let mining_tool = held.ends_with("_pickaxe") || held.ends_with("_shovel");
    if mining_tool {
        result.evidence.push("mining_tool_held".into());
    }
    let breaks = event
        .recent_block_breaks
        .iter()
        .filter(|b| {
            b.age_ms <= 10_000
                && matches!(
                    b.material,
                    RecentBlockBreakMaterial::Stone
                        | RecentBlockBreakMaterial::Earth
                        | RecentBlockBreakMaterial::Ore
                )
        })
        .count();
    if breaks > 0 {
        result
            .evidence
            .push(format!("recent_terrain_breaks:{breaks}"));
    }
    let drops = event
        .dropped_items
        .iter()
        .filter(|d| d.mining_related && d.age_ms.is_none_or(|age| age <= 15_000))
        .count();
    if drops > 0 {
        result.evidence.push(format!("fresh_mining_drops:{drops}"));
    }
    let actively_breaking = event.player.block_breaking_active == Some(true);
    if actively_breaking {
        result.evidence.push("block_breaking_active".into());
    }
    if mining_tool && (breaks >= 2 || (breaks >= 1 && (actively_breaking || drops > 0))) {
        result.mining_state = MiningState::Active;
        result.mining_label = "プレイヤーは地下で採掘している".into();
        return result;
    }
    let cover = event
        .world
        .overhead_cover_type
        .as_deref()
        .unwrap_or("")
        .trim_matches(space)
        .to_lowercase();
    let natural_cover = matches!(cover.as_str(), "stone" | "earth" | "ore");
    if natural_cover {
        result.evidence.push(format!("natural_overhead:{cover}"));
    }
    let depth_signal = match event.world.depth_below_surface {
        Some(depth) => depth >= 6,
        None => event.player.position.y.is_some_and(|y| y <= 48.0),
    };
    if depth_signal {
        result.evidence.push("below_surface".into());
    }
    let interior_signal = event.world.safe_zone_with_door == Some(true)
        || event.world.nearby_door_count.unwrap_or(0) > 0
        || event.world.nearby_bed_count.unwrap_or(0) > 0
        || event.world.nearby_window_present == Some(true);
    if interior_signal {
        result.evidence.push("interior_fixture_observed".into());
    }
    if natural_cover && depth_signal && !interior_signal {
        result.mining_state = MiningState::LikelyPlace;
        result.mining_label = "坑道らしい地下空間にいる".into();
    }
    result
}

#[cfg(test)]
#[path = "projection_tests.rs"]
mod tests;
