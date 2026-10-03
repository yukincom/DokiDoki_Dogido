//! The same bounded world facts accompany player replies and spontaneous speech.
//! Facts come from GameEvent; dialogue history and generated text never enter here.
use crate::{chat_catalog, chat_world, environment, events::GameEvent, world_catalog};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub observations: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub catalog_knowledge: Option<crate::catalog_knowledge::Knowledge>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub villager_routines: Option<crate::villager_routines::Context>,
    #[serde(default)]
    pub changes: Vec<Change>,
    #[serde(default)]
    pub revision: u64,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Change {
    pub kind: String,
    pub before: Value,
    pub now: Value,
    pub observed_at: String,
    pub revision: u64,
}

impl Context {
    pub fn for_reaction(mut self, kind: &str, details: &Value) -> Self {
        let mut knowledge = self.catalog_knowledge.take().unwrap_or_default();
        knowledge.extend(crate::catalog_knowledge::for_reaction(kind, details));
        self.catalog_knowledge = (!knowledge.is_empty()).then_some(knowledge);
        self
    }
}

pub fn project(
    event: &GameEvent,
    structure: Option<&str>,
    home_distance: f64,
    darkness_light: f64,
    thunder_age_ms: u64,
) -> anyhow::Result<Context> {
    let catalog = world_catalog::catalog();
    let projection = environment::projection::project_environment(event);
    let place = chat_world::place_context(
        catalog,
        chat_catalog::catalog(),
        event,
        structure,
        home_distance,
    )?;
    let climate = catalog.climate(event.world.biome.as_deref())?;
    let precipitation = environment::precipitation::from_event(event, &climate)?;
    let weather = if projection.include_sky_context {
        // Climate resolves rain/snow. A world thunder setting is not a heard thunderclap.
        json!({"label":chat_world::weather_label(event,&climate)?,
            "precipitation_kind":precipitation.precipitation_kind,
            "precipitation_possible":precipitation.precipitation_possible,
            "surface_snow_observed":precipitation.surface_snow_observed})
    } else {
        Value::Null
    };
    let smell = if let Some(mut present) = environment::ambient::smell_context(event) {
        present["status"] = "present".into();
        present["description"] = environment::ambient::current_smell_reply(event).text.into();
        present
    } else if let Some(observation) = &event.smell_observation {
        json!({"status":observation.status,"suppression_reason":observation.suppression_reason})
    } else {
        json!({"status":"unknown"})
    };
    let knowledge = crate::catalog_knowledge::for_event(event, structure);
    Ok(Context {
        catalog_knowledge: (!knowledge.is_empty()).then_some(knowledge),
        villager_routines: crate::villager_routines::project(event),
        observations: json!({
            "observed_at":event.observed_at,
            "dimension":event.player.dimension,
            "player_environment":event.player.environment,
            "place":place.place_line,
            "location_biome":place.biome_label,
            "biome_scene_visible":projection.include_biome_context,
            "sky_visible":event.world.sky_visible,
            "time_phase":if projection.include_sky_context {json!(event.world.time_phase)} else {Value::Null},
            "weather":weather,
            "thunder_heard":event.world.thunder_sound_recent_ms.is_some_and(|age|age>=0 && age as u64<=thunder_age_ms),
            "local_light":event.world.local_light,
            "darkness_observed":event.world.local_light.map(|v| v as f64<=darkness_light),
            "smell":smell,
            "mob_states":crate::mob_environment::project(event)
        }),
        ..Context::default()
    })
}

