//! 生eventの受理・接続分離・モデル材料への反映を実際のDialogue経由で検査する。
use super::*;
use crate::{
    chat_materials::CompletedHistory,
    dialogue::{DialogueConfig, chat_context},
};
use serde_json::{Value, json};
use std::sync::Arc;
const OWNER: &str = "11111111-1111-4111-8111-111111111111";
fn server() -> Arc<Dialogue> {
    let mut c = DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        ..Default::default()
    };
    c.haiku.enabled = false;
    c.haiku.memory_enabled = false;
    let d = Dialogue::new(c).unwrap();
    d.register("s", "呼称", true);
    d.bind_body_owner("s", Some(OWNER));
    d.register("other", "他", true);
    d
}
fn event(sequence: i64, tick: u64, z: f64) -> GameEvent {
    let mut body: Value =
        serde_json::from_str(include_str!("../../../fixtures/body-observation-v3.json")).unwrap();
    body["sample"]["server_tick"] = json!(tick);
    body["sample"]["senses"]["source_server_tick"] = json!(tick);
    body["sample"]["snapshot_revision"] = json!(tick * 2);
    body["sample"]["player_pose"]["position"]["z"] = json!(z);
    GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"body-test","observed_at":chrono::Utc::now(),"sequence":sequence,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20,"yaw":120},
        "world":{"biome":"plains","time_phase":"day","sky_visible":true,"local_light":15,"danger_darkness_score":0},
        "body_observation":body})).unwrap()
}
fn edit(e: GameEvent, change: impl FnOnce(&mut Value)) -> GameEvent {
    let mut v = serde_json::to_value(e).unwrap();
    change(&mut v);
    GameEvent::parse(v).unwrap()
}
fn capture(d: &Dialogue, e: &GameEvent) -> chat_context::Native {
    let data = d.data.lock().unwrap();
    chat_context::capture(
        &data.sessions["s"],
        e,
        &d.config.combat,
        CompletedHistory::default(),
        None,
    )
    .unwrap()
}
fn body(c: &chat_context::Native) -> &Value {
    &c.context.world_context.as_ref().unwrap().observations["body_observation"]
}
#[tokio::test]
async fn body_original_event_updates_once_and_capture_is_immutable() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let a = event(1, 100, 23.0);
    d.observe("s", a.clone(), Some("a"));
    let first = capture(&d, &a);
    let first_diagnostic = d.snapshot(Some("s"))["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["session_id"] == "s")
        .unwrap()["body_observation"]
        .clone();
    assert_eq!(first_diagnostic["current"]["player"]["position"]["z"], 23.0);
    d.observe("other", a.clone(), Some("a"));
    let b = edit(event(2, 110, 28.0), |v| {
        let sample = &mut v["body_observation"]["sample"];
        sample["self_pose"]["yaw"] = json!(90.0);
        sample["self_pose"]["view_yaw"] = json!(90.0);
        sample["player_pose"]["yaw"] = json!(-45.0);
        sample["player_pose"]["pitch"] = json!(-20.0);
        sample["vision"]["player_visibility"] = json!("not_visible");
        sample["vision"]["reason"] = json!("occluded");
    });
    d.observe("s", b.clone(), Some("b"));
    let second = capture(&d, &b);
    let changes = &second.context.world_context.as_ref().unwrap().changes;
    assert_eq!(
        changes.iter().map(|c| c.kind.as_str()).collect::<Vec<_>>(),
        [
            "body_player_position",
            "body_self_orientation",
            "body_player_orientation",
            "body_player_visibility"
        ]
    );
    assert_eq!(changes[0].before["z"], 23.0);
    assert_eq!(changes[0].now["z"], 28.0);
    assert_eq!(changes[3].before["player_visibility"], "visible");
    assert_eq!(changes[3].now["player_visibility"], "not_visible");
    let second_diagnostic = d.snapshot(Some("s"))["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["session_id"] == "s")
        .unwrap()["body_observation"]
        .clone();
    assert_eq!(
        second_diagnostic["current"]["player"]["position"]["z"],
        28.0
    );
    assert_eq!(first_diagnostic["current"]["player"]["position"]["z"], 23.0);
    assert_eq!(
        first_diagnostic["current"]["vision"]["player_visibility"],
        "visible"
    );
    assert_eq!(second_diagnostic["current"], body(&second)["current"]);
    assert_eq!(second_diagnostic["previous"], body(&second)["previous"]);
    assert_eq!(
        body(&second)["current"]["vision"]["player_visibility"],
        "not_visible"
    );
    assert_eq!(
        body(&second)["previous"]["vision"]["player_visibility"],
        "visible"
    );
    assert_eq!(body(&second)["current"]["player"]["position"]["z"], 28.0);
    assert_eq!(body(&second)["previous"]["player"]["position"]["z"], 23.0);
    assert_eq!(body(&first)["current"]["player"]["position"]["z"], 23.0);
    assert_eq!(body(&second)["current"]["self"]["yaw"], 90.0); // player yawは混ぜない。
    assert_eq!(body(&second)["current"]["self"]["view_yaw"], 90.0);
    assert_eq!(body(&second)["current"]["self"]["view_pitch"], 0.0);
    assert_eq!(body(&second)["current"]["player"]["yaw"], -45.0);
    assert_eq!(body(&second)["current"]["player"]["pitch"], -20.0);
    let dup = event(2, 120, 99.0);
    d.observe("s", dup.clone(), Some("dup"));
    assert_eq!(
        body(&capture(&d, &dup))["current"]["player"]["position"]["z"],
        28.0
    );
    let partial = edit(event(3, 110, 88.0), |v| {
        v.as_object_mut().unwrap().remove("body_observation");
    });
    d.observe("s", partial.clone(), Some("partial"));
    assert_eq!(
        body(&capture(&d, &partial))["current"]["player"]["position"]["z"],
        28.0
    );
    assert_eq!(
        context(&d.data.lock().unwrap().sessions["other"])
            .unwrap()
            .observations["body_observation"]["reason"],
        "owner_mismatch"
    );
    assert_eq!(
        second.context.world_context.as_ref().unwrap().observations["observer"],
        "dogido"
    );
    let input = crate::chat_materials::PlayerInput {
        raw_text: "オレはさっきより離れた？".into(),
        semantic_text: "オレはさっきより離れた？".into(),
        ..Default::default()
    };
    let crate::chat_materials::Prepared::Reply(reply) = crate::chat_materials::prepare(
        &b,
        &second.settings,
        &input,
        &second.context,
        &second.snapshot,
        &chat_context::CatalogLabels,
    )
    .unwrap() else {
        panic!("normal reply route");
    };
    let request = reply.request("fixture-model", 256).unwrap();
    let wire = serde_json::to_value(&request).unwrap().to_string();
    assert_eq!(
        request.messages[1]
            .content
            .matches("\"body_observation\"")
            .count(),
        1
    );
    assert!(
        request.messages[0]
            .content
            .contains("body_observation の self はオレ")
    );
    for kind in [
        "body_player_position",
        "body_self_orientation",
        "body_player_orientation",
        "body_player_visibility",
    ] {
        assert!(request.messages[1].content.contains(kind));
    }
    assert!(wire.contains("server_pose"));
    assert!(wire.contains("pose_basis"));
    assert!(wire.contains("body_eye_raycast_fov"));
    assert!(wire.contains("occluded"));
    assert!(wire.contains("view_pitch"));
    assert!(wire.contains("previous"));
    assert!(!wire.contains(OWNER));
    let mut result = json!({"text":"fixture"});
    attach_input(&mut result, &reply.details);
    assert_eq!(result["body_observation_input"], body(&second).clone());
    let later = event(4, 130, 42.0);
    d.observe("s", later.clone(), Some("later"));
    assert_eq!(
        body(&capture(&d, &later))["current"]["player"]["position"]["z"],
        42.0
    );
    assert_eq!(
        result["body_observation_input"]["current"]["player"]["position"]["z"],
        28.0
    );
    assert_eq!(
        result["body_observation_input"]["previous"]["player"]["position"]["z"],
        23.0
    );
    assert_eq!(
        result["body_observation_input"]["current"]["vision"]["reason"],
        "occluded"
    );
    assert_eq!(
        body(&capture(&d, &later))["current"]["vision"]["reason"],
        "in_view"
    );
    assert!(!result.to_string().contains(OWNER));
    drop(permit);
    d.shutdown().await;
}
#[tokio::test]
async fn body_unavailable_vision_is_kept_in_normal_chat_and_diagnostic_without_losing_pose() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let e = edit(event(1, 100, 23.0), |v| {
        v["body_observation"]["sample"]["vision"]["player_visibility"] = json!("unavailable");
        v["body_observation"]["sample"]["vision"]["reason"] = json!("area_unloaded");
    });
    d.observe("s", e.clone(), Some("unavailable-vision"));
    let captured = capture(&d, &e);
    let projected = body(&captured);
    assert_eq!(projected["status"], "observed");
    assert_eq!(projected["current"]["self"]["position"]["z"], 20.0);
    assert_eq!(projected["current"]["player"]["position"]["z"], 23.0);
    assert_eq!(
        projected["current"]["vision"]["player_visibility"],
        "unavailable"
    );
    let snapshot = d.snapshot(Some("s"));
    let diagnostic = snapshot["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["session_id"] == "s")
        .unwrap();
    assert_eq!(
        diagnostic["body_observation"]["current"],
        projected["current"]
    );
    let input = crate::chat_materials::PlayerInput {
        raw_text: "今オレが見える？".into(),
        semantic_text: "今オレが見える？".into(),
        ..Default::default()
    };
    let crate::chat_materials::Prepared::Reply(reply) = crate::chat_materials::prepare(
        &e,
        &captured.settings,
        &input,
        &captured.context,
        &captured.snapshot,
        &chat_context::CatalogLabels,
    )
    .unwrap() else {
        panic!("normal reply route");
    };
    let request = reply.request("fixture-model", 256).unwrap();
    assert!(request.messages[1].content.contains("area_unloaded"));
    assert!(request.messages[1].content.contains("pose_basis"));
    let mut result = json!({"text":"fixture"});
    attach_input(&mut result, &reply.details);
    assert_eq!(result["body_observation_input"], projected.clone());
    assert!(result["body_observation_input"]["current"]["self"]["position"].is_object());
    drop(permit);
    d.shutdown().await;
}
#[test]
fn body_game_event_boundary_rejects_old_version_or_invalid_vision() {
    for (path, value) in [
        ("/body_observation/version", json!(1)),
        ("/body_observation/sample/self_pose/view_yaw", json!(180)),
        ("/body_observation/sample/player_pose/pitch", json!(91)),
        (
            "/body_observation/sample/vision/player_visibility",
            json!("unavailable"),
        ),
        ("/body_observation/sample/vision/reason", json!("occluded")),
        ("/body_observation/sample/vision/range_blocks", json!(0)),
    ] {
        let mut v = serde_json::to_value(event(1, 100, 23.0)).unwrap();
        *v.pointer_mut(path).unwrap() = value;
        assert!(GameEvent::parse(v).is_err(), "{path}");
    }
}
#[tokio::test]
async fn body_expired_transport_and_explicit_absence_cannot_be_revived_by_partial_events() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let a = event(1, 100, 23.0);
    d.observe("s", a.clone(), Some("a"));
    let stale = edit(event(2, 110, 28.0), |v| {
        v["observed_at"] = json!(chrono::Utc::now() - chrono::Duration::seconds(4))
    });
    d.observe("s", stale.clone(), Some("stale"));
    assert_eq!(body(&capture(&d, &stale))["reason"], "stale");
    let partial = edit(event(3, 120, 90.0), |v| {
        v.as_object_mut().unwrap().remove("body_observation");
    });
    d.observe("s", partial.clone(), Some("partial"));
    assert_eq!(body(&capture(&d, &partial))["status"], "unavailable");
    let b = event(4, 130, 29.0);
    d.observe("s", b.clone(), Some("b"));
    assert!(body(&capture(&d, &b))["previous"].is_null());
    let absent =
        edit(
            event(5, 140, 99.0),
            |v| {
                v["body_observation"] =
                    json!({"version":3,"status":"unavailable","reason":"unloaded"})
            },
        );
    d.observe("s", absent.clone(), Some("absent"));
    assert!(body(&capture(&d, &absent)).get("current").is_none());
    drop(permit);
    d.shutdown().await;
}

