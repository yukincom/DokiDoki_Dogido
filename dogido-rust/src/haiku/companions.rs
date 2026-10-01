//! Routine followers enter only after the scene is chosen, as one optional identity.
//! A completed player turn can make its subject primary; ordinary calls cannot.
use super::{
    materials::ReadingSnapshot,
    source_atoms::{CatalogSourceSnapshot, catalog_source_snapshot},
};
use crate::{
    events::{GameEvent, PassiveMob},
    mob_identity,
};

pub fn pet(mob: &PassiveMob) -> bool {
    mob.identity.as_ref().is_some_and(|i| i.tamed)
}

pub fn name_reading(mob: &PassiveMob, readings: &ReadingSnapshot) -> Option<(String, String)> {
    let name = mob.identity.as_ref().and_then(mob_identity::name)?;
    let kana = super::lexical::hiragana(name);
    let reading = readings.resolve(
        name,
        kana.chars()
            .all(|c| ('ぁ'..='ゖ').contains(&c) || c == 'ー')
            .then_some(kana.as_str()),
    )?;
    let reading = super::lexical::hiragana(&reading);
    (!reading.is_empty()
        && reading
            .chars()
            .all(|c| ('ぁ'..='ゖ').contains(&c) || c == 'ー'))
    .then(|| (name.to_owned(), reading))
}

pub fn label(mob: &PassiveMob, species: &str, readings: &ReadingSnapshot) -> String {
    name_reading(mob, readings)
        .map(|(name, _)| format!("{name}（{species}）"))
        .unwrap_or_else(|| species.into())
}

pub fn source(
    mob: &PassiveMob,
    readings: &ReadingSnapshot,
    background: bool,
) -> Option<CatalogSourceSnapshot> {
    let species = mob_identity::species_label(&mob.r#type);
    let mut source = catalog_source_snapshot(
        "mob",
        &mob.r#type,
        crate::chat_catalog::catalog()
            .mob_entry(&mob.r#type)
            .unwrap_or(&serde_json::Value::Null),
        if background {
            "background_companion"
        } else {
            "passive_mob"
        },
        &species,
    )?;
    if let Some(identity) = &mob.identity {
        source.catalog_type = "mob_individual".into();
        source.catalog_id = identity.entity_id.clone();
    }
    if background {
        source.note_raw.clear();
        source.extra_fields.clear();
    }
    if let Some((name, reading)) = name_reading(mob, readings) {
        source.label = format!("{name}（{species}）");
        source.reading.clear();
        source
            .extra_fields
            .insert(0, ("name_reading".into(), reading));
    }
    Some(source)
}

pub fn primary_event(event: &GameEvent, player_topics: &[String]) -> GameEvent {
    event.retaining_passive_mobs(|mob| {
        !pet(mob)
            || player_topics.iter().any(|text| {
                mob.identity
                    .as_ref()
                    .and_then(mob_identity::name)
                    .is_some_and(|name| {
                        mob_identity::named_in(text, name)
                            && !event
                                .passive_mobs
                                .iter()
                                .filter_map(|other| {
                                    other.identity.as_ref().and_then(mob_identity::name)
                                })
                                .any(|other| {
                                    mob_identity::longer_name(other, name)
                                        && mob_identity::named_in(text, other)
                                })
                    })
                    || mob_identity::species_mentioned(&mob.r#type, text)
            })
    })
}

pub fn background(
    event: &GameEvent,
    primary: &GameEvent,
    readings: &ReadingSnapshot,
) -> Vec<CatalogSourceSnapshot> {
    let available = event
        .passive_mobs
        .iter()
        .filter(|mob| {
            pet(mob)
                && !primary.passive_mobs.iter().any(|picked| {
                    picked
                        .identity
                        .as_ref()
                        .zip(mob.identity.as_ref())
                        .is_some_and(|(a, b)| a.entity_id == b.entity_id)
                })
        })
        .collect::<Vec<_>>();
    if available.is_empty() {
        return vec![];
    }
    let index = event.sequence.unwrap_or(0) as usize % available.len();
    source(available[index], readings, true)
        .into_iter()
        .collect()
}
