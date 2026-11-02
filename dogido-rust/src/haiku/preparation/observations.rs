//! 発句開始時に固定した現在観測と、既存台帳の直近視認を別の出典へ投影する。
//! 保持期限・身体世代の照合はsnapshot_bodyが所有し、ここでは現在視認へ昇格させない。
use super::*;
use crate::chat_observation::Snapshot;

fn observation(reference: String, text: String, role: &str) -> SourceAtom {
    SourceAtom {
        atom_id: format!("{reference}:fact"),
        text,
        source_ref: reference,
        field_path: "observed_label".into(),
        observation_role: role.into(),
        kind: "observation".into(),
        claim_class: "factual".into(),
        claim_scopes: vec!["observed_state".into()],
        basis_atom_ids: vec![],
    }
}

pub(super) fn current_atoms(event: &GameEvent) -> Vec<SourceAtom> {
    let at = event.observed_at.timestamp_micros();
    let mut atoms = vec![];
    for mob in &event.visual_threats {
        let label = crate::mob_identity::species_label(&mob.r#type);
        atoms.push(observation(
            format!("current_visual:{at}:{}", mob.r#type),
            format!("発句開始時のドギドの視認記録に{label}がある"),
            "current_visual",
        ));
    }
    if let Some(composition) = &event.nearby_composition {
        for mob in &composition.mobs {
            let label = crate::mob_identity::species_label(&mob.id);
            atoms.push(observation(
                format!("current_surroundings:{at}:{}", mob.id),
                format!("発句開始時のドギドの周辺観測に{label}がある"),
                "current_surroundings",
            ));
        }
    }
    atoms
}

pub fn recent_atoms(event: &GameEvent, snapshot: &Snapshot) -> Vec<SourceAtom> {
    let at = event.observed_at.timestamp_micros();
    let mut atoms = vec![];
    for sighting in &snapshot.recent.passive_sightings {
        let label = crate::mob_identity::species_label(&sighting.mob_type);
        atoms.push(observation(
            format!("recent_visual:{at}:{}", sighting.mob_type),
            format!(
                "さっき見たもん（{}秒前に見た{label}）",
                sighting.seconds_ago
            ),
            "recent_visual",
        ));
    }
    for memo in &snapshot.recent.visual_memos {
        if event.visual_threats.iter().any(|mob| {
            mob.r#type.trim_start_matches("minecraft:")
                == memo.mob_type.trim_start_matches("minecraft:")
        }) {
            continue;
        }
        let seconds = at.saturating_sub(memo.seen_at_us).max(0) / 1_000_000;
        atoms.push(observation(
            format!("recent_visual:{}:{}", memo.seen_at_us, memo.dedupe_key),
            format!(
                "さっき見たもん（{seconds}秒前に見た{}）",
                memo.label_ja
            ),
            "recent_visual",
        ));
    }
    atoms
}
