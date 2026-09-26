//! Bounded workshop editor. Candidate CAS, frozen lines, source reservations and
//! grounding are code-owned; a rejected/unfinished check never changes the poem.
use super::{generation::*, *};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

pub const CONTRACT: &str = "line_compare_and_swap_v1";
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Basis {
    pub target_indices: BTreeSet<usize>,
    pub source_atoms: Vec<SourceAtom>,
    pub details: Map<String, Value>,
}
#[derive(Debug, Deserialize)]
pub struct Input {
    pub lines: Vec<String>,
    pub line_sources: BTreeMap<usize, Vec<String>>,
    pub findings: Vec<Value>,
    pub basis: Basis,
    pub max_tokens: Option<u64>,
    pub grounding_max_tokens: u64,
}
#[derive(Debug, Serialize)]
pub struct Revision {
    pub accepted: bool,
    pub lines: Vec<String>,
    pub line_sources: Vec<LineSource>,
    pub failure_reason: Option<String>,
    pub feedback: Vec<Value>,
}
fn rejected(input: &Input, reason: &str, feedback: Vec<Value>) -> Revision {
    Revision {
        accepted: false,
        lines: input.lines.clone(),
        line_sources: vec![],
        failure_reason: Some(reason.into()),
        feedback,
    }
}

/// Also used at staging/adoption, against the complete current canonical records.
/// Model semantic verdicts are checked during generation; never invented here.
pub fn validate_sources(basis: &Basis, lines: &[String], sources: &[Vec<String>]) -> Result<()> {
    ensure!(
        lines.len() == 3
            && sources.len() == 3
            && !basis.target_indices.is_empty()
            && basis.target_indices.iter().all(|i| *i < 3),
        "invalid_targets"
    );
    let atoms: BTreeMap<_, _> = basis
        .source_atoms
        .iter()
        .map(|a| (a.atom_id.clone(), a.clone()))
        .collect();
    let mut used = BTreeSet::new();
    for i in 0..3 {
        let ids = &sources[i];
        ensure!(
            !ids.is_empty()
                && ids.iter().all(|id| atoms.contains_key(id))
                && ids.iter().collect::<BTreeSet<_>>().len() == ids.len(),
            "invalid_line_sources"
        );
        let claims = reservations(ids, &atoms);
        ensure!(used.is_disjoint(&claims), "source_reused");
        used.extend(claims);
        if basis.target_indices.contains(&i) {
            ensure!(
                meter::line_failure_reasons(&lines[i], i, &basis.details).is_empty(),
                "invalid_line_form"
            );
            ensure!(
                !lines
                    .iter()
                    .enumerate()
                    .any(|(j, t)| i != j && t == &lines[i]),
                "duplicate_line"
            );
        }
    }
    Ok(())
}