/// Session-owned changes since the last completed decision, not a second memory store.
#[derive(Default)]
pub struct State {
    current: Option<Context>,
    considered: u64,
}
impl State {
    pub fn observe(&mut self, mut next: Context) {
        if let Some(previous) = &self.current {
            if previous.observations["dimension"] != next.observations["dimension"] {
                next.revision = previous.revision.saturating_add(1);
                self.considered = previous.revision;
                self.current = Some(next);
                return;
            }
            next.revision = previous.revision;
            next.changes = previous
                .changes
                .iter()
                .filter(|c| c.revision > self.considered)
                .cloned()
                .collect();
            // A timetable exists only while villagers are currently observed.
            // Remove unconsumed old changes as soon as they leave the current frame.
            if next.villager_routines.is_none() {
                next.changes.retain(|c| c.kind != "villager_routines");
            } else if previous.villager_routines.is_some()
                && previous.villager_routines != next.villager_routines
            {
                next.revision = next.revision.saturating_add(1);
                next.changes.retain(|c| c.kind != "villager_routines");
                next.changes.push(Change {
                    kind: "villager_routines".into(),
                    before: json!(previous.villager_routines),
                    now: json!(next.villager_routines),
                    observed_at: next.observations["observed_at"]
                        .as_str()
                        .unwrap_or("")
                        .into(),
                    revision: next.revision,
                });
            }
            next.changes.retain(|change| {
                change.kind != "mob_environment"
                    || crate::mob_environment::still_observed(
                        &change.now,
                        &next.observations["mob_states"],
                    )
            });
            for (before, now) in crate::mob_environment::changes(
                &previous.observations["mob_states"],
                &next.observations["mob_states"],
            ) {
                next.revision = next.revision.saturating_add(1);
                next.changes.retain(|change| {
                    change.kind != "mob_environment" || change.now["subject"] != now["subject"]
                });
                next.changes.push(Change {
                    kind: "mob_environment".into(),
                    before,
                    now,
                    observed_at: next.observations["observed_at"]
                        .as_str()
                        .unwrap_or("")
                        .into(),
                    revision: next.revision,
                });
            }
            for key in ["weather", "smell", "place", "darkness_observed"] {
                let before = &previous.observations[key];
                let after = &next.observations[key];
                // Smell direction may move while the identity stays the same. The
                // current direction is available without creating endless new turns.
                let signature = |value: &Value| {
                    let mut value = value.clone();
                    if key == "smell"
                        && let Some(map) = value.as_object_mut()
                    {
                        map.remove("direction_estimate");
                        map.remove("description");
                    }
                    value
                };
                if signature(before) != signature(after) {
                    next.revision = next.revision.saturating_add(1);
                    next.changes.retain(|c| c.kind != key);
                    next.changes.push(Change {
                        kind: key.into(),
                        before: before.clone(),
                        now: after.clone(),
                        observed_at: next.observations["observed_at"]
                            .as_str()
                            .unwrap_or("")
                            .into(),
                        revision: next.revision,
                    });
                }
            }
        }
        self.current = Some(next);
    }
    pub fn context(&self) -> Option<Context> {
        self.current.clone().map(|mut c| {
            c.changes.retain(|c| c.revision > self.considered);
            c
        })
    }
    pub fn considered(&mut self, revision: u64) {
        // Only a version actually offered to this decision can be acknowledged.
        if let Some(current) = &self.current {
            self.considered = self.considered.max(revision.min(current.revision));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn frame(weather: &str, smell: Value) -> Context {
        Context {
            observations: json!({"dimension":"overworld","observed_at":"now","weather":weather,
            "smell":smell,"place":"草原","darkness_observed":false}),
            ..Context::default()
        }
    }
    #[test]
    fn villager_time_changes_are_current_bounded_and_removed_outside_observation() {
        let context = |time: i64, visible: bool| {
            let event = GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-10-03T00:00:00Z",
                "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
                "player":{"dimension":"minecraft:overworld"},"world":{"time_of_day":time,"sky_visible":false},
                "passive_mobs":if visible {json!([{"type":"villager","is_baby":true}])} else {json!([])}})).unwrap();
            project(&event, None, 32.0, 7.0, 5000).unwrap()
        };
        let mut state = State::default();
        state.observe(context(10000, true));
        state.observe(context(11999, true));
        assert!(state.context().unwrap().changes.is_empty());
        state.observe(context(12000, true));
        let offered = state.context().unwrap();
        assert_eq!(offered.changes.len(), 1);
        assert_eq!(offered.changes[0].kind, "villager_routines");
        assert_eq!(
            offered.changes[0].before["villagers"][0]["activity"],
            "play"
        );
        assert_eq!(offered.changes[0].now["villagers"][0]["activity"], "sleep");
        state.observe(context(13000, true));
        assert_eq!(state.context().unwrap().revision, offered.revision);
        state.observe(context(13000, false));
        let absent = state.context().unwrap();
        assert!(absent.villager_routines.is_none());
        assert!(absent.changes.is_empty());
        assert!(
            !serde_json::to_string(&absent)
                .unwrap()
                .contains("villager_routines")
        );
        state.considered(offered.revision);
        state.observe(context(4000, true));
        assert_eq!(
            state
                .context()
                .unwrap()
                .villager_routines
                .unwrap()
                .villagers[0]
                .activity,
            "play"
        );
        assert!(state.context().unwrap().changes.is_empty());
    }

    #[test]
    fn conversation_changes_survive_busy_turns_and_acknowledge_only_the_offered_version() {
        let mut state = State::default();
        state.observe(frame("clear", json!({"status":"none"})));
        state.observe(frame("rain", json!({"status":"none"})));
        let offered = state.context().unwrap();
        assert_eq!(offered.changes[0].kind, "weather");
        state.observe(frame(
            "rain",
            json!({"status":"present","smell_id":"zombie"}),
        ));
        state.considered(offered.revision);
        let next = state.context().unwrap();
        assert_eq!(next.changes.len(), 1);
        assert_eq!(next.changes[0].kind, "smell");
        state.considered(next.revision);
        assert!(state.context().unwrap().changes.is_empty());
        assert_eq!(
            state.context().unwrap().observations["smell"]["status"],
            "present"
        );
    }
    #[test]
    fn direction_updates_do_not_create_another_smell_arrival() {
        let mut state = State::default();
        state.observe(frame("clear",json!({"status":"present","smell_id":"zombie","direction_estimate":{"cardinal":"east"},"description":"東や"})));
        state.observe(frame("clear",json!({"status":"present","smell_id":"zombie","direction_estimate":{"cardinal":"west"},"description":"西や"})));
        assert!(state.context().unwrap().changes.is_empty());
        assert_eq!(
            state.context().unwrap().observations["smell"]["direction_estimate"]["cardinal"],
            "west"
        );
    }
    #[test]
    fn projection_preserves_actual_smell_and_unknown_senses_without_leaking_raw_measurements() {
        let base = json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":1,"observed_at":"2026-10-01T00:00:00Z",
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"dimension":"minecraft:overworld"},"world":{"biome":"plains","sky_visible":true,"weather":"rain"}});
        let mut e = base.clone();
        e["smell_observation"] = json!({"status":"present","smell_id":"zombie","category":"decay",
            "valence":"unpleasant","specificity":"source","source_kind":"entity","effective_strength":5,
            "direction_estimate":{"cardinal":"east"}});
        let context = project(&GameEvent::parse(e).unwrap(), None, 10.0, 7.0, 5000).unwrap();
        let smell = &context.observations["smell"];
        assert_eq!(smell["status"], "present");
        assert_eq!(smell["direction_estimate"]["cardinal"], "east");
        for key in [
            "effective_strength",
            "temperature_modifier",
            "source_kind",
            "distance",
            "entity_id",
            "count",
        ] {
            assert!(smell.get(key).is_none(), "{key}");
        }
        let context = project(&GameEvent::parse(base).unwrap(), None, 10.0, 7.0, 5000).unwrap();
        assert_eq!(context.observations["smell"]["status"], "unknown");
        assert!(context.observations["darkness_observed"].is_null());
    }
}
