//! 句の相談中は環境反応へ脱線せず、敵の観測に基づく中断条件を適用する。
use super::{Session, observation_fresh};
use crate::events::{EventName, GameEvent};

// 一時的な川柳データ採取用。採取後は false に戻し、視認・スキャンも中断条件へ戻す。
pub(super) const TEMPORARY_ALLOW_SCANNED_HOSTILES_FOR_SENRYU_COLLECTION: bool = true;

pub(super) fn active(s: &Session) -> bool {
    s.haiku.workshop.as_ref().is_some_and(|w| w.open)
}

pub(super) fn hostile(e: &GameEvent) -> bool {
    let scanned_hostiles = scanned_hostiles_only(e);
    (!TEMPORARY_ALLOW_SCANNED_HOSTILES_FOR_SENRYU_COLLECTION || !scanned_hostiles)
        && (!e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
            || e.combat.combat_active_hint == Some(true)
            || [
                e.combat.hostiles_within_7,
                e.combat.hostiles_within_10,
                e.combat.hostiles_within_scan_ground,
            ]
            .iter()
            .any(|n| n.is_some_and(|n| n > 0)))
}

/// 一時採取で無視するのは視認／スキャンだけ。音響・明示戦闘・直近被弾は除く。
pub(super) fn scanned_hostiles_only(e: &GameEvent) -> bool {
    (!e.visual_threats.is_empty()
        || [
            e.combat.hostiles_within_7,
            e.combat.hostiles_within_10,
            e.combat.hostiles_within_scan_ground,
        ]
        .iter()
        .any(|n| n.is_some_and(|n| n > 0)))
        && e.auditory_threats.is_empty()
        && e.combat.combat_active_hint != Some(true)
        && e.combat.recent_damage_ms.is_none_or(|ms| ms >= 3000)
        && !matches!(
            e.event.name,
            EventName::PlayerDied
                | EventName::HostileDefeated
                | EventName::CreeperDetonated
                | EventName::CombatEnded
        )
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    fn event(combat: Value, event_name: &str) -> GameEvent {
        GameEvent::parse(json!({
            "schema_version":"2026-05-24",
            "adapter":"workshop-focus-test",
            "observed_at":"2026-10-18T00:00:00Z",
            "event":{"name":event_name,"source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"dimension":"minecraft:overworld","health":20},
            "combat":combat
        }))
        .unwrap()
    }

    #[test]
    fn collection_treats_scan_only_hostiles_as_non_interrupting() {
        let scanned = event(json!({"hostiles_within_7":1}), "status_snapshot");
        assert!(scanned_hostiles_only(&scanned));
        assert_eq!(
            hostile(&scanned),
            !TEMPORARY_ALLOW_SCANNED_HOSTILES_FOR_SENRYU_COLLECTION
        );
    }

    #[test]
    fn collection_still_interrupts_for_audio_damage_and_explicit_combat() {
        let audible = event(json!({"hostiles_within_7":1}), "status_snapshot");
        // 観測契約で受理された敵対音を追加し、視覚だけの条件から外す。
        let audible = GameEvent::parse({
            let mut value = serde_json::to_value(audible).unwrap();
            value["auditory_threats"] = json!([{
                "label":"zombie","source_id":"audio-zombie","heard_ago_ms":0
            }]);
            value
        })
        .unwrap();
        assert!(!scanned_hostiles_only(&audible));
        assert!(hostile(&audible));

        let damaged = event(
            json!({"hostiles_within_7":1,"recent_damage_ms":1000}),
            "status_snapshot",
        );
        assert!(!scanned_hostiles_only(&damaged));
        assert!(hostile(&damaged));

        let explicit = event(json!({"hostiles_within_7":1}), "combat_ended");
        assert!(!scanned_hostiles_only(&explicit));
        assert!(hostile(&explicit));
    }
}
