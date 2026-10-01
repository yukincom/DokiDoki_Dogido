use super::*;
use crate::dialogue::{Dialogue, DialogueConfig};
use crate::playback::Status as PlaybackStatus;
use serde_json::json;
use std::sync::Arc;

fn server(overrides: Value) -> Arc<Dialogue> {
    let mut config = DialogueConfig {
        audio_enabled: false,
        ..Default::default()
    };
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    config.combat = crate::combat::model::Settings::merged(overrides.as_object().unwrap()).unwrap();
    let d = Dialogue::new(config).unwrap();
    d.register("s", "登録名", true);
    d
}
fn event(at: chrono::DateTime<chrono::Utc>, seq: i64, name: &str, fields: Value) -> GameEvent {
    let mut value = json!({"schema_version":"2026-05-24","adapter":"session-test","observed_at":at,"sequence":seq,
        "event":{"name":name,"source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"name":"ゲーム名","dimension":"minecraft:overworld","health":20},
        "world":{"biome":"plains","time_phase":"day","sky_visible":true,"local_light":15,"danger_darkness_score":0.0},
        "combat":{"hostile_outcomes":[]}});
    for (key, field) in fields.as_object().unwrap() {
        if let (Some(target), Some(add)) = (value[key].as_object_mut(), field.as_object()) {
            target.extend(add.clone());
        } else {
            value[key] = field.clone();
        }
    }
    GameEvent::parse(value).unwrap()
}
fn snapshot(d: &Dialogue, e: &GameEvent) -> Snapshot {
    d.data.lock().unwrap().sessions["s"]
        .chat_observation
        .snapshot(e, &CatalogLabels)
        .unwrap()
}
fn kill(id: &str, mob: &str) -> Value {
    json!({"entity_id":id,"type":mob,"outcome":"player_kill","evidence":"server_death_event"})
}
#[test]
fn original_inventory_order_is_private_validated_and_clone_stable() {
    let mut v: Value = serde_json::from_str(r#"{"schema_version":"2026-05-24","adapter":"test","observed_at":"2026-09-29T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"inventory":{"z:item":1,"a:item":2,"m:item":"3"}}"#).unwrap();
    let e = GameEvent::parse(v.clone()).unwrap();
    assert_eq!(e.inventory_order(), ["z:item", "a:item", "m:item"]);
    assert_eq!(e.clone().inventory_order(), e.inventory_order());
    let serialized = json!(e);
    assert!(serialized.get("inventory_order").is_none());
    assert_eq!(
        serialized["inventory"],
        json!({"z:item":1,"a:item":2,"m:item":3})
    );
    let deserialized: GameEvent = serde_json::from_str(&v.to_string()).unwrap();
    assert_eq!(deserialized.inventory_order(), e.inventory_order());
    v["inventory"]["a:item"] = json!([]);
    assert!(GameEvent::parse(v).is_err());
}
#[test]
fn actual_labels_match_canonical_complete_dictionary() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../fixtures/chat-observation-labels.json"
    ))
    .unwrap();
    for (id, expected) in fixture["mobs"].as_object().unwrap() {
        assert_eq!(CatalogLabels.mob_label(id), expected.as_str(), "mob {id}");
    }
    for (id, expected) in fixture["mob_fallback"].as_object().unwrap() {
        assert_eq!(
            CatalogLabels.mob_fallback_label(id),
            expected.as_str(),
            "fallback {id}"
        );
    }
    for (id, expected) in fixture["hostiles"].as_object().unwrap() {
        assert_eq!(
            CatalogLabels.hostile_label(id),
            expected.as_str().unwrap(),
            "hostile {id}"
        );
    }
    for (id, expected) in fixture["blocks"].as_object().unwrap() {
        assert_eq!(
            CatalogLabels.block_label(id),
            expected.as_str(),
            "block {id}"
        );
    }
    assert_eq!(
        CatalogLabels.hostile_label("minecraft:zombie"),
        "minecraft:zombie"
    );
    assert_eq!(CatalogLabels.mob_fallback_label("minecraft:zombie"), None);
    assert_eq!(CatalogLabels.mob_label("minecraft:zombie"), Some("ゾンビ"));
    assert_eq!(
        CatalogLabels.block_label("custom:stone"),
        CatalogLabels.block_label("stone")
    );
    assert_eq!(CatalogLabels.mob_label("unknown"), None);
}
#[tokio::test]
async fn admission_partial_memory_and_haiku_state_have_separate_update_boundaries() {
    let d = server(
        json!({"player_chat_visual_retention_ms":500,"player_chat_hearing_retention_ms":1000,"default_call_name":"設定名"}),
    );
    let permit = d.serial.acquire().await.unwrap(); // No helper/model/audio may start.
    let at = chrono::Utc::now() - chrono::Duration::seconds(4);
    let full = event(
        at,
        1,
        "status_snapshot",
        json!({"world":{"structure":"custom:VILLAGE"},"meta":{"call_name":" 呼称 "},
        "inventory":{"z:item":1,"a:item":2},"visual_threats":[{"type":"zombie","entity_id":"z","distance":4}]}),
    );
    d.observe("s", full.clone(), Some("full"));
    let original = {
        let data = d.data.lock().unwrap();
        let s = &data.sessions["s"];
        assert_eq!(
            s.haiku_context.current_structure.as_deref(),
            Some("village")
        );
        assert_eq!(s.haiku_context.player_name, "呼称");
        assert_eq!(s.haiku_context.inventory_order, ["z:item", "a:item"]);
        s.haiku_context.clone()
    };
    let sound = event(
        at + chrono::Duration::milliseconds(300),
        2,
        "hostile_audio_detected",
        json!({"event":{"source_kind":"auditory"},
        "world":{"structure":"stronghold"},"inventory":{"other":9},
        "auditory_threats":[{"label":"skeleton","source_id":"s","spoken_name_allowed":true,"direction":{"horizontal":"right"},"distance_band":"close"}]}),
    );
    d.observe("s", sound.clone(), Some("sound"));
    let got = snapshot(&d, &sound);
    assert!(got.current.visual_types.is_empty());
    assert_eq!(got.recent.visual_types, ["zombie"]);
    assert_eq!(got.recent.hearing_memos.len(), 1);
    assert_eq!(got.recent.visual_memos[0].seen_at_us, at.timestamp_micros());
    assert_eq!(
        json!(d.data.lock().unwrap().sessions["s"].haiku_context),
        json!(original)
    );
    let later = event(
        at + chrono::Duration::milliseconds(700),
        3,
        "status_snapshot",
        json!({}),
    );
    d.observe("s", later.clone(), Some("later"));
    assert!(snapshot(&d, &later).recent.visual_types.is_empty());
    // Same delivery or old sequence carrying a new visual cannot renew memory/context.
    let poisoned = event(
        at + chrono::Duration::milliseconds(800),
        2,
        "status_snapshot",
        json!({"world":{"structure":"stronghold"},"visual_threats":[{"type":"creeper","distance":3}]}),
    );
    assert_eq!(
        d.observe("s", poisoned.clone(), Some("new-key"))["deduplicated"],
        true
    );
    let dup = event(
        at + chrono::Duration::milliseconds(900),
        4,
        "status_snapshot",
        json!({"world":{"structure":"stronghold"}}),
    );
    assert_eq!(d.observe("s", dup, Some("later"))["deduplicated"], true);
    let got = snapshot(&d, &later);
    assert!(got.recent.visual_types.is_empty());
    assert_eq!(got.hearing.types, ["skeleton"]);
    assert!(
        d.data.lock().unwrap().sessions["s"]
            .haiku_context
            .current_structure
            .is_none()
    );
    d.shutdown().await;
    drop(permit);
}
#[tokio::test]
async fn engine_confirmed_outcome_is_consumed_once_without_replay_ttl_extension() {
    let d = server(json!({"player_chat_name_correction_retention_ms":500}));
    let permit = d.serial.acquire().await.unwrap();
    let at = chrono::Utc::now() - chrono::Duration::seconds(4);
    let first = event(
        at,
        1,
        "hostile_defeated",
        json!({"combat":{"hostile_outcomes":[kill("z","zombie")]}}),
    );
    d.observe("s", first.clone(), None);
    assert_eq!(snapshot(&d, &first).name_context.types, ["zombie"]);
    assert!(
        d.data
            .lock()
            .unwrap()
            .sessions
            .get_mut("s")
            .unwrap()
            .combat
            .take_name_updates()
            .confirmed_types
            .is_empty()
    );
    let end = event(
        at + chrono::Duration::milliseconds(100),
        2,
        "combat_ended",
        json!({"combat":{"hostile_outcomes":[kill("s","skeleton")]}}),
    );
    d.observe("s", end.clone(), None);
    assert_eq!(
        snapshot(&d, &end).name_context.types,
        ["zombie", "skeleton"]
    );
    let repeat = event(
        at + chrono::Duration::milliseconds(400),
        3,
        "hostile_defeated",
        json!({"combat":{"hostile_outcomes":[kill("z","zombie")]}}),
    );
    d.observe("s", repeat, None);
    let check = event(
        at + chrono::Duration::milliseconds(550),
        4,
        "status_snapshot",
        json!({}),
    );
    d.observe("s", check.clone(), None);
    let snap = snapshot(&d, &check);
    assert_eq!(snap.name_context.types, ["skeleton"]);
    assert!(snap.current.visual_types.is_empty());
    d.close("s");
    assert_eq!(
        d.observe("s", check.clone(), None)["reason"],
        "unknown_session_id"
    );
    d.register("s", "new", true);
    assert!(snapshot(&d, &check).name_context.types.is_empty());
    // Fresh session owns a fresh combat ledger; the same UUID is no longer a replay there.
    let again = event(
        at + chrono::Duration::milliseconds(700),
        1,
        "hostile_defeated",
        json!({"combat":{"hostile_outcomes":[kill("z","zombie")]}}),
    );
    d.observe("s", again.clone(), None);
    assert_eq!(snapshot(&d, &again).name_context.types, ["zombie"]);
    d.shutdown().await;
    drop(permit);
}
#[tokio::test]
async fn capture_freezes_history_repair_digest_and_survives_epoch_changes_without_writes() {
    let d = server(json!({}));
    let permit = d.serial.acquire().await.unwrap();
    let e = event(chrono::Utc::now(), 1, "status_snapshot", json!({}));
    d.observe("s", e.clone(), None);
    let native = {
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        s.history.push("old", "user", "ヤギがいるよ");
        s.history.push("old", "assistant", "せやったな。");
        s.history.push("repair", "user", "違うよ");
        s.history.annotate(
            "repair",
            &crate::planner::repair::Repair {
                action: crate::planner::Action::ClarifyRepair,
                target_turn_id: "old".into(),
                target_quote: "ヤギ".into(),
                signal_quote: "違う".into(),
                replacement_quote: String::new(),
                current_text: "違うよ".into(),
            },
        );
        s.history.push("repair", "assistant", "どんな意味やった？");
        let history = CompletedHistory {
            conversation_history: s.history.lines(),
            conversation_turns: s.history.rows(),
            event_digest: "- 戻った話題".into(),
        };
        capture(s, &e, &d.config.combat, history, None).unwrap()
    };
    let frozen = json!(native);
    d.interrupt("s");
    {
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        s.epoch += 1;
        s.history.push("new", "user", "別の話");
    }
    assert_eq!(json!(native), frozen);
    assert_eq!(frozen["context"]["history"]["event_digest"], "- 戻った話題");
    assert_eq!(
        frozen["context"]["history"]["conversation_turns"]
            .as_array()
            .unwrap()
            .len(),
        4
    );
    let pending = crate::planner::repair::pending(&native.context.history.conversation_turns);
    assert_eq!(pending["repair_action"], "clarify_repair");
    assert_eq!(pending["repair_target_quote"], "ヤギ");
    assert!(
        native
            .context
            .history
            .conversation_history
            .contains("会話の修復待ち")
    );
    let _: Native = serde_json::from_value(frozen).unwrap();
    d.shutdown().await;
    drop(permit);
}

