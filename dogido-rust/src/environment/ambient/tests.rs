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
fn light_response_is_consumed_once_without_another_leaf() {
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
    let payload = json!({"action":"acknowledge_supply_gain","speech":"備えが増えたな。"});
    assert!(
        a.resolve_light_plan(r.request_id + 1, &payload, &e, 2, &f, &Settings::default())
            .is_none()
    );
    let line = a
        .resolve_light_plan(r.request_id, &payload, &e, 2, &f, &Settings::default())
        .unwrap();
    assert_eq!(line.kind, "light_source_gain");
    assert_eq!(line.text, "備えが増えたな。");
    assert!(line.leaf.is_none());
    assert!(still_applicable(&line, &e));
    assert!(!still_applicable(
        &line,
        &event(json!({"inventory":{"torch":2}}))
    ));
    assert!(still_applicable(
        &line,
        &event(json!({"inventory":{"torch":1},
        "world":{"biome":"desert","time_phase":"day"}}))
    ));
    assert!(
        a.resolve_light_plan(r.request_id, &payload, &e, 3, &f, &Settings::default())
            .is_none()
    );
}
#[test]
fn light_comment_survives_ordinary_lighting_and_scene_changes() {
    let payload = json!({"action":"acknowledge_supply_gain","speech":"備えが増えたな。"});
    for (previous, current, change) in (0..4).flat_map(|change| [(0, 1, change), (1, 2, change)]) {
        let mut a = Ambient::default();
        let mut focus = AmbientFocus::default();
        let settings = Settings::default();
        a.update(&event(json!({"inventory":{"torch":previous}})), 0, true, &settings);
        let mut e = event(json!({"inventory":{"torch":current}}));
        a.update(&e, 1, true, &settings);
        a.actions(&e, 1, Mode::Normal, false, &focus, &settings);
        let request = a.take_light_plan().unwrap();
        match change {
            0 => focus.light.surroundings_reasonably_lit = true,
            1 => focus.light.nearby_light_present = true,
            2 => {
                e = event(
                    json!({"inventory":{"torch":current},"world":{"biome":"desert","time_phase":"day"}}),
                )
            }
            _ => {
                e = event(
                    json!({"inventory":{"torch":current},"world":{"biome":"plains","time_phase":"night"}}),
                )
            }
        }
        // 備えのコメントは通常の移動・時間帯・照明の変化でも配送する。危険な暗さは別試験。
        assert!(
            a.resolve_light_plan(
                request.request_id,
                &payload,
                &e,
                2,
                &focus,
                &Settings::default()
            )
            .is_some(),
            "change {change}"
        );
    }
}
#[test]
fn light_generated_for_an_old_body_epoch_is_discarded() {
    let with_epoch = |e: &GameEvent, epoch: &str| {
        e.with_body_senses(
            None,
            crate::body::senses::Origin {
                context: Some(crate::body::contracts::Context {
                    owner_id: "owner".into(),
                    body_id: "body".into(),
                    epoch: epoch.into(),
                    dimension: "minecraft:overworld".into(),
                }),
                revision: Some(1),
                position: None,
                available: true,
                age_ms: 0,
                body: json!({}),
                player_safety: e.world.clone(),
                player_observed_at: e.observed_at.clone(),
            },
        )
    };
    for next_epoch in ["old", "new"] {
        let mut a = Ambient::default();
        let s = Settings::default();
        let f = AmbientFocus::default();
        let old = with_epoch(&event(json!({})), "old");
        let supplied = event(json!({"inventory":{"torch":1}}));
        let current = with_epoch(&supplied, "old");
        a.update(&old, 0, true, &s);
        a.update(&current, 1, true, &s);
        a.actions(&current, 1, Mode::Normal, false, &f, &s);
        let request = a.take_light_plan().unwrap();
        let later = with_epoch(&supplied, next_epoch);
        let response = a.resolve_light_plan(
            request.request_id,
            &json!({"action":"acknowledge_supply_gain","speech":"備えが増えたな。"}),
            &later,
            2,
            &f,
            &s,
        );
        assert_eq!(response.is_some(), next_epoch == "old");
    }
}

