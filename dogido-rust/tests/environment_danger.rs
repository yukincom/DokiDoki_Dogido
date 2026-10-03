use dogido_rust::{
    combat::model::{Mode, Settings, Speech},
    environment::danger::{Danger, heard_thunder, still_applicable},
    events::GameEvent,
};
use serde_json::{Value, json};
fn event(world: Value) -> GameEvent {
    GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-09-26T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"player":{"dimension":"minecraft:overworld","position":{"x":0,"z":0}},"world":world})).unwrap()
}
fn edit(e: &mut GameEvent, f: impl FnOnce(&mut dogido_rust::events::EventData)) {
    let mut data = (**e).clone();
    f(&mut data);
    *e = GameEvent::parse(serde_json::to_value(data).unwrap()).unwrap();
}
fn dark() -> GameEvent {
    event(
        json!({"biome":"plains","time_phase":"day","sky_visible":false,"local_light":0,"danger_darkness_score":1,"ceiling_height":6,"enclosure_score":0.5}),
    )
}
fn tick(
    d: &mut Danger,
    e: &GameEvent,
    now: u64,
    complete: bool,
    busy: bool,
    focus: bool,
) -> Vec<Speech> {
    let s = Settings::default();
    d.update(e, now, complete, &s);
    let mode = if e.world.is_submerged != Some(true)
        && e.world.danger_darkness_score.unwrap_or(0.0) >= 0.72
        && e.world.local_light.is_none_or(|v| v <= 3)
    {
        Mode::Alert
    } else {
        Mode::Normal
    };
    let a = d.urgent(e, now, mode, focus, &s);
    if a.is_empty() {
        d.actions(e, now, mode, busy, focus, &s)
    } else {
        a
    }
}
#[test]
fn unchanged_severe_darkness_loops_only_after_delay() {
    let mut d = Danger::default();
    let e = dark();
    assert_eq!(
        tick(&mut d, &e, 0, true, false, false)[0].kind,
        "dark_push_no_light"
    );
    assert!(tick(&mut d, &e, 4999, true, false, false).is_empty());
    assert_eq!(
        tick(&mut d, &e, 5000, true, false, false)[0].cue_id,
        Some("suppressed_breath")
    );
    assert!(tick(&mut d, &e, 8799, true, false, false).is_empty());
    assert_eq!(
        tick(&mut d, &e, 8800, true, false, false)[0].kind,
        "dark_push_breath"
    );
}
#[test]
fn actual_recovery_stops_once() {
    let mut d = Danger::default();
    let mut e = dark();
    tick(&mut d, &e, 0, true, false, false);
    edit(&mut e, |e| e.world.local_light = Some(4));
    let a = tick(&mut d, &e, 1000, true, false, false);
    assert_eq!(a[0].kind, "dark_push_stop");
    assert!(!d.dark_push_active());
    assert!(
        !tick(&mut d, &e, 2000, true, false, false)
            .iter()
            .any(|a| a.kind == "dark_push_stop")
    );
}
#[test]
fn partial_empty_notification_does_not_recover_or_reenter() {
    let mut d = Danger::default();
    let e = dark();
    tick(&mut d, &e, 0, true, false, false);
    let p = event(json!({}));
    assert!(tick(&mut d, &p, 1000, false, false, false).is_empty());
    assert!(d.dark_push_active());
    assert_eq!(
        tick(&mut d, &e, 5000, true, false, false)[0].kind,
        "dark_push_breath"
    );
}
#[test]
fn stage_one_needs_movement_and_scary_threshold() {
    let mut d = Danger::default();
    let mut e = dark();
    edit(&mut e, |e| e.world.local_light = Some(3));
    assert_eq!(
        tick(&mut d, &e, 0, true, false, false)[0].kind,
        "occluded_entry_no_light"
    );
    edit(&mut e, |e| e.world.local_light = Some(0));
    assert!(tick(&mut d, &e, 1000, true, false, false).is_empty());
    edit(&mut e, |e| e.player.position.x = Some(0.999));
    assert!(tick(&mut d, &e, 2000, true, false, false).is_empty());
    edit(&mut e, |e| e.player.position.x = Some(1.0));
    assert_eq!(
        tick(&mut d, &e, 3000, true, false, false)[0].kind,
        "dark_push_no_light"
    );
}
#[test]
fn light_inventory_does_not_claim_recovery() {
    let mut d = Danger::default();
    let mut e = dark();
    tick(&mut d, &e, 0, true, false, false);
    edit(&mut e, |e| {
        e.inventory.insert("torch".into(), 64);
    });
    let ctx = d.light_context(&e, &Settings::default());
    assert!(!ctx.recovered);
    assert!(ctx.severe_darkness);
    assert!(tick(&mut d, &e, 1000, true, false, false).is_empty());
    assert!(d.dark_push_active());
}
#[test]
fn busy_preserves_entry_only_while_observed() {
    let mut d = Danger::default();
    let e = dark();
    assert!(tick(&mut d, &e, 0, true, true, false).is_empty());
    assert_eq!(
        tick(&mut d, &e, 1000, true, false, false)[0].kind,
        "dark_push_no_light"
    );
    let mut d = Danger::default();
    tick(&mut d, &e, 0, true, true, false);
    let safe = event(json!({"sky_visible":true,"local_light":15}));
    assert!(tick(&mut d, &safe, 1000, true, false, false).is_empty());
}
#[test]
fn full_recovery_stops_breath_during_busy() {
    let mut d = Danger::default();
    let mut e = dark();
    tick(&mut d, &e, 0, true, false, false);
    edit(&mut e, |e| e.world.local_light = Some(4));
    let a = tick(&mut d, &e, 1000, true, true, false);
    assert_eq!(a.len(), 1);
    assert_eq!(a[0].kind, "dark_push_stop");
    assert_eq!(
        tick(&mut d, &e, 2000, true, false, false)[0].kind,
        "dark_push_after_breath"
    );
}
#[test]
fn thunder_message_and_cue_have_independent_timers() {
    let mut d = Danger::default();
    let e = event(json!({"thunder_sound_recent_ms":0}));
    let first = tick(&mut d, &e, 0, false, true, true);
    assert_eq!(first.len(), 2);
    assert!(first[0].interrupt);
    assert_eq!(first[1].kind, "thunder_reaction");
    assert_eq!(
        first[1].leaf.as_ref().unwrap().details["scream_status"],
        "scheduled"
    );
    assert!(tick(&mut d, &e, 179999, false, true, true).is_empty());
    let a = tick(&mut d, &e, 180000, false, true, true);
    assert_eq!(a.len(), 1);
    assert!(!a[0].interrupt);
    assert_eq!(
        a[0].leaf.as_ref().unwrap().details["scream_status"],
        "not_scheduled"
    );
    assert_eq!(tick(&mut d, &e, 600000, false, true, true).len(), 2);
}
#[test]
fn lightning_requires_current_distance_and_freshness() {
    let s = Settings::default();
    let mut e = event(
        json!({"nearby_lightning_strike_recent_ms":2000,"nearby_lightning_strike_distance":16}),
    );
    assert!(heard_thunder(&e, &s));
    edit(&mut e, |e| {
        e.world.nearby_lightning_strike_recent_ms = Some(2001)
    });
    assert!(!heard_thunder(&e, &s));
    edit(&mut e, |e| {
        e.world.nearby_lightning_strike_recent_ms = Some(0)
    });
    edit(&mut e, |e| e.world.nearby_lightning_strike_distance = None);
    assert!(!heard_thunder(&e, &s));
}
#[test]
fn evening_once_per_cycle_and_not_in_focus() {
    let mut d = Danger::default();
    let mut e =
        event(json!({"sky_visible":true,"local_light":12,"time_phase":"evening","biome":"plains"}));
    assert!(tick(&mut d, &e, 0, true, false, true).is_empty());
    assert_eq!(
        tick(&mut d, &e, 1000, true, true, false)[0].kind,
        "night_warning_surface"
    );
    assert!(tick(&mut d, &e, 2000, true, false, false).is_empty());
    edit(&mut e, |e| {
        e.world.time_phase = Some(dogido_rust::events::TimePhase::Day)
    });
    tick(&mut d, &e, 3000, true, false, false);
    edit(&mut e, |e| {
        e.world.time_phase = Some(dogido_rust::events::TimePhase::Evening)
    });
    assert_eq!(
        tick(&mut d, &e, 4000, true, false, false)[0].kind,
        "night_warning_surface"
    );
}
#[test]
fn shelter_shape_jitter_does_not_repeat_relief() {
    let mut d = Danger::default();
    let mut e = event(
        json!({"sky_visible":false,"local_light":0,"time_phase":"night","cardinal_wall_count":4,"ceiling_height":2}),
    );
    assert_eq!(
        tick(&mut d, &e, 0, true, false, false)[0].kind,
        "emergency_shelter_relief"
    );
    edit(&mut e, |e| e.world.cardinal_wall_count = Some(2));
    tick(&mut d, &e, 1000, true, false, false);
    edit(&mut e, |e| e.world.cardinal_wall_count = Some(4));
    assert!(
        !tick(&mut d, &e, 2000, true, false, false)
            .iter()
            .any(|a| a.kind == "emergency_shelter_relief")
    );
    edit(&mut e, |e| {
        e.world.time_phase = Some(dogido_rust::events::TimePhase::Morning)
    });
    assert_eq!(
        tick(&mut d, &e, 3000, true, false, false)[0].kind,
        "emergency_shelter_morning"
    );
}
#[test]
fn portal_initial_baseline_and_disappearance() {
    let mut d = Danger::default();
    let mut e = event(json!({"nearby_portal_type":"nether_portal"}));
    assert!(tick(&mut d, &e, 0, true, false, false).is_empty());
    edit(&mut e, |e| {
        e.world.nearby_portal_type = Some("end_portal".into())
    });
    assert!(tick(&mut d, &e, 1000, true, true, false).is_empty());
    edit(&mut e, |e| e.world.nearby_portal_type = None);
    assert!(tick(&mut d, &e, 2000, true, false, false).is_empty());
    edit(&mut e, |e| {
        e.world.nearby_portal_type = Some("end_portal".into())
    });
    let a = tick(&mut d, &e, 3000, true, false, false);
    assert_eq!(a[0].kind, "portal_appearance");
    edit(&mut e, |e| e.world.nearby_portal_type = None);
    assert!(!still_applicable(&a[0], &e, &Settings::default()));
}
#[test]
fn dimension_change_does_not_synthesize_entry_edge() {
    let mut d = Danger::default();
    let mut e = dark();
    tick(&mut d, &e, 0, true, false, false);
    edit(&mut e, |e| {
        e.player.dimension = Some("minecraft:the_nether".into())
    });
    assert!(tick(&mut d, &e, 1000, true, false, false).is_empty());
    assert!(!d.dark_push_active());
    assert!(
        !tick(&mut d, &e, 2000, true, false, false)
            .iter()
            .any(|a| a.kind == "dark_push_no_light")
    );
}
#[test]
fn front_ambush_resets_breath_to_stage_one() {
    let mut d = Danger::default();
    let e = dark();
    tick(&mut d, &e, 0, true, false, false);
    assert_eq!(
        d.interrupt_for_threat(&e, &Settings::default())[0].kind,
        "dark_push_stop"
    );
    assert!(d.dark_push_active());
    assert!(
        !tick(&mut d, &e, 6000, true, false, false)
            .iter()
            .any(|a| a.kind == "dark_push_breath")
    );
}

