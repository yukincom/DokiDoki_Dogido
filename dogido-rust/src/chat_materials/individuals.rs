use super::*;
use crate::{chat_observation::SoundCandidate, mob_identity};

pub(super) fn current_observations(snapshot: &Snapshot) -> Vec<handoff::Observation> {
    snapshot
        .named_mobs
        .iter()
        .filter(|row| row.current)
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
        let line = if let Some(name) = known_name {
            let vocal = memo.sound_event.as_deref().is_some_and(|event| {
                matches!(
                    event.rsplit('.').next().unwrap_or(""),
                    "ambient"
                        | "growl"
                        | "pant"
                        | "whine"
                        | "hurt"
                        | "death"
                        | "purr"
                        | "purreow"
                        | "hiss"
                )
            });
            format!(
                "{name}の{} {} {}{}",
                if vocal { "声" } else { "音" },
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
                &memo.direction,
                &memo.distance_band,
                !candidate.current,
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
