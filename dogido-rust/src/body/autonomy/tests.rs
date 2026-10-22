//! 実モデル・ゲーム・時刻待ちを使わず、受理根拠・対象固定・再送と終端の境界を検査する。
use super::{contracts::*, *};
use crate::body::contracts::{Position, Sample};
use crate::events::{
    AdapterCommandResult, AdapterCommandResultCommandType, AdapterCommandResultStatus, EventTime,
};
use chrono::{DateTime, Duration as UtcDuration, Utc};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    time::{Duration, Instant},
};

const ENTITY: &str = "22222222-2222-4222-8222-222222222222";
const OTHER: &str = "99999999-9999-4999-8999-999999999999";
fn utc() -> DateTime<Utc> {
    "2026-10-14T00:00:00Z".parse().unwrap()
}
fn sample(tick: u64) -> Sample {
    let mut value = crate::body::tests::fixture()["sample"].clone();
    value["server_tick"] = json!(tick);
    value["snapshot_revision"] = json!(tick * 2);
    value["player_pose"]["position"] = json!({"x":0,"y":64,"z":0});
    serde_json::from_value(value).unwrap()
}
fn entity() -> Candidate {
    Candidate {
        id: format!("e:{ENTITY}"),
        kind: CandidateKind::Entity,
        type_id: "minecraft:cow".into(),
        label: "ウシ".into(),
        position: Position {
            x: 2.0,
            y: 64.0,
            z: 0.0,
        },
        entity_id: Some(ENTITY.into()),
    }
}
fn observation(s: &Sample) -> Observation {
    Observation::Observed(Box::new(Observed {
        version: 2,
        context: s.context.clone(),
        server_tick: s.server_tick,
        snapshot_revision: s.snapshot_revision,
        age_ms: 0,
        enabled: true,
        mode: Mode::Follow,
        follow_distance: 16.0,
        warp_distance: 24.0,
        reassurance: Reassurance { value: 100.0, owner_distance: 2.0, owner_visible: true, face_visible: true, owner_working: false },
        attention: Attention { scope: AttentionScope::Ambient, expansion: 1.0 },
        activity: Activity::Holding,
        reason: "follow_wait".into(),
        candidates: vec![entity()],
        action_result: None,
    }))
}
fn data(observation: &mut Observation) -> &mut Observed {
    let Observation::Observed(data) = observation else {
        panic!("observed fixture")
    };
    data
}
fn observe(state: &mut State, s: &Sample, at: Instant) {
    state.observe(Some(&observation(s)), Some(s), at, Some(0));
}
fn prepared(at: Instant) -> (State, Prepared) {
    let mut state = State::default();
    observe(&mut state, &sample(1), at);
    let prepared = state.prepare(at).unwrap();
    (state, prepared)
}
fn inspect(state: &mut State, p: &Prepared, at: Instant) -> Value {
    state
        .choose(
            p,
            r#"{"action":"inspect","target_id":"c1","reason":"気になる形や"}"#,
            at,
            utc(),
        )
        .unwrap()
        .unwrap()
}
fn result(command: &Value) -> AdapterCommandResult {
    let context = &command["context"];
    AdapterCommandResult {
        body_view: None,
        command_id: command["command_id"].as_str().unwrap().into(),
        command_type: AdapterCommandResultCommandType::InspectBodyTarget,
        status: AdapterCommandResultStatus::Succeeded,
        executed_at: EventTime::Aware((utc() + UtcDuration::seconds(1)).fixed_offset()),
        selected_slot: None,
        selected_item_id: None,
        detail_code: "arrived".into(),
        extra: BTreeMap::from([
            ("body_id".into(), context["body_id"].clone()),
            ("epoch".into(), context["epoch"].clone()),
        ]),
    }
}

