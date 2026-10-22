use super::*;
use crate::{chat_observation::SoundCandidate, mob_identity};

pub(super) fn current_observations(snapshot: &Snapshot, user: &str) -> Vec<handoff::Observation> {
    let mentioned = mob_identity::mentioned(&snapshot.named_mobs, user);
    snapshot
        .named_mobs
        .iter()
        .filter(|row| {
            row.current
                && (mentioned
                    .iter()
                    .any(|named| named.entity_id == row.entity_id)
                    || (mentioned.is_empty()
                        && mob_identity::species_mentioned(&row.mob_type, user)))
        })
        .map(|row| handoff::Observation {
            entity_id: format!("individual:{}", row.entity_id),
            label: row.custom_name.clone(),
        })
        .collect()
}

pub(super) fn context(snapshot: &Snapshot, user: &str, look: &str, current_only: bool) -> String {
    let mentioned = mob_identity::mentioned(&snapshot.named_mobs, user);
    let rows = snapshot
        .named_mobs
        .iter()
        .filter(|row| !current_only || row.current)
        .filter(|row| {
            mentioned
                .iter()
                .any(|named| named.entity_id == row.entity_id)
                || row.custom_name == look
                || (mentioned.is_empty() && species_mentioned(&row.mob_type, user))
        })
        .take(8)
        .map(|row| {
            json!({"name":row.custom_name,"species":mob_identity::species_label(&row.mob_type),
            "tamed":row.tamed,"current_observation":row.current,"basis":row.basis})
        })
        .collect::<Vec<_>>();
    if rows.is_empty() {
        String::new()
    } else {
        serde_json::to_string(&rows).unwrap()
    }
}

fn species_mentioned(kind: &str, user: &str) -> bool {
    mob_identity::species_mentioned(kind, user)
}

pub(super) struct HearingAnswer {
    pub summary: String,
    pub named: Vec<String>,
    pub sources: Vec<String>,
    pub ambiguous: bool,
}
pub(super) fn hearing(snapshot: &Snapshot, user: &str) -> HearingAnswer {
    let mentioned = mob_identity::mentioned(&snapshot.named_mobs, user);
    let candidates = &snapshot.hearing.candidates;
    // A known individual with no corresponding sound must not borrow another cat's voice.
    if candidates.is_empty() && mentioned.is_empty() {
        return HearingAnswer {
            summary: snapshot.hearing.summary.clone(),
            named: snapshot.hearing.named_mobs.clone(),
            sources: snapshot.hearing.source_labels.clone(),
            ambiguous: false,
        };
    }
    let duplicate_name = mentioned.iter().any(|row| {
        mentioned.iter().any(|other| {
            mob_identity::same_name(&row.custom_name, &other.custom_name)
                && row.entity_id != other.entity_id
        })
    });
    let explicit_species = candidates.iter().any(|c| {
        c.memo
            .mob_type
            .as_deref()
            .is_some_and(|kind| species_mentioned(kind, user))
    });
    let explicit_species = explicit_species
        || snapshot
            .named_mobs
            .iter()
            .any(|row| species_mentioned(&row.mob_type, user));
    let mut selected: Vec<&SoundCandidate> = candidates
        .iter()
        .filter(|candidate| {
            if !mentioned.is_empty() {
                candidate
                    .memo
                    .identity
                    .as_ref()
                    .is_some_and(|i| mentioned.iter().any(|row| row.entity_id == i.entity_id))
            } else if explicit_species {
                candidate
                    .memo
                    .mob_type
                    .as_deref()
                    .is_some_and(|kind| species_mentioned(kind, user))
            } else {
                true
            }
        })
        .collect();
    if mentioned.is_empty()
        && !explicit_species
        && selected
            .iter()
            .any(|c| !c.memo.identity.as_ref().is_some_and(|i| i.tamed))
    {
        selected.retain(|c| !c.memo.identity.as_ref().is_some_and(|i| i.tamed));
    }
    selected.sort_by_key(|c| {
        (
            c.memo.kind != "hostile",
            !c.current,
            std::cmp::Reverse(c.memo.heard_at_us),
        )
    });
    let mut lines = vec![];
    let mut named = vec![];
    let mut sources = vec![];
    for candidate in selected.into_iter().take(4) {
        let memo = &candidate.memo;
        let known_name = memo
            .identity
            .as_ref()
            .and_then(|identity| {
                snapshot
                    .named_mobs
                    .iter()
                    .find(|row| row.entity_id == identity.entity_id)
            })
            .map(|row| row.custom_name.as_str())
            .filter(|name| {
                snapshot
                    .named_mobs
                    .iter()
                    .filter(|row| mob_identity::same_name(&row.custom_name, name))
                    .count()
                    == 1
            });
        let line = if let Some(name) = known_name.filter(|_| memo.emitter.is_none()) {
            format!(
                "{} {} {}{}",
                crate::chat_observation::sound_description(
                    Some(name),
                    &memo.kind,
                    memo.sound_event.as_deref(),
                    true,
                ),
                memo.direction,
                memo.distance_band,
                if candidate.current {
                    ""
                } else {
                    "（ついさっき）"
                }
            )
        } else {
            crate::chat_observation::line(
                memo.label_ja.as_deref(),
                &memo.kind,
                memo.sound_event.as_deref(),
                &memo.direction,
                &memo.distance_band,
                !candidate.current,
                memo.emitter.as_deref(),
            )
        };
        if !lines.contains(&line) {
            lines.push(line);
        }
        if let Some(label) = &memo.label_ja {
            let out = if memo.kind == "environment" {
                &mut sources
            } else {
                &mut named
            };
            if !out.contains(label) {
                out.push(label.clone());
            }
        }
    }
    HearingAnswer {
        summary: lines.join("、"),
        named,
        sources,
        ambiguous: duplicate_name,
    }
}

