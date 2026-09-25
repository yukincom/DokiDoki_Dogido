use super::*;

pub(super) fn submerged(e: &GameEvent) -> bool {
    e.world.is_submerged == Some(true)
}
pub(super) fn sky(e: &GameEvent) -> bool {
    e.world.sky_visible == Some(true)
}
pub(super) fn ceiling(e: &GameEvent) -> f64 {
    e.world.ceiling_height.filter(|v| *v != 0.0).unwrap_or(24.0)
}
pub(super) fn lamp(e: &GameEvent, s: &Settings) -> bool {
    e.world.nearby_light_source_count.unwrap_or(0) > 0
        && e.world
            .nearest_light_source_distance
            .is_none_or(|d| d <= n(s, "lit_interior_safe_light_source_distance"))
}
pub(super) fn buffered(e: &GameEvent, s: &Settings) -> bool {
    lamp(e, s) && e.world.local_light.unwrap_or(15) >= 4
}
pub(super) fn safe(e: &GameEvent) -> bool {
    let w = &e.world;
    if submerged(e) || w.nearby_door_count.unwrap_or(0) <= 0 || w.local_light.unwrap_or(0) < 8 {
        return false;
    }
    match w.safe_zone_with_door {
        Some(false) => false,
        Some(true) => !(sky(e) && ceiling(e) >= 8.0 && w.enclosure_score.unwrap_or(0.0) < 0.45),
        None => w.enclosure_score.unwrap_or(0.0) >= 0.18 || ceiling(e) <= 5.0 || !sky(e),
    }
}
pub(super) fn lit(e: &GameEvent, s: &Settings) -> bool {
    let w = &e.world;
    !submerged(e)
        && !sky(e)
        && (w.local_light.unwrap_or(15) as f64 >= n(s, "lit_interior_safe_light_threshold")
            || lamp(e, s))
        && !matches!(w.overhead_cover_type.as_deref(), Some("foliage" | "fluid"))
        && ceiling(e) <= n(s, "lit_interior_safe_max_ceiling_height")
        && w.connected_dark_volume
            .is_some_and(|v| v as f64 <= n(s, "lit_interior_safe_max_connected_volume"))
        && w.nearest_dark_spawn_distance
            .is_some_and(|v| v >= n(s, "lit_interior_safe_min_spawn_distance"))
}
pub(super) fn shelter(e: &GameEvent, s: &Settings) -> bool {
    let w = &e.world;
    let home = w.nearby_bed_count.unwrap_or(0) > 0
        && w.respawn_point_set == Some(true)
        && w.respawn_distance
            .is_some_and(|v| v <= n(s, "home_bed_prompt_distance"));
    !submerged(e)
        && !safe(e)
        && !home
        && w.double_height_open_side_count.unwrap_or(0) == 0
        && w.cardinal_wall_count.unwrap_or(0) >= 4
        && ceiling(e) <= n(s, "emergency_shelter_max_ceiling_height")
}
pub(super) fn cramped(e: &GameEvent, s: &Settings) -> bool {
    let w = &e.world;
    !submerged(e)
        && !sky(e)
        && w.double_height_open_side_count.unwrap_or(0) == 0
        && ceiling(e) <= n(s, "cramped_dark_burrow_max_ceiling_height")
        && !matches!(w.overhead_cover_type.as_deref(), Some("foliage" | "fluid"))
        && (w.enclosure_score.unwrap_or(0.0) >= n(s, "cramped_dark_burrow_min_enclosure_score")
            || w.connected_dark_volume
                .is_some_and(|v| v as f64 <= n(s, "cramped_dark_burrow_max_connected_volume"))
                && w.cardinal_wall_count.unwrap_or(0) as f64
                    >= n(s, "cramped_dark_burrow_min_wall_count"))
}
pub(super) fn dark(e: &GameEvent, s: &Settings) -> bool {
    e.world
        .local_light
        .map(|v| v as f64 <= n(s, "occluded_entry_light_threshold"))
        .unwrap_or(
            e.world.danger_darkness_score.unwrap_or(0.0)
                >= n(s, "occluded_entry_darkness_threshold"),
        )
}
pub(super) fn zone(e: &GameEvent, s: &Settings) -> bool {
    !submerged(e)
        && !buffered(e, s)
        && !lit(e, s)
        && !safe(e)
        && !shelter(e, s)
        && !cramped(e, s)
        && crate::combat::core::occluded(e)
        && dark(e, s)
}
pub(super) fn water_dark(e: &GameEvent, s: &Settings) -> bool {
    submerged(e)
        && e.world.submerged_depth_blocks.unwrap_or(0) as f64
            >= n(s, "submerged_darkness_depth_threshold")
        && dark(e, s)
}
pub(super) fn canopy(e: &GameEvent) -> bool {
    !submerged(e)
        && e.world.overhead_cover_type.as_deref() == Some("foliage")
        && e.world.ceiling_height.unwrap_or(0.0) >= 3.0
}
pub(super) fn foliage(e: &GameEvent) -> bool {
    canopy(e)
        && e.player.position.y.is_none_or(|y| y > 50.0)
        && matches!(
            biome(e),
            "forest"
                | "flower_forest"
                | "birch_forest"
                | "old_growth_birch_forest"
                | "dark_forest"
                | "jungle"
                | "bamboo_jungle"
                | "sparse_jungle"
                | "taiga"
                | "old_growth_pine_taiga"
                | "old_growth_spruce_taiga"
                | "snowy_taiga"
                | "grove"
                | "mangrove_swamp"
        )
        && e.world.local_light.unwrap_or(15) <= 8
        && e.world.danger_darkness_score.unwrap_or(0.0) >= 0.58
}
pub(super) fn blocked(e: &GameEvent) -> bool {
    e.visual_threats.iter().any(|t| {
        t.distance.is_some_and(|d| {
            d <= 6.0 || t.approaching || ranged(&t.r#type) && d <= range(&t.r#type) + 1.5
        })
    }) || e.auditory_threats.iter().any(|t| {
        matches!(
            t.distance_band,
            Some(crate::events::DistanceBand::Touching | crate::events::DistanceBand::VeryClose)
        )
    })
}
pub(super) fn torch(e: &GameEvent) -> bool {
    ["torch", "soul_torch", "lantern", "soul_lantern"]
        .iter()
        .any(|k| e.inventory.get(*k).copied().unwrap_or(0) > 0)
}
pub(super) fn craftable(e: &GameEvent) -> bool {
    e.inventory.get("coal").copied().unwrap_or(0)
        + e.inventory.get("charcoal").copied().unwrap_or(0)
        > 0
        && e.inventory.get("stick").copied().unwrap_or(0) > 0
}
pub(super) fn materials(e: &GameEvent) -> bool {
    e.nearby_resources.iter().any(|r| {
        matches!(r.name.as_str(), "coal_ore" | "coal" | "charcoal")
            || r.name.ends_with("_log")
            || r.name.ends_with("_planks")
    })
}
pub(super) fn weapon(e: &GameEvent) -> bool {
    let test = |v: &str| {
        ["sword", "axe", "bow", "crossbow", "trident", "mace"]
            .iter()
            .any(|k| v.contains(k))
    };
    e.player.held_item.as_deref().is_some_and(test)
        || e.inventory.iter().any(|(k, v)| *v > 0 && test(k))
}
pub(super) fn bed(e: &GameEvent) -> bool {
    let test = |v: &str| {
        let v = v.trim_start_matches("minecraft:");
        v == "bed" || v.ends_with("_bed")
    };
    e.player.held_item.as_deref().is_some_and(test)
        || e.inventory.iter().any(|(k, v)| *v > 0 && test(k))
}
pub(super) fn cave(e: &GameEvent) -> bool {
    biome(e) == "deep_dark" || biome(e).ends_with("_caves")
}
pub(super) fn biome(e: &GameEvent) -> &str {
    e.world
        .biome
        .as_deref()
        .unwrap_or("")
        .trim_start_matches("minecraft:")
}
pub(super) fn overworld(e: &GameEvent) -> bool {
    matches!(
        e.player.dimension.as_deref(),
        None | Some("" | "overworld" | "minecraft:overworld")
    )
}
pub(super) fn phase(e: &GameEvent) -> Option<TimePhase> {
    if overworld(e) {
        e.world.time_phase
    } else {
        None
    }
}
pub(super) fn night(e: &GameEvent, s: &Settings) -> bool {
    phase(e) == Some(TimePhase::Night)
        || overworld(e)
            && e.world
                .time_of_day
                .is_some_and(|v| v as f64 >= n(s, "emergency_shelter_night_start"))
}
pub(super) fn morning(e: &GameEvent, s: &Settings) -> bool {
    matches!(phase(e), Some(TimePhase::Morning | TimePhase::Day))
        || overworld(e)
            && e.world
                .time_of_day
                .is_some_and(|v| (v as f64) < n(s, "emergency_shelter_morning_cutoff"))
}
