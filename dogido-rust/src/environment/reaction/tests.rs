use super::*;

fn attach(action: &mut Speech, event: &GameEvent, recent: &Value) {
    super::attach(action, event, recent, &Settings::default());
}

fn event(fields: Value) -> GameEvent {
    let mut value = json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":1,
        "observed_at":"2026-10-01T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld"},"world":{"biome":"forest","time_phase":"day","sky_visible":true}});
    value
        .as_object_mut()
        .unwrap()
        .extend(fields.as_object().unwrap().clone());
    GameEvent::parse(value).unwrap()
}
fn smell(id: &str, category: &str) -> GameEvent {
    event(
        json!({"smell_observation":{"status":"present","components":[{"smell_id":id,"category":category,"valence":"pleasant","source_kind":"hotbar","effective_strength":3}],"basis":"smell_policy_v2"}}),
    )
}
fn action(kind: &'static str, details: Value) -> Speech {
    let mut action = Speech::new(kind, "ちょっと気になるわ。");
    action.delivery = Delivery::Ambient;
    action.leaf = Some(LeafRequest {
        kind: kind.into(),
        details,
        temperature: 0.5,
    });
    action
}
fn prepared(action: &Speech) -> Prepared {
    Prepared::new(&json!({"kind":KIND,"model":"fixture","max_tokens":2048,"details":action.leaf.as_ref().unwrap().details,"fallback_text":action.text}),"fixture").unwrap()
}
fn generated(value: Value) -> GeneratedText {
    GeneratedText {
        text: value.to_string(),
        finish_reason: Some("stop".into()),
        completion_tokens: Some(20),
        prompt_tokens: None,
    }
}
fn outcome(prepared: &Prepared, speech: &str) -> (String, &'static str) {
    prepared.finish(Some(&generated(json!({"action":"speak","speech":speech}))))
}

#[test]
fn model_expression_is_not_limited_by_a_small_model_word_filter_or_character_ceiling() {
    let mut a = action("occluded_entry_no_light", json!({}));
    attach(&mut a, &event(json!({})), &json!({}));
    let p = prepared(&a);
    let text = "木々の間を歩いてると、さっき話してたブタのことを思い出すな。オレは暗いところがちょっと苦手やけど、こうして一緒に景色を眺めるのは好きやで。あの話の続き、気が向いたらまた聞かせてな。";
    assert!(text.chars().count() > 80);
    assert_eq!(outcome(&p, text), (text.into(), "speak"));
}

#[test]
fn silence_is_distinct_from_error_and_whole_contract_is_required() {
    let mut a = action("occluded_entry_no_light", json!({}));
    attach(&mut a, &event(json!({})), &json!({}));
    let p = prepared(&a);
    assert_eq!(
        p.finish(Some(&generated(json!({"action":"silent","speech":""})))),
        (String::new(), "silent")
    );
    for (body, expected) in [
        (
            json!({"action":"silent","speech":""}),
            (String::new(), "silent"),
        ),
        (
            json!({"action":"speak","speech":"そやな。"}),
            ("そやな。".into(), "speak"),
        ),
    ] {
        let mut fenced = generated(body);
        fenced.text = format!("```json\n{}\n```", fenced.text);
        assert_eq!(p.finish(Some(&fenced)), expected);
    }
    for value in [
        json!({"action":"silent","speech":"黙るで"}),
        json!({"action":"move","speech":""}),
        json!({"action":"speak","speech":"そうやな。","save":true}),
        json!({"result":{"action":"silent","speech":""}}),
    ] {
        assert_eq!(p.finish(Some(&generated(value))).1, "invalid_contract");
    }
    let mut raw = generated(json!({"action":"silent","speech":""}));
    raw.finish_reason = Some("length".into());
    assert_eq!(p.finish(Some(&raw)).1, "truncated_output");
    raw.finish_reason = Some("stop".into());
    raw.text = "{\"outer\": {\"action\":\"silent\",\"speech\":\"\"}".into();
    assert_eq!(p.finish(Some(&raw)).1, "invalid_contract");
    assert_eq!(p.finish(None), (a.text.clone(), "generation_error"));
    for text in [
        "",
        "壊れた\u{0}出力や",
        "そうやな。そうやな。そうやな。",
        &"長い話やな。".repeat(20),
    ] {
        assert_eq!(outcome(&p, text).1, "broken_output");
    }
    for text in [
        "うん。",
        "おお！",
        "Minecraftのウシ、かわいいな。",
        "怖い怖い怖い、でも一緒なら大丈夫や。",
        "そうかな？オレは違う見方もできると思うで。",
    ] {
        assert_eq!(outcome(&p, text), (text.into(), "speak"));
    }
}

