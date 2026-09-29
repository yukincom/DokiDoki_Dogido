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
            eq(
                &serde_json::to_value(&p.planner_input).unwrap(),
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
                    eq(
                        &leaf.details,
                        &row.expected["details"],
                        &format!("{} details", row.name),
                    );
                    assert_eq!(
                        leaf.fallback_text, row.expected["fallback_text"],
                        "{}",
                        row.name
                    );
                    let payload = leaf.input("mock-chat", 512);
                    let input = json!({"prompt":{"schema_version":payload.prompt.schema_version,"kind":payload.prompt.kind,"model":payload.prompt.model,"details":payload.prompt.details,"temperature":payload.prompt.temperature,"max_tokens":payload.prompt.max_tokens,"enable_thinking":payload.prompt.enable_thinking},"validation":payload.validation,"fallback_text":payload.fallback_text});
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
