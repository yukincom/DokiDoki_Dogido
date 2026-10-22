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
#[test]
fn haiku_candidate_limit_keeps_visible_mobs_and_place_ahead_of_inventory() {
    let d = server();
    for (index, sky_visible) in [true, false].into_iter().enumerate() {
        let raw = edit(event(index as i64 + 1, 100 + index as u64, 23.0), |v| {
            v["player"]["held_item"] = json!("birch_door");
            v["inventory"] = json!({"birch_door": 1, "cobblestone": 64, "stick": 64, "bone": 32});
            let senses = &mut v["body_observation"]["sample"]["senses"];
            senses["world"] = json!({"biome":"plains", "sky_visible":sky_visible, "weather":"clear", "time_phase":"day"});
            senses["nearby_resources"] = json!(["oak_log", "stone", "sand", "grass_block", "dirt", "cherry_leaves"]
                .iter().enumerate().map(|(i, name)| json!({"type":"block", "name":name, "distance":i + 1})).collect::<Vec<_>>());
            senses["dropped_items"] = json!(["apple", "bread", "feather", "string"]
                .iter().enumerate().map(|(i, name)| json!({"name":name, "count":1, "distance":i + 1})).collect::<Vec<_>>());
            senses["passive_mobs"] = json!(["cow", "sheep", "pig"]
                .iter().map(|kind| json!({"type":kind, "distance":2})).collect::<Vec<_>>());
        });
        let sensory = {
            let mut data = d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            super::observe(s, &raw);
            super::event(s, &raw)
        };
        let world = crate::world_catalog::catalog();
        let context = crate::haiku::materials::capture(
            &sensory,
            crate::haiku::materials::RuntimeRead {
                current_structure: None, player_name: "試験", inventory_order: sensory.inventory_order(),
            },
            world, crate::chat_catalog::catalog(), world, &Default::default(),
        ).unwrap();
        let keys: Vec<_> = context.feature_candidates.iter().map(|row| row.key.as_str()).collect();
        assert_eq!(keys.len(), 14);
        for key in ["nearby_1", "nearby_6", "mob_1", "mob_2", "mob_3"] {
            assert!(keys.contains(&key), "{key}: {keys:?}");
        }
        assert_eq!(keys.contains(&"biome"), sky_visible);
        assert_eq!(keys.contains(&"biome_group"), sky_visible);
        // 候補欄で押し出されても、持ち物そのものと出典は消さない。
        assert!(!context.held_item.is_empty() && !context.inventory_items.is_empty());
        assert!(context.source_atoms.iter().any(|atom| atom.observation_role == "selected_item"));
    }
}
#[tokio::test]
async fn body_comparison_stays_internal_while_model_context_contains_no_raw_pose() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let a = event(1, 100, 23.0);
    d.observe("s", a.clone(), Some("a"));
    let first = capture(&d, &a);
    let b = edit(event(2, 110, 28.0), |v| {
        let sample = &mut v["body_observation"]["sample"];
        sample["self_pose"]["yaw"] = json!(90.0);
        sample["player_pose"]["yaw"] = json!(-45.0);
        sample["vision"]["player_visibility"] = json!("not_visible");
        sample["vision"]["reason"] = json!("occluded");
    });
    d.observe("s", b.clone(), Some("b"));
    let second = capture(&d, &b);
    let context = second.context.world_context.as_ref().unwrap();
    assert_eq!(context.changes.len(), 5);
    assert_eq!(context.changes[4].kind, "body_position_relation");
    assert_eq!(context.changes[4].before["distance_blocks"], 3.0);
    assert_eq!(context.changes[4].now["distance_blocks"], 8.0);
    assert_eq!(body(&second)["current"]["position_relation"], context.changes[4].now);
    for change in &context.changes[..3] {
        assert!(change.before.is_null());
        assert_eq!(change.now, json!({"changed":true}));
    }
    assert_eq!(context.changes[3].now["player_visibility"], "not_visible");
    {
        let data = d.data.lock().unwrap();
        let internal = data.sessions["s"]
            .conversation_observation
            .context()
            .unwrap();
        assert_eq!(internal.changes[0].before["z"], 23.0);
        assert_eq!(internal.changes[0].now["z"], 28.0);
        let raw = data.sessions["s"]
            .body_observation
            .project(Instant::now())
            .unwrap();
        assert_eq!(raw["current"]["player"]["position"]["z"], 28.0);
        assert_eq!(raw["previous"]["player"]["position"]["z"], 23.0);
    }
    assert_eq!(
        body(&first)["current"]["vision"]["player_visibility"],
        "visible"
    );
    assert_eq!(
        body(&second)["current"]["vision"]["player_visibility"],
        "not_visible"
    );
    for field in ["self", "player"] {
        assert!(body(&second)["current"].get(field).is_none());
    }
    for field in [
        "previous",
        "coordinate_system",
        "sample_interval_ticks",
        "pose_basis",
    ] {
        assert!(body(&second).get(field).is_none());
    }
    let input = crate::chat_materials::PlayerInput {
        raw_text: "今どこ？".into(),
        semantic_text: "今どこ？".into(),
        ..Default::default()
    };
    let crate::chat_materials::Prepared::Reply(reply) = crate::chat_materials::prepare(
        second.sensory_event.as_ref().unwrap(),
        &second.settings,
        &input,
        &second.context,
        &second.snapshot,
        &chat_context::CatalogLabels,
    )
    .unwrap() else {
        panic!("normal reply route")
    };
    let request = reply.request("fixture-model", 256).unwrap();
    let wire = request.messages[1].content.clone();
    assert!(wire.contains("body_player_position") && wire.contains("occluded"));
    for field in [
        "coordinate_system",
        "view_yaw",
        "view_pitch",
        "eye_position",
        OWNER,
    ] {
        assert!(!wire.contains(field), "{field}");
    }
    let mut result = json!({"text":"fixture"});
    attach_input(&mut result, &reply.details);
    let later = event(3, 120, 42.0);
    d.observe("s", later.clone(), Some("later"));
    assert_eq!(result["body_observation_input"], body(&second).clone());
    assert_eq!(
        body(&capture(&d, &later))["current"]["vision"]["reason"],
        "in_view"
    );
    drop(permit);
    d.shutdown().await;
}

