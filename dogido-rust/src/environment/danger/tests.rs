//! 身体の停止と隣接距離を、警告選択・未再生の再照合・クールダウンまで通して確認する。
use super::*;
use std::time::Instant;

fn heat(distance: f64, stationary: Option<bool>, magma: bool) -> GameEvent {
    let raw = crate::body::tests::with_senses(json!({
        "schema_version":"2026-05-24", "adapter":"fixture", "observed_at":chrono::Utc::now(),
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20},
        "world":{"biome":"plains","time_phase":"day","sky_visible":true,"local_light":15,
            "nearby_damaging_light_source_count":28,"nearest_damaging_light_source_distance":distance,
            "standing_on_magma_block":magma}
    }), 100);
    let now = Instant::now();
    let mut body = crate::body::State::default();
    body.observe(raw.body_observation.as_ref(), Some("11111111-1111-4111-8111-111111111111"),
        Some("minecraft:overworld"), now, Some(0));
    body.sensory_event(&raw, now, stationary)
}

fn urgent(d: &mut Danger, e: &GameEvent, now: u64) -> Vec<Speech> {
    let settings = Settings::default();
    d.update(e, now, true, &settings);
    d.urgent(e, now, Mode::Normal, true, &settings)
}

#[test]
fn heat_uses_integer_adjacent_distance_and_current_stationary_state() {
    for (distance, expected) in [(0.0,true),(1.0,true),(1.414,true),(1.732,true),(1.999,true),
        (2.0,false),(2.24,false),(4.12,false),(5.01,false)] {
        for stationary in [None, Some(false), Some(true)] {
            let e = heat(distance, stationary, false);
            let mut danger = Danger::default();
            let warning = urgent(&mut danger, &e, 0);
            let eligible = expected && stationary == Some(true);
            assert_eq!(!warning.is_empty(), eligible, "distance={distance} stationary={stationary:?}");
            assert_eq!(still_applicable(&Speech::new("damaging_light", "熱源"), &e, &Settings::default()), eligible);
            if eligible { assert_eq!(warning[0].kind, "damaging_light"); }
        }
    }
    // 足元マグマの専用反応と二重に選ばない。
    assert!(urgent(&mut Danger::default(), &heat(0.0, Some(true), true), 0).is_empty());
}

#[test]
fn moving_heat_never_consumes_cooldown_or_survives_as_a_deferred_candidate() {
    let mut danger = Danger::default();
    assert!(urgent(&mut danger, &heat(1.0, Some(false), false), 0).is_empty());
    assert!(urgent(&mut danger, &heat(2.24, Some(true), false), 1000).is_empty());
    let near = heat(1.73, Some(true), false);
    let warning = urgent(&mut danger, &near, 2000);
    assert_eq!(warning[0].kind, "damaging_light");
    assert!(still_applicable(&warning[0], &near, &Settings::default()));
    assert!(!still_applicable(&warning[0], &heat(1.0, Some(false), false), &Settings::default()));
    assert!(!still_applicable(&warning[0], &heat(2.24, Some(true), false), &Settings::default()));
    assert!(urgent(&mut danger, &near, 601999).is_empty());
    assert_eq!(urgent(&mut danger, &near, 602000)[0].kind, "damaging_light");
}
