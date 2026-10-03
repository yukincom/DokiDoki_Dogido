//! Current per-individual measurements paired with catalogue species IDs.
//! Knowledge never establishes a measurement, elapsed duration, death or conversion.
use crate::events::{GameEvent, MobEnvironment, MobIdentity, VisualThreat};
use serde_json::{Value, json};

struct Observation<'a> {
    species: &'a str,
    identity: Option<&'a MobIdentity>,
    fallback_id: Option<&'a str>,
    environment: Option<&'a MobEnvironment>,
    legacy_water: bool,
    legacy_fire: bool,
    basis: &'a str,
    approaching: Option<bool>,
}

fn add(rows: &mut Vec<Value>, observation: Observation<'_>) {
    let Observation {
        species,
        identity,
        fallback_id,
        environment,
        legacy_water,
        legacy_fire,
        basis,
        approaching,
    } = observation;
    let Some(catalog) = crate::catalog_knowledge::mob(species, None, false, basis) else {
        return;
    };
    let mut state = serde_json::Map::new();
    if let Some(environment) = environment {
        for (key, value) in [
            ("touching_water", environment.touching_water),
            ("submerged_in_water", environment.submerged_in_water),
            ("touching_water_or_rain", environment.touching_water_or_rain),
            ("on_ground", environment.on_ground),
            ("on_fire", environment.on_fire),
        ] {
            if let Some(value) = value {
                state.insert(key.into(), value.into());
            }
        }
    }
    // Legacy false is also the wire default: it cannot establish dry land.
    if environment.is_none() {
        if legacy_water {
            state.entry("touching_water").or_insert(true.into());
        }
        if legacy_fire {
            state.entry("on_fire").or_insert(true.into());
        }
    }
    if state.is_empty() {
        return;
    }
    let medium = match (state.get("touching_water"), state.get("on_ground")) {
        (Some(Value::Bool(true)), _) => "水に触れている",
        (Some(Value::Bool(false)), Some(Value::Bool(true))) => "水に触れず地面にいる",
        (Some(Value::Bool(false)), _) => "水の外にいる（地面にいるかは別の観測）",
        _ => "水中か陸上かは未確認",
    };
    let subject = identity
        .map(|v| v.entity_id.as_str())
        .or(fallback_id)
        .filter(|s| !s.is_empty());
    let mut row = json!({"catalog_id":catalog.id,"label":catalog.label,"basis":basis,
        "environment":state,"medium":medium});
    if let Some(subject) = subject {
        row["subject"] = subject.into();
    }
    if let Some(approaching) = approaching {
        row["approaching"] = approaching.into();
    }
    if let Some(subject) = subject
        && let Some(previous) = rows
            .iter_mut()
            .find(|other| other["subject"] == subject && other["catalog_id"] == row["catalog_id"])
    {
        // Crosshair is sampled after the scan; retain its newer measurements.
        if row.get("approaching").is_none() && previous.get("approaching").is_some() {
            row["approaching"] = previous["approaching"].clone();
        }
        *previous = row;
    } else {
        rows.push(row);
    }
}

pub fn project(event: &GameEvent) -> Value {
    let mut rows = vec![];
    for target in &event.visual_threats {
        add(
            &mut rows,
            Observation {
                species: &target.r#type,
                identity: target.identity.as_ref(),
                fallback_id: target.entity_id.as_deref(),
                environment: target.environment.as_ref(),
                legacy_water: target.in_water,
                legacy_fire: target.on_fire,
                basis: "visual",
                approaching: Some(target.approaching),
            },
        );
    }
    for target in &event.passive_mobs {
        add(
            &mut rows,
            Observation {
                species: &target.r#type,
                identity: target.identity.as_ref(),
                fallback_id: None,
                environment: target.environment.as_ref(),
                legacy_water: false,
                legacy_fire: false,
                basis: "passive_observation",
                approaching: None,
            },
        );
    }
    if let Some(target) = &event.look_target
        && target.kind == "entity"
    {
        add(
            &mut rows,
            Observation {
                species: &target.name,
                identity: target.identity.as_ref(),
                fallback_id: None,
                environment: target.environment.as_ref(),
                legacy_water: false,
                legacy_fire: false,
                basis: "look_target",
                approaching: None,
            },
        );
    }
    rows.into()
}

pub fn visual(target: &VisualThreat) -> Option<Value> {
    let mut rows = vec![];
    add(
        &mut rows,
        Observation {
            species: &target.r#type,
            identity: target.identity.as_ref(),
            fallback_id: target.entity_id.as_deref(),
            environment: target.environment.as_ref(),
            legacy_water: target.in_water,
            legacy_fire: target.on_fire,
            basis: "visual",
            approaching: Some(target.approaching),
        },
    );
    rows.pop()
}

pub fn burns_in_daylight(species: &str) -> bool {
    let normalized = species.trim().to_lowercase();
    let key = normalized.strip_prefix("minecraft:").unwrap_or(&normalized);
    crate::chat_catalog::catalog()
        .all_mob_entries()
        .get(key)
        .is_some_and(|entry| entry["burns_in_daylight"] == true)
}

fn same_subject(a: &Value, b: &Value) -> bool {
    a["subject"].as_str().is_some_and(|s| !s.is_empty())
        && a["subject"] == b["subject"]
        && a["catalog_id"] == b["catalog_id"]
}

pub(crate) fn still_observed(old: &Value, current: &Value) -> bool {
    current
        .as_array()
        .is_some_and(|rows| rows.iter().any(|row| same_subject(old, row)))
}

pub(crate) fn changes(before: &Value, now: &Value) -> Vec<(Value, Value)> {
    let (Some(before), Some(now)) = (before.as_array(), now.as_array()) else {
        return vec![];
    };
    now.iter()
        .filter_map(|row| {
            let previous = before.iter().find(|old| same_subject(old, row))?;
            // Jumping or slight distance changes do not make extra environmental events.
            let changed = [
                "touching_water",
                "submerged_in_water",
                "touching_water_or_rain",
                "on_fire",
            ]
            .iter()
            .any(|key| {
                let (a, b) = (&previous["environment"][*key], &row["environment"][*key]);
                a.is_boolean() && b.is_boolean() && a != b
            });
            changed.then(|| (previous.clone(), row.clone()))
        })
        .collect()
}

#[cfg(test)]
mod tests;
