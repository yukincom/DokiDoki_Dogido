//! 通常敵の単体・群れ視認警告。ボス・聴覚・戦闘後の判断は未移植。
//! 記憶の時刻は呼び手の単調時計。AI、音声、履歴への書込みはここでは行わない。
mod groups;
use crate::events::{GameEvent, HorizontalDirection as H, VisualThreat};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::LazyLock;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub panic_distance: f64,
    pub rear_warning_distance: f64,
    pub recent_damage_window_ms: u64,
    pub hostile_comment_cooldown_ms: u64,
    pub multi_hostile_comment_cooldown_ms: u64,
    pub panic_scream_cooldown_ms: u64,
    pub hostile_mass_callout_threshold: usize,
    pub hostile_query_distance: f64,
    pub other_realm_swarm_visual_threshold: usize,
    pub other_realm_audio_generic_threshold: usize,
    pub battle_speed: f64,
    pub cue_dir: PathBuf,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            panic_distance: 7.0,
            rear_warning_distance: 3.0,
            recent_damage_window_ms: 3000,
            hostile_comment_cooldown_ms: 60_000,
            multi_hostile_comment_cooldown_ms: 30_000,
            panic_scream_cooldown_ms: 1200,
            hostile_mass_callout_threshold: 4,
            hostile_query_distance: 16.0,
            other_realm_swarm_visual_threshold: 4,
            other_realm_audio_generic_threshold: 2,
            battle_speed: 1.0,
            cue_dir: "cue_voice".into(),
        }
    }
}
impl Settings {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            self.panic_distance.is_finite()
                && self.panic_distance >= 0.0
                && self.rear_warning_distance.is_finite()
                && self.rear_warning_distance >= 0.0
                && self.hostile_query_distance.is_finite()
                && self.hostile_query_distance >= 0.0
                && self.battle_speed.is_finite()
                && self.battle_speed > 0.0,
            "invalid warning settings"
        );
        Ok(())
    }
}
#[derive(Deserialize)]
struct Catalog {
    labels: HashMap<String, String>,
    ranged: HashSet<String>,
    effective_range: HashMap<String, f64>,
}
static CATALOG: LazyLock<Catalog> = LazyLock::new(|| {
    serde_json::from_str(include_str!("threat_catalog.json")).expect("checked catalog")
});