#[test]
fn flattened_wire_is_closed_and_roundtrips() {
    let o = observation(&sample(1));
    o.validate().unwrap();
    let mut v = serde_json::to_value(&o).unwrap();
    assert_eq!(v["status"], "observed");
    assert!(v.get("sample").is_none());
    serde_json::from_value::<Observation>(v.clone())
        .unwrap()
        .validate()
        .unwrap();
    for key in [
        "reassurance",
        "attention",
        "enabled",
        "mode",
        "context",
        "candidates",
        "follow_distance",
        "reason",
    ] {
        let mut missing = v.clone();
        missing.as_object_mut().unwrap().remove(key);
        assert!(
            serde_json::from_value::<Observation>(missing).is_err(),
            "{key}"
        );
    }
    v["extra"] = json!(true);
    assert!(serde_json::from_value::<Observation>(v).is_err());
    for bad in [
        json!({"version":1,"status":"observed","mode":"unknown"}),
        json!({"version":1,"status":"unavailable","reason":"not_sampled","extra":0}),
    ] {
        assert!(serde_json::from_value::<Observation>(bad).is_err());
    }
}
#[test]
fn wire_rejects_identity_bounds_duplicate_and_nonfinite() {
    let original = serde_json::to_value(observation(&sample(1))).unwrap();
    for (pointer, value) in [
        ("/version", json!(1)),
        ("/context/owner_id", json!("broken")),
        ("/context/dimension", json!("Minecraft:world")),
        ("/follow_distance", json!(65)),
        ("/warp_distance", json!(129)),
        ("/follow_distance", json!(0)),
        (
            "/candidates/0/entity_id",
            json!("33333333-3333-4333-8333-333333333333"),
        ),
        ("/candidates/0/type_id", json!("bad:id:extra")),
        ("/candidates/0/position/x", json!(30000001)),
        ("/candidates/0/label", json!("牛".repeat(81))),
        ("/reason", json!("")),
        ("/server_tick", json!(u64::MAX)),
        ("/age_ms", json!(u64::MAX)),
        ("/reassurance/value", json!(-0.1)),
        ("/reassurance/value", json!(100.1)),
        ("/reassurance/owner_distance", json!(-1)),
        ("/reassurance/owner_visible", json!(false)),
        ("/attention/expansion", json!(1.1)),
    ] {
        let mut v = original.clone();
        *v.pointer_mut(pointer).unwrap() = value;
        assert!(
            serde_json::from_value::<Observation>(v)
                .unwrap()
                .validate()
                .is_err(),
            "{pointer}"
        );
    }
    let mut o = observation(&sample(1));
    data(&mut o).candidates.push(entity());
    assert!(o.validate().is_err());
    data(&mut o).candidates = vec![entity(); 25];
    assert!(o.validate().is_err());
    let mut o = observation(&sample(1));
    data(&mut o).candidates[0].position.x = f64::NAN;
    assert!(o.validate().is_err());
    data(&mut o).candidates[0] = entity();
    data(&mut o).follow_distance = f64::INFINITY;
    assert!(o.validate().is_err());
}
#[test]
fn block_identity_is_exact_cell_center_and_has_no_entity() {
    let mut c = Candidate {
        id: "b:-2,64,3:minecraft:amethyst_block".into(),
        kind: CandidateKind::Block,
        type_id: "minecraft:amethyst_block".into(),
        label: "アメジスト".into(),
        position: Position {
            x: -1.5,
            y: 64.5,
            z: 3.5,
        },
        entity_id: None,
    };
    c.validate().unwrap();
    c.position.x = -2.5;
    assert!(c.validate().is_err());
    c.position.x = -1.5;
    c.entity_id = Some(ENTITY.into());
    assert!(c.validate().is_err());
    c.entity_id = None;
    c.position.y = 64.0;
    assert!(c.validate().is_err());
}
#[test]
fn action_result_contract_does_not_promote_acceptance() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    let command = inspect(&mut state, &p, at);
    let s = sample(2);
    let mut o = observation(&s);
    data(&mut o).action_result = Some(ActionResult {
        action_id: command["command_id"].as_str().unwrap().into(),
        sequence: 1,
        outcome: Outcome::Accepted,
        reason: "accepted".into(),
    });
    o.validate().unwrap();
    state.observe(Some(&o), Some(&s), at + Duration::from_secs(1), Some(0));
    assert_eq!(
        state.commands(at + Duration::from_secs(1), utc() + UtcDuration::seconds(1)),
        vec![command]
    );
    data(&mut o).action_result.as_mut().unwrap().sequence = u64::MAX;
    assert!(o.validate().is_err());
}
#[test]
fn only_matching_accepted_context_tick_and_revision_can_publish_candidates() {
    let at = Instant::now();
    let s = sample(1);
    let original = observation(&s);
    for change in 0..6 {
        let mut o = original.clone();
        let d = data(&mut o);
        match change {
            0 => d.context.owner_id = OTHER.into(),
            1 => d.context.body_id = OTHER.into(),
            2 => d.context.epoch = OTHER.into(),
            3 => d.context.dimension = "minecraft:the_nether".into(),
            4 => d.server_tick += 1,
            _ => d.snapshot_revision += 1,
        }
        let mut state = State::default();
        state.observe(Some(&o), Some(&s), at, Some(0));
        assert!(state.prepare(at).is_none());
    }
    let mut state = State::default();
    state.observe(Some(&original), None, at, Some(0));
    assert!(state.prepare(at).is_none());
}
#[test]
fn duplicates_missing_transport_and_age_boundary_never_refresh_reading() {
    let at = Instant::now();
    let s = sample(1);
    let o = observation(&s);
    let mut state = State::default();
    state.observe(Some(&o), Some(&s), at, Some(0));
    state.observe(
        Some(&o),
        Some(&s),
        at + Duration::from_millis(2900),
        Some(0),
    );
    assert!(state.available(at + Duration::from_millis(2999)));
    assert!(!state.available(at + Duration::from_millis(3000)));
    for (transport, age) in [(None, 0), (Some(2999), 1), (Some(u64::MAX), 1)] {
        let mut state = State::default();
        let mut o = o.clone();
        data(&mut o).age_ms = age;
        state.observe(Some(&o), Some(&s), at, transport);
        assert!(!state.available(at));
    }
    let mut state = State::default();
    let mut o = o.clone();
    data(&mut o).age_ms = 50;
    state.observe(Some(&o), Some(&s), at, Some(100));
    assert!(state.available(at + Duration::from_millis(2849)));
    assert!(!state.available(at + Duration::from_millis(2850)));
}
#[test]
fn missing_observation_preserves_age_but_missing_body_cancels() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    let command = inspect(&mut state, &p, at);
    state.observe(None, Some(&sample(2)), at + Duration::from_secs(1), Some(0));
    assert_eq!(
        state.commands(at + Duration::from_secs(1), utc() + UtcDuration::seconds(1)),
        vec![command]
    );
    state.observe(None, None, at + Duration::from_secs(1), Some(0));
    assert!(state.commands(at, utc()).is_empty());
    assert!(state.prepare(at).is_none());
}
#[test]
fn unavailable_drops_pending_and_duplicate_cannot_resurrect_it() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    inspect(&mut state, &p, at);
    state.observe(
        Some(&Observation::Unavailable {
            version: 2,
            reason: "not_sampled".into(),
        }),
        Some(&sample(1)),
        at,
        Some(0),
    );
    observe(&mut state, &sample(1), at + Duration::from_secs(1));
    assert!(!state.available(at + Duration::from_secs(1)));
    assert!(state.commands(at, utc()).is_empty());
}
#[test]
fn disabled_direct_and_priority_activities_cancel_generation_and_commands() {
    let at = Instant::now();
    for activity in [
        Activity::Unbound,
        Activity::OwnerAbsent,
        Activity::Following,
        Activity::Calling,
        Activity::Retreating,
        Activity::Resting,
        Activity::Downed,
    ] {
        let (mut state, p) = prepared(at);
        inspect(&mut state, &p, at);
        let s = sample(2);
        let mut o = observation(&s);
        data(&mut o).activity = activity;
        state.observe(Some(&o), Some(&s), at, Some(0));
        assert!(!state.available(at));
        assert!(state.commands(at, utc()).is_empty());
    }
    for mode in [Mode::Direct, Mode::Hold, Mode::Explore] {
        let (mut state, p) = prepared(at);
        let s = sample(2);
        let mut o = observation(&s);
        data(&mut o).mode = mode;
        state.observe(Some(&o), Some(&s), at, Some(0));
        assert!(
            state
                .choose(
                    &p,
                    r#"{"action":"stay","target_id":null,"reason":"待つ"}"#,
                    at,
                    utc()
                )
                .is_err()
        );
    }
    let (mut state, _) = prepared(at);
    let s = sample(2);
    let mut o = observation(&s);
    data(&mut o).enabled = false;
    state.observe(Some(&o), Some(&s), at, Some(0));
    assert!(!state.available(at));
}
#[test]
fn executing_inspection_remains_available_but_does_not_start_another_choice() {
    let at = Instant::now();
    for activity in [Activity::Exploring, Activity::Moving] {
        let (mut state, p) = prepared(at);
        let command = inspect(&mut state, &p, at);
        let s = sample(2);
        let mut o = observation(&s);
        data(&mut o).activity = activity;
        state.observe(Some(&o), Some(&s), at, Some(0));
        assert!(state.available(at));
        assert_eq!(state.commands(at, utc()), vec![command]);
        assert!(state.prepare(at).is_none());
    }
}
#[test]
fn preparation_keeps_candidates_anonymous_without_a_second_novelty_table() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    assert_eq!(p.model_candidates[0]["id"], "c1");
    assert!(p.model_candidates[0].get("newly_seen_type").is_none());
    let text = p.model_candidates.to_string();
    assert!(!text.contains(ENTITY));
    assert!(!text.contains("position"));
    assert!(
        state
            .choose(
                &p,
                r#"{"action":"stay","target_id":null,"reason":"ここで待つ"}"#,
                at,
                utc()
            )
            .unwrap()
            .is_none()
    );
    observe(&mut state, &sample(2), at + Duration::from_secs(31));
    let p = state.prepare(at + Duration::from_secs(31)).unwrap();
    assert!(p.model_candidates[0].get("newly_seen_type").is_none());
    assert_eq!(p.recent_choices[0]["action"], "stay");
    assert!(p.recent_choices[0]["type_id"].is_null());
}

