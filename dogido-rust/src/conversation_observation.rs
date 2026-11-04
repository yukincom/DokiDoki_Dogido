//! The same bounded world facts accompany player replies and spontaneous speech.
//! Facts come from GameEvent; dialogue history and generated text never enter here.
use crate::{chat_catalog, chat_world, environment, events::GameEvent, world_catalog};
use environment::notice::{self, Snapshot};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

mod body_changes;
pub(crate) mod dialogue_capacity;
mod body_facts;
pub(crate) mod image_timing;
pub(crate) mod hearing;
pub(crate) mod subjects;

/// Reuse catalogue-name extraction on the current turn. The existing mob-query
/// route may also use the latest named completed line; an empty result must not
/// be treated as evidence of absence.
pub(crate) fn chat_topic_names(user: &str, history: &str, use_history: bool) -> Vec<String> {
    let mut names = crate::chat_validation::mentioned(user);
    if names.is_empty() && use_history {
        for line in history.lines().rev() {
            names = crate::chat_validation::mentioned(line);
            if !names.is_empty() { break; }
        }
    }
    names
}

pub(crate) fn chat_topic_mobs(user: &str, history: &str, use_history: bool) -> Vec<String> {
    let mut ids = Vec::new();
    for name in chat_topic_names(user, history, use_history) {
        if let Some(entry) = crate::catalog_knowledge::mob(&name, None, false, "player_named")
            && !ids.contains(&entry.id)
        {
            ids.push(entry.id);
        }
    }
    ids
}

/// Narrow structured mob observations only when the existing name lookup found
/// an unambiguous set of conversation subjects. Unknown hearing is preserved.
pub(crate) fn select_chat_mobs(world: &mut Value, topics: &[String], combat: bool) {
    if topics.is_empty() { return; }
    let matches = |id: &str| {
        let id = id.strip_prefix("minecraft:").unwrap_or(id);
        topics.iter().any(|topic| topic == id)
    };
    if let Some(observations) = world.get_mut("observations").and_then(Value::as_object_mut) {
        for (key, id_key) in [("nearby_mob_types", "id"), ("mob_states", "catalog_id")] {
            if let Some(rows) = observations.get_mut(key).and_then(Value::as_array_mut) {
                rows.retain(|row| row[id_key].as_str().is_none_or(&matches));
            }
        }
        if !combat && let Some(rows) = observations.get_mut("visible_hostiles").and_then(Value::as_object_mut) {
            rows.retain(|key, _| matches(key));
        }
        if let Some(sounds) = observations.get_mut("hearing").and_then(|v| v.get_mut("sounds")).and_then(Value::as_array_mut) {
            sounds.retain(|sound| {
                let kind = crate::chat_observation::resolve_mob(
                    "", sound["sound_event"].as_str(), &crate::chat_observation::CatalogLabels);
                kind.as_deref().is_none_or(&matches)
            });
        }
    }
    if let Some(changes) = world.get_mut("changes").and_then(Value::as_array_mut) {
        changes.retain_mut(|change| match change["kind"].as_str() {
            Some("mob_environment") => change["now"]["catalog_id"].as_str().is_none_or(&matches),
            Some("nearby_mob_types") => {
                for key in ["before", "now"] {
                    if let Some(rows) = change[key].as_array_mut() {
                        rows.retain(|row| row["id"].as_str().is_none_or(&matches));
                    }
                }
                change["before"] != change["now"]
            }
            _ => true,
        });
    }
}

/// Strip bookkeeping only from the copy serialized for the model.
pub(crate) fn strip_model_bookkeeping(value: &mut Value) {
    match value {
        Value::Object(object) => {
            for key in ["observed_at", "captured_at", "capture_id", "motion_sequence", "revision"] {
                object.shift_remove(key);
            }
            for child in object.values_mut() {
                strip_model_bookkeeping(child);
            }
        }
        Value::Array(rows) => {
            for child in rows { strip_model_bookkeeping(child); }
        }
        _ => {}
    }
}

