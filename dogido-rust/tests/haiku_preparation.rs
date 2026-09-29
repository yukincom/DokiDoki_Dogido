use dogido_rust::{
    haiku::{
        GroundedHaikuResult,
        materials::Entries,
        preparation::{self, Preparation, Reading, Start},
    },
    world_catalog,
};
use serde_json::{Value, json};
struct Dictionary(Value);
impl Reading for Dictionary {
    async fn hiraganize(&mut self, surface: &str) -> anyhow::Result<String> {
        Ok(self
            .0
            .get(surface)
            .and_then(Value::as_str)
            .unwrap_or(surface)
            .into())
    }
}
#[test]
fn canonical_raw_catalog_entries_are_not_fixture_backed() {
    let fixture: Value =
        serde_json::from_str(include_str!("../fixtures/haiku-preparation-entries.json")).unwrap();
    let world = world_catalog::catalog();
    for (kind, key) in [(true, "items"), (false, "blocks")] {
        for (id, expected) in fixture[key].as_object().unwrap() {
            let actual = if kind {
                world.item_entry(id)
            } else {
                world.block_entry(id)
            };
            assert_eq!(actual, Some(expected), "{key}/{id}");
        }
    }
    for (id, expected) in fixture["mob_labels"].as_object().unwrap() {
        assert_eq!(world.mob_label(id), expected.as_str().unwrap(), "mob {id}");
        assert_eq!(
            world.mob_label(&format!("minecraft:{id}")),
            format!("minecraft:{id}")
        );
    }
}
#[tokio::test]
async fn canonical_persistent_preparation_traces() {
    let mut n = 0;
    for (i, line) in include_str!("../fixtures/haiku-preparation.jsonl")
        .lines()
        .enumerate()
    {
        let row: Value = serde_json::from_str(line).unwrap();
        let start: Start = serde_json::from_value(row["start"].clone()).unwrap();
        let (mut prep, context) = Preparation::capture(start).unwrap();
        assert_eq!(json!(context), row["context"], "context {i}");
        if context.fixed_text.is_none() {
            let mut inspiration = prep.inspiration(&row["irony_payload"]).unwrap();
            assert_eq!(json!(inspiration), row["inspiration"], "inspiration {i}");
            inspiration.text.clear();
            inspiration.request.details.clear();
            let mut materials = prep.materials(&row["scene_payload"]).unwrap();
            assert_eq!(json!(materials), row["materials"], "materials {i}");
            materials.materials.clear();
            materials.input.source_atoms.clear();
        }
        let result: GroundedHaikuResult = serde_json::from_value(row["result"].clone()).unwrap();
        let emission = prep
            .emission(&result, &mut Dictionary(row["readings"].clone()))
            .await
            .unwrap();
        assert_eq!(json!(emission), row["emission"], "emission {i}");
        assert!(
            prep.emission(&result, &mut Dictionary(json!({})))
                .await
                .is_err()
        );
        assert!(prep.inspiration(&json!({"found":false})).is_err());
        n += 1;
    }
    assert!(n > 200);
}
#[tokio::test]
async fn stage_rejection_does_not_consume_job_or_allow_fixed_substitution() {
    let row: Value = serde_json::from_str(
        include_str!("../fixtures/haiku-preparation.jsonl")
            .lines()
            .next()
            .unwrap(),
    )
    .unwrap();
    let start: Start = serde_json::from_value(row["start"].clone()).unwrap();
    let (mut job, context) = Preparation::capture(start.clone()).unwrap();
    assert!(context.fixed_text.is_some());
    assert!(job.inspiration(&Value::Null).is_err());
    assert!(job.materials(&Value::Null).is_err());
    let mut result: GroundedHaikuResult = serde_json::from_value(row["result"].clone()).unwrap();
    result.text = "はなのかげ ゆれるこもれび かぜをきく".into();
    assert!(
        job.emission(&result, &mut Dictionary(json!({})))
            .await
            .is_err()
    );
    result.text = context.fixed_text.unwrap();
    assert!(
        job.emission(&result, &mut Dictionary(row["readings"].clone()))
            .await
            .is_ok()
    );
    let mut enabled = start;
    enabled.settings.llm_enabled = true;
    let (mut job, _) = Preparation::capture(enabled).unwrap();
    assert!(job.materials(&Value::Null).is_err());
    assert!(
        job.emission(&result, &mut Dictionary(json!({})))
            .await
            .is_err()
    );
    job.inspiration(&json!({"elements":1})).unwrap();
    assert!(job.inspiration(&Value::Null).is_err());
    job.materials(&json!({"motifs":1})).unwrap();
    result.accepted = false;
    assert!(
        job.emission(&result, &mut Dictionary(json!({})))
            .await
            .is_err()
    );
    result.accepted = true;
    result.text = preparation::fallback::failed_text();
    assert!(
        job.emission(&result, &mut Dictionary(json!({})))
            .await
            .is_err()
    );
}

#[test]
fn canonical_fixed_catalog_rules_and_completed_dialogue_boundaries() {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("../fixtures/haiku-preparation-fallback.json")).unwrap();
    for (i, row) in cases.iter().enumerate() {
        let event = serde_json::from_value(row["event"].clone()).unwrap();
        assert_eq!(
            preparation::fallback::fixed_text(&event),
            row["expected"],
            "fixed {i}"
        );
    }
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("../fixtures/haiku-preparation-dialogue.json")).unwrap();
    for row in cases {
        let output = preparation::dialogue_material(row["turns"].as_array().unwrap());
        if row["error"].is_null() {
            assert_eq!(output.unwrap(), row["expected"], "{row}");
        } else {
            assert!(output.is_err());
        }
    }
}

#[test]
fn preparation_retains_settings_domain_validation() {
    let row: Value = serde_json::from_str(
        include_str!("../fixtures/haiku-preparation.jsonl")
            .lines()
            .next()
            .unwrap(),
    )
    .unwrap();
    let start: Start = serde_json::from_value(row["start"].clone()).unwrap();
    for rounds in [-1, 9] {
        let mut invalid = start.clone();
        invalid.settings.max_regeneration_rounds = rounds;
        assert!(Preparation::capture(invalid).is_err());
    }
    let mut invalid = start.clone();
    invalid.settings.grounding_max_tokens = 0;
    assert!(Preparation::capture(invalid).is_err());
    let mut invalid = start;
    invalid.settings.generation_strategy = "unknown".into();
    assert!(Preparation::capture(invalid).is_err());
}