#[test]
fn arrival_cools_only_the_visited_object_and_not_unselected_objects_of_the_same_type() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    let command = inspect(&mut state, &p, at);
    assert_eq!(state.observe_results(&[result(&command)], at + Duration::from_secs(1),
        utc() + UtcDuration::seconds(1)).len(), 1);
    let s = sample(2);
    let mut o = observation(&s);
    let mut other = entity();
    other.id = format!("e:{OTHER}"); other.entity_id = Some(OTHER.into());
    data(&mut o).candidates.push(other.clone());
    state.observe(Some(&o), Some(&s), at + Duration::from_secs(31), Some(0));
    let p = state.prepare(at + Duration::from_secs(31)).unwrap();
    assert_eq!(p.candidates.len(), 1);
    assert_eq!(p.candidates[0].id, other.id);
    assert_eq!(p.model_candidates[0]["id"], "c1");
    assert!(p.model_candidates[0].get("newly_seen_type").is_none());
    // フィルタ後のc1は、元の候補表の先頭ではなく未訪問の別個体を指す。
    let next = inspect(&mut state, &p, at + Duration::from_secs(31));
    assert_eq!(next["target"]["id"], other.id);
    state.cancel();
    observe(&mut state, &sample(3), at + Duration::from_secs(120));
    assert!(state.prepare(at + Duration::from_secs(120)).unwrap().candidates.is_empty());
    state.cancel_preparation();
    observe(&mut state, &sample(4), at + Duration::from_secs(123));
    assert_eq!(state.prepare(at + Duration::from_secs(123)).unwrap().candidates[0].id, entity().id);
}

