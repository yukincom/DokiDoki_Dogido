//! Exact catalogue descriptions for identified subjects, kept apart from observations.
//! This module neither selects speech nor treats descriptive tags as live world facts.
use crate::{chat_catalog, entry_catalog, events::GameEvent, world_catalog};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

const USAGE: &str = "対象の一般的な特徴と、作者が用意した表現の材料。説明文・特徴タグ・役割は自然な会話の参考にする。今回見えた姿、聞こえた音、実際の動き、在否は観測に従い、音だけの対象を目撃したことにはしない。場面・感情・比喩のタグは演技の命令ではない。現在地でのポータルの行き先は、今回渡されたportal_roleを優先する。";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub kind: String,
    pub id: String,
    pub label: String,
    pub basis: Vec<String>,
    pub general: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Knowledge {
    pub usage: String,
    pub entries: Vec<Entry>,
}
impl Default for Knowledge {
    fn default() -> Self {
        Self {
            usage: USAGE.into(),
            entries: vec![],
        }
    }
}
impl Knowledge {
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    fn add(&mut self, entry: Option<Entry>) {
        let Some(entry) = entry else {
            return;
        };
        if let Some(existing) = self.entries.iter_mut().find(|row| {
            row.kind == entry.kind
                && row.id == entry.id
                && row.label == entry.label
                && row.general == entry.general
        }) {
            for basis in entry.basis {
                if !existing.basis.contains(&basis) {
                    existing.basis.push(basis);
                }
            }
        } else {
            self.entries.push(entry);
        }
    }

    pub fn extend(&mut self, other: Self) {
        for entry in other.entries {
            self.add(Some(entry));
        }
    }
}

fn id(raw: &str) -> Option<String> {
    let raw = raw.trim().to_lowercase();
    let key = raw.strip_prefix("minecraft:").unwrap_or(&raw);
    (!key.is_empty() && !key.contains(':')).then(|| key.to_owned())
}

/// Preserve authored text and every descriptive tag; only non-prose routing and
/// dictionary metadata are omitted. Climate numbers stay in the precipitation code.
fn prose(raw: &Value) -> Value {
    let Some(fields) = raw.as_object() else {
        return json!({});
    };
    Value::Object(
        fields
            .iter()
            .filter(|(key, _)| {
                !matches!(
                    key.as_str(),
                    "label"
                        | "japanese"
                        | "reading"
                        | "section"
                        | "group_path"
                        | "source"
                        | "refs"
                        | "priority"
                        | "threat_class"
                        | "observed_speech_aliases"
                        | "observed_speech_rewrite_from_ids"
                        | "professions"
                        | "baby"
                        | "dogido_tactics"
                        | "temperature"
                        | "downfall"
                        | "snow_starts_at_y"
                )
            })
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    )
}

fn entry(kind: &str, id: &str, label: &str, basis: &str, general: Value) -> Entry {
    Entry {
        kind: kind.into(),
        id: id.into(),
        label: label.into(),
        basis: vec![basis.into()],
        general,
    }
}

pub fn mob(reference: &str, profession: Option<&str>, baby: bool, basis: &str) -> Option<Entry> {
    let key = id(reference)?;
    let entries = chat_catalog::catalog().all_mob_entries();
    // Callers supply a code-observed ID or an exact, unambiguous catalogue label.
    // No free-text/tag search or fuzzy identification is performed here.
    let (key, base) = if let Some(raw) = entries.get(&key) {
        (key, raw)
    } else {
        let mut matches = entries
            .iter()
            .filter(|(_, raw)| raw["label"].as_str() == Some(reference.trim()));
        let (key, raw) = matches.next()?;
        if matches.next().is_some() {
            return None;
        }
        (key.clone(), raw)
    };
    let mut raw = base.clone();
    if key == "villager" {
        let profession = profession.and_then(id).unwrap_or_default();
        let overlay = if baby {
            &base["baby"]
        } else {
            &base["professions"][profession]
        };
        if let Some(overlay) = overlay.as_object() {
            for (key, value) in overlay {
                if key == "poetic" {
                    if let Some(tags) = value.as_object() {
                        for (tag, value) in tags {
                            raw["poetic"][tag] = value.clone();
                        }
                    }
                } else {
                    raw[key] = value.clone();
                }
            }
        }
    }
    Some(entry(
        "mob",
        &key,
        raw["label"].as_str().unwrap_or(&key),
        basis,
        prose(&raw),
    ))
}

fn thing(kind: &str, raw: &str, basis: &str) -> Option<Entry> {
    let key = id(raw)?;
    let catalog = world_catalog::catalog();
    // Portal blocks live in the existing command-only catalogue. They are still
    // world blocks; the dictionary's storage category must not discard their note.
    let raw = if kind == "block" {
        catalog
            .block_entry(&key)
            .or_else(|| catalog.item_entry(&key))
    } else {
        catalog
            .item_entry(&key)
            .or_else(|| catalog.block_entry(&key))
    }?;
    Some(entry(
        kind,
        &key,
        raw["label"].as_str().unwrap_or(&key),
        basis,
        prose(raw),
    ))
}

fn place(kind: &str, raw: &str, basis: &str) -> Option<Entry> {
    let key = id(raw)?;
    let (document, child) = if kind == "biome" {
        (&*entry_catalog::BIOMES, "biomes")
    } else {
        (&*entry_catalog::STRUCTURES, "structures")
    };
    let (group_id, group) = document["groups"]
        .as_object()?
        .iter()
        .find(|(_, group)| group[child].get(&key).is_some())?;
    let raw = &group[child][&key];
    let mut group = group.clone();
    group.as_object_mut()?.remove(child);
    let mut general = prose(raw);
    general["group"] = json!({"id":group_id,"label":group["label"],"description":prose(&group)});
    Some(entry(
        kind,
        &key,
        raw["japanese"].as_str().unwrap_or(&key),
        basis,
        general,
    ))
}

