use dogido_rust::{
    chat_observation::{ChatObservationMemory, Labels, NameOutcomeUpdate},
    combat::{
        core::Engine,
        model::{Mode, Settings},
        outcomes::Outcomes,
    },
    events::GameEvent,
    threats,
};
use serde::Deserialize;
use serde_json::{Map, Value, json};
fn empty(v: NameOutcomeUpdate) {
    assert!(v.confirmed_types.is_empty());
}
#[derive(Deserialize)]
struct Trace {
    name: String,
    settings: Map<String, Value>,
    steps: Vec<Step>,
}
#[derive(Deserialize)]
struct Step {
    event: GameEvent,
    expected_python: Value,
    expected: Value,
    deviation: String,
}
#[test]
fn persistent_canonical_series_and_explicit_replay_deviations() {
    let mut exact = 0;
    let mut differences = 0;
    let mut traces = 0;
    for line in include_str!("../fixtures/combat-name-updates.jsonl").lines() {
        let trace: Trace = serde_json::from_str(line).unwrap();
        let mut o = Outcomes::with_settings(&Settings::merged(&trace.settings).unwrap());
        for (i, row) in trace.steps.iter().enumerate() {
            o.observe(&row.event, i as u64 * 1000);
            let value = serde_json::to_value(o.take_name_updates()).unwrap();
            assert_eq!(value, row.expected, "{} {i}", trace.name);
            empty(o.take_name_updates());
            if row.deviation.is_empty() {
                assert_eq!(value, row.expected_python);
                exact += 1;
            } else {
                assert_eq!(row.deviation, "session_replay_suppression");
                assert_ne!(value, row.expected_python);
                differences += 1;
            }
        }
        traces += 1;
    }
    assert_eq!((traces, exact + differences), (43, 175));
    assert!(exact > 0 && differences > 0);
}
fn frame(ms: u64, name: &str, fields: Value) -> GameEvent {
    let at = chrono::DateTime::from_timestamp_millis(1_790_640_000_000 + ms as i64)
        .unwrap()
        .to_rfc3339();
    let mut v = json!({"schema_version":"2026-05-24","adapter":"pure-test","observed_at":at,"event":{"name":name,"source_kind":"system","priority_hint":"background","certainty":"high"},"player":{"dimension":"minecraft:overworld","health":20},"world":{"sky_visible":true,"time_phase":"day","biome":"plains","local_light":15},"combat":{"hostile_outcomes":[]}});
    for (k, value) in fields.as_object().unwrap() {
        if let (Some(target), Some(source)) = (v[k].as_object_mut(), value.as_object()) {
            target.extend(source.clone());
        } else {
            v[k] = value.clone();
        }
    }
    GameEvent::parse(v).unwrap()
}
fn outcome(id: &str, kind: &str, result: &str) -> Value {
    json!({"entity_id":id,"type":kind,"outcome":result,"evidence":"server_death_event"})
}
fn tick(e: &mut Engine, v: &GameEvent, at: u64, complete: bool) {
    e.observe(
        v,
        at,
        complete,
        false,
        &Settings::default(),
        &threats::Settings::default(),
    );
}
struct TestLabels;
impl Labels for TestLabels {
    fn mob_label(&self, id: &str) -> Option<&str> {
        match id {
            "zombie" => Some("ゾンビ"),
            "skeleton" => Some("スケルトン"),
            _ => None,
        }
    }
    fn mob_fallback_label(&self, _: &str) -> Option<&str> {
        None
    }
    fn hostile_label(&self, id: &str) -> String {
        self.mob_label(id).unwrap_or(id).into()
    }
    fn block_label(&self, _: &str) -> Option<&str> {
        None
    }
}
#[test]
fn repeated_death_after_end_does_not_renew_name_ttl_and_current_is_not_presence() {
    let mut engine = Engine::default();
    let mut memory = ChatObservationMemory::default();
    let first = frame(
        0,
        "hostile_defeated",
        json!({"combat":{"hostile_outcomes":[outcome("z","zombie","player_kill")]}}),
    );
    tick(&mut engine, &first, 0, true);
    let updates = engine.take_name_updates();
    assert_eq!(updates.confirmed_types, ["zombie"]);
    memory.observe(&first, &updates, &TestLabels).unwrap();
    let end = frame(1000, "combat_ended", json!({}));
    tick(&mut engine, &end, 1000, true);
    memory
        .observe(&end, &engine.take_name_updates(), &TestLabels)
        .unwrap();
    let repeat = frame(
        5000,
        "hostile_defeated",
        json!({"combat":{"hostile_outcomes":[outcome("z","zombie","player_kill")]}}),
    );
    tick(&mut engine, &repeat, 5000, true);
    let updates = engine.take_name_updates();
    assert!(updates.confirmed_types.is_empty());
    memory.observe(&repeat, &updates, &TestLabels).unwrap();
    let inside = memory
        .snapshot(&frame(10000, "status_snapshot", json!({})), &TestLabels)
        .unwrap();
    assert_eq!(inside.name_context.types, ["zombie"]);
    assert!(inside.current.visual_types.is_empty() && inside.current.hearing_types.is_empty());
    assert!(
        memory
            .snapshot(&frame(10001, "status_snapshot", json!({})), &TestLabels)
            .unwrap()
            .name_context
            .types
            .is_empty()
    );
}
#[test]
fn partial_disappearance_and_empty_outcomes_never_infer_death() {
    let mut engine = Engine::default();
    let seen = frame(
        0,
        "status_snapshot",
        json!({"visual_threats":[{"type":"zombie","entity_id":"z","distance":4}],"combat":{"hostile_outcomes":null}}),
    );
    tick(&mut engine, &seen, 0, true);
    empty(engine.take_name_updates());
    engine.mode = Mode::Alert;
    let gone = frame(
        1000,
        "hostile_audio_detected",
        json!({"combat":{"hostile_outcomes":null}}),
    );
    tick(&mut engine, &gone, 1000, false);
    empty(engine.take_name_updates());
    tick(&mut engine, &gone, 2000, false);
    empty(engine.take_name_updates());
    tick(&mut engine, &seen, 3000, true);
    empty(engine.take_name_updates());
    tick(
        &mut engine,
        &frame(4000, "status_snapshot", json!({})),
        4000,
        true,
    );
    empty(engine.take_name_updates());
}
#[test]
fn names_survive_combat_filtering_without_changing_speech_and_do_not_wait_for_aftermath() {
    let mut outcomes = Outcomes::default();
    let mixed = frame(
        0,
        "creeper_detonated",
        json!({"combat":{"hostile_outcomes":[outcome("z","zombie","player_kill"),outcome("c","creeper","creeper_detonation")]}}),
    );
    let speech = outcomes.observe(&mixed, 0).unwrap();
    assert_eq!(speech.kind, "creeper_detonated");
    assert!(!speech.text.contains("ゾンビ"));
    assert_eq!(outcomes.take_notes(), ["クリーパーが爆発した"]);
    assert_eq!(
        outcomes.take_name_updates().confirmed_types,
        ["zombie", "creeper"]
    );
    let killed = frame(
        1000,
        "hostile_defeated",
        json!({"combat":{"hostile_outcomes":[outcome("z","zombie","player_kill")]}}),
    );
    assert_eq!(
        outcomes.observe(&killed, 1000).unwrap().kind,
        "hostile_defeated"
    );
    empty(outcomes.take_name_updates());
    let end = frame(
        2000,
        "combat_ended",
        json!({"visual_threats":[{"type":"skeleton","entity_id":"alive"}],"combat":{"hostile_outcomes":[outcome("w","witch","other_death")],"hostiles_within_10":1}}),
    );
    assert!(outcomes.observe(&end, 2000).is_none());
    assert_eq!(outcomes.take_name_updates().confirmed_types, ["witch"]);
    assert!(outcomes.aftermath(&end, 2000).is_none());
    empty(outcomes.take_name_updates());
}
#[test]
fn dimension_reset_does_not_turn_an_old_name_delivery_into_a_fresh_update() {
    let mut engine = Engine::default();
    let died = frame(
        0,
        "hostile_defeated",
        json!({"combat":{"hostile_outcomes":[outcome("z","zombie","player_kill")]}}),
    );
    tick(&mut engine, &died, 0, true);
    assert_eq!(engine.take_name_updates().confirmed_types, ["zombie"]);
    tick(
        &mut engine,
        &frame(
            1000,
            "status_snapshot",
            json!({"player":{"dimension":"the_nether"}}),
        ),
        1000,
        true,
    );
    empty(engine.take_name_updates());
    let replay = frame(
        2000,
        "hostile_defeated",
        json!({"player":{"dimension":"the_nether"},"combat":{"hostile_outcomes":[outcome("z","zombie","player_kill")]}}),
    );
    tick(&mut engine, &replay, 2000, true);
    empty(engine.take_name_updates());
}
#[test]
fn pending_snapshot_is_not_a_queue_and_long_session_cannot_replay_evicted_combat_key() {
    let mut o = Outcomes::default();
    for i in 0..4098 {
        let e = frame(
            i,
            "hostile_defeated",
            json!({"combat":{"hostile_outcomes":[outcome(&format!("id{i}"),"zombie","player_kill")]}}),
        );
        o.observe(&e, i);
    }
    assert_eq!(o.take_name_updates().confirmed_types, ["zombie"]);
    empty(o.take_name_updates());
    let replay = frame(
        5000,
        "hostile_defeated",
        json!({"combat":{"hostile_outcomes":[outcome("id0","zombie","player_kill")]}}),
    );
    assert!(o.observe(&replay, 5000).is_some()); // existing 4096-entry combat behavior is untouched
    empty(o.take_name_updates());
    o.observe(
        &frame(
            6000,
            "hostile_defeated",
            json!({"combat":{"hostile_outcomes":[outcome("new","witch","other_death")]}}),
        ),
        6000,
    );
    o.observe(&frame(6001, "status_snapshot", json!({})), 6001);
    empty(o.take_name_updates());
}
#[test]
fn consumed_ids_are_removed_by_observation_memory_even_when_name_hook_is_empty() {
    let mut o = Outcomes::default();
    let mut memory = ChatObservationMemory::default();
    for at in [0, 1000] {
        let event = frame(
            at,
            "hostile_defeated",
            json!({"visual_threats":[{"type":"zombie","entity_id":"dead"},{"type":"skeleton","entity_id":"live"}],"combat":{"hostile_outcomes":[outcome("dead","zombie","player_kill")]}}),
        );
        o.observe(&event, at);
        let update = o.take_name_updates();
        assert_eq!(update.confirmed_types.len(), usize::from(at == 0));
        memory.observe(&event, &update, &TestLabels).unwrap();
        let snap = memory
            .snapshot(&frame(at + 1, "status_snapshot", json!({})), &TestLabels)
            .unwrap();
        assert_eq!(snap.recent.visual_types, ["skeleton"]);
    }
}

