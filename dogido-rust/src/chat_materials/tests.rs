use super::*;
use std::collections::HashMap;
#[derive(Deserialize)]
struct FixtureLabels {
    mobs: HashMap<String, String>,
    mob_fallback: HashMap<String, String>,
    hostiles: HashMap<String, String>,
    blocks: HashMap<String, String>,
}
impl Labels for FixtureLabels {
    fn mob_label(&self, id: &str) -> Option<&str> {
        let id = chat_catalog::strip(id).to_lowercase();
        self.mobs
            .get(id.strip_prefix("minecraft:").unwrap_or(&id))
            .map(String::as_str)
    }
    fn mob_fallback_label(&self, id: &str) -> Option<&str> {
        self.mob_fallback.get(id).map(String::as_str)
    }
    fn hostile_label(&self, id: &str) -> String {
        self.hostiles.get(id).cloned().unwrap_or_else(|| id.into())
    }
    fn block_label(&self, id: &str) -> Option<&str> {
        self.blocks
            .get(&chat_catalog::strip(id.split(':').next_back().unwrap()).to_lowercase())
            .map(String::as_str)
    }
}
fn labels() -> FixtureLabels {
    serde_json::from_str(include_str!("../../fixtures/chat-observation-labels.json")).unwrap()
}
#[derive(Deserialize)]
struct Row {
    name: String,
    event: GameEvent,
    settings: Settings,
    input: PlayerInput,
    context: Context,
    snapshot: Snapshot,
    plan: Plan,
    expected: Value,
}
fn eq(actual: &Value, expected: &Value, name: &str) {
    if actual == expected {
        return;
    }
    if let (Some(a), Some(b)) = (actual.as_object(), expected.as_object()) {
        for key in a.keys().chain(b.keys()) {
            assert_eq!(a.get(key), b.get(key), "{name}: {key}");
        }
    }
    assert_eq!(actual, expected, "{name}");
}
fn check(row: &Row, labels: &FixtureLabels) {
    let untouched = serde_json::to_value(&row.snapshot).unwrap();
    let before = before_plan(
        &row.event,
        &row.settings,
        &row.input,
        &row.context,
        &row.snapshot,
        labels,
        Some("mock-chat"),
    )
    .unwrap();
    match before {
        Before::Knowledge => assert_eq!(row.expected["kind"], "knowledge", "{}", row.name),
        Before::Fixed(f) => {
            assert_eq!(row.expected["kind"], "fixed_before", "{}", row.name);
            assert!(f.announce_smell);
            assert_eq!(f.text, row.expected["text"], "{}", row.name);
            assert_eq!(f.reason, row.expected["reason"]);
        }
        Before::Plan(p) => {
            // Legacy fixture covers the unchanged material fields; the frozen
            // crosshair binding added afterwards is exercised separately below.
            let mut planner_input = serde_json::to_value(&p.planner_input).unwrap();
            planner_input.as_object_mut().unwrap().remove("look_target");
            eq(
                &planner_input,
                &row.expected["planner_input"],
                &format!("{} planner", row.name),
            );
            let result = after_plan(&p, &row.plan).unwrap();
            match result {
                After::Fixed(f) => {
                    assert_eq!(row.expected["kind"], "fixed_after", "{}", row.name);
                    assert!(!f.announce_smell);
                    assert_eq!(f.text, row.expected["text"], "{}", row.name);
                    assert_eq!(f.reason, row.expected["reason"], "{}", row.name);
                }
                After::Leaf(leaf) => {
                    assert_eq!(row.expected["kind"], "leaf", "{}", row.name);
                    let mut legacy_details = leaf.details.clone();
                    legacy_details
                        .as_object_mut()
                        .unwrap()
                        .remove("named_entity_description_hints");
                    eq(
                        &legacy_details,
                        &row.expected["details"],
                        &format!("{} details", row.name),
                    );
                    assert_eq!(
                        leaf.fallback_text, row.expected["fallback_text"],
                        "{}",
                        row.name
                    );
                    let payload = leaf.input("mock-chat", 512);
                    let mut input = json!({"prompt":{"schema_version":payload.prompt.schema_version,"kind":payload.prompt.kind,"model":payload.prompt.model,"details":payload.prompt.details,"temperature":payload.prompt.temperature,"max_tokens":payload.prompt.max_tokens,"enable_thinking":payload.prompt.enable_thinking},"validation":payload.validation,"fallback_text":payload.fallback_text});
                    input["prompt"]["details"]
                        .as_object_mut()
                        .unwrap()
                        .remove("named_entity_description_hints");
                    input["prompt"]["details"]
                        .as_object_mut()
                        .unwrap()
                        .remove("player_chat_plan_focus");
                    assert_eq!(input["prompt"]["details"]["dialogue_choice"], true);
                    input["prompt"]["details"]
                        .as_object_mut()
                        .unwrap()
                        .remove("dialogue_choice");
                    eq(
                        &input,
                        &row.expected["input"],
                        &format!("{} payload", row.name),
                    );
                    let After::Leaf(again) = after_plan(&p, &row.plan).unwrap() else {
                        panic!("unstable projection")
                    };
                    eq(&again.details, &leaf.details, "repeated pure projection");
                }
            }
        }
    }
    assert_eq!(
        serde_json::to_value(&row.snapshot).unwrap(),
        untouched,
        "read mutated memory"
    );
}
#[test]
fn canonical_full_renderer_with_mock_plan_and_leaf() {
    let labels = labels();
    let mut count = 0;
    for line in include_str!("../../fixtures/chat-materials.jsonl").lines() {
        let row: Row = serde_json::from_str(line).unwrap();
        check(&row, &labels);
        count += 1;
    }
    assert!(count > 400, "truncated fixture: {count}");
}
#[test]
fn frozen_turn_does_not_merge_a_new_event_or_reinterpret_completed_history() {
    let labels = labels();
    let mut row: Row = serde_json::from_str(
        include_str!("../../fixtures/chat-materials.jsonl")
            .lines()
            .next()
            .unwrap(),
    )
    .unwrap();
    let Before::Plan(p) = before_plan(
        &row.event,
        &row.settings,
        &row.input,
        &row.context,
        &row.snapshot,
        &labels,
        Some("mock-chat"),
    )
    .unwrap() else {
        panic!()
    };
    row.context.history.event_digest = "later turn".into();
    let mut later = serde_json::to_value(&row.event).unwrap();
    later["world"]["biome"] = json!("the_end");
    row.event = GameEvent::parse(later).unwrap();
    row.snapshot.current.passive_types.push("warden".into());
    row.input.semantic_text = "匂いは？".into();
    let After::Leaf(leaf) = after_plan(&p, &row.plan).unwrap() else {
        panic!()
    };
    eq(&leaf.details, &row.expected["details"], "frozen turn");
    // Runtime owner must cancel stale epochs instead of supplying a newer event to after_plan.
}
#[test]
fn absent_model_or_empty_user_prepares_no_planner_call() {
    let labels = labels();
    let mut row: Row = serde_json::from_str(
        include_str!("../../fixtures/chat-materials.jsonl")
            .lines()
            .next()
            .unwrap(),
    )
    .unwrap();
    let Before::Plan(p) = before_plan(
        &row.event,
        &row.settings,
        &row.input,
        &row.context,
        &row.snapshot,
        &labels,
        None,
    )
    .unwrap() else {
        panic!()
    };
    assert!(p.planner.request.is_none());
    row.input.semantic_text = "  \n ".into();
    let Before::Plan(p) = before_plan(
        &row.event,
        &row.settings,
        &row.input,
        &row.context,
        &row.snapshot,
        &labels,
        Some("mock-chat"),
    )
    .unwrap() else {
        panic!()
    };
    assert!(p.planner.request.is_none());
}