#[tokio::test]
async fn body_unavailable_vision_keeps_raw_pose_only_in_diagnostic() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let e = edit(event(1, 100, 23.0), |v| {
        v["body_observation"]["sample"]["vision"]["player_visibility"] = json!("unavailable");
        v["body_observation"]["sample"]["vision"]["reason"] = json!("area_unloaded");
    });
    d.observe("s", e.clone(), None);
    let captured = capture(&d, &e);
    let projected = body(&captured);
    assert_eq!(projected["status"], "observed");
    assert_eq!(
        projected["current"]["vision"]["player_visibility"],
        "unavailable"
    );
    assert!(projected["current"].get("self").is_none());
    let snapshot = d.snapshot(Some("s"));
    let diagnostic = snapshot["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["session_id"] == "s")
        .unwrap();
    assert_eq!(
        diagnostic["body_observation"]["current"]["player"]["position"]["z"],
        23.0
    );
    assert_eq!(
        diagnostic["body_observation"]["current"]["vision"],
        projected["current"]["vision"]
    );
    let probe = &diagnostic["body_probe"];
    assert_eq!(probe["context"]["owner_id"], OWNER);
    assert_eq!(probe["server_tick"], 100);
    assert_eq!(probe["snapshot_revision"], 200);
    assert_eq!(probe["player_pose"]["position"]["z"], 23.0);
    assert_eq!(probe["vision"]["player_visibility"], "unavailable");
    assert!(captured.context.world_context.as_ref().unwrap().observations.get("body_probe").is_none());
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
    assert_eq!(offered.changes.len(), 2);
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
    assert_eq!(current.changes.len(), 2);
    assert_eq!(current.changes[1].kind, "body_position_relation");
    assert_eq!(current.changes[1].before["distance_blocks"], 79.0);
    assert_eq!(current.changes[1].now["distance_blocks"], 78.0);
    assert!(current.changes[0].before.is_null());
    assert_eq!(current.changes[0].now, json!({"changed":true}));
    {
        let data = d.data.lock().unwrap();
        let internal = data.sessions["s"]
            .conversation_observation
            .context()
            .unwrap();
        assert_eq!(internal.changes[0].before["z"], 99.0);
        assert_eq!(internal.changes[0].now["z"], 98.0);
    }
    drop(permit);
    d.shutdown().await;
}