#[test]
fn arrival_dwell_and_follow_settling_delay_selection() {
    let at = Instant::now();
    let mut state = State::default();
    for (i, reason) in ["inspection_dwell", "owner_near_dwell", "owner_face_dwell", "follow_settling"].into_iter().enumerate() {
        let s = sample(i as u64 + 1);
        let mut o = observation(&s);
        data(&mut o).reason = reason.into();
        state.observe(Some(&o), Some(&s), at, Some(0));
        assert!(state.available(at));
        assert!(state.prepare(at).is_none());
    }
    for reason in ["inspection_dwell", "owner_near_dwell", "owner_face_dwell", "follow_settling"] {
        let (mut state, p) = prepared(at);
        let s = sample(2);
        let mut o = observation(&s);
        data(&mut o).reason = reason.into();
        state.observe(Some(&o), Some(&s), at, Some(0));
        assert!(state.choose(&p, r#"{"action":"inspect","target_id":"c1","reason":"見る"}"#, at, utc()).is_err());
    }
}

#[test]
fn looking_around_allows_a_choice_but_does_not_restore_a_lost_target() {
    let at = Instant::now();
    for disappears in [false, true] {
        let mut state = State::default();
        let s = sample(1);
        let mut o = observation(&s);
        data(&mut o).reason = "follow_lookaround".into();
        state.observe(Some(&o), Some(&s), at, Some(0));
        let p = state.prepare(at).expect("a lookaround can start selection");
        let latest = sample(2);
        let mut o = observation(&latest);
        data(&mut o).reason = "follow_lookaround".into();
        if disappears { data(&mut o).candidates.clear(); }
        state.observe(Some(&o), Some(&latest), at, Some(0));
        let result = state.choose(&p, r#"{"action":"inspect","target_id":"c1","reason":"近くで見る"}"#, at, utc());
        if disappears {
            assert_eq!(result.unwrap_err().to_string(), "body_autonomy_target_disappeared");
        } else {
            assert!(result.unwrap().is_some());
        }
    }
}

#[test]
fn failed_cancelled_stale_and_duplicate_receipts_do_not_create_or_extend_a_visit() {
    let at = Instant::now();
    for case in ["failed", "cancelled", "stale", "duplicate"] {
        let (mut state, p) = prepared(at);
        let command = inspect(&mut state, &p, at);
        let mut receipt = result(&command);
        if case == "failed" { receipt.status = AdapterCommandResultStatus::Failed; receipt.detail_code = "path_blocked".into(); }
        if case == "cancelled" { state.cancel(); }
        let seconds = if case == "stale" { 4 } else { 1 };
        let observed = state.observe_results(&[receipt.clone()], at + Duration::from_secs(seconds), utc() + UtcDuration::seconds(seconds as i64));
        assert_eq!(observed.len(), usize::from(matches!(case, "failed" | "duplicate")));
        if case == "duplicate" {
            observe(&mut state, &sample(2), at + Duration::from_secs(20));
            assert!(state.observe_results(&[receipt], at + Duration::from_secs(20), utc() + UtcDuration::seconds(20)).is_empty());
            observe(&mut state, &sample(3), at + Duration::from_secs(120));
            assert!(state.prepare(at + Duration::from_secs(120)).unwrap().candidates.is_empty());
    state.cancel_preparation();
            observe(&mut state, &sample(4), at + Duration::from_secs(123));
            assert!(state.prepare(at + Duration::from_secs(123)).is_some());
        } else {
            observe(&mut state, &sample(2), at + Duration::from_secs(31));
            assert!(state.prepare(at + Duration::from_secs(31)).is_some(), "{case}");
        }
    }
}

#[test]
fn confirmed_visits_survive_temporary_cancellation_but_not_a_new_body_epoch() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    let command = inspect(&mut state, &p, at);
    state.observe_results(&[result(&command)], at + Duration::from_secs(1), utc() + UtcDuration::seconds(1));
    state.cancel();
    state.observe(None, None, at + Duration::from_secs(2), Some(0));
    observe(&mut state, &sample(2), at + Duration::from_secs(31));
    assert!(state.prepare(at + Duration::from_secs(31)).unwrap().candidates.is_empty());
    let mut s = sample(3);
    s.context.epoch = OTHER.into();
    observe(&mut state, &s, at + Duration::from_secs(32));
    assert!(state.prepare(at + Duration::from_secs(32)).is_some());
}
#[test]
fn new_type_waits_full_three_seconds_and_invalid_choice_is_consumed_once() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    assert!(state.choose(&p, "{}", at, utc()).is_err());
    assert!(
        state
            .choose(
                &p,
                r#"{"action":"stay","target_id":null,"reason":"待つ"}"#,
                at,
                utc()
            )
            .is_err()
    );
    let s = sample(2);
    let mut o = observation(&s);
    data(&mut o).candidates[0].type_id = "minecraft:pig".into();
    state.observe(Some(&o), Some(&s), at + Duration::from_millis(2999), Some(0));
    assert!(state.prepare(at + Duration::from_millis(2999)).is_none());
    assert!(state.prepare(at + Duration::from_secs(3)).is_some());
}
#[test]
fn choice_rejects_unknown_fields_forged_ids_types_and_invalid_reasons() {
    let at = Instant::now();
    let outputs = [
        json!({"action":"inspect","target_id":"c0","reason":"見る"}),
        json!({"action":"inspect","target_id":format!("e:{ENTITY}"),"reason":"見る"}),
        json!({"action":"inspect","target_id":1,"reason":"見る"}),
        json!({"action":"stay","target_id":"c1","reason":"待つ"}),
        json!({"action":"inspect","target_id":null,"reason":"見る"}),
        json!({"action":"stay","reason":"待つ"}),
        json!({"action":"attack","target_id":"c1","reason":"見る"}),
        json!({"action":"stay","target_id":null,"reason":" "}),
        json!({"action":"stay","target_id":null,"reason":"長".repeat(161)}),
        json!({"action":"stay","target_id":null,"reason":"待つ","position":{"x":1}}),
    ];
    for output in outputs {
        let (mut state, p) = prepared(at);
        assert!(
            state.choose(&p, &output.to_string(), at, utc()).is_err(),
            "{output}"
        );
    }
}
#[test]
fn choice_revalidates_disappearance_type_and_movement_and_keeps_original_target() {
    let at = Instant::now();
    for change in 0..3 {
        let (mut state, p) = prepared(at);
        let s = sample(2);
        let mut o = observation(&s);
        match change {
            0 => data(&mut o).candidates.clear(),
            1 => data(&mut o).candidates[0].type_id = "minecraft:pig".into(),
            _ => data(&mut o).candidates[0].position.x += 1.51,
        }
        state.observe(Some(&o), Some(&s), at, Some(0));
        assert!(
            state
                .choose(
                    &p,
                    r#"{"action":"inspect","target_id":"c1","reason":"見る"}"#,
                    at,
                    utc()
                )
                .is_err()
        );
    }
    let (mut state, p) = prepared(at);
    let s = sample(2);
    let mut o = observation(&s);
    data(&mut o).candidates[0].position.x += 1.5;
    state.observe(Some(&o), Some(&s), at, Some(0));
    let command = inspect(&mut state, &p, at);
    assert_eq!(command["target"]["position"]["x"], 2.0);
}
#[test]
fn choice_uses_latest_accepted_owner_even_without_new_autonomy_payload() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    let mut s = sample(2);
    s.player_pose.position.x = 100.0;
    state.observe(None, Some(&s), at, Some(0));
    assert!(
        state
            .choose(
                &p,
                r#"{"action":"inspect","target_id":"c1","reason":"見る"}"#,
                at,
                utc()
            )
            .is_err()
    );
}
#[test]
fn choice_refuses_stale_generation_cancellation_and_repeated_use() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    assert!(
        state
            .choose(
                &p,
                r#"{"action":"inspect","target_id":"c1","reason":"見る"}"#,
                at + Duration::from_secs(3),
                utc()
            )
            .is_err()
    );
    let (mut state, p) = prepared(at);
    state.cancel();
    assert!(state.choose(&p, "{}", at, utc()).is_err());
    let (mut state, p) = prepared(at);
    inspect(&mut state, &p, at);
    assert!(state.choose(&p, "{}", at, utc()).is_err());
    let (mut state, p) = prepared(at);
    let mut s = sample(1);
    s.context.epoch = ENTITY.into();
    observe(&mut state, &s, at);
    assert!(state.choose(&p, "{}", at, utc()).is_err());
    let newer = state.prepare(at).unwrap();
    assert!(newer.model_candidates[0].get("newly_seen_type").is_none());
    assert_eq!(newer.recent_choices, json!([]));
}
#[test]
fn command_retry_is_identical_and_stops_at_five_seconds_but_keeps_busy() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    let command = inspect(&mut state, &p, at);
    observe(&mut state, &sample(2), at + Duration::from_secs(4));
    assert_eq!(
        state.commands(
            at + Duration::from_millis(4999),
            utc() + UtcDuration::milliseconds(4999)
        ),
        vec![command.clone()]
    );
    assert!(
        state
            .commands(at + Duration::from_secs(5), utc() + UtcDuration::seconds(5))
            .is_empty()
    );
    assert!(
        state
            .commands(at + Duration::from_secs(5), utc())
            .is_empty()
    );
    assert!(
        state
            .commands(at, utc() + UtcDuration::seconds(5))
            .is_empty()
    );
    assert!(
        state
            .commands(at, utc() - UtcDuration::seconds(1))
            .is_empty()
    );
    let s = sample(3);
    let mut o = observation(&s);
    data(&mut o).candidates[0].type_id = "minecraft:pig".into();
    state.observe(Some(&o), Some(&s), at + Duration::from_secs(31), Some(0));
    assert!(state.prepare(at + Duration::from_secs(31)).is_none());
    assert_eq!(
        command["expires_at"],
        json!(utc() + UtcDuration::seconds(5))
    );
}
#[test]
fn receipt_requires_command_type_context_time_and_arrival() {
    let at = Instant::now();
    for change in 0..9 {
        let (mut state, p) = prepared(at);
        let command = inspect(&mut state, &p, at);
        let mut r = result(&command);
        match change {
            0 => r.command_id = ENTITY.into(),
            1 => r.command_type = AdapterCommandResultCommandType::RecallHorse,
            2 => {
                r.extra.insert("body_id".into(), json!(OTHER));
            }
            3 => {
                r.extra.insert("epoch".into(), json!(OTHER));
            }
            4 => {
                r.executed_at =
                    EventTime::Aware((utc() - UtcDuration::milliseconds(1)).fixed_offset())
            }
            5 => {
                r.executed_at = EventTime::Aware((utc() + UtcDuration::seconds(35)).fixed_offset())
            }
            6 => r.executed_at = EventTime::Naive(utc().naive_utc()),
            7 => r.detail_code = "accepted".into(),
            _ => r.executed_at = EventTime::Aware((utc() + UtcDuration::seconds(2)).fixed_offset()),
        }
        assert!(
            state
                .observe_results(
                    &[r],
                    at + Duration::from_secs(1),
                    utc() + UtcDuration::seconds(1)
                )
                .is_empty(),
            "{change}"
        );
        assert_eq!(state.commands(at, utc()), vec![command]);
    }
}
#[test]
fn valid_terminal_receipt_is_consumed_once_and_failure_is_not_arrival() {
    let at = Instant::now();
    for status in [
        AdapterCommandResultStatus::Succeeded,
        AdapterCommandResultStatus::Rejected,
        AdapterCommandResultStatus::Failed,
        AdapterCommandResultStatus::Expired,
    ] {
        let (mut state, p) = prepared(at);
        let command = inspect(&mut state, &p, at);
        let mut r = result(&command);
        r.status = status;
        if status != AdapterCommandResultStatus::Succeeded {
            r.detail_code = "target_unavailable".into();
        }
        let out = state.observe_results(
            &[r.clone(), r.clone()],
            at + Duration::from_secs(1),
            utc() + UtcDuration::seconds(1),
        );
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["status"], json!(status));
        assert!(
            state
                .observe_results(
                    &[r],
                    at + Duration::from_secs(2),
                    utc() + UtcDuration::seconds(2)
                )
                .is_empty()
        );
        assert!(state.commands(at, utc()).is_empty());
    }
}
#[test]
fn deadline_is_thirty_five_seconds_and_prepare_also_retires_once() {
    let at = Instant::now();
    let (mut state, p) = prepared(at);
    let command = inspect(&mut state, &p, at);
    observe(&mut state, &sample(2), at + Duration::from_millis(34999));
    assert!(
        state
            .observe_results(
                &[],
                at + Duration::from_millis(34999),
                utc() + UtcDuration::milliseconds(34999)
            )
            .is_empty()
    );
    let s = sample(3);
    let mut o = observation(&s);
    data(&mut o).candidates[0].type_id = "minecraft:pig".into();
    state.observe(Some(&o), Some(&s), at + Duration::from_secs(35), Some(0));
    assert!(state.prepare(at + Duration::from_secs(35)).is_some());
    let out = state.observe_results(
        &[result(&command)],
        at + Duration::from_secs(35),
        utc() + UtcDuration::seconds(35),
    );
    assert_eq!(out.len(), 1);
    assert_eq!(out[0]["status"], "expired");
    assert_eq!(out[0]["command_id"], command["command_id"]);
    assert!(
        state
            .observe_results(
                &[],
                at + Duration::from_secs(36),
                utc() + UtcDuration::seconds(36)
            )
            .is_empty()
    );
}
#[test]
fn selected_history_is_bounded_and_never_claims_arrival() {
    let at = Instant::now();
    let mut state = State::default();
    for i in 0..10 {
        let time = at + Duration::from_secs(i * 30);
        let s = sample(i + 1);
        let mut o = observation(&s);
        data(&mut o).candidates[0].type_id = format!("test:kind{i}");
        state.observe(Some(&o), Some(&s), time, Some(0));
        let p = state.prepare(time).unwrap();
        assert_eq!(
            p.recent_choices.as_array().unwrap().len(),
            (i as usize).min(8)
        );
        assert!(!p.recent_choices.to_string().contains("arrived"));
        state
            .choose(
                &p,
                r#"{"action":"stay","target_id":null,"reason":"待つ"}"#,
                time,
                utc() + UtcDuration::seconds(i as i64 * 30),
            )
            .unwrap();
    }
}