#[test]
fn smell_context_preserves_components_and_strength_without_inventing_spatial_information() {
    let e = smell("bread", "food");
    let mut a = super::super::ambient::current_smell_reply(&e);
    a.delivery = Delivery::Ambient;
    attach(&mut a, &e, &json!({}));
    assert!(a.cue_id.is_none());
    let p = prepared(&a);
    let parsed: Value = serde_json::from_str(&p.request.messages[1].content).unwrap();
    let context = parsed["observations"]["smell"].to_string();
    assert!(
        context.contains("effective_strength")
            && context.contains("source_kind")
            && !context.contains("distance")
    );
    assert_eq!(
        outcome(&p, "ええ匂いやな。なんやお腹がすいてきたわ。").1,
        "speak"
    );
    for text in [
        "パンの話してたらお腹すいてきたわ。",
        "パンの匂いの話、覚えとるで。",
        "パンの匂いかな？",
    ] {
        assert_eq!(outcome(&p, text).1, "speak", "{text}");
    }
    let source = smell("bread", "food");
    let mut a = action("smell", json!({}));
    attach(&mut a, &source, &json!({}));
    assert_eq!(
        outcome(&prepared(&a), "パンの匂いでお腹がすいてきたわ。").1,
        "speak"
    );
}

#[test]
fn smell_and_biome_guards_are_not_generated_wording() {
    let e = smell("bread", "food");
    let mut a = action("smell", json!({}));
    attach(&mut a, &e, &json!({}));
    a.text = "ええ匂いやな。".into();
    assert!(super::super::ambient::still_applicable(&a, &e));
    assert!(!super::super::ambient::still_applicable(
        &a,
        &smell("cake", "food")
    ));
    assert!(!super::super::ambient::still_applicable(
        &a,
        &event(
            json!({"smell_observation":{"status":"none","components":[],"basis":"smell_policy_v2"}})
        )
    ));

    let e = event(json!({}));
    let mut a = action("occluded_entry_no_light", json!({}));
    a.text = "夜にわいた敵が残っとるんやで。".into();
    attach(&mut a, &e, &json!({}));
    assert!(!a.text.contains("敵"));
    let p = prepared(&a);
    assert!(!p.request.messages[1].content.contains("夜にわいた"));
    a.text = "木陰は気になるな。".into();
    assert!(super::super::ambient::still_applicable(&a, &e));
    for change in [
        json!({"world":{"biome":"taiga","time_phase":"day"}}),
        json!({"world":{"biome":"forest","time_phase":"night"}}),
        json!({"world":{"biome":"forest","time_phase":"day","sky_visible":false,"overhead_cover_type":"stone"}}),
        json!({"world":{"biome":"forest","time_phase":"day","sky_visible":true,"overhead_cover_type":"foliage"}}),
        json!({"player":{"dimension":"minecraft:the_nether"}}),
    ] {
        assert!(!super::super::ambient::still_applicable(&a, &event(change)));
    }
    assert_eq!(
        outcome(&p, "木陰には敵がおるかもしれんな。気ぃつけよ。").1,
        "speak"
    );
    for text in [
        "敵がおるんかな。",
        "敵がおるなら気ぃつけなな。",
        "『敵がおる』ってさっき話してたな。",
    ] {
        assert_eq!(outcome(&p, text).1, "speak", "{text}");
    }
}

fn measured_smell(strength: i64) -> GameEvent {
    event(
        json!({"smell_observation":{"status":"present","components":[{"smell_id":"zombie","category":"decay","valence":"unpleasant","source_kind":"entity","effective_strength":strength}],"basis":"smell_policy_v2"}}),
    )
}

