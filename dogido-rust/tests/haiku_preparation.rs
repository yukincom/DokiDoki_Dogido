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

fn companion_start(turns: Value, name: &str) -> Start {
    let mut row: Value = serde_json::from_str(
        include_str!("../fixtures/haiku-preparation.jsonl")
            .lines()
            .next()
            .unwrap(),
    )
    .unwrap();
    row["start"]["settings"]["llm_enabled"] = json!(true);
    row["start"]["event"]["passive_mobs"] = json!([
        {"type":"wolf","identity":{"entity_id":"wolf","custom_name":"ハク","tamed":true}},
        {"type":"cat","identity":{"entity_id":"cat","custom_name":name,"tamed":true}}]);
    row["start"]["dialogue_material"] = Value::Null;
    row["start"]["completed_turns"] = turns;
    serde_json::from_value(row["start"].clone()).unwrap()
}
#[test]
fn routine_cat_and_wolf_do_not_drive_the_scene_but_one_can_supply_background() {
    let start = companion_start(json!([]), "クロちゃん");
    let original = json!(start.event);
    let (mut prep, context) = Preparation::capture(start.clone()).unwrap();
    let request = json!(context.request.unwrap().details);
    assert_eq!(request["passive_mobs"], json!([]));
    assert!(!request.to_string().contains("クロちゃん"));
    assert!(!request.to_string().contains("wolf"));
    let inspiration = prep.inspiration(&json!({"found":false})).unwrap();
    assert!(
        !json!(inspiration.request.details)
            .to_string()
            .contains("クロちゃん")
    );
    let result = prep.materials(&json!({"found":false})).unwrap();
    assert_eq!(
        result.materials["background_companions"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let references: std::collections::HashSet<_> = result
        .input
        .source_atoms
        .iter()
        .filter(|a| a.observation_role == "background_companion")
        .map(|a| a.source_ref.clone())
        .collect();
    assert_eq!(
        references.len(),
        1,
        "routine followers cannot make a two-pet contrast"
    );
    assert_eq!(
        json!(start.event),
        original,
        "material projection cannot erase observations"
    );
}
#[test]
fn completed_pet_topic_can_be_primary_and_name_and_species_share_a_source() {
    let turns = json!([{"turn_id":"pet-turn","player_text":"クロちゃんがクリーパーを追い払った","dogido_text":"頼もしいな。"}]);
    let (mut prep, context) = Preparation::capture(companion_start(turns, "クロちゃん")).unwrap();
    let request = json!(context.request.unwrap().details);
    assert!(request["passive_mobs"].to_string().contains("クロちゃん"));
    assert!(!request["passive_mobs"].to_string().contains("ハク"));
    let sources = request["catalog_sources"].as_array().unwrap();
    let cat = sources
        .iter()
        .find(|s| s["source_ref"] == "mob_individual:cat")
        .unwrap();
    assert!(
        cat["label"].as_str().unwrap().contains("クロちゃん")
            && cat["label"].as_str().unwrap().contains("ネコ")
    );
    assert!(cat["extra_fields"].to_string().contains("くろちゃん"));
    prep.inspiration(&json!({"found":false})).unwrap();
    let result = prep.materials(&json!({"found":false})).unwrap();
    assert!(result.input.source_atoms.iter().any(|a|a.claim_scopes==["player_reported_context"] && a.text.contains("クロちゃん")));
    assert!(
        !result
            .input
            .source_atoms
            .iter()
            .any(|a| a.observation_role == "passive_mob" && a.text.contains("追い払"))
    );
}
#[test]
fn pet_name_needs_kana_or_a_registered_reading_and_assistant_cannot_promote_it() {
    let turns = json!([{"turn_id":"pet-turn","player_text":"日差しがいいね","dogido_text":"クロちゃんはかわいいな。"}]);
    let (_, context) = Preparation::capture(companion_start(turns, "クロちゃん")).unwrap();
    assert_eq!(context.request.unwrap().details["passive_mobs"], json!([]));
    let turns =
        json!([{"turn_id":"pet-turn","player_text":"漆黒が頑張った","dogido_text":"頼もしいな。"}]);
    let mut start = companion_start(turns, "漆黒");
    let (_, context) = Preparation::capture(start.clone()).unwrap();
    assert!(
        !json!(context.request.unwrap().details)
            .to_string()
            .contains("漆黒")
    );
    start
        .reading_corrections
        .push(json!({"surface":"漆黒","reading":"くろ","forbidden_readings":[]}));
    let (_, context) = Preparation::capture(start).unwrap();
    let details = json!(context.request.unwrap().details);
    assert!(details["catalog_sources"].to_string().contains("漆黒"));
    assert!(details["catalog_sources"].to_string().contains("くろ"));
}

#[test]
fn pet_topic_after_the_short_summary_still_promotes_only_that_pet() {
    let turns = json!([{"turn_id":"pet-turn","player_text":"さっきクリーパーが近づいたときにクロちゃんが追い払った","dogido_text":"頼もしいな。"}]);
    let (_, context) = Preparation::capture(companion_start(turns, "クロちゃん")).unwrap();
    let details = context.request.unwrap().details;
    assert!(details["passive_mobs"].to_string().contains("クロちゃん"));
    assert!(!details["passive_mobs"].to_string().contains("ハク"));
}
