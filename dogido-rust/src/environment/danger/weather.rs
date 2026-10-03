use super::*;

impl Danger {
    pub(super) fn surface_evening(&self, e: &GameEvent) -> bool {
        phase(e) == Some(TimePhase::Evening)
            && e.world.weather != Some(Weather::Thunder)
            && overworld(e)
            && !suppressed_biome(e)
            && sky(e)
            && !submerged(e)
            && !cave(e)
            && !safe(e)
    }
    pub(super) fn cave_night(&self, e: &GameEvent) -> bool {
        overworld(e) && !suppressed_biome(e) && (submerged(e) || cave(e))
    }
    pub(super) fn schedule_night(&self, e: &GameEvent) -> bool {
        !self.boss_presence
            && !self.ominous_presence
            && match phase(e) {
                Some(TimePhase::Evening) => self.surface_evening(e) || self.cave_night(e),
                Some(TimePhase::Night) => self.cave_night(e),
                _ => false,
            }
    }
    pub(super) fn night_action(&mut self, e: &GameEvent, interrupt: bool) -> Vec<Speech> {
        if self.night_spoken
            || self.boss_presence
            || self.ominous_presence
            || !(self.night_pending || self.schedule_night(e))
        {
            return vec![];
        }
        let mut a = if self.surface_evening(e) {
            Speech::new(
                "night_warning_surface",
                text("darkness", &["night_warning", "surface_evening"]),
            )
        } else if self.cave_night(e)
            && matches!(phase(e), Some(TimePhase::Evening | TimePhase::Night))
        {
            Speech::new(
                "night_warning_cave",
                text("darkness", &["night_warning", "cave_or_submerged"]).replace(
                    "{phase_label}",
                    if phase(e) == Some(TimePhase::Evening) {
                        "夕方"
                    } else {
                        "夜"
                    },
                ),
            )
        } else {
            return vec![];
        };
        a.interrupt = interrupt;
        self.night_pending = false;
        self.night_spoken = true;
        vec![a]
    }
    pub(super) fn thunder(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Vec<Speech> {
        let lightning = fresh(
            e.world.nearby_lightning_strike_recent_ms,
            ms(s, "nearby_lightning_recent_ms"),
        ) && e
            .world
            .nearby_lightning_strike_distance
            .is_some_and(|d| d <= n(s, "hostile_query_distance"));
        if !lightning
            && !fresh(
                e.world.thunder_sound_recent_ms,
                ms(s, "weather_sound_recent_ms"),
            )
        {
            return vec![];
        }
        let message = ready(
            now,
            self.thunder_message,
            s,
            "thunder_reaction_message_cooldown_ms",
        );
        let cue = ready(
            now,
            self.thunder_cue,
            s,
            "thunder_reaction_panic_cue_cooldown_ms",
        );
        if !message && !cue {
            return vec![];
        }
        let immediate = lightning || self.thunder_message.is_none();
        let mut a = vec![];
        if cue {
            let mut q = Speech::new(
                "thunder_cue",
                if lightning {
                    "ひいっ！"
                } else {
                    "ヒイ！"
                },
            );
            q.cue_id = Some(if lightning {
                "spot_hostile_gasp"
            } else {
                "suppressed_gasp"
            });
            q.interrupt = immediate;
            a.push(q);
            self.thunder_cue = Some(now);
        }
        if message {
            let scene = if lightning {
                "nearby_lightning_strike"
            } else {
                "thunder_heard"
            };
            let mut details = context(e, s);
            details["scene"] = json!(scene);
            details["thunder_reaction"] = json!(true);
            details["scream_status"] = json!(if cue { "scheduled" } else { "not_scheduled" });
            details["nearby_lightning"] = json!(lightning);
            details["cold_biome"] = json!(cold(e));
            details["dry_biome"] = json!(dry(e));
            let mut line = leaf(
                "thunder_reaction",
                text("fallback", &["weather_transition", scene]),
                details,
                0.72,
            );
            line.interrupt = immediate && a.is_empty();
            a.push(line);
            self.thunder_message = Some(now);
        }
        a
    }
    pub(super) fn portal(&mut self, e: &GameEvent, _now: u64, s: &Settings) -> Option<Speech> {
        let portal = self.pending_portal.take()?;
        let encounter = self.pending_portal_encounter.take();
        if e.world.nearby_portal_type.as_deref() != Some(portal.as_str()) {
            return None;
        }
        use crate::events::WorldStateNearbyPortalEncounter;
        let fallback_key = match encounter {
            Some(WorldStateNearbyPortalEncounter::Appeared) => "appearance_fallbacks",
            Some(WorldStateNearbyPortalEncounter::Arrived) => "arrival_fallbacks",
            _ => "observed_fallbacks",
        };
        let fallback = text("exploration", &["portal", fallback_key, &portal]);
        if fallback.is_empty() {
            return None;
        }
        self.portal_seen.insert(portal.clone());
        let mut details = context(e, s);
        details["portal_type"] = json!(portal);
        details["portal_encounter"] = json!(encounter);
        details["portal_label"] = json!(match portal.as_str() {
            "nether_portal" => "ネザーポータル",
            "end_portal" => "エンドポータル",
            "end_gateway" => "エンドゲートウェイ",
            _ => &portal,
        });
        details["portal_distance"] = json!(e.world.nearby_portal_distance);
        details["dimension"] = json!(e.player.dimension.as_deref().unwrap_or(""));
        Some(leaf("portal_appearance", fallback, details, 0.55))
    }
}
fn fresh(v: Option<i64>, window: u64) -> bool {
    v.is_some_and(|v| v >= 0 && (v as u64) <= window)
}
fn suppressed_biome(e: &GameEvent) -> bool {
    matches!(biome(e), "dark_forest" | "mushroom_fields" | "pale_garden")
}