#[test]
fn resolved_strength_reaches_the_model_and_change_invalidates_old_speech() {
    let e = measured_smell(5);
    let mut a = action("smell", json!({}));
    attach(&mut a, &e, &json!({}));
    let p = prepared(&a);
    let context: Value = serde_json::from_str(&p.request.messages[1].content).unwrap();
    let observed_smell = &context["observations"]["smell"]["components"][0];
    assert_eq!(observed_smell["effective_strength"], 5);
    for hidden in [
        "direction_estimate",
        "distance_estimate",
        "source_id",
        "entity_id",
        "position",
    ] {
        assert!(observed_smell.get(hidden).is_none());
    }
    assert!(still_applicable(&a, &e));
    assert!(!still_applicable(&a, &measured_smell(8)));
    assert!(only_smell_estimate_changed(&a, &measured_smell(8)));
    assert!(!only_smell_estimate_changed(&a, &e));
    assert!(!only_smell_estimate_changed(&a, &smell("bread", "food")));
    assert!(!only_smell_estimate_changed(
        &a,
        &event(
            json!({"smell_observation":{"status":"none","components":[],"basis":"smell_policy_v2"}})
        )
    ));
    let mut elsewhere = serde_json::to_value(measured_smell(8)).unwrap();
    elsewhere["player"]["dimension"] = json!("minecraft:the_nether");
    assert!(!only_smell_estimate_changed(
        &a,
        &GameEvent::parse(elsewhere).unwrap()
    ));
}

#[test]
fn fixed_smell_questions_use_current_strength_and_cannot_locate_the_source() {
    let e = measured_smell(5);
    for text in [
        "匂いはどっちから？",
        "どこから匂う？",
        "匂いの距離はどのくらい？",
    ] {
        assert!(super::super::ambient::is_smell_query(text));
    }
    let reply = super::super::ambient::current_smell_query_reply(&e, "匂いはどっちから？");
    assert!(!reply.text.contains("ブロック"));
    assert!(reply.text.contains("匂いだけでは方向までは分からへん"));
    assert!(reply.cue_id.is_none());
    assert!(super::super::ambient::still_applicable(&reply, &e));
    let stronger = measured_smell(8);
    assert!(!super::super::ambient::still_applicable(&reply, &stronger));
    let current = super::super::ambient::current_smell_query_reply(&stronger, "匂いはどっちから？");
    assert!(current.text.contains("強烈に"));
    assert!(current.text.contains("匂いだけでは方向までは分からへん"));
    assert!(super::super::ambient::still_applicable(&current, &stronger));
    let range_question =
        super::super::ambient::current_smell_query_reply(&stronger, "匂いの距離はどのくらい？");
    assert!(range_question.text.contains("距離までは分からへん"));
    assert!(!range_question.text.contains("ブロック"));
    assert!(super::super::ambient::still_applicable(
        &range_question,
        &stronger
    ));
    assert!(!super::super::ambient::is_smell_query(
        "川柳の匂いの表現はどっち？"
    ));
}

#[test]
fn underground_forest_is_a_location_not_a_visible_surface_scene() {
    let e = event(
        json!({"world":{"biome":"forest","sky_visible":false,"overhead_cover_type":"stone","time_phase":"day"}}),
    );
    let mut a = action("occluded_entry_no_light", json!({}));
    attach(&mut a, &e, &json!({}));
    let p = prepared(&a);
    assert_eq!(
        serde_json::from_str::<Value>(&p.request.messages[1].content).unwrap()["observations"]["location_biome"],
        "森林"
    );
    assert_eq!(
        serde_json::from_str::<Value>(&p.request.messages[1].content).unwrap()["observations"]["biome_scene_visible"],
        false
    );
    assert_eq!(
        serde_json::from_str::<Value>(&p.request.messages[1].content).unwrap()["observations"]["overhead_cover"],
        "stone"
    );
}