#[tokio::test]
async fn body_senses_feed_chat_smell_environment_and_haiku_without_player_fallback() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let raw = edit(event(1, 100, 23.0), |v| {
        v["player"]["position"] = json!({"x":80,"y":120,"z":80});
        v["player"]["held_item"] = json!("torch");
        v["inventory"] = json!({"torch":3});
        v["world"]["biome"] = json!("desert");
        v["passive_mobs"] = json!([{"type":"pig"}]);
        v["nearby_resources"] = json!([{"type":"block","name":"sand","distance":1}]);
        v["look_target"] = json!({"kind":"entity","name":"sheep"});
        v["smell_observation"] = json!({"status":"present","components":[{"smell_id":"bread","category":"food","valence":"pleasant","source_kind":"hotbar","effective_strength":3}],"basis":"smell_policy_v2"});
        let senses = &mut v["body_observation"]["sample"]["senses"];
        senses["world"]["biome"] = json!("snowy_taiga");
        senses["passive_mobs"] = json!([{"type":"cow"}]);
        senses["nearby_resources"] = json!([{"type":"block","name":"snow_block","distance":2}]);
        senses["smell_observation"] = json!({"status":"present","components":[{"smell_id":"zombie","category":"decay","valence":"unpleasant","source_kind":"entity","effective_strength":3}],"basis":"smell_policy_v2"});
        senses["auditory_threats"] = json!([{"label":"skeleton","source_id":"one-emission","spoken_name_allowed":true,"heard_ago_ms":100,"direction":{"horizontal":"right"},"distance_band":"close"}]);
    });
    d.observe("s", raw.clone(), None);
    let captured = capture(&d, &raw);
    let sensory = captured.sensory_event.as_ref().unwrap();
    assert_eq!(sensory.observer_y(), Some(64.0));
    assert_eq!(sensory.player.position.y, Some(120.0));
    assert_eq!(sensory.nearby_resources[0].name, "snow_block");
    assert_eq!(captured.snapshot.current.passive_types, ["cow"]);
    assert_eq!(captured.snapshot.current.hearing_types, ["skeleton"]);
    assert!(
        !captured
            .snapshot
            .current
            .passive_types
            .contains(&"sheep".into())
    );
    let world = &captured
        .context
        .world_context
        .as_ref()
        .unwrap()
        .observations;
    assert_eq!(
        world.as_object().unwrap().keys().next().unwrap(),
        "body_observation"
    );
    assert_eq!(world["observer"], "dogido");
    assert_eq!(world["smell"]["components"][0]["smell_id"], "zombie");
    assert!(world.get("vision").is_none());
    assert!(
        world["body_observation"]["current"]["senses"]
            .get("passive_mobs")
            .is_none()
    );
    let first_seen = captured.snapshot.recent.hearing_memos[0].heard_at_us;
    let first_age = sensory.auditory_threats[0].heard_ago_ms.unwrap();
    assert!(first_age >= 125);
    {
        let data = d.data.lock().unwrap();
        let s = &data.sessions["s"];
        assert_eq!(s.latest.as_ref().unwrap().passive_mobs[0].r#type, "pig");
        assert_eq!(
            s.latest.as_ref().unwrap().world.biome.as_deref(),
            Some("desert")
        );
        let reply = super::super::warnings::answer_fixed_query(
            s,
            "この匂い何？",
            "smell",
            0,
            &d.config.combat,
        );
        assert!(reply.text.contains("ゾンビ") && !reply.text.contains("パン"));
        let mut reaction = crate::combat::model::Speech::new("smell", "");
        reaction.delivery = crate::combat::model::Delivery::Ambient;
        crate::environment::reaction::attach(&mut reaction, sensory, &json!({}), &d.config.combat);
        let mut reactions = [reaction.clone()];
        assert!(super::super::environment_runtime::refresh_dialogue_context(
            s,
            &mut reactions
        ));
        assert_eq!(
            reactions[0].leaf.as_ref().unwrap().details["reaction_context"]["world_context"]["observations"]
                ["smell"]["components"][0]["smell_id"],
            "zombie"
        );
        assert!(crate::environment::reaction::still_applicable(
            &reaction, sensory
        ));
        let world_catalog = crate::world_catalog::catalog();
        let materials = crate::haiku::materials::capture(
            sensory,
            crate::haiku::materials::RuntimeRead {
                current_structure: None,
                player_name: "試験",
                inventory_order: sensory.inventory_order(),
            },
            world_catalog,
            crate::chat_catalog::catalog(),
            world_catalog,
            &Default::default(),
        )
        .unwrap();
        assert_eq!(materials.biome_id, "snowy_taiga");
        assert!(
            materials
                .nearby_blocks
                .iter()
                .any(|label| label.contains("雪"))
        );
        assert!(
            materials
                .passive_mobs
                .iter()
                .any(|label| label.contains("ウシ"))
        );
        let mob = materials
            .feature_candidates
            .iter()
            .position(|row| row.key == "mob_1")
            .unwrap();
        let inventory = materials
            .feature_candidates
            .iter()
            .position(|row| row.key == "held_item")
            .unwrap();
        assert!(mob < inventory);
    }
    // 身体の同じrevisionを含む別eventは、現在材料を読めても記憶の時刻を更新しない。
    let repeat = edit(raw.clone(), |v| {
        v["sequence"] = json!(2);
        v["observed_at"] = json!(chrono::Utc::now());
    });
    d.observe("s", repeat.clone(), None);
    let repeated = capture(&d, &repeat);
    assert_eq!(
        repeated.snapshot.recent.hearing_memos[0].heard_at_us,
        first_seen
    );
    assert!(
        repeated.sensory_event.as_ref().unwrap().auditory_threats[0]
            .heard_ago_ms
            .unwrap()
            >= first_age
    );
    // 位置・所有者が一致していても、新しい身体世代へ前の感覚記憶を持ち込まない。
    let epoch = edit(event(3, 110, 23.0), |v| {
        v["body_observation"]["sample"]["context"]["epoch"] =
            json!("44444444-4444-4444-8444-444444444444");
    });
    d.observe("s", epoch.clone(), None);
    let changed = capture(&d, &epoch);
    assert!(changed.snapshot.recent.hearing_memos.is_empty());
    assert!(changed.snapshot.recent.passive_sightings.is_empty());
    // 身体が未取得の間もプレイヤーの持ち物と指差しは保持し、自己感覚へ転用しない。
    let unavailable = edit(raw.clone(), |v| {
        v["sequence"] = json!(4);
        v["observed_at"] = json!(chrono::Utc::now());
        v["body_observation"] = json!({"version":3,"status":"unavailable","reason":"unloaded"});
    });
    d.observe("s", unavailable.clone(), None);
    let empty = capture(&d, &unavailable);
    let empty_event = empty.sensory_event.as_ref().unwrap();
    assert!(empty_event.passive_mobs.is_empty() && empty_event.nearby_resources.is_empty());
    assert!(empty_event.world.biome.is_none() && empty_event.smell_observation.is_none());
    assert!(empty.snapshot.recent.hearing_memos.is_empty());
    assert_eq!(empty_event.inventory.get("torch"), Some(&3));
    assert_eq!(empty_event.look_target.as_ref().unwrap().name, "sheep");
    assert_eq!(
        empty.context.world_context.as_ref().unwrap().observations["sensory_status"],
        "unavailable"
    );
    {
        let data = d.data.lock().unwrap();
        let reply = super::super::warnings::answer_fixed_query(
            &data.sessions["s"],
            "この匂い何？",
            "smell",
            0,
            &d.config.combat,
        );
        assert_eq!(reply.cue_id, Some("smell_unsupported"));
    }
    drop(permit);
    d.shutdown().await;
}

