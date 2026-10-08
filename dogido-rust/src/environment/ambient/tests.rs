use super::*;
use crate::events::VehicleState;
fn event(mut fields: Value) -> GameEvent {
    let mut value = json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-09-25T00:00:00Z","sequence":1,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"world":{"biome":"plains","time_phase":"day"}});
    value
        .as_object_mut()
        .unwrap()
        .extend(fields.as_object_mut().unwrap().clone());
    GameEvent::parse(value).unwrap()
}
fn actions(a: &mut Ambient, e: &GameEvent, now: u64) -> Vec<Speech> {
    let s = Settings::default();
    a.update(e, now, true, &s);
    a.actions(e, now, Mode::Normal, false, &AmbientFocus::default(), &s)
}
#[test]
fn ambient_reference_cases_match_fixture() {
    let rows: Value = serde_json::from_str(include_str!("reference.json")).unwrap();
    for (n, row) in rows.as_array().unwrap().iter().enumerate() {
        let got = match row["op"].as_str().unwrap() {
            "scent_query" => json!(smell::is_query(row["text"].as_str().unwrap())),
            "smell" => {
                let e = GameEvent::parse(row["event"].clone()).unwrap();
                let s = smell::speech(&e);
                json!({"text":s.text,"cue_id":s.cue_id})
            }
            "vehicle" => {
                let v: VehicleState = serde_json::from_value(row["vehicle"].clone()).unwrap();
                json!(player_vehicle_fact(Some(&v)))
            }
            "light_context" => {
                let c = &row["context"];
                let c = LightContext {
                    surroundings_reasonably_lit: c["surroundings_reasonably_lit"]
                        .as_bool()
                        .unwrap(),
                    severe_darkness: c["severe_darkness"].as_bool().unwrap(),
                    nearby_light_present: c["nearby_light_present"].as_bool().unwrap(),
                    dark_push_context_before: c["dark_push_context_before"].as_bool().unwrap(),
                    dark_push_recovered: c["dark_push_recovered"].as_bool().unwrap(),
                };
                let p = row["previous"].as_i64().unwrap();
                let q = row["current"].as_i64().unwrap();
                let recent = row["recent"].as_bool().unwrap();
                json!({"allowed_actions":light::allowed(p,q,&c,recent),"facts":light::facts(p,q,&c,recent)})
            }
            _ => panic!("unknown fixture"),
        };
        assert_eq!(got, row["expected"], "reference {n} {}", row["op"]);
    }
}

#[test]
fn no_ambient_during_threat_busy_or_foreground() {
    let s = Settings::default();
    let mut a = Ambient::default();
    let e = event(json!({"passive_mobs":[{"type":"cat"}]}));
    a.update(&e, 0, true, &s);
    for (mode, busy, f) in [
        (Mode::Panic, false, AmbientFocus::default()),
        (Mode::Normal, true, AmbientFocus::default()),
        (
            Mode::Normal,
            false,
            AmbientFocus {
                foreground: true,
                ..Default::default()
            },
        ),
    ] {
        assert!(a.actions(&e, 0, mode, busy, &f, &s).is_empty());
    }
    assert_eq!(
        a.actions(
            &e,
            1,
            Mode::Normal,
            false,
            &AmbientFocus {
                notice_ready: true,
                ..Default::default()
            },
            &s
        )[0]
        .kind,
        "surroundings_notice"
    );
}
#[test]
fn casual_foreground_mutes_mobs_for_thirty_seconds_only() {
    let s = Settings::default();
    let mut a = Ambient::default();
    let e = event(json!({"passive_mobs":[{"type":"cat"}]}));
    let f = AmbientFocus {
        foreground: true,
        casual_foreground: true,
        last_player_input_at: Some(0),
        notice_ready: true,
        ..Default::default()
    };
    a.update(&e, 0, true, &s);
    assert!(a.actions(&e, 29999, Mode::Normal, false, &f, &s).is_empty());
    assert_eq!(a.actions(&e, 30000, Mode::Normal, false, &f, &s).len(), 1);
}

