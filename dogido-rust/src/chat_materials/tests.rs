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
        assert!(!reply.complete(Some("尖ってるし緑色やな。")).unwrap());
        let retry = reply.request().unwrap();
        assert!(
            retry
                .messages
                .last()
                .unwrap()
                .content
                .contains("名前を先に")
        );
        assert!(reply.complete(Some("よう見えへんな。")).unwrap());
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
            assert!(
                messages
                    .iter()
                    .any(|m| m.content.contains("エンダーマン：黒い、長身、紫の目"))
            );
        }
    }
}
