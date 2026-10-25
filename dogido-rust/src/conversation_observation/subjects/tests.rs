//! 実測の主体・雨と接触の組合せ・水没・鮮度を検査する。実モデルの返答品質は別に評価する。
use super::*;
use crate::conversation_observation::{self, State};
use std::time::{Duration, Instant};

fn raw(
    body_environment: Value,
    body_world: Value,
    player_environment: Value,
    player_world: Value,
) -> GameEvent {
    let event = crate::body::tests::with_senses(
        json!({
            "schema_version":"2026-05-24","adapter":"fixture","observed_at":chrono::Utc::now(),
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"position":{"x":0,"y":64,"z":0},"environment":player_environment},
            "world":player_world
        }),
        100,
    );
    let mut wire = serde_json::to_value(event).unwrap();
    wire["body_observation"]["sample"]["senses"]["environment"] = body_environment;
    wire["body_observation"]["sample"]["senses"]["world"] = body_world;
    GameEvent::parse(wire).unwrap()
}
fn rain_world(cover: &str, sky: bool) -> Value {
    json!({"biome":"forest","weather":"rain","overhead_cover_type":cover,"sky_visible":sky,"depth_below_surface":0})
}
fn contact(water: bool, submerged: bool, rain: bool) -> Value {
    json!({"touching_water":water,"submerged_in_water":submerged,"touching_rain":rain,"touching_water_or_rain":water || rain})
}
fn state(raw: &GameEvent, now: Instant) -> crate::body::State {
    let mut state = crate::body::State::default();
    state.observe(
        raw.body_observation.as_ref(),
        Some("11111111-1111-4111-8111-111111111111"),
        raw.player.dimension.as_deref(),
        now,
        Some(0),
    );
    state
}
fn context(event: &GameEvent) -> conversation_observation::Context {
    conversation_observation::project(event, None, 32., 7., 5000).unwrap()
}

#[test]
fn sheltered_player_and_exposed_dogido_keep_their_own_weather_and_cover() {
    let mut player_contact = contact(false, false, false);
    player_contact["on_ground"] = json!(true);
    player_contact["on_fire"] = json!(false);
    let raw = raw(
        contact(false, false, true),
        rain_world("none", true),
        player_contact,
        rain_world("foliage", false),
    );
    let now = Instant::now();
    let event = state(&raw, now).sensory_event(&raw, now);
    let c = context(&event);
    let p = &c.observations["subject_states"]["player"];
    let d = &c.observations["subject_states"]["dogido"];
    assert_eq!(p["precipitation_kind"], "rain");
    assert_eq!(d["precipitation_kind"], "rain");
    assert_eq!(p["contact"]["on_ground"], true);
    assert_eq!(p["contact"]["on_fire"], false);
    assert!(d["contact"]["on_ground"].is_null());
    assert!(d["contact"]["on_fire"].is_null());
    assert_eq!(p["overhead_cover"], "foliage");
    assert_eq!(d["overhead_cover"], "none");
    assert!(
        p["relations"]["rain_and_cover"]
            .as_str()
            .unwrap()
            .contains("プレイヤーは木の葉の下")
    );
    assert!(
        d["relations"]["rain_and_cover"]
            .as_str()
            .unwrap()
            .contains("ドギドにも雨が当たっている")
    );
    assert!(c.observations.get("player_environment").is_none());
}

#[test]
fn body_cover_does_not_supply_the_players_missing_cover_and_body_contact_is_not_borrowed() {
    let raw = raw(
        Value::Null,
        rain_world("foliage", false),
        contact(false, false, false),
        json!({"weather":"rain","biome":"forest"}),
    );
    let now = Instant::now();
    let c = context(&state(&raw, now).sensory_event(&raw, now));
    let p = &c.observations["subject_states"]["player"];
    let d = &c.observations["subject_states"]["dogido"];
    assert!(p["overhead_cover"].is_null());
    assert!(
        p["relations"]["rain_and_cover"]
            .as_str()
            .unwrap()
            .contains("理由は未確認")
    );
    assert!(d["contact"]["touching_rain"].is_null());
    assert!(
        d["relations"]["rain_and_cover"]
            .as_str()
            .unwrap()
            .contains("当たっているかは未確認")
    );
}

#[test]
fn cover_alone_does_not_prove_protection_and_snow_does_not_use_rain_contact() {
    let e = |p, r, c, s| rain_relation("ドギド", p, r, c, s);
    assert!(
        e(
            PrecipitationKind::Rain,
            Some(true),
            Some("foliage"),
            Some(false)
        )
        .contains("雨が当たっている")
    );
    assert!(
        e(
            PrecipitationKind::Rain,
            Some(false),
            Some("stone"),
            Some(false)
        )
        .contains("遮蔽物の下")
    );
    assert!(e(PrecipitationKind::Rain, Some(false), None, None).contains("理由は未確認"));
    assert!(
        e(
            PrecipitationKind::Snow,
            Some(false),
            Some("foliage"),
            Some(false)
        )
        .contains("雪が当たっているかは未観測")
    );
    assert!(e(PrecipitationKind::None, Some(true), None, None).contains("一致していない"));
}