#[test]
fn underwater_comment_cooldown_never_prevents_stopping_breath() {
    let mut d = Danger::default();
    let water = event(json!({"is_submerged":true,"submerged_depth_blocks":5,"local_light":0}));
    assert_eq!(
        tick(&mut d, &water, 0, true, false, false)[0].kind,
        "submerged_darkness"
    );
    assert_eq!(
        tick(&mut d, &dark(), 1000, true, false, false)[0].kind,
        "dark_push_no_light"
    );
    let a = tick(&mut d, &water, 2000, true, false, false);
    assert_eq!(a.len(), 1);
    assert_eq!(a[0].kind, "dark_push_stop");
    assert!(!d.dark_push_active());
}

#[test]
fn delivery_checks_current_light_inventory_and_night_phase() {
    let s = Settings::default();
    let mut d = Danger::default();
    let mut e = dark();
    edit(&mut e, |e| {
        e.inventory.insert("torch".into(), 1);
    });
    let a = tick(&mut d, &e, 0, true, false, false).remove(0);
    assert!(d.still_applicable(&a, &e, &s));
    edit(&mut e, |e| e.inventory.clear());
    assert!(!d.still_applicable(&a, &e, &s));
    let mut d = Danger::default();
    let mut e = event(json!({"biome":"dripstone_caves","time_phase":"evening","local_light":15}));
    let a = tick(&mut d, &e, 0, true, false, false).remove(0);
    edit(&mut e, |e| {
        e.world.time_phase = Some(dogido_rust::events::TimePhase::Night)
    });
    assert!(!still_applicable(&a, &e, &s));
}

