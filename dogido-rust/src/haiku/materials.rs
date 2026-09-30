//! One capture per job. Current structure, ordered inventory and readings are
//! explicit snapshots owned by the caller, never a new empty RuntimeState.
use super::{
    context::{Context, Feature, Scene},
    source_atoms::*,
};
use crate::{
    chat_catalog::{Catalog, strip, text, truth},
    environment::{
        precipitation::{self, PrecipitationKind},
        projection::{MiningState, project_environment},
    },
    events::{GameEvent, TimePhase, Weather},
    world_catalog::{WorldCatalog, structure_label},
};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    sync::LazyLock,
};
mod constraints;
mod selection;
pub use constraints::constraint_details;
pub use selection::*;
static RULES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("materials/rules.json"))
        .expect("canonical haiku context rules")
});
/// Existing raw entry readers and exact MOB_VOICE_LABELS lookup. This is not a
/// substitute for WorldCatalog's display-label reader; both contracts are used.
pub trait Entries {
    fn item_entry(&self, id: &str) -> Option<&Value>;
    fn block_entry(&self, id: &str) -> Option<&Value>;
    fn mob_label(&self, raw_id: &str) -> String;
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ReadingCorrection {
    pub reading: String,
    #[serde(default)]
    pub forbidden_readings: Vec<String>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ReadingSnapshot {
    #[serde(default)]
    pub by_surface: HashMap<String, ReadingCorrection>,
}
impl ReadingSnapshot {
    pub fn resolve(&self, surface: &str, catalog: Option<&str>) -> Option<String> {
        self.by_surface
            .get(strip(surface))
            .filter(|_| !surface.is_empty())
            .map(|v| strip(&v.reading))
            .filter(|v| !v.is_empty())
            .or_else(|| catalog.map(strip).filter(|v| !v.is_empty()))
            .map(str::to_owned)
    }
    pub fn forbidden(&self, surface: &str) -> Vec<String> {
        self.by_surface
            .get(strip(surface))
            .filter(|_| !surface.is_empty())
            .map(|v| {
                v.forbidden_readings
                    .iter()
                    .filter(|v| !v.is_empty())
                    .cloned()
                    .collect()
            })
            .unwrap_or_default()
    }
}
pub struct RuntimeRead<'a> {
    pub current_structure: Option<&'a str>,
    pub player_name: &'a str,
    pub inventory_order: &'a [String],
}
fn field(v: &Value, k: &str) -> String {
    v.get(k).filter(|v| truth(v)).map(text).unwrap_or_default()
}
fn array_strings(v: &Value) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().filter(|v| truth(v)).map(text).collect())
        .unwrap_or_default()
}
fn normalize_id(s: &str) -> String {
    strip(s)
        .to_lowercase()
        .rsplit(':')
        .next()
        .unwrap_or("")
        .into()
}
fn biome_id(s: Option<&str>) -> String {
    strip(s.unwrap_or("")).to_lowercase()
}
fn distance_cmp(a: Option<f64>, b: Option<f64>) -> std::cmp::Ordering {
    a.unwrap_or(f64::INFINITY)
        .partial_cmp(&b.unwrap_or(f64::INFINITY))
        .unwrap_or(std::cmp::Ordering::Equal)
}
fn sorted_inventory(event: &GameEvent) -> Vec<(&String, &i64)> {
    let mut values: Vec<_> = event.inventory.iter().collect();
    values.sort_by(|a, b| b.1.cmp(a.1).then(a.0.cmp(b.0)));
    values
}
fn phase(event: &GameEvent) -> &'static str {
    match event.world.time_phase {
        Some(TimePhase::Morning) => "morning",
        Some(TimePhase::Day) => "day",
        Some(TimePhase::Evening) => "evening",
        Some(TimePhase::Night) => "night",
        None => "unknown",
    }
}
fn weather(event: &GameEvent) -> &'static str {
    match event.world.weather {
        Some(Weather::Clear) => "clear",
        Some(Weather::Rain) => "rain",
        Some(Weather::Thunder) => "thunder",
        None => "unknown",
    }
}
fn time_label(s: &str) -> &'static str {
    match s {
        "morning" => "朝",
        "day" => "昼",
        "evening" => "夕方",
        "night" => "夜",
        _ => "不明",
    }
}
fn weather_label(s: &str) -> &'static str {
    match s {
        "clear" => "晴れ",
        "rain" => "雨",
        "thunder" => "雷",
        _ => "不明",
    }
}
fn structure_fields(
    event: &GameEvent,
    current: Option<&str>,
    catalog: &Catalog,
) -> (String, String) {
    let raw = event
        .world
        .structure
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(current)
        .unwrap_or("");
    let id = strip(raw.strip_prefix("minecraft:").unwrap_or(raw));
    if id.is_empty() {
        return (String::new(), String::new());
    }
    let entry = catalog
        .structure_entries()
        .get(&strip(id).to_lowercase())
        .unwrap_or(&Value::Null);
    let label = field(entry, "label");
    let label = strip(&label);
    (
        id.into(),
        if label.is_empty() {
            structure_label(catalog, Some(id))
        } else {
            label.into()
        },
    )
}
pub fn climate_hint(world: &WorldCatalog, biome: Option<&str>) -> Result<String> {
    let climate = world.climate(biome)?;
    let entry = world.biome_entry(biome).unwrap_or(&Value::Null);
    let group = field(entry, "group_label");
    let band = group.replace("バイオーム", "");
    let band = strip(&band);
    let feel = climate
        .biome_temperature
        .map(|t| {
            if t >= 1.5 {
                "とても暑い"
            } else if t >= 1.0 {
                "暑い"
            } else if t >= 0.5 {
                "穏やか"
            } else if t >= 0.2 {
                "涼しい"
            } else if t >= 0.0 {
                "寒い"
            } else {
                "とても寒い"
            }
        })
        .unwrap_or("");
    Ok(
        if !feel.is_empty() && !band.is_empty() && !feel.contains(band) {
            format!("{feel}（{band}）")
        } else if !feel.is_empty() {
            feel.into()
        } else {
            band.into()
        },
    )
}
fn biome_reading_label(
    world: &WorldCatalog,
    biome: Option<&str>,
    readings: &ReadingSnapshot,
) -> String {
    let Some(entry) = world.biome_entry(biome) else {
        return world.biome_label(biome);
    };
    let raw = [
        field(entry, "label"),
        field(entry, "japanese"),
        biome_id(biome),
    ]
    .into_iter()
    .find(|s| !s.is_empty())
    .unwrap_or_default();
    let label = strip(&raw);
    let reading = readings.resolve(label, entry.get("reading").and_then(Value::as_str));
    match reading.filter(|r| r != label) {
        Some(reading) => format!("{label}（{reading}）"),
        None => label.into(),
    }
}
fn poetic_tags(catalog: &Catalog, id: &str) -> Vec<String> {
    let Some(entry) = catalog.mob_entry(id) else {
        return vec![];
    };
    let p = &entry["poetic"];
    if !p.is_object() {
        return vec![];
    }
    let mut values = vec![];
    for k in [
        "visual_tags",
        "sound_tags",
        "motion_tags",
        "scene_tags",
        "reaction_tags",
        "comic_tags",
    ] {
        values.extend(array_strings(&p[k]));
    }
    let role = field(p, "role");
    if !role.is_empty() {
        values.push(role);
    }
    unique(values)
}
fn poetic_line(catalog: &Catalog, id: &str) -> Option<String> {
    let entry = catalog.mob_entry(id)?;
    let p = &entry["poetic"];
    if !p.is_object() {
        return None;
    }
    let label = field(entry, "label");
    let label = if label.is_empty() { id } else { strip(&label) };
    if label.is_empty() {
        return None;
    }
    let role = field(p, "role");
    let role = strip(&role);
    let mut tags = vec![];
    for key in [
        "visual_tags",
        "sound_tags",
        "motion_tags",
        "comic_tags",
        "scene_tags",
        "reaction_tags",
    ] {
        if let Some(values) = p[key].as_array() {
            for v in values {
                let raw = if truth(v) { text(v) } else { String::new() };
                let tag = strip(&raw);
                if tag.is_empty() || tag == role || tags.iter().any(|s| s == tag) {
                    continue;
                }
                tags.push(tag.to_owned());
                if tags.len() >= 4 {
                    break;
                }
            }
        }
        if tags.len() >= 4 {
            break;
        }
    }
    if !role.is_empty() && !tags.is_empty() {
        Some(format!("{label}: {role}（{}）", tags.join("、")))
    } else if !role.is_empty() {
        Some(format!("{label}: {role}"))
    } else if !tags.is_empty() {
        Some(format!("{label}: {}", tags.join("、")))
    } else {
        None
    }
}
fn unique(values: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut out = vec![];
    for v in values {
        if !v.is_empty() && !out.contains(&v) {
            out.push(v);
        }
    }
    out
}
fn poetic_lines(
    event: &GameEvent,
    catalog: &Catalog,
    entries: &impl Entries,
) -> (Vec<String>, HashSet<String>) {
    let mut lines = vec![];
    let mut covered = HashSet::new();
    let mut labels = HashSet::new();
    for mob in &event.passive_mobs {
        let mut id = normalize_id(&mob.r#type);
        if id.is_empty() {
            id = strip(&mob.r#type).to_lowercase();
        }
        if id.is_empty() || covered.contains(&id) {
            continue;
        }
        let label = entries.mob_label(&mob.r#type);
        if !label.is_empty() && labels.contains(&label) {
            continue;
        }
        if let Some(line) = poetic_line(catalog, &mob.r#type) {
            lines.push(line);
            covered.insert(id);
            if !label.is_empty() {
                labels.insert(label);
            }
            if lines.len() >= 2 {
                break;
            }
        }
    }
    (lines, covered)
}
fn tags(
    event: &GameEvent,
    features: &[Feature],
    covered: &HashSet<String>,
    catalog: &Catalog,
) -> Vec<String> {
    let mut values: Vec<_> = features.iter().flat_map(|f| f.tags.clone()).collect();
    for mob in event.passive_mobs.iter().take(4) {
        let mut id = normalize_id(&mob.r#type);
        if id.is_empty() {
            id = strip(&mob.r#type).to_lowercase();
        }
        if !covered.contains(&id) {
            values.extend(poetic_tags(catalog, &mob.r#type));
        }
    }
    let covered_tags: HashSet<_> = covered
        .iter()
        .flat_map(|id| poetic_tags(catalog, id))
        .collect();
    unique(values.into_iter().filter(|t| !covered_tags.contains(t)))
        .into_iter()
        .take(if covered.is_empty() { 16 } else { 8 })
        .collect()
}
pub fn capture(
    event: &GameEvent,
    runtime: RuntimeRead<'_>,
    world: &WorldCatalog,
    catalog: &Catalog,
    entries: &impl Entries,
    readings: &ReadingSnapshot,
) -> Result<Context> {
    let environment = project_environment(event);
    let poem = poem_item(event, world, runtime.inventory_order)?;
    let inventory = inventory_values(event, world, entries);
    let nearby = nearby_blocks(event, world, entries);
    let dropped = dropped_items(event, world);
    let passive = unique(
        event
            .passive_mobs
            .iter()
            .map(|m| entries.mob_label(&m.r#type)),
    )
    .into_iter()
    .take(4)
    .collect::<Vec<_>>();
    let precipitation =
        precipitation::from_event(event, &world.climate(event.world.biome.as_deref())?)?;
    let (structure_id, structure_label) =
        structure_fields(event, runtime.current_structure, catalog);
    let hint = climate_hint(world, event.world.biome.as_deref())?;
    let biome_label = biome_reading_label(world, event.world.biome.as_deref(), readings);
    let biome_entry = world
        .biome_entry(event.world.biome.as_deref())
        .unwrap_or(&Value::Null);
    let group = field(biome_entry, "group_label");
    let group = if group.is_empty() {
        "不明".into()
    } else {
        group
    };
    let features = features(
        event,
        &poem,
        &inventory.items,
        &nearby,
        &dropped,
        &passive,
        &precipitation,
        &environment,
        &structure_label,
        &hint,
        &biome_label,
        &group,
        catalog,
        entries,
    );
    let (poetic_lines, covered) = poetic_lines(event, catalog, entries);
    let sources = catalog_sources(
        event,
        &poem,
        &inventory.items,
        environment.include_biome_context,
        &structure_id,
        &structure_label,
        &biome_label,
        world,
        catalog,
        entries,
    );
    let source_atoms = merge_source_atoms(&[
        atoms_from_catalog_sources(&sources, 8, 5),
        atoms_from_observations(
            &features
                .iter()
                .map(|f| json!({"source":f.source,"key":f.key,"label":f.label}))
                .collect::<Vec<_>>(),
        ),
    ]);
    let tensions = tensions(
        event,
        world,
        &world.item_label(event.player.held_item.as_deref()),
        &poem.label,
        &passive,
        &nearby,
        environment.include_biome_context,
        environment.include_sky_context,
    );
    let id = biome_id(event.world.biome.as_deref());
    let phase = phase(event);
    let weather = weather(event);
    Ok(Context {
        player_name: runtime.player_name.into(),
        biome_id: if id.is_empty() { "unknown".into() } else { id },
        biome_label,
        biome_group: group,
        biome_traits: if hint.is_empty() {
            vec![]
        } else {
            vec![hint.clone()]
        },
        time_phase: phase.into(),
        time_label: time_label(phase).into(),
        weather: weather.into(),
        weather_label: if precipitation.precipitation_kind == PrecipitationKind::Snow {
            "雪".into()
        } else {
            weather_label(weather).into()
        },
        precipitation_context: precipitation,
        poem_item_id: poem.id,
        held_item: poem.label,
        poem_item_source: poem.source,
        inventory_items: inventory.items,
        inventory_close_pair: inventory.close_pair,
        inventory_far_item: inventory.far_item,
        nearby_blocks: nearby,
        dropped_items: dropped,
        passive_mobs: passive,
        haiku_tags: tags(event, &features, &covered, catalog),
        feature_candidates: features,
        candidate_tensions: tensions,
        catalog_notes: catalog_notes_projection(&sources),
        catalog_sources: sources,
        source_atoms,
        poetic_lines,
        structure_id,
        structure_label,
        climate_hint: hint,
        include_biome_context: environment.include_biome_context,
        include_sky_context: environment.include_sky_context,
    })
}
#[allow(clippy::too_many_arguments)]
fn features(
    event: &GameEvent,
    poem: &PoemItem,
    inventory: &[String],
    nearby: &[String],
    dropped: &[String],
    passive: &[String],
    precipitation: &precipitation::PrecipitationContext,
    environment: &crate::environment::projection::EnvironmentProjection,
    structure: &str,
    hint: &str,
    biome_label: &str,
    group: &str,
    catalog: &Catalog,
    entries: &impl Entries,
) -> Vec<Feature> {
    let mut out = vec![];
    let mut add = |source: &str, key: String, label: String, tags: Vec<String>| {
        out.push(Feature {
            source: source.into(),
            key,
            label,
            tags,
        })
    };
    let portal = strip(event.world.nearby_portal_type.as_deref().unwrap_or("")).to_lowercase();
    if !portal.is_empty() {
        let label = match portal.as_str() {
            "nether_portal" => "ネザーポータル",
            "end_portal" => "エンドポータル",
            "end_gateway" => "エンドゲートウェイ",
            _ => &portal,
        };
        add(
            "ポータル",
            "portal".into(),
            label.into(),
            array_strings(&RULES["portal_tags"]),
        );
    }
    for (i, label) in nearby.iter().take(6).enumerate() {
        add("周辺", format!("nearby_{}", i + 1), label.clone(), vec![]);
    }
    for (i, label) in dropped.iter().take(4).enumerate() {
        add(
            "落下物",
            format!("dropped_{}", i + 1),
            label.clone(),
            vec![],
        );
    }
    if !poem.label.is_empty() {
        let (source, key) = if poem.source == "pocket" {
            ("持ち物", "pocket_item")
        } else {
            ("手持ち", "held_item")
        };
        add(source, key.into(), poem.label.clone(), vec![]);
    }
    let mut seen = HashSet::from([poem.label.clone()]);
    for (i, label) in inventory.iter().enumerate() {
        if !label.is_empty() && seen.insert(label.clone()) {
            add(
                "持ち物",
                format!("inventory_{}", i + 1),
                label.clone(),
                vec![],
            );
        }
    }
    for (i, label) in passive.iter().take(3).enumerate() {
        let id = event
            .passive_mobs
            .iter()
            .find(|m| entries.mob_label(&m.r#type) == *label)
            .map(|m| m.r#type.as_str())
            .unwrap_or("");
        add(
            "Mob",
            format!("mob_{}", i + 1),
            label.clone(),
            poetic_tags(catalog, id),
        );
    }
    let vehicle = crate::environment::ambient::player_vehicle_fact(event.player.vehicle.as_ref());
    if !vehicle.is_empty() {
        add("乗車", "vehicle_activity".into(), vehicle, vec![]);
    }
    if !environment.mining_label.is_empty() {
        add(
            if environment.mining_state == MiningState::Active {
                "行動"
            } else {
                "空間"
            },
            "mining_context".into(),
            environment.mining_label.clone(),
            ["地下", "採掘", "石", "土"].map(str::to_owned).to_vec(),
        );
    }
    if !structure.is_empty() {
        add("構造物", "structure".into(), structure.into(), vec![]);
        if !hint.is_empty() && environment.include_biome_context {
            add("気候", "climate".into(), hint.into(), vec![]);
        }
    } else if environment.include_biome_context {
        add("バイオーム", "biome".into(), biome_label.into(), vec![]);
        add("地帯", "biome_group".into(), group.into(), vec![]);
        if !hint.is_empty() {
            add("地形", "trait_1".into(), hint.into(), vec![]);
        }
    }
    if environment.include_sky_context {
        let snow = precipitation.precipitation_kind == PrecipitationKind::Snow;
        if snow {
            add(
                "降雪",
                "local_precipitation".into(),
                "現在は雪".into(),
                vec![],
            );
        }
        add(
            "天気",
            "weather".into(),
            if snow {
                "雪"
            } else {
                weather_label(weather(event))
            }
            .into(),
            vec![],
        );
        add(
            "時間",
            "time_phase".into(),
            time_label(phase(event)).into(),
            vec![],
        );
    }
    out.truncate(14);
    out
}
#[allow(clippy::too_many_arguments)]
fn catalog_sources(
    event: &GameEvent,
    poem: &PoemItem,
    inventory: &[String],
    include_biome: bool,
    structure_id: &str,
    structure_label: &str,
    biome_label: &str,
    world: &WorldCatalog,
    catalog: &Catalog,
    entries: &impl Entries,
) -> Vec<CatalogSourceSnapshot> {
    let mut sources = vec![];
    let mut seen = HashSet::new();
    let mut append = |kind: &str, id: &str, entry: Option<&Value>, role: &str, label: &str| {
        if let Some(source) =
            catalog_source_snapshot(kind, id, entry.unwrap_or(&Value::Null), role, label)
            && seen.insert(source.source_ref())
        {
            sources.push(source);
        }
    };
    let mut nearby: Vec<_> = event.nearby_resources.iter().collect();
    nearby.sort_by(|a, b| distance_cmp(a.distance, b.distance));
    for r in nearby.into_iter().take(6) {
        append(
            "block",
            &normalize_id(&r.name),
            entries.block_entry(&r.name),
            "nearby_block",
            &world.block_label(Some(&r.name)),
        );
    }
    let mut dropped: Vec<_> = event.dropped_items.iter().collect();
    dropped.sort_by(|a, b| distance_cmp(a.distance, b.distance));
    for d in dropped.into_iter().take(4) {
        append(
            "item",
            &normalize_id(&d.name),
            entries.item_entry(&d.name),
            "dropped_item",
            &world.item_label(Some(&d.name)),
        );
    }
    if !poem.id.is_empty() && poem.id != "air" {
        append(
            "item",
            &poem.id,
            entries.item_entry(&poem.id),
            "selected_item",
            &poem.label,
        );
    }
    for (id, count) in sorted_inventory(event) {
        let label = world.item_label(Some(id));
        if *count <= 0 || label.is_empty() || !inventory.contains(&label) {
            continue;
        }
        let normalized = normalize_id(id);
        append(
            "item",
            if normalized.is_empty() {
                id
            } else {
                &normalized
            },
            entries.item_entry(id),
            "inventory_item",
            &label,
        );
    }
    if !structure_id.is_empty() {
        append(
            "structure",
            structure_id,
            catalog
                .structure_entries()
                .get(&strip(structure_id).to_lowercase()),
            "current_structure",
            structure_label,
        );
    }
    if include_biome {
        append(
            "biome",
            &biome_id(event.world.biome.as_deref()),
            world.biome_entry(event.world.biome.as_deref()),
            "current_biome",
            biome_label,
        );
    }
    for m in event.passive_mobs.iter().take(3) {
        append(
            "mob",
            &normalize_id(&m.r#type),
            catalog.mob_entry(&m.r#type),
            "passive_mob",
            &entries.mob_label(&m.r#type),
        );
    }
    sources
}
#[allow(clippy::too_many_arguments)]
fn tensions(
    event: &GameEvent,
    world: &WorldCatalog,
    real_held: &str,
    poem_held: &str,
    passive: &[String],
    nearby: &[String],
    biome_visible: bool,
    sky_visible: bool,
) -> Vec<String> {
    let biome = biome_id(event.world.biome.as_deref());
    let label = world.biome_label(event.world.biome.as_deref());
    let entry = world
        .biome_entry(event.world.biome.as_deref())
        .unwrap_or(&Value::Null);
    let group = field(entry, "group_id");
    let mut out = vec![];
    if biome_visible
        && sky_visible
        && group == "dry"
        && ["rain", "thunder"].contains(&weather(event))
    {
        out.push("乾いた土地やのに空だけ荒れとる".into());
    }
    if biome_visible
        && group == "dry"
        && passive
            .iter()
            .any(|v| ["熱帯魚", "イカ", "フグ", "サケ", "タラ"].contains(&v.as_str()))
    {
        out.push(format!("{label}なのに水のいきものがおる"));
    }
    if biome_visible && passive.iter().any(|v| v == "熱帯魚") && !biome.contains("ocean") {
        out.push("海やないのに熱帯魚がおる".into());
    }
    if biome_visible
        && passive.iter().any(|v| v == "ヒツジ")
        && !["plains", "savanna", "meadow"].contains(&biome.as_str())
    {
        out.push(format!("{label}やのにヒツジがのんびりしとる"));
    }
    if biome_visible
        && nearby.iter().any(|v| v == "シラカバの葉")
        && !biome.starts_with("birch_")
        && biome != "old_growth_birch_forest"
    {
        out.push(format!("{label}やのにシラカバの気配がある"));
    }
    let deep = event.player.position.y.is_some_and(|y| y <= 16.0);
    if deep {
        out.push("深い地下でダイヤを夢みとる".into());
        let held = if real_held.is_empty() {
            poem_held
        } else {
            real_held
        };
        if !held.is_empty() {
            out.push(format!("深い地下なのに手には{held}がある"));
        }
    }
    if biome_visible && biome == "mushroom_fields" {
        out.push("安全すぎて逆に妙や".into());
    }
    if sky_visible && phase(event) == "night" && !passive.is_empty() {
        out.push("夜やのにのどかな気配が残っとる".into());
    }
    if sky_visible && phase(event) == "day" && deep {
        out.push("昼やのに地の底みたいや".into());
    }
    unique(out).into_iter().take(8).collect()
}