#[test]
fn combined_water_or_rain_does_not_disambiguate_rain_while_in_water() {
    for (fields, expected) in [
        (
            json!({"touching_water":true,"touching_water_or_rain":true}),
            None,
        ),
        (
            json!({"touching_water":false,"touching_water_or_rain":true}),
            Some(true),
        ),
        (json!({"touching_water_or_rain":false}), Some(false)),
        (
            json!({"touching_water":true,"touching_water_or_rain":true,"touching_rain":false}),
            Some(false),
        ),
        (json!({}), None),
    ] {
        let environment = serde_json::from_value(fields).unwrap();
        assert_eq!(rain_contact(Some(&environment)), expected);
    }
}

#[test]
fn player_altitude_and_biome_are_not_replaced_by_dogidos_climate() {
    let raw = raw(
        contact(false, false, false),
        json!({"biome":"snowy_plains","weather":"rain"}),
        contact(false, false, true),
        rain_world("none", true),
    );
    let now = Instant::now();
    let c = context(&state(&raw, now).sensory_event(&raw, now));
    assert_eq!(
        c.observations["subject_states"]["player"]["precipitation_kind"],
        "rain"
    );
    assert_eq!(
        c.observations["subject_states"]["dogido"]["precipitation_kind"],
        "snow"
    );
}

#[test]
fn water_contact_is_not_swimming_or_eye_submersion() {
    let mut w = rain_world("none", true);
    w["is_submerged"] = json!(true);
    let raw = raw(
        contact(true, false, false),
        w,
        contact(false, false, true),
        rain_world("none", true),
    );
    let now = Instant::now();
    let c = context(&state(&raw, now).sensory_event(&raw, now));
    assert!(
        c.observations["place"]
            .as_str()
            .unwrap()
            .contains("水に触れている場所")
    );
    let water = c.observations["subject_states"]["dogido"]["relations"]["water"]
        .as_str()
        .unwrap();
    assert!(water.contains("眼の位置は水面より下ではない"));
    assert!(!water.contains("泳"));
    assert!(
        water_relation("ドギド", Some(true), Some(true)).contains("眼の位置は水面より下にある")
    );
}

#[test]
fn stale_body_drops_its_relations_and_cannot_reuse_player_contact() {
    let raw = raw(
        contact(false, false, true),
        rain_world("none", true),
        contact(false, false, false),
        rain_world("foliage", false),
    );
    let now = Instant::now();
    let state = state(&raw, now);
    let fresh = state.sensory_event(&raw, now);
    assert_eq!(
        context(&fresh).observations["subject_states"]["dogido"]["status"],
        "observed"
    );
    let stale = state.sensory_event(&raw, now + Duration::from_secs(3));
    let c = context(&stale);
    assert_eq!(c.observations["sensory_status"], "unavailable");
    assert!(c.observations.get("subject_states").is_none());
}

#[test]
fn current_values_survive_consideration_but_timestamp_only_updates_do_not_create_changes() {
    let raw = raw(
        contact(false, false, false),
        rain_world("foliage", false),
        contact(false, false, true),
        rain_world("none", true),
    );
    let now = Instant::now();
    let c = context(&state(&raw, now).sensory_event(&raw, now));
    let mut observer = State::default();
    observer.observe(c.clone());
    observer.sample_notice(0, 100, true);
    let mut later = c.clone();
    later.observations["subject_states"]["dogido"]["observed_at"] = json!("later");
    observer.observe(later.clone());
    observer.sample_notice(100, 100, true);
    assert!(observer.context().unwrap().changes.is_empty());
    assert!(observer.noticing_context().is_none());
    later.observations["subject_states"]["dogido"]["contact"]["touching_rain"] = json!(true);
    observer.observe(later);
    observer.sample_notice(200, 100, true);
    assert!(
        observer
            .context()
            .unwrap()
            .changes
            .iter()
            .any(|c| c.kind == "subject_states")
    );
    assert!(observer.noticing_context().is_some());
    let revision = observer.context().unwrap().revision;
    observer.considered(revision);
    let c = observer.context().unwrap();
    assert!(c.changes.is_empty());
    assert_eq!(
        c.observations["subject_states"]["dogido"]["contact"]["touching_rain"],
        true
    );
}