#[test]
fn thunder_heard_is_not_a_nearby_strike_and_weather_is_not_thunder_heard() {
    let e = event(
        json!({"world":{"biome":"plains","weather":"thunder","sky_visible":true,"thunder_sound_recent_ms":0}}),
    );
    let mut a = action(
        "thunder_reaction",
        json!({"scene":"thunder_heard","thunder_reaction":true,"nearby_lightning":false}),
    );
    attach(&mut a, &e, &json!({}));
    let p = prepared(&a);
    assert_eq!(outcome(&p, "雷の音は何度聞いても落ち着かんな。").1, "speak");
    assert_eq!(outcome(&p, "雷が落ちたら怖いな。").1, "speak");
    assert!(super::super::ambient::still_applicable(&a, &e));
}

#[test]
fn strike_without_sound_does_not_invent_heard_thunder() {
    let e = event(
        json!({"world":{"biome":"plains","weather":"thunder","sky_visible":true,
        "nearby_lightning_strike_recent_ms":0,"nearby_lightning_strike_distance":4}}),
    );
    let mut a = action(
        "thunder_reaction",
        json!({"scene":"nearby_lightning_strike","thunder_reaction":true,"nearby_lightning":true}),
    );
    attach(&mut a, &e, &json!({}));
    let p = prepared(&a);
    assert_eq!(
        serde_json::from_str::<Value>(&p.request.messages[1].content).unwrap()["observations"]["thunder_heard"],
        false
    );
    assert_eq!(
        serde_json::from_str::<Value>(&p.request.messages[1].content).unwrap()["observations"]["nearby_lightning_observed"],
        true
    );
    assert_eq!(outcome(&p, "近くに落ちたな、びっくりしたわ。").1, "speak");
    assert_eq!(outcome(&p, "雷が鳴ったらびっくりするわ。").1, "speak");
}

#[test]
fn final_environment_input_uses_shared_weather_situations_and_actual_scream_state() {
    let e = event(
        json!({"world":{"biome":"plains","weather":"thunder","sky_visible":true,"thunder_sound_recent_ms":0}}),
    );
    for state in ["scheduled", "completed", "not_scheduled"] {
        let mut thunder = action(
            "thunder_reaction",
            json!({"scene":"thunder_heard","scream_status":state}),
        );
        attach(&mut thunder, &e, &json!({}));
        let request = prepared(&thunder).request;
        let context: Value = serde_json::from_str(&request.messages[1].content).unwrap();
        assert_eq!(context["event"], "thunder_reaction");
        assert_eq!(context["situation"], "雷鳴が聞こえた。");
        assert_eq!(context["observations"]["thunder_heard"], true);
        assert_eq!(context["self_state"]["scream_status"], state);
        assert_eq!(
            context["self_state"]["situation"]
                .as_str()
                .unwrap()
                .contains("直後"),
            state == "completed"
        );
    }
}

#[test]
fn sensory_questions_combat_and_severe_darkness_keep_the_original_path() {
    let e = smell("bread", "food");
    for delivery in [Delivery::PlayerReply, Delivery::Combat] {
        let mut a = super::super::ambient::current_smell_reply(&e);
        a.delivery = delivery;
        let before = serde_json::to_value(&a).unwrap();
        attach(&mut a, &e, &json!({}));
        assert_eq!(serde_json::to_value(&a).unwrap(), before);
    }
    for kind in [
        "dark_push_no_light",
        "dark_push_breath",
        "night_warning_surface",
        "thunder_cue",
        "damaging_light",
        "aftermath",
    ] {
        let mut a = action(kind, json!({}));
        let before = serde_json::to_value(&a).unwrap();
        attach(&mut a, &e, &json!({}));
        assert_eq!(serde_json::to_value(&a).unwrap(), before, "{kind}");
    }
}