/// Remove only the generated UTC prefix from normalized history rows; the
/// speaker's own text, including any dates they mentioned, is unchanged.
pub(crate) fn history_without_clock(history: &str) -> String {
    history.lines().map(|line| {
        let Some((clock, rest)) = line.strip_prefix('[').and_then(|line| line.split_once(" | ")) else {
            return line.to_owned();
        };
        let valid = clock.strip_suffix(" UTC").is_some_and(|value| {
            chrono::NaiveDateTime::parse_from_str(value, "%Y-%m-%d %H:%M:%S").is_ok()
        });
        if valid { format!("[{rest}") } else { line.to_owned() }
    }).collect::<Vec<_>>().join("\n")
}

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
                let mut senses = body["current"]["senses"].clone();
                if let Some(fields) = senses.as_object_mut() {
                    fields.retain(|key, _| matches!(key.as_str(), "status" | "incomplete" | "reason"));
                }
                json!({"status":"observed", "current":{
                    "vision":body_facts::vision(&body["current"]["vision"]), "senses":senses,
                    "position_relation":body_facts::position(&body["current"]["position_relation"])}})
            } else { json!({"status":"unavailable","reason":body["reason"]}) };
        }
        // 雨・雷のboolは内部の変化検出用。モデルの聴覚はhearingへ一本化する。
        if let Some(observations) = self.observations.as_object_mut() {
            observations.remove("rain_heard");
            observations.remove("thunder_heard");
        }
        // この差分は個体IDを失っている。最新の視認で抑制できるhearingだけをモデルへ渡す。
        // 内部の周期比較・保留差分は元のContextに残る。
        self.changes.retain(|change| change.kind != "heard_hostile_sounds");
        for change in &mut self.changes {
            if matches!(change.kind.as_str(), "body_self_position" | "body_player_position" | "body_self_orientation" | "body_player_orientation") {
                // 既に表示用に変換した差分は、反応用コピーを作るときにも保持する。
                if change.before.is_object() {
                    change.now = body_facts::motion(&change.kind, &change.before, &change.now);
                    change.before = Value::Null;
                }
            } else if change.kind == "body_position_relation" {
                change.before = body_facts::position(&change.before);
                change.now = body_facts::position(&change.now);
            } else if change.kind == "body_view" {
                // 撮影識別は内部用。フレーム化でもこの通知にマスの説明や撮影台帳を重ねない。
                change.before = Value::Null;
                change.now = json!({"changed":true});
            } else if change.kind == "body_player_visibility" {
                change.before = body_facts::vision(&change.before);
                change.now = body_facts::vision(&change.now);
            }
        }
        self.changes.retain(|c| !c.now.is_null() || !matches!(c.kind.as_str(), "body_self_position" | "body_player_position" | "body_self_orientation" | "body_player_orientation"));
        self
    }
    /// 同じ観測時刻の共通事実へ、既存の保持期間内の音だけを添える。
    pub(crate) fn with_recent_hearing(mut self, event: &GameEvent, snapshot: &crate::chat_observation::Snapshot, labels: &impl crate::chat_observation::Labels) -> Self {
        hearing::append_recent(&mut self.observations["hearing"], event, snapshot, labels);
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
            observations: json!({"observer":"dogido","sensory_status":"unavailable","weather":{"precipitation_kind":"unknown"},"hearing":hearing::project(event, thunder_age_ms)}),
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
    let weather = json!({"label":chat_world::weather_label_from_context(event,&precipitation),
        "precipitation_kind":precipitation.precipitation_kind});
    let smell = if let Some(mut present) = environment::ambient::smell_context(event) {
        present["status"] = "present".into();
        present
    } else if let Some(observation) = &event.smell_observation {
        json!({"status":observation.status,"suppression_reason":observation.suppression_reason})
    } else {
        json!({"status":"unknown"})
    };
    let knowledge = crate::catalog_knowledge::for_event(event, structure);
    let mob_list = event.nearby_composition.as_ref().filter(|c| !c.mobs_truncated || !c.mobs.is_empty()).map(|c| {
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
        .filter(|c| !c.blocks_truncated || !c.blocks.is_empty())
        .map(|c| {
            let mut ids = c.blocks.clone();
            ids.sort();
            ids.into_iter()
                .map(|id| json!({"label":catalog.block_label(Some(&id)),"id":id}))
                .collect::<Vec<_>>()
        });
    let mut context = Context {
        catalog_knowledge: (!knowledge.is_empty()).then_some(knowledge),
        villager_routines: crate::villager_routines::project(event),
        observations: json!({
            "observed_at":event.observed_at,
            "hearing":hearing::project(event, thunder_age_ms),
            "visible_hostiles":event.body_origin().filter(|o| !o.incomplete.contains(&crate::body::senses::Incomplete::VisualThreats)
                || !event.visual_threats.is_empty()).map(|_| notice::visible_hostiles(event)),
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
    };
    if let Some(c) = &event.nearby_composition {
        if event.body_origin().is_some_and(|origin| origin.available) && !c.least_common_blocks.is_empty() {
            let labels: Vec<_> = c.least_common_blocks.iter().map(|id| catalog.block_label(Some(id))).collect();
            context.observations[dialogue_capacity::LOCAL_HIGHLIGHTS] = json!(labels);
        }
        if c.mobs_truncated { context.observations["nearby_mob_types_partial"] = json!(true); }
        if c.blocks_truncated { context.observations["nearby_block_types_partial"] = json!(true); }
    }
    Ok(context)
}

fn quantity(value: &crate::events::NearbyQuantity) -> Value {
    match value {
        crate::events::NearbyQuantity::Exact(n) => json!(n),
        crate::events::NearbyQuantity::Many(_) => json!("いっぱい"),
    }
}

pub(crate) const PLAYER_HOSTILES_CLEARED: &str = "player_hostiles_cleared";
pub(crate) const PLAYER_HOSTILE_DEFEATED: &str = "player_hostile_defeated";

/// Session-owned changes since the last completed decision, not a second memory store.
#[derive(Default)]
pub struct State {
    player_hostiles_observed: bool,
    player_dimension: Option<String>,
    // プレイヤーの明示戦闘結果は、身体感覚の一時失効では消さない。
    player_changes: Vec<Change>,
    body_context: Option<crate::body::contracts::Context>,
    current: Option<Context>,
    considered: u64,
    notice_previous: Option<Snapshot>,
    notice_pending: Option<Context>,
    notice_last_sample: Option<u64>,
    spontaneous_hearing: hearing::Spontaneous,
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
                player_hostiles_observed: self.player_hostiles_observed,
                player_dimension: self.player_dimension.take(),
                player_changes: std::mem::take(&mut self.player_changes),
                considered: revision,
                ..Self::default()
            };
        }
        if origin.available { self.spontaneous_hearing.observe(event); }
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
                if matches!(key, "nearby_mob_types" | "nearby_block_types") {
                    let partial = format!("{key}_partial");
                    if previous.observations[&partial] == true || next.observations[&partial] == true {
                        continue;
                    }
                }
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
    /// プレイヤー周辺の戦闘観測はドギド自身の視覚・聴覚とは別。距離・解除時間の再判定はしない。
    pub(crate) fn observe_player_combat(
        &mut self,
        event: &GameEvent,
        defeats: Vec<String>,
        workshop_changes: Option<&mut Vec<Change>>,
    ) {
        let Some(context) = &mut self.current else {
            return;
        };
        if let Some(dim) = &event.player.dimension {
            if self.player_dimension.as_ref().is_some_and(|previous| previous != dim) {
                self.player_hostiles_observed = false;
                self.player_changes.clear();
            }
            self.player_dimension = Some(dim.clone());
        }
        let died = event.event.name == crate::events::EventName::PlayerDied;
        let present = if died { Some(false) } else {
            crate::combat::outcomes::Outcomes::hostile_presence(event)
        };
        // 中断中の事実はその句の再開判断だけが受け取る。周期の気づきと二重に配送しない。
        if present == Some(true) || died {
            self.player_changes.retain(|c| c.kind != PLAYER_HOSTILES_CLEARED);
        }
        let changes = workshop_changes.unwrap_or(&mut self.player_changes);
        let cleared = present.is_some_and(|present| {
            std::mem::replace(&mut self.player_hostiles_observed, present) && !present && !died
        });
        if present == Some(true) || died {
            changes.retain(|c| c.kind != PLAYER_HOSTILES_CLEARED);
        }
        let mut append = |kind: &str, before: Value, fact: &str| {
            // 生成や相談が長く保留されても、古い撃破だけを押し出し、他の差分を保つ。
            if kind == PLAYER_HOSTILE_DEFEATED
                && changes.iter().filter(|c| c.kind == kind).count() >= 32
                && let Some(oldest) = changes.iter().position(|c| c.kind == kind)
            {
                changes.remove(oldest);
            }
            context.revision = context.revision.saturating_add(1);
            changes.push(Change {
                kind: kind.into(),
                before,
                now: json!({"scope":"プレイヤーの周囲","fact":fact}),
                observed_at: event.observed_at.to_rfc3339(),
                revision: context.revision,
            });
        };
        for fact in defeats {
            append(PLAYER_HOSTILE_DEFEATED, Value::Null, &fact);
        }
        let still_nearby = event.combat.former_opponent_nearby == Some(true);
        if event.ended_by_last_kill() && (!cleared || still_nearby) {
            append("combat_ended", Value::Null, "プレイヤーと交戦していた最後の敵が倒れ、戦闘が終わった");
        }
        if cleared && still_nearby && !event.ended_by_last_kill() {
            append("combat_ended", Value::Null, "プレイヤーとの交戦は終わった。相手はまだ周囲にいる");
        } else if cleared && !still_nearby {
            append(PLAYER_HOSTILES_CLEARED, json!(true), "どうやら敵が周囲からいなくなったようだ");
        }
    }
    pub fn context(&self) -> Option<Context> {
        self.current.clone().map(|mut c| {
            c.changes.retain(|c| c.revision > self.considered);
            c.changes.extend(self.player_changes.iter().cloned());
            c.changes.sort_by_key(|change| change.revision);
            c
        })
    }
    /// 生成直前に一度だけ呼ぶ。予約時の読取や通常の質問は継続提示の基準を進めない。
    pub(crate) fn present_spontaneous_hearing(&mut self, hearing: &mut Value,
        sounds: Vec<crate::chat_observation::SoundCandidate>, retention_ms: u64) {
        self.spontaneous_hearing.present(hearing, sounds, retention_ms);
    }
    pub fn considered(&mut self, revision: u64) {
        // Only a version actually offered to this decision can be acknowledged.
        if let Some(current) = &self.current {
            let offered = revision.min(current.revision);
            self.considered = self.considered.max(offered);
            self.player_changes.retain(|change| change.revision > offered);
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
