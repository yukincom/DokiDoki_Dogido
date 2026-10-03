//! One raw observation's name-use permission, separate from combat speech credit.
//! Only explicit outcomes can update names; this ledger cannot produce a combat action.
use super::outcome_key;
use crate::{
    chat_catalog::normalized_observation_id,
    chat_observation::NameOutcomeUpdate,
    events::{EventName, GameEvent},
};
use std::collections::HashSet;

#[derive(Debug, Default)]
pub(super) struct Tracker {
    // Entity IDs are session scoped. Do not expire this at the shorter name TTL:
    // that would turn a late duplicate into a new observation and renew the TTL.
    seen: HashSet<String>,
    pending: NameOutcomeUpdate,
}
impl Tracker {
    pub fn take(&mut self) -> NameOutcomeUpdate {
        std::mem::take(&mut self.pending)
    }
    pub fn observe(&mut self, event: &GameEvent) {
        self.pending = NameOutcomeUpdate::default();
        if let Some(outcomes) = &event.combat.hostile_outcomes
            && matches!(
                event.event.name,
                EventName::HostileDefeated | EventName::CreeperDetonated | EventName::CombatEnded
            )
        {
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
    }
}
fn add(types: &mut Vec<String>, raw: &str) {
    let id = normalized_observation_id(raw);
    if !id.is_empty() && !types.contains(&id) {
        types.push(id);
    }
}