#[test]
fn deictic_identification_uses_frozen_crosshair_and_not_catalog_search() {
    let labels = labels();
    for (kind, id, label, question) in [
        ("block", "cactus", "サボテン", "これは何かな"),
        ("block", "grass_block", "草ブロック", "これは何ですか?"),
        ("entity", "enderman", "エンダーマン", "これ何"),
    ] {
        let mut row: Row = serde_json::from_str(
            include_str!("../../fixtures/chat-materials.jsonl")
                .lines()
                .next()
                .unwrap(),
        )
        .unwrap();
        let mut event = serde_json::to_value(&row.event).unwrap();
        event["look_target"] = json!({"kind":kind,"name":id,"distance":2.0});
        row.event = GameEvent::parse(event).unwrap();
        row.input = PlayerInput {
            raw_text: question.into(),
            semantic_text: question.into(),
            ..Default::default()
        };
        row.context = Context::default();
        let Before::Plan(p) = before_plan(
            &row.event,
            &row.settings,
            &row.input,
            &row.context,
            &row.snapshot,
            &labels,
            Some("mock-chat"),
        )
        .unwrap() else {
            panic!()
        };
        let plan = Plan {
            action: Action::IdentifyEntity,
            entity_query: "これ".into(),
            ..p.planner.fallback.clone()
        };
        let After::Leaf(leaf) = after_plan(&p, &plan).unwrap() else {
            panic!("crosshair discarded: {id}")
        };
        assert_eq!(leaf.details["entity_grounding_status"], "observed");
        assert_eq!(leaf.details["entity_observed_ids"], json!([id]));
        assert_eq!(leaf.details["entity_observed_labels"], json!([label]));
        assert_eq!(leaf.details["required_identification_label"], label);
        assert_eq!(leaf.fallback_text, format!("それは{label}やで。"));
        assert!(leaf.handoff.catalog.as_ref().unwrap().is_empty());
        let mut handoff = handoff::Handoff::default();
        handoff
            .record_plan(p.planner.request.as_ref().unwrap(), &plan)
            .unwrap();
        handoff.resolve(leaf.handoff_input.clone(), true).unwrap();
        let input = leaf.input("mock-chat", 512);
        handoff.validate_leaf(&input.prompt.details).unwrap();
        handoff
            .validate_materials(&input.prompt.details, &input.validation, false)
            .unwrap();
        let messages = input.prompt.into_request().unwrap().messages;
        assert!(messages.iter().any(|m| m.content.contains(label)));
        let mut reply =
            chat_validation::Turn::new(leaf.input("mock-chat", 72), "mock-chat", 72).unwrap();
        reply.request().unwrap();
        assert!(
            !reply
                .complete(Some(
                    r#"{"action":"speak","speech":"尖ってるし緑色やな。"}"#
                ))
                .unwrap()
        );
        let retry = reply.request().unwrap();
        assert!(
            retry
                .messages
                .last()
                .unwrap()
                .content
                .contains("名前を先に")
        );
        assert!(
            reply
                .complete(Some(r#"{"action":"speak","speech":"よう見えへんな。"}"#))
                .unwrap()
        );
        assert_eq!(
            reply.take_outcome().unwrap().final_text,
            format!("それは{label}やで。")
        );

        let mut handoff = handoff::Handoff::default();
        handoff
            .record_plan(p.planner.request.as_ref().unwrap(), &plan)
            .unwrap();
        let mut swapped = leaf.handoff_input;
        swapped.look_target.as_mut().unwrap().entity_id = "zombie".into();
        assert!(handoff.resolve(swapped, true).is_err());

        let named = Plan {
            entity_query: "クリーパー".into(),
            ..plan
        };
        let After::Leaf(named_leaf) = after_plan(&p, &named).unwrap() else {
            panic!()
        };
        assert_ne!(named_leaf.details["entity_observed_ids"], json!([id]));
        assert!(
            !named_leaf.details["entity_candidate_ids"]
                .as_array()
                .unwrap()
                .contains(&json!(id))
        );
    }
}

#[test]
fn absent_crosshair_or_workshop_does_not_identify_from_history() {
    let labels = labels();
    for workshop in [false, true] {
        let mut row: Row = serde_json::from_str(
            include_str!("../../fixtures/chat-materials.jsonl")
                .lines()
                .next()
                .unwrap(),
        )
        .unwrap();
        let mut event = serde_json::to_value(&row.event).unwrap();
        event["look_target"] = if workshop {
            json!({"kind":"block","name":"cactus","distance":2.0})
        } else {
            Value::Null
        };
        row.event = GameEvent::parse(event).unwrap();
        row.input = PlayerInput {
            raw_text: "これ何".into(),
            semantic_text: "これ何".into(),
            ..Default::default()
        };
        row.context = Context {
            workshop_open: workshop,
            ..Default::default()
        };
        row.context.history.conversation_turns =
            vec![json!({"turn_id":"old","role":"assistant","text":"それはサボテンやで。"})];
        let Before::Plan(p) = before_plan(
            &row.event,
            &row.settings,
            &row.input,
            &row.context,
            &row.snapshot,
            &labels,
            Some("mock-chat"),
        )
        .unwrap() else {
            panic!()
        };
        let plan = Plan {
            action: Action::IdentifyEntity,
            entity_query: "これ".into(),
            ..p.planner.fallback.clone()
        };
        assert!(p.planner_input.look_target.is_none());
        let After::Fixed(fixed) = after_plan(&p, &plan).unwrap() else {
            panic!()
        };
        assert!(fixed.text.contains("分からへん"));
    }
}

#[test]
fn named_mob_hints_reach_leaf_without_becoming_presence_or_catalog_lookup() {
    let labels = labels();
    for (user, workshop, expected) in [
        ("エンダーマン", false, true),
        ("そうだね", false, false),
        ("エンダーマン", true, false),
    ] {
        let mut row: Row = serde_json::from_str(
            include_str!("../../fixtures/chat-materials.jsonl")
                .lines()
                .next()
                .unwrap(),
        )
        .unwrap();
        row.input = PlayerInput {
            raw_text: user.into(),
            semantic_text: user.into(),
            ..Default::default()
        };
        row.context = Context {
            workshop_open: workshop,
            ..Default::default()
        };
        row.context.history.conversation_turns =
            vec![json!({"turn_id":"old","role":"assistant","text":"エンダーマンはちっちゃいな。"})];
        let Before::Plan(p) = before_plan(
            &row.event,
            &row.settings,
            &row.input,
            &row.context,
            &row.snapshot,
            &labels,
            Some("mock-chat"),
        )
        .unwrap() else {
            panic!()
        };
        let plan = Plan {
            action: Action::ContinueConversation,
            entity_query: String::new(),
            ..p.planner.fallback.clone()
        };
        let After::Leaf(leaf) = after_plan(&p, &plan).unwrap() else {
            panic!()
        };
        assert_eq!(
            leaf.details.get("named_entity_description_hints").is_some(),
            expected
        );
        assert_eq!(leaf.details["entity_grounding_status"], "not_applicable");
        assert_eq!(leaf.details["entity_observed_ids"], json!([]));
        assert!(leaf.handoff.catalog.as_ref().unwrap().is_empty());
        if expected {
            let messages = leaf
                .input("mock-chat", 512)
                .prompt
                .into_request()
                .unwrap()
                .messages;
            assert!(messages.iter().any(|m| {
                m.content.contains(
                    leaf.details["named_entity_description_hints"]
                        .as_str()
                        .unwrap(),
                )
            }));
        }
    }
}

fn individual_event(at: &str, fields: Value) -> GameEvent {
    let mut value = json!({"schema_version":"2026-05-24","adapter":"test","observed_at":at,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld"},"world":{"biome":"plains","sky_visible":true,"time_phase":"day"}});
    value
        .as_object_mut()
        .unwrap()
        .extend(fields.as_object().unwrap().clone());
    GameEvent::parse(value).unwrap()
}
fn individual_turn(
    event: &GameEvent,
    snapshot: &Snapshot,
    user: &str,
    sound: bool,
    action: Action,
    query: &str,
) -> After {
    let input = PlayerInput {
        raw_text: user.into(),
        semantic_text: user.into(),
        asks_about_sound: sound,
        ..Default::default()
    };
    let Before::Plan(prepared) = before_plan(
        event,
        &Settings::default(),
        &input,
        &Context::default(),
        snapshot,
        &labels(),
        Some("mock"),
    )
    .unwrap() else {
        panic!()
    };
    let plan = Plan {
        action,
        entity_query: query.into(),
        ..prepared.planner.fallback.clone()
    };
    after_plan(&prepared, &plan).unwrap()
}
fn heard_pet() -> Value {
    json!({"type":"cat","source_id":"cat-1","sound_event":"entity.cat.ambient","direction":{"horizontal":"right"},"heard_ago_ms":0,
        "identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true}})
}
fn individual_snapshot(event: &GameEvent) -> Snapshot {
    let mut memory = crate::chat_observation::ChatObservationMemory::default();
    memory
        .observe(event, &Default::default(), &labels())
        .unwrap();
    memory.snapshot(event, &labels()).unwrap()
}
#[test]
fn pet_sounds_are_secondary_except_for_an_explicit_name_or_species() {
    let mixed = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"ambient_sounds":[heard_pet(),
        {"type":"block:campfire","source_id":"fire","sound_event":"block.campfire.crackle","direction":{"horizontal":"left"}}]}),
    );
    let snapshot = individual_snapshot(&mixed);
    assert!(
        snapshot.current.hearing_types.contains(&"cat".to_owned()),
        "answer ranking never changes presence evidence"
    );
    for (user, pet) in [
        ("何の音？", false),
        ("クロちゃんの声？", true),
        ("猫の声？", true),
    ] {
        let After::Leaf(leaf) =
            individual_turn(&mixed, &snapshot, user, true, Action::AnswerObservation, "")
        else {
            panic!("{user}")
        };
        let summary = leaf.details["hearing_summary"].as_str().unwrap();
        assert_eq!(summary.contains("クロちゃんの声"), pet, "{user}: {summary}");
        assert_eq!(summary.contains("焚き火"), !pet, "{user}: {summary}");
    }
    let alone = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"ambient_sounds":[heard_pet()]}),
    );
    let After::Leaf(leaf) = individual_turn(
        &alone,
        &individual_snapshot(&alone),
        "何の音？",
        true,
        Action::AnswerObservation,
        "",
    ) else {
        panic!()
    };
    assert!(
        leaf.details["hearing_summary"]
            .as_str()
            .unwrap()
            .contains("クロちゃんの声")
    );
}
#[test]
fn explicit_pet_sound_never_borrows_another_cats_voice() {
    let event = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true}}],
        "ambient_sounds":[{"type":"cat","source_id":"cat-2","identity":{"entity_id":"cat-2","custom_name":"シロ","tamed":false}}]}),
    );
    let After::Fixed(reply) = individual_turn(
        &event,
        &individual_snapshot(&event),
        "クロちゃんの声？",
        true,
        Action::AnswerObservation,
        "",
    ) else {
        panic!()
    };
    assert_eq!(reply.reason, "no_hearing_evidence");
    let duplicate = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"passive_mobs":[
        {"type":"wolf","identity":{"entity_id":"wolf-1","custom_name":"クロちゃん","tamed":true}}],"ambient_sounds":[heard_pet()]}),
    );
    let After::Fixed(reply) = individual_turn(
        &duplicate,
        &individual_snapshot(&duplicate),
        "クロちゃんの声？",
        true,
        Action::AnswerObservation,
        "",
    ) else {
        panic!()
    };
    assert_eq!(reply.reason, "ambiguous_named_sound");
}

