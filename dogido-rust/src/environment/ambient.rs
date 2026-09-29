//! Non-urgent environment decisions. No I/O, model calls, playback, or world actions.
mod catalog;
mod light;
mod mobs;
mod places;
mod smell;
mod surroundings;
#[cfg(test)]
mod tests;

use crate::{
    combat::model::{LeafRequest, Mode, Scope, Settings, Speech, elapsed},
    events::{EventName, GameEvent},
};
pub(crate) use catalog::biome_label;
pub(crate) use catalog::general as general_text;
pub use light::{LightContext, LightPlanRequest};
use serde_json::{Value, json};
pub(crate) use smell::is_query as is_smell_query;
use std::collections::HashMap;
pub use surroundings::player_vehicle_fact;
/// 配送時に明示質問の返答だけを現在観測から作り直す。状態を進めない。
pub fn current_smell_reply(event: &GameEvent) -> Speech {
    smell::speech(event)
}

#[derive(Clone, Debug, Default)]
pub struct AmbientFocus {
    pub foreground: bool,
    pub casual_foreground: bool,
    pub last_player_input_at: Option<u64>,
    pub player_priority: bool,
    pub boss_presence: bool,
    pub ominous_presence: bool,
    pub submerged: bool,
    pub safe_zone_with_door: bool,
    pub light: LightContext,
}
#[derive(Clone, Default)]
pub struct Ambient {
    smell: smell::Presence,
    places: places::Places,
    last_danger: Option<u64>,
    mob_comments: HashMap<String, u64>,
    last_player_input: Option<u64>,
    dimension: Option<String>,
    inventory: Option<i64>,
    gain: Option<(i64, i64)>,
    last_light_comment: Option<u64>,
    light_serial: u64,
    light_pending: Option<LightPlanRequest>,
    light_ready: Option<LightPlanRequest>,
    firefly_reacted: bool,
    last_eye: Option<u64>,
    weather: Option<String>,
    weather_pending: Option<(String, String)>,
}
pub fn defaults() -> Settings {
    let mut s = Settings::default();
    let extra: serde_json::Map<String, Value> =
        serde_json::from_str(include_str!("ambient_defaults.json")).expect("ambient defaults");
    s.0.extend(extra);
    s
}
impl Ambient {
    pub fn update(&mut self, e: &GameEvent, now: u64, complete: bool, s: &Settings) {
        let dimension = e
            .player
            .dimension
            .as_deref()
            .unwrap_or("")
            .trim()
            .to_owned();
        if !dimension.is_empty() {
            if self.dimension.as_ref().is_some_and(|old| old != &dimension) {
                self.smell = smell::Presence::default();
                self.places.dimension_changed();
                self.last_danger = None;
                self.firefly_reacted = false;
                self.weather = None;
                self.weather_pending = None;
                self.gain = None;
                self.light_pending = None;
                self.light_ready = None;
            }
            self.dimension = Some(dimension);
        }
        if e.meta
            .user_text
            .as_deref()
            .is_some_and(|t| !t.trim().is_empty())
        {
            self.note_player_input(now);
        }
        // Missing fields in partial audio/mob events are not absence observations.
        if complete || e.smell_observation.is_some() || !e.zombie_scent_clues.is_empty() {
            self.smell.update(e);
        }
        if !e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
            || e.combat
                .recent_damage_ms
                .is_some_and(|age| age <= s.ms("recent_damage_window_ms") as i64)
        {
            self.last_danger = Some(now);
        }
        if complete {
            self.places.update(e, now, s);
            if surroundings::phase(e) != Some("night") {
                self.firefly_reacted = false;
            }
            if let Some(current) = e.world.weather.as_ref().map(catalog::enum_text) {
                if let Some(previous) = self.weather.as_ref().filter(|old| **old != current) {
                    self.weather_pending = Some((previous.clone(), current.clone()));
                }
                self.weather = Some(current);
            }
        }
        self.gain = None;
        if complete && e.event.name == EventName::StatusSnapshot {
            let current = light::count(e);
            if self.inventory.is_some_and(|n| n != current) {
                self.light_pending = None;
                self.light_ready = None;
            }
            if let Some(previous) = self.inventory.filter(|n| current > *n) {
                self.gain = Some((previous, current));
            }
            self.inventory = Some(current);
        }
    }
    pub fn note_player_input(&mut self, now: u64) {
        self.last_player_input = Some(now);
        self.light_pending = None;
        self.light_ready = None;
    }
    pub fn actions(
        &mut self,
        e: &GameEvent,
        now: u64,
        mode: Mode,
        busy: bool,
        focus: &AmbientFocus,
        s: &Settings,
    ) -> Vec<Speech> {
        let input_at = focus.last_player_input_at.or(self.last_player_input);
        if busy
            || focus.player_priority
            || !elapsed(now, input_at, s.ms("player_input_priority_cooldown_ms"))
            || matches!(mode, Mode::Panic | Mode::SuppressedPanic | Mode::Aftermath)
            || !e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
        {
            return vec![];
        }
        if focus.foreground {
            if focus.casual_foreground
                && elapsed(now, input_at, s.ms("conversation_ambient_mute_ms"))
                && input_at.is_some()
            {
                return self
                    .mob_action_if_allowed(e, now, mode, focus, s)
                    .into_iter()
                    .collect();
            }
            return vec![];
        }
        if self.light_request(e, now, focus, s) {
            return vec![];
        }
        if let Some(speech) = self.weather_action(e, focus, s) {
            return vec![speech];
        }
        if let Some(speech) = self.smell.action(e, now, s) {
            return vec![speech];
        }
        if let Some(speech) = self.eye_action(e, now, s) {
            return vec![speech];
        }
        if focus.boss_presence {
            self.places.clear_pending();
            return vec![];
        }
        if let Some(actions) = self.firefly_actions(e, focus) {
            return actions;
        }
        if let Some(speech) = self.places.action(e, now, focus, s) {
            return vec![speech];
        }
        if focus.ominous_presence {
            return vec![];
        }
        self.mob_action_if_allowed(e, now, mode, focus, s)
            .into_iter()
            .collect()
    }
    pub fn smell_query(&mut self, e: &GameEvent, text: &str, now: u64) -> Option<Speech> {
        if !smell::is_query(text) {
            return None;
        }
        self.smell.mark(e, now);
        Some(smell::speech(e))
    }
    pub fn smell_answer(&mut self, e: &GameEvent, now: u64) -> Speech {
        self.smell.mark(e, now);
        smell::speech(e)
    }
    pub fn take_light_plan(&mut self) -> Option<LightPlanRequest> {
        self.light_ready.take()
    }
    pub fn clear_weather_transition(&mut self) {
        self.weather_pending = None;
    }
}
fn leaf(kind: &'static str, text: String, details: Value, temperature: f64) -> Speech {
    let mut speech = Speech::new(kind, text);
    speech.scope = Scope::Safe;
    speech.leaf = Some(LeafRequest {
        kind: kind.to_owned(),
        details,
        temperature,
    });
    speech
}
fn common_details(e: &GameEvent, s: &Settings) -> Value {
    json!({"player_name":crate::combat::core::call_name(e,s),"biome":catalog::biome_label(e.world.biome.as_deref().unwrap_or("unknown")),"time_phase":e.world.time_phase})
}
/// Recheck immediately before and during rendering against the current environment notification.
/// The host separately verifies observation freshness, focus, and authoritative hostile lists.
pub fn still_applicable(speech: &Speech, e: &GameEvent) -> bool {
    // 天候leafのうち、実雷鳴への反応はDanger側の担当。
    // 暗所や雷の許可条件をambientの「敵なし」で上書きしない。
    if !matches!(
        speech.kind,
        "ambient"
            | "weather_transition"
            | "smell"
            | "light_source_gain"
            | "ender_eye_throw"
            | "firefly"
            | "firefly_cue"
            | "structure_entry"
            | "special_biome_entry"
    ) || speech
        .leaf
        .as_ref()
        .is_some_and(|r| r.details["thunder_reaction"] == true)
    {
        return true;
    }
    if !e.visual_threats.is_empty() || !e.auditory_threats.is_empty() {
        return false;
    }
    let guard = speech.leaf.as_ref().map(|r| &r.details["__ambient_guard"]);
    if let Some(g) = guard {
        if let Some(kind) = g["mob_type"].as_str() {
            return e.passive_mobs.iter().any(|m| {
                m.r#type == kind
                    && (g["crowd"] == true
                        || g["baby"].as_bool().unwrap_or(false) == m.is_baby.unwrap_or(false))
                    && g["profession"].as_str().is_none_or(|p| {
                        m.profession.as_deref().map(catalog::norm).as_deref() == Some(p)
                    })
                    && (catalog::norm(kind) != "villager" || mobs::schedule(e, m) != "sleep")
            });
        }
        if let Some(weather) = g["weather"].as_str() {
            return e
                .world
                .weather
                .as_ref()
                .is_some_and(|v| catalog::enum_text(v) == weather);
        }
        if let Some(structure) = g["structure"].as_str() {
            return e.world.structure.as_deref().map(catalog::norm).as_deref() == Some(structure);
        }
        if let Some(count) = g["light_source_count"].as_i64() {
            return light::count(e) == count;
        }
    }
    match speech.kind {
        "smell" => smell::speech(e).text == speech.text,
        "firefly" | "firefly_cue" => {
            surroundings::phase(e) == Some("night")
                && e.world.nearby_firefly_bush_count.unwrap_or(0) > 0
        }
        "special_biome_entry" => ["day", "night", "ominous"].iter().any(|phase| {
            catalog::biome_lines(e.world.biome.as_deref().unwrap_or(""), phase)
                .contains(&speech.text)
        }),
        _ => true,
    }
}
impl Ambient {
    /// Specific zombie scent retains its old safety priority without entering combat mode.
    /// Call after lightning/current player input, before low-priority environment branches.
    pub fn priority_smell(
        &mut self,
        e: &GameEvent,
        now: u64,
        mode: Mode,
        busy: bool,
        s: &Settings,
    ) -> Option<Speech> {
        use crate::events::{SmellObservationSmellId::Zombie, SmellObservationSpecificity::Source};
        if busy
            || matches!(mode, Mode::Panic | Mode::SuppressedPanic | Mode::Aftermath)
            || !e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
        {
            return None;
        }
        let zombie = e
            .smell_observation
            .as_ref()
            .is_some_and(|o| o.smell_id == Some(Zombie) && o.specificity == Some(Source))
            || e.smell_observation.is_none() && !e.zombie_scent_clues.is_empty();
        if zombie {
            self.smell.action(e, now, s)
        } else {
            None
        }
    }
}