#[test]
fn mixed_components_reach_model_and_fixed_answer_without_order_dependent_guards() {
    let first = json!({"smell_id":"zombie","category":"decay","valence":"unpleasant",
        "source_kind":"entity","effective_strength":1});
    let second = json!({"smell_id":"soup","category":"food","valence":"pleasant",
        "source_kind":"hotbar","effective_strength":3});
    let mixed = |rows: Value| {
        event(json!({"smell_observation":{
        "status":"present","basis":"smell_policy_v2","components":rows}}))
    };
    let e = mixed(json!([first.clone(), second.clone()]));
    let mut a = action("smell", json!({}));
    attach(&mut a, &e, &json!({}));
    let p = prepared(&a);
    let context: Value = serde_json::from_str(&p.request.messages[1].content).unwrap();
    let components = &context["observations"]["smell"]["components"];
    assert_eq!(components[0]["smell_id"], "soup");
    assert_eq!(components[0]["effective_strength"], 3);
    assert_eq!(components[1]["smell_id"], "zombie");
    assert_eq!(components[1]["effective_strength"], 1);
    assert!(still_applicable(&a, &mixed(json!([second, first]))));
    let answer = super::super::ambient::current_smell_reply(&e);
    assert!(answer.text.contains("スープの匂い"));
    assert!(answer.text.contains("ゾンビ系の匂いがほのかに"));
    assert!(answer.cue_id.is_none());
    assert!(!answer.text.contains("近くにおる"));
    let directional =
        super::super::ambient::current_smell_query_reply(&e, "スープの匂いはどっちから？");
    assert!(!directional.text.contains("東"));
    assert!(
        directional
            .text
            .contains("匂いだけでは方向までは分からへん")
    );
    assert!(super::super::ambient::still_applicable(&directional, &e));
}

#[test]
fn rain_after_reaction_uses_one_candidate_and_expires_with_its_window() {
    let e = event(
        json!({"smell_observation":{"status":"present","basis":"smell_policy_v2",
            "rain_after_active":true,"components":[{"smell_id":"bread","category":"food",
                "valence":"pleasant","source_kind":"hotbar","effective_strength":3}],
            "rain_after_notice":{"window_id":12,"component":{"smell_id":"rain_after",
                "category":"rain_after","valence":"pleasant","source_kind":"hotbar","effective_strength":2}}
        }}),
    );
    let mut a = Speech::new("smell", "雨上がりの匂いがするで。");
    a.delivery = Delivery::Ambient;
    a.rain_after_window = Some(12);
    attach(&mut a, &e, &json!({}));
    assert_eq!(
        a.leaf.as_ref().unwrap().details["reaction_context"]["observations"]["smell"]["components"]
            [0]["smell_id"],
        "rain_after"
    );
    assert!(still_applicable(&a, &e));
    let mut changed = serde_json::to_value(&e).unwrap();
    changed["smell_observation"]["components"] = json!([]);
    changed["smell_observation"]["status"] = json!("none");
    assert!(still_applicable(
        &a,
        &GameEvent::parse(changed.clone()).unwrap()
    )); // 通常臭の変化で一瞬の気づきを取り消さない。
    changed["smell_observation"]["rain_after_notice"]["window_id"] = json!(13);
    assert!(!still_applicable(&a, &GameEvent::parse(changed).unwrap()));
}

#[test]
fn unified_contract_keeps_valid_speech_independent_of_invalid_body_action() {
    for body in [json!({"action":"invented","target_id":"wrong"}),json!(23),Value::Null] {
        let raw=json!({"action":"speak","speech":"そやな。","body_action":body}).to_string();
        let (speech, action)=parse_reply(&raw,true).unwrap();
        assert_eq!(speech,crate::speech_choice::Choice::Speak("そやな。".into()));
        assert_eq!(action,Some(body));
        assert!(parse_reply(&raw,false).is_err());
    }
}

#[test]
fn unified_contract_allows_silent_movement_without_repairing_invalid_speech() {
    let body=json!({"action":"owner_near","target_id":null});
    let (speech,action)=parse_reply(&json!({"action":"silent","speech":"","body_action":body}).to_string(),true).unwrap();
    assert_eq!(speech,crate::speech_choice::Choice::Silent);
    assert_eq!(action,Some(body.clone()));
    for invalid in [json!({"action":"silent","speech":"話す","body_action":body}),
        json!({"action":"speak","speech":"","body_action":body}),
        json!({"action":"silent","speech":"","body_action":body,"tool":"invented"})] {
        assert!(parse_reply(&invalid.to_string(),true).is_err());
    }
}