pub(super) struct Focused {
    pub event: GameEvent,
    pub snapshot: Snapshot,
    pub context: Context,
}

/// Current names/species mentioned by the player determine priority for this reply only.
/// Retained names and previous model replies never create a current observation.
pub(super) fn focus(
    event: &GameEvent,
    snapshot: &Snapshot,
    context: &Context,
    user: &str,
) -> Option<Focused> {
    // The retained name table is capped independently of the current candidate scan.
    // Add missing current visual names only to this reply's read projection.
    let now = match &event.observed_at {
        crate::events::EventTime::Aware(at) => at.timestamp_micros(),
        crate::events::EventTime::Naive(at) => at.and_utc().timestamp_micros(),
    };
    let (_, observed_names) = mob_identity::observed(event, now);
    let mut snapshot = snapshot.clone();
    for row in observed_names {
        if row.current
            && row.basis != "hearing"
            && !row.custom_name.is_empty()
            && !snapshot
                .named_mobs
                .iter()
                .any(|known| known.entity_id == row.entity_id)
        {
            snapshot.named_mobs.push(row);
        }
    }
    let named = mob_identity::mentioned(&snapshot.named_mobs, user);
    let candidates = event
        .passive_mobs
        .iter()
        .chain(&event.companion_candidates)
        .collect::<Vec<_>>();
    let matches_topic = |mob: &&crate::events::PassiveMob| {
        mob.identity.as_ref().is_some_and(|identity| {
            (identity.tamed || mob_identity::name(identity).is_some())
                && (named.iter().any(|row| row.entity_id == identity.entity_id)
                    || (named.is_empty() && mob_identity::species_mentioned(&mob.r#type, user)))
        })
    };
    let mut selected = candidates
        .iter()
        .copied()
        .filter(matches_topic)
        .take(4)
        .cloned()
        .collect::<Vec<_>>();
    if selected.is_empty() {
        return None;
    }
    for mob in &event.passive_mobs {
        if selected.len() == 4 {
            break;
        }
        let already_selected = mob.identity.as_ref().is_some_and(|identity| {
            selected.iter().any(|other| {
                other
                    .identity
                    .as_ref()
                    .is_some_and(|other| other.entity_id == identity.entity_id)
            })
        });
        if !already_selected {
            selected.push(mob.clone());
        }
    }
    let event = event.with_passive_mobs(selected);
    snapshot.current.passive_types =
        current::unique(event.passive_mobs.iter().map(|mob| mob.r#type.as_str()));
    let mut context = context.clone();
    if let Some(world) = &mut context.world_context {
        // Reuse the frame's place/weather measurements; only the chosen animals change.
        world.observations["mob_states"] = crate::mob_environment::project(&event);
        world.villager_routines = crate::villager_routines::project(&event);
        let knowledge =
            crate::catalog_knowledge::for_event(&event, context.current_structure.as_deref());
        world.catalog_knowledge = (!knowledge.is_empty()).then_some(knowledge);
    }
    Some(Focused {
        event,
        snapshot,
        context,
    })
}