#[tokio::test]
async fn old_reply_ack_cannot_consume_new_body_generation_changes() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let a = event(1, 100, 23.0);
    d.observe("s", a.clone(), None);
    let b = event(2, 110, 24.0);
    d.observe("s", b.clone(), None);
    let offered = capture(&d, &b).context.world_context.unwrap();
    assert_eq!(offered.changes.len(), 1);
    let generation = |e: GameEvent| {
        edit(e, |v| {
            v["body_observation"]["sample"]["context"]["epoch"] =
                json!("44444444-4444-4444-8444-444444444444");
        })
    };
    let c = generation(event(3, 120, 99.0));
    d.observe("s", c.clone(), None);
    assert!(
        capture(&d, &c)
            .context
            .world_context
            .unwrap()
            .changes
            .is_empty()
    );
    let e = generation(event(4, 130, 98.0));
    d.observe("s", e.clone(), None);
    d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .conversation_observation
        .considered(offered.revision);
    let current = capture(&d, &e).context.world_context.unwrap();
    assert!(current.revision > offered.revision);
    assert_eq!(current.changes.len(), 1);
    assert_eq!(current.changes[0].before["z"], 99.0);
    assert_eq!(current.changes[0].now["z"], 98.0);
    drop(permit);
    d.shutdown().await;
}
