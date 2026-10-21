//! 川柳の明示修正依頼から一回だけ候補を生成する。
//! 原文一致・対象外不変・出典IDの整合性を照合し、受け取った候補を既存の採用待ちへ渡す。
//! 意味や自然さを別LLMで採点しない。失敗時は元句を返し、採用・保存は呼出元が担当する。
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

/// 採用待ちの作成時と保存前に、現在の正本三行に対して出典ID・重複・形式を再照合する。
/// 出典IDの整合性を確認する処理であり、候補の意味や自然さを検証済みとは扱わない。
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
    }
    Ok(())
}

/// 対象と固定行の出典を先に確かめ、一案だけ生成し、コード検査の合否を返す。
/// 不成立の候補を追加のモデル評価・再生成へ回さない。
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
    let payload = request(
        backend,
        StructuredRequest {
            kind: "haiku_workshop_revision".into(),
            details,
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
    let mut source_by_line = input.line_sources.clone();
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
            let ids: Option<Vec<&str>> = row["atom_ids"]
                .as_array()
                .and_then(|v| v.iter().map(Value::as_str).collect());
            if let Some(ids) = ids.filter(|ids| !ids.is_empty()) {
                source_by_line.insert(i, ids.iter().map(|id| (*id).to_owned()).collect());
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
    if signatures.contains("") {
        failures.push("empty_line_signature".into());
    }
    let output: Vec<_> = (0..3)
        .map(|i| {
            if targets.contains(&i) {
                lines[i].text.clone()
            } else {
                input.lines[i].clone()
            }
        })
        .collect();
    let sources: Vec<Vec<String>> = (0..3)
        .map(|i| source_by_line.get(&i).cloned().unwrap_or_default())
        .collect();
    if failures.is_empty() {
        if let Err(error) = validate_sources(&input.basis, &output, &sources) {
            failures.push(error.to_string());
        }
    }
    if !failures.is_empty() {
        tracing::info!(event = "workshop_revision_rejected", ?failures);
        return Ok(rejected(
            &input,
            "invalid_revision",
            vec![json!({
                "attempt":1,"global_failure_reasons":failures,
                "rejected_replacements":targets.iter().map(|i|json!({"line_index":i,"replacement_text":lines[*i].text})).collect::<Vec<_>>()
            })],
        ));
    }
    let line_sources = (0..3)
        .map(|i| LineSource {
            line_index: i,
            text: output[i].clone(),
            sources: sources[i].iter().map(|id| atoms[id].clone()).collect(),
            atom_ids: sources[i].clone(),
        })
        .collect();
    Ok(Revision {
        accepted: true,
        lines: output,
        line_sources,
        failure_reason: None,
        feedback: vec![],
    })
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
            assert_eq!(r.mode, TransformMode::Normalize);
            let text = r.text;
            Ok(LineForm {
                text: text.clone(),
                signature: text.chars().filter(|c| !matches!(c, '/' | ' ')).collect(),
            })
        }
    }
    #[tokio::test]
    async fn one_candidate_preserves_frozen_lines_and_generator_source_ids() {
        let mut mock = Mock::new(vec![edit()]);
        let out = generate(&mut mock, input()).await.unwrap();
        assert!(out.accepted);
        assert_eq!(out.lines, ["さくらいろ", "くろいおのへと", "あさのいろ"]);
        assert_eq!(out.line_sources[1].atom_ids, ["s1"]);
        assert_eq!(mock.requests.len(), 1);
        assert_eq!(mock.requests[0].route, "haiku");
        assert_eq!(out.line_sources[0].atom_ids, ["s0"]);
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
    async fn invalid_cas_indices_and_sources_keep_original_without_retry() {
        for case in 0..9 {
            let mut bad = edit();
            match case {
                0 => bad["lines"][0]["expected_text"] = json!("別の句"),
                1 => bad["lines"][0]["line_index"] = json!(1),
                2 => bad["lines"][0]["atom_ids"] = json!(["s1"]),
                3 => bad["lines"][0]["atom_ids"] = json!(["s0", "s0"]),
                4 => bad["lines"][0]["replacement_text"] = json!("は"),
                5 => bad["lines"][0]["replacement_text"] = json!("あさのいろ"),
                6 => bad["lines"][0]["replacement_text"] = json!("さくらのは"),
                7 => bad["lines"][0]["replacement_text"] = json!("/////"),
                _ => bad["lines"]
                    .as_array_mut()
                    .unwrap()
                    .push(edit()["lines"][0].clone()),
            }
            let mut mock = Mock::new(vec![bad]);
            let out = generate(&mut mock, input()).await.unwrap();
            assert_eq!(out.accepted, matches!(case, 4 | 5), "{case}");
            assert_eq!(mock.requests.len(), 1);
            if matches!(case, 4 | 5) {
                assert_eq!(out.lines[0], if case == 4 { "は" } else { "あさのいろ" });
                assert_eq!(&out.lines[1..], &input().lines[1..]);
                continue;
            }
            assert_eq!(out.lines, input().lines);
            assert!(
                mock.requests
                    .iter()
                    .all(|r| r.kind == "haiku_workshop_revision")
            );
            assert_eq!(out.feedback.len(), 1);
        }
    }
    #[tokio::test]
    async fn hard_terms_do_not_veto_model_revision() {
        let mut i = input();
        i.basis.details.insert(
            "haiku_constraints".into(),
            json!({"forbidden_terms":["さくら"]}),
        );
        let original = i.lines.clone();
        let mut mock = Mock::new(vec![edit()]);
        let out = generate(&mut mock, i).await.unwrap();
        assert!(out.accepted);
        assert_eq!(out.lines[0], "さくらいろ");
        assert_eq!(&out.lines[1..], &original[1..]);
        assert_eq!(mock.requests.len(), 1);
    }

    #[tokio::test]
    async fn generator_cannot_borrow_frozen_claim_via_basis_id() {
        let mut i = input();
        let mut derived = i.basis.source_atoms[0].clone();
        derived.atom_id = "derived".into();
        derived.basis_atom_ids = vec!["s1".into()];
        i.basis.source_atoms.push(derived);
        let mut bad = edit();
        bad["lines"][0]["atom_ids"] = json!(["derived"]);
        let mut mock = Mock::new(vec![bad]);
        assert!(!generate(&mut mock, i).await.unwrap().accepted);
        assert_eq!(mock.requests.len(), 1);
    }
}
