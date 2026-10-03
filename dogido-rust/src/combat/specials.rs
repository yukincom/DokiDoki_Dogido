//! ボス・奇襲・環境付き戦闘の閉じた判断。I/O、LLM、音声再生はしない。
//!
//! `observe` は受信一件につき一度だけ、各選択メソッドは優先順に呼ぶ。
//! 選択メソッドの Some は発話を消費するので、候補の列挙目的で呼ばない。
//! 導火・通常近接奇襲・低体力・一般群れは core 所有。台詞の根拠は受信観測のみ。
mod bosses;
mod encounters;
mod environment;
mod words;

use super::{
    catalog,
    model::{LeafRequest, Mode, Scope, Settings, Speech, elapsed, label},
};
use crate::events::{
    EventName, GameEvent, HorizontalDirection as H, TimePhase, VerticalRelation as V, VisualThreat,
    Weather,
};
use crate::threats::identity;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};
use words::*;

#[derive(Clone, Debug, Default)]
pub struct Specials {
    dimension: Option<String>,
    warped_at: Option<u64>,
    return_ready_at: Option<u64>,
    seen: HashMap<String, u64>,
    fresh_visual: HashSet<String>,
    boss_seen: HashSet<String>,
    fresh_boss: HashSet<String>,
    boss_commented: HashMap<String, u64>,
    last_visual_at: Option<u64>,
    last_visual_types: HashSet<String>,
    last_sonic_at: Option<u64>,
    warden_attack: bool,
    warden_golems: bool,
    warden_extreme: bool,
    last_dragon_seen_at: Option<u64>,
    dragon_perched: bool,
    last_dragon_charge_at: Option<u64>,
    last_crystal_count: Option<i64>,
    pending_crystals: Option<i64>,
    last_crystal_at: Option<u64>,
    crystal_hint: bool,
    passive_seen: HashMap<String, u64>,
    neutral_commented: HashMap<String, u64>,
    close_flying: HashSet<String>,
    entered_flying: HashSet<String>,
    burning_ids: HashSet<String>,
    new_burning: HashSet<String>,
    water_handled: HashSet<String>,
    last_water_at: Option<u64>,
    last_rain_at: Option<u64>,
    last_burning_at: Option<u64>,
    last_rear_at: Option<u64>,
    screamed: HashMap<String, u64>,
    mass_latched: bool,
    ground_count: usize,
    last_omen: Option<String>,
    last_omen_at: Option<u64>,
    effects: HashSet<String>,
    entered_fatigue: bool,
    last_fatigue_at: Option<u64>,
    ominous_kind: Option<String>,
    ominous_seen_at: Option<u64>,
    ominous_comment_at: Option<u64>,
    // Observation severity changes each frame; cooldown priority belongs to the selected speech.
    ominous_comment_priority: u8,
    ominous_severity: u8,
    ominous_stage: u8,
}

/// Only accepted, fresh ominous observations can preempt an already selected lower reaction.
pub(crate) fn incoming_ominous_priority(event: &GameEvent, settings: &Settings) -> u8 {
    fresh_ominous(event, settings).map_or(0, |kind| {
        if kind == "warden_sonic_boom"
            && event.world.ominous_sound_recent_ms.unwrap_or(i64::MAX) as u64
                > settings.ms("warden_sonic_boom_fresh_ms")
        {
            0
        } else {
            severity(&kind)
        }
    })
}

pub(crate) fn ominous_reaction_priority(action: &Speech) -> u8 {
    action
        .leaf
        .as_ref()
        .filter(|leaf| {
            action.kind == "deep_dark_ominous_sound" && leaf.kind == "deep_dark_ominous_sound"
        })
        .and_then(|leaf| leaf.details["ominous_kind"].as_str())
        .map_or(0, severity)
}

impl Specials {
    pub fn has_fresh_boss(&self, e: &GameEvent) -> bool {
        e.visual_threats
            .iter()
            .any(|t| self.fresh_boss.contains(&identity(t)))
    }
    pub fn reset(&mut self) {
        *self = Self::default();
    }

