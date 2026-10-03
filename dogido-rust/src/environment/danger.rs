//! Dangerous environment policy. `update` receives one observation before selection.
//! Partial notifications never replace full geometry, create an entry edge, or end a dark push.
mod geometry;
mod weather;
mod words;
use crate::{
    combat::model::{LeafRequest, Mode, Settings, Speech, elapsed},
    events::{EventName, GameEvent, TimePhase, Weather},
};
use geometry::*;
use serde::Serialize;
use serde_json::{Value, json};
use std::{collections::BTreeSet, sync::LazyLock};
use words::*;

static DEFAULTS: LazyLock<serde_json::Map<String, Value>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("danger_defaults.json")).expect("danger defaults")
});
pub fn defaults() -> &'static serde_json::Map<String, Value> {
    &DEFAULTS
}
fn n(s: &Settings, k: &str) -> f64 {
    s.0.get(k)
        .or_else(|| DEFAULTS.get(k))
        .and_then(Value::as_f64)
        .expect("environment setting")
}
fn ms(s: &Settings, k: &str) -> u64 {
    n(s, k) as u64
}
fn ready(now: u64, last: Option<u64>, s: &Settings, k: &str) -> bool {
    elapsed(now, last, ms(s, k))
}
fn control() -> Speech {
    let mut a = Speech::new("dark_push_stop", "");
    a.interrupt = true;
    a
}
fn snapshot(e: &GameEvent) -> bool {
    e.event.name == EventName::StatusSnapshot
}
pub fn safe_zone_with_door(e: &GameEvent) -> bool {
    safe(e)
}
pub fn heard_thunder(e: &GameEvent, s: &Settings) -> bool {
    e.world
        .thunder_sound_recent_ms
        .is_some_and(|v| v >= 0 && v as u64 <= ms(s, "weather_sound_recent_ms"))
        || e.world
            .nearby_lightning_strike_recent_ms
            .is_some_and(|v| v >= 0 && v as u64 <= ms(s, "nearby_lightning_recent_ms"))
            && e.world
                .nearby_lightning_strike_distance
                .is_some_and(|d| d <= n(s, "hostile_query_distance"))
}

#[derive(Clone, Debug, Serialize)]
pub struct LightContext {
    pub reasonably_lit: bool,
    pub severe_darkness: bool,
    pub nearby_light: bool,
    pub dark_push_before: bool,
    pub recovered: bool,
}

