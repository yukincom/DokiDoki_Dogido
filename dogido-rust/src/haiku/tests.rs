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
fn report(indices: &[usize]) -> Value {
    let verdicts: Map<_, _> = indices
        .iter()
        .map(|i| (i.to_string(), json!("pass")))
        .collect();
    json!({"verdicts":verdicts,"assessments":indices.iter().map(|i|json!({"line_index":i,"atom_ids":[i+1]})).collect::<Vec<_>>(),"failure_reasons":{}})
}
fn draft() -> Value {
    json!({"lines":LINES})
}
fn full() -> Value {
    report(&[0, 1, 2])
}
fn negative(indices: &[usize]) -> Value {
    let mut r = full();
    for i in indices {
        r["verdicts"][i.to_string()] = json!("meaning_fail");
        r["assessments"][*i]["atom_ids"] = json!([]);
        r["failure_reasons"][i.to_string()] = json!("材料の意味と合っていない。");
    }
    r
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
async fn first_pass_preserves_sources_numbering_and_separate_budget() {
    let mut b = Scripted::new(vec![draft(), full()]);
    let mut i = input();
    i.grounding_max_tokens = 600;
    let r = generate(&mut b, i).await.unwrap();
    assert!(r.accepted);
    assert_eq!(r.text, LINES.join("\n"));
    assert_eq!(r.regeneration_rounds, 0);
    assert_eq!(r.prompt_variant, PROMPT_VARIANT);
    assert!(r.failure_reason.is_none());
    assert_eq!(r.line_sources.len(), 3);
    for (n, row) in r.line_sources.iter().enumerate() {
        assert_eq!(row.atom_ids, vec![atoms()[n].atom_id.clone()]);
        assert_eq!(row.sources, vec![atoms()[n].clone()]);
    }
    assert_eq!(
        b.requests.iter().map(|r| r.max_tokens).collect::<Vec<_>>(),
        vec![Some(192), Some(600)]
    );
    assert_eq!(b.requests[0].route, "haiku");
    assert_eq!(b.requests[0].temperature, 0.60);
    assert_eq!(b.requests[1].route, "chat");
    assert_eq!(b.requests[1].temperature, 0.0);
    assert_eq!(
        b.requests[1].details["grounding_atom_numbers"][&atoms()[2].atom_id],
        3
    );
    assert_eq!(
        b.transforms
            .iter()
            .filter(|r| r.mode == TransformMode::Correct)
            .count(),
        3
    );
}
#[tokio::test]
async fn missing_middle_reassesses_only_original_line_once() {
    let mut b = Scripted::new(vec![draft(), report(&[0, 2]), report(&[1])]);
    let r = generate(&mut b, input()).await.unwrap();
    assert!(r.accepted);
    assert_eq!(r.text, LINES.join("\n"));
    assert_eq!(r.regeneration_rounds, 0);
    assert_eq!(
        b.requests[2].details["grounding_lines"],
        json!([{"line_index":1,"text":LINES[1]}])
    );
    assert!(
        b.requests
            .iter()
            .skip(1)
            .all(|r| r.kind == "haiku_line_grounding")
    );
}
#[tokio::test]
async fn unavailable_is_not_six_rewrites_and_missing_each_line_retries_once() {
    let broken = json!({"__dogido_status":"output_truncated","assessments":[]});
    let mut b = Scripted::new(vec![draft(), report(&[0, 2]), broken.clone()]);
    let r = generate(&mut b, input()).await.unwrap();
    assert_eq!(r.failure_reason.as_deref(), Some("grounding_unavailable"));
    assert_eq!(r.regeneration_rounds, 0);
    assert_eq!(b.requests.len(), 3);
    assert_eq!(r.text, input().fallback_text);
    let mut b = Scripted::new(vec![
        draft(),
        broken.clone(),
        broken.clone(),
        broken.clone(),
        broken,
    ]);
    let r = generate(&mut b, input()).await.unwrap();
    assert!(!r.accepted);
    assert_eq!(b.requests.len(), 5);
    for (n, text) in LINES.iter().enumerate() {
        assert_eq!(
            b.requests[n + 2].details["grounding_lines"],
            json!([{"line_index":n,"text":text}])
        );
    }
}
#[tokio::test]
async fn malformed_refs_types_verdicts_and_contradictions_are_unknown_not_content_failure() {
    for bad in [
        json!([0]),
        json!([4]),
        json!([true]),
        json!([1.0]),
        json!(["1"]),
        json!([1, 1]),
        json!([]),
        json!(["made:up"]),
    ] {
        let mut invalid = full();
        invalid["assessments"][0]["atom_ids"] = bad;
        let mut b = Scripted::new(vec![draft(), invalid.clone(), invalid]);
        let r = generate(&mut b, input()).await.unwrap();
        assert_eq!(r.failure_reason.as_deref(), Some("grounding_unavailable"));
        assert_eq!(b.requests.len(), 3);
    }
    for field in ["index", "verdict", "contradiction"] {
        let mut invalid = full();
        match field {
            "index" => invalid["assessments"][0]["line_index"] = json!(true),
            "verdict" => invalid["verdicts"]["0"] = json!("PASS"),
            _ => invalid["assessments"][0]["meaning_retained"] = json!(false),
        }
        let mut b = Scripted::new(vec![draft(), invalid.clone(), invalid]);
        let r = generate(&mut b, input()).await.unwrap();
        assert_eq!(r.failure_reason.as_deref(), Some("grounding_unavailable"));
    }
}
#[tokio::test]
async fn negative_with_empty_evidence_regenerates_and_eligible_ids_are_renumbered() {
    let mut bad = negative(&[1]);
    bad["failure_reasons"]["0"] = json!("合格理由は捨てる");
    let fixed = json!({"verdicts":{"1":"pass"},"assessments":[{"line_index":1,"atom_ids":[1]}]});
    let mut b = Scripted::new(vec![
        draft(),
        bad,
        json!({"lines":[{"line_index":1,"text":"くろいつるぎの"}]}),
        fixed,
    ]);
    let r = generate(&mut b, input()).await.unwrap();
    assert!(r.accepted);
    assert_eq!(r.regeneration_rounds, 1);
    assert_eq!(r.line_sources[1].atom_ids, vec![atoms()[1].atom_id.clone()]);
    let rows = &b.requests[2].details["current_lines"];
    assert_eq!(
        rows[0],
        json!({"line_index":0,"text":LINES[0],"frozen":true})
    );
    assert_eq!(rows[1]["assessment_comment"], "材料の意味と合っていない。");
    assert_eq!(rows[1]["meter_status"], "within_range");
    assert_eq!(
        b.requests[3].details["grounding_atom_numbers"],
        json!({atoms()[1].atom_id.clone():1})
    );
    assert_eq!(
        b.transforms
            .iter()
            .filter(|r| r.line_index == 0 && r.mode == TransformMode::Correct)
            .count(),
        1
    );
}

#[tokio::test]
async fn grounded_reason_array_keeps_up_to_three_actual_failures_and_both_verdict_axes() {
    let mut bad = negative(&[1]);
    bad["verdicts"]["1"] = json!("both_fail");
    bad["failure_reasons"]["1"] = json!([
        {"kind":"meaning","fragment":"つるぎ","reason":"材料との意味の対応がない"},
        {"kind":"japanese","fragment":"くろき","reason":"この用例では修飾先が不明"},
        {"kind":"japanese","fragment":"の","reason":"この用例では後ろの言葉につながらない"},
        {"kind":"japanese","fragment":"の","reason":"第四の理由は伝えない"}
    ]);
    let mut b = Scripted::new(vec![draft(), bad, json!({"lines":[]})]);
    let mut i = input();
    i.max_regeneration_rounds = 1;
    let r = generate(&mut b, i).await.unwrap();
    assert_eq!(r.failure_reason.as_deref(), Some("max_regeneration_rounds"));
    let row = &b.requests[2].details["current_lines"][1];
    assert_eq!(
        row["failure_reasons"],
        json!(["meaning_not_retained", "unnatural_japanese"])
    );
    assert_eq!(
        row["assessment_comment"],
        "意味「つるぎ」:材料との意味の対応がない、日本語「くろき」:この用例では修飾先が不明、日本語「の」:この用例では後ろの言葉につながらない"
    );
    assert!(row["assessment_comment"].as_str().unwrap().chars().count() <= 240);
    assert_eq!(
        b.requests.len(),
        3,
        "diagnostics do not add an assessment call"
    );
}

#[tokio::test]
async fn malformed_or_absent_reasons_do_not_change_a_valid_negative_verdict() {
    for reason in [
        Value::Null,
        json!([]),
        json!([null, true, "旧文字列は配列内では受けない"]),
        json!([{"kind":"meaning","fragment":"つるぎ","reason":"合格した軸への指摘"}]),
        json!([{"kind":"japanese","fragment":"存在しない語","reason":"その断片は句にない"}]),
        json!([{"kind":"japanese","fragment":"","reason":"空の断片"}]),
        json!([{"kind":"japanese","fragment":"つるぎ","reason":""}]),
        json!([{"kind":"japanese","fragment":"つるぎ","reason":"長".repeat(49)}]),
        json!([{"kind":"japanese","fragment":"つるぎ","reason":"一行\n二行"}]),
        json!([{"kind":"unknown","fragment":"つるぎ","reason":"未知の軸"}]),
        json!([{"kind":"japanese","fragment":"つるぎ"}]),
        json!([{"kind":"japanese","fragment":"つるぎ","reason":"理由","extra":true}]),
    ] {
        let mut bad = full();
        bad["verdicts"]["1"] = json!("japanese_fail");
        bad["failure_reasons"]["1"] = reason.clone();
        let mut b = Scripted::new(vec![draft(), bad, json!({"lines":[]})]);
        let mut i = input();
        i.max_regeneration_rounds = 1;
        let r = generate(&mut b, i).await.unwrap();
        assert_eq!(
            r.failure_reason.as_deref(),
            Some("max_regeneration_rounds"),
            "{reason}"
        );
        let row = &b.requests[2].details["current_lines"][1];
        assert_eq!(
            row["failure_reasons"],
            json!(["unnatural_japanese"]),
            "{reason}"
        );
        assert_eq!(row["assessment_comment"], "", "{reason}");
        assert_eq!(b.requests.len(), 3, "{reason}");
    }
}

#[tokio::test]
async fn duplicate_reasons_are_not_extra_failures_and_pass_reasons_remain_ignored() {
    let reason = json!({"kind":"japanese","fragment":"つるぎ","reason":"この断片の使い方が不自然"});
    let mut bad = full();
    bad["verdicts"]["1"] = json!("japanese_fail");
    bad["failure_reasons"]["1"] = json!([reason, reason, reason]);
    let mut b = Scripted::new(vec![draft(), bad, json!({"lines":[]})]);
    let mut i = input();
    i.max_regeneration_rounds = 1;
    generate(&mut b, i).await.unwrap();
    assert_eq!(
        b.requests[2].details["current_lines"][1]["assessment_comment"],
        "日本語「つるぎ」:この断片の使い方が不自然"
    );

    let mut pass = full();
    pass["failure_reasons"]["1"] = json!([reason]);
    let mut b = Scripted::new(vec![draft(), pass]);
    assert!(generate(&mut b, input()).await.unwrap().accepted);
    assert_eq!(b.requests.len(), 2);
}

#[tokio::test]
async fn a_new_candidate_replaces_old_feedback_before_a_repeated_rewrite() {
    let mut first = negative(&[1]);
    first["failure_reasons"]["1"] =
        json!([{"kind":"meaning","fragment":"くろき","reason":"最初の候補だけへの指摘"}]);
    let second = "くろいつるぎの";
    let check_second = json!({"verdicts":{"1":"japanese_fail"},"assessments":[{"line_index":1,"atom_ids":[1]}],
        "failure_reasons":{"1":[{"kind":"japanese","fragment":"くろい","reason":"二つ目の候補だけへの指摘"}]}});
    let mut b = Scripted::new(vec![
        draft(),
        first,
        json!({"lines":[{"line_index":1,"text":second}]}),
        check_second,
        json!({"lines":[{"line_index":1,"text":second}]}),
        json!({"lines":[{"line_index":1,"text":"つるぎひかるよ"}]}),
        json!({"verdicts":{"1":"pass"},"assessments":[{"line_index":1,"atom_ids":[1]}]}),
    ]);
    let r = generate(&mut b, input()).await.unwrap();
    assert!(r.accepted);
    let repeated = &b.requests[5].details["current_lines"][1];
    assert_eq!(repeated["text"], second);
    assert_eq!(
        repeated["failure_reasons"],
        json!(["unnatural_japanese", "duplicate_candidate"])
    );
    assert_eq!(
        repeated["assessment_comment"],
        "日本語「くろい」:二つ目の候補だけへの指摘"
    );
    assert!(!repeated.to_string().contains("最初の候補"));
    assert_eq!(
        b.requests
            .iter()
            .filter(|r| r.kind == "haiku_line_grounding")
            .count(),
        3
    );
}

#[tokio::test]
async fn correction_does_not_attach_a_reason_quote_to_different_displayed_text() {
    let mut bad = full();
    bad["verdicts"]["1"] = json!("japanese_fail");
    bad["failure_reasons"]["1"] =
        json!([{"kind":"japanese","fragment":"くろき","reason":"補正前の断片への指摘"}]);
    let mut b = Scripted::new(vec![draft(), bad, json!({"lines":[]})]);
    b.preset(
        TransformMode::Correct,
        LINES[1],
        "くろいつるぎの",
        "くろいつるぎの",
    );
    let mut i = input();
    i.max_regeneration_rounds = 1;
    generate(&mut b, i).await.unwrap();
    let row = &b.requests[2].details["current_lines"][1];
    assert_eq!(row["text"], "くろいつるぎの");
    assert_eq!(row["failure_reasons"], json!(["unnatural_japanese"]));
    assert_eq!(row["assessment_comment"], "");
}
#[tokio::test]
async fn legacy_and_top_level_single_assessments_are_accepted_with_exact_ids() {
    let legacy = json!({"assessments":[{"line_index":0,"atom_ids":[atoms()[0].atom_id],"meaning_retained":true,"natural_japanese":true},{"line_index":2,"atom_ids":[atoms()[2].atom_id],"meaning_retained":true,"natural_japanese":true}]});
    let single = json!({"line_index":1,"atom_ids":[atoms()[1].atom_id],"meaning_retained":true,"natural_japanese":true});
    let mut b = Scripted::new(vec![draft(), legacy, single]);
    assert!(generate(&mut b, input()).await.unwrap().accepted);
}
#[tokio::test]
async fn four_strategies_expand_only_dependencies_and_freeze_the_other_slots() {
    for (strategy, targets) in [
        ("whole_poem", vec![0, 1, 2]),
        ("three_slot", vec![1]),
        ("one_plus_two", vec![1, 2]),
        ("two_plus_one", vec![0, 1]),
    ] {
        let texts = ["つきのかげ", "みどりのもりに", "しろいくも"];
        let regen = json!({"lines":targets.iter().map(|i|json!({"line_index":i,"text":texts[*i]})).collect::<Vec<_>>()});
        let verdicts: Map<_, _> = targets
            .iter()
            .map(|i| (i.to_string(), json!("pass")))
            .collect();
        let checks = json!({"verdicts":verdicts,"assessments":targets.iter().enumerate().map(|(n,i)|json!({"line_index":i,"atom_ids":[n+1]})).collect::<Vec<_>>()});
        let mut b = Scripted::new(vec![draft(), negative(&[1]), regen, checks]);
        let mut i = input();
        i.generation_strategy = strategy.into();
        let r = generate(&mut b, i).await.unwrap();
        assert!(r.accepted, "{strategy}: {r:?}");
        assert_eq!(b.requests[2].details["failed_line_indices"], json!(targets));
        assert_eq!(r.regeneration_rounds, 1);
        for (index, text) in LINES.iter().enumerate() {
            assert_eq!(
                b.requests[2].details["current_lines"][index]["frozen"],
                !targets.contains(&index)
            );
            if !targets.contains(&index) {
                assert_eq!(r.line_sources[index].text, *text);
            }
        }
    }
}
#[tokio::test]
async fn reuse_primary_or_basis_fails_but_shared_poetic_interpretation_does_not_reserve() {
    let mut reused = full();
    reused["assessments"][1]["atom_ids"] = json!([1]);
    let mut b = Scripted::new(vec![draft(), reused, json!({"lines":[]})]);
    let mut i = input();
    i.max_regeneration_rounds = 1;
    let r = generate(&mut b, i).await.unwrap();
    assert!(!r.accepted);
    assert_eq!(
        b.requests[2].details["current_lines"][1]["failure_reasons"],
        json!(["source_reused"])
    );
    let mut i = input();
    let base = i.source_atoms[0].atom_id.clone();
    let mut derived = i.source_atoms[0].clone();
    derived.atom_id = "derived:clause".into();
    derived.kind = "preface_clause".into();
    derived.basis_atom_ids = vec![base];
    i.source_atoms.push(derived);
    let mut checks = full();
    checks["assessments"][1]["atom_ids"] = json!([4]);
    i.max_regeneration_rounds = 0;
    let mut b = Scripted::new(vec![draft(), checks]);
    assert_eq!(
        generate(&mut b, i.clone())
            .await
            .unwrap()
            .failure_reason
            .as_deref(),
        Some("max_regeneration_rounds")
    );
    i.source_atoms[3].kind = "poetic_interpretation".into();
    let mut checks = full();
    for n in 0..3 {
        checks["assessments"][n]["atom_ids"] = json!([4]);
    }
    let mut b = Scripted::new(vec![draft(), checks]);
    let r = generate(&mut b, i).await.unwrap();
    assert!(r.accepted);
    assert!(
        r.line_sources
            .iter()
            .all(|s| s.atom_ids == vec!["derived:clause"])
    );
}
#[tokio::test]
async fn basis_only_numbers_are_printed_but_never_accepted_as_eligible_evidence() {
    let mut i = input();
    i.source_atoms[0].basis_atom_ids = vec!["basis:outside".into()];
    let mut bad = full();
    bad["assessments"][0]["atom_ids"] = json!([4]);
    let mut b = Scripted::new(vec![draft(), bad.clone(), bad]);
    let r = generate(&mut b, i).await.unwrap();
    assert_eq!(r.failure_reason.as_deref(), Some("grounding_unavailable"));
    assert_eq!(
        b.requests[1].details["grounding_atom_numbers"]["basis:outside"],
        4
    );
}
#[tokio::test]
async fn duplicate_candidates_skip_grounding_and_cannot_move_between_lines() {
    let mut b = Scripted::new(vec![
        draft(),
        negative(&[1]),
        json!({"lines":[{"line_index":1,"text":"クロキツルギノ"}]}),
        json!({"lines":[{"line_index":1,"text":LINES[0]}]}),
        json!({"lines":[{"line_index":1,"text":"くろいつるぎの"}]}),
        json!({"verdicts":{"1":"pass"},"assessments":[{"line_index":1,"atom_ids":[1]}]}),
    ]);
    let r = generate(&mut b, input()).await.unwrap();
    assert!(r.accepted);
    assert_eq!(r.regeneration_rounds, 3);
    assert_eq!(
        b.requests
            .iter()
            .filter(|r| r.kind == "haiku_line_grounding")
            .count(),
        2
    );
    assert_eq!(
        b.requests[3].details["current_lines"][1]["failure_reasons"],
        json!(["meaning_not_retained", "duplicate_candidate"])
    );
    assert_eq!(
        b.requests[3].details["current_lines"][1]["assessment_comment"],
        "材料の意味と合っていない。"
    );
    assert_eq!(
        b.requests[4].details["current_lines"][1]["assessment_comment"],
        "材料の意味と合っていない。"
    );
    assert_eq!(b.requests[4].details["current_lines"][1]["text"], LINES[1]);
}
#[tokio::test]
async fn initial_duplicate_is_forced_without_rechecking_that_line() {
    let mut checks = report(&[0, 1]);
    checks["assessments"][1]["atom_ids"] = json!([2]);
    let mut b = Scripted::new(vec![
        json!({"lines":[LINES[0],LINES[1],"クサチノヒ"]}),
        checks,
        json!({"lines":[{"line_index":2,"text":"しろいくも"}]}),
        json!({"verdicts":{"2":"pass"},"assessments":[{"line_index":2,"atom_ids":[1]}]}),
    ]);
    let r = generate(&mut b, input()).await.unwrap();
    assert!(r.accepted);
    assert_eq!(
        b.requests[1].details["grounding_lines"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(
        b.requests[2].details["current_lines"][2]["failure_reasons"],
        json!(["duplicate_line"])
    );
}
#[tokio::test]
async fn regeneration_is_atomic_when_frozen_extra_missing_duplicate_or_malformed_row() {
    for rows in [
        json!([{ "line_index":1,"text":"くろいつるぎの"},{"line_index":0,"text":"つきのかげ"}]),
        json!([]),
        json!([{ "line_index":1,"text":"くろいつるぎの"},{"line_index":1,"text":"みどりのもりに"}]),
        json!([null]),
        json!([{ "line_index":true,"text":"くろいつるぎの"}]),
        json!([{ "line_index":1,"text":"かな\nかな"}]),
    ] {
        let mut b = Scripted::new(vec![
            draft(),
            negative(&[1]),
            json!({"lines":rows}),
            json!({"lines":[{"line_index":1,"text":"くろいつるぎの"}]}),
            json!({"verdicts":{"1":"pass"},"assessments":[{"line_index":1,"atom_ids":[1]}]}),
        ]);
        let r = generate(&mut b, input()).await.unwrap();
        assert!(r.accepted);
        assert_eq!(r.regeneration_rounds, 2);
        assert_eq!(
            b.requests[3].details["current_lines"][1]["failure_reasons"],
            json!(["meaning_not_retained", "missing_regenerated_line"])
        );
        assert_eq!(b.requests[3].details["current_lines"][1]["text"], LINES[1]);
        assert_eq!(
            b.requests
                .iter()
                .filter(|r| r.kind == "haiku_line_grounding")
                .count(),
            2
        );
    }
}
#[tokio::test]
async fn normalization_and_grounded_correction_precede_checks_and_record_the_actual_line() {
    let mut b = Scripted::new(vec![
        json!({"lines":["草地の日",LINES[1],LINES[2]]}),
        full(),
    ]);
    b.preset(TransformMode::Normalize, "草地の日", LINES[0], LINES[0]);
    b.preset(TransformMode::Correct, LINES[2], "かげさむし", "かげさむし");
    let r = generate(&mut b, input()).await.unwrap();
    assert!(r.accepted);
    assert_eq!(
        b.requests[1].details["grounding_lines"][0]["text"],
        LINES[0]
    );
    assert_eq!(r.line_sources[2].text, "かげさむし");
    let c = b
        .transforms
        .iter()
        .find(|r| r.mode == TransformMode::Correct && r.line_index == 2)
        .unwrap();
    assert_eq!(c.atom_ids, vec![atoms()[2].atom_id.clone()]);
    assert_eq!(c.source_atoms.len(), 3);
}
#[tokio::test]
async fn nonretained_meaning_never_invokes_catalog_correction_and_success_reasons_are_dropped() {
    let mut bad = full();
    bad["verdicts"]["1"] = json!("japanese_fail");
    bad["failure_reasons"] = json!({"0":"合格理由","1":"まだ日本語が不自然"});
    let mut b = Scripted::new(vec![draft(), bad, json!({"lines":[]})]);
    let mut i = input();
    i.max_regeneration_rounds = 1;
    generate(&mut b, i).await.unwrap();
    assert_eq!(
        b.requests[2].details["current_lines"][1]["assessment_comment"],
        "まだ日本語が不自然"
    );
    assert!(
        b.requests[2].details["current_lines"][0]
            .get("assessment_comment")
            .is_none()
    );
    let mut b = Scripted::new(vec![draft(), negative(&[0, 1, 2])]);
    let mut i = input();
    i.max_regeneration_rounds = 0;
    generate(&mut b, i).await.unwrap();
    assert!(
        b.transforms
            .iter()
            .all(|r| r.mode == TransformMode::Normalize)
    );
}
#[tokio::test]
async fn same_natural_candidate_after_correction_is_also_already_seen() {
    let mut b = Scripted::new(vec![
        draft(),
        negative(&[1]),
        json!({"lines":[{"line_index":1,"text":"かげさむし"}]}),
        json!({"lines":[]}),
    ]);
    b.preset(TransformMode::Correct, LINES[2], "かげさむし", "かげさむし");
    let mut i = input();
    i.max_regeneration_rounds = 2;
    let r = generate(&mut b, i).await.unwrap();
    assert!(!r.accepted);
    assert_eq!(
        b.requests[3].details["current_lines"][1]["failure_reasons"],
        json!(["meaning_not_retained", "duplicate_candidate"])
    );
    assert_eq!(
        b.requests
            .iter()
            .filter(|r| r.kind == "haiku_line_grounding")
            .count(),
        1
    );
}
#[tokio::test]
async fn errors_fail_closed_and_do_not_become_accepted_fallbacks() {
    let mut b = Scripted::new(vec![json!({"__test_error":true})]);
    assert_eq!(
        generate(&mut b, input())
            .await
            .unwrap()
            .failure_reason
            .as_deref(),
        Some("invalid_draft")
    );
    let mut b = Scripted::new(vec![draft(), report(&[0, 2]), json!({"__test_error":true})]);
    assert_eq!(
        generate(&mut b, input())
            .await
            .unwrap()
            .failure_reason
            .as_deref(),
        Some("grounding_unavailable")
    );
    let mut b = Scripted::new(vec![draft()]);
    b.transform_error = true;
    assert!(generate(&mut b, input()).await.is_err());
    for invalid in [
        json!(null),
        json!({"lines":[LINES[0],LINES[1]]}),
        json!({"lines":[LINES[0],LINES[1],12]}),
        json!({"lines":LINES,"__dogido_status":"disabled"}),
    ] {
        let mut b = Scripted::new(vec![invalid]);
        assert_eq!(
            generate(&mut b, input())
                .await
                .unwrap()
                .failure_reason
                .as_deref(),
            Some("invalid_draft")
        );
    }
}
#[tokio::test]
async fn limits_guard_disabled_strategy_and_claim_count_without_backend_calls() {
    for (kind, reason) in [
        ("disabled", "llm_unavailable"),
        ("strategy", "invalid_generation_strategy"),
        ("atoms", "insufficient_source_atoms"),
    ] {
        let mut i = input();
        match kind {
            "disabled" => i.llm_enabled = false,
            "strategy" => i.generation_strategy = "unexpected".into(),
            _ => i.source_atoms.truncate(2),
        };
        let mut b = Scripted::new(vec![]);
        assert_eq!(
            generate(&mut b, i).await.unwrap().failure_reason.as_deref(),
            Some(reason)
        );
        assert!(b.requests.is_empty());
    }
    for (rounds, expected) in [(-2, 0), (6, 6), (20, 8)] {
        let mut responses = vec![draft(), negative(&[0, 1, 2])];
        responses.extend((0..expected).map(|_| json!({"lines":[]})));
        let mut b = Scripted::new(responses);
        let mut i = input();
        i.max_regeneration_rounds = rounds;
        let r = generate(&mut b, i).await.unwrap();
        assert_eq!(r.failure_reason.as_deref(), Some("max_regeneration_rounds"));
        assert_eq!(r.regeneration_rounds, expected);
        assert_eq!(b.requests.len(), 2 + expected);
    }
}
#[tokio::test]
async fn one_line_reserving_multiple_claims_can_leave_insufficient_unused_atoms() {
    let mut check = negative(&[1]);
    check["assessments"][0]["atom_ids"] = json!([1, 2]);
    let mut b = Scripted::new(vec![draft(), check]);
    let r = generate(&mut b, input()).await.unwrap();
    assert_eq!(
        r.failure_reason.as_deref(),
        Some("insufficient_unused_atoms")
    );
    assert_eq!(r.regeneration_rounds, 0);
    assert_eq!(b.requests.len(), 2);
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
