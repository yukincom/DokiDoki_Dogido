use crate::events::{HotbarSlot, HotbarSlotWeaponKind as Kind};
use chrono::{DateTime, Duration, Utc};
use serde::Serialize;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RiskPolicy {
    Auto,
    Confirm,
    Deny,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Gate {
    Command,
    CapabilityMissing,
    NoCandidate,
    ConfirmRequired,
    Denied,
}
pub fn gate(policy: RiskPolicy, capable: bool, selection: Option<&WeaponSelection>) -> Gate {
    if policy == RiskPolicy::Deny {
        Gate::Denied
    } else if !capable {
        Gate::CapabilityMissing
    } else if selection.is_none() {
        Gate::NoCandidate
    } else if policy == RiskPolicy::Confirm {
        Gate::ConfirmRequired
    } else {
        Gate::Command
    }
}
#[derive(Clone, Debug, Serialize)]
pub struct SelectHotbarCommand {
    pub command_id: String,
    #[serde(rename = "type")]
    pub command_type: &'static str,
    pub slot: i64,
    pub expected_item_id: String,
    pub issued_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
}
#[derive(Clone, Debug, Serialize)]
pub struct WeaponSelection {
    pub slot: i64,
    pub item_id: String,
    pub weapon_kind: Kind,
    pub used_fallback: bool,
}
impl WeaponSelection {
    pub(super) fn command(&self, now: DateTime<Utc>) -> SelectHotbarCommand {
        SelectHotbarCommand {
            command_id: format!("cmd_{}", uuid::Uuid::new_v4().simple()),
            command_type: "select_hotbar",
            slot: self.slot,
            expected_item_id: self.item_id.clone(),
            issued_at: now,
            expires_at: now + Duration::seconds(2),
        }
    }
}
pub fn select_weapon_slot(slots: &[HotbarSlot]) -> Option<WeaponSelection> {
    for kind in [Kind::Sword, Kind::Trident, Kind::Axe, Kind::Bow, Kind::Tool] {
        let key = |s: &HotbarSlot| {
            let attack = if kind == Kind::Sword {
                s.attack_damage.unwrap_or(f64::INFINITY)
            } else {
                -s.attack_damage.unwrap_or(f64::NEG_INFINITY)
            };
            let remaining = if s.max_damage <= 0 {
                f64::INFINITY
            } else {
                (s.max_damage - s.damage).max(0) as f64
            };
            (attack, remaining, s.slot)
        };
        if let Some(s) = slots
            .iter()
            .filter(|s| {
                s.weapon_kind == kind
                    && s.count > 0
                    && s.item_id.as_ref().is_some_and(|i| !i.is_empty())
            })
            .min_by(|a, b| {
                key(a)
                    .partial_cmp(&key(b))
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
        {
            return Some(WeaponSelection {
                slot: s.slot,
                item_id: s.item_id.clone().unwrap(),
                weapon_kind: kind,
                used_fallback: kind != Kind::Sword,
            });
        }
    }
    None
}