#[derive(Clone, Default)]
pub struct Danger {
    dimension: Option<String>,
    complete: bool,
    dimension_changed: bool,
    previous_zone: bool,
    previous_water: bool,
    previous_shelter: bool,
    previous_foliage: bool,
    entered_zone: bool,
    entered_water: bool,
    entered_shelter: bool,
    entered_foliage: bool,
    stage: u8,
    active: bool,
    entry_x: Option<f64>,
    entry_z: Option<f64>,
    reference_light: Option<f64>,
    reference_darkness: Option<f64>,
    breath_ready: Option<u64>,
    last_breath: Option<u64>,
    pending_relief: Option<u64>,
    last_dark_advice: Option<u64>,
    last_water: Option<u64>,
    last_foliage: Option<u64>,
    shelter_advised: bool,
    shelter_seen: bool,
    shelter_entry_spoken: bool,
    shelter_morning_spoken: bool,
    shelter_reset_ready: bool,
    night_pending: bool,
    night_spoken: bool,
    thunder_message: Option<u64>,
    thunder_cue: Option<u64>,
    last_magma: Option<u64>,
    last_damaging_light: Option<u64>,
    last_portal_frame: Option<u64>,
    portal_initialized: bool,
    portal_seen: BTreeSet<String>,
    pending_portal: Option<String>,
    pending_portal_encounter: Option<crate::events::WorldStateNearbyPortalEncounter>,
    boss_presence: bool,
    ominous_presence: bool,
    last_mode: Mode,
}
impl Danger {
    /// Commit mode bookkeeping even when another policy candidate wins this frame.
    pub fn finish_frame(&mut self, mode: Mode) {
        self.last_mode = mode;
    }
    pub fn still_applicable(&self, a: &Speech, e: &GameEvent, s: &Settings) -> bool {
        still_applicable(a, e, s)
            && match a.kind {
                "dark_push_no_light" | "dark_push_breath" => self.active,
                "dark_push_after_breath" => {
                    !self.dark_push_active() && !self.entered_zone && !self.entered_water
                }
                _ => true,
            }
    }
    pub fn dark_push_active(&self) -> bool {
        self.active || self.stage >= 1
    }
    pub fn set_presence(&mut self, boss: bool, ominous: bool) {
        self.boss_presence = boss;
        self.ominous_presence = ominous;
    }
    pub fn light_context(&self, e: &GameEvent, s: &Settings) -> LightContext {
        let reasonably_lit = buffered(e, s)
            || lit(e, s)
            || e.world.danger_darkness_score.unwrap_or(0.0) < n(s, "darkness_alert_threshold")
            || e.world
                .local_light
                .is_some_and(|v| v as f64 > n(s, "darkness_advice_light_threshold"));
        LightContext {
            reasonably_lit,
            severe_darkness: !reasonably_lit
                && e.world.danger_darkness_score.unwrap_or(0.0) >= n(s, "darkness_alert_threshold")
                && e.world
                    .local_light
                    .is_none_or(|v| v as f64 <= n(s, "darkness_advice_light_threshold")),
            nearby_light: lamp(e, s),
            dark_push_before: self.dark_push_active(),
            recovered: self.dark_push_active() && self.recovered(e, s),
        }
    }
    pub fn update(&mut self, e: &GameEvent, _now: u64, complete: bool, s: &Settings) {
        self.complete = complete;
        self.dimension_changed = false;
        if !complete {
            return;
        }
        let dimension = e.player.dimension.clone();
        self.dimension_changed =
            self.dimension.is_some() && dimension.is_some() && dimension != self.dimension;
        if self.dimension_changed {
            self.reset_dark();
            self.reference_light = None;
            self.reference_darkness = None;
            self.last_breath = None;
            self.previous_zone = false;
            self.previous_water = false;
            self.previous_shelter = false;
            self.previous_foliage = false;
            self.entered_zone = false;
            self.entered_water = false;
            self.entered_shelter = false;
            self.entered_foliage = false;
            self.shelter_entry_spoken = false;
            self.night_pending = false;
            self.portal_initialized = false;
            self.portal_seen.clear();
            self.pending_portal = None;
            self.pending_portal_encounter = None;
        }
        self.dimension = dimension;
        let (z, w, h, f) = (zone(e, s), water_dark(e, s), shelter(e, s), foliage(e));
        self.entered_zone &= z;
        self.entered_water &= w;
        self.entered_shelter &= h;
        self.entered_foliage &= f;
        if snapshot(e) {
            if !self.dimension_changed {
                self.entered_zone = z && (self.entered_zone || !self.previous_zone);
                self.entered_water = w && (self.entered_water || !self.previous_water);
                self.entered_shelter = h && (self.entered_shelter || !self.previous_shelter);
                self.entered_foliage = f && (self.entered_foliage || !self.previous_foliage);
            }
            self.previous_zone = z;
            self.previous_water = w;
            self.previous_shelter = h;
            self.previous_foliage = f;
            let portal = e
                .world
                .nearby_portal_type
                .as_deref()
                .map(str::trim)
                .filter(|p| !p.is_empty())
                .map(str::to_lowercase);
            if !self.portal_initialized {
                self.portal_initialized = true;
                if let Some(p) = portal.clone() {
                    self.portal_seen.insert(p);
                }
            } else if let Some(p) = portal.clone()
                && !self.portal_seen.contains(&p)
            {
                // The adapter retains one encounter; a changed full snapshot may be another portal.
                self.pending_portal_encounter = e.world.nearby_portal_encounter;
                self.pending_portal = Some(p);
            }
            // A pending present-tense announcement must remain observable when delivered.
            if self.pending_portal.as_ref() != portal.as_ref() {
                self.pending_portal = None;
                self.pending_portal_encounter = None;
            }
        }
        if matches!(phase(e), Some(TimePhase::Morning | TimePhase::Day)) {
            self.night_pending = false;
            self.night_spoken = false;
        } else if !self.night_spoken && self.schedule_night(e) {
            self.night_pending = true;
        }
        if self.boss_presence || self.ominous_presence {
            self.night_pending = false;
        }
        if morning(e, s) {
            self.shelter_reset_ready = true;
        }
        if matches!(phase(e), Some(TimePhase::Evening | TimePhase::Night)) && h {
            self.shelter_seen = true;
        }
        if phase(e) == Some(TimePhase::Night) && self.shelter_reset_ready {
            self.shelter_advised = false;
            self.shelter_seen = false;
            self.shelter_entry_spoken = false;
            self.shelter_morning_spoken = false;
            self.shelter_reset_ready = false;
        }
    }
    /// Must run after combat urgent selection; thunder is based on the supplied notification.
    pub fn urgent(
        &mut self,
        e: &GameEvent,
        now: u64,
        mode: Mode,
        focus: bool,
        s: &Settings,
    ) -> Vec<Speech> {
        if self.dimension_changed {
            return vec![];
        }
        let stop = self.combat_recovery(e, now, s);
        if !stop.is_empty() {
            return stop;
        }
        if matches!(mode, Mode::Panic | Mode::SuppressedPanic | Mode::Aftermath) {
            return vec![];
        }
        let thunder = self.thunder(e, now, s);
        if !thunder.is_empty() {
            return thunder;
        }
        // Nearby heat can hurt before a casual reply finishes. Use the current
        // geometric observation, not damage, and keep combat/thunder priority.
        if self.complete
            && let Some(warning) = self.damaging_light(e, now, s)
        {
            return vec![warning];
        }
        if self.complete && !focus && self.surface_evening(e) {
            return self.night_action(e, true);
        }
        vec![]
    }
    /// Called only if higher-priority combat/urgent actions were not selected.
    pub fn actions(
        &mut self,
        e: &GameEvent,
        now: u64,
        mode: Mode,
        busy: bool,
        focus: bool,
        s: &Settings,
    ) -> Vec<Speech> {
        let previous_mode = self.last_mode;
        self.last_mode = mode;
        if !self.complete || self.dimension_changed {
            return vec![];
        }
        if blocked(e) {
            return if self.should_stop(e, s) {
                self.stop(e, now, true, s)
            } else {
                vec![]
            };
        }
        if matches!(mode, Mode::Panic | Mode::SuppressedPanic | Mode::Aftermath) {
            return vec![];
        }
        // Stop obsolete breathing even while another turn is generating/playing.
        if busy {
            return if self.should_stop(e, s) {
                self.stop(e, now, true, s)
            } else {
                vec![]
            };
        }
        let stop = self.should_stop(e, s);
        if !focus {
            if snapshot(e)
                && (self.shelter_advised || self.shelter_seen)
                && !self.shelter_morning_spoken
                && shelter(e, s)
                && morning(e, s)
            {
                self.shelter_morning_spoken = true;
                let mut a = vec![];
                if stop {
                    self.reset_dark();
                    a.push(control())
                }
                a.push(Speech::new(
                    "emergency_shelter_morning",
                    text("darkness", &["emergency_shelter", "morning_release"]),
                ));
                return a;
            }
            if self.entered_shelter && night(e, s) && !self.shelter_entry_spoken {
                self.entered_shelter = false;
                self.shelter_entry_spoken = true;
                self.reset_dark();
                let mut a = vec![];
                if stop {
                    a.push(control())
                }
                a.push(dark_leaf("emergency_shelter_relief", e, s));
                return a;
            }
            if self.entered_water {
                self.entered_water = false;
                self.reset_dark();
                if ready(
                    now,
                    self.last_water,
                    s,
                    "submerged_darkness_comment_cooldown_ms",
                ) {
                    self.last_water = Some(now);
                    let mut a = vec![];
                    if stop {
                        a.push(control())
                    }
                    a.push(Speech::new(
                        "submerged_darkness",
                        text("darkness", &["darkness", "submerged_entry"]),
                    ));
                    return a;
                }
                // Cooldown suppresses the underwater sentence, never the stop of
                // previously running breathing. Python reset the state here but
                // could omit its control action when that sentence was on cooldown.
                if stop {
                    return vec![control()];
                }
            }
        }
        if self.entered_zone && zone(e, s) {
            self.entered_zone = false;
            self.reset_dark();
            self.set_reference(e, s);
            if !torch(e) && self.severe(e, s) {
                return vec![self.escalate(e, now, s)];
            }
            self.stage = 1;
            return vec![dark_leaf(
                if torch(e) {
                    "occluded_entry_with_light"
                } else {
                    "occluded_entry_no_light"
                },
                e,
                s,
            )];
        }
        if self.should_warn(e, s) {
            return vec![self.escalate(e, now, s)];
        }
        if stop {
            return self.stop(e, now, false, s);
        }
        if let Some(until) = self.pending_relief {
            if now >= until || self.entered_zone || self.entered_water || self.suppress_relief(e, s)
            {
                self.pending_relief = None;
            } else if e.visual_threats.is_empty() && e.auditory_threats.is_empty() {
                self.pending_relief = None;
                let mut a = dark_leaf("dark_push_after_breath", e, s);
                a.protect_ms = 2000;
                return vec![a];
            }
        }
        if self.active
            && zone(e, s)
            && snapshot(e)
            && self.breath_ready.is_none_or(|at| now >= at)
            && ready(now, self.last_breath, s, "dark_push_breath_loop_ms")
        {
            self.last_breath = Some(now);
            let mut a = Speech::new("dark_push_breath", "ハァハァ……");
            a.cue_id = Some("suppressed_breath");
            return vec![a];
        }
        if focus {
            return vec![];
        }
        let night = self.night_action(e, false);
        if !night.is_empty() {
            return night;
        }
        if let Some(a) = self.portal(e, now, s) {
            return vec![a];
        }
        if self.boss_presence {
            return vec![];
        }
        if e.world.standing_on_magma_block == Some(true)
            && ready(now, self.last_magma, s, "magma_block_comment_cooldown_ms")
        {
            self.last_magma = Some(now);
            return vec![Speech::new(
                "magma_block",
                "………しゃがめば大丈夫なんが不思議やな……",
            )];
        }
        if let Some(warning) = self.damaging_light(e, now, s) {
            return vec![warning];
        }
        if self.shelter_advice(e, s) {
            self.shelter_advised = true;
            self.shelter_morning_spoken = false;
            let key = if bed(e) {
                "advice_with_bed"
            } else if e.world.nearby_bed_count.unwrap_or(0) > 0 {
                "advice_nearby_bed"
            } else {
                "advice"
            };
            return vec![Speech::new(
                "emergency_shelter_advice",
                text("darkness", &["emergency_shelter", key]),
            )];
        }
        if self.entered_foliage
            && e.world.structure.is_none()
            && foliage(e)
            && !safe(e)
            && ready(
                now,
                self.last_foliage,
                s,
                "foliage_darkness_comment_cooldown_ms",
            )
        {
            self.entered_foliage = false;
            self.last_foliage = Some(now);
            return vec![Speech::new(
                "foliage_shade",
                "木がしげっているとこは暗いわー。こういうとこはおひさんでとっても敵が残っとるんやで……。",
            )];
        }
        if self.ominous_presence {
            return vec![];
        }
        if e.world
            .nearby_end_portal_frame_distance
            .is_some_and(|d| d <= n(s, "end_portal_frame_comment_distance"))
            && ready(
                now,
                self.last_portal_frame,
                s,
                "portal_frame_comment_cooldown_ms",
            )
        {
            self.last_portal_frame = Some(now);
            return vec![Speech::new(
                "end_portal_frame",
                text("exploration", &["portal", "frame_nearby"]),
            )];
        }
        if previous_mode != Mode::Alert
            && let Some(a) = self.advice(e, now, s)
        {
            return vec![a];
        }
        vec![]
    }
    fn damaging_light(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        if e.world.standing_on_magma_block == Some(true)
            || e.world.nearby_damaging_light_source_count.unwrap_or(0) <= 0
            || !e
                .world
                .nearest_damaging_light_source_distance
                .is_some_and(|d| d <= n(s, "damaging_light_warning_max_distance"))
            || !ready(
                now,
                self.last_damaging_light,
                s,
                "damaging_light_warning_cooldown_ms",
            )
        {
            return None;
        }
        self.last_damaging_light = Some(now);
        Some(Speech::new("damaging_light", "触るとあちちやで！"))
    }
    /// While combat owns speech, retain the relief transition from leaving the dark area.
    pub fn combat_recovery(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Vec<Speech> {
        if self.complete && !self.dimension_changed && blocked(e) && self.should_stop(e, s) {
            self.stop(e, now, true, s)
        } else {
            vec![]
        }
    }
    /// The combat engine calls this after selecting its forward-ambush action.
    pub fn interrupt_for_threat(&mut self, e: &GameEvent, s: &Settings) -> Vec<Speech> {
        if !self.dark_push_active() {
            return vec![];
        }
        self.reset_dark();
        self.last_breath = None;
        if zone(e, s) {
            self.stage = 1;
            self.set_reference(e, s)
        }
        vec![control()]
    }
    fn reset_dark(&mut self) {
        self.stage = 0;
        self.active = false;
        self.breath_ready = None;
        self.entry_x = None;
        self.entry_z = None;
        self.pending_relief = None;
    }
    fn set_reference(&mut self, e: &GameEvent, s: &Settings) {
        let severe = self.severe(e, s);
        self.reference_light = e.world.local_light.map(|v| {
            v as f64
                + if severe {
                    n(s, "dark_push_worse_light_delta")
                } else {
                    0.0
                }
        });
        self.reference_darkness = e.world.danger_darkness_score.map(|v| {
            (v - if severe {
                n(s, "dark_push_worse_darkness_delta")
            } else {
                0.0
            })
            .max(0.0)
        });
        self.entry_x = e.player.position.x;
        self.entry_z = e.player.position.z;
    }
    fn severe(&self, e: &GameEvent, s: &Settings) -> bool {
        e.world
            .local_light
            .map(|v| v as f64 <= n(s, "dark_push_escalation_light_threshold"))
            .unwrap_or(e.world.danger_darkness_score.is_some_and(|v| {
                v >= n(s, "occluded_entry_darkness_threshold")
                    .max(n(s, "dark_push_escalation_darkness_threshold"))
            }))
    }
    fn should_warn(&self, e: &GameEvent, s: &Settings) -> bool {
        if !zone(e, s) || self.active || self.stage < 1 || !snapshot(e) || self.entered_zone {
            return false;
        }
        let moved = match (
            self.entry_x,
            self.entry_z,
            e.player.position.x,
            e.player.position.z,
        ) {
            (Some(x), Some(z), Some(xx), Some(zz)) => {
                (xx - x).powi(2) + (zz - z).powi(2) >= n(s, "dark_push_progress_distance").powi(2)
            }
            _ => false,
        };
        let worse = e
            .world
            .local_light
            .zip(self.reference_light)
            .is_some_and(|(v, r)| v as f64 <= r - n(s, "dark_push_worse_light_delta"))
            || e.world
                .danger_darkness_score
                .zip(self.reference_darkness)
                .is_some_and(|(v, r)| v >= r + n(s, "dark_push_worse_darkness_delta"));
        let scary = e
            .world
            .local_light
            .map(|v| v as f64 <= n(s, "dark_push_escalation_light_threshold"))
            .unwrap_or(
                e.world
                    .danger_darkness_score
                    .is_some_and(|v| v >= n(s, "dark_push_escalation_darkness_threshold")),
            );
        (moved || (self.entry_x.is_none() || self.entry_z.is_none()) && worse) && scary
    }
    fn recovered(&self, e: &GameEvent, s: &Settings) -> bool {
        e.world
            .local_light
            .zip(self.reference_light)
            .is_some_and(|(v, r)| v as f64 > r || r > 0.0 && v as f64 >= r)
            || e.world
                .danger_darkness_score
                .zip(self.reference_darkness)
                .is_some_and(|(v, r)| v + n(s, "dark_push_recover_darkness_margin") < r)
    }
    fn should_stop(&self, e: &GameEvent, s: &Settings) -> bool {
        self.active && (!zone(e, s) || self.recovered(e, s))
            || !self.active && self.stage >= 1 && snapshot(e) && !self.entered_zone && !zone(e, s)
    }
    fn escalate(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Speech {
        self.pending_relief = None;
        self.last_breath = None;
        self.breath_ready = Some(now.saturating_add(ms(s, "dark_push_breath_delay_ms")));
        self.stage = 2;
        self.active = true;
        dark_leaf("dark_push_no_light", e, s)
    }
    fn suppress_relief(&self, e: &GameEvent, s: &Settings) -> bool {
        self.boss_presence
            || safe(e)
            || shelter(e, s)
            || cramped(e, s)
            || matches!(
                e.world.time_phase,
                Some(TimePhase::Morning | TimePhase::Day)
            ) && sky(e)
                && e.world.local_light.unwrap_or(0) >= 10
    }
    fn stop(&mut self, e: &GameEvent, now: u64, defer: bool, s: &Settings) -> Vec<Speech> {
        self.reset_dark();
        let mut a = vec![control()];
        if defer {
            self.pending_relief =
                Some(now.saturating_add(ms(s, "dark_push_after_breath_defer_ms")));
        } else if !self.suppress_relief(e, s) {
            let mut line = dark_leaf("dark_push_after_breath", e, s);
            line.protect_ms = 2000;
            a.push(line)
        }
        a
    }
    fn shelter_advice(&self, e: &GameEvent, s: &Settings) -> bool {
        snapshot(e)
            && overworld(e)
            && !cave(e)
            && !self.shelter_advised
            && !submerged(e)
            && !safe(e)
            && !shelter(e, s)
            && !buffered(e, s)
            && !lit(e, s)
            && !canopy(e)
            && !foliage(e)
            && e.world
                .local_light
                .is_none_or(|v| v as f64 <= n(s, "darkness_advice_light_threshold"))
            && e.world.time_of_day.is_some_and(|v| {
                v as f64
                    >= if matches!(e.world.weather, Some(Weather::Rain | Weather::Thunder)) {
                        n(s, "emergency_shelter_night_start").min(12969.0)
                    } else {
                        n(s, "emergency_shelter_night_start")
                    }
            })
            && biome(e) != "mushroom_fields"
            && (e.world.respawn_point_set != Some(true)
                || e.world
                    .respawn_distance
                    .is_none_or(|v| v >= n(s, "emergency_shelter_respawn_distance")))
    }
    fn advice(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        if submerged(e) {
            if e.world.submerged_depth_blocks.unwrap_or(0) as f64
                >= n(s, "submerged_darkness_depth_threshold")
                && ready(
                    now,
                    self.last_water,
                    s,
                    "submerged_darkness_comment_cooldown_ms",
                )
            {
                self.last_water = Some(now);
                return Some(Speech::new(
                    "submerged_darkness",
                    text("darkness", &["darkness", "submerged_entry"]),
                ));
            }
            return None;
        }
        if !ready(now, self.last_dark_advice, s, "darkness_advice_cooldown_ms")
            || shelter(e, s)
            || cramped(e, s)
            || buffered(e, s)
            || lit(e, s)
            || safe(e)
            || foliage(e)
            || e.world.danger_darkness_score.unwrap_or(0.0) < n(s, "darkness_alert_threshold")
            || e.world
                .local_light
                .is_some_and(|v| v as f64 > n(s, "darkness_advice_light_threshold"))
        {
            return None;
        }
        let line = if torch(e) {
            "なあ、ここ急に暗なってきたやん。松明つけとこ。"
        } else if craftable(e) {
            "石炭あるやん、今のうちに松明作っとこや。"
        } else if materials(e) {
            "このへんで木とか石炭拾って、先に松明作っとこ。"
        } else if !weapon(e) {
            if !ready(
                now,
                self.last_dark_advice,
                s,
                "darkness_llm_comment_cooldown_ms",
            ) {
                return None;
            }
            self.last_dark_advice = Some(now);
            return Some(dark_leaf("darkness_escape", e, s));
        } else if matches!(phase(e), Some(TimePhase::Evening | TimePhase::Night)) {
            "これはもうあかん、こんなんいえに帰ったほうがええって。"
        } else {
            "なんかこの先、普通に危ない空気してるで。"
        };
        self.last_dark_advice = Some(now);
        Some(Speech::new("darkness_advice", line))
    }
}

/// Revalidate present-tense facts before audio delivery. Thunder is a past observed event.
pub fn still_applicable(a: &Speech, e: &GameEvent, s: &Settings) -> bool {
    match a.kind {
        "occluded_entry_with_light" => zone(e, s) && torch(e),
        "occluded_entry_no_light" => zone(e, s) && !torch(e),
        "dark_push_no_light" | "dark_push_breath" => zone(e, s),
        "submerged_darkness" => water_dark(e, s),
        "emergency_shelter_relief" => shelter(e, s) && night(e, s),
        "emergency_shelter_morning" => shelter(e, s) && morning(e, s),
        "emergency_shelter_advice" => {
            Danger::default().shelter_advice(e, s)
                && a.text
                    == text(
                        "darkness",
                        &[
                            "emergency_shelter",
                            if bed(e) {
                                "advice_with_bed"
                            } else if e.world.nearby_bed_count.unwrap_or(0) > 0 {
                                "advice_nearby_bed"
                            } else {
                                "advice"
                            },
                        ],
                    )
        }
        "dark_push_after_breath" => {
            e.visual_threats.is_empty()
                && e.auditory_threats.is_empty()
                && a.leaf.as_ref().is_none_or(|l| {
                    l.details["time_phase"]
                        == phase(e)
                            .map(|p| serde_json::to_value(p).expect("phase"))
                            .unwrap_or(json!("unknown"))
                })
        }
        "foliage_shade" => foliage(e) && !safe(e),
        "magma_block" => e.world.standing_on_magma_block == Some(true),
        "damaging_light" => {
            e.world.nearby_damaging_light_source_count.unwrap_or(0) > 0
                && e.world
                    .nearest_damaging_light_source_distance
                    .is_some_and(|d| d <= n(s, "damaging_light_warning_max_distance"))
                && e.world.standing_on_magma_block != Some(true)
        }
        "end_portal_frame" => e
            .world
            .nearby_end_portal_frame_distance
            .is_some_and(|d| d <= n(s, "end_portal_frame_comment_distance")),
        "portal_appearance" => {
            a.leaf
                .as_ref()
                .and_then(|l| l.details["portal_type"].as_str())
                == e.world.nearby_portal_type.as_deref()
        }
        "night_warning_surface" => Danger::default().surface_evening(e),
        "night_warning_cave" => {
            Danger::default().cave_night(e)
                && matches!(phase(e), Some(TimePhase::Evening | TimePhase::Night))
                && a.text
                    == text("darkness", &["night_warning", "cave_or_submerged"]).replace(
                        "{phase_label}",
                        if phase(e) == Some(TimePhase::Evening) {
                            "夕方"
                        } else {
                            "夜"
                        },
                    )
        }
        "darkness_advice" | "darkness_escape" => {
            !submerged(e)
                && !safe(e)
                && !shelter(e, s)
                && !buffered(e, s)
                && !lit(e, s)
                && e.world.danger_darkness_score.unwrap_or(0.0) >= n(s, "darkness_alert_threshold")
                && e.world
                    .local_light
                    .is_none_or(|v| v as f64 <= n(s, "darkness_advice_light_threshold"))
        }
        _ => true,
    }
}
