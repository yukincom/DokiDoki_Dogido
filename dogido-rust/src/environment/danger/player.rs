//! プレイヤー本人の現在の接触・移動・空気から、継続中の危険の発話機会を一度だけ選ぶ。
//! 状態はDangerが全snapshotごとに更新する。被ダメージの原因・着地先・救助操作は推定しない。
use crate::{
    combat::model::{LeafRequest, Speech},
    events::{EventName, GameEvent},
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::BTreeSet;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Kind {
    Lava,
    LowAir,
    Falling,
    Fire,
}

fn effect(event: &GameEvent, id: &str) -> bool {
    event
        .player
        .active_status_effects
        .iter()
        .any(|v| v.trim_start_matches("minecraft:") == id)
}

/// 新しい危険判定は実測の組合せだけ。未知値は肯定にせず、保護効果も現在値だけを参照する。
pub(crate) fn current(event: &GameEvent) -> BTreeSet<Kind> {
    let mut kinds = BTreeSet::new();
    if event.event.name == EventName::PlayerDied
        || event.player.health.is_some_and(|h| h <= 0.0)
        || event.player.invulnerable == Some(true)
    {
        return kinds;
    }
    let Some(contact) = event.player.environment.as_ref() else {
        return kinds;
    };
    if contact.touching_lava == Some(true) && !effect(event, "fire_resistance") {
        kinds.insert(Kind::Lava);
    }
    if contact.submerged_in_water == Some(true)
        && event
            .player_safety_world()
            .air_supply
            .is_some_and(|air| air <= 60)
        && !effect(event, "water_breathing")
        && !effect(event, "conduit_power")
    {
        kinds.insert(Kind::LowAir);
    }
    if let Some(motion) = event.player.motion.as_ref()
        && motion.fall_distance.is_some_and(|v| v > 3.0)
        && motion.vertical_velocity.is_some_and(|v| v < 0.0)
        && motion.climbing == Some(false)
        && motion.gliding == Some(false)
        && motion.flying == Some(false)
        && contact.on_ground == Some(false)
        && contact.touching_water == Some(false)
        && contact.touching_lava == Some(false)
        && event.player.vehicle.is_none()
        && !effect(event, "slow_falling")
        && !effect(event, "levitation")
    {
        kinds.insert(Kind::Falling);
    }
    if contact.on_fire == Some(true)
        && contact.touching_lava != Some(true)
        && !effect(event, "fire_resistance")
    {
        kinds.insert(Kind::Fire);
    }
    kinds
}

/// 被弾やcombat_active_hintは原因不明でも立つため、敵の実観測とは分ける。
/// ここで戦闘modeを変えず、敵の姿・音・近接個体数がある間は既存戦闘警告へ譲る。
pub(crate) fn hostile_observed(event: &GameEvent) -> bool {
    !event.visual_threats.is_empty()
        || !event.auditory_threats.is_empty()
        || [
            event.combat.hostiles_within_7,
            event.combat.hostiles_within_10,
            event.combat.hostiles_within_scan_ground,
        ]
        .iter()
        .any(|n| n.is_some_and(|n| n > 0))
}

pub(crate) fn kind(action: &Speech) -> Option<Kind> {
    (action.kind == "player_hazard")
        .then(|| action.leaf.as_ref()?.details.get("player_hazard").cloned())
        .flatten()
        .and_then(|v| serde_json::from_value(v).ok())
}

pub(crate) fn player_origin(action: &Speech) -> bool {
    action.kind == "player_hazard" || action.kind == "magma_block"
}

pub(crate) fn applicable(action: &Speech, event: &GameEvent) -> bool {
    kind(action).is_some_and(|kind| current(event).contains(&kind)) && !hostile_observed(event)
}

#[derive(Clone, Default)]
pub(crate) struct State {
    dimension: Option<String>,
    active: BTreeSet<Kind>,
    considered: BTreeSet<Kind>,
}
impl State {
    /// 欠測・解消・次元変更で古い機会を落とす。時刻だけでは再発話を作らない。
    pub(crate) fn observe(&mut self, event: &GameEvent) {
        if self.dimension != event.player.dimension {
            self.considered.clear();
        }
        self.dimension = event.player.dimension.clone();
        self.active = current(event);
        self.considered.retain(|kind| self.active.contains(kind));
    }
    /// 配送枠が使えると確認した複製上で呼び、採用されたときだけDangerへ反映する。
    pub(crate) fn take(&mut self, event: &GameEvent) -> Option<Speech> {
        if hostile_observed(event) {
            return None;
        }
        let current = current(event);
        let kind = self
            .active
            .iter()
            .copied()
            .find(|kind| !self.considered.contains(kind) && current.contains(kind))?;
        self.considered.insert(kind);
        let text = match kind {
            Kind::Lava => "溶岩に触れてるで！",
            Kind::LowAir => "息が続かんようになりそうやで！",
            Kind::Falling => "落ちてるで、気ぃつけて！",
            Kind::Fire => "燃えてるで、気ぃつけて！",
        };
        let mut action = Speech::new("player_hazard", text);
        action.interrupt = true;
        action.leaf = Some(LeafRequest {
            kind: crate::environment::reaction::KIND.into(),
            details: json!({"player_hazard":kind}),
            temperature: 0.65,
        });
        Some(action)
    }
}

#[cfg(test)]
mod tests;
