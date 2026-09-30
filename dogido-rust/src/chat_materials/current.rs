use super::*;
use crate::chat_observation::{self, Labels, PassiveSighting};
use crate::events::{HorizontalDirection, TimePhase, Weather};
use crate::world_catalog;
use serde_json::json;
pub(super) fn unique<'a>(groups: impl IntoIterator<Item = &'a str>) -> Vec<String> {
    let mut result = vec![];
    for raw in groups {
        let value = chat_catalog::normalized_observation_id(raw);
        if !value.is_empty() && !result.contains(&value) {
            result.push(value)
        }
    }
    result
}
pub(super) fn overworld(event: &GameEvent) -> bool {
    matches!(
        chat_catalog::strip(event.player.dimension.as_deref().unwrap_or(""))
            .to_lowercase()
            .as_str(),
        "" | "overworld" | "minecraft:overworld"
    )
}
pub(super) fn evening(event: &GameEvent) -> bool {
    overworld(event) && event.world.time_phase == Some(TimePhase::Evening)
}
pub(super) fn safety(event: &GameEvent) -> &'static str {
    let biome = chat_catalog::strip(event.world.biome.as_deref().unwrap_or("")).to_lowercase();
    if !overworld(event)
        || matches!(
            biome.as_str(),
            "dark_forest" | "mushroom_fields" | "pale_garden"
        )
        || event.world.sky_visible != Some(true)
        || event.world.is_submerged == Some(true)
        || biome == "deep_dark"
        || biome.ends_with("_caves")
        || crate::environment::danger::safe_zone_with_door(event)
    {
        return "none";
    }
    if evening(event) || event.world.weather == Some(Weather::Thunder) {
        "seek_safe_place"
    } else {
        "none"
    }
}
pub(super) fn call_name(event: &GameEvent, default: &str) -> String {
    [
        event.meta.call_name.as_deref().unwrap_or(""),
        default,
        event.player.name.as_deref().unwrap_or(""),
    ]
    .into_iter()
    .map(chat_catalog::strip)
    .find(|v| !v.is_empty())
    .unwrap_or("プレイヤー")
    .into()
}
pub(super) fn observed(
    event: &GameEvent,
    ids: &[String],
    structure: Option<&str>,
) -> Vec<handoff::Observation> {
    let mut candidates = ids.to_vec();
    if let Some(vehicle) = &event.player.vehicle
        && !vehicle.vehicle_id.is_empty()
    {
        candidates.push(vehicle.vehicle_id.clone());
    }
    if let Some(id) = structure.filter(|v| !v.is_empty()) {
        candidates.push(id.into())
    }
    if let Some(look) = &event.look_target
        && look.kind == "entity"
        && !look.name.is_empty()
    {
        candidates.push(look.name.clone());
    }
    let catalog = chat_catalog::catalog();
    unique(candidates.iter().map(String::as_str))
        .into_iter()
        .map(|id| {
            let entry = catalog
                .mob_entry(&id)
                .filter(|v| chat_catalog::truth(v))
                .or_else(|| catalog.structure_entries().get(&id));
            let label = entry
                .and_then(|e| e.get("label"))
                .filter(|v| chat_catalog::truth(v))
                .map(chat_catalog::text)
                .unwrap_or_else(|| id.clone());
            handoff::Observation {
                entity_id: id,
                label: chat_catalog::strip(&label).into(),
            }
        })
        .collect()
}
fn direction(horizontal: Option<HorizontalDirection>) -> &'static str {
    match horizontal {
        Some(HorizontalDirection::Front) => "前",
        Some(HorizontalDirection::FrontRight) => "右前",
        Some(HorizontalDirection::Right) => "右",
        Some(HorizontalDirection::BackRight) => "右後ろ",
        Some(HorizontalDirection::Back) => "後ろ",
        Some(HorizontalDirection::BackLeft) => "左後ろ",
        Some(HorizontalDirection::Left) => "左",
        Some(HorizontalDirection::FrontLeft) => "左前",
        None => "近く",
    }
}
pub(super) fn audio_fallback(event: &GameEvent, labels: &impl Labels) -> Option<String> {
    let audio = event.auditory_threats.first()?;
    let id = chat_observation::resolve_mob(&audio.label, audio.sound_event.as_deref(), labels);
    let label = chat_observation::mob_label(id.as_deref(), labels);
    let band = serde_json::to_value(audio.distance_band)
        .ok()?
        .as_str()
        .unwrap_or("")
        .to_owned();
    let dir = direction(audio.direction.horizontal);
    Some(
        match label {
            Some(name) => format!("音 {name} {dir} {band}"),
            None => format!("音（種別未確定） {dir} {band}"),
        }
        .trim()
        .into(),
    )
}
pub(super) fn threat(
    event: &GameEvent,
    snapshot: &Snapshot,
    audio_fallback: Option<&str>,
    hearing: Option<&str>,
) -> String {
    let mut parts = vec![];
    if let Some(nearest) = event.visual_threats.iter().reduce(|a, b| {
        if b.distance.unwrap_or(999.) < a.distance.unwrap_or(999.) {
            b
        } else {
            a
        }
    }) {
        let distance = nearest
            .distance
            .map(|v| format!("{v:.0}マス"))
            .unwrap_or_else(|| "近く".into());
        let label = crate::combat::catalog::labels()
            .get(&nearest.r#type)
            .map(chat_catalog::text)
            .unwrap_or_else(|| nearest.r#type.clone());
        parts.push(format!(
            "視認 {label} が{} {distance}",
            crate::threats::direction(nearest)
        ));
        parts.push(format!("視認リスト{}体", event.visual_threats.len()));
    } else if let Some(recent) = snapshot
        .recent
        .visual_summary
        .as_deref()
        .filter(|v| !v.is_empty())
    {
        parts.push(recent.into())
    } else if hearing.is_none()
        && let Some(audio) = audio_fallback
    {
        parts.push(audio.into());
    }
    if let Some(hearing) = hearing.map(chat_catalog::strip).filter(|v| !v.is_empty()) {
        parts.push(format!("音メモ: {hearing}"))
    }
    if event.combat.combat_active_hint == Some(true) && !parts.is_empty() {
        parts.push("交戦中っぽい".into())
    }
    parts.join("、")
}
pub(super) fn observation(
    event: &GameEvent,
    threat: &str,
    hearing: &str,
    passive: &[String],
    look: &str,
    recent: &[PassiveSighting],
) -> String {
    let catalog = chat_catalog::catalog();
    let mut lines = vec![];
    let look = chat_catalog::strip(look);
    if !look.is_empty() {
        lines.push(format!("視線先: {look}"))
    }
    let vehicle = crate::environment::ambient::player_vehicle_fact(event.player.vehicle.as_ref());
    if !vehicle.is_empty() {
        lines.push(vehicle)
    }
    let threat = chat_catalog::strip(threat);
    if !threat.is_empty() && threat != "とくになし" {
        lines.push(format!("脅威: {threat}"))
    }
    let label = |id: &str| {
        catalog
            .mob_entry(id)
            .and_then(|e| e.get("label"))
            .filter(|v| chat_catalog::truth(v))
            .map(chat_catalog::text)
            .unwrap_or_else(|| id.into())
    };
    let mut names = vec![];
    for id in passive {
        let name = label(id);
        if !name.is_empty() && !names.contains(&name) {
            names.push(name)
        }
        if names.len() >= 4 {
            break;
        }
    }
    if !names.is_empty() {
        lines.push(format!("近くの生き物: {}", names.join("、")))
    }
    if !recent.is_empty() {
        lines.push(format!(
            "過去の視認（現在の在否は未確認）: {}",
            recent
                .iter()
                .map(|r| format!("{}（{}秒前）", label(&r.mob_type), r.seconds_ago))
                .collect::<Vec<_>>()
                .join("、")
        ))
    }
    let hearing = chat_catalog::strip(hearing);
    if !hearing.is_empty() {
        lines.push(format!("音: {hearing}"))
    }
    lines
        .into_iter()
        .take(4)
        .map(|line| format!("- {line}"))
        .collect::<Vec<_>>()
        .join("\n")
}
pub(super) fn frame_details(
    event: &GameEvent,
    settings: &Settings,
    ctx: &Context,
    input: &PlayerInput,
    snapshot: &Snapshot,
    travel: &travel::Travel,
    flags: (bool, bool, bool),
) -> Result<Value> {
    let (combat, visual, dark) = flags;
    let c = world_catalog::catalog();
    let env = crate::environment::projection::project_environment(event);
    let climate = c.climate(event.world.biome.as_deref())?;
    let precipitation = crate::environment::precipitation::from_event(event, &climate)?;
    let place = crate::chat_world::place_context(
        c,
        chat_catalog::catalog(),
        event,
        ctx.current_structure.as_deref(),
        settings.home_bed_prompt_distance,
    )?;
    let inv = input.asks_inventory;
    let mut out = json!({
        "player_name": call_name(event,&settings.default_call_name),
        "user_text": chat_catalog::strip(&input.semantic_text).chars().take(160).collect::<String>(),
        "biome": place.biome_label,
        "structure_label": ctx.current_structure.as_deref().filter(|v|!v.is_empty()).map(|v|world_catalog::structure_label(chat_catalog::catalog(),Some(v))).unwrap_or_default(),
        "place_context": place.place_line,
        "space_kind": place.space_kind,
        "sky_visible": place.sky_visible,
        "include_biome_context": !place.biome_label.is_empty(),
        "include_sky_context": env.include_sky_context,
        "time_phase": if env.include_sky_context{serde_json::to_value(event.world.time_phase)?.as_str().unwrap_or("").to_owned()}else{String::new()},
        "safety_priority": safety(event),
        "player_turn_plan": travel.action,
        "player_turn_plan_evidence": travel.evidence,
        "home_progress": if travel.action=="return_home"{serde_json::to_value(snapshot.home.progress)?}else{json!("unknown")},
        "weather": if env.include_sky_context{serde_json::to_value(event.world.weather)?.as_str().unwrap_or("").to_owned()}else{String::new()},
        "weather_label": if env.include_sky_context{crate::chat_world::weather_label(event,&climate)?}else{String::new()},
        "weather_fact": if env.include_sky_context{crate::chat_world::weather_fact(event)}else{""},
        "mode": ctx.mode,
        "combat_active": combat,
        "has_visual_threats": visual,
        "danger_darkness_high": dark,
        "asks_about_sound": input.asks_about_sound,
        "asks_inventory": inv,
        "inventory_summary": if inv{crate::chat_world::inventory_summary(c,&event.inventory,18)}else{String::new()},
        "held_item_label": if inv{c.item_label(event.player.held_item.as_deref())}else{String::new()},
        "conversation_history": ctx.history.conversation_history,
        "conversation_turns": ctx.history.conversation_turns,
        "event_digest": ctx.history.event_digest
    });
    out["character_mode"] = json!(crate::chat_prompt::mode(&out));
    if env.include_sky_context {
        out.as_object_mut().unwrap().extend(
            serde_json::to_value(precipitation.to_prompt_details())?
                .as_object()
                .unwrap()
                .clone(),
        );
    }
    if let Some(workshop) = &ctx.workshop_details {
        out.as_object_mut()
            .unwrap()
            .extend(serde_json::to_value(workshop)?.as_object().unwrap().clone());
    }
    Ok(out)
}
