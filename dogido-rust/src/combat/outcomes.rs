//! 観測根拠のある死亡・撃破・爆散と、戦闘から抜けた後の安堵。
//! 視認消失・死亡音・経験値だけではプレイヤー撃破へ昇格させない。
use super::{
    catalog,
    model::{LeafRequest, Scope, Settings, Speech, label},
};
use crate::events::{EventName, GameEvent, HostileOutcome, HostileOutcomeOutcome as Outcome};
use serde_json::{Value, json};
use std::collections::{HashSet, VecDeque};
use std::sync::LazyLock;

static DEATH: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../data/fallbacks/death.json")).expect("death catalog")
});
static AFTERMATH: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../data/fallbacks/aftermath.json"))
        .expect("aftermath catalog")
});

#[derive(Clone, Debug)]
struct Hostile {
    kind: String,
    id: Option<String>,
}

#[derive(Debug)]
pub struct Outcomes {
    default_call_name: String,
    last_hostiles: Vec<Hostile>,
    announced: HashSet<String>,
    announcement_order: VecDeque<String>,
    notes: Vec<String>,
}
impl Default for Outcomes {
    fn default() -> Self {
        Self::with_settings(&Settings::default())
    }
}

fn normalized(value: &str) -> &str {
    value.strip_prefix("minecraft:").unwrap_or(value).trim()
}
fn outcome_key(outcome: &HostileOutcome) -> String {
    outcome
        .entity_id
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| {
            format!(
                "legacy:{}:{:?}:{:?}",
                outcome.r#type, outcome.outcome, outcome.evidence
            )
        })
}
fn labels(outcomes: &[HostileOutcome], kind: Outcome) -> String {
    let mut result = Vec::new();
    for outcome in outcomes.iter().filter(|o| o.outcome == kind) {
        let value = label(&outcome.r#type);
        if !result.contains(&value) {
            result.push(value);
        }
    }
    if result.is_empty() {
        "敵".to_owned()
    } else {
        result.join("と")
    }
}
fn remaining(event: &GameEvent) -> bool {
    !event.visual_threats.is_empty()
        || !event.auditory_threats.is_empty()
        || [
            event.combat.hostiles_within_7,
            event.combat.hostiles_within_10,
            event.combat.hostiles_within_scan_ground,
            event.combat.hostiles_within_30_ground,
        ]
        .iter()
        .any(|count| count.is_some_and(|count| count > 0))
}
fn call_name<'a>(event: &'a GameEvent, default: &'a str) -> &'a str {
    event
        .meta
        .call_name
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .or_else(|| (!default.trim().is_empty()).then_some(default.trim()))
        .or(event
            .player
            .name
            .as_deref()
            .map(str::trim)
            .filter(|v| !v.is_empty()))
        .unwrap_or("プレイヤー")
}

impl Outcomes {
    pub fn with_settings(settings: &Settings) -> Self {
        Self {
            default_call_name: settings.text("default_call_name").to_owned(),
            last_hostiles: Vec::new(),
            announced: HashSet::new(),
            announcement_order: VecDeque::new(),
            notes: Vec::new(),
        }
    }
    /// 会話文脈への採用・保存は実行層が決める。ここではコードで確認した短い事実だけ。
    pub fn take_notes(&mut self) -> Vec<String> {
        std::mem::take(&mut self.notes)
    }
    pub fn has_boss_context(&self) -> bool {
        self.last_hostiles
            .iter()
            .any(|h| matches!(h.kind.as_str(), "warden" | "ender_dragon" | "wither"))
    }
    pub fn boss_defeat_confirmed(&self, event: &GameEvent) -> bool {
        self.last_hostiles.iter().all(|h| match h.kind.as_str() {
            "warden" => event.combat.warden_defeat_confirmed == Some(true),
            "ender_dragon" => event.combat.dragon_defeat_confirmed == Some(true),
            _ => true,
        })
    }
    pub fn clear_confirmed(event: &GameEvent) -> bool {
        event.event.name == EventName::CombatEnded && !remaining(event)
    }
    fn record(&mut self, outcomes: &[HostileOutcome]) {
        for outcome in outcomes {
            let key = outcome_key(outcome);
            if self.announced.insert(key.clone()) {
                self.announcement_order.push_back(key);
                // 長時間セッションでも一度だけの台帳を無限に増やさない。
                if self.announcement_order.len() > 4096
                    && let Some(old) = self.announcement_order.pop_front()
                {
                    self.announced.remove(&old);
                }
            }
            let name = label(&outcome.r#type);
            let note = match outcome.outcome {
                Outcome::PlayerKill => format!("{name}を倒した"),
                Outcome::CreeperDetonation => format!("{name}が爆発した"),
                Outcome::ExplosionDeath => format!("{name}が爆発で倒れた"),
                Outcome::OtherDeath => format!("{name}が倒れた"),
            };
            if !self.notes.contains(&note) {
                self.notes.push(note);
            }
            // 同種の生存個体がいれば残す。個体IDなしの旧通知では種だけで除去する。
            self.last_hostiles.retain(|hostile| {
                if let Some(id) = outcome
                    .entity_id
                    .as_ref()
                    .filter(|id| !id.trim().is_empty())
                {
                    hostile.id.as_ref().is_some_and(|known| known != id)
                        || hostile.kind != normalized(&outcome.r#type)
                } else {
                    hostile.kind != normalized(&outcome.r#type)
                }
            });
        }
    }
    fn fresh_outcomes(&self, event: &GameEvent) -> Vec<HostileOutcome> {
        let mut seen = HashSet::new();
        event
            .combat
            .hostile_outcomes
            .as_deref()
            .unwrap_or_default()
            .iter()
            .filter(|outcome| {
                let key = outcome_key(outcome);
                !self.announced.contains(&key) && seen.insert(key)
            })
            .cloned()
            .collect()
    }
    /// full/partialに関係なく現在の明示観測だけを受ける。空の部分イベントで記憶を消さない。
    pub fn observe(&mut self, event: &GameEvent, _now_ms: u64) -> Option<Speech> {
        if !event.visual_threats.is_empty() {
            self.last_hostiles = event
                .visual_threats
                .iter()
                .map(|v| Hostile {
                    kind: normalized(&v.r#type).to_owned(),
                    id: v.entity_id.clone(),
                })
                .collect();
        } else if !event.auditory_threats.is_empty() {
            let mut heard = Vec::new();
            for threat in &event.auditory_threats {
                let source = threat
                    .sound_event
                    .as_deref()
                    .unwrap_or_default()
                    .replace('/', ".");
                let kind = std::iter::once(normalized(&threat.label))
                    .chain(source.split('.'))
                    .find(|candidate| catalog::labels().get(*candidate).is_some());
                if let Some(kind) = kind {
                    heard.push(Hostile {
                        kind: kind.to_owned(),
                        id: threat.source_id.clone(),
                    });
                }
            }
            if !heard.is_empty() {
                self.last_hostiles = heard;
            }
        }
        if event.event.name == EventName::PlayerDied {
            let cause = event.meta.death_cause.as_deref().unwrap_or("unknown");
            let lower = cause.to_lowercase();
            let hostile = [
                "zombie", "creeper", "skeleton", "witch", "spider", "enderman",
            ]
            .into_iter()
            .find(|kind| lower.contains(kind));
            let key = if hostile.is_some() {
                "hostile"
            } else if ["fall", "fell", "void", "accident"]
                .iter()
                .any(|v| lower.contains(v))
            {
                "fall"
            } else {
                "default"
            };
            let text = DEATH[key].as_str().expect("death fallback").to_owned();
            let leaf = Some(LeafRequest {
                kind: "death".to_owned(),
                temperature: 0.2,
                details: json!({"cause":cause,"hostile":hostile.unwrap_or(""),"player_name":call_name(event, &self.default_call_name)}),
            });
            self.last_hostiles.clear();
            let mut speech = Speech::new("death", text);
            speech.interrupt = true;
            speech.leaf = leaf;
            return Some(speech);
        }
        if !matches!(
            event.event.name,
            EventName::HostileDefeated | EventName::CreeperDetonated
        ) {
            return None;
        }
        let all = self.fresh_outcomes(event);
        let outcomes: Vec<_> = all
            .into_iter()
            .filter(|o| {
                (o.outcome == Outcome::CreeperDetonation)
                    == (event.event.name == EventName::CreeperDetonated)
            })
            .collect();
        if outcomes.is_empty() {
            return None;
        }
        let (kind, text) = if event.event.name == EventName::CreeperDetonated {
            let charged = outcomes
                .iter()
                .filter(|o| normalized(&o.r#type) == "charged_creeper")
                .count();
            let normal = outcomes.len() - charged;
            let text = if charged > 0 {
                let count = if charged > 1 {
                    format!("が{charged}体も")
                } else {
                    String::new()
                };
                format!("うわああっ！ 帯電クリーパー{count}爆発したでぇ！！")
            } else if normal > 1 {
                format!("うわっ！ クリーパーが{normal}体も爆発したぁ！")
            } else {
                "うわっ！ クリーパー爆発したぁ！ びっくりしたやん！".to_owned()
            };
            ("creeper_detonated", text)
        } else {
            let text = if outcomes.iter().any(|o| o.outcome == Outcome::PlayerKill) {
                format!(
                    "よっしゃ！ {}倒したで！ ようやったぁ！",
                    labels(&outcomes, Outcome::PlayerKill)
                )
            } else if outcomes
                .iter()
                .any(|o| o.outcome == Outcome::ExplosionDeath)
            {
                format!(
                    "うわっ！ 爆発で{}倒れたで！ びっくりしたぁ！",
                    labels(&outcomes, Outcome::ExplosionDeath)
                )
            } else {
                format!(
                    "あっ、{}倒れたで。ひとまず一体減ったな。",
                    labels(&outcomes, Outcome::OtherDeath)
                )
            };
            ("hostile_defeated", text)
        };
        self.record(&outcomes);
        let mut speech = Speech::new(kind, text);
        speech.interrupt = true;
        Some(speech)
    }
    /// mode遷移・待ち時間・重複した終了イベントの排除はCore所有。
    pub fn aftermath(&mut self, event: &GameEvent, _now_ms: u64) -> Option<Speech> {
        let boss = self.has_boss_context();
        let confirmed_boss = boss && self.boss_defeat_confirmed(event);
        // ボス討伐確認直後は音の残響だけを許す。現在の視認個体は許さない。
        if !event.visual_threats.is_empty()
            || (!confirmed_boss && remaining(event))
            || (boss && !confirmed_boss)
        {
            return None;
        }
        let boss_kind = self
            .last_hostiles
            .iter()
            .find(|h| h.kind == "warden")
            .or_else(|| self.last_hostiles.iter().find(|h| h.kind == "ender_dragon"))
            .map(|h| h.kind.clone());
        let outcomes = self.fresh_outcomes(event);
        let mut leaf = None;
        let text = if let Some(kind) = boss_kind {
            catalog::text("boss", &[&kind, "defeated"])
        } else {
            let outcome = if outcomes.iter().any(|o| {
                o.outcome == Outcome::CreeperDetonation
                    && normalized(&o.r#type) == "charged_creeper"
            }) {
                "charged_creeper_detonated"
            } else if outcomes
                .iter()
                .any(|o| o.outcome == Outcome::CreeperDetonation)
            {
                "creeper_detonated"
            } else if outcomes.iter().any(|o| o.outcome == Outcome::PlayerKill) {
                "player_kill"
            } else if outcomes
                .iter()
                .any(|o| o.outcome == Outcome::ExplosionDeath)
            {
                "explosion_death"
            } else if !outcomes.is_empty() {
                "hostile_defeated"
            } else {
                "disengaged"
            };
            let fallback = AFTERMATH[outcome]
                .as_str()
                .expect("aftermath fallback")
                .to_owned();
            let mut hostiles = Vec::new();
            let kinds: Vec<_> = if outcomes.is_empty() {
                self.last_hostiles.iter().map(|h| h.kind.as_str()).collect()
            } else {
                outcomes.iter().map(|o| o.r#type.as_str()).collect()
            };
            for kind in kinds {
                let name = label(kind);
                if !hostiles.contains(&name) {
                    hostiles.push(name);
                }
            }
            hostiles.truncate(4);
            let health = match event.player.health {
                None => "不明",
                Some(v) if v <= 8.0 => "かなり減ってる",
                Some(v) if v <= 14.0 => "少し減ってる",
                _ => "まだ余力はある",
            };
            let clear = Self::clear_confirmed(event);
            leaf = Some(LeafRequest {
                kind: "aftermath".to_owned(),
                temperature: 0.2,
                details: json!({"player_name":call_name(event, &self.default_call_name),"hostiles":hostiles,"health_state":health,
                "hostile_clear_confirmed":clear,"remaining_hostiles":if clear {Some(0)} else {None},"combat_outcome":outcome}),
            });
            fallback
        };
        if outcomes.is_empty() && !self.last_hostiles.is_empty() {
            let mut names = Vec::new();
            for hostile in &self.last_hostiles {
                let name = label(&hostile.kind);
                if !names.contains(&name) {
                    names.push(name);
                }
            }
            names.truncate(3);
            self.notes.push(format!("{}と交戦した", names.join("、")));
        }
        self.record(&outcomes);
        self.last_hostiles.clear();
        let mut speech = Speech::new("aftermath", text);
        speech.leaf = leaf;
        speech.interrupt = event.event.name == EventName::CombatEnded || boss;
        speech.protect_ms = if boss { 2500 } else { 0 };
        speech.scope = if boss { Scope::Event } else { Scope::Safe };
        Some(speech)
    }
}

/// LLM文の採用前にもコードで戦闘結果より強い主張を棄却する。
pub fn claim_conflicts(outcome: &str, line: &str) -> bool {
    let text: String = line.chars().filter(|v| !v.is_whitespace()).collect();
    let kills = [
        "倒した",
        "倒せた",
        "やっつけた",
        "仕留めた",
        "討ち取った",
        "退治した",
        "撃破した",
        "片づけた",
        "片付けた",
    ];
    let death = [
        "倒れた",
        "死んだ",
        "くたばった",
        "撃破できた",
        "退治できた",
        "爆発した",
        "爆散した",
    ];
    let uncredited = matches!(
        outcome,
        "hostile_defeated"
            | "explosion_death"
            | "creeper_detonated"
            | "charged_creeper_detonated"
            | "disengaged"
    );
    (uncredited && kills.iter().any(|claim| text.contains(claim)))
        || (outcome == "disengaged" && death.iter().any(|claim| text.contains(claim)))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn event(name: &str, visual: Value, audio: Value, combat: Value, meta: Value) -> GameEvent {
        GameEvent::parse(json!({"schema_version":"2026-05-24","game":"minecraft-java","adapter":"dogido-fabric-client","observed_at":"2026-09-25T00:00:00Z","sequence":1,
            "event":{"name":name,"source_kind":"system","priority_hint":"normal","certainty":"high"},
            "player":{"health":12},"visual_threats":visual,"auditory_threats":audio,"combat":combat,"meta":meta})).unwrap()
    }
    fn mob(kind: &str, id: &str) -> Value {
        json!({"type":kind,"entity_id":id,"distance":8,"certainty":"high"})
    }
    fn outcome(kind: &str, id: &str, result: &str) -> Value {
        json!({"type":kind,"entity_id":id,"outcome":result,"evidence":if result=="creeper_detonation" {"explosion_packet"} else {"server_death_event"}})
    }
    fn end(outcomes: Value) -> GameEvent {
        event(
            "combat_ended",
            json!([]),
            json!([]),
            json!({"hostile_outcomes":outcomes}),
            json!({}),
        )
    }
    #[test]
    fn deaths_are_once_and_not_reused_for_next_disengagement() {
        let mut state = Outcomes::default();
        state.observe(
            &event(
                "status_snapshot",
                json!([mob("creeper", "c1")]),
                json!([]),
                json!({}),
                json!({}),
            ),
            0,
        );
        let dead = outcome("creeper", "c1", "other_death");
        let defeat = event(
            "hostile_defeated",
            json!([]),
            json!([]),
            json!({"hostile_outcomes":[dead.clone()]}),
            json!({}),
        );
        assert!(
            state
                .observe(&defeat, 1)
                .unwrap()
                .text
                .contains("クリーパー倒れた")
        );
        assert!(state.observe(&defeat, 2).is_none());
        let speech = state.aftermath(&end(json!([dead])), 3).unwrap();
        assert!(speech.text.contains("気配は遠のいた"));
        assert_eq!(speech.leaf.unwrap().details["hostiles"], json!([]));
        state.observe(
            &event(
                "status_snapshot",
                json!([mob("enderman", "e1")]),
                json!([]),
                json!({}),
                json!({}),
            ),
            4,
        );
        let speech = state.aftermath(&end(json!([])), 5).unwrap();
        assert_eq!(
            speech.leaf.unwrap().details["hostiles"],
            json!(["エンダーマン"])
        );
    }
    #[test]
    fn outcome_evidence_drives_credit_and_claim_validation() {
        for (result, expected) in [
            ("player_kill", "player_kill"),
            ("other_death", "hostile_defeated"),
            ("explosion_death", "explosion_death"),
            ("creeper_detonation", "creeper_detonated"),
        ] {
            let mut state = Outcomes::default();
            let leaf = state
                .aftermath(&end(json!([outcome("creeper", "c1", result)])), 0)
                .unwrap()
                .leaf
                .unwrap();
            assert_eq!(leaf.details["combat_outcome"], expected);
            assert_eq!(leaf.details["remaining_hostiles"], 0);
            assert_eq!(
                claim_conflicts(expected, "よう倒したな！"),
                result != "player_kill"
            );
        }
        let mut state = Outcomes::default();
        let speech = state.aftermath(&end(Value::Null), 0).unwrap();
        assert_eq!(speech.leaf.unwrap().details["combat_outcome"], "disengaged");
        assert!(claim_conflicts("disengaged", "敵が倒れたな"));
        assert!(!claim_conflicts("hostile_defeated", "敵が倒れたな"));
    }
    #[test]
    fn charged_and_multiple_detonations_have_distinct_lines() {
        for (kind, count, phrase) in [
            ("creeper", 1, "クリーパー爆発したぁ"),
            ("creeper", 2, "クリーパーが2体も"),
            ("charged_creeper", 1, "帯電クリーパー爆発"),
            ("charged_creeper", 2, "帯電クリーパーが2体も"),
        ] {
            let mut state = Outcomes::default();
            let outcomes: Vec<_> = (0..count)
                .map(|i| outcome(kind, &format!("c{i}"), "creeper_detonation"))
                .collect();
            let e = event(
                "creeper_detonated",
                json!([]),
                json!([]),
                json!({"hostile_outcomes":outcomes}),
                json!({}),
            );
            assert!(state.observe(&e, 0).unwrap().text.contains(phrase));
            assert!(state.observe(&e, 1).is_none());
        }
    }
    #[test]
    fn no_relief_with_remaining_enemy_and_boss_needs_explicit_defeat() {
        for (visual, audio, combat) in [
            (json!([mob("zombie", "z1")]), json!([]), json!({})),
            (json!([]), json!([{"label":"zombie"}]), json!({})),
            (
                json!([]),
                json!([]),
                json!({"hostiles_within_scan_ground":1}),
            ),
        ] {
            let e = event("combat_ended", visual, audio, combat, json!({}));
            assert!(!Outcomes::clear_confirmed(&e));
            assert!(Outcomes::default().aftermath(&e, 0).is_none());
        }
        let mut state = Outcomes::default();
        state.observe(
            &event(
                "status_snapshot",
                json!([mob("warden", "w1")]),
                json!([]),
                json!({}),
                json!({}),
            ),
            0,
        );
        assert!(state.has_boss_context());
        assert!(!state.boss_defeat_confirmed(&end(json!([]))));
        assert!(state.aftermath(&end(json!([])), 1).is_none());
        let e = event(
            "combat_ended",
            json!([]),
            json!([{"label":"warden"}]),
            json!({"warden_defeat_confirmed":true}),
            json!({}),
        );
        let speech = state.aftermath(&e, 2).unwrap();
        assert_eq!(speech.text, catalog::text("boss", &["warden", "defeated"]));
        assert_eq!(speech.protect_ms, 2500);
        assert!(speech.leaf.is_none());
    }
    #[test]
    fn death_fallbacks_and_leaf_details_match_source() {
        for (cause, key) in [
            ("slain by zombie", "hostile"),
            ("fell out of world", "fall"),
            ("lava", "default"),
        ] {
            let mut state = Outcomes::default();
            let e = event(
                "player_died",
                json!([]),
                json!([]),
                json!({}),
                json!({"death_cause":cause,"call_name":"テスター"}),
            );
            let speech = state.observe(&e, 0).unwrap();
            assert_eq!(speech.text, DEATH[key].as_str().unwrap());
            let leaf = speech.leaf.unwrap();
            assert_eq!(leaf.details["cause"], cause);
            assert_eq!(leaf.details["player_name"], "テスター");
        }
    }
    #[test]
    fn audio_only_last_enemy_is_retained_without_invented_kill() {
        let mut state = Outcomes::default();
        state.observe(
            &event(
                "hostile_audio_detected",
                json!([]),
                json!([{"label":"unknown","sound_event":"minecraft:entity.spider.ambient"}]),
                json!({}),
                json!({}),
            ),
            0,
        );
        let speech = state.aftermath(&end(json!([])), 1).unwrap();
        assert_eq!(
            speech.leaf.unwrap().details["hostiles"],
            json!(["スパイダー"])
        );
        assert_eq!(state.take_notes(), ["スパイダーと交戦した"]);
    }
}