#[test]
fn replayed_payload_cannot_replace_candidates_or_restore_a_retired_context() {
    let at = Instant::now();
    let s = sample(1);
    let old = observation(&s);
    let mut state = State::default();
    state.observe(Some(&old), Some(&s), at, Some(0));
    let mut changed = old.clone();
    data(&mut changed).candidates[0].type_id = "minecraft:pig".into();
    state.observe(Some(&changed), Some(&s), at, Some(0));
    assert_eq!(
        state.prepare(at).unwrap().candidates[0].type_id,
        "minecraft:cow"
    );

    let mut next = sample(2);
    next.context.epoch = OTHER.into();
    let mut newer = observation(&next);
    data(&mut newer).candidates[0].type_id = "minecraft:sheep".into();
    state.observe(Some(&newer), Some(&next), at, Some(0));
    state.observe(Some(&old), Some(&next), at, Some(0));
    let prepared = state.prepare(at).unwrap();
    assert_eq!(prepared.context, next.context);
    assert_eq!(prepared.candidates[0].type_id, "minecraft:sheep");
}

#[test]
fn both_selected_and_latest_target_must_be_inside_current_owner_range() {
    let at = Instant::now();
    // 元位置だけ範囲外の場合と、移動後だけ範囲外の場合をそれぞれ拒否する。
    for owner_x in [18.5, -13.0] {
        let (mut state, p) = prepared(at);
        let mut s = sample(2);
        s.player_pose.position.x = owner_x;
        let mut o = observation(&s);
        data(&mut o).candidates[0].position.x = 3.5;
        state.observe(Some(&o), Some(&s), at, Some(0));
        assert!(
            state
                .choose(
                    &p,
                    r#"{"action":"inspect","target_id":"c1","reason":"見る"}"#,
                    at,
                    utc()
                )
                .is_err()
        );
    }
}

