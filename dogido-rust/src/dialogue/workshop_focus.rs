//! 句の相談中は環境反応へ脱線せず、実際の敵の観測だけに発話を譲る。
use super::{Session, observation_fresh};
use crate::events::{EventName, GameEvent};

pub(super) fn active(s: &Session) -> bool {
    s.haiku.workshop.as_ref().is_some_and(|w| w.open)
}

fn hostile(e: &GameEvent) -> bool {
    !e.visual_threats.is_empty()
        || !e.auditory_threats.is_empty()
        || e.combat.combat_active_hint == Some(true)
        || [
            e.combat.hostiles_within_7,
            e.combat.hostiles_within_10,
            e.combat.hostiles_within_scan_ground,
        ]
        .iter()
        .any(|n| n.is_some_and(|n| n > 0))
}

/// 暗さや敵の根拠がない被ダメージを、敵接近と取り違えない。
/// 死亡・観測失効の停止と、既に始まった戦闘からの復帰は別に維持する。
pub(super) fn quiet(s: &Session) -> bool {
    active(s) && quiet_observation(s)
}

pub(super) fn quiet_observation(s: &Session) -> bool {
    observation_fresh(s)
        && s.latest.as_ref().is_some_and(|e| {
            !hostile(e)
                && e.event.name != EventName::PlayerDied
                && e.player.health.is_none_or(|h| h > 0.0)
        })
        && s.audio_latest.as_ref().is_none_or(|e| !hostile(e))
}

pub(super) fn owns_input(s: &Session) -> bool {
    quiet(s)
        && s.haiku
            .workshop
            .as_ref()
            .is_some_and(|w| !w.combat_paused())
}

pub(super) fn combat_completion(kind: &str) -> bool {
    matches!(
        kind,
        "hostile_defeated" | "creeper_detonated" | "aftermath" | "death"
    )
}
