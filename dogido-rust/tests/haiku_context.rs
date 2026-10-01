use dogido_rust::{
    chat_catalog,
    events::GameEvent,
    haiku::{
        context::{Irony, Scene, scene_for_spoken_irony},
        materials::{self, Entries, ReadingSnapshot, RuntimeRead},
    },
    world_catalog,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::HashMap;
#[derive(Deserialize)]
struct EntryFixture {
    items: HashMap<String, Value>,
    blocks: HashMap<String, Value>,
    mob_labels: HashMap<String, String>,
}
fn norm(id: &str) -> String {
    id.rsplit(':')
        .next()
        .unwrap_or("")
        .trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
        .to_lowercase()
}
impl Entries for EntryFixture {
    fn item_entry(&self, id: &str) -> Option<&Value> {
        self.items.get(&norm(id))
    }
    fn block_entry(&self, id: &str) -> Option<&Value> {
        self.blocks.get(&norm(id))
    }
    fn mob_label(&self, id: &str) -> String {
        self.mob_labels
            .get(id)
            .cloned()
            .unwrap_or_else(|| id.into())
    }
}
fn entries() -> EntryFixture {
    serde_json::from_str(include_str!("../fixtures/haiku-context-entries.json")).unwrap()
}
#[test]
fn canonical_contexts_and_all_detail_projections() {
    let entries = entries();
    let world = world_catalog::catalog();
    let catalog = chat_catalog::catalog();
    let mut count = 0;
    for (index, line) in include_str!("../fixtures/haiku-context.jsonl")
        .lines()
        .enumerate()
    {
        let row: Value = serde_json::from_str(line).unwrap();
        let event: GameEvent = serde_json::from_value(row["event"].clone()).unwrap();
        let order: Vec<_> = row["event"]["inventory"]
            .as_object()
            .unwrap()
            .keys()
            .cloned()
            .collect();
        let readings: ReadingSnapshot = serde_json::from_value(row["readings"].clone()).unwrap();
        let context = materials::capture(
            &event,
            RuntimeRead {
                current_structure: row["current_structure"].as_str(),
                player_name: row["player_name"].as_str().unwrap(),
                inventory_order: &order,
            },
            world,
            catalog,
            &entries,
            &readings,
        )
        .unwrap();
        assert_eq!(
            serde_json::to_value(&context).unwrap(),
            row["expected"],
            "context {index}"
        );
        let irony: Irony = serde_json::from_value(row["irony"].clone()).unwrap();
        let scene = scene_for_spoken_irony(&irony, &Scene::default(), &context.source_atoms);
        assert_eq!(
            serde_json::to_value(&scene).unwrap(),
            row["lifted"],
            "scene {index}"
        );
        assert_eq!(
            json!({"irony":context.irony_details(),"scene":context.scene_details(Some(&irony)),"prompt":context.prompt_details(Some(&irony),Some(&scene))}),
            row["details"],
            "details {index}"
        );
        assert!(context.feature_candidates.len() <= 14);
        count += 1;
    }
    assert!(count > 300);
}
#[test]
fn canonical_constraints_keep_soft_notes_separate() {
    let rows: Vec<Value> =
        serde_json::from_str(include_str!("../fixtures/haiku-constraints.json")).unwrap();
    for (index, row) in rows.iter().enumerate() {
        let event: GameEvent = serde_json::from_value(row["event"].clone()).unwrap();
        let scene = Scene {
            motifs: serde_json::from_value(row["motifs"].clone()).unwrap(),
            ..Scene::default()
        };
        let readings: ReadingSnapshot =
            serde_json::from_value(row.get("readings").cloned().unwrap_or_else(|| json!({})))
                .unwrap();
        let value = materials::constraint_details(
            &event,
            &scene,
            world_catalog::catalog(),
            &readings,
            row["lessons"].as_array().unwrap(),
        );
        assert_eq!(json!(value), row["expected"], "constraint {index}");
        assert!(
            !value.unwrap_or_else(|| json!({"forbidden_terms":[]}))["forbidden_terms"]
                .as_array()
                .unwrap()
                .contains(&json!("あめ"))
        );
    }
}
#[test]
fn canonical_irony_mapping() {
    let rows: Vec<Value> =
        serde_json::from_str(include_str!("../fixtures/haiku-irony.json")).unwrap();
    for (i, row) in rows.iter().enumerate() {
        let actual = Irony::from_mapping(&row["payload"]);
        if row["error"].is_null() {
            assert_eq!(json!(actual.unwrap()), row["expected"], "irony {i}");
        } else {
            assert!(actual.is_err(), "irony {i}");
        }
    }
}
#[test]
fn inventory_order_is_required_and_captured_context_is_owned() {
    let row: Value = serde_json::from_str(
        include_str!("../fixtures/haiku-context.jsonl")
            .lines()
            .next()
            .unwrap(),
    )
    .unwrap();
    let e: GameEvent = serde_json::from_value(row["event"].clone()).unwrap();
    assert!(materials::poem_item(&e, world_catalog::catalog(), &[]).is_err());
    let order: Vec<_> = row["event"]["inventory"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();
    let entries = entries();
    let context = materials::capture(
        &e,
        RuntimeRead {
            current_structure: Some("village"),
            player_name: "固定",
            inventory_order: &order,
        },
        world_catalog::catalog(),
        chat_catalog::catalog(),
        &entries,
        &ReadingSnapshot::default(),
    )
    .unwrap();
    let before = context.irony_details();
    let mut next = row["event"].clone();
    next["player"]["held_item"] = json!("apple");
    let next: GameEvent = serde_json::from_value(next).unwrap();
    let _other = materials::capture(
        &next,
        RuntimeRead {
            current_structure: None,
            player_name: "次",
            inventory_order: &order,
        },
        world_catalog::catalog(),
        chat_catalog::catalog(),
        &entries,
        &ReadingSnapshot::default(),
    )
    .unwrap();
    assert_eq!(context.irony_details(), before);
}
#[test]
fn canonical_weight_and_work_tool_rules() {
    let rows: Vec<Value> =
        serde_json::from_str(include_str!("../fixtures/haiku-selection.json")).unwrap();
    for r in rows {
        let id = r["id"].as_str().unwrap();
        assert_eq!(
            materials::pocket_weight(id, r["count"].as_i64().unwrap()),
            r["weight"].as_i64().unwrap(),
            "weight {id}"
        );
        assert_eq!(
            materials::is_work_tool(id),
            r["work_tool"].as_bool().unwrap(),
            "tool {id}"
        );
    }
}
#[test]
fn canonical_scene_input_and_spoken_irony_basis() {
    let rows: Vec<Value> =
        serde_json::from_str(include_str!("../fixtures/haiku-scene.json")).unwrap();
    for (i, r) in rows.iter().enumerate() {
        let atoms: Vec<dogido_rust::haiku::SourceAtom> =
            serde_json::from_value(r["atoms"].clone()).unwrap();
        let result = Scene::from_mapping(&r["payload"], &atoms);
        if r["error"].is_null() {
            assert_eq!(json!(result.unwrap()), r["expected"], "scene parse {i}");
        } else {
            assert!(result.is_err());
        }
    }
    let rows: Vec<Value> =
        serde_json::from_str(include_str!("../fixtures/haiku-scene-lift.json")).unwrap();
    for (i, r) in rows.iter().enumerate() {
        let atoms: Vec<dogido_rust::haiku::SourceAtom> =
            serde_json::from_value(r["atoms"].clone()).unwrap();
        let irony = serde_json::from_value(r["irony"].clone()).unwrap();
        let scene = serde_json::from_value(r["scene"].clone()).unwrap();
        assert_eq!(
            json!(scene_for_spoken_irony(&irony, &scene, &atoms)),
            r["expected"],
            "lift {i}"
        );
        assert_eq!(
            json!(dogido_rust::haiku::context::irony_basis_atom_ids(
                &irony, &atoms
            )),
            r["basis"],
            "basis {i}"
        );
    }
}
