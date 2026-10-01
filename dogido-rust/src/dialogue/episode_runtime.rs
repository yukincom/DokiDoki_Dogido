//! Snapshot the synchronous event decision; never claim later generation/playback.
use super::{Data, Dialogue, Session};
use crate::{
    combat::model::{Delivery, Speech},
    episode_log::Record,
};
use serde_json::{Value, json};
use std::collections::HashSet;

pub(super) struct Before {
    pub state: Value,
    turns: HashSet<String>,
    pending: Value,
}
impl Before {
    pub fn capture(d: &Data, sid: &str) -> Self {
        let s = &d.sessions[sid];
        Self {
            state: state(s),
            turns: d
                .rows
                .iter()
                .filter(|r| r["session_id"] == sid)
                .filter_map(|r| r["turn_id"].as_str().map(str::to_owned))
                .collect(),
            pending: json!(s.pending_warning),
        }
    }
    pub fn actions(&mut self, d: &Data, sid: &str) -> Vec<Value> {
        let mut actions = vec![];
        for row in d.rows.iter().filter(|r| {
            r["session_id"] == sid
                && r["turn_id"]
                    .as_str()
                    .is_some_and(|id| !self.turns.contains(id))
        }) {
            for speech in row["combat_actions"].as_array().into_iter().flatten() {
                project(
                    &mut actions,
                    speech,
                    if row["category"] == "callout" {
                        "callout"
                    } else {
                        "speech"
                    },
                );
            }
        }
        let pending = &d.sessions[sid].pending_warning;
        if json!(pending) != self.pending {
            for speech in pending.iter().flatten() {
                project(&mut actions, &json!(speech), layer(speech));
            }
        }
        self.turns.extend(
            d.rows
                .iter()
                .filter(|r| r["session_id"] == sid)
                .filter_map(|r| r["turn_id"].as_str().map(str::to_owned)),
        );
        actions
    }
    // submit_inner can select an immediate fixed answer (e.g. assist). Only that
    // input's new queue row belongs here; unrelated asynchronous completions do not.
    pub fn input_actions(&self, d: &Data, sid: &str, text: &str) -> Vec<Value> {
        let mut actions = vec![];
        for row in d.rows.iter().filter(|r| {
            r["session_id"] == sid
                && r["source"] == "player_input"
                && r["player_input_text"] == text
                && r["turn_id"]
                    .as_str()
                    .is_some_and(|id| !self.turns.contains(id))
        }) {
            for speech in row["combat_actions"].as_array().into_iter().flatten() {
                project(
                    &mut actions,
                    speech,
                    if row["category"] == "callout" {
                        "callout"
                    } else {
                        "speech"
                    },
                );
            }
        }
        actions
    }
}
fn state(s: &Session) -> Value {
    json!({"mode":s.mode,"pending_haiku_after_preface":s.haiku.active.is_some(),
        "player_input_queued":s.pending_input.is_some()||s.deferred_input.is_some()||!s.knowledge_queue.is_empty(),
        "workshop":s.haiku.workshop.as_ref().map_or("none",|w|if w.combat_paused(){"combat_paused"}else if w.open{"open"}else{"none"})})
}
fn layer(s: &Speech) -> &'static str {
    if matches!(s.delivery, Delivery::Combat) {
        "callout"
    } else {
        "speech"
    }
}
fn action(s: &Value, layer: &str) -> Value {
    json!({"kind":"audio","layer":layer,"text":s["text"],"cue_id":s["cue_id"],
        "cue_sequence":s["cue_sequence"].as_array().cloned().unwrap_or_default(),
        "interrupt":s["interrupt"].as_bool().unwrap_or(false),"protect_ms":s["protect_ms"].as_u64().unwrap_or(0),
        "speech_profile":null,"speed_scale":null,"speech_segments":[],"speech_segment_pause_ms":0,
        "queue_priority":"normal","queue_replace_key":null,"references":[]})
}
fn project(out: &mut Vec<Value>, s: &Value, default_layer: &str) {
    if s["visual_plan"].is_object() {
        let plan = &s["visual_plan"];
        if plan["cue"].is_object() {
            let mut cue = s.clone();
            cue["text"] = plan["cue"]["text"].clone();
            cue["cue_id"] = plan["cue"]["id"].clone();
            cue["cue_sequence"] = json!([]);
            out.push(action(&cue, "panic_cue"));
        }
        if plan["text"].as_str().is_some_and(|s| !s.is_empty()) {
            let mut body = s.clone();
            body["text"] = plan["text"].clone();
            body["cue_id"] = Value::Null;
            body["cue_sequence"] = plan["cue_sequence"].clone();
            out.push(action(&body, "callout"));
        }
    } else {
        let layer = if s["cue_id"].is_string() {
            "panic_cue"
        } else {
            default_layer
        };
        out.push(action(s, layer));
    }
}
impl Dialogue {
    pub(super) fn record_episode(&self, record: Record) {
        if let Some(recorder) = &self.episodes {
            recorder.record(record);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dialogue::DialogueConfig;
    use std::{path::PathBuf, sync::Arc};
    fn config(root: &std::path::Path) -> DialogueConfig {
        let mut c = DialogueConfig {
            audio_enabled: false,
            ..DialogueConfig::default()
        };
        c.haiku.enabled = false;
        c.haiku.memory_dir = root.into();
        c
    }
    fn root() -> PathBuf {
        std::env::temp_dir().join(format!("dogido-episode-test-{}", uuid::Uuid::new_v4()))
    }
    fn event(sequence: i64) -> crate::events::GameEvent {
        crate::events::GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":sequence,
            "observed_at":chrono::Utc::now(),"event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "world":{"time_phase":"day","weather":"clear","biome":"plains","local_light":15,"sky_visible":true,"danger_darkness_score":0.0}})).unwrap()
    }
    fn rows(root: &std::path::Path) -> Vec<Value> {
        std::fs::read_to_string(root.join("eval/episodes.jsonl"))
            .unwrap()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }
    #[tokio::test]
    async fn nonduplicate_events_and_only_new_adapter_receipts_are_recorded() {
        let root = root();
        let d = Dialogue::new(config(&root)).unwrap();
        d.register("s", "試験", false);
        let first = d.observe("s", event(1), Some("one"));
        assert_eq!(d.observe("s", event(1), Some("one"))["deduplicated"], true);
        let mut second = json!(event(2));
        second["command_results"] = json!([{"command_id":"unknown-command","command_type":"select_hotbar","status":"failed","executed_at":chrono::Utc::now(),"detail_code":"test_failure"}]);
        let receipt = crate::events::GameEvent::parse(second.clone()).unwrap();
        let ack = d.observe("s", receipt.clone(), None);
        assert_eq!(ack["acknowledged_command_ids"], json!(["unknown-command"]));
        assert_eq!(d.observe("s", receipt, None)["deduplicated"], true);
        second["sequence"] = json!(3);
        d.observe("s", crate::events::GameEvent::parse(second).unwrap(), None);
        d.shutdown().await;
        let rows = rows(&root);
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0]["event_id"], first["event_id"]);
        assert_eq!(rows[0]["result"]["status"], "no_action");
        assert_eq!(rows[1]["result"]["scope"], "adapter_execution_observed");
        assert_eq!(
            rows[1]["result"]["adapter_command_results"][0]["command_id"],
            "unknown-command"
        );
        assert_eq!(rows[2]["result"]["scope"], "service_decision");
        assert_eq!(rows[2]["result"]["adapter_command_results"], json!([]));
        assert_eq!(
            crate::memory_api::read(&root, crate::memory_api::View::Haiku).unwrap(),
            json!([])
        );
        std::fs::remove_dir_all(root).unwrap();
    }
    #[tokio::test]
    async fn issued_commands_and_validated_receipts_keep_the_adapter_contract() {
        let root = root();
        let d = Dialogue::new(config(&root)).unwrap();
        d.register("s", "試験", false);
        d.set_execution_capabilities("s", &["client.hotbar.select.v1".into()]);
        let mut e = json!(event(1));
        e["player"]["hotbar"] = json!({"selected_slot":8,"slots":[{"slot":0,"item_id":"minecraft:stone_sword","count":1,"damage":0,"max_damage":131,"attack_damage":5,"weapon_kind":"sword"}]});
        e["meta"]["user_text"] = json!("剣に持ち替えて");
        let reply = d.observe("s", crate::events::GameEvent::parse(e).unwrap(), None);
        let command = &reply["commands"][0];
        assert!(command["command_id"].is_string(), "{reply}");
        let mut receipt = json!(event(2));
        receipt["command_results"] = json!([{"command_id":command["command_id"],"command_type":"select_hotbar","status":"succeeded","executed_at":chrono::Utc::now(),"selected_slot":7,"selected_item_id":command["expected_item_id"]}]);
        let response = d.observe("s", crate::events::GameEvent::parse(receipt).unwrap(), None);
        assert_eq!(
            response["acknowledged_command_ids"],
            json!([command["command_id"]])
        );
        assert_eq!(response["commands"], json!([]));
        d.shutdown().await;
        let rows = rows(&root);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["action"]["adapter_commands"], reply["commands"]);
        assert_eq!(rows[0]["result"]["scope"], "service_decision");
        assert!(
            rows[0]["action"]["items"]
                .as_array()
                .unwrap()
                .iter()
                .any(|a| a["text"] == reply["player_input"]["assist"]["feedback"])
        );
        assert_eq!(rows[1]["result"]["scope"], "adapter_execution_observed");
        let observed = &rows[1]["result"]["adapter_command_results"][0];
        assert_eq!(observed["status"], "failed");
        assert_eq!(observed["detail_code"], "result_mismatch");
        // The first reply is still queued; the unchanged queue is not a new selection.
        assert_eq!(rows[1]["action"]["items"], json!([]));
        std::fs::remove_dir_all(root).unwrap();
    }
    #[tokio::test]
    async fn writer_failure_or_disabled_memory_does_not_reject_events() {
        for enabled in [true, false] {
            let root = root();
            std::fs::create_dir_all(&root).unwrap();
            std::fs::write(root.join("eval"), "not a directory").unwrap();
            let mut c = config(&root);
            c.haiku.memory_enabled = enabled;
            let d = Dialogue::new(c).unwrap();
            d.register("s", "試験", false);
            assert_eq!(d.observe("s", event(1), None)["accepted"], true);
            d.shutdown().await;
            assert!(!root.join("eval/episodes.jsonl").exists());
            std::fs::remove_dir_all(root).unwrap();
        }
    }
    #[tokio::test]
    async fn warning_selection_is_not_reported_as_successful_playback() {
        let root = root();
        let d: Arc<Dialogue> = Dialogue::new(config(&root)).unwrap();
        d.register("s", "試験", false);
        let mut e = json!(event(1));
        e["event"]["name"] = json!("threat_approaching");
        e["event"]["source_kind"] = json!("visual");
        e["visual_threats"] = json!([{"type":"creeper","entity_id":"c1","distance":4,"direction":{"horizontal":"front","vertical":"same"},"approaching":true}]);
        e["combat"] =
            json!({"combat_active_hint":true,"hostiles_within_7":1,"hostiles_within_10":1});
        d.observe("s", crate::events::GameEvent::parse(e).unwrap(), None);
        d.shutdown().await;
        let rows = rows(&root);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["state_before"]["mode"], "normal");
        assert_eq!(rows[0]["decision"]["mode_after"], "panic");
        assert_eq!(rows[0]["result"]["scope"], "service_decision");
        assert_eq!(rows[0]["result"]["status"], "actions_selected");
        assert_eq!(
            rows[0]["result"]["output_flags"]["panic_cue_enqueued"],
            true
        );
        std::fs::remove_dir_all(root).unwrap();
    }
}
