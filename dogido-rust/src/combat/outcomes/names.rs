//! One raw observation's name-use permission, separate from combat speech credit.
//! Neither this ledger nor legacy visual disappearance can produce a combat action.
use super::outcome_key;
use crate::{
    chat_catalog::{normalized_observation_id, strip},
    chat_observation::NameOutcomeUpdate,
    combat::model::Mode,
    events::{EventName, GameEvent},
};
use std::collections::HashSet;

#[derive(Debug, Default)]
pub(super) struct Tracker {
    // Entity IDs are session scoped. Do not expire this at the shorter name TTL:
    // that would turn a late duplicate into a new observation and renew the TTL.
    seen: HashSet<String>,
    tracked: Vec<(String, String)>,
    dimension: Option<String>,
    pending: NameOutcomeUpdate,
}
impl Tracker {
    pub fn take(&mut self) -> NameOutcomeUpdate {
        std::mem::take(&mut self.pending)
    }
    pub fn observe(&mut self, event: &GameEvent, mode: Mode, damage_window_ms: u64) {
        self.pending = NameOutcomeUpdate::default();
        let dimension = event
            .player
            .dimension
            .as_deref()
            .map(strip)
            .filter(|v| !v.is_empty())
            .map(str::to_lowercase);
        if self.dimension != dimension {
            if self.dimension.is_some() && dimension.is_some() {
                self.tracked.clear();
            }
            self.dimension = dimension;
        }
        // Canonical dict assignment keeps the first position and last value.
        let mut current: Vec<(String, String)> = vec![];
        for v in &event.visual_threats {
            let Some(id) = v.entity_id.as_ref().filter(|v| !v.is_empty()) else {
                continue;
            };
            if let Some(row) = current.iter_mut().find(|r| &r.0 == id) {
                row.1 = v.r#type.clone();
            } else {
                current.push((id.clone(), v.r#type.clone()));
            }
        }
        if let Some(outcomes) = &event.combat.hostile_outcomes {
            if matches!(
                event.event.name,
                EventName::HostileDefeated | EventName::CreeperDetonated | EventName::CombatEnded
            ) {
                // Immediate Python memory accepts all outcome kinds; only speech
                // dispatch filters by event kind. Preserve that distinction.
                let fresh: Vec<_> = outcomes
                    .iter()
                    .filter(|v| !self.seen.contains(&outcome_key(v)))
                    .collect();
                for outcome in &fresh {
                    add(&mut self.pending.confirmed_types, &outcome.r#type);
                }
                self.seen.extend(fresh.into_iter().map(outcome_key));
            }
        } else if matches!(
            mode,
            Mode::Panic | Mode::SuppressedPanic | Mode::Alert | Mode::Aftermath
        ) || event.combat.combat_active_hint == Some(true)
            || event
                .combat
                .recent_damage_ms
                .is_some_and(|n| i128::from(n) <= i128::from(damage_window_ms))
        {
            for (id, kind) in &self.tracked {
                if !current.iter().any(|r| &r.0 == id) {
                    add(&mut self.pending.legacy_disappeared_types, kind);
                }
            }
        }
        self.tracked = current;
        // Python _flush_combat_dialogue_notes clears tracking after kill-name update.
        if event.event.name == EventName::CombatEnded {
            self.tracked.clear();
        }
    }
}
fn add(types: &mut Vec<String>, raw: &str) {
    let id = normalized_observation_id(raw);
    if !id.is_empty() && !types.contains(&id) {
        types.push(id);
    }
}