#[test]
fn named_sound_uses_latest_name_normalized_longest_match_and_actual_sound_kind() {
    let original = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"ambient_sounds":[heard_pet()]}),
    );
    let mut memory = crate::chat_observation::ChatObservationMemory::default();
    memory
        .observe(&original, &Default::default(), &labels())
        .unwrap();
    let renamed = individual_event(
        "2026-09-30T01:00:01Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"cat-1","custom_name":"シロ","tamed":true}}]}),
    );
    memory
        .observe(&renamed, &Default::default(), &labels())
        .unwrap();
    let snap = memory.snapshot(&renamed, &labels()).unwrap();
    let answer = individuals::hearing(&snap, "シロの声？");
    assert!(answer.summary.contains("シロの声") && !answer.summary.contains("クロちゃん"));
    let removed = individual_event(
        "2026-09-30T01:00:02Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"cat-1","tamed":true}}]}),
    );
    memory
        .observe(&removed, &Default::default(), &labels())
        .unwrap();
    let snap = memory.snapshot(&removed, &labels()).unwrap();
    assert!(
        !individuals::hearing(&snap, "何の音？")
            .summary
            .contains("クロちゃん")
    );

    let mixed = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"ambient_sounds":[heard_pet(),
        {"type":"wolf","source_id":"wolf-1","sound_event":"entity.wolf.step",
        "identity":{"entity_id":"wolf-1","custom_name":"くろ","tamed":true}}]}),
    );
    let snap = individual_snapshot(&mixed);
    let answer = individuals::hearing(&snap, "クロちゃんの声？");
    assert!(answer.summary.contains("クロちゃんの声") && !answer.summary.contains("くろの"));
    let context = individuals::context(&snap, "クロちゃんが助けてくれた", "", false);
    assert!(context.contains("クロちゃん") && !context.contains("オオカミ"));
    let answer = individuals::hearing(&snap, "くろの音？");
    assert!(answer.summary.contains("くろの音") && !answer.summary.contains("くろの声"));
    let duplicate = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"ambient_sounds":[heard_pet(),
        {"type":"wolf","source_id":"wolf-1","sound_event":"entity.wolf.ambient",
        "identity":{"entity_id":"wolf-1","custom_name":"くろちゃん","tamed":true}}]}),
    );
    assert!(individuals::hearing(&individual_snapshot(&duplicate), "クロちゃんの声？").ambiguous);
}
#[test]
fn pet_report_uses_observed_name_species_mapping_without_inventing_an_event() {
    let cat = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true}}]}),
    );
    let user = "クロちゃんがクリーパーを追い払ってくれたー";
    let snapshot = individual_snapshot(&cat);
    let After::Leaf(leaf) = individual_turn(
        &cat,
        &snapshot,
        user,
        false,
        Action::ContinueConversation,
        "",
    ) else {
        panic!()
    };
    let facts = leaf.details["named_mob_context"].as_str().unwrap();
    assert!(facts.contains("クロちゃん") && facts.contains("ネコ"));
    assert!(!facts.contains("クリーパー") && !facts.contains("追い払"));
    assert!(
        leaf.details["allowed_speech_labels"]
            .as_array()
            .unwrap()
            .contains(&json!("ネコ"))
    );
    assert_eq!(leaf.details["entity_grounding_status"], "not_applicable");
    let messages = leaf
        .input("mock", 128)
        .prompt
        .into_request()
        .unwrap()
        .messages;
    assert!(
        messages
            .iter()
            .any(|m| m.content.contains("名前と種類") && m.content.contains("クロちゃん"))
    );
    assert!(leaf.handoff.catalog.unwrap().is_empty());
}
#[test]
fn named_presence_is_individual_and_remembered_names_do_not_assert_current_presence() {
    let cat = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true}}]}),
    );
    let mut memory = crate::chat_observation::ChatObservationMemory::default();
    memory
        .observe(&cat, &Default::default(), &labels())
        .unwrap();
    let snap = memory.snapshot(&cat, &labels()).unwrap();
    let After::Leaf(leaf) = individual_turn(
        &cat,
        &snap,
        "クロちゃんいる？",
        false,
        Action::CheckEntityPresence,
        "クロちゃん",
    ) else {
        panic!()
    };
    assert_eq!(
        leaf.details["entity_observed_ids"],
        json!(["individual:cat-1"])
    );
    let gone = individual_event(
        "2026-09-30T01:00:15Z",
        json!({"passive_mobs":[{"type":"cat"}]}),
    );
    memory
        .observe(&gone, &Default::default(), &labels())
        .unwrap();
    let snap = memory.snapshot(&gone, &labels()).unwrap();
    let After::Fixed(reply) = individual_turn(
        &gone,
        &snap,
        "クロちゃんいる？",
        false,
        Action::CheckEntityPresence,
        "クロちゃん",
    ) else {
        panic!()
    };
    assert!(
        reply.text.contains("クロちゃんは確認できてへん"),
        "{}",
        reply.text
    );
    let After::Leaf(leaf) = individual_turn(
        &gone,
        &snap,
        "クロちゃん頑張ったね",
        false,
        Action::ContinueConversation,
        "",
    ) else {
        panic!()
    };
    assert!(
        leaf.details["named_mob_context"]
            .as_str()
            .unwrap()
            .contains("\"current_observation\":false")
    );
    let duplicate = individual_event(
        "2026-09-30T01:00:16Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true}},
        {"type":"wolf","identity":{"entity_id":"wolf-1","custom_name":"クロちゃん","tamed":true}}]}),
    );
    let After::Fixed(reply) = individual_turn(
        &duplicate,
        &individual_snapshot(&duplicate),
        "クロちゃんいる？",
        false,
        Action::CheckEntityPresence,
        "クロちゃん",
    ) else {
        panic!()
    };
    assert!(reply.text.contains("どれのことか"));
}

