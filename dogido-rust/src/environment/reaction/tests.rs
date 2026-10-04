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
fn smell(id: &str, category: &str, specificity: &str) -> GameEvent {
    event(
        json!({"smell_observation":{"status":"present","smell_id":id,"category":category,
        "specificity":specificity,"valence":"pleasant","source_kind":if specificity == "source" { "hotbar" } else { "mixed" },"effective_strength":3}}),
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
fn candidate_has_one_decision_and_only_selected_facts_and_delivered_context() {
    let mut a = action(
        "ambient",
        json!({"mob":"ウシ","direction":"右","distance":2,"mob_temperament":"passive",
        "mob_count":7,"fallback_candidates":["古い固定台詞"],"mob_tags":["走る"],"villager_schedule":"work",
        "__ambient_guard":{"mob_type":"cow","entity_id":"private-id"}}),
    );
    attach(
        &mut a,
        &event(json!({})),
        &json!({"completed_conversation":[{"player_text":"のんびり歩こか","dogido_text":"せやな。"}]}),
    );
    let p = prepared(&a);
    assert_eq!(p.request.kind, KIND);
    assert_eq!(p.request.max_tokens, 2048);
    assert_eq!(p.request.messages.len(), 2);
    let content = &p.request.messages[1].content;
    assert!(content.contains("passive"));
    assert!(content.contains("ウシ") && content.contains("のんびり歩こか"));
    for hidden in [
        "private-id",
        "古い固定台詞",
        "走る",
        "villager_schedule",
        "mob_count",
        "effective_strength",
    ] {
        assert!(!content.contains(hidden), "{hidden}");
    }
    assert_eq!(
        outcome(&p, "ウシものんびりしとるように見えるな。気持ちよさそうや。").1,
        "speak"
    );
    assert_eq!(outcome(&p, "さっきのブタの話を思い出したわ。").1, "speak");
    assert_eq!(outcome(&p, "ブタがおるんかな。").1, "speak");
}

#[test]
fn model_expression_is_not_limited_by_a_small_model_word_filter_or_character_ceiling() {
    let mut a = action("special_biome_entry", json!({}));
    attach(&mut a, &event(json!({})), &json!({}));
    let p = prepared(&a);
    let text = "木々の間を歩いてると、さっき話してたブタのことを思い出すな。オレは暗いところがちょっと苦手やけど、こうして一緒に景色を眺めるのは好きやで。あの話の続き、気が向いたらまた聞かせてな。";
    assert!(text.chars().count() > 80);
    assert_eq!(outcome(&p, text), (text.into(), "speak"));
}

#[test]
fn silence_is_distinct_from_error_and_whole_contract_is_required() {
    let mut a = action("special_biome_entry", json!({}));
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
fn smell_context_preserves_sensor_specificity_without_inventing_spatial_information() {
    let e = smell("food", "food", "category");
    let mut a = super::super::ambient::current_smell_reply(&e);
    a.delivery = Delivery::Ambient;
    attach(&mut a, &e, &json!({}));
    assert!(a.cue_id.is_none());
    let p = prepared(&a);
    let parsed: Value = serde_json::from_str(&p.request.messages[1].content).unwrap();
    let context = parsed["observations"]["smell"].to_string();
    assert!(
        !context.contains("effective_strength")
            && !context.contains("source_kind")
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
    let source = smell("bread", "food", "source");
    let mut a = action("smell", json!({}));
    attach(&mut a, &source, &json!({}));
    assert_eq!(
        outcome(&prepared(&a), "パンの匂いでお腹がすいてきたわ。").1,
        "speak"
    );
}

#[test]
fn smell_and_biome_guards_are_not_generated_wording() {
    let e = smell("bread", "food", "source");
    let mut a = action("smell", json!({}));
    attach(&mut a, &e, &json!({}));
    a.text = "ええ匂いやな。".into();
    assert!(super::super::ambient::still_applicable(&a, &e));
    assert!(!super::super::ambient::still_applicable(
        &a,
        &smell("cake", "food", "source")
    ));
    assert!(!super::super::ambient::still_applicable(
        &a,
        &event(json!({"smell_observation":{"status":"none"}}))
    ));

    let e = event(json!({}));
    let mut a = action("special_biome_entry", json!({}));
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

fn spatial_smell(minimum: i64, cardinal: Option<&str>) -> GameEvent {
    event(json!({"smell_observation":{
        "status":"present","smell_id":"zombie","category":"decay","valence":"unpleasant",
        "source_kind":"entity","specificity":"source","effective_strength":8-minimum,
        "direction_estimate":cardinal.map(|value|json!({"cardinal":value}))
    }}))
}

#[test]
fn resolved_spatial_estimates_reach_the_model_and_change_invalidates_old_speech() {
    let e = spatial_smell(3, Some("east"));
    let mut a = action("smell", json!({}));
    attach(&mut a, &e, &json!({}));
    let p = prepared(&a);
    let context: Value = serde_json::from_str(&p.request.messages[1].content).unwrap();
    let observed_smell = &context["observations"]["smell"];
    assert!(observed_smell.get("distance_estimate").is_none());
    assert_eq!(observed_smell["direction_estimate"]["cardinal"], "east");
    assert_eq!(
        observed_smell["direction_estimate"]["basis"],
        "source_bearing"
    );
    for hidden in ["source_id", "entity_id", "position", "effective_strength"] {
        assert!(observed_smell.get(hidden).is_none());
    }
    assert!(still_applicable(&a, &e));
    assert!(still_applicable(&a, &spatial_smell(4, Some("east"))));
    assert!(!still_applicable(&a, &spatial_smell(3, Some("west"))));
    assert!(!still_applicable(&a, &spatial_smell(3, None)));
    assert!(!only_smell_spatial_changed(
        &a,
        &spatial_smell(4, Some("east"))
    ));
    assert!(only_smell_spatial_changed(&a, &spatial_smell(3, None)));
    assert!(!only_smell_spatial_changed(&a, &e));
    assert!(!only_smell_spatial_changed(
        &a,
        &smell("bread", "food", "source")
    ));
    assert!(!only_smell_spatial_changed(
        &a,
        &event(json!({"smell_observation":{"status":"none"}}))
    ));
    let mut elsewhere = serde_json::to_value(spatial_smell(4, Some("east"))).unwrap();
    elsewhere["player"]["dimension"] = json!("minecraft:the_nether");
    assert!(!only_smell_spatial_changed(
        &a,
        &GameEvent::parse(elsewhere).unwrap()
    ));
}

#[test]
fn fixed_smell_questions_use_current_estimates_and_keep_unknown_direction_explicit() {
    let e = spatial_smell(3, None);
    for text in [
        "匂いはどっちから？",
        "どこから匂う？",
        "匂いの距離はどのくらい？",
    ] {
        assert!(super::super::ambient::is_smell_query(text));
    }
    let reply = super::super::ambient::current_smell_query_reply(&e, "匂いはどっちから？");
    assert!(!reply.text.contains("ブロック"));
    assert!(reply.text.contains("方向はまだ絞れてへん"));
    assert!(reply.cue_id.is_none());
    assert!(super::super::ambient::still_applicable(&reply, &e));
    let located = spatial_smell(3, Some("east"));
    assert!(!super::super::ambient::still_applicable(&reply, &located));
    let current = super::super::ambient::current_smell_query_reply(&located, "匂いはどっちから？");
    assert!(current.text.contains("東のほうから来とるみたいや"));
    assert!(!current.text.contains("まだ絞れてへん"));
    assert!(super::super::ambient::still_applicable(&current, &located));
    let range_question =
        super::super::ambient::current_smell_query_reply(&located, "匂いの距離はどのくらい？");
    assert!(range_question.text.contains("距離までは分からへん"));
    assert!(!range_question.text.contains("ブロック"));
    assert!(super::super::ambient::still_applicable(
        &range_question,
        &located
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
    let mut a = action("special_biome_entry", json!({}));
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
    let mut a = action(
        "weather_transition",
        json!({"scene":"thunder_started","weather_from":"clear","weather_to":"thunder"}),
    );
    attach(&mut a, &e, &json!({}));
    assert_eq!(
        serde_json::from_str::<Value>(&prepared(&a).request.messages[1].content).unwrap()["observations"]
            ["thunder_heard"],
        false
    );
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
    let mut weather = action(
        "weather_transition",
        json!({"scene":"thunder_started","weather_from":"rain","weather_to":"thunder"}),
    );
    attach(&mut weather, &e, &json!({}));
    let request = prepared(&weather).request;
    let context: Value = serde_json::from_str(&request.messages[1].content).unwrap();
    assert_eq!(context["event"], "weather_transition");
    assert_eq!(
        context["situation"],
        "空が一気に暗くなって、嵐の気配がしてきた。"
    );
    assert_eq!(context["observations"]["thunder_heard"], false);
    assert!(context.get("self_state").is_none());
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
fn shared_weather_situation_matches_actual_altitude_precipitation() {
    for (biome, y, precipitation, situation) in [
        ("taiga", 64, "rain", "雨が降り始めた。"),
        (
            "taiga",
            250,
            "snow",
            "雪がしんしんと降り始めた。あたりが静かに白くなってきとる。",
        ),
        ("desert", 64, "none", "空が曇ってきた。"),
        ("unknown_biome", 64, "unknown", "天候が変わった。"),
    ] {
        let e = event(json!({
            "player":{"dimension":"minecraft:overworld","position":{"x":0,"y":y,"z":0}},
            "world":{"biome":biome,"weather":"rain","sky_visible":true}
        }));
        let mut a = action(
            "weather_transition",
            json!({"scene":"rain_started","weather_from":"clear","weather_to":"rain"}),
        );
        attach(&mut a, &e, &json!({}));
        let request = prepared(&a).request;
        let context: Value = serde_json::from_str(&request.messages[1].content).unwrap();
        assert_eq!(context["situation"], situation, "{biome}:{y}");
        assert_eq!(
            context["observations"]["local_precipitation"],
            precipitation
        );
    }
}

#[test]
fn sensory_questions_combat_and_severe_darkness_keep_the_original_path() {
    let e = smell("bread", "food", "source");
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
