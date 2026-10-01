//! Observed individual names. Species, nickname and taming are separate facts.
//! No inference from collars, names, leads, player reports or model replies.
use crate::events::{GameEvent, MobIdentity};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

pub fn name(identity: &MobIdentity) -> Option<&str> {
    let value = identity.custom_name.as_deref()?.trim();
    (!value.is_empty() && !value.chars().any(char::is_control)).then_some(value)
}

pub fn named_in(text: &str, custom_name: &str) -> bool {
    !custom_name.is_empty() && normalized(text).contains(&normalized(custom_name))
}

pub fn normalized(text: &str) -> String {
    crate::haiku::lexical::hiragana(text).to_lowercase()
}

pub fn same_name(a: &str, b: &str) -> bool {
    normalized(a) == normalized(b)
}

pub fn longer_name(long: &str, short: &str) -> bool {
    !same_name(long, short) && named_in(long, short)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct NamedMob {
    pub entity_id: String,
    pub custom_name: String,
    pub mob_type: String,
    pub tamed: bool,
    pub observed_at_us: i64,
    pub current: bool,
    pub basis: String,
}

/// Visual/crosshair observations outrank older retained audio for the same ID.
pub fn observed(event: &GameEvent, now: i64) -> (HashSet<String>, Vec<NamedMob>) {
    let mut ids = HashSet::new();
    let mut out = vec![];
    let mut add = |identity: &MobIdentity, kind: &str, basis: &str, age: i64| {
        if identity.entity_id.is_empty() || !ids.insert(identity.entity_id.clone()) {
            return;
        }
        // Empty names are internal tombstones so retained audio cannot undo removal.
        out.push(NamedMob {
            entity_id: identity.entity_id.clone(),
            custom_name: name(identity).unwrap_or("").into(),
            mob_type: crate::chat_catalog::normalized_observation_id(kind),
            tamed: identity.tamed,
            observed_at_us: now.saturating_sub(age.saturating_mul(1000)),
            current: basis != "hearing" || age == 0,
            basis: basis.into(),
        });
    };
    if let Some(look) = &event.look_target
        && look.kind == "entity"
        && let Some(identity) = &look.identity
    {
        add(identity, &look.name, "look", 0);
    }
    for mob in &event.passive_mobs {
        if let Some(identity) = &mob.identity {
            add(identity, &mob.r#type, "visual", 0);
        }
    }
    for mob in &event.visual_threats {
        if let Some(identity) = &mob.identity
            && mob.entity_id.as_deref() == Some(identity.entity_id.as_str())
        {
            add(identity, &mob.r#type, "visual", 0);
        }
    }
    for sound in &event.ambient_sounds {
        if let Some(identity) = &sound.identity
            && sound.source_id.as_deref() == Some(identity.entity_id.as_str())
        {
            add(
                identity,
                &sound.r#type,
                "hearing",
                sound.heard_ago_ms.unwrap_or(0),
            );
        }
    }
    (ids, out)
}

/// A longer name wins over its substring; duplicate names retain all individuals.
pub fn mentioned<'a>(rows: &'a [NamedMob], text: &str) -> Vec<&'a NamedMob> {
    rows.iter()
        .filter(|row| named_in(text, &row.custom_name))
        .filter(|row| {
            !rows.iter().any(|other| {
                longer_name(&other.custom_name, &row.custom_name)
                    && named_in(text, &other.custom_name)
            })
        })
        .collect()
}

/// Merge by the actual observation time; visual metadata wins an exact tie.
pub fn merge_names(
    memory: &[NamedMob],
    observed: Vec<NamedMob>,
    now: i64,
    retention_ms: i64,
) -> Vec<NamedMob> {
    let mut rows = memory
        .iter()
        .filter(|row| {
            now.saturating_sub(row.observed_at_us).max(0) <= retention_ms.saturating_mul(1000)
        })
        .cloned()
        .map(|mut row| {
            row.current = false;
            row
        })
        .collect::<Vec<_>>();
    for incoming in observed {
        if let Some(prior) = rows
            .iter_mut()
            .find(|row| row.entity_id == incoming.entity_id)
        {
            if incoming.observed_at_us > prior.observed_at_us
                || (incoming.observed_at_us == prior.observed_at_us
                    && (incoming.basis != "hearing" || prior.basis == "hearing"))
            {
                *prior = incoming;
            }
        } else {
            rows.push(incoming);
        }
    }
    rows.sort_by_key(|row| std::cmp::Reverse(row.observed_at_us));
    rows.truncate(32);
    rows
}

pub fn species_label(kind: &str) -> String {
    crate::chat_catalog::catalog()
        .mob_entry(kind)
        .and_then(|entry| entry["label"].as_str())
        .unwrap_or(kind)
        .into()
}

pub fn species_mentioned(kind: &str, text: &str) -> bool {
    let label = species_label(kind);
    // Common written forms absent from the legacy spoken-label dictionary.
    let written = match kind.trim_start_matches("minecraft:") {
        "cat" => Some("猫"),
        "wolf" => Some("狼"),
        _ => None,
    };
    written.is_some_and(|word| text.contains(word))
        || crate::chat_validation::mentioned(text).contains(&label)
        || named_in(text, &label)
        || crate::chat_catalog::catalog()
            .mob_entry(kind)
            .is_some_and(|entry| {
                entry["reading"]
                    .as_str()
                    .is_some_and(|reading| named_in(text, reading))
            })
}