fn light_request(a: &mut Ambient, f: &AmbientFocus) -> (GameEvent, LightPlanRequest) {
    let s = Settings::default();
    let e = event(json!({"inventory":{"torch":1}}));
    a.update(&event(json!({})), 0, true, &s);
    a.update(&e, 1, true, &s);
    assert!(a.actions(&e, 1, Mode::Normal, false, f, &s).is_empty());
    let req = a.take_light_plan().unwrap();
    (e, req)
}
#[test]
fn light_planner_is_bounded_and_does_not_choose_speech_itself() {
    let f = AmbientFocus {
        light: LightContext {
            surroundings_reasonably_lit: true,
            ..Default::default()
        },
        ..Default::default()
    };
    let mut a = Ambient::default();
    let (e, r) = light_request(&mut a, &f);
    assert_eq!(
        r.details["allowed_actions"],
        json!(["stay_silent", "acknowledge_supply_gain"])
    );
    assert!(!r.details.to_string().contains("previous_count"));
    let payload = json!({"action":"acknowledge_supply_gain","basis_ids":["first_light_supply"],"confidence":0.85});
    assert!(
        a.resolve_light_plan(r.request_id + 1, &payload, &e, 2, &f, &Settings::default())
            .is_none()
    );
    let line = a
        .resolve_light_plan(r.request_id, &payload, &e, 2, &f, &Settings::default())
        .unwrap();
    assert_eq!(line.kind, "light_source_gain");
    assert!(
        a.resolve_light_plan(r.request_id, &payload, &e, 3, &f, &Settings::default())
            .is_none()
    );
}
#[test]
fn light_invalid_none_low_confidence_and_unobserved_basis_are_silent() {
    let f = AmbientFocus {
        light: LightContext {
            surroundings_reasonably_lit: true,
            ..Default::default()
        },
        ..Default::default()
    };
    for payload in [
        Value::Null,
        json!({"action":"relief_after_darkness","basis_ids":["dark_push_recovered"],"confidence":1}),
        json!({"action":"acknowledge_supply_gain","basis_ids":["first_light_supply"],"confidence":0.7}),
        json!({"action":"acknowledge_supply_gain","basis_ids":["fabricated"],"confidence":1}),
        json!({"action":"acknowledge_supply_gain","basis_ids":["first_light_supply","first_light_supply"],"confidence":1}),
    ] {
        let mut a = Ambient::default();
        let (e, r) = light_request(&mut a, &f);
        assert!(
            a.resolve_light_plan(r.request_id, &payload, &e, 2, &f, &Settings::default())
                .is_none()
        );
    }
}
#[test]
fn light_recovery_is_past_fact_but_new_darkness_or_input_cancels() {
    let f = AmbientFocus {
        light: LightContext {
            surroundings_reasonably_lit: true,
            dark_push_context_before: true,
            dark_push_recovered: true,
            ..Default::default()
        },
        ..Default::default()
    };
    let payload = json!({"action":"relief_after_darkness","basis_ids":["dark_push_recovered"],"confidence":1});
    let mut a = Ambient::default();
    let (e, r) = light_request(&mut a, &f);
    let later = AmbientFocus {
        light: LightContext {
            surroundings_reasonably_lit: true,
            ..Default::default()
        },
        ..Default::default()
    };
    assert!(
        a.resolve_light_plan(
            r.request_id,
            &payload,
            &e,
            3000,
            &later,
            &Settings::default()
        )
        .is_some()
    );
    let mut a = Ambient::default();
    let (e, r) = light_request(&mut a, &f);
    let dark = AmbientFocus {
        light: LightContext {
            severe_darkness: true,
            ..Default::default()
        },
        ..Default::default()
    };
    assert!(
        a.resolve_light_plan(
            r.request_id,
            &payload,
            &e,
            3000,
            &dark,
            &Settings::default()
        )
        .is_none()
    );
    let mut a = Ambient::default();
    let (e, r) = light_request(&mut a, &f);
    a.note_player_input(2);
    assert!(
        a.resolve_light_plan(
            r.request_id,
            &payload,
            &e,
            3000,
            &later,
            &Settings::default()
        )
        .is_none()
    );
}
#[test]
fn initial_inventory_and_abundant_safe_supply_do_not_request_model() {
    let mut a = Ambient::default();
    let s = Settings::default();
    let f = AmbientFocus {
        light: LightContext {
            surroundings_reasonably_lit: true,
            ..Default::default()
        },
        ..Default::default()
    };
    let e = event(json!({"inventory":{"torch":32}}));
    a.update(&e, 0, true, &s);
    a.actions(&e, 0, Mode::Normal, false, &f, &s);
    assert!(a.take_light_plan().is_none());
    let e = event(json!({"inventory":{"torch":64}}));
    a.update(&e, 1, true, &s);
    a.actions(&e, 1, Mode::Normal, false, &f, &s);
    assert!(a.take_light_plan().is_none());
}

