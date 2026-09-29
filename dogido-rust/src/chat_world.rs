//! Current-frame conversation material. Session state and name authorization stay elsewhere.
use crate::chat_catalog::{Catalog, strip};
use crate::environment::{
    precipitation,
    projection::{MiningState, project_environment},
};
use crate::events::{GameEvent, TimePhase, Weather};
use crate::world_catalog::{self, WorldCatalog};
use anyhow::{Result, ensure};
use serde::Serialize;
use std::collections::BTreeMap;

/// Call only for the existing inventory-question path; no always-on prompt expansion.
pub fn inventory_summary(
    catalog: &WorldCatalog,
    inventory: &BTreeMap<String, i64>,
    max_items: isize,
) -> String {
    let mut counted: Vec<_> = inventory
        .iter()
        .filter(|(_, v)| **v > 0)
        .map(|(id, n)| {
            let key = id.strip_prefix("minecraft:").unwrap_or(id);
            (*n, key, catalog.item_label(Some(key)))
        })
        .collect();
    if counted.is_empty() {
        return "（所持品データなし、または空）".into();
    }
    counted.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(b.1)));
    let len = counted.len();
    let end = if max_items < 0 {
        len.saturating_sub(max_items.unsigned_abs())
    } else {
        len.min(max_items as usize)
    };
    let mut parts: Vec<_> = counted[..end]
        .iter()
        .map(|(n, _, label)| format!("{label}×{n}"))
        .collect();
    if (len as i128) > (max_items as i128) {
        parts.push(format!("ほか{}種", len as i128 - max_items as i128));
    }
    parts.join("、")
}
pub fn wants_look_answer(input: &str) -> bool {
    let text = strip(input);
    if text.is_empty() {
        return false;
    }
    let markers = [
        "これ何",
        "これなに",
        "これは何",
        "これはなに",
        "何かな",
        "なにかな",
        "何これ",
        "なにこれ",
        "それ何",
        "それなに",
        "あれ何",
        "このブロック",
        "この花",
        "この石",
        "見てる",
        "指して",
        "指差",
    ];
    if markers.iter().any(|m| text.contains(m)) {
        return true;
    }
    let compact = text.replace([' ', '　'], "");
    matches!(
        compact.as_str(),
        "これ？" | "これ?" | "これ" | "それ？" | "それ?" | "あれ？" | "あれ?"
    ) || ["これ", "それ", "あれ"].iter().any(|m| text.contains(m))
        && ["何", "なに", "？", "?"].iter().any(|m| text.contains(m))
}
pub fn home_evidence(event: &GameEvent, home_bed_prompt_distance: f64) -> Vec<String> {
    let w = &event.world;
    if w.respawn_point_set != Some(true)
        || !w
            .respawn_distance
            .is_some_and(|d| d <= home_bed_prompt_distance)
    {
        return vec![];
    }
    let mut out = vec!["nearby_respawn_point".into()];
    if w.nearby_bed_count.unwrap_or(0) > 0 {
        out.push("nearby_bed".into())
    }
    if w.nearby_door_count.unwrap_or(0) > 0 {
        out.push("nearby_door".into())
    }
    if out.len() == 1 {
        out.clear()
    }
    out
}
fn foliage(event: &GameEvent) -> bool {
    let w = &event.world;
    if w.is_submerged == Some(true)
        || w.overhead_cover_type
            .as_deref()
            .unwrap_or("")
            .to_lowercase()
            != "foliage"
        || w.ceiling_height.unwrap_or(0.0) < 3.0
    {
        return false;
    }
    if event.player.position.y.is_some_and(|y| y <= 50.0) {
        return false;
    }
    let biome = strip(w.biome.as_deref().unwrap_or("")).to_lowercase();
    world_catalog::foliage_biome(&biome)
        && w.local_light.unwrap_or(15) <= 8
        && w.danger_darkness_score.unwrap_or(0.0) >= 0.58
}
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct Place {
    pub space_kind: String,
    pub sky_visible: Option<bool>,
    pub place_line: String,
    pub biome_label: String,
    pub home_evidence: Vec<String>,
}
/// current_structure is the caller's session state; world.structure is not substituted.
pub fn place_context(
    catalog: &WorldCatalog,
    entries: &Catalog,
    event: &GameEvent,
    current_structure: Option<&str>,
    home_bed_prompt_distance: f64,
) -> Result<Place> {
    let environment = project_environment(event);
    let w = &event.world;
    let y = event.player.position.y;
    let sky = w.sky_visible;
    let ceiling = w.ceiling_height;
    let biome_label = if environment.include_biome_context {
        catalog.biome_label(w.biome.as_deref())
    } else {
        String::new()
    };
    let cover = w
        .overhead_cover_type
        .as_deref()
        .filter(|s| !s.is_empty())
        .unwrap_or("unknown")
        .to_lowercase();
    let occluded = crate::combat::core::occluded_with_cover(event, &cover);
    let home = home_evidence(event, home_bed_prompt_distance);
    let (kind, ja) = if w.is_submerged == Some(true) {
        ("underwater", "水中".into())
    } else if environment.mining_state == MiningState::Active {
        ("active_mining", "地下で採掘中".into())
    } else if !home.is_empty() {
        let fixtures = [("nearby_bed", "ベッド"), ("nearby_door", "ドア")]
            .into_iter()
            .filter(|(e, _)| home.iter().any(|h| h == e))
            .map(|(_, label)| label)
            .collect::<Vec<_>>()
            .join("・");
        (
            "home_base",
            format!("家・拠点らしい場所（近くの{fixtures}と設定済みリスポーン地点が一致）"),
        )
    } else if environment.cave_biome {
        ("cave_biome", "洞窟バイオームの中".into())
    } else if environment.mining_state == MiningState::LikelyPlace {
        ("mine_like", "坑道らしい地下空間".into())
    } else if sky == Some(false)
        && (ceiling.is_some_and(|v| v <= 8.0)
            || w.enclosure_score.unwrap_or(0.0) >= 0.35
            || y.is_some_and(|v| v <= 48.0)
            || occluded)
    {
        (
            "underground_or_roofed",
            "地下っぽい／屋根のある空間（空は見えない）".into(),
        )
    } else if foliage(event) || (cover == "foliage" && sky != Some(true)) {
        ("canopy", "木陰っぽい空間".into())
    } else if sky == Some(true) {
        ("open_surface", "開けた地上（空が見える）".into())
    } else if sky == Some(false) {
        (
            "roofed_unclear",
            "空は見えないが、深さははっきりしない空間".into(),
        )
    } else {
        ("unknown", "空間の詳細は不明".into())
    };
    let sky_ja = match sky {
        Some(true) => "空が見える",
        Some(false) => "空は見えない",
        None => "空の見え方は不明",
    };
    let mut bits = vec![format!("空間: {ja}"), sky_ja.into()];
    if !biome_label.is_empty() {
        let prefix = if environment.cave_biome && !environment.include_sky_context {
            "洞窟バイオーム"
        } else {
            "地表バイオーム"
        };
        bits.insert(0, format!("{prefix}: {biome_label}"));
    }
    if let Some(y) = y {
        ensure!(y.is_finite(), "nonfinite player Y cannot be rounded");
        let rounded = y.round_ties_even();
        bits.push(format!(
            "高さY{}",
            if rounded == 0.0 {
                "0".into()
            } else {
                format!("{rounded:.0}")
            }
        ));
    }
    if let Some(ceiling) = ceiling {
        bits.push(format!("天井おおよそ{ceiling:.0}m"));
    }
    if let Some(light) = w.local_light {
        bits.push(format!("明るさ{light}"));
    }
    if let Some(id) = current_structure.filter(|v| !v.is_empty()) {
        bits.push(format!(
            "構造物: {}",
            world_catalog::structure_label(entries, Some(id))
        ));
    }
    Ok(Place {
        space_kind: kind.into(),
        sky_visible: sky,
        place_line: bits.join(" / "),
        biome_label,
        home_evidence: home,
    })
}
pub fn weather_label(event: &GameEvent, climate: &precipitation::Climate) -> Result<String> {
    if precipitation::from_event(event, climate)?.precipitation_kind
        == precipitation::PrecipitationKind::Snow
    {
        return Ok("雪".into());
    }
    Ok(match event.world.weather {
        Some(Weather::Clear) => "晴れ",
        Some(Weather::Rain) => "雨",
        Some(Weather::Thunder) => "雷",
        None => "不明",
    }
    .into())
}
pub fn weather_fact(event: &GameEvent) -> &'static str {
    if matches!(event.world.weather, Some(Weather::Rain | Weather::Thunder))
        && matches!(
            event.world.time_phase,
            Some(TimePhase::Day | TimePhase::Morning)
        )
    {
        "薄暗い。敵が地上にいる。"
    } else {
        ""
    }
}