pub fn identity(t: &VisualThreat) -> String {
    t.entity_id
        .as_ref()
        .filter(|id| !id.is_empty())
        .cloned()
        .unwrap_or_else(|| {
            let dir = t
                .direction
                .horizontal
                .map(|d| {
                    serde_json::to_value(d)
                        .unwrap()
                        .as_str()
                        .unwrap()
                        .to_owned()
                })
                .unwrap_or_else(|| "nearby".into());
            format!("{}:{dir}", t.r#type)
        })
}
pub(crate) fn direction(t: &VisualThreat) -> &'static str {
    match t.direction.horizontal {
        Some(H::Front) => "前",
        Some(H::FrontRight) => "右前",
        Some(H::Right) => "右",
        Some(H::BackRight) => "右後ろ",
        Some(H::Back) => "後ろ",
        Some(H::BackLeft) => "左後ろ",
        Some(H::Left) => "左",
        Some(H::FrontLeft) => "左前",
        None => "近く",
    }
}
fn rear(t: &VisualThreat) -> bool {
    matches!(
        t.direction.horizontal,
        Some(H::Back | H::BackLeft | H::BackRight)
    )
}
pub fn visual_text(t: &VisualThreat, panic: bool) -> String {
    if t.r#type == "charged_creeper" {
        return "うわああっ！帯電クリーパーや！！逃げろぉ！！".into();
    }
    let dir = direction(t);
    let name = CATALOG.labels.get(&t.r#type).unwrap_or(&t.r#type);
    let variants = if panic {
        [
            format!("{dir}！ {name}や！"),
            format!("うわっ、{dir}に{name}や！"),
            format!("{dir}や！ {name}来とる！"),
        ]
    } else {
        [
            format!("{dir}に{name}おるで。"),
            format!("ひっ、{dir}に{name}おる。"),
            format!("{dir}や、{name}見えとるで。"),
        ]
    };
    variants[identity(t).chars().map(|c| c as usize).sum::<usize>() % 3].clone()
}
fn fuse_text(t: &VisualThreat) -> String {
    let dir = direction(t);
    if t.r#type == "charged_creeper" {
        format!("{dir}！ 帯電クリーパー膨らんどる！ 爆発するでぇ！！")
    } else {
        format!("{dir}！ クリーパー膨らんどる、爆発するでぇ！")
    }
}
fn fusing(t: &VisualThreat) -> bool {
    matches!(t.r#type.as_str(), "creeper" | "charged_creeper") && t.fuse_active == Some(true)
}

#[derive(Clone, Debug, Serialize)]
pub struct Cue {
    pub id: &'static str,
    pub text: &'static str,
    pub file: &'static str,
}
#[derive(Clone, Debug, Serialize)]
pub struct Warning {
    pub target: String,
    pub hostile_type: String,
    pub horizontal: Option<H>,
    pub kind: &'static str,
    pub text: String,
    pub cue: Option<Cue>,
    pub cue_sequence: Vec<String>,
    pub group_counts: Vec<(String, usize)>,
    pub group_support: Vec<String>,
    pub suppressed: bool,
}
impl Warning {
    /// 同じ未完了警告を現在の方向へ更新する。通常cooldownの新規警告ではない。
    pub fn relocated(
        &self,
        event: &GameEvent,
        settings: &Settings,
        cue_started: bool,
    ) -> Option<Self> {
        if self.kind != "creeper_fuse"
            && self.kind != "close_ambush"
            && (!self.group_counts.is_empty() || groups::has_report(event, settings))
        {
            let mut next = groups::refresh(self, event, settings)?;
            if !cue_started {
                next.cue = self.cue.clone();
            }
            return Some(next);
        }
        let t = event.visual_threats.iter().find(|t| {
            identity(t) == self.target
                && t.r#type == self.hostile_type
                && (self.kind != "creeper_fuse" || fusing(t))
        })?;
        let mut next = self.clone();
        next.horizontal = t.direction.horizontal;
        if cue_started {
            next.cue = None;
        }
        next.text = match self.kind {
            "creeper_fuse" => fuse_text(t),
            "visual_hostile" => visual_text(t, is_panic(t, event, settings)),
            _ => String::new(),
        };
        if self.suppressed && self.kind == "visual_hostile" {
            next.text = format!("{}……", direction(t));
        }
        (!next.text.is_empty() || next.cue.is_some()).then_some(next)
    }
    pub fn applicable(&self, event: &GameEvent, settings: &Settings) -> bool {
        if !self.group_counts.is_empty() {
            return groups::refresh(self, event, settings)
                .is_some_and(|next| next.kind == self.kind && next.text == self.text);
        }
        if self.kind == "visual_hostile" && groups::has_report(event, settings) {
            return false;
        }
        event.visual_threats.iter().any(|t| {
            identity(t) == self.target
                && t.r#type == self.hostile_type
                && t.direction.horizontal == self.horizontal
                && (self.kind != "creeper_fuse" || fusing(t))
        })
    }
    pub fn display_text(&self) -> String {
        [
            self.cue.as_ref().map(|c| c.text).unwrap_or(""),
            self.text.as_str(),
        ]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
    }
}
#[derive(Clone, Default)]
pub struct Policy {
    seen: HashMap<String, u64>,
    screamed: HashMap<String, u64>,
    commented: HashMap<String, u64>,
    heard: HashMap<String, u64>,
    active_fuses: HashSet<String>,
    last_callout: Option<u64>,
    last_cue: Option<u64>,
    last_single: Option<(String, u64)>,
    increase_ids: HashSet<String>,
    last_ground_count: usize,
}
fn elapsed(now: u64, at: Option<u64>, window: u64) -> bool {
    at.is_none_or(|at| now.saturating_sub(at) >= window)
}
impl Policy {
    pub fn begin_frame(&mut self, now: u64, s: &Settings) {
        for entries in [
            &mut self.seen,
            &mut self.screamed,
            &mut self.commented,
            &mut self.heard,
        ] {
            entries.retain(|_, at| now.saturating_sub(*at) < s.hostile_comment_cooldown_ms);
        }
    }
    pub fn finish_frame(&mut self, e: &GameEvent, now: u64, s: &Settings) {
        for t in &e.visual_threats {
            self.seen.insert(identity(t), now);
        }
        self.update_group_presence(e, s);
        let current: HashSet<_> = e.visual_threats.iter().map(identity).collect();
        self.increase_ids.retain(|id| current.contains(id));
        self.active_fuses = e
            .visual_threats
            .iter()
            .filter(|t| fusing(t))
            .map(identity)
            .collect();
    }
    pub fn mark_handled(&mut self, ids: &[String], single: Option<&str>, now: u64) {
        for id in ids {
            self.commented.insert(id.clone(), now);
        }
        self.last_callout = Some(now);
        self.last_single = single.map(|kind| (kind.to_owned(), now));
    }
    pub fn mark_commented(&mut self, ids: &[String], now: u64) {
        for id in ids {
            self.commented.insert(id.clone(), now);
        }
    }
    pub fn mark_heard(&mut self, ids: &[String], now: u64) {
        for id in ids {
            self.heard.insert(id.clone(), now);
        }
    }
    pub fn mark_cue(&mut self, now: u64) {
        self.last_cue = Some(now);
    }
    pub fn common_cue_ready(&self, now: u64, s: &Settings) -> bool {
        elapsed(now, self.last_cue, s.panic_scream_cooldown_ms)
    }
    pub fn commented_recent(&self, id: &str, now: u64, s: &Settings) -> bool {
        !elapsed(
            now,
            self.commented.get(id).copied(),
            s.hostile_comment_cooldown_ms,
        )
    }
    pub fn priority_cooldown(&self, now: u64, s: &Settings) -> bool {
        !elapsed(now, self.last_callout, s.multi_hostile_comment_cooldown_ms)
    }
    pub fn fuse(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Warning> {
        let t = e
            .visual_threats
            .iter()
            .filter(|t| fusing(t) && !self.active_fuses.contains(&identity(t)))
            .min_by(|a, b| {
                (a.r#type != "charged_creeper")
                    .cmp(&(b.r#type != "charged_creeper"))
                    .then_with(|| {
                        a.distance
                            .unwrap_or(f64::INFINITY)
                            .total_cmp(&b.distance.unwrap_or(f64::INFINITY))
                    })
            })?;
        Some(self.plan(
            t,
            "creeper_fuse",
            fuse_text(t),
            Some(("spot_hostile_gasp", "ひいっ！")),
            e,
            now,
            s,
        ))
    }
    pub fn close_ambush(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Warning> {
        if !self.cue_allowed(e, now, s) {
            return None;
        }
        let known = e.visual_threats.len() == 1
            || e.visual_threats.iter().any(|t| {
                let id = identity(t);
                self.seen.contains_key(&id)
                    || self.commented.contains_key(&id)
                    || self.screamed.contains_key(&id)
            });
        if !known {
            return None;
        }
        let t = e
            .visual_threats
            .iter()
            .filter(|t| {
                let id = identity(t);
                !daylight_water_survivor(t, e)
                    && t.distance.is_some_and(|d| d <= 3.0)
                    && !self.seen.contains_key(&id)
                    && !self.commented.contains_key(&id)
                    && !self.screamed.contains_key(&id)
            })
            .min_by(|a, b| a.distance.unwrap().total_cmp(&b.distance.unwrap()))?;
        self.screamed.insert(identity(t), now);
        Some(self.plan(
            t,
            "close_ambush",
            String::new(),
            Some(("panic_scream_start", "きゃー！")),
            e,
            now,
            s,
        ))
    }
    pub fn has_close_ambush(&self, e: &GameEvent) -> bool {
        let known = e.visual_threats.len() == 1
            || e.visual_threats.iter().any(|t| {
                let id = identity(t);
                self.seen.contains_key(&id)
                    || self.commented.contains_key(&id)
                    || self.screamed.contains_key(&id)
            });
        known
            && e.visual_threats.iter().any(|t| {
                let id = identity(t);
                !daylight_water_survivor(t, e)
                    && t.distance.is_some_and(|d| d <= 3.0)
                    && !self.seen.contains_key(&id)
                    && !self.commented.contains_key(&id)
                    && !self.screamed.contains_key(&id)
            })
    }
    pub fn ordinary(
        &mut self,
        e: &GameEvent,
        now: u64,
        s: &Settings,
        suppressed: bool,
    ) -> Option<Warning> {
        let prior_cue = self.last_cue;
        let mut plan = if let [t] = e.visual_threats.as_slice() {
            if t.r#type == "ender_dragon" {
                return None;
            }
            self.single_regular(t, e, now, s)
        } else if e.visual_threats.len() >= 2 {
            self.group_regular(e, now, s)
        } else {
            None
        }?;
        if suppressed {
            plan.soften(e);
            self.last_cue = prior_cue;
        }
        Some(plan)
    }
    /// busyの間は通常警告を消費しない。新規導火だけ現在の音声に優先する。
    pub fn observe(
        &mut self,
        e: &GameEvent,
        now: u64,
        busy: bool,
        s: &Settings,
    ) -> Option<Warning> {
        for entries in [&mut self.seen, &mut self.screamed, &mut self.commented] {
            entries.retain(|_, at| now.saturating_sub(*at) < s.hostile_comment_cooldown_ms);
        }
        let fuse = e
            .visual_threats
            .iter()
            .filter(|t| fusing(t) && !self.active_fuses.contains(&identity(t)))
            .min_by(|a, b| {
                (a.r#type != "charged_creeper")
                    .cmp(&(b.r#type != "charged_creeper"))
                    .then_with(|| {
                        a.distance
                            .unwrap_or(f64::INFINITY)
                            .total_cmp(&b.distance.unwrap_or(f64::INFINITY))
                    })
            });
        let plan = if let Some(t) = fuse {
            Some(self.plan(
                t,
                "creeper_fuse",
                fuse_text(t),
                Some(("spot_hostile_gasp", "ひいっ！")),
                e,
                now,
                s,
            ))
        } else if busy {
            None
        } else if let [t] = e.visual_threats.as_slice() {
            // この段階は通常の単独敵。専用の環境・ボス・後方奇襲規則は後続で移す。
            let supported = groups::ordinary(t);
            let special_rear = matches!(e.event.name, crate::events::EventName::ThreatApproaching)
                && t.direction.horizontal == Some(H::Back)
                && !CATALOG.ranged.contains(&t.r#type)
                && t.distance.is_some_and(|d| d <= s.rear_warning_distance);
            if !supported || special_rear {
                None
            } else {
                self.single(t, e, now, s)
            }
        } else if e.visual_threats.len() >= 2 && e.visual_threats.iter().all(groups::ordinary) {
            self.group(e, now, s)
        } else {
            None
        };
        for t in &e.visual_threats {
            self.seen.insert(identity(t), now);
        }
        self.update_group_presence(e, s);
        let current_ids: HashSet<_> = e.visual_threats.iter().map(identity).collect();
        self.increase_ids.retain(|id| current_ids.contains(id));
        self.active_fuses = e
            .visual_threats
            .iter()
            .filter(|t| fusing(t))
            .map(identity)
            .collect();
        plan
    }
    fn single(
        &mut self,
        t: &VisualThreat,
        e: &GameEvent,
        now: u64,
        s: &Settings,
    ) -> Option<Warning> {
        let key = identity(t);
        let cue_allowed = self.cue_allowed(e, now, s);
        if t.distance.is_some_and(|d| d <= 3.0)
            && !self.seen.contains_key(&key)
            && !self.screamed.contains_key(&key)
            && !self.commented.contains_key(&key)
            && cue_allowed
        {
            self.screamed.insert(key, now);
            return Some(self.plan(
                t,
                "close_ambush",
                String::new(),
                Some(("panic_scream_start", "きゃー！")),
                e,
                now,
                s,
            ));
        }
        if !elapsed(
            now,
            self.commented.get(&key).copied(),
            s.hostile_comment_cooldown_ms,
        ) || !elapsed(now, self.last_callout, s.multi_hostile_comment_cooldown_ms)
        {
            return None;
        }
        let panic = is_panic(t, e, s);
        let gasp = spotted_gasp(t, s);
        Some(self.plan(
            t,
            "visual_hostile",
            visual_text(t, panic),
            gasp.then_some(("spot_hostile_gasp", "ハッ")),
            e,
            now,
            s,
        ))
    }
    pub fn cue_allowed(&self, e: &GameEvent, now: u64, s: &Settings) -> bool {
        e.world
            .biome
            .as_deref()
            .unwrap_or("")
            .strip_prefix("minecraft:")
            .unwrap_or(e.world.biome.as_deref().unwrap_or(""))
            != "deep_dark"
            && elapsed(now, self.last_cue, s.panic_scream_cooldown_ms)
    }
    #[allow(clippy::too_many_arguments)]
    fn plan(
        &mut self,
        t: &VisualThreat,
        kind: &'static str,
        text: String,
        cue: Option<(&'static str, &'static str)>,
        e: &GameEvent,
        now: u64,
        s: &Settings,
    ) -> Warning {
        let key = identity(t);
        let cue = cue
            .filter(|_| self.cue_allowed(e, now, s))
            .map(|(id, text)| {
                self.last_cue = Some(now);
                Cue {
                    id,
                    text,
                    file: if id == "panic_scream_start" {
                        "panic/universfield-man-scream-08-352438.mp3"
                    } else {
                        "panic/freesound_community-male-gasp-1-7183.mp3"
                    },
                }
            });
        if !text.is_empty() {
            self.commented.insert(key.clone(), now);
            self.last_callout = Some(now);
            self.last_single = Some((t.r#type.clone(), now));
        }
        Warning {
            target: key,
            hostile_type: t.r#type.clone(),
            horizontal: t.direction.horizontal,
            kind,
            text,
            cue,
            cue_sequence: Vec::new(),
            group_counts: Vec::new(),
            group_support: Vec::new(),
            suppressed: false,
        }
    }
    fn single_regular(
        &mut self,
        t: &VisualThreat,
        e: &GameEvent,
        now: u64,
        s: &Settings,
    ) -> Option<Warning> {
        let key = identity(t);
        if self.commented_recent(&key, now, s)
            || !elapsed(
                now,
                self.heard.get(&key).copied(),
                s.hostile_comment_cooldown_ms,
            )
            || self.priority_cooldown(now, s)
        {
            return None;
        }
        Some(self.plan(
            t,
            "visual_hostile",
            visual_text(t, is_panic(t, e, s)),
            spotted_gasp(t, s).then_some(("spot_hostile_gasp", "ハッ")),
            e,
            now,
            s,
        ))
    }
}

impl Warning {
    pub fn soften(&mut self, e: &GameEvent) {
        self.suppressed = true;
        self.cue = None;
        if self.kind == "visual_hostile" {
            if let Some(t) = e.visual_threats.iter().find(|t| identity(t) == self.target) {
                self.text = format!("{}……", direction(t));
            }
        } else if self.kind == "hostile_massive" {
            self.text = "敵がぎょうさんおる……。".into();
        } else if self.kind == "hostile_overwhelmed" {
            let parts = self
                .group_support
                .iter()
                .filter_map(|id| e.visual_threats.iter().find(|t| identity(t) == *id))
                .map(|t| {
                    format!(
                        "{}に{}",
                        direction(t),
                        CATALOG.labels.get(&t.r#type).unwrap_or(&t.r#type)
                    )
                })
                .collect::<Vec<_>>();
            self.text = if parts.is_empty() {
                "あかんあかんあかん……もうあかん……。".into()
            } else {
                format!("あかんあかん……。{}……。", parts.join("、"))
            };
        } else if self.kind == "hostile_count" {
            self.text = self.text.trim_end_matches("おるで。").to_owned() + "おる……。";
            if let Some(last) = self.cue_sequence.last_mut() {
                *last = "common/phrases/ga_orude".into();
            }
        }
    }
}
fn daylight_water_survivor(t: &VisualThreat, e: &GameEvent) -> bool {
    matches!(
        e.world.time_phase,
        Some(crate::events::TimePhase::Morning | crate::events::TimePhase::Day)
    ) && e.world.sky_visible == Some(true)
        && t.in_water
        && !t.on_fire
        && matches!(
            t.r#type.as_str(),
            "skeleton" | "zombie" | "drowned" | "zombie_villager" | "zombified_piglin" | "phantom"
        )
}

pub fn ground_count(e: &GameEvent, s: &Settings) -> usize {
    groups::ground_count(e, s)
}
pub fn highest(e: &GameEvent) -> Option<&VisualThreat> {
    e.visual_threats
        .iter()
        .min_by(|a, b| groups::priority(a, b))
}

fn is_panic(t: &VisualThreat, e: &GameEvent, s: &Settings) -> bool {
    t.distance
        .is_some_and(|d| d <= s.panic_distance || rear(t) && d <= s.rear_warning_distance)
        || e.combat.hostiles_within_10.is_some_and(|n| n >= 2)
        || e.combat
            .recent_damage_ms
            .is_some_and(|ms| ms >= 0 && ms as u64 <= s.recent_damage_window_ms)
}

pub fn spotted_gasp(t: &VisualThreat, s: &Settings) -> bool {
    t.distance.is_some_and(|d| {
        rear(t) && d <= s.rear_warning_distance
            || if CATALOG.ranged.contains(&t.r#type) {
                d <= CATALOG
                    .effective_range
                    .get(&t.r#type)
                    .copied()
                    .unwrap_or(6.0)
                    + 1.5
            } else {
                d <= 6.0 || t.approaching && d <= 7.0
            }
    })
}
