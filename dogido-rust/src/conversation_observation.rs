//! The same bounded world facts accompany player replies and spontaneous speech.
//! Facts come from GameEvent; dialogue history and generated text never enter here.
use crate::{chat_catalog, chat_world, environment, events::GameEvent, world_catalog};
use environment::notice::{self, Snapshot};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

mod body_changes;
mod body_facts;
pub(crate) mod subjects;

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
    /// 内部の比較台帳を保ち、モデル用コピーの位置・視認を主語付きの事実へ表示する。
    /// 現在値とbefore/nowに同じ変換を使い、位置計算の入力はモデルへ渡さない。
    pub fn for_model(mut self) -> Self {
        if let Some(body) = self.observations.get_mut("body_observation") {
            *body = if body["status"] == "observed" {
                json!({"status":"observed", "current":{
                    "vision":body_facts::vision(&body["current"]["vision"]), "senses":body["current"]["senses"],
                    "position_relation":body_facts::position(&body["current"]["position_relation"])}})
            } else { json!({"status":"unavailable","reason":body["reason"]}) };
        }
        for change in &mut self.changes {
            if matches!(change.kind.as_str(), "body_self_position" | "body_player_position" | "body_self_orientation" | "body_player_orientation") {
                change.before = Value::Null;
                change.now = json!({"changed":true});
            } else if change.kind == "body_position_relation" {
                change.before = body_facts::position(&change.before);
                change.now = body_facts::position(&change.now);
            } else if change.kind == "body_player_visibility" {
                change.before = body_facts::vision(&change.before);
                change.now = body_facts::vision(&change.now);
            }
        }
        self
    }
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
    if event.body_origin().is_some_and(|o| !o.available) {
        return Ok(Context {
            observations: json!({"observer":"dogido","sensory_status":"unavailable"}),
            ..Context::default()
        });
    }
    let catalog = world_catalog::catalog();
    let projection = environment::projection::project_environment(event);
    let place = chat_world::shared_place_context(
        catalog,
        chat_catalog::catalog(),
        event,
        structure,
        home_distance,
        &projection,
    )?;
    let climate = catalog.climate(event.world.biome.as_deref())?;
    let precipitation = environment::precipitation::from_event(event, &climate)?;
    // 空の可視性・頭上の遮蔽は別項目。木下へ入っても観測済みの雨を消さず、
    // 雨からnullへの差分を「雨が止んだ」と解釈させない。未取得だけは不明のまま残す。
    let weather = if event.world.weather.is_some() || projection.include_sky_context {
        // 気候と高度で雨雪を区別する。世界の雷設定は実際に聞いた雷鳴ではない。
        json!({"label":chat_world::weather_label_from_context(event,&precipitation),
            "precipitation_kind":precipitation.precipitation_kind,
            "precipitation_possible":precipitation.precipitation_possible,
            "surface_snow_observed":precipitation.surface_snow_observed})
    } else {
        Value::Null
    };
    let smell = if let Some(mut present) = environment::ambient::smell_context(event) {
        present["status"] = "present".into();
        present
    } else if let Some(observation) = &event.smell_observation {
        json!({"status":observation.status,"suppression_reason":observation.suppression_reason})
    } else {
        json!({"status":"unknown"})
    };
    let knowledge = crate::catalog_knowledge::for_event(event, structure);
    let mob_list = event.nearby_composition.as_ref().filter(|c| !c.mobs_truncated).map(|c| {
        let mut mobs: Vec<_> = c.mobs.iter().collect();
        mobs.sort_by_key(|m| &m.id);
        mobs.into_iter().map(|mob| {
            let mut row = json!({"id":mob.id,"label":environment::ambient::mob_label(&mob.id),
                "quantity":quantity(&mob.quantity)});
            if !mob.professions.is_empty() {
                let mut groups: Vec<_> = mob.professions.iter().collect();
                groups.sort_by_key(|g| &g.id);
                row["profession_breakdown"] = json!({"relation":"村人合計の内訳",
                    "groups":groups.into_iter().map(|group| {
                        let label = crate::catalog_knowledge::mob("villager", Some(&group.id), group.id == "baby", "nearby_composition")
                            .map(|entry| entry.label).unwrap_or_else(|| "村人".into());
                        json!({"id":group.id,"label":label,"quantity":quantity(&group.quantity)})
                    }).collect::<Vec<_>>()});
            }
            row
        }).collect::<Vec<_>>()
    });
    let block_list = event
        .nearby_composition
        .as_ref()
        .filter(|c| !c.blocks_truncated)
        .map(|c| {
            let mut ids = c.blocks.clone();
            ids.sort();
            ids.into_iter()
                .map(|id| json!({"label":catalog.block_label(Some(&id)),"id":id}))
                .collect::<Vec<_>>()
        });
    Ok(Context {
        catalog_knowledge: (!knowledge.is_empty()).then_some(knowledge),
        villager_routines: crate::villager_routines::project(event),
        observations: json!({
            "observed_at":event.observed_at,
            "dimension":event.player.dimension,
            "subject_states":subjects::project(event, &precipitation)?,
            "place":place.place_line,
            "location_biome":event.world.biome.as_deref().map(|id| json!({
                "id":id,"label":environment::ambient::biome_label(id)})),
            "structure":structure.or(event.world.structure.as_deref()),
            "overhead_cover":event.world.overhead_cover_type,
            "nearby_mob_types":mob_list,
            "nearby_block_types":block_list,
            "biome_scene_visible":projection.include_biome_context,
            "sky_visible":event.world.sky_visible,
            "time_phase":if projection.include_sky_context {json!(event.world.time_phase)} else {Value::Null},
            "weather":weather,
            "rain_heard":event.world.rain_sound_recent_ms.is_some_and(|age|age>=0 && age as u64<=thunder_age_ms),
            "thunder_heard":event.world.thunder_sound_recent_ms.is_some_and(|age|age>=0 && age as u64<=thunder_age_ms),
            "local_light":event.world.local_light,
            "darkness_observed":event.world.local_light.map(|v| v as f64<=darkness_light),
            "smell":smell,
            "mob_states":crate::mob_environment::project(event)
        }),
        ..Context::default()
    })
}