#[tokio::test]
async fn non_status_structure_player_name_and_time_domain_boundaries_are_explicit() {
    let d = server(json!({"default_call_name":"  "}));
    let permit = d.serial.acquire().await.unwrap();
    let at = chrono::Utc::now() - chrono::Duration::seconds(2);
    let full = event(
        at,
        1,
        "status_snapshot",
        json!({"world":{"structure":"custom:VILLAGE"},"inventory":{"first":1}}),
    );
    d.observe("s", full.clone(), None);
    let non_status = event(
        at + chrono::Duration::milliseconds(100),
        2,
        "combat_ended",
        json!({"world":{"structure":"stronghold"},"inventory":{"second":2}}),
    );
    d.observe("s", non_status.clone(), None);
    {
        let data = d.data.lock().unwrap();
        let s = &data.sessions["s"];
        assert_eq!(
            s.haiku_context.current_structure.as_deref(),
            Some("village")
        );
        assert_eq!(s.haiku_context.inventory_order, ["second"]);
        assert_eq!(s.haiku_context.player_name, "ゲーム名");
    }
    // Naive timestamps cannot be treated as UTC or mix into aware memory.
    let before = json!(snapshot(&d, &non_status));
    let mut naive = json!(non_status);
    naive["sequence"] = 3.into();
    naive["observed_at"] = "2026-09-29T00:00:00".into();
    naive["passive_mobs"] = json!([{"type":"cat"}]);
    assert_eq!(
        d.observe("s", GameEvent::parse(naive).unwrap(), None)["accepted"],
        true
    );
    assert_eq!(json!(snapshot(&d, &non_status)), before);
    let changed = event(
        at + chrono::Duration::milliseconds(200),
        4,
        "status_snapshot",
        json!({"player":{"dimension":"minecraft:the_nether","name":" "}}),
    );
    d.observe("s", changed.clone(), None);
    {
        let data = d.data.lock().unwrap();
        let s = &data.sessions["s"];
        assert!(s.haiku_context.current_structure.is_none());
        assert_eq!(s.haiku_context.player_name, "プレイヤー");
        assert!(s.haiku_context.inventory_order.is_empty());
    }
    // An old generation result cannot reinsert observations or rename the snapshot.
    let context = json!(d.data.lock().unwrap().sessions["s"].haiku_context);
    assert!(!d.update(
        "s",
        "unknown",
        u64::MAX,
        PlaybackStatus::Completed,
        Some(&json!({"text":"猫がいる"}))
    ));
    assert_eq!(
        json!(d.data.lock().unwrap().sessions["s"].haiku_context),
        context
    );
    assert!(
        !snapshot(&d, &changed)
            .name_context
            .types
            .contains(&"cat".into())
    );
    d.shutdown().await;
    drop(permit);
}

#[test]
fn workshop_speech_fields_match_canonical_projection_and_pending_reading() {
    let mut count = 0;
    for row in include_str!("../../../fixtures/session-context-workshop.jsonl").lines() {
        let row: Value = serde_json::from_str(row).unwrap();
        let view = row["view"].clone();
        let fields = workshop_details(&view).unwrap();
        assert_eq!(
            json!(fields),
            row["expected"],
            "workshop fixture {count}: {view}"
        );
        assert_eq!(view, row["view"]);
        count += 1;
    }
    assert_eq!(count, 136);
    assert!(workshop_details(&json!({})).is_err());
}