#[test]
fn recorded_real_model_fences_pass_existing_object_extraction_and_current_candidate_validation() {
    let probes: Vec<Value> = serde_json::from_str(include_str!("../../../fixtures/body-interest-real-probes-2026-10-14.json")).unwrap();
    for probe in probes {
        let at=Instant::now();
        let s=sample(1);
        let mut obs=observation(&s);
        let mut second=entity();
        second.entity_id=Some(OTHER.into()); second.id=format!("e:{OTHER}");
        data(&mut obs).candidates.push(second);
        let mut state=State::default();
        state.observe(Some(&obs),Some(&s),at,Some(0));
        let prepared=state.prepare(at).unwrap();
        let command=state.choose(&prepared,probe["response_text"].as_str().unwrap(),at,utc()).unwrap().unwrap();
        let expected=if probe["expected_target_id"]=="c1" {ENTITY} else {OTHER};
        assert_eq!(command["target"]["entity_id"],expected);
    }
}

#[test]
fn current_follow_radius_filters_inspection_but_preserves_owner_choices() {
    let at = Instant::now();
    let mut state = State::default();
    let s = sample(1);
    let mut o = observation(&s);
    data(&mut o).follow_distance = 8.0;
    data(&mut o).candidates[0].position.x = 8.001;
    state.observe(Some(&o), Some(&s), at, Some(0));
    let empty = state.prepare(at).unwrap();
    assert!(empty.candidates.is_empty());
    assert!(!empty.available_actions.contains(&"inspect".into()));
    assert!(empty.available_actions.contains(&"owner_near".into()));
    state.cancel_preparation();
    let at = at + Duration::from_secs(3);
    let s = sample(2);
    let mut o = observation(&s);
    data(&mut o).follow_distance = 8.0;
    let mut outside = entity();
    outside.id = format!("e:{OTHER}"); outside.entity_id = Some(OTHER.into()); outside.position.x = 8.001;
    data(&mut o).candidates = vec![outside, entity()];
    data(&mut o).candidates[1].position.x = 8.0;
    state.observe(Some(&o), Some(&s), at, Some(0));
    let p = state.prepare(at).unwrap();
    assert_eq!(p.candidates.len(), 1);
    assert_eq!(p.candidates[0].id, entity().id);
    assert_eq!(p.model_candidates[0]["id"], "c1");
    assert_eq!(inspect(&mut state, &p, at)["target"]["position"]["x"], 8.0);
}

#[test]
fn owner_choices_work_without_environment_candidates_and_never_fake_recovery_or_visits() {
    for action in ["owner_near", "owner_face"] {
        let at=Instant::now(); let mut state=State::default(); let sample=sample(1);
        let mut observation=observation(&sample); let row=data(&mut observation);
        row.candidates.clear(); row.reassurance.value=20.0;
        state.observe(Some(&observation),Some(&sample),at,Some(0));
        let ready=state.ready_reassurance(at).unwrap(); assert_eq!(ready["value"],20.0);
        let prepared=state.prepare(at).unwrap(); assert!(prepared.candidates.is_empty());
        assert!(state.ready_reassurance(at).is_none());
        let command=state.choose(&prepared,&json!({"action":action,"target_id":null}).to_string(),at,utc()).unwrap().unwrap();
        assert_eq!(command["type"],"inspect_body_target"); assert_eq!(command["purpose"],action); assert!(command["target"].is_null());
        state.cancel_preparation();
        assert_eq!(state.commands(at,utc()),vec![command.clone()]);
        assert_eq!(state.observe_results(&[result(&command)],at+Duration::from_secs(1),utc()+UtcDuration::seconds(1)).len(),1);
        assert_eq!(state.reassurance(at+Duration::from_secs(1)).unwrap().value,20.0);
        assert_eq!(state.attention(at).unwrap()["basis"],"body_attention");
        assert!(state.reassurance(at+Duration::from_secs(3)).is_none());
    }
}

