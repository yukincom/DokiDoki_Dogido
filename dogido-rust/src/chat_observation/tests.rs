use super::*;
use serde_json::{Value, json};
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
        let id = strip(id).to_lowercase();
        self.mobs
            .get(id.strip_prefix("minecraft:").unwrap_or(&id))
            .map(String::as_str)
    }
    fn mob_fallback_label(&self, id: &str) -> Option<&str> {
        self.mob_fallback.get(id).map(String::as_str)
    }
    fn hostile_label(&self, id: &str) -> String {
        self.hostiles
            .get(id)
            .map_or_else(|| id.to_owned(), Clone::clone)
    }
    fn block_label(&self, id: &str) -> Option<&str> {
        self.blocks
            .get(&strip(id.split(':').next_back().unwrap()).to_lowercase())
            .map(String::as_str)
    }
}
fn labels() -> FixtureLabels {
    serde_json::from_str(include_str!("../../fixtures/chat-observation-labels.json")).unwrap()
}
#[derive(Deserialize)]
struct Trace {
    name: String,
    settings: Settings,
    steps: Vec<Step>,
}
#[derive(Deserialize)]
struct Step {
    event: GameEvent,
    accepted: bool,
    outcome_updates: NameOutcomeUpdate,
    expected: Value,
}
#[test]
fn persistent_python_traces() {
    let labels = labels();
    let mut traces = 0;
    let mut steps = 0;
    for line in include_str!("../../fixtures/chat-observation.jsonl").lines() {
        let trace: Trace = serde_json::from_str(line).unwrap();
        let mut memory = ChatObservationMemory::new(trace.settings);
        for (i, row) in trace.steps.iter().enumerate() {
            if row.accepted {
                memory
                    .observe(&row.event, &row.outcome_updates, &labels)
                    .unwrap();
            }
            let actual =
                serde_json::to_value(memory.snapshot(&row.event, &labels).unwrap()).unwrap();
            assert_eq!(actual, row.expected, "{} step {i}", trace.name);
            assert!(memory.visual.len() <= 12);
            assert!(memory.hearing.len() <= 12);
            assert!(memory.home.len() <= 5);
            steps += 1;
        }
        traces += 1;
    }
    assert!(
        traces > 100 && steps > 500,
        "fixture unexpectedly small: {traces}/{steps}"
    );
}
fn frame(at: &str, fields: Value) -> GameEvent {
    let mut value = json!({"schema_version":"2026-05-24","adapter":"pure-test","observed_at":at,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld"},"world":{},"combat":{"hostile_outcomes":[]}});
    for (key, v) in fields.as_object().unwrap() {
        value[key] = v.clone();
    }
    GameEvent::parse(value).unwrap()
}
#[test]
fn snapshot_is_read_only_and_current_never_contains_recent_or_death_names() {
    let labels = labels();
    let mut memory = ChatObservationMemory::default();
    let start = frame(
        "2026-09-29T00:00:00Z",
        json!({"visual_threats":[{"type":"zombie","entity_id":"z"}],"passive_mobs":[{"type":"cow"}]}),
    );
    memory
        .observe(
            &start,
            &NameOutcomeUpdate {
                confirmed_types: vec!["witch".into()],
                ..Default::default()
            },
            &labels,
        )
        .unwrap();
    let early = frame("2026-09-29T00:00:01Z", json!({}));
    let snapshot = memory.snapshot(&early, &labels).unwrap();
    assert!(snapshot.current.visual_types.is_empty() && snapshot.current.passive_types.is_empty());
    assert_eq!(snapshot.name_context.types, vec!["zombie", "cow", "witch"]);
    let later = frame("2026-09-29T00:00:13Z", json!({}));
    assert!(
        memory
            .snapshot(&later, &labels)
            .unwrap()
            .recent
            .visual_memos
            .is_empty()
    );
    assert_eq!(memory.snapshot(&early, &labels).unwrap(), snapshot); // a read did not prune or renew
}
#[test]
fn mixed_timezone_domains_fail_without_partial_update() {
    let labels = labels();
    let mut memory = ChatObservationMemory::default();
    let event = frame(
        "2026-09-29T00:00:00Z",
        json!({"passive_mobs":[{"type":"cow"}]}),
    );
    memory
        .observe(&event, &NameOutcomeUpdate::default(), &labels)
        .unwrap();
    let before = memory.snapshot(&event, &labels).unwrap();
    let naive = frame(
        "2026-09-29T00:00:01",
        json!({"passive_mobs":[{"type":"pig"}]}),
    );
    assert_eq!(
        memory.observe(&naive, &NameOutcomeUpdate::default(), &labels),
        Err(MixedTimeDomains)
    );
    assert_eq!(memory.snapshot(&naive, &labels), Err(MixedTimeDomains));
    assert_eq!(memory.snapshot(&event, &labels).unwrap(), before);
}
#[test]
fn explicit_empty_outcome_update_does_not_manufacture_a_death() {
    let labels = labels();
    let mut memory = ChatObservationMemory::default();
    let before = frame(
        "2026-09-29T00:00:00Z",
        json!({"visual_threats":[{"type":"zombie","entity_id":"z"}]}),
    );
    memory
        .observe(&before, &NameOutcomeUpdate::default(), &labels)
        .unwrap();
    let absent = frame("2026-09-29T00:00:09Z", json!({}));
    memory
        .observe(&absent, &NameOutcomeUpdate::default(), &labels)
        .unwrap();
    let later = frame("2026-09-29T00:00:11Z", json!({}));
    assert!(
        memory
            .snapshot(&later, &labels)
            .unwrap()
            .name_context
            .types
            .is_empty()
    );
    assert_eq!(
        memory
            .snapshot(&later, &labels)
            .unwrap()
            .recent
            .visual_types,
        vec!["zombie"]
    );
}