#[test]
fn light_invalid_action_or_speech_is_silent() {
    let f = AmbientFocus {
        light: LightContext {
            surroundings_reasonably_lit: true,
            ..Default::default()
        },
        ..Default::default()
    };
    for payload in [
        Value::Null,
        json!({"action":"relief_after_darkness","speech":"明るくなってほっとしたわ。"}),
        json!({"action":"acknowledge_supply_gain","speech":""}),
        json!({"action":"acknowledge_supply_gain","speech":3}),
        json!({"action":"stay_silent","speech":"話すで。"}),
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
    let payload = json!({"action":"relief_after_darkness","speech":"明るくなってほっとしたわ。"});
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
        json!({"smell_observation":{"status":"present","components":[{"smell_id":"zombie","category":"decay","valence":"unpleasant","source_kind":"entity","effective_strength":8}],"basis":"smell_policy_v2"}}),
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
        None
    );
    assert!(
        a.actions(&e, 2, Mode::Normal, false, &AmbientFocus::default(), &s)
            .is_empty()
    );
}
#[test]
fn invalid_smell_shape_is_rejected_before_decision_and_null_is_unknown() {
    let bad = json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-09-25T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"smell_observation":{"status":"present","components":[{"smell_id":"cat"}],"basis":"smell_policy_v2"}});
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
    let payload = json!({"action":"acknowledge_supply_gain","speech":"備えが増えたな。"});
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

#[test]
fn v2_smell_contract_rejects_old_or_inconsistent_observations() {
    let parse = |observation: Value| {
        GameEvent::parse(json!({
        "schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-10-08T00:00:00Z",
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "smell_observation":observation}))
    };
    let component = json!({"smell_id":"bread","category":"food","valence":"pleasant",
        "source_kind":"hotbar","effective_strength":3});
    let present =
        json!({"status":"present","basis":"smell_policy_v2","components":[component.clone()]});
    assert!(parse(present.clone()).is_ok());
    for status in ["none", "suppressed"] {
        let mut observation = json!({"status":status,"basis":"smell_policy_v2","components":[]});
        if status == "suppressed" {
            observation["suppression_reason"] = json!("rain");
        }
        assert!(parse(observation.clone()).is_ok());
        observation["components"] = json!([component.clone()]);
        assert!(parse(observation).is_err());
    }
    for observation in [
        json!({"status":"none"}),
        json!({"status":"present","smell_id":"bread","category":"food","valence":"pleasant",
            "source_kind":"hotbar","specificity":"source","effective_strength":3,"basis":"smell_policy_v1"}),
        json!({"status":"none","components":[],"basis":"smell_policy_v1"}),
        json!({"status":"present","basis":"smell_policy_v2","components":[]}),
        json!({"status":"present","basis":"smell_policy_v2","components":[component.clone(),component.clone()]}),
        json!({"status":"suppressed","basis":"smell_policy_v2","components":[]}),
    ] {
        assert!(parse(observation.clone()).is_err(), "{observation}");
    }
    for key in [
        "smell_id",
        "category",
        "valence",
        "source_kind",
        "specificity",
        "effective_strength",
        "direction_estimate",
    ] {
        let mut observation = present.clone();
        observation[key] = Value::Null;
        assert!(parse(observation).is_err(), "legacy field {key}");
    }
    for key in [
        "smell_id",
        "category",
        "valence",
        "source_kind",
        "effective_strength",
    ] {
        let mut observation = present.clone();
        observation["components"][0]
            .as_object_mut()
            .unwrap()
            .remove(key);
        assert!(parse(observation).is_err(), "missing component field {key}");
    }
    for strength in [0, 13] {
        let mut observation = present.clone();
        observation["components"][0]["effective_strength"] = json!(strength);
        assert!(parse(observation).is_err());
    }
    for (key, value) in [
        ("direction_estimate", json!({"cardinal":"east"})),
        ("specificity", json!("source")),
        ("entity_id", json!("private")),
        ("category", json!("decay")),
        ("valence", json!("unpleasant")),
    ] {
        let mut observation = present.clone();
        observation["components"][0][key] = value;
        assert!(parse(observation).is_err(), "component field {key}");
    }
}