#[test]
fn face_approach_rechecks_current_working_state_and_owner_actions_reject_candidate_ids() {
    let at=Instant::now();
    for action in ["owner_near","owner_face"] {
        let (mut state,prepared)=prepared(at);
        assert!(state.choose(&prepared,&json!({"action":action,"target_id":"c1"}).to_string(),at,utc()).is_err());
    }
    let (mut state,prepared)=prepared(at); let current=sample(2); let mut observation=observation(&current);
    data(&mut observation).reassurance.owner_working=true;
    state.observe(Some(&observation),Some(&current),at,Some(0));
    assert!(state.choose(&prepared,r#"{"action":"owner_face","target_id":null}"#,at,utc()).is_err());
    let prepared=state.prepare(at+Duration::from_secs(3)); assert!(prepared.is_none(),"stale sample");
    let at=at+Duration::from_secs(3);
    let latest=sample(3); let mut observation=super::tests::observation(&latest); data(&mut observation).reassurance.owner_working=true;
    state.observe(Some(&observation),Some(&latest),at,Some(0));
    let prepared=state.prepare(at).unwrap();
    assert!(!prepared.available_actions.contains(&"owner_face".into()));
}

#[test]
fn following_exposes_feeling_but_cannot_reserve_a_model_movement() {
    let at=Instant::now(); let mut state=State::default(); let sample=sample(1); let mut observation=observation(&sample);
    data(&mut observation).activity=Activity::Following;
    state.observe(Some(&observation),Some(&sample),at,Some(0));
    assert!(state.reassurance(at).is_some()); assert!(state.attention(at).is_some());
    assert!(state.ready_reassurance(at).is_none()); assert!(state.prepare(at).is_none());
}

#[test]
fn model_actions_explain_only_the_currently_allowed_choices() {
    let at = Instant::now();
    for working in [false, true] {
        for has_candidate in [false, true] {
            let mut state = State::default();
            let sample = sample(1);
            let mut observed = observation(&sample);
            data(&mut observed).reassurance.owner_working = working;
            if !has_candidate { data(&mut observed).candidates.clear(); }
            state.observe(Some(&observed), Some(&sample), at, Some(0));
            let prepared = state.prepare(at).unwrap();
            let actions = prepared.model_actions();
            assert_eq!(actions["owner_near"], "そばへ戻る");
            assert_eq!(actions["stay"], "その場におる");
            assert_eq!(actions.get("owner_face").is_some(), !working);
            assert_eq!(actions.get("inspect").is_some(), has_candidate);
            assert_eq!(actions.as_object().unwrap().len(), prepared.available_actions.len());
            if working {
                assert!(state.choose(&prepared, r#"{"action":"owner_face","target_id":null}"#, at, utc()).is_err());
            } else if !has_candidate {
                assert!(state.choose(&prepared, r#"{"action":"inspect","target_id":"c1"}"#, at, utc()).is_err());
            }
        }
    }
}

/// 実追従の前後を同じ身体世代で送る。単なる距離や初回の待機を到着にはしない。
fn returned_from_follow(state: &mut State, at: Instant) {
    let first = sample(1); let mut moving = observation(&first);
    data(&mut moving).activity = Activity::Following; data(&mut moving).reason = "distance_follow".into();
    state.observe(Some(&moving),Some(&first),at,Some(0));
    let next = sample(2); let mut holding = observation(&next);
    data(&mut holding).reason = "follow_settling".into();
    state.observe(Some(&holding),Some(&next),at+Duration::from_millis(100),Some(0));
}

#[test]
fn a_real_follow_arrival_can_choose_face_during_settling_once() {
    let at=Instant::now(); let mut state=State::default();
    returned_from_follow(&mut state,at);
    let now=at+Duration::from_millis(100);
    assert!(state.owner_arrival_pending(now)); assert!(state.ready_reassurance(now).is_some());
    let prepared=state.prepare(now).unwrap();
    assert!(prepared.after_owner_arrival);
    assert_eq!(prepared.available_actions,vec!["stay","owner_face"]);
    assert!(prepared.candidates.is_empty()); assert_eq!(prepared.model_candidates,json!([]));
    assert!(!state.owner_arrival_pending(now));
    let command=state.choose(&prepared,r#"{"action":"owner_face","target_id":null}"#,now,utc()).unwrap().unwrap();
    assert_eq!(command["purpose"],"owner_face");
    let arrived_sample=sample(3); let mut arrived=observation(&arrived_sample);
    data(&mut arrived).reason="owner_face_dwell".into();
    state.observe(Some(&arrived),Some(&arrived_sample),at+Duration::from_secs(1),Some(0));
    assert_eq!(state.observe_results(&[result(&command)],at+Duration::from_secs(1),utc()+UtcDuration::seconds(1)).len(),1);
    assert!(!state.owner_arrival_pending(at+Duration::from_secs(1)));
}

#[test]
fn initial_settling_and_staying_near_do_not_create_arrival_decisions() {
    let at=Instant::now(); let mut state=State::default();
    for tick in 1..=3 {
        let s=sample(tick); let mut o=observation(&s); data(&mut o).reason="follow_settling".into();
        state.observe(Some(&o),Some(&s),at,Some(0));
        assert!(!state.owner_arrival_pending(at)); assert!(state.prepare(at).is_none());
    }
}

#[test]
fn only_a_valid_near_arrival_receipt_creates_the_second_step() {
    for action in ["owner_near","owner_face","inspect"] {
        for success in [false,true] {
            let at=Instant::now(); let (mut state,p)=prepared(at);
            let command=state.choose(&p,&json!({"action":action,"target_id":if action=="inspect" {Some("c1")} else {None}}).to_string(),at,utc()).unwrap().unwrap();
            let s=sample(2); let mut o=observation(&s); data(&mut o).reason="owner_near_dwell".into();
            state.observe(Some(&o),Some(&s),at+Duration::from_secs(1),Some(0));
            assert!(!state.owner_arrival_pending(at+Duration::from_secs(1)),"dwell alone is not arrival");
            let mut receipt=result(&command);
            if !success { receipt.status=AdapterCommandResultStatus::Rejected; receipt.detail_code="blocked".into(); }
            let now=at+Duration::from_secs(1);
            assert_eq!(state.observe_results(&[receipt.clone()],now,utc()+UtcDuration::seconds(1)).len(),1);
            assert_eq!(state.owner_arrival_pending(now),success && action=="owner_near");
            if success && action=="owner_near" {
                let second=state.prepare(now).expect("actual arrival bypasses ordinary 3s spacing and 5s dwell");
                assert!(second.after_owner_arrival);
                assert!(state.choose(&second,r#"{"action":"stay","target_id":null}"#,now,utc()+UtcDuration::seconds(1)).unwrap().is_none());
            }
            assert!(state.observe_results(&[receipt],now,utc()+UtcDuration::seconds(1)).is_empty());
            assert!(!state.owner_arrival_pending(now));
        }
    }
}

#[test]
fn an_arrival_rereads_working_state_and_excludes_other_moves() {
    for output in ["owner_face","owner_near","inspect"] {
        let at=Instant::now(); let mut state=State::default(); returned_from_follow(&mut state,at);
        let s=sample(3); let mut o=observation(&s); data(&mut o).reason="follow_settling".into();
        data(&mut o).reassurance.owner_working=true;
        state.observe(Some(&o),Some(&s),at+Duration::from_millis(200),Some(0));
        let p=state.prepare(at+Duration::from_millis(200)).unwrap();
        assert_eq!(p.available_actions,vec!["stay"]);
        assert!(state.choose(&p,&json!({"action":output,"target_id":null}).to_string(),at+Duration::from_millis(200),utc()).is_err());
        assert!(!state.owner_arrival_pending(at+Duration::from_millis(200)));
    }
    let at=Instant::now(); let mut state=State::default(); returned_from_follow(&mut state,at);
    let p=state.prepare(at+Duration::from_millis(100)).unwrap();
    let s=sample(3); let mut o=observation(&s); data(&mut o).reason="follow_settling".into();
    data(&mut o).reassurance.owner_working=true;
    state.observe(Some(&o),Some(&s),at+Duration::from_millis(200),Some(0));
    assert!(state.choose(&p,r#"{"action":"owner_face","target_id":null}"#,at+Duration::from_millis(200),utc()).is_err());
}

#[test]
fn departure_control_changes_stale_frames_and_cancellation_do_not_reuse_arrival() {
    for transition in ["distance","following","mode","disabled","epoch","missing","stale","cancel"] {
        let at=Instant::now(); let mut state=State::default(); returned_from_follow(&mut state,at);
        let s=sample(3); let mut o=observation(&s); data(&mut o).reason="follow_settling".into();
        match transition {
            "distance" => data(&mut o).reassurance.owner_distance=17.0,
            "following" => data(&mut o).activity=Activity::Following,
            "mode" => data(&mut o).mode=Mode::Hold,
            "disabled" => data(&mut o).enabled=false,
            "epoch" => {
                let mut changed=s.clone(); changed.context.epoch=OTHER.into(); data(&mut o).context=changed.context.clone();
                state.observe(Some(&o),Some(&changed),at+Duration::from_millis(200),Some(0));
            }
            "missing" => state.observe(None,None,at+Duration::from_millis(200),Some(0)),
            "stale" => {},
            "cancel" => state.cancel_owner_arrival(),
            _ => unreachable!(),
        }
        if matches!(transition,"distance"|"following"|"mode"|"disabled") {
            state.observe(Some(&o),Some(&s),at+Duration::from_millis(200),Some(0));
        }
        let now=at+Duration::from_secs(if transition=="stale" {4} else {1});
        assert!(!state.owner_arrival_pending(now),"{transition}");
    }
}

#[test]
fn looking_around_preserves_a_real_return_but_a_stale_gap_never_revives_it() {
    for prepared_before_look in [false,true] {
        let at=Instant::now(); let mut state=State::default(); returned_from_follow(&mut state,at);
        let p=prepared_before_look.then(|| state.prepare(at+Duration::from_millis(100)).unwrap());
        let s=sample(3); let mut o=observation(&s); data(&mut o).reason="follow_lookaround".into();
        state.observe(Some(&o),Some(&s),at+Duration::from_secs(1),Some(0));
        let p=p.unwrap_or_else(|| {
            assert!(state.owner_arrival_pending(at+Duration::from_secs(1)));
            state.prepare(at+Duration::from_secs(1)).unwrap()
        });
        assert!(state.choose(&p,r#"{"action":"owner_face","target_id":null}"#,at+Duration::from_secs(1),utc()).unwrap().is_some());
    }
    let at=Instant::now(); let mut state=State::default(); returned_from_follow(&mut state,at);
    let s=sample(3); let mut o=observation(&s); data(&mut o).reason="follow_settling".into();
    state.observe(Some(&o),Some(&s),at+Duration::from_secs(4),Some(0));
    assert!(!state.owner_arrival_pending(at+Duration::from_secs(4)));
    assert!(state.prepare(at+Duration::from_secs(4)).is_none());
}

#[test]
fn image_choice_has_no_invented_coordinates_and_keeps_existing_availability_checks() {
    for invalid in ["valid","color","extra","expired","unavailable"] {
        let at=Instant::now();let (mut state,mut prepared)=prepared(at);
        prepared.image_capture=Some(("view_12345678-1234-1234-1234-123456789abc".into(),utc()));
        if invalid!="unavailable" {prepared.available_actions.push("inspect_image".into());}
        let mut choice=json!({"action":"inspect_image","target_id":null,"image_target":{"description":"黄色い花","kind":"flower","color":"yellow","horizontal":"left","vertical":"any"}});
        if invalid=="color" {choice["image_target"]["color"]=json!("unknown_color");}
        if invalid=="extra" {choice["image_target"]["x"]=json!(64);}
        let now=if invalid=="expired" {utc()+chrono::Duration::seconds(31)} else {utc()};
        let result=state.choose(&prepared,&choice.to_string(),at,now);
        if invalid=="valid" {let command=result.unwrap().unwrap();assert!(command["target"].is_null());assert_eq!(command["purpose"],"inspect");
            assert_eq!(command["image_target"]["color"],"yellow");assert!(command["image_target"].get("x").is_none());assert!(command["image_target"].get("position").is_none());}
        else {assert!(result.is_err(),"{invalid}");}
    }
}