    /// complete=false の音イベントでは、空の visual/effects/count で既観測を消さない。
    /// 戻り値 true の次元変更フレームは core が flush のみを出す。
    pub fn observe(&mut self, e: &GameEvent, now: u64, complete: bool, s: &Settings) -> bool {
        self.fresh_visual.clear();
        self.fresh_boss.clear();
        self.entered_flying.clear();
        self.new_burning.clear();
        self.entered_fatigue = false;
        let current = e
            .player
            .dimension
            .as_deref()
            .map(|v| v.trim().to_lowercase())
            .filter(|v| !v.is_empty());
        let changed = self.dimension.is_some() && current.is_some() && self.dimension != current;
        if changed {
            let returning = self.dimension.as_deref().is_some_and(is_other_dimension)
                && current.as_deref().is_some_and(is_overworld);
            self.reset();
            self.warped_at = Some(now);
            if returning {
                self.return_ready_at =
                    Some(now.saturating_add(s.ms("overworld_return_line_delay_ms")));
            }
        } else if self.dimension.is_none() && current.as_deref().is_some_and(|d| !is_overworld(d)) {
            self.warped_at = Some(now);
        }
        if current.is_some() {
            self.dimension = current;
        }
        if matches!(e.event.name, EventName::CombatEnded | EventName::PlayerDied) {
            self.reset_combat();
        }
        self.seen
            .retain(|_, at| now.saturating_sub(*at) < s.ms("hostile_comment_cooldown_ms"));
        self.screamed
            .retain(|_, at| now.saturating_sub(*at) < s.ms("hostile_comment_cooldown_ms"));
        self.passive_seen
            .retain(|_, at| now.saturating_sub(*at) <= s.ms("neutral_hostility_memory_ms"));
        self.neutral_commented.retain(|_, at| {
            now.saturating_sub(*at) < s.ms("neutral_turned_hostile_comment_cooldown_ms")
        });
        for mob in &e.passive_mobs {
            self.passive_seen.insert(norm(&mob.r#type), now);
        }
        if !e.visual_threats.is_empty() {
            self.last_visual_at = Some(now);
            self.last_visual_types = e.visual_threats.iter().map(|t| norm(&t.r#type)).collect();
        }
        let phase = dragon_phase(e);
        if phase.is_some()
            || e.visual_threats
                .iter()
                .any(|t| norm(&t.r#type) == "ender_dragon")
        {
            self.last_dragon_seen_at = Some(now);
        }
        if phase.as_deref().is_some_and(|p| !is_perch(p)) {
            self.dragon_perched = false;
        }
        if let Some(count) = e.combat.end_crystal_count {
            if self.last_crystal_count.is_some_and(|old| count < old) {
                self.pending_crystals = Some(count);
            }
            self.last_crystal_count = Some(count);
        }
        if complete {
            for t in &e.visual_threats {
                let id = identity(t);
                if !self.seen.contains_key(&id) {
                    self.fresh_visual.insert(id.clone());
                }
                if is_boss(&t.r#type) && !self.boss_seen.contains(&id) {
                    self.fresh_boss.insert(id.clone());
                }
                self.seen.insert(id.clone(), now);
                if is_boss(&t.r#type) {
                    self.boss_seen.insert(id);
                }
            }
            let flying: HashSet<_> = e
                .visual_threats
                .iter()
                .filter(|t| {
                    is_flying(&t.r#type)
                        && t.distance
                            .is_some_and(|d| d <= s.number("hostile_query_distance"))
                })
                .map(identity)
                .collect();
            self.entered_flying = flying.difference(&self.close_flying).cloned().collect();
            self.close_flying = flying;
            let burning: HashSet<_> = e
                .visual_threats
                .iter()
                .filter(|t| t.on_fire)
                .map(identity)
                .collect();
            self.new_burning = burning.difference(&self.burning_ids).cloned().collect();
            self.burning_ids = burning;
            let current_ids: HashSet<_> = e.visual_threats.iter().map(identity).collect();
            self.water_handled.retain(|id| current_ids.contains(id));
            let effects: HashSet<_> = e
                .player
                .active_status_effects
                .iter()
                .map(|v| norm(v))
                .collect();
            self.entered_fatigue = e.event.name == EventName::StatusSnapshot
                && effects.contains("mining_fatigue")
                && !self.effects.contains("mining_fatigue");
            self.effects = effects;
        }
        if complete || matches!(e.event.name, EventName::CombatEnded | EventName::PlayerDied) {
            let count = ground_count(e, s);
            if count == 0 && self.ground_count > 0 {
                self.mass_latched = false;
            }
            self.ground_count = count;
        }
        let raw = e
            .world
            .ominous_sound_kind
            .as_deref()
            .map(norm)
            .filter(|k| !k.is_empty());
        if raw.as_deref().is_some_and(|k| !ominous_context(e, k)) {
            self.ominous_kind = None;
            self.ominous_seen_at = None;
            self.ominous_severity = 0;
            self.ominous_stage = 0;
        } else if let Some(kind) = fresh_ominous(e, s) {
            if elapsed(now, self.ominous_seen_at, s.ms("ominous_sound_reset_ms")) {
                self.ominous_stage = 0;
            }
            self.ominous_severity = severity(&kind);
            self.ominous_kind = Some(kind);
            self.ominous_seen_at = Some(now);
        } else if elapsed(now, self.ominous_seen_at, s.ms("ominous_sound_reset_ms")) {
            self.ominous_kind = None;
            self.ominous_severity = 0;
            self.ominous_stage = 0;
        }
        changed
    }

    fn reset_combat(&mut self) {
        self.boss_seen.clear();
        self.last_sonic_at = None;
        self.warden_attack = false;
        self.warden_golems = false;
        self.warden_extreme = false;
        self.dragon_perched = false;
        self.last_dragon_charge_at = None;
        self.pending_crystals = None;
        self.crystal_hint = false;
    }

    fn leaf_speech(
        &mut self,
        mut speech: Speech,
        kind: &'static str,
        details: Value,
        temperature: f64,
    ) -> Speech {
        speech.leaf = Some(LeafRequest {
            kind: kind.to_owned(),
            details,
            temperature,
        });
        speech
    }
    pub fn boss_presence(&self, now: u64, s: &Settings) -> bool {
        !elapsed(
            now,
            self.last_visual_at,
            s.ms("boss_recent_visual_window_ms"),
        ) && self.last_visual_types.iter().any(|k| is_boss(k))
    }
    pub fn ominous_presence(&self, now: u64, s: &Settings) -> bool {
        self.ominous_kind.is_some()
            && !elapsed(now, self.ominous_seen_at, s.ms("ominous_sound_reset_ms"))
    }
    pub fn mass_latched(&self) -> bool {
        self.mass_latched
    }
    /// 通常の新規近接悲鳴も、水中生存の文脈では止める。
    pub fn water_survivor(e: &GameEvent, t: &VisualThreat) -> bool {
        daylight(e)
            && burns_in_daylight(&t.r#type)
            && t.in_water
            && !t.on_fire
            && t.environment
                .as_ref()
                .is_none_or(|state| state.touching_water == Some(true))
    }
}

#[cfg(test)]
mod tests;