#[test]
fn full_non_snapshot_invalidates_deferred_water_entry() {
    let mut d = Danger::default();
    let water = event(json!({"is_submerged":true,"submerged_depth_blocks":5,"local_light":0}));
    assert!(tick(&mut d, &water, 0, true, true, false).is_empty());
    let mut clear = event(json!({"is_submerged":false,"local_light":15}));
    edit(&mut clear, |e| {
        e.event.name = dogido_rust::events::EventName::ThreatApproaching
    });
    assert!(tick(&mut d, &clear, 1000, true, false, false).is_empty());
}

#[test]
fn heat_warns_before_contact_even_during_conversation_and_keeps_cooldown() {
    let mut d = Danger::default();
    let mut e = event(
        json!({"biome":"plains","time_phase":"day","sky_visible":true,
        "local_light":15,"nearby_damaging_light_source_count":1,"nearest_damaging_light_source_distance":5.01}),
    );
    edit(&mut e, |e| {
        e.player.health = Some(20.0);
        e.combat.recent_damage_ms = Some(60000);
    });
    assert!(tick(&mut d, &e, 0, true, true, false).is_empty());
    edit(&mut e, |e| {
        e.world.nearest_damaging_light_source_distance = Some(4.12)
    });
    let warning = tick(&mut d, &e, 1000, true, true, false);
    assert_eq!(warning[0].kind, "damaging_light");
    assert!(still_applicable(&warning[0], &e, &Settings::default()));
    assert!(tick(&mut d, &e, 2000, true, true, false).is_empty());
    edit(&mut e, |e| {
        e.world.nearest_damaging_light_source_distance = Some(5.01)
    });
    assert!(!still_applicable(&warning[0], &e, &Settings::default()));
    edit(&mut e, |e| {
        e.world.nearest_damaging_light_source_distance = Some(0.0)
    });
    assert!(tick(&mut d, &e, 600999, true, true, false).is_empty());
    assert_eq!(
        tick(&mut d, &e, 601000, true, true, false)[0].kind,
        "damaging_light"
    );
}