#[test]
fn smell_components_reject_removed_direction_even_on_external_sources() {
    for direction in [json!({"cardinal":"east"}), Value::Null] {
        let mut value = serde_json::to_value(rain_after_event(1)).unwrap();
        value["smell_observation"]["components"][0]["direction_estimate"] = direction;
        assert!(GameEvent::parse(value).is_err());
    }
}

fn rain_after_event(window: u64) -> GameEvent {
    event(
        json!({"smell_observation":{"status":"present","basis":"smell_policy_v2",
            "rain_after_active":true,
            "components":[{"smell_id":"zombie","category":"decay","valence":"unpleasant",
                "source_kind":"entity","effective_strength":8}],
            "rain_after_notice":{"window_id":window,"component":{"smell_id":"rain_after",
                "category":"rain_after","valence":"pleasant","source_kind":"hotbar","effective_strength":2}}
        }}),
    )
}

#[test]
fn rain_after_notice_accepts_ground_and_hotbar_without_replacing_normal_components() {
    let e = rain_after_event(1);
    let base = serde_json::to_value(&e).unwrap();
    for source in ["block", "hotbar"] {
        let mut value = base.clone();
        value["smell_observation"]["rain_after_notice"]["component"]["source_kind"] = json!(source);
        assert!(GameEvent::parse(value.clone()).is_ok());
        value["smell_observation"]["rain_after_notice"]["component"]["direction_estimate"] =
            json!({"cardinal":"east","basis":"source_bearing"});
        assert!(GameEvent::parse(value).is_err());
    }
    for source in ["entity", "dropped_item", "biome"] {
        let mut value = base.clone();
        value["smell_observation"]["rain_after_notice"]["component"]["source_kind"] = json!(source);
        assert!(GameEvent::parse(value).is_err());
    }
    // 窓IDは正のJSON整数のみ。文字列やboolを同じ窓へ暗黙変換しない。
    for window in [json!(-1), json!("1"), json!(true), json!(1.5)] {
        let mut value = base.clone();
        value["smell_observation"]["rain_after_notice"]["window_id"] = window;
        assert!(GameEvent::parse(value).is_err());
    }
    let mut no_normal = base.clone();
    no_normal["smell_observation"]["status"] = json!("none");
    no_normal["smell_observation"]["components"] = json!([]);
    assert!(GameEvent::parse(no_normal.clone()).is_ok());
    no_normal["smell_observation"]["status"] = json!("suppressed");
    no_normal["smell_observation"]["suppression_reason"] = json!("submerged");
    assert!(GameEvent::parse(no_normal).is_err());
    for bad in [
        "inactive",
        "zero_window",
        "wrong_id",
        "range",
        "old_components",
    ] {
        let mut value = base.clone();
        match bad {
            "inactive" => value["smell_observation"]["rain_after_active"] = json!(false),
            "zero_window" => {
                value["smell_observation"]["rain_after_notice"]["window_id"] = json!(0)
            }
            "wrong_id" => {
                value["smell_observation"]["rain_after_notice"]["component"]["smell_id"] =
                    json!("bread")
            }
            "range" => {
                value["smell_observation"]["rain_after_notice"]["component"]["effective_strength"] =
                    json!(13)
            }
            _ => {
                value["smell_observation"]["components"] =
                    json!([value["smell_observation"]["rain_after_notice"]["component"].clone()]);
                value["smell_observation"]
                    .as_object_mut()
                    .unwrap()
                    .remove("rain_after_notice");
            }
        }
        assert!(GameEvent::parse(value).is_err(), "{bad}");
    }
}

