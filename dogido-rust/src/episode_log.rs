//! Best-effort evaluation records, never read by memory, prompts or decisions.
use crate::events::{EventTime, GameEvent};
use chrono::{SecondsFormat, Timelike};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    sync::{
        Mutex,
        mpsc::{self, SyncSender},
    },
    thread::JoinHandle,
};
pub const SCHEMA_VERSION: u64 = 5;
fn observed_at(event: &GameEvent) -> String {
    match &event.observed_at {
        EventTime::Aware(time) => time.to_rfc3339_opts(
            if time.nanosecond() == 0 {
                SecondsFormat::Secs
            } else {
                SecondsFormat::Micros
            },
            false,
        ),
        EventTime::Naive(time) => time
            .format(if time.nanosecond() == 0 {
                "%Y-%m-%dT%H:%M:%S"
            } else {
                "%Y-%m-%dT%H:%M:%S%.6f"
            })
            .to_string(),
    }
}
pub struct Record {
    pub event: GameEvent,
    pub event_id: String,
    pub session_id: String,
    pub recorded_at: String,
    pub state_before: Value,
    pub mode_after: Value,
    pub combat_active: bool,
    pub actions: Vec<Value>,
    pub adapter_commands: Vec<Value>,
    /// Newly consumed real adapter receipts, never repeated ACKs or inferred success.
    pub command_results: Vec<Value>,
}
fn pick(value: &Value, names: &str) -> Value {
    Value::Object(
        names
            .split_whitespace()
            .map(|name| (name.to_owned(), value[name].clone()))
            .collect(),
    )
}
fn rows(value: &Value, names: &str) -> Vec<Value> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .map(|row| pick(row, names))
        .collect()
}
fn unique(items: &[Value], key: &str) -> Vec<String> {
    items
        .iter()
        .filter_map(|v| v[key].as_str().map(str::to_owned))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}