#[test]
fn eye_requires_actual_recent_event() {
    let mut a = Ambient::default();
    let eye = event(json!({"world":{"biome":"plains","ender_eye_launch_recent_ms":2000}}));
    assert_eq!(actions(&mut a, &eye, 4000)[0].kind, "ender_eye_throw");
    assert!(actions(&mut a, &eye, 5000).is_empty());
    let stale = event(json!({"world":{"ender_eye_launch_recent_ms":2001}}));
    assert!(actions(&mut a, &stale, 13000).is_empty());
}

#[test]
fn losing_clone_does_not_consume_mob_or_light_decision() {
    let mut a = Ambient::default();
    let s = Settings::default();
    let e = event(json!({"passive_mobs":[{"type":"cat"}]}));
    a.update(&e, 0, true, &s);
    let mut losing = a.clone();
    assert_eq!(
        losing
            .actions(
                &e,
                0,
                Mode::Normal,
                false,
                &AmbientFocus {
                    notice_ready: true,
                    ..Default::default()
                },
                &s
            )
            .len(),
        1
    );
    assert_eq!(
        a.actions(
            &e,
            1,
            Mode::Normal,
            false,
            &AmbientFocus {
                notice_ready: true,
                ..Default::default()
            },
            &s
        )
        .len(),
        1
    );
    let mut a = Ambient::default();
    let f = AmbientFocus {
        light: LightContext {
            surroundings_reasonably_lit: true,
            ..Default::default()
        },
        ..Default::default()
    };
    a.update(&event(json!({})), 0, true, &s);
    let e = event(json!({"inventory":{"torch":1}}));
    a.update(&e, 1, true, &s);
    let mut losing = a.clone();
    losing.actions(&e, 1, Mode::Normal, false, &f, &s);
    assert!(losing.take_light_plan().is_some());
    a.actions(&e, 1, Mode::Normal, false, &f, &s);
    assert!(a.take_light_plan().is_some());
}
#[test]
fn priority_zombie_scent_is_shared_with_ambient_without_mode_change() {
    let e = event(
        json!({"smell_observation":{"status":"present","smell_id":"zombie","category":"decay","valence":"unpleasant","source_kind":"entity","specificity":"source","effective_strength":8}}),
    );
    let mut a = Ambient::default();
    let s = Settings::default();
    a.update(&e, 0, true, &s);
    assert!(a.priority_smell(&e, 0, Mode::Normal, false, &s).is_none());
    a.update(&e, 1, true, &s);
    assert_eq!(
        a.priority_smell(&e, 1, Mode::Normal, false, &s)
            .unwrap()
            .cue_id,
        Some("zombie_scent_warning")
    );
    assert!(
        a.actions(&e, 2, Mode::Normal, false, &AmbientFocus::default(), &s)
            .is_empty()
    );
}
#[test]
fn invalid_smell_shape_is_rejected_before_decision_and_null_is_unknown() {
    let bad = json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-09-25T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"smell_observation":{"status":"present","smell_id":"cat"}});
    assert!(GameEvent::parse(bad).is_err());
    assert_eq!(
        smell::speech(&event(json!({"smell_observation":null}))).cue_id,
        Some("smell_unsupported")
    );
}
#[test]
fn light_result_must_be_in_the_original_offered_actions() {
    let f = AmbientFocus {
        light: LightContext {
            surroundings_reasonably_lit: true,
            dark_push_context_before: true,
            dark_push_recovered: true,
            ..Default::default()
        },
        ..Default::default()
    };
    let mut a = Ambient::default();
    let (e, r) = light_request(&mut a, &f);
    assert_eq!(
        r.details["allowed_actions"],
        json!(["stay_silent", "relief_after_darkness"])
    );
    let payload = json!({"action":"acknowledge_supply_gain","basis_ids":["first_light_supply"],"confidence":1});
    let later = AmbientFocus {
        light: LightContext {
            surroundings_reasonably_lit: true,
            ..Default::default()
        },
        ..Default::default()
    };
    assert!(
        a.resolve_light_plan(
            r.request_id,
            &payload,
            &e,
            2000,
            &later,
            &Settings::default()
        )
        .is_none()
    );
}
