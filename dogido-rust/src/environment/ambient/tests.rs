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
fn scent(status: &str, id: &str) -> GameEvent {
    let obs = if status == "present" {
        json!({"status":status,"smell_id":id,"category":"food","valence":"pleasant","source_kind":"hotbar","specificity":"source","effective_strength":3})
    } else {
        json!({"status":status})
    };
    event(json!({"smell_observation":obs}))
}
fn actions(a: &mut Ambient, e: &GameEvent, now: u64) -> Vec<Speech> {
    let s = defaults();
    a.update(e, now, true, &s);
    a.actions(e, now, Mode::Normal, false, &AmbientFocus::default(), &s)
}
#[test]
fn python_reference_cases_match() {
    let rows: Value = serde_json::from_str(include_str!("reference.json")).unwrap();
    for (n, row) in rows.as_array().unwrap().iter().enumerate() {
        let got = match row["op"].as_str().unwrap() {
            "scent_query" => json!(smell::is_query(row["text"].as_str().unwrap())),
            "smell" => {
                let e = GameEvent::parse(row["event"].clone()).unwrap();
                let s = smell::speech(&e);
                json!({"text":s.text,"cue_id":s.cue_id})
            }
            "mob_candidates" => {
                let e = GameEvent::parse(row["event"].clone()).unwrap();
                json!(mobs::fallback_candidates(&e, &e.passive_mobs[0]))
            }
            "villager_schedule" => {
                let e = event(
                    json!({"world":{"time_of_day":row["time"]},"passive_mobs":[{"type":"villager","profession":row["profession"],"is_baby":row["baby"]}]}),
                );
                json!(mobs::schedule(&e, &e.passive_mobs[0]))
            }
            "weather_scene" => json!(surroundings::weather_scene(
                row["from"].as_str().unwrap(),
                row["to"].as_str().unwrap(),
                row["cold"].as_bool().unwrap(),
                row["dry"].as_bool().unwrap()
            )),
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
            "smell_sequence" | "mob_sequence" => {
                let mut machine = Ambient::default();
                for step in row["steps"].as_array().unwrap() {
                    let e = GameEvent::parse(step["event"].clone()).unwrap();
                    let got = actions(&mut machine, &e, step["now"].as_u64().unwrap());
                    assert_eq!(
                        json!(got.iter().map(|s| &s.text).collect::<Vec<_>>()),
                        step["expected"],
                        "sequence {n}: {step}"
                    );
                }
                Value::Null
            }
            _ => panic!("unknown fixture"),
        };
        assert_eq!(got, row["expected"], "reference {n} {}", row["op"]);
    }
}
#[test]
fn smell_requires_two_stable_observations_and_two_absences() {
    let mut a = Ambient::default();
    let bread = scent("present", "bread");
    let none = scent("none", "");
    assert!(actions(&mut a, &bread, 0).is_empty());
    assert_eq!(actions(&mut a, &bread, 1000)[0].cue_id, Some("smell_bread"));
    assert!(actions(&mut a, &bread, 122000).is_empty());
    assert!(actions(&mut a, &none, 123000).is_empty());
    assert!(actions(&mut a, &bread, 124000).is_empty());
    actions(&mut a, &none, 125000);
    actions(&mut a, &none, 126000);
    assert!(actions(&mut a, &bread, 127000).is_empty());
    assert_eq!(actions(&mut a, &bread, 128000).len(), 1);
}
#[test]
fn smell_winner_change_global_cooldown_and_query_consumes_pending() {
    let mut a = Ambient::default();
    let bread = scent("present", "bread");
    let cake = scent("present", "cake");
    actions(&mut a, &bread, 0);
    actions(&mut a, &bread, 1000);
    actions(&mut a, &cake, 2000);
    assert!(actions(&mut a, &cake, 3000).is_empty());
    assert_eq!(actions(&mut a, &cake, 121000)[0].cue_id, Some("smell_cake"));
    let mut a = Ambient::default();
    a.update(&bread, 0, true, &defaults());
    let reply = a.smell_query(&bread, "この匂いなに？", 1).unwrap();
    assert_eq!(reply.cue_id, Some("smell_bread"));
    assert!(actions(&mut a, &bread, 150000).is_empty());
}
#[test]
fn partial_absence_does_not_reset_smell() {
    let mut a = Ambient::default();
    let s = defaults();
    let bread = scent("present", "bread");
    a.update(&bread, 0, true, &s);
    a.update(&event(json!({"event":{"name":"ambient_mob_detected","source_kind":"visual","priority_hint":"background","certainty":"high"}})),1,false,&s);
    assert_eq!(actions(&mut a, &bread, 2).len(), 1);
    assert!(!still_applicable(
        &smell::speech(&bread),
        &scent("none", "")
    ));
}
#[test]
fn explicit_none_overrides_legacy_and_old_adapter_is_immediate() {
    let mut a = Ambient::default();
    let e = event(
        json!({"zombie_scent_clues":[{"type":"zombie","entity_id":"z1","distance_band":"close","certainty":"medium"}]}),
    );
    assert_eq!(
        actions(&mut a, &e, 0)[0].cue_id,
        Some("zombie_scent_warning")
    );
    let e = event(
        json!({"smell_observation":{"status":"none"},"zombie_scent_clues":[{"type":"zombie","entity_id":"z1","distance_band":"close","certainty":"medium"}]}),
    );
    assert_eq!(smell::speech(&e).cue_id, Some("smell_none"));
}
#[test]
fn no_ambient_during_threat_busy_or_foreground() {
    let s = defaults();
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
        a.actions(&e, 1, Mode::Normal, false, &AmbientFocus::default(), &s)[0].kind,
        "ambient"
    );
}
#[test]
fn casual_foreground_mutes_mobs_for_thirty_seconds_only() {
    let s = defaults();
    let mut a = Ambient::default();
    let e = event(json!({"passive_mobs":[{"type":"cat"}]}));
    let f = AmbientFocus {
        foreground: true,
        casual_foreground: true,
        last_player_input_at: Some(0),
        ..Default::default()
    };
    a.update(&e, 0, true, &s);
    assert!(a.actions(&e, 29999, Mode::Normal, false, &f, &s).is_empty());
    assert_eq!(a.actions(&e, 30000, Mode::Normal, false, &f, &s).len(), 1);
}
#[test]
fn mob_cooldown_per_species_and_villager_crowd_sleep() {
    let mut a = Ambient::default();
    let cat = event(json!({"passive_mobs":[{"type":"cat"}]}));
    let cow = event(json!({"passive_mobs":[{"type":"cow"}]}));
    assert_eq!(actions(&mut a, &cat, 0).len(), 1);
    assert!(actions(&mut a, &cat, 30000).is_empty());
    assert_eq!(actions(&mut a, &cow, 30001).len(), 1);
    assert_eq!(actions(&mut a, &cat, 120000).len(), 1);
    let crowd = event(
        json!({"world":{"time_of_day":3000},"passive_mobs":[{"type":"villager","profession":"farmer","distance":3},{"type":"villager","profession":"cleric","distance":2},{"type":"villager","profession":"librarian","distance":1}]}),
    );
    let mut a = Ambient::default();
    let line = actions(&mut a, &crowd, 0).remove(0);
    let details = &line.leaf.unwrap().details;
    assert_eq!(details["mob"], "村人");
    assert_eq!(details["mob_count"], 1);
    assert!(details.get("mob_profession").is_none());
    let lone = event(
        json!({"world":{"time_of_day":3000},"passive_mobs":[{"type":"villager","profession":"armorer"}]}),
    );
    assert!(actions(&mut a, &lone, 1000).is_empty());
    let sleep = event(
        json!({"world":{"time_of_day":12000},"passive_mobs":[{"type":"villager","profession":"farmer"},{"type":"cat"}]}),
    );
    assert_eq!(
        actions(&mut a, &sleep, 2000)[0]
            .leaf
            .as_ref()
            .unwrap()
            .details["mob"],
        "ネコ"
    );
}
#[test]
fn mobs_do_not_use_recent_absent_species_and_thunder_surface_is_quiet() {
    let mut a = Ambient::default();
    let e = event(json!({"passive_mobs":[{"type":"cat"}]}));
    let line = actions(&mut a, &e, 0).remove(0);
    assert!(!still_applicable(&line, &event(json!({}))));
    let thunder = event(
        json!({"world":{"weather":"thunder","biome":"plains"},"passive_mobs":[{"type":"cow"}]}),
    );
    let mut a = Ambient::default();
    assert!(actions(&mut a, &thunder, 0).is_empty());
    let cave = event(
        json!({"world":{"weather":"thunder","biome":"deep_dark"},"passive_mobs":[{"type":"cow"}]}),
    );
    assert_eq!(actions(&mut a, &cave, 1000)[0].kind, "special_biome_entry");
    assert_eq!(actions(&mut a, &cave, 2000)[0].kind, "ambient");
}
fn light_request(a: &mut Ambient, f: &AmbientFocus) -> (GameEvent, LightPlanRequest) {
    let s = defaults();
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
        a.resolve_light_plan(r.request_id + 1, &payload, &e, 2, &f, &defaults())
            .is_none()
    );
    let line = a
        .resolve_light_plan(r.request_id, &payload, &e, 2, &f, &defaults())
        .unwrap();
    assert_eq!(line.kind, "light_source_gain");
    assert!(
        a.resolve_light_plan(r.request_id, &payload, &e, 3, &f, &defaults())
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
            a.resolve_light_plan(r.request_id, &payload, &e, 2, &f, &defaults())
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
        a.resolve_light_plan(r.request_id, &payload, &e, 3000, &later, &defaults())
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
        a.resolve_light_plan(r.request_id, &payload, &e, 3000, &dark, &defaults())
            .is_none()
    );
    let mut a = Ambient::default();
    let (e, r) = light_request(&mut a, &f);
    a.note_player_input(2);
    assert!(
        a.resolve_light_plan(r.request_id, &payload, &e, 3000, &later, &defaults())
            .is_none()
    );
}
#[test]
fn initial_inventory_and_abundant_safe_supply_do_not_request_model() {
    let mut a = Ambient::default();
    let s = defaults();
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
fn structure_beats_biome_and_entry_cooldowns_are_independent() {
    let mut a = Ambient::default();
    let e = event(json!({"world":{"biome":"deep_dark","structure":"minecraft:ancient_city"}}));
    let line = actions(&mut a, &e, 0).remove(0);
    assert_eq!(line.kind, "structure_entry");
    assert_eq!(line.leaf.as_ref().unwrap().details["biome"], "地下");
    assert!(actions(&mut a, &e, 1000).is_empty());
    let empty = event(json!({}));
    actions(&mut a, &empty, 2000);
    assert!(actions(&mut a, &e, 3000).is_empty());
    actions(&mut a, &empty, 600000);
    assert_eq!(actions(&mut a, &e, 601000)[0].kind, "structure_entry");
    assert!(!still_applicable(&line, &empty));
}
#[test]
fn firefly_night_latch_resets_and_eye_requires_actual_recent_event() {
    let mut a = Ambient::default();
    let e = event(
        json!({"world":{"biome":"plains","time_phase":"night","nearby_firefly_bush_count":1}}),
    );
    assert_eq!(actions(&mut a, &e, 0).len(), 2);
    assert!(actions(&mut a, &e, 1000).is_empty());
    actions(&mut a, &event(json!({})), 2000);
    assert_eq!(actions(&mut a, &e, 3000).len(), 2);
    let eye = event(json!({"world":{"biome":"plains","ender_eye_launch_recent_ms":2000}}));
    assert_eq!(actions(&mut a, &eye, 4000)[0].kind, "ender_eye_throw");
    assert!(actions(&mut a, &eye, 5000).is_empty());
    let stale = event(json!({"world":{"ender_eye_launch_recent_ms":2001}}));
    assert!(actions(&mut a, &stale, 13000).is_empty());
}
#[test]
fn weather_transition_uses_sky_or_recent_sound_and_can_be_cleared_by_thunder() {
    let mut a = Ambient::default();
    actions(&mut a, &event(json!({"world":{"weather":"clear"}})), 0);
    let rain =
        event(json!({"world":{"weather":"rain","sky_visible":false,"rain_sound_recent_ms":4000}}));
    assert_eq!(
        actions(&mut a, &rain, 1)[0].leaf.as_ref().unwrap().details["scene"],
        "rain_suspected"
    );
    let mut a = Ambient::default();
    let s = defaults();
    a.update(&event(json!({"world":{"weather":"clear"}})), 0, true, &s);
    a.update(&rain, 1, true, &s);
    a.clear_weather_transition();
    assert!(
        a.actions(&rain, 1, Mode::Normal, false, &AmbientFocus::default(), &s)
            .is_empty()
    );
}
#[test]
fn losing_clone_does_not_consume_mob_or_light_decision() {
    let mut a = Ambient::default();
    let s = defaults();
    let e = event(json!({"passive_mobs":[{"type":"cat"}]}));
    a.update(&e, 0, true, &s);
    let mut losing = a.clone();
    assert_eq!(
        losing
            .actions(&e, 0, Mode::Normal, false, &AmbientFocus::default(), &s)
            .len(),
        1
    );
    assert_eq!(
        a.actions(&e, 1, Mode::Normal, false, &AmbientFocus::default(), &s)
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
    let s = defaults();
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
        a.resolve_light_plan(r.request_id, &payload, &e, 2000, &later, &defaults())
            .is_none()
    );
}
