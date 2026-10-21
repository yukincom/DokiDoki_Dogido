use super::*;
use anyhow::Result;
use serde_json::json;
use std::collections::{BTreeMap, VecDeque};
const LINES: [&str; 3] = ["くさちのひ", "くろきつるぎの", "かげのさむさ"];
fn atoms() -> Vec<SourceAtom> {
    ["草地の日", "黒い剣", "冷たい影"]
        .into_iter()
        .enumerate()
        .map(|(i, text)| SourceAtom {
            atom_id: format!("observation:grounding_recovery:{i}"),
            text: text.into(),
            source_ref: format!("observation:{i}"),
            field_path: "observed_label".into(),
            observation_role: "test".into(),
            kind: "observation".into(),
            claim_class: "factual".into(),
            claim_scopes: vec!["observed_state".into()],
            basis_atom_ids: vec![],
        })
        .collect()
}
fn input() -> Input {
    Input {
        source_atoms: atoms(),
        fallback_text: "まとまらんかった。。。".into(),
        max_tokens: Some(192),
        ..Input::default()
    }
}
fn draft() -> Value {
    json!({"lines":LINES})
}
struct Scripted {
    responses: VecDeque<Value>,
    requests: Vec<StructuredRequest>,
    transforms: Vec<TransformRequest>,
    forms: BTreeMap<String, LineForm>,
    transform_error: bool,
}
impl Scripted {
    fn new(responses: Vec<Value>) -> Self {
        Self {
            responses: responses.into(),
            requests: vec![],
            transforms: vec![],
            forms: BTreeMap::new(),
            transform_error: false,
        }
    }
    fn preset(&mut self, mode: TransformMode, text: &str, replacement: &str, signature: &str) {
        self.forms.insert(
            format!("{mode:?}:{text}"),
            LineForm {
                text: replacement.into(),
                signature: signature.into(),
            },
        );
    }
}
impl Backend for Scripted {
    async fn generate(&mut self, request: StructuredRequest) -> Result<Value> {
        self.requests.push(request);
        let value = self
            .responses
            .pop_front()
            .expect("unexpected extra model request");
        anyhow::ensure!(
            value.get("__test_error").is_none(),
            "simulated generation error"
        );
        Ok(value)
    }
    async fn transform(&mut self, request: TransformRequest) -> Result<LineForm> {
        let output = self
            .forms
            .get(&format!("{:?}:{}", request.mode, request.text))
            .cloned()
            .unwrap_or_else(|| LineForm {
                text: request.text.clone(),
                signature: meter::normalize_term(&request.text)
                    .chars()
                    .filter(|c| c.is_alphanumeric())
                    .collect(),
            });
        self.transforms.push(request);
        anyhow::ensure!(!self.transform_error, "simulated transform failure");
        Ok(output)
    }
}