pub async fn generate<B: Backend>(backend: &mut B, input: Input) -> Result<Revision> {
    let targets = &input.basis.target_indices;
    if input.lines.len() != 3 || targets.is_empty() || targets.iter().any(|i| *i >= 3) {
        return Ok(rejected(&input, "invalid_targets", vec![]));
    }
    let atoms: BTreeMap<_, _> = input
        .basis
        .source_atoms
        .iter()
        .map(|a| (a.atom_id.clone(), a.clone()))
        .collect();
    if atoms.is_empty() {
        return Ok(rejected(&input, "no_source_atoms", vec![]));
    }
    let frozen: Indices = (0..3).filter(|i| !targets.contains(i)).collect();
    let mut used = BTreeSet::new();
    for i in &frozen {
        let Some(ids) = input.line_sources.get(i).filter(|ids| !ids.is_empty()) else {
            return Ok(rejected(&input, "missing_frozen_line_sources", vec![]));
        };
        if ids.iter().any(|id| !atoms.contains_key(id)) {
            return Ok(rejected(&input, "invalid_frozen_line_sources", vec![]));
        }
        let claims = reservations(ids, &atoms);
        if !used.is_disjoint(&claims) {
            return Ok(rejected(&input, "source_reused", vec![]));
        }
        used.extend(claims);
    }
    let eligible = eligible_atoms(&input.basis.source_atoms, &used);
    if eligible.len() < targets.len() {
        return Ok(rejected(&input, "insufficient_source_atoms", vec![]));
    }
    let eligible_ids: BTreeSet<_> = eligible.iter().map(|a| a.atom_id.clone()).collect();
    let mut original = Vec::new();
    for (i, t) in input.lines.iter().enumerate() {
        original.push(
            transform(
                backend,
                t.clone(),
                i,
                TransformMode::Normalize,
                vec![],
                vec![],
            )
            .await?,
        );
    }
    let mut details = input.basis.details.clone();
    details.insert(
        "current_lines".into(),
        json!(
            input
                .lines
                .iter()
                .enumerate()
                .map(|(i, t)| json!({"line_index":i,"text":t,"frozen":!targets.contains(&i)}))
                .collect::<Vec<_>>()
        ),
    );
    details.insert("target_line_indices".into(), json!(targets));
    details.insert("workshop_findings".into(), json!(input.findings));
    details.insert("source_atoms".into(), json!(eligible));
    details.insert("edit_contract".into(), json!(CONTRACT));
    let mut seen = BTreeSet::new();
    let mut feedback = Vec::new();
    // Exactly two editor attempts; assessment transport recovery is independent.
    for attempt in 1..=2 {
        let payload = request(
            backend,
            StructuredRequest {
                kind: "haiku_workshop_revision".into(),
                details: details.clone(),
                route: "haiku".into(),
                temperature: 0.30,
                max_tokens: input.max_tokens,
                fallback_value: json!({"lines":[]}),
            },
        )
        .await;
        let mut lines = original.clone();
        let mut failures = Vec::<String>::new();
        let mut indices = BTreeSet::new();
        let mut selected_ids = BTreeSet::new();
        if let Some(rows) = payload
            .get("lines")
            .and_then(Value::as_array)
            .filter(|_| structured_accepted(&payload))
        {
            for row in rows {
                if !row.as_object().is_some_and(|r| {
                    r.len() == 4
                        && [
                            "line_index",
                            "expected_text",
                            "replacement_text",
                            "atom_ids",
                        ]
                        .iter()
                        .all(|k| r.contains_key(*k))
                }) {
                    failures.push("invalid_edit_row".into());
                    continue;
                }
                let Some(i) = row["line_index"]
                    .as_u64()
                    .and_then(|i| usize::try_from(i).ok())
                    .filter(|i| targets.contains(i))
                else {
                    failures.push("unexpected_line_index".into());
                    continue;
                };
                if !indices.insert(i) {
                    failures.push("duplicate_target_edit".into());
                    continue;
                }
                if row["expected_text"]
                    .as_str()
                    .and_then(clean_line)
                    .as_deref()
                    != Some(input.lines[i].as_str())
                {
                    failures.push("expected_text_mismatch".into());
                }
                let Some(text) = row["replacement_text"].as_str().and_then(clean_line) else {
                    failures.push("empty_replacement".into());
                    continue;
                };
                lines[i] =
                    transform(backend, text, i, TransformMode::Normalize, vec![], vec![]).await?;
                if lines[i].signature == original[i].signature {
                    failures.push("unchanged_replacement".into());
                }
                failures.extend(meter::line_failure_reasons(
                    &lines[i].text,
                    i,
                    &input.basis.details,
                ));
                let ids: Option<Vec<&str>> = row["atom_ids"]
                    .as_array()
                    .and_then(|v| v.iter().map(Value::as_str).collect());
                if let Some(ids) = ids.filter(|ids| !ids.is_empty()) {
                    for id in ids {
                        if !eligible_ids.contains(id) {
                            failures.push("unknown_atom_id".into());
                        }
                        if !selected_ids.insert(id.to_owned()) {
                            failures.push("source_reused".into());
                        }
                    }
                } else {
                    failures.push("invalid_atom_ids".into());
                }
            }
        } else {
            failures.push("structured_rejected".into());
        }
        if &indices != targets {
            failures.push("missing_target_edit".into());
        }
        let signatures: BTreeSet<_> = lines.iter().map(|l| l.signature.as_str()).collect();
        if signatures.len() != 3 {
            failures.push("duplicate_line".into());
        }
        let signature: Vec<_> = targets
            .iter()
            .map(|i| (*i, lines[*i].signature.clone()))
            .collect();
        let mut line_failures = Failures::new();
        let mut comments = BTreeMap::new();
        if failures.is_empty() {
            if !seen.insert(signature) {
                failures.push("duplicate_candidate".into());
            } else {
                let mut check_details = input.basis.details.clone();
                check_details.insert("revision_edits".into(),json!(targets.iter().map(|i|json!({"line_index":i,"expected_text":input.lines[*i],"replacement_text":lines[*i].text})).collect::<Vec<_>>()));
                let assessments = assess_lines(
                    backend,
                    &check_details,
                    &lines,
                    targets,
                    &eligible,
                    input.grounding_max_tokens,
                )
                .await;
                if targets.iter().any(|i| !assessments.contains_key(i)) {
                    return Ok(rejected(&input, "grounding_unavailable", feedback));
                }
                comments = assessments
                    .iter()
                    .map(|(i, a)| (*i, a.reason.clone()))
                    .collect();
                let frozen_signatures = frozen
                    .iter()
                    .map(|i| original[*i].signature.clone())
                    .collect();
                let (accepted, failed) = accept_lines(
                    backend,
                    &mut lines,
                    targets,
                    &assessments,
                    &input.basis.details,
                    &atoms,
                    &used,
                    frozen_signatures,
                )
                .await?;
                line_failures = failed;
                // Lexical correction must not accidentally turn the edit into a no-op.
                for i in targets {
                    if lines[*i].signature == original[*i].signature {
                        line_failures
                            .entry(*i)
                            .or_default()
                            .push("unchanged_replacement".into());
                    }
                }
                if line_failures.is_empty() {
                    let line_sources: Vec<_> = (0..3)
                        .map(|i| {
                            let ids = accepted.get(&i).map_or_else(
                                || input.line_sources[&i].clone(),
                                |a| a.atom_ids.clone(),
                            );
                            LineSource {
                                line_index: i,
                                text: lines[i].text.clone(),
                                sources: ids.iter().map(|id| atoms[id].clone()).collect(),
                                atom_ids: ids,
                            }
                        })
                        .collect();
                    let output: Vec<_> = lines
                        .iter()
                        .enumerate()
                        .map(|(i, l)| {
                            if targets.contains(&i) {
                                l.text.clone()
                            } else {
                                input.lines[i].clone()
                            }
                        })
                        .collect();
                    validate_sources(
                        &input.basis,
                        &output,
                        &line_sources
                            .iter()
                            .map(|s| s.atom_ids.clone())
                            .collect::<Vec<_>>(),
                    )?;
                    return Ok(Revision {
                        accepted: true,
                        lines: output,
                        line_sources,
                        failure_reason: None,
                        feedback,
                    });
                }
                for (i, reasons) in &line_failures {
                    tracing::info!(
                        event = "workshop_revision_rejected",
                        attempt,
                        line_index = i,
                        ?reasons
                    );
                }
            }
        }
        let rejected_replacements: Vec<_> = targets
            .iter()
            .map(|i| json!({"line_index":i,"replacement_text":lines[*i].text}))
            .collect();
        let f = json!({"attempt":attempt,"global_failure_reasons":failures,"line_failures":line_failures.iter().map(|(i,r)|json!({"line_index":i,"failure_reasons":r,"assessment_comment":comments.get(i).map(String::as_str).unwrap_or("")})).collect::<Vec<_>>(),"rejected_replacements":rejected_replacements});
        details.insert("edit_retry_feedback".into(), f.clone());
        details.insert("rejected_replacements".into(), json!(rejected_replacements));
        feedback.push(f);
    }
    Ok(rejected(&input, "invalid_revision", feedback))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    fn input() -> Input {
        Input {
            lines: ["さくらのは", "くろいおのへと", "あさのいろ"]
                .map(str::to_owned)
                .to_vec(),
            line_sources: BTreeMap::from([
                (0, vec!["s0".into()]),
                (1, vec!["s1".into()]),
                (2, vec!["s2".into()]),
            ]),
            findings: vec![],
            max_tokens: Some(192),
            grounding_max_tokens: 512,
            basis: Basis {
                target_indices: BTreeSet::from([0]),
                details: Map::new(),
                source_atoms: (0..3)
                    .map(|i| SourceAtom {
                        atom_id: format!("s{i}"),
                        text: "材料".into(),
                        source_ref: "observation".into(),
                        field_path: "test".into(),
                        observation_role: "test".into(),
                        kind: "observation".into(),
                        claim_class: "factual".into(),
                        claim_scopes: vec!["observed_state".into()],
                        basis_atom_ids: vec![],
                    })
                    .collect(),
            },
        }
    }
    fn edit() -> Value {
        json!({"lines":[{"line_index":0,"expected_text":"さくらのは","replacement_text":"さくらいろ","atom_ids":["s0"]}]})
    }
    fn verdict(pass: bool) -> Value {
        json!({"verdicts":{"0":if pass {"pass"}else{"japanese_fail"}},"assessments":[{"line_index":0,"atom_ids":[1]}],"failure_reasons":{"0":"日本語が不自然"}})
    }
    struct Mock {
        responses: VecDeque<Value>,
        requests: Vec<StructuredRequest>,
    }
    impl Mock {
        fn new(rows: Vec<Value>) -> Self {
            Self {
                responses: rows.into(),
                requests: vec![],
            }
        }
    }
    impl Backend for Mock {
        async fn generate(&mut self, r: StructuredRequest) -> Result<Value> {
            self.requests.push(r);
            Ok(self.responses.pop_front().expect("unexpected model call"))
        }
        async fn transform(&mut self, r: TransformRequest) -> Result<LineForm> {
            Ok(LineForm {
                text: r.text.clone(),
                signature: r.text,
            })
        }
    }
    #[tokio::test]
    async fn frozen_sources_cas_and_checker_evidence_are_retained() {
        let mut mock = Mock::new(vec![edit(), verdict(true)]);
        let out = generate(&mut mock, input()).await.unwrap();
        assert!(out.accepted);
        assert_eq!(out.lines, ["さくらいろ", "くろいおのへと", "あさのいろ"]);
        assert_eq!(out.line_sources[1].atom_ids, ["s1"]);
        assert_eq!(mock.requests.len(), 2);
        assert_eq!(mock.requests[0].route, "haiku");
        assert_eq!(mock.requests[1].route, "chat");
        assert_eq!(mock.requests[1].max_tokens, Some(512));
        assert_eq!(
            mock.requests[0].details["source_atoms"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }
    #[tokio::test]
    async fn invalid_or_missing_frozen_evidence_never_calls_model() {
        for unknown in [false, true] {
            let mut i = input();
            i.line_sources.insert(
                1,
                if unknown {
                    vec!["unknown".into()]
                } else {
                    vec![]
                },
            );
            let mut mock = Mock::new(vec![]);
            let out = generate(&mut mock, i).await.unwrap();
            assert!(!out.accepted);
            assert!(mock.requests.is_empty());
        }
    }
    #[tokio::test]
    async fn malformed_cas_indices_sources_and_meter_are_rejected_before_checker() {
        for case in 0..8 {
            let mut bad = edit();
            match case {
                0 => bad["lines"][0]["expected_text"] = json!("別の句"),
                1 => bad["lines"][0]["line_index"] = json!(1),
                2 => bad["lines"][0]["atom_ids"] = json!(["s1"]),
                3 => bad["lines"][0]["atom_ids"] = json!(["s0", "s0"]),
                4 => bad["lines"][0]["replacement_text"] = json!("は"),
                5 => bad["lines"][0]["replacement_text"] = json!("あさのいろ"),
                6 => bad["lines"][0]["replacement_text"] = json!("さくらのは"),
                _ => bad["lines"]
                    .as_array_mut()
                    .unwrap()
                    .push(edit()["lines"][0].clone()),
            }
            let mut mock = Mock::new(vec![bad.clone(), bad]);
            let out = generate(&mut mock, input()).await.unwrap();
            assert!(!out.accepted, "{case}");
            assert_eq!(mock.requests.len(), 2);
            assert!(
                mock.requests
                    .iter()
                    .all(|r| r.kind == "haiku_workshop_revision")
            );
            assert!(mock.requests[1].details["edit_retry_feedback"].is_object());
        }
    }
    #[tokio::test]
    async fn same_failed_candidate_is_not_checked_twice() {
        let mut mock = Mock::new(vec![edit(), verdict(false), edit()]);
        let out = generate(&mut mock, input()).await.unwrap();
        assert!(!out.accepted);
        assert_eq!(mock.requests.len(), 3);
        assert!(out.feedback[1].to_string().contains("duplicate_candidate"));
    }
    #[tokio::test]
    async fn incomplete_grounding_retries_only_checker_then_keeps_original() {
        let mut mock = Mock::new(vec![edit(), json!({}), json!({})]);
        let out = generate(&mut mock, input()).await.unwrap();
        assert_eq!(out.failure_reason.as_deref(), Some("grounding_unavailable"));
        assert_eq!(out.lines, input().lines);
        assert_eq!(
            mock.requests
                .iter()
                .filter(|r| r.kind == "haiku_workshop_revision")
                .count(),
            1
        );
    }
    #[tokio::test]
    async fn second_new_candidate_can_pass_but_hard_terms_remain_hard() {
        let mut second = edit();
        second["lines"][0]["replacement_text"] = json!("はるのいろ");
        let mut mock = Mock::new(vec![edit(), verdict(false), second, verdict(true)]);
        let out = generate(&mut mock, input()).await.unwrap();
        assert!(out.accepted);
        assert_eq!(out.lines[0], "はるのいろ");
        let mut i = input();
        i.basis.details.insert(
            "haiku_constraints".into(),
            json!({"forbidden_terms":["さくら"]}),
        );
        let mut mock = Mock::new(vec![edit(), edit()]);
        assert!(!generate(&mut mock, i).await.unwrap().accepted);
    }
    #[tokio::test]
    async fn checker_cannot_borrow_frozen_claim_via_basis_id() {
        let mut i = input();
        let mut derived = i.basis.source_atoms[0].clone();
        derived.atom_id = "derived".into();
        derived.basis_atom_ids = vec!["s1".into()];
        i.basis.source_atoms.push(derived);
        let mut bad = edit();
        bad["lines"][0]["atom_ids"] = json!(["derived"]);
        let mut mock = Mock::new(vec![bad.clone(), bad]);
        assert!(!generate(&mut mock, i).await.unwrap().accepted);
    }
}