#[test]
fn portal_encounter_survives_deferral_and_selects_matching_fallback() {
    for (encounter, fragment) in [
        (json!("appeared"), "出てきた"),
        (json!("arrived"), "来た"),
        (json!("observed"), "あ、"),
        (Value::Null, "あ、"),
    ] {
        let mut d = Danger::default();
        tick(&mut d, &event(json!({})), 0, true, false, false);
        let e = event(
            json!({"nearby_portal_type":"nether_portal","nearby_portal_encounter":encounter}),
        );
        assert!(tick(&mut d, &e, 1000, true, true, false).is_empty());
        // Repeated snapshots of the same encounter retain its original classification.
        let line = tick(&mut d, &e, 2000, true, false, false).remove(0);
        assert_eq!(
            line.leaf.as_ref().unwrap().details["portal_encounter"],
            encounter
        );
        assert!(line.text.contains(fragment), "{}", line.text);
    }
}

#[test]
fn deferred_portal_uses_changed_encounter_from_full_snapshot() {
    let mut d = Danger::default();
    tick(&mut d, &event(json!({})), 0, true, false, false);
    let appeared =
        event(json!({"nearby_portal_type":"nether_portal","nearby_portal_encounter":"appeared"}));
    assert!(tick(&mut d, &appeared, 1000, true, true, false).is_empty());
    let arrived =
        event(json!({"nearby_portal_type":"nether_portal","nearby_portal_encounter":"arrived"}));
    let line = tick(&mut d, &arrived, 2000, true, false, false).remove(0);
    assert_eq!(line.leaf.unwrap().details["portal_encounter"], "arrived");
    assert!(line.text.contains("来た"));
}