#[tokio::test]
async fn first_pass_uses_one_generation_and_records_provided_materials() {
    let mut b = Scripted::new(vec![draft()]);
    let r = generate(&mut b, input()).await.unwrap();
    assert!(r.accepted);
    assert_eq!(r.text, LINES.join("\n"));
    assert_eq!(r.regeneration_rounds, 0);
    assert_eq!(b.requests.len(), 1);
    assert_eq!(b.requests[0].kind, "haiku_draft");
    assert_eq!(b.requests[0].route, "haiku");
    assert_eq!(b.requests[0].max_tokens, Some(192));
    assert!(b.requests[0].details.get("source_atoms").is_none());
    for (i, source) in r.line_sources.iter().enumerate() {
        assert_eq!(source.atom_ids, vec![atoms()[i].atom_id.clone()]);
        let material = &b.requests[0].details["available_line_materials"][i]["materials"][0];
        assert_eq!(material["text"], atoms()[i].text);
        assert!(material.get("atom_id").is_none());
    }
    assert!(
        b.transforms
            .iter()
            .all(|t| t.mode == TransformMode::Normalize)
    );
}
#[tokio::test]
async fn invalid_meter_retries_only_target_with_unadopted_material() {
    let mut b = Scripted::new(vec![
        json!({"lines":[LINES[0],"あ",LINES[2]]}),
        json!({"lines":[{"line_index":1,"text":LINES[1]}]}),
    ]);
    let r = generate(&mut b, input()).await.unwrap();
    assert!(r.accepted);
    assert_eq!(r.regeneration_rounds, 1);
    let d = &b.requests[1].details;
    assert_eq!(d["failed_line_indices"], json!([1]));
    assert_eq!(d["available_line_materials"].as_array().unwrap().len(), 1);
    assert_eq!(
        d["available_line_materials"][0]["materials"][0]["text"],
        atoms()[1].text
    );
    assert!(
        !d["available_line_materials"]
            .to_string()
            .contains(&atoms()[0].text)
    );
    assert!(
        !d["available_line_materials"]
            .to_string()
            .contains(&atoms()[2].text)
    );
    assert_eq!(d["current_lines"][0]["frozen"], true);
    assert_eq!(d["current_lines"][2]["text"], LINES[2]);
    assert!(b.requests.iter().all(|r| r.kind != "haiku_line_grounding"));
}
#[tokio::test]
async fn all_current_materials_reach_model_without_splitting_same_source_between_lines() {
    let mut i = input();
    let mut apple = atoms()[0].clone();
    apple.atom_id = "apple-note".into();
    apple.text = "同じリンゴの別の性質".into();
    i.source_atoms.insert(1, apple);
    let mut b = Scripted::new(vec![draft()]);
    let r = generate(&mut b, i.clone()).await.unwrap();
    assert!(r.accepted);
    let delivered = b.requests[0].details["available_line_materials"].to_string();
    for a in &i.source_atoms {
        assert!(delivered.contains(&a.text), "{}", a.text);
    }
    assert_eq!(r.line_sources[0].atom_ids.len(), 2);
    assert_eq!(r.line_sources[1].atom_ids, vec![atoms()[1].atom_id.clone()]);
    assert_eq!(r.line_sources[2].atom_ids, vec![atoms()[2].atom_id.clone()]);
}
#[tokio::test]
async fn composite_preface_does_not_exhaust_materials_on_whole_poem_retry() {
    let mut i = input();
    i.generation_strategy = "whole_poem".into();
    let mut composite = atoms()[0].clone();
    composite.atom_id = "preface".into();
    composite.kind = "preface_clause".into();
    composite.basis_atom_ids = vec![atoms()[0].atom_id.clone(), atoms()[1].atom_id.clone()];
    i.source_atoms.push(composite);
    let mut b = Scripted::new(vec![
        json!({"lines":[LINES[0],"あ",LINES[2]]}),
        json!({"lines":(0..3).map(|n|json!({"line_index":n,"text":LINES[n]})).collect::<Vec<_>>()}),
    ]);
    let r = generate(&mut b, i).await.unwrap();
    assert!(r.accepted);
    assert_eq!(r.regeneration_rounds, 1);
}
#[tokio::test]
async fn material_alias_cannot_repeat_a_fixed_lines_primary_fact() {
    let mut i = input();
    let mut alias = atoms()[0].clone();
    alias.atom_id = "alias".into();
    alias.basis_atom_ids = vec![atoms()[0].atom_id.clone()];
    i.source_atoms.push(alias);
    let mut b = Scripted::new(vec![
        json!({"lines":[LINES[0],"あ",LINES[2]]}),
        json!({"lines":[{"line_index":1,"text":LINES[1]}]}),
    ]);
    let r = generate(&mut b, i).await.unwrap();
    assert!(r.accepted);
    assert_eq!(r.line_sources[1].atom_ids, vec![atoms()[1].atom_id.clone()]);
}
#[tokio::test]
async fn repeated_output_is_not_a_semantic_or_duplication_veto() {
    let mut b = Scripted::new(vec![json!({"lines":["まとまらん",LINES[1],"まとまらん"]})]);
    let r = generate(&mut b, input()).await.unwrap();
    assert!(r.accepted);
    assert_eq!(b.requests.len(), 1);
}
#[tokio::test]
async fn four_strategies_retry_only_the_failed_group() {
    for (strategy, targets) in [
        ("three_slot", vec![1]),
        ("whole_poem", vec![0, 1, 2]),
        ("one_plus_two", vec![1, 2]),
        ("two_plus_one", vec![0, 1]),
    ] {
        let mut i = input();
        i.generation_strategy = strategy.into();
        let retry = json!({"lines":targets.iter().map(|n|json!({"line_index":n,"text":LINES[*n]})).collect::<Vec<_>>()});
        let mut b = Scripted::new(vec![json!({"lines":[LINES[0],"あ",LINES[2]]}), retry]);
        assert!(generate(&mut b, i).await.unwrap().accepted);
        assert_eq!(b.requests[1].details["failed_line_indices"], json!(targets));
    }
}
#[tokio::test]
async fn malformed_draft_and_repeated_failures_consume_the_same_finite_budget() {
    let mut i = input();
    i.max_regeneration_rounds = 2;
    let mut b = Scripted::new(vec![
        json!({"lines":[]}),
        json!({"lines":[]}),
        json!({"lines":[]}),
    ]);
    let r = generate(&mut b, i).await.unwrap();
    assert!(!r.accepted);
    assert_eq!(r.regeneration_rounds, 2);
    assert_eq!(r.failure_reason.as_deref(), Some("max_regeneration_rounds"));
    assert_eq!(b.requests.len(), 3);
    assert_eq!(
        b.requests[1].details["failed_line_indices"],
        json!([0, 1, 2])
    );
}
#[tokio::test]
async fn malformed_regeneration_never_overwrites_fixed_lines() {
    for rows in [
        json!([{"line_index":0,"text":"しろいくも"}]),
        json!([]),
        json!([{"line_index":1,"text":LINES[1]},{"line_index":1,"text":LINES[1]}]),
    ] {
        let mut i = input();
        i.max_regeneration_rounds = 2;
        let mut b = Scripted::new(vec![
            json!({"lines":[LINES[0],"あ",LINES[2]]}),
            json!({"lines":rows}),
            json!({"lines":[{"line_index":1,"text":LINES[1]}]}),
        ]);
        let r = generate(&mut b, i).await.unwrap();
        assert!(r.accepted);
        assert_eq!(r.text, LINES.join("\n"));
        assert_eq!(b.requests[2].details["current_lines"][1]["text"], "あ");
    }
}
#[tokio::test]
async fn line_wraps_and_blank_rows_are_completed_without_an_extra_call() {
    let mut b = Scripted::new(vec![
        json!({"lines":["", "くさち\nのひ", "くろきつるぎの", "かげのさむさ", ""]}),
    ]);
    assert_eq!(
        generate(&mut b, input()).await.unwrap().text,
        LINES.join("\n")
    );
    assert_eq!(b.requests.len(), 1);
}
#[tokio::test]
async fn dictionary_normalization_still_precedes_meter_check() {
    let mut b = Scripted::new(vec![json!({"lines":["草地の日",LINES[1],LINES[2]]})]);
    b.preset(TransformMode::Normalize, "草地の日", LINES[0], LINES[0]);
    assert!(generate(&mut b, input()).await.unwrap().accepted);
}
#[tokio::test]
async fn failed_transport_uses_attempt_budget_but_transform_failure_propagates() {
    let mut i = input();
    i.max_regeneration_rounds = 0;
    let mut b = Scripted::new(vec![json!({"__test_error":true})]);
    let r = generate(&mut b, i).await.unwrap();
    assert!(!r.accepted);
    assert!(r.line_sources.is_empty());
    let mut b = Scripted::new(vec![draft()]);
    b.transform_error = true;
    assert!(generate(&mut b, input()).await.is_err());
}
#[tokio::test]
async fn no_model_call_without_enabled_generation_strategy_and_materials() {
    for case in 0..3 {
        let mut i = input();
        match case {
            0 => i.llm_enabled = false,
            1 => i.generation_strategy = "wrong".into(),
            _ => i.source_atoms.truncate(2),
        }
        let mut b = Scripted::new(vec![]);
        assert!(!generate(&mut b, i).await.unwrap().accepted);
        assert!(b.requests.is_empty());
    }
}
#[test]
fn native_meter_matches_sound_script_and_hard_only_contract() {
    assert_eq!(meter::count_japanese_sounds("きゃくのこえ"), 5);
    assert_eq!(meter::count_japanese_sounds("ゃくのこえ"), 5);
    assert_eq!(meter::count_japanese_sounds("きっぷのひ"), 5);
    assert_eq!(meter::count_japanese_sounds("コーヒー"), 4);
    let mut details = Map::new();
    details.insert(
        "haiku_constraints".into(),
        json!({"forbidden_terms":["ツルハシ"],"player_lessons":[{"forbidden_fragments":["かげ"]}]}),
    );
    assert_eq!(
        meter::line_failure_reasons("つるはしのひ", 0, &details),
        vec!["hard_forbidden_term"]
    );
    assert!(meter::line_failure_reasons("かげさむし", 0, &details).is_empty());
    assert!(
        meter::line_failure_reasons("あいうえお", 0, &details)
            .contains(&"gibberish_sequence".into())
    );
    assert!(
        meter::line_failure_reasons("影さむし", 0, &details).contains(&"invalid_script".into())
    );
    assert!(meter::line_failure_reasons("かげ\nさむし", 0, &details).contains(&"multiline".into()));
    assert_eq!(
        meter::line_failure_reasons("", 0, &details),
        vec!["empty_line"]
    );
    assert_eq!(
        meter::line_failure_reasons("かな", 3, &details),
        vec!["invalid_line_index"]
    );
    assert!(meter::line_failure_reasons("つきよ", 0, &details).contains(&"meter_too_short".into()));
    assert!(
        meter::line_failure_reasons("くろいつるぎの", 0, &details)
            .contains(&"meter_too_long".into())
    );
}