#[test]
fn one_rain_after_output_returns_immediately_to_normal_smell_without_consuming_its_cooldown() {
    let e = rain_after_event(1);
    let settings = Settings::default();
    let mut a = Ambient::default();
    a.update(&e, 0, true, &settings);
    a.update(&e, 1, true, &settings);
    let first = a
        .actions(
            &e,
            1,
            Mode::Normal,
            false,
            &AmbientFocus::default(),
            &settings,
        )
        .remove(0);
    assert_eq!(first.rain_after_window, Some(1));
    assert!(first.text.contains("雨上がり"));
    assert!(!first.text.contains("ゾンビ"));
    // 通常の共有観測は残っており、一言後に次snapshotを待たず参照できる。
    assert_eq!(
        smell_context(&e).unwrap()["components"][0]["smell_id"],
        "zombie"
    );
    a.end_rain_after_attention(1);
    let next = a
        .priority_smell(&e, 2, Mode::Normal, false, &settings)
        .unwrap();
    assert_eq!(next.rain_after_window, None);
    assert!(next.text.contains("ゾンビ"));
    assert_eq!(a.smell_query_reply(&e, "匂いは？").rain_after_window, None);
    a.update(&e, 3, true, &settings);
    assert!(
        a.priority_smell(&e, 3, Mode::Normal, false, &settings)
            .is_none()
    );
    let next_rain = rain_after_event(2);
    a.update(&next_rain, 4, true, &settings);
    a.end_rain_after_attention(1); // 前の窓の遅延完了は新しい窓を消費しない。
    assert_eq!(
        a.actions(
            &next_rain,
            4,
            Mode::Normal,
            false,
            &AmbientFocus::default(),
            &settings
        )[0]
        .rain_after_window,
        Some(2)
    );
}

#[test]
fn rain_after_busy_missing_source_and_submersion_do_not_rearm_a_finished_window() {
    let e = rain_after_event(1);
    let settings = Settings::default();
    let mut a = Ambient::default();
    a.update(&e, 0, true, &settings);
    assert!(
        a.actions(
            &e,
            1,
            Mode::Normal,
            true,
            &AmbientFocus::default(),
            &settings
        )
        .is_empty()
    );
    assert_eq!(
        a.smell_query_reply(&e, "匂いはどっち？").rain_after_window,
        Some(1)
    );
    a.end_rain_after_attention(1);
    let missing = event(json!({}));
    a.update(&missing, 2, false, &settings);
    let underwater = event(
        json!({"smell_observation":{"status":"suppressed","components":[],
        "basis":"smell_policy_v2","rain_after_active":true,"suppression_reason":"submerged"}}),
    );
    a.update(&underwater, 3, true, &settings);
    a.update(&e, 4, true, &settings);
    assert_eq!(a.smell_query_reply(&e, "匂いは？").rain_after_window, None);
}

#[test]
fn rain_after_respects_nonhostile_foreground_and_conversation_mute() {
    let e = rain_after_event(1);
    let settings = Settings::default();
    for focus in [
        AmbientFocus {
            foreground: true,
            ..Default::default()
        },
        AmbientFocus {
            foreground: true,
            casual_foreground: true,
            last_player_input_at: Some(900),
            ..Default::default()
        },
        AmbientFocus {
            boss_presence: true,
            ..Default::default()
        },
        AmbientFocus {
            ominous_presence: true,
            ..Default::default()
        },
    ] {
        let mut a = Ambient::default();
        a.update(&e, 0, true, &settings);
        assert!(
            a.actions(&e, 1000, Mode::Normal, false, &focus, &settings)
                .is_empty()
        );
        assert_eq!(
            a.smell_query_reply(&e, "匂いは？").rain_after_window,
            Some(1)
        );
    }
}