fn ominous(kind: &str) -> Option<Entry> {
    match kind {
        "sculk_sensor" | "sculk_shrieker" => thing("block", kind, "heard"),
        "warden_heartbeat" | "warden_presence" => mob("warden", None, false, "heard"),
        _ => None,
    }
}

/// Shared context for ordinary replies and spontaneous reactions. Only subjects
/// in this accepted frame are looked up; related mobs mentioned inside a note
/// never become additional observations or lookup targets.
pub fn for_event(event: &GameEvent, structure: Option<&str>) -> Knowledge {
    let mut out = Knowledge::default();
    if let Some(target) = &event.look_target {
        out.add(if target.kind == "entity" {
            let observed = target.identity.as_ref().and_then(|identity| {
                event.passive_mobs.iter().find(|mob| {
                    mob.identity
                        .as_ref()
                        .is_some_and(|other| other.entity_id == identity.entity_id)
                        && id(&mob.r#type) == id(&target.name)
                })
            });
            mob(
                &target.name,
                observed.and_then(|m| m.profession.as_deref()),
                observed.is_some_and(|m| m.is_baby == Some(true)),
                "look_target",
            )
        } else {
            thing("block", &target.name, "look_target")
        });
    }
    if let Some(portal) = &event.world.nearby_portal_type {
        out.add(thing("block", portal, "nearby_portal"));
    }
    if let Some(kind) = &event.world.ominous_sound_kind {
        out.add(ominous(kind));
    }
    for target in &event.passive_mobs {
        out.add(mob(
            &target.r#type,
            target.profession.as_deref(),
            target.is_baby == Some(true),
            "passive_observation",
        ));
    }
    for target in &event.visual_threats {
        out.add(mob(&target.r#type, None, false, "visual"));
    }
    for target in &event.auditory_threats {
        if target.spoken_name_allowed {
            out.add(mob(&target.label, None, false, "heard"));
        }
    }
    for target in &event.ambient_sounds {
        out.add(mob(&target.r#type, None, false, "heard"));
    }
    if let Some(key) = structure.or(event.world.structure.as_deref()) {
        out.add(place("structure", key, "current_structure"));
    }
    if let Some(key) = &event.world.biome {
        out.add(place("biome", key, "location_biome"));
    }
    if let Some(key) = &event.player.held_item {
        out.add(thing("item", key, "player_held_item"));
    }
    if let Some(vehicle) = &event.player.vehicle {
        out.add(
            mob(&vehicle.vehicle_id, None, false, "player_riding")
                .or_else(|| thing("item", &vehicle.vehicle_id, "player_riding")),
        );
    }
    for resource in &event.nearby_resources {
        out.add(thing("block", &resource.name, "nearby_resource"));
    }
    out
}

/// Include the selected event's subject even when the latest shared frame has
/// moved on (for example, a defeated enemy). Basis identifies this as the event
/// target, not fresh presence. Existing event applicability remains code-owned.
pub fn for_reaction(kind: &str, details: &Value) -> Knowledge {
    let mut out = Knowledge::default();
    match kind {
        "portal_appearance" => {
            out.add(
                details["portal_type"]
                    .as_str()
                    .and_then(|id| thing("block", id, "portal_observation")),
            );
        }
        "deep_dark_ominous_sound" => {
            out.add(details["ominous_kind"].as_str().and_then(ominous));
        }
        "ambient" => {
            let reference = details["__ambient_guard"]["mob_type"]
                .as_str()
                .or_else(|| details["mob"].as_str());
            out.add(reference.and_then(|id| {
                mob(
                    id,
                    details["mob_profession"].as_str(),
                    details["mob_is_baby"] == true,
                    "reaction_target",
                )
            }));
        }
        "death" | "newly_burning_visual" | "occluded_hostile_presence" => {
            let basis = if kind == "occluded_hostile_presence" {
                "heard"
            } else {
                "reaction_target"
            };
            out.add(
                details["hostile"]
                    .as_str()
                    .and_then(|id| mob(id, None, false, basis)),
            );
        }
        "aftermath"
        | "daylight_water"
        | "daylight_water_skeleton"
        | "darkness_escape"
        | "dark_push_no_light"
        | "dark_push_after_breath" => {
            if let Some(targets) = details["hostiles"].as_array() {
                for target in targets {
                    out.add(
                        target
                            .as_str()
                            .and_then(|id| mob(id, None, false, "reaction_target")),
                    );
                }
            }
        }
        "structure_entry" => {
            out.add(
                details["structure"]
                    .as_str()
                    .and_then(|id| place("structure", id, "reaction_target")),
            );
        }
        "ender_eye_throw" => out.add(thing("item", "ender_eye", "player_action")),
        _ => {}
    }
    out
}

/// Render the dictionary beside current observations, without duplicating it or
/// relabelling it as something just seen/heard. The Session's source is cloned.
pub fn separate(context: &mut serde_json::Map<String, Value>) {
    let knowledge = context
        .get_mut("world_context")
        .and_then(Value::as_object_mut)
        .and_then(|world| world.shift_remove("catalog_knowledge"));
    if let Some(knowledge) = knowledge.filter(|value| !value.is_null()) {
        context.insert("catalog_knowledge".into(), knowledge);
    }
}

#[cfg(test)]
mod tests;