#[test]
fn outcome_schema_rejects_missing_null_and_empty_entity_ids() {
    let base = serde_json::to_value(frame(0, "hostile_defeated", json!({}))).unwrap();
    for id in [None, Some(Value::Null), Some(json!(""))] {
        let mut invalid = outcome("valid", "zombie", "player_kill");
        invalid.as_object_mut().unwrap().remove("entity_id");
        if let Some(id) = id {
            invalid["entity_id"] = id;
        }
        let mut event = base.clone();
        event["combat"]["hostile_outcomes"] = json!([invalid]);
        assert!(GameEvent::parse(event).is_err());
    }
}

#[test]
fn consumed_id_keeps_other_and_unidentified_same_species_memos() {
    let mut memory = ChatObservationMemory::default();
    let seen = frame(
        0,
        "status_snapshot",
        json!({
            "visual_threats":[
                {"type":"zombie","entity_id":"dead"},
                {"type":"zombie","entity_id":"live"},
                {"type":"zombie"}
            ],
            "auditory_threats":[
                {"label":"zombie","source_id":"dead"},
                {"label":"zombie","source_id":"live"},
                {"label":"zombie"}
            ]
        }),
    );
    memory.observe(&seen, &NameOutcomeUpdate::default(), &TestLabels).unwrap();
    let died = frame(
        1000,
        "hostile_defeated",
        json!({"combat":{"hostile_outcomes":[outcome("dead","zombie","player_kill")]}}),
    );
    memory.observe(&died, &NameOutcomeUpdate::default(), &TestLabels).unwrap();
    let snapshot = memory.snapshot(&died, &TestLabels).unwrap();
    assert_eq!(snapshot.recent.visual_memos.len(), 2);
    assert!(snapshot.recent.visual_memos.iter().all(|v| v.dedupe_key != "visual:dead"));
    assert_eq!(snapshot.recent.hearing_memos.len(), 2);
    assert!(snapshot.recent.hearing_memos.iter().all(|v| v.dedupe_key != "hostile:dead"));
}
