//! 接触・落下・酸素の実測と、連続する危険の一度だけの機会を検査する。
use super::*;
use serde_json::{Value, json};

fn event(contact: Value, motion: Value, air: Option<i64>) -> GameEvent {
    GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"test","observed_at":"2026-10-25T00:00:00Z",
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20,"environment":contact,"motion":motion},
        "world":{"air_supply":air}})).unwrap()
}
fn falling() -> GameEvent {
    event(
        json!({"touching_water":false,"touching_lava":false,"on_ground":false}),
        json!({"fall_distance":4.1,"vertical_velocity":-0.5,"flying":false,"gliding":false,"climbing":false}),
        None,
    )
}

#[test]
fn lava_fire_and_nearby_heat_are_different_episode_facts() {
    let lava = event(
        json!({"touching_lava":true,"on_fire":true}),
        Value::Null,
        None,
    );
    let fire = event(
        json!({"touching_lava":false,"on_fire":true}),
        Value::Null,
        None,
    );
    let safe = event(
        json!({"touching_lava":false,"on_fire":false}),
        Value::Null,
        None,
    );
    let mut state = State::default();
    state.observe(&lava);
    let action = state.take(&lava).unwrap();
    assert_eq!(kind(&action), Some(Kind::Lava));
    for _ in 0..3 {
        state.observe(&lava);
        assert!(state.take(&lava).is_none());
    }
    state.observe(&fire);
    assert!(!applicable(&action, &fire));
    assert_eq!(kind(&state.take(&fire).unwrap()), Some(Kind::Fire));
    state.observe(&safe);
    assert!(state.take(&safe).is_none());
    state.observe(&lava);
    assert!(state.take(&lava).is_some());
    let mut nearby = json!(safe);
    nearby["world"]["nearby_damaging_light_source_count"] = json!(1);
    nearby["world"]["nearest_damaging_light_source_distance"] = json!(1.0);
    assert!(current(&GameEvent::parse(nearby).unwrap()).is_empty());
}

#[test]
fn incomplete_or_protected_movement_does_not_assert_falling_danger() {
    let input = falling();
    assert_eq!(current(&input), BTreeSet::from([Kind::Falling]));
    for (path, value) in [
        (vec!["player", "motion", "fall_distance"], json!(3.0)),
        (vec!["player", "motion", "vertical_velocity"], json!(0)),
        (vec!["player", "motion", "climbing"], json!(true)),
        (vec!["player", "motion", "gliding"], json!(true)),
        (vec!["player", "motion", "flying"], json!(true)),
        (vec!["player", "motion", "flying"], Value::Null),
        (vec!["player", "environment", "on_ground"], json!(true)),
        (vec!["player", "environment", "touching_water"], json!(true)),
        (
            vec!["player", "active_status_effects"],
            json!(["slow_falling"]),
        ),
        (vec!["player", "invulnerable"], json!(true)),
    ] {
        let mut wire = json!(input);
        let mut field = &mut wire;
        for name in path {
            field = &mut field[name];
        }
        *field = value;
        assert!(!current(&GameEvent::parse(wire).unwrap()).contains(&Kind::Falling));
    }
    let mut bad = json!(input);
    bad["player"]["motion"]["fall_distance"] = json!(-1);
    assert!(GameEvent::parse(bad).is_err());
}

#[test]
fn air_is_player_air_and_requires_current_eye_submersion() {
    for (eyes, air, expected) in [
        (true, 60, true),
        (true, 61, false),
        (false, 0, false),
        (true, -10, true),
    ] {
        assert_eq!(
            current(&event(
                json!({"submerged_in_water":eyes}),
                Value::Null,
                Some(air)
            ))
            .contains(&Kind::LowAir),
            expected
        );
    }
    assert!(
        current(&event(
            json!({"submerged_in_water":true}),
            Value::Null,
            None
        ))
        .is_empty()
    );
    let mut wire = json!(event(
        json!({"submerged_in_water":true}),
        Value::Null,
        Some(0)
    ));
    wire["player"]["active_status_effects"] = json!(["water_breathing"]);
    assert!(current(&GameEvent::parse(wire).unwrap()).is_empty());
}

#[test]
fn damage_alone_does_not_create_or_block_a_known_hazard_but_real_hostiles_take_priority() {
    let mut wire = json!(event(json!({"touching_lava":true}), Value::Null, None));
    wire["combat"] = json!({"recent_damage_ms":0,"combat_active_hint":true});
    let lava = GameEvent::parse(wire.clone()).unwrap();
    let mut state = State::default();
    state.observe(&lava);
    assert!(state.take(&lava).is_some());
    wire["combat"]["hostiles_within_10"] = json!(1);
    let hostile = GameEvent::parse(wire).unwrap();
    let mut state = State::default();
    state.observe(&hostile);
    assert!(state.take(&hostile).is_none());
    state.observe(&lava);
    assert!(state.take(&lava).is_some());
    assert!(current(&event(json!({}), Value::Null, None)).is_empty());
}

#[test]
fn death_immunity_and_fire_resistance_do_not_raise_contact_danger() {
    for (field, value) in [
        ("health", json!(0)),
        ("invulnerable", json!(true)),
        (
            "active_status_effects",
            json!(["minecraft:fire_resistance"]),
        ),
    ] {
        let mut wire = json!(event(
            json!({"touching_lava":true,"on_fire":true}),
            Value::Null,
            None
        ));
        wire["player"][field] = value;
        assert!(current(&GameEvent::parse(wire).unwrap()).is_empty());
    }
}
