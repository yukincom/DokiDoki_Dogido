//! 中断中の五分類と単独敵の安定期間。モデルは安全判定・句・保存を変更しない。
use crate::events::GameEvent;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    ResumeWorkshop,
    WorkshopInput,
    CloseWorkshop,
    Unrelated,
    #[default]
    Uncertain,
}
impl Action {
    /// 分類器へ渡す許可集合。IPCとpromptは同じRustの型から組み立てる。
    pub const ALL: [Self; 5] = [
        Self::ResumeWorkshop,
        Self::WorkshopInput,
        Self::CloseWorkshop,
        Self::Unrelated,
        Self::Uncertain,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Self::ResumeWorkshop => "resume_workshop",
            Self::WorkshopInput => "workshop_input",
            Self::CloseWorkshop => "close_workshop",
            Self::Unrelated => "unrelated",
            Self::Uncertain => "uncertain",
        }
    }
}
#[derive(Debug, Deserialize, Serialize, Default)]
#[serde(deny_unknown_fields)]
pub struct Analysis {
    pub action: Action,
    pub confidence: f64,
    pub evidence: String,
}
impl Analysis {
    /// SDK接続側の外形検査とは別に、行為候補として必要な信頼度と原文根拠を検査する。
    /// SDK・chatのどちらもここを通す。合格後もcombat_classifierの原文行為ガードと
    /// 現在の戦闘状態を通るまで、再開・終了や句の変更は行わない。
    pub fn parse(payload: &Value, text: &str) -> Self {
        let Ok(a) = serde_json::from_value::<Self>(payload.clone()) else {
            return Self::default();
        };
        a.validate(text)
    }
    pub fn validate(self, text: &str) -> Self {
        let evidence = self.evidence.trim();
        if !(0.75..=1.0).contains(&self.confidence)
            || evidence.chars().count() < 2
            || evidence.chars().count() > 120
            || !text.contains(evidence)
        {
            Self::default()
        } else {
            self
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct StableThreat {
    signature: Option<String>,
    since: u64,
    last: Option<u64>,
    pub generation: u64,
}
pub fn signature(e: &GameEvent, damage_ms: u64) -> Option<String> {
    if e.visual_threats.len() != 1
        || !e.auditory_threats.is_empty()
        || e.combat
            .recent_damage_ms
            .is_some_and(|v| v <= damage_ms as i64)
        || e.combat.hostiles_within_10.is_some_and(|n| n > 1)
        || e.player.health.is_some_and(|h| h <= 0.0)
    {
        return None;
    }
    let t = &e.visual_threats[0];
    let kind = t.r#type.to_lowercase();
    if ["warden", "dragon", "wither"]
        .iter()
        .any(|v| kind.contains(v))
        || t.approaching
        || t.fuse_active == Some(true)
        || t.distance.is_none_or(|d| d <= 3.0)
    {
        return None;
    }
    let id = t.entity_id.as_ref()?.trim();
    (!id.is_empty()).then(|| format!("{kind}:{id}"))
}
impl StableThreat {
    pub fn reset(&mut self) {
        if self.signature.take().is_some() {
            self.generation = self.generation.wrapping_add(1);
        }
        self.last = None;
    }
    pub fn observe(&mut self, e: &GameEvent, now: u64, damage_ms: u64) {
        let next = signature(e, damage_ms);
        if next != self.signature || self.last.is_none_or(|t| now.saturating_sub(t) > 10_000) {
            self.since = now;
            self.generation = self.generation.wrapping_add(1);
        }
        self.signature = next;
        self.last = Some(now);
    }
    pub fn ready(&self, e: &GameEvent, now: u64, delay_ms: u64, damage_ms: u64) -> Option<String> {
        let key = signature(e, damage_ms)?;
        (self.signature.as_ref() == Some(&key)
            && self.last.is_some_and(|t| now.saturating_sub(t) <= 10_000)
            && now.saturating_sub(self.since) >= delay_ms)
            .then_some(key)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn event(threat: Value, combat: Value) -> GameEvent {
        GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-09-26T00:00:00Z",
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "visual_threats":[threat],"combat":combat})).unwrap()
    }
    fn mob() -> Value {
        json!({"type":"zombie","entity_id":"z1","distance":6,"approaching":false})
    }
    #[test]
    fn stable_period_requires_same_known_enemy_and_resets_after_damage_or_gap() {
        let e = event(mob(), json!({}));
        let mut s = StableThreat::default();
        s.observe(&e, 0, 3000);
        assert_eq!(s.ready(&e, 7999, 8000, 3000), None);
        s.observe(&e, 8000, 3000);
        assert!(s.ready(&e, 8000, 8000, 3000).is_some());
        let g = s.generation;
        s.observe(&event(mob(), json!({"recent_damage_ms":0})), 8001, 3000);
        s.observe(&e, 8002, 3000);
        assert!(s.generation > g);
        assert_eq!(s.ready(&e, 9000, 8000, 3000), None);
        s.observe(&e, 22000, 3000);
        assert_eq!(s.ready(&e, 22000, 8000, 3000), None);
    }
    #[test]
    fn unstable_count_boss_unknown_id_close_or_fuse_cannot_override() {
        for patch in [
            json!({"type":"warden"}),
            json!({"type":"ender_dragon"}),
            json!({"type":"wither"}),
            json!({"entity_id":null}),
            json!({"distance":3}),
            json!({"approaching":true}),
            json!({"fuse_active":true}),
        ] {
            let mut t = mob();
            t.as_object_mut()
                .unwrap()
                .extend(patch.as_object().unwrap().clone());
            assert_eq!(signature(&event(t, json!({})), 3000), None);
        }
        assert_eq!(
            signature(&event(mob(), json!({"hostiles_within_10":2})), 3000),
            None
        );
    }
    #[test]
    fn input_contract_requires_closed_action_confidence_and_verbatim_evidence() {
        let p = json!({"action":"resume_workshop","confidence":0.9,"evidence":"句の続きを"});
        assert_eq!(
            Analysis::parse(&p, "句の続きを話そう").action,
            Action::ResumeWorkshop
        );
        for (key, value) in [
            ("action", json!("save")),
            ("confidence", json!(0.7)),
            ("confidence", json!(true)),
            ("evidence", json!("別の文")),
            ("speech", json!("保存した")),
        ] {
            let mut bad = p.clone();
            bad[key] = value;
            assert_eq!(
                Analysis::parse(&bad, "句の続きを話そう").action,
                Action::Uncertain
            );
        }
    }

    #[test]
    fn allowed_actions_share_wire_names_and_pass_the_same_analysis_contract() {
        let names = Action::ALL.map(Action::name);
        assert_eq!(
            names.iter().collect::<std::collections::HashSet<_>>().len(),
            Action::ALL.len()
        );
        assert_eq!(serde_json::to_value(Action::ALL).unwrap(), json!(names));
        for action in Action::ALL {
            let wire = serde_json::to_value(action).unwrap();
            assert_eq!(
                serde_json::from_value::<Action>(wire.clone()).unwrap(),
                action
            );
            let payload = json!({"action":wire,"confidence":0.75,"evidence":"句の相談"});
            assert_eq!(Analysis::parse(&payload, "句の相談をしよう").action, action);
        }
        assert!(serde_json::from_value::<Action>(json!("save")).is_err());
    }
}