fn quantity(value: &crate::events::NearbyQuantity) -> Value {
    match value {
        crate::events::NearbyQuantity::Exact(n) => json!(n),
        crate::events::NearbyQuantity::Many(_) => json!("いっぱい"),
    }
}

/// Session-owned changes since the last completed decision, not a second memory store.
#[derive(Default)]
pub struct State {
    body_context: Option<crate::body::contracts::Context>,
    current: Option<Context>,
    considered: u64,
    notice_previous: Option<Snapshot>,
    notice_pending: Option<Context>,
    notice_last_sample: Option<u64>,
}
impl State {
    /// 身体の世代変更・失効では感覚の比較だけを消し、Sessionの会話履歴に触れない。
    pub(crate) fn observe_origin(&mut self, event: &GameEvent) {
        let Some(origin) = event.body_origin() else {
            return;
        };
        if !origin.available || origin.context != self.body_context {
            // 古い判断の完了通知で、新世代の差分を考慮済みにしない。
            let revision = self
                .current
                .as_ref()
                .map_or(self.considered, |c| c.revision);
            *self = Self {
                body_context: origin.context.clone(),
                considered: revision,
                ..Self::default()
            };
        }
    }
    /// 周期に来た新しい全snapshotだけを比較する。初回は基準の作成だけ。
    /// 今回の機会を使えなくても差分は次回へ持ち越さず、発話結果にも依存しない。
    pub fn sample_notice(&mut self, now: u64, interval_ms: u64, full_snapshot: bool) {
        self.notice_pending = None;
        if !full_snapshot
            || self
                .notice_last_sample
                .is_some_and(|last| now.saturating_sub(last) < interval_ms)
        {
            return;
        }
        let Some(mut current) = self.context() else {
            return;
        };
        if let Some(previous) = &self.notice_previous {
            current.changes = notice::changes(previous, &current);
            if !current.changes.is_empty() {
                self.notice_pending = Some(current.clone());
            }
        }
        self.notice_previous = Some(notice::snapshot(&current));
        self.notice_last_sample = Some(now);
    }
    pub fn noticing_context(&self) -> Option<Context> {
        self.notice_pending.clone().map(Context::for_model)
    }
    pub fn finish_notice_opportunity(&mut self) {
        self.notice_pending = None;
    }
    pub fn observe(&mut self, mut next: Context) {
        next.revision = self.considered;
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
            body_changes::append(&previous.observations, &mut next);
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
            for key in [
                "weather",
                "rain_heard",
                "smell",
                "place",
                "location_biome",
                "structure",
                "overhead_cover",
                "nearby_mob_types",
                "nearby_block_types",
                "time_phase",
                "sky_visible",
                "local_light",
                "darkness_observed",
                "subject_states",
            ] {
                let before = &previous.observations[key];
                let after = &next.observations[key];
                // Smell direction may move while the identity stays the same. The
                // current direction is available without creating endless new turns.
                let signature = |value: &Value| {
                    if key == "subject_states" {
                        return subjects::signature(value);
                    }
                    if key == "nearby_mob_types" {
                        return notice::mob_kind_signature(value);
                    }
                    if key == "smell" {
                        return environment::ambient::smell_change_signature(value);
                    }
                    value.clone()
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
        state.observe(frame(
            "clear",
            json!({"status":"none","components":[],"basis":"smell_policy_v2"}),
        ));
        state.observe(frame(
            "rain",
            json!({"status":"none","components":[],"basis":"smell_policy_v2"}),
        ));
        let offered = state.context().unwrap();
        assert_eq!(offered.changes[0].kind, "weather");
        state.observe(frame(
            "rain",
            json!({"status":"present","components":[{"smell_id":"zombie"}],"basis":"smell_policy_v2"}),
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
    fn strength_updates_do_not_create_another_smell_arrival() {
        let mut state = State::default();
        state.observe(frame("clear",json!({"status":"present","components":[{"smell_id":"zombie","effective_strength":5}],"basis":"smell_policy_v2"})));
        state.observe(frame("clear",json!({"status":"present","components":[{"smell_id":"zombie","effective_strength":8}],"basis":"smell_policy_v2"})));
        assert!(state.context().unwrap().changes.is_empty());
        assert_eq!(
            state.context().unwrap().observations["smell"]["components"][0]["effective_strength"],
            8
        );
    }
    fn surface(light: u8, y: f64, ceiling: f64) -> Context {
        let event = GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture",
            "observed_at":"2026-10-06T00:00:00Z",
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"dimension":"minecraft:overworld","position":{"y":y}},
            "world":{"biome":"meadow","sky_visible":true,"weather":"clear","local_light":light,"ceiling_height":ceiling}})).unwrap();
        project(&event, Some("village_plains"), 10.0, 3.0, 5000).unwrap()
    }
    #[test]
    fn shared_place_keeps_structure_without_repeating_separate_observations() {
        let context = surface(8, 123.0, 24.0);
        assert_eq!(
            context.observations["place"],
            "開けた地上 / 構造物: 平原の村"
        );
        assert_eq!(
            context.observations["location_biome"],
            json!({"id":"meadow","label":"草地"})
        );
        assert_eq!(context.observations["sky_visible"], true);
        assert_eq!(context.observations["local_light"], 8);
    }
    #[test]
    fn light_only_change_does_not_repeat_the_place_or_turn_measurements_into_moves() {
        let mut state = State::default();
        state.observe(surface(8, 123.0, 24.0));
        state.observe(surface(7, 123.0, 24.0));
        let context = state.context().unwrap();
        assert_eq!(context.changes.len(), 1);
        assert_eq!(context.changes[0].kind, "local_light");
        assert_eq!(context.changes[0].before, 8);
        assert_eq!(context.changes[0].now, 7);
        state.considered(context.revision);
        state.observe(surface(7, 124.0, 23.0));
        assert!(state.context().unwrap().changes.is_empty());
        state.observe(surface(3, 124.0, 23.0));
        assert!(
            state
                .context()
                .unwrap()
                .changes
                .iter()
                .any(|c| c.kind == "darkness_observed" && c.now == true)
        );
    }
    #[test]
    fn separated_sky_and_biome_changes_remain_visible() {
        let mut state = State::default();
        state.observe(surface(8, 123.0, 24.0));
        let mut next = surface(8, 123.0, 24.0);
        next.observations["location_biome"] = json!("タイガ");
        next.observations["sky_visible"] = json!(false);
        state.observe(next);
        let context = state.context().unwrap();
        assert_eq!(
            context
                .changes
                .iter()
                .map(|c| c.kind.as_str())
                .collect::<Vec<_>>(),
            ["location_biome", "sky_visible"]
        );
    }
    #[test]
    fn projection_preserves_actual_smell_and_unknown_senses_without_leaking_raw_measurements() {
        let base = json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":1,"observed_at":"2026-10-01T00:00:00Z",
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"dimension":"minecraft:overworld"},"world":{"biome":"plains","sky_visible":true,"weather":"rain"}});
        let mut e = base.clone();
        e["smell_observation"] = json!({"status":"present","components":[{"smell_id":"zombie","category":"decay","valence":"unpleasant","source_kind":"entity","effective_strength":5}],"basis":"smell_policy_v2"});
        let context = project(&GameEvent::parse(e).unwrap(), None, 10.0, 7.0, 5000).unwrap();
        let smell = &context.observations["smell"];
        assert_eq!(smell["status"], "present");
        let component = &smell["components"][0];
        assert_eq!(component["effective_strength"], 5);
        assert_eq!(component["source_kind"], "entity");
        for key in [
            "direction_estimate",
            "temperature_modifier",
            "distance",
            "entity_id",
            "count",
        ] {
            assert!(
                smell.get(key).is_none() && component.get(key).is_none(),
                "{key}"
            );
        }
        let context = project(&GameEvent::parse(base).unwrap(), None, 10.0, 7.0, 5000).unwrap();
        assert_eq!(context.observations["smell"]["status"], "unknown");
        assert!(context.observations["darkness_observed"].is_null());
    }
}
