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
fn direction(t: &VisualThreat) -> &'static str {
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
#[derive(Default)]
pub struct Policy {
    seen: HashMap<String, u64>,
    screamed: HashMap<String, u64>,
    commented: HashMap<String, u64>,
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
    fn cue_allowed(&self, e: &GameEvent, now: u64, s: &Settings) -> bool {
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
        }
    }
}

fn is_panic(t: &VisualThreat, e: &GameEvent, s: &Settings) -> bool {
    t.distance
        .is_some_and(|d| d <= s.panic_distance || rear(t) && d <= s.rear_warning_distance)
        || e.combat.hostiles_within_10.is_some_and(|n| n >= 2)
        || e.combat
            .recent_damage_ms
            .is_some_and(|ms| ms >= 0 && ms as u64 <= s.recent_damage_window_ms)
}

fn spotted_gasp(t: &VisualThreat, s: &Settings) -> bool {
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