#[test]
fn shared_world_context_is_used_once_without_legacy_place_or_weather_materials() {
    let event = individual_event(
        "2026-10-06T00:00:00Z",
        json!({
        "player":{"dimension":"minecraft:overworld","position":{"y":123}},
        "world":{"biome":"meadow","sky_visible":true,"weather":"clear","local_light":8,"ceiling_height":24}}),
    );
    let world = crate::conversation_observation::project(&event, None, 10.0, 3.0, 5000).unwrap();
    let context = Context {
        world_context: Some(world.clone()),
        ..Context::default()
    };
    let input = PlayerInput {
        raw_text: "いい天気だね".into(),
        semantic_text: "いい天気だね".into(),
        ..PlayerInput::default()
    };
    let Before::Plan(prepared) = before_plan(
        &event,
        &Settings::default(),
        &input,
        &context,
        &individual_snapshot(&event),
        &labels(),
        Some("mock"),
    )
    .unwrap() else {
        panic!()
    };
    assert_eq!(prepared.base_details["world_context"], json!(world));
    for key in [
        "place_context",
        "biome",
        "space_kind",
        "sky_visible",
        "time_phase",
        "weather",
        "weather_label",
        "weather_context",
        "precipitation_kind",
    ] {
        assert!(
            prepared.base_details.get(key).is_none(),
            "duplicate material: {key}"
        );
    }
    let After::Leaf(leaf) = after_plan(&prepared, &prepared.planner.fallback).unwrap() else {
        panic!()
    };
    let messages = leaf
        .input("mock", 512)
        .prompt
        .into_request()
        .unwrap()
        .messages;
    let text = messages
        .iter()
        .map(|m| m.content.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(text.matches("開けた地上").count(), 1);
    assert!(!text.contains("天井おおよそ") && !text.contains("高さY"));
}

#[test]
fn companion_priority_is_limited_to_the_current_player_topic() {
    let event = individual_event(
        "2026-10-06T02:00:00Z",
        json!({
        "passive_mobs":[{"type":"cow"},{"type":"chicken"},{"type":"cow"},{"type":"chicken"}],
        "companion_candidates":[
            {"type":"cat","identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true},
                "environment":{"touching_water":false,"on_ground":true}},
            {"type":"wolf","identity":{"entity_id":"wolf-1","tamed":true}}]}),
    );
    let snapshot = individual_snapshot(&event);
    let context = Context {
        world_context: Some(
            crate::conversation_observation::project(&event, None, 10.0, 3.0, 5000).unwrap(),
        ),
        ..Context::default()
    };
    let original = serde_json::to_value(&event).unwrap();
    let prepare = |user: &str| {
        let input = PlayerInput {
            raw_text: user.into(),
            semantic_text: user.into(),
            ..PlayerInput::default()
        };
        let Before::Plan(turn) = before_plan(
            &event,
            &Settings::default(),
            &input,
            &context,
            &snapshot,
            &labels(),
            Some("mock"),
        )
        .unwrap() else {
            panic!()
        };
        turn
    };
    let unrelated = prepare("いい天気だね");
    assert_eq!(unrelated.passive, ["cow", "chicken"]);
    assert!(
        !unrelated
            .planner_input
            .observation_summary
            .contains("クロちゃん")
    );
    assert!(
        !unrelated
            .planner_input
            .observed_entities
            .iter()
            .any(|row| row["label"] == "クロちゃん")
    );
    assert!(
        !unrelated.base_details["world_context"]["catalog_knowledge"]["entries"]
            .as_array()
            .unwrap()
            .iter()
            .any(|entry| entry["id"] == "cat")
    );
    let named = prepare("クロちゃんがいるね");
    assert_eq!(named.event.passive_mobs.len(), 4);
    assert_eq!(named.event.passive_mobs[0].r#type, "cat");
    assert!(
        named
            .planner_input
            .observation_summary
            .contains("クロちゃん")
    );
    assert!(
        named.base_details["world_context"]["catalog_knowledge"]["entries"]
            .as_array()
            .unwrap()
            .iter()
            .any(|entry| entry["id"] == "cat")
    );
    assert_eq!(prepare("猫かわいいね").event.passive_mobs[0].r#type, "cat");
    assert_eq!(prepare("狼がいるね").event.passive_mobs[0].r#type, "wolf");
    assert_eq!(prepare("いい天気だね").passive, ["cow", "chicken"]);
    assert_eq!(serde_json::to_value(&event).unwrap(), original);
    assert_eq!(snapshot.current.passive_types, ["cow", "chicken"]);
}

#[test]
fn retained_companion_name_never_restores_current_presence() {
    let named = individual_event(
        "2026-10-06T02:00:00Z",
        json!({"companion_candidates":[
        {"type":"cat","identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true}}]}),
    );
    let gone = individual_event("2026-10-06T02:00:01Z", json!({}));
    let mut memory = crate::chat_observation::ChatObservationMemory::default();
    memory
        .observe(&named, &Default::default(), &labels())
        .unwrap();
    memory
        .observe(&gone, &Default::default(), &labels())
        .unwrap();
    let snapshot = memory.snapshot(&gone, &labels()).unwrap();
    assert!(!snapshot.named_mobs[0].current);
    assert!(individuals::focus(&gone, &snapshot, &Context::default(), "クロちゃんは？").is_none());
    assert!(individuals::current_observations(&snapshot, "クロちゃんは？").is_empty());
}

#[test]
fn current_companion_name_survives_the_retained_name_table_limit() {
    let regular = (0..4)
        .map(|i| json!({"type":"cow", "identity":{"entity_id":format!("cow-{i}")}}))
        .collect::<Vec<_>>();
    let companions = (0..32)
        .map(|i| {
            json!({"type":"cat", "identity":{
        "entity_id":format!("cat-{i}"), "custom_name":format!("個体番号{i}号"), "tamed":true}})
        })
        .collect::<Vec<_>>();
    let event = individual_event(
        "2026-10-06T02:00:00Z",
        json!({
        "passive_mobs":regular, "companion_candidates":companions}),
    );
    let snapshot = individual_snapshot(&event);
    assert!(
        !snapshot
            .named_mobs
            .iter()
            .any(|row| row.entity_id == "cat-31")
    );
    let focus =
        individuals::focus(&event, &snapshot, &Context::default(), "個体番号31号は？").unwrap();
    assert_eq!(
        focus.event.passive_mobs[0]
            .identity
            .as_ref()
            .unwrap()
            .entity_id,
        "cat-31"
    );
    assert!(
        individuals::current_observations(&focus.snapshot, "個体番号31号は？")
            .iter()
            .any(|row| row.entity_id == "individual:cat-31")
    );
    assert!(individuals::focus(&event, &snapshot, &Context::default(), "いい天気だね").is_none());
    assert!(
        !snapshot
            .named_mobs
            .iter()
            .any(|row| row.entity_id == "cat-31")
    );
}