pub fn observation(event: &GameEvent) -> Value {
    let e = json!(event);
    let mut visual = rows(
        &e["visual_threats"],
        "type entity_id distance direction approaching fuse_active on_fire in_water certainty",
    );
    let mut auditory = rows(
        &e["auditory_threats"],
        "label source_id sound_event direction distance_band certainty spoken_name_allowed",
    );
    let scent = rows(
        &e["zombie_scent_clues"],
        "type entity_id distance_band certainty basis",
    );
    let mut passive = rows(
        &e["passive_mobs"],
        "type distance direction certainty temperament caution_reason is_baby profession villager_type",
    );
    let mut ambient = rows(
        &e["ambient_sounds"],
        "type source_id sound_event direction distance_band certainty",
    );
    let mut resources = rows(&e["nearby_resources"], "type name distance direction");
    for row in visual
        .iter_mut()
        .chain(auditory.iter_mut())
        .chain(passive.iter_mut())
        .chain(ambient.iter_mut())
        .chain(resources.iter_mut())
    {
        row["direction"] = pick(&row["direction"], "horizontal cardinal vertical");
    }
    let nearest = visual.iter().min_by(|a, b| {
        a["distance"]
            .as_f64()
            .unwrap_or(f64::INFINITY)
            .total_cmp(&b["distance"].as_f64().unwrap_or(f64::INFINITY))
    });
    let mut player = pick(
        &e["player"],
        "dimension position health hunger held_item block_breaking_active yaw pitch hotbar vehicle active_status_effects",
    );
    player["position"] = pick(&player["position"], "x y z");
    json!({"player":player,
        "world":pick(&e["world"],"biome structure time_of_day time_phase weather local_light sky_visible surface_y depth_below_surface ceiling_height overhead_cover_type is_submerged submerged_depth_blocks air_supply cardinal_wall_count double_height_open_side_count drafty_opening_count enclosure_score connected_dark_volume nearest_dark_spawn_distance danger_darkness_score nearby_light_source_count nearest_light_source_distance nearby_door_count open_door_count nearby_window_present nearby_bed_count nearby_sleeping_people_count safe_zone_with_door respawn_point_set respawn_distance"),
        "visual_threats":{"count":visual.len(),"types":unique(&visual,"type"),"nearest":nearest,"items":visual},
        "auditory_threats":{"count":auditory.len(),"labels":unique(&auditory,"label"),"distance_bands":unique(&auditory,"distance_band"),"items":auditory},
        "zombie_scent_clues":{"count":scent.len(),"types":unique(&scent,"type"),"items":scent},
        "smell_observation":e["smell_observation"],"ambient_sounds":ambient,
        "passive_mobs":{"count":passive.len(),"types":unique(&passive,"type"),"items":passive},
        "inventory":e["inventory"],"nearby_resources":resources,"dropped_items":e["dropped_items"],"recent_block_breaks":e["recent_block_breaks"],
        "look_target":if e["look_target"].is_null(){Value::Null}else{pick(&e["look_target"],"kind name distance")},
        "combat":pick(&e["combat"],"combat_active_hint recent_damage_ms recent_hostile_visual_ms recent_hostile_audio_ms hostiles_within_7 hostiles_within_10 hostile_scan_distance hostiles_within_scan_ground hostiles_within_30_ground hostile_outcomes")})
}
impl Record {
    pub fn payload(&self) -> Value {
        let e = json!(self.event);
        let raw = self
            .event
            .meta
            .user_text
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty());
        let actions = !self.actions.is_empty();
        let commands = !self.adapter_commands.is_empty();
        let results = !self.command_results.is_empty();
        let layers: Vec<_> = self.actions.iter().map(|a| a["layer"].clone()).collect();
        json!({"schema_version":SCHEMA_VERSION,"record_type":"decision_episode","episode_id":format!("ep_{}",uuid::Uuid::new_v4().simple()),
            "recorded_at":self.recorded_at,"session_id":self.session_id,"event_id":self.event_id,
            "trigger":{"input_schema_version":e["schema_version"],"game":e["game"],"adapter":e["adapter"],"event_name":e["event"]["name"],
                "source_kind":e["event"]["source_kind"],"priority_hint":e["event"]["priority_hint"],"certainty":e["event"]["certainty"],
                "sequence":e["sequence"],"observed_at":observed_at(&self.event),"player_input":raw.map(|text|json!({"raw":text,"interpreted":null}))},
            "observation":observation(&self.event),"state_before":self.state_before,
            "decision":{"source":"state_machine_and_service_policy","kind":if actions||commands{"emit_actions"}else if results{"observe_adapter_result"}else{"no_action"},
                "mode_after":self.mode_after,"mode_changed":self.state_before["mode"]!=self.mode_after,"combat_active":self.combat_active,"haiku_emitted":false,"layers":layers},
            "action":{"count":self.actions.len(),"items":self.actions,"adapter_commands":self.adapter_commands},
            "result":{"status":if actions{"actions_selected"}else if commands{"commands_selected"}else if results{"adapter_result_observed"}else{"no_action"},
                "scope":if results{"adapter_execution_observed"}else{"service_decision"},
                "output_flags":{"panic_cue_enqueued":layers.contains(&json!("panic_cue")),"callout_enqueued":layers.contains(&json!("callout")),"speech_enqueued":layers.contains(&json!("speech"))},
                "adapter_command_results":self.command_results}})
    }
}
/// Filesystem work stays off the game-event worker. Shutdown drains accepted records.
pub struct Recorder {
    sender: Mutex<Option<SyncSender<Record>>>,
    worker: Mutex<Option<JoinHandle<()>>>,
}
impl Recorder {
    pub fn new(root: PathBuf) -> std::io::Result<Self> {
        let (sender, receiver) = mpsc::sync_channel::<Record>(256);
        let worker = std::thread::Builder::new()
            .name("dogido-episodes".into())
            .spawn(move || {
                let path = root.join("eval/episodes.jsonl");
                for record in receiver {
                    let result = (|| -> anyhow::Result<()> {
                        let mut line = serde_json::to_vec(&record.payload())?;
                        line.push(b'\n');
                        fs::create_dir_all(path.parent().expect("episode parent"))?;
                        let mut file = OpenOptions::new().create(true).append(true).open(&path)?;
                        file.lock()?;
                        file.write_all(&line)?;
                        Ok(())
                    })();
                    if let Err(error) = result {
                        tracing::warn!(event="episode_write_failed",path=%path.display(),%error);
                    }
                }
            })?;
        Ok(Self {
            sender: Mutex::new(Some(sender)),
            worker: Mutex::new(Some(worker)),
        })
    }
    pub fn record(&self, record: Record) {
        let Ok(sender) = self.sender.lock() else {
            return;
        };
        if let Some(sender) = &*sender
            && let Err(error) = sender.try_send(record)
        {
            tracing::warn!(event="episode_record_dropped",reason=%error);
        }
    }
    pub fn close(&self) {
        if let Ok(mut sender) = self.sender.lock() {
            sender.take();
        }
        if let Ok(mut worker) = self.worker.lock()
            && let Some(worker) = worker.take()
            && worker.join().is_err()
        {
            tracing::warn!(event = "episode_writer_failed");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn schema_five_matches_python_observations_actions_and_receipts() {
        let fixtures: Vec<Value> =
            serde_json::from_str(include_str!("../fixtures/episode-records.json")).unwrap();
        for fixture in fixtures {
            let e = &fixture["expected"];
            let record = Record {
                event: GameEvent::parse(fixture["event"].clone()).unwrap(),
                event_id: "evt_fixture".into(),
                session_id: "ses_fixture".into(),
                recorded_at: e["recorded_at"].as_str().unwrap().into(),
                state_before: e["state_before"].clone(),
                mode_after: e["decision"]["mode_after"].clone(),
                combat_active: e["decision"]["combat_active"].as_bool().unwrap(),
                actions: e["action"]["items"].as_array().unwrap().clone(),
                adapter_commands: vec![],
                command_results: e["result"]["adapter_command_results"]
                    .as_array()
                    .unwrap()
                    .clone(),
            };
            let mut actual = record.payload();
            actual.as_object_mut().unwrap().remove("episode_id");
            assert_eq!(actual, *e);
        }
    }
}
