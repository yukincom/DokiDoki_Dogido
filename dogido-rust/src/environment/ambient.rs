//! Non-urgent environment decisions. No I/O, model calls, playback, or world actions.
mod catalog;
mod light;
mod places;
mod smell;
mod surroundings;
#[cfg(test)]
mod tests;

use crate::{
    combat::model::{LeafRequest, Mode, Scope, Settings, Speech, elapsed},
    events::{EventName, GameEvent},
};
pub(crate) use catalog::{biome_label, mob_label};
pub use light::{LightContext, LightPlanRequest};
use serde_json::{Value, json};
pub(crate) use smell::is_query as is_smell_query;
pub use surroundings::player_vehicle_fact;
/// 配送時に明示質問の返答だけを現在観測から作り直す。状態を進めない。
pub fn current_smell_reply(event: &GameEvent) -> Speech {
    smell::speech(event)
}

pub fn current_smell_query_reply(event: &GameEvent, text: &str) -> Speech {
    smell::query_reply(event, text)
}

/// Resolved smell and bounded estimates, never exact positions, object IDs,
/// raw intensity/history or hidden nearby-source observations.
pub(crate) fn smell_context(event: &GameEvent) -> Option<Value> {
    let observation = smell::observation(event)?;
    if observation.status != crate::events::SmellObservationStatus::Present {
        return None;
    }
    let mut context = json!({"specificity":observation.specificity,"smell_id":observation.smell_id,
        "category":observation.category,"valence":observation.valence});
    if let Some(direction) = observation.direction_estimate {
        context["direction_estimate"] = json!(direction);
    }
    Some(context)
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
    pub notice_ready: bool,
}
#[derive(Clone, Default)]
pub struct Ambient {
    smell: smell::Presence,
    places: places::Places,
    last_danger: Option<u64>,
    last_player_input: Option<u64>,
    dimension: Option<String>,
    inventory: Option<i64>,
    gain: Option<(i64, i64)>,
    last_light_comment: Option<u64>,
    light_serial: u64,
    light_pending: Option<LightPlanRequest>,
    light_ready: Option<LightPlanRequest>,
    last_eye: Option<u64>,
}

impl Ambient {
    /// Code-owned structure state: changed only by status snapshots or dimension resets.
    pub fn current_structure(&self) -> Option<&str> {
        self.places.current_structure()
    }

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
        if complete || e.smell_observation.is_some() {
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
            self.places.update(e);
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
        if focus.foreground && !focus.casual_foreground {
            return vec![];
        }
        if self.light_request(e, now, focus, s) {
            return vec![];
        }
        if let Some(speech) = self.eye_action(e, now, s) {
            return vec![speech];
        }
        if focus.boss_presence || focus.ominous_presence {
            return vec![];
        }
        if focus.casual_foreground
            && (input_at.is_none() || !elapsed(now, input_at, s.ms("conversation_ambient_mute_ms")))
        {
            return vec![];
        }
        if focus.notice_ready {
            vec![super::notice::action()]
        } else {
            vec![]
        }
    }
    pub fn smell_query(&mut self, e: &GameEvent, text: &str, now: u64) -> Option<Speech> {
        if !smell::is_query(text) {
            return None;
        }
        self.smell.mark(e, now);
        Some(smell::query_reply(e, text))
    }
    pub(crate) fn reconsider_smell_spatial(&mut self, e: &GameEvent) {
        self.smell.reconsider_spatial(e);
    }
    pub fn smell_answer(&mut self, e: &GameEvent, now: u64) -> Speech {
        self.smell.mark(e, now);
        smell::speech(e)
    }
    pub fn take_light_plan(&mut self) -> Option<LightPlanRequest> {
        self.light_ready.take()
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
    if !super::reaction::still_applicable(speech, e) {
        return false;
    }
    // 天候leafのうち、実雷鳴への反応はDanger側の担当。
    // 暗所や雷の許可条件をambientの「敵なし」で上書きしない。
    if !matches!(
        speech.kind,
        "surroundings_notice" | "smell" | "light_source_gain" | "ender_eye_throw"
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
        if let Some(count) = g["light_source_count"].as_i64() {
            return light::count(e) == count;
        }
    }
    match speech.kind {
        "smell" if super::reaction::is_model_reaction(speech) => true,
        "smell" => smell::still_applicable(speech, e),
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
            .is_some_and(|o| o.smell_id == Some(Zombie) && o.specificity == Some(Source));
        if zombie {
            self.smell.action(e, now, s)
        } else {
            None
        }
    }
}
