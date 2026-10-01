//! Session-owned observation/history projection. Never observes a merged frame.
use super::Session;
use crate::{
    chat_materials::{CompletedHistory, Context, Settings},
    chat_observation::{self, ChatObservationMemory, Labels, Snapshot},
    events::GameEvent,
    haiku::materials::Entries,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub(super) struct CatalogLabels;
impl Labels for CatalogLabels {
    fn mob_label(&self, id: &str) -> Option<&str> {
        crate::chat_catalog::catalog()
            .mob_entry(id)
            .and_then(|entry| entry.get("label"))
            .and_then(Value::as_str)
    }
    fn mob_fallback_label(&self, id: &str) -> Option<&str> {
        // MOB_VOICE_LABELS: passive, then hostile, then neutral; exact raw ID.
        [
            &*crate::entry_catalog::NEUTRAL,
            &*crate::entry_catalog::HOSTILE,
            &*crate::entry_catalog::PASSIVE,
        ]
        .into_iter()
        .find_map(|doc| {
            doc["items"]
                .get(id)
                .and_then(|entry| entry.get("label"))
                .and_then(Value::as_str)
        })
    }
    fn hostile_label(&self, id: &str) -> String {
        // HOSTILE_LABELS.get(raw, raw), unlike combat::model::label's namespace stripping.
        crate::combat::catalog::labels()
            .get(id)
            .and_then(Value::as_str)
            .unwrap_or(id)
            .into()
    }
    fn block_label(&self, id: &str) -> Option<&str> {
        let entry = crate::world_catalog::catalog().block_entry(id)?;
        entry
            .get("label")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .or_else(|| entry.get("japanese").and_then(Value::as_str))
    }
}

pub(super) fn memory(settings: &crate::combat::model::Settings) -> ChatObservationMemory {
    ChatObservationMemory::new(chat_observation::Settings {
        player_chat_visual_retention_ms: settings
            .ms("player_chat_visual_retention_ms")
            .min(i64::MAX as u64) as i64,
        player_chat_hearing_retention_ms: settings
            .ms("player_chat_hearing_retention_ms")
            .min(i64::MAX as u64) as i64,
        player_chat_name_correction_retention_ms: settings
            .ms("player_chat_name_correction_retention_ms")
            .min(i64::MAX as u64) as i64,
        weather_sound_recent_ms: settings.ms("weather_sound_recent_ms").min(i64::MAX as u64) as i64,
        home_bed_prompt_distance: settings.number("home_bed_prompt_distance"),
    })
}

pub(super) fn update_haiku(
    s: &mut Session,
    event: &GameEvent,
    settings: &crate::combat::model::Settings,
) {
    let player_name = [
        event.meta.call_name.as_deref().unwrap_or(""),
        settings.text("default_call_name"),
        event.player.name.as_deref().unwrap_or(""),
        "プレイヤー",
    ]
    .into_iter()
    .map(crate::chat_catalog::strip)
    .find(|s| !s.is_empty())
    .unwrap()
    .to_owned();
    s.haiku_context = crate::haiku::preparation::RuntimeSnapshot {
        current_structure: s.ambient.current_structure().map(str::to_owned),
        inventory_order: event.inventory_order().to_vec(),
        player_name,
    };
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Native {
    pub snapshot: Snapshot,
    pub context: Context,
    pub settings: Settings,
}

pub(super) fn capture(
    s: &Session,
    event: &GameEvent,
    settings: &crate::combat::model::Settings,
    history: CompletedHistory,
    workshop: Option<&Value>,
) -> anyhow::Result<Native> {
    Ok(Native {
        snapshot: s.chat_observation.snapshot(event, &CatalogLabels)?,
        context: Context {
            mode: serde_json::to_value(s.mode)
                .expect("mode enum")
                .as_str()
                .unwrap()
                .into(),
            current_structure: s.ambient.current_structure().map(str::to_owned),
            history,
            workshop_open: workshop.is_some(),
            workshop_details: workshop.map(workshop_details).transpose()?,
        },
        settings: Settings {
            darkness_alert_threshold: settings.number("darkness_alert_threshold"),
            home_bed_prompt_distance: settings.number("home_bed_prompt_distance"),
            default_call_name: settings.text("default_call_name").into(),
        },
    })
}

/// Exact small speech projection from the same view passed to the workshop job.
/// `Workshop::open` already enriches its immutable materials, but legacy views
/// retain the canonical metadata fallback here as well.
fn workshop_details(view: &Value) -> anyhow::Result<crate::chat_materials::WorkshopFields> {
    use crate::workshop_editing::materials::{material_context_visible, short_material_entries};
    let snapshot = crate::workshop_projection::Snapshot::from_view(view)?;
    let mut materials = snapshot.materials;
    for (key, visibility) in [
        ("interpretation", None),
        ("biome", Some("biome")),
        ("structure", None),
        ("time_phase", Some("sky")),
    ] {
        let value = &view["emission"][key];
        if crate::chat_catalog::truth(value)
            && materials.get(key).is_none()
            && visibility.is_none_or(|key| material_context_visible(&materials, key))
        {
            materials[key] = value.clone();
        }
    }
    let material = short_material_entries(&materials)
        .into_iter()
        .min_by_key(|(label, source)| {
            let rank = match source.as_str() {
                "held_item" => 0,
                "nearby_block" => 1,
                "inventory_item" => 2,
                "structure" => 3,
                "motif" => 4,
                "passive_mob" => 5,
                "biome" => 6,
                "place" => 7,
                "time_phase" => 8,
                _ => 9,
            };
            let len = label.chars().count();
            (rank, len <= 2, len > 20, len, label.clone())
        })
        .map(|(label, _)| label)
        .unwrap_or_default();
    let reading = snapshot
        .pending_revision
        .as_deref()
        .filter(|s| !s.is_empty())
        .unwrap_or(&snapshot.surface_text);
    Ok(crate::chat_materials::WorkshopFields {
        haiku_workshop_open: "1".into(),
        haiku_workshop_text: crate::chat_catalog::strip(reading).into(),
        haiku_workshop_materials: material,
    })
}

#[cfg(test)]
mod tests;
