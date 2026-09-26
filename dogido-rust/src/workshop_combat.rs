//! 戦闘中に保持した句を戻す理由。発話・モデル・保存は持たない。
use crate::events::{EventName, GameEvent, HostileOutcomeOutcome as Outcome};
use serde::Serialize;
use std::time::Instant;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Reason {
    Victory,
    Detonated,
    Explosion,
    Defeated,
    Escaped,
    Safe,
}

impl Reason {
    pub fn observed(event: &GameEvent) -> Option<Self> {
        let c = &event.combat;
        let has = |kind| {
            c.hostile_outcomes
                .as_ref()
                .is_some_and(|v| v.iter().any(|o| o.outcome == kind))
        };
        if c.warden_defeat_confirmed == Some(true)
            || c.dragon_defeat_confirmed == Some(true)
            || has(Outcome::PlayerKill)
        {
            Some(Self::Victory)
        } else if has(Outcome::CreeperDetonation) {
            Some(Self::Detonated)
        } else if has(Outcome::ExplosionDeath) {
            Some(Self::Explosion)
        } else if has(Outcome::OtherDeath) {
            Some(Self::Defeated)
        } else if event.event.name == EventName::CombatEnded {
            Some(Self::Escaped)
        } else {
            // 視認消失・死亡音・経験値は撃破の根拠にしない。
            None
        }
    }
    pub fn prompt(self, verse: &str) -> String {
        let lead = match self {
            Self::Victory => "やったな。倒せたみたいや。",
            Self::Detonated => "うわぁ、クリーパー爆発したな。びっくりしたわ。",
            Self::Explosion => "うわっ、爆発で敵が倒れたみたいやな。",
            Self::Defeated => "敵は倒れたみたいやな。",
            Self::Escaped => "ひとまず離れられたみたいやな。",
            Self::Safe => "落ち着いたみたいやな。",
        };
        format!("{lead}中断してた句はこれやで。\n{verse}\n続ける？")
    }
}

#[derive(Clone, Debug, Default)]
pub struct Recovery {
    pub reason: Option<Reason>,
    pub announcing: bool,
    pub retry_at: Option<Instant>,
}
impl Recovery {
    pub fn observe(&mut self, event: &GameEvent) {
        if !event.visual_threats.is_empty() || !event.auditory_threats.is_empty() {
            self.reason = None;
        } else if let Some(reason) = Reason::observed(event) {
            // 撃破等の通知後の空snapshot／空combat_endedで確定済みの理由を消さない。
            if self.reason.is_none() || reason != Reason::Escaped {
                self.reason = Some(reason);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn event(name: &str, combat: serde_json::Value) -> GameEvent {
        GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture", "sequence":1,
            "observed_at":"2026-09-26T00:00:00Z","event":{"name":name,"source_kind":"system","priority_hint":"background","certainty":"high"},
            "combat":combat})).unwrap()
    }
    #[test]
    fn explicit_outcomes_only_and_reason_survives_later_empty_frames() {
        for (outcome, expected) in [
            ("player_kill", Reason::Victory),
            ("creeper_detonation", Reason::Detonated),
            ("explosion_death", Reason::Explosion),
            ("other_death", Reason::Defeated),
        ] {
            let e = event(
                "combat_ended",
                json!({"hostile_outcomes":[{"type":"creeper","entity_id":"c1","outcome":outcome,
                "evidence":if outcome=="creeper_detonation" {"explosion_packet"} else {"server_death_event"}}]}),
            );
            let mut r = Recovery::default();
            r.observe(&e);
            r.observe(&event("status_snapshot", json!({})));
            r.observe(&event("combat_ended", json!({"hostile_outcomes":[]})));
            assert_eq!(r.reason, Some(expected));
        }
        assert_eq!(
            Reason::observed(&event(
                "status_snapshot",
                json!({"nearby_experience_orb_count":9})
            )),
            None
        );
        assert_eq!(
            Reason::observed(&event(
                "combat_ended",
                json!({"nearby_experience_orb_count":9})
            )),
            Some(Reason::Escaped)
        );
    }
    #[test]
    fn new_enemy_drops_old_victory_and_boss_requires_explicit_evidence() {
        let mut r = Recovery {
            reason: Some(Reason::Victory),
            ..Recovery::default()
        };
        let mut e = serde_json::to_value(event("status_snapshot", json!({}))).unwrap();
        e["visual_threats"] = json!([{"type":"zombie","entity_id":"z2","distance":6}]);
        r.observe(&GameEvent::parse(e).unwrap());
        assert_eq!(r.reason, None);
        assert_eq!(
            Reason::observed(&event(
                "combat_ended",
                json!({"dragon_defeat_confirmed":true})
            )),
            Some(Reason::Victory)
        );
        assert!(!Reason::Escaped.prompt("さくらのは").contains("倒"));
    }
}