/// 現在値は差分の消費後も残り、未提示の新しい差分を古い判断で消さない。
#[tokio::test]
async fn position_current_survives_ack_and_changes_are_latest_only() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let a = event(1, 100, 23.0);
    d.observe("s", a.clone(), None);
    let first = capture(&d, &a).context.world_context.unwrap();
    assert!(first.changes.is_empty());
    let current = &first.observations["body_observation"]["current"]["position_relation"];
    assert_eq!(current["distance_blocks"], 3.0);
    assert_eq!(current["relative_positions"][0]["horizontal"], "front");
    assert_eq!(current["relative_positions"][1]["horizontal"], "right");
    let b = event(2, 110, 26.0);
    d.observe("s", b.clone(), None);
    let offered = capture(&d, &b).context.world_context.unwrap();
    let c = event(3, 120, 29.0);
    d.observe("s", c.clone(), None);
    {
        let mut data = d.data.lock().unwrap();
        data.sessions.get_mut("s").unwrap().conversation_observation.considered(offered.revision);
    }
    let latest = capture(&d, &c).context.world_context.unwrap();
    let relation_changes: Vec<_> = latest.changes.iter().filter(|c| c.kind == "body_position_relation").collect();
    assert_eq!(relation_changes.len(), 1);
    assert_eq!(relation_changes[0].before["distance_blocks"], 6.0);
    assert_eq!(relation_changes[0].now["distance_blocks"], 9.0);
    {
        let mut data = d.data.lock().unwrap();
        data.sessions.get_mut("s").unwrap().conversation_observation.considered(latest.revision);
    }
    let repeated = capture(&d, &c).context.world_context.unwrap();
    assert!(repeated.changes.is_empty());
    assert_eq!(repeated.observations, latest.observations);
    let stationary = event(4, 130, 29.0);
    d.observe("s", stationary.clone(), None);
    assert!(capture(&d, &stationary).context.world_context.unwrap().changes.is_empty());
    // 表示する方向と0.1ブロック単位の距離が同じなら、位置関係の差分は作らない。
    let small_move = event(5, 140, 29.01);
    d.observe("s", small_move.clone(), None);
    assert!(!capture(&d, &small_move).context.world_context.unwrap().changes.iter().any(|c| c.kind == "body_position_relation"));
    let rotated = edit(event(6, 150, 29.01), |v| {
        v["body_observation"]["sample"]["player_pose"]["yaw"] = json!(0.0);
    });
    d.observe("s", rotated.clone(), None);
    let rotated = capture(&d, &rotated).context.world_context.unwrap();
    let change = rotated.changes.iter().find(|c| c.kind == "body_position_relation").unwrap();
    assert_eq!(change.now["relative_positions"][0]["horizontal"], "front");
    assert_eq!(change.before["relative_positions"][1]["horizontal"], "right");
    assert_eq!(change.now["relative_positions"][1]["horizontal"], "back");
    drop(permit);
    d.shutdown().await;
}

#[tokio::test]
async fn paused_and_invalidated_body_do_not_expose_last_position_or_changes() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let a = event(1, 100, 23.0);
    d.observe("s", a.clone(), None);
    let b = event(2, 110, 26.0);
    d.observe("s", b.clone(), None);
    {
        let mut data = d.data.lock().unwrap();
        data.sessions.get_mut("s").unwrap().foreground.game_paused = true;
    }
    let paused = capture(&d, &b).context.world_context.unwrap();
    assert_eq!(paused.observations["body_observation"]["reason"], "game_paused");
    assert!(paused.changes.is_empty());
    assert!(paused.observations["body_observation"].get("current").is_none());
    {
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        s.foreground.game_paused = false;
        s.body_observation.observe(a.body_observation.as_ref(), Some(OWNER),
            Some("minecraft:the_nether"), Instant::now(), Some(0));
    }
    let expired = capture(&d, &b).context.world_context.unwrap();
    assert_eq!(expired.observations["body_observation"]["status"], "unavailable");
    assert!(expired.changes.is_empty());
    assert!(expired.observations["body_observation"].get("current").is_none());
    drop(permit);
    d.shutdown().await;
}

#[tokio::test]
async fn notice_refreshes_body_but_preserves_periodic_table_and_ack_revision() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let a = event(1, 100, 23.0);
    d.observe("s", a.clone(), None);
    let b = event(2, 110, 24.0);
    d.observe("s", b.clone(), None);
    let offered = capture(&d, &b).context.world_context.unwrap();
    let periodic = crate::conversation_observation::Context {
        observations: json!({"nearby_mob_types":["cow"],"body_observation":offered.observations["body_observation"]}),
        changes: vec![crate::conversation_observation::Change {
            kind: "nearby_mob_types".into(), before: json!([]), now: json!(["cow"]),
            observed_at: "fixed_periodic_time".into(), revision: offered.revision,
        }],
        revision: offered.revision,
        ..Default::default()
    };
    let mut action = crate::environment::notice::action();
    action.leaf.as_mut().unwrap().details["reaction_context"] = json!({"world_context":periodic});
    let c = event(3, 120, 29.0);
    d.observe("s", c.clone(), None);
    {
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        assert!(super::super::environment_runtime::refresh_dialogue_context(s, std::slice::from_mut(&mut action)));
        let refreshed = &action.leaf.as_ref().unwrap().details["reaction_context"]["world_context"];
        assert_eq!(refreshed["observations"]["body_observation"]["current"]["position_relation"]["distance_blocks"], 9.0);
        assert_eq!(refreshed["observations"]["nearby_mob_types"], json!(["cow"]));
        assert_eq!(refreshed["changes"], json!(periodic.changes));
        assert_eq!(refreshed["revision"], periodic.revision);
        s.conversation_observation.considered(periodic.revision);
        let remaining = s.conversation_observation.context().unwrap();
        assert!(remaining.changes.iter().any(|c| c.kind == "body_position_relation" && c.now["distance_blocks"] == 9.0));
        s.foreground.game_paused = true;
        assert!(super::super::environment_runtime::refresh_dialogue_context(s, std::slice::from_mut(&mut action)));
        let paused = &action.leaf.as_ref().unwrap().details["reaction_context"]["world_context"];
        assert_eq!(paused["observations"]["body_observation"]["status"], "unavailable");
        assert_eq!(paused["revision"], periodic.revision);
    }
    drop(permit);
    d.shutdown().await;
}
