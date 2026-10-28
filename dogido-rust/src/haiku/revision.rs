//! 川柳の明示修正依頼から一回だけ候補を生成する。
//! 元句・対象外不変を照合し、受け取った表現を既存の採用待ちへ渡す。材料の記録は任意。
//! 意味や自然さを別LLMで採点しない。失敗時は元句を返し、採用・保存は呼出元が担当する。
use super::{generation::*, *};
use anyhow::Result;
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

/// 指定行の一案を受け取る。材料は参考情報であり、未使用・重複・欠落で本文を拒否しない。
/// 読みへの変換で表記を上書きせず、元句と実行対象だけを照合する。
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
    details.insert("source_atoms".into(), json!(input.basis.source_atoms));
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
    let mut lines = input.lines.clone();
    let mut failures = Vec::<String>::new();
    let mut indices = BTreeSet::new();
    let mut source_by_line = input.line_sources.clone();
    if let Some(rows) = payload
        .get("lines")
        .and_then(Value::as_array)
        .filter(|_| structured_accepted(&payload))
    {
        for row in rows {
            let index = row["line_index"]
                .as_u64()
                .and_then(|i| usize::try_from(i).ok())
                .or_else(|| {
                    (row.get("line_index").is_none() && targets.len() == 1)
                        .then(|| *targets.first().unwrap())
                });
            let Some(i) = index.filter(|i| targets.contains(i)) else {
                failures.push("unexpected_line_index".into());
                continue;
            };
            if !indices.insert(i) {
                failures.push("duplicate_target_edit".into());
                continue;
            }
            // 既知の元句は省略時に補える。モデルが別の元句を返した場合は適用しない。
            if let Some(expected) = row.get("expected_text")
                && expected.as_str().map(str::trim) != Some(input.lines[i].trim())
            {
                failures.push("expected_text_mismatch".into());
            }
            let Some(text) = row["replacement_text"]
                .as_str()
                .map(str::trim)
                .filter(|s| !s.is_empty() && !s.contains(['\n', '\r']))
            else {
                failures.push("invalid_replacement_shape".into());
                continue;
            };
            if text == input.lines[i] {
                failures.push("unchanged_replacement".into());
            }
            lines[i] = text.into();
            // 実在する材料だけを任意の注記として保存する。未確認IDを出典に捏造しない。
            let ids: BTreeSet<String> = row["atom_ids"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .filter(|id| atoms.contains_key(*id))
                .map(str::to_owned)
                .collect();
            source_by_line.insert(i, ids.into_iter().collect());
        }
    } else {
        failures.push("structured_rejected".into());
    }
    if &indices != targets {
        failures.push("missing_target_edit".into());
    }
    if !failures.is_empty() {
        tracing::info!(event = "workshop_revision_not_applied", ?failures);
        return Ok(rejected(
            &input,
            "invalid_revision",
            vec![json!({
                "attempt":1,"global_failure_reasons":failures,
                "rejected_replacements":targets.iter().map(|i|json!({"line_index":i,"replacement_text":lines[*i]})).collect::<Vec<_>>()
            })],
        ));
    }
    let line_sources = (0..3)
        .map(|i| {
            let ids: Vec<_> = source_by_line
                .get(&i)
                .into_iter()
                .flatten()
                .filter(|id| atoms.contains_key(*id))
                .cloned()
                .collect();
            LineSource {
                line_index: i,
                text: lines[i].clone(),
                sources: ids.iter().map(|id| atoms[id].clone()).collect(),
                atom_ids: ids,
            }
        })
        .collect();
    Ok(Revision {
        accepted: true,
        lines,
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
            3
        );
    }
    #[tokio::test]
    async fn missing_or_reused_materials_never_block_revision_generation() {
        for mode in 0..5 {
            let mut i = input();
            let mut proposal = edit();
            match mode {
                0 => {
                    i.basis.source_atoms.clear();
                    i.line_sources.clear();
                }
                1 => {
                    i.line_sources.insert(1, vec![]);
                }
                2 => {
                    proposal["lines"][0]["atom_ids"] = json!(["s1", "s1", "unknown"]);
                }
                3 => {
                    proposal["lines"][0]
                        .as_object_mut()
                        .unwrap()
                        .remove("atom_ids");
                }
                _ => {
                    proposal["lines"][0]["atom_ids"] = json!({"invalid":"optional annotation"});
                }
            }
            let mut mock = Mock::new(vec![proposal]);
            let out = generate(&mut mock, i).await.unwrap();
            assert!(out.accepted, "{mode}: {out:?}");
            assert_eq!(out.lines[0], "さくらいろ");
            assert_eq!(mock.requests.len(), 1);
            assert!(
                out.line_sources[0]
                    .atom_ids
                    .iter()
                    .all(|id| id != "unknown")
            );
            if mode == 2 {
                assert_eq!(out.line_sources[0].atom_ids, vec!["s1"]);
            }
        }
    }
    #[tokio::test]
    async fn optional_cas_fields_are_completed_and_surface_is_preserved() {
        let mut proposal = edit();
        let row = proposal["lines"][0].as_object_mut().unwrap();
        row.remove("line_index");
        row.remove("expected_text");
        row.remove("atom_ids");
        row.insert("replacement_text".into(), json!("桜色、春の風"));
        row.insert("comment".into(), json!("任意の補助情報"));
        let mut mock = Mock::new(vec![proposal]);
        let out = generate(&mut mock, input()).await.unwrap();
        assert!(out.accepted);
        assert_eq!(out.lines[0], "桜色、春の風");
        assert!(out.line_sources[0].sources.is_empty());
        assert_eq!(&out.lines[1..], &input().lines[1..]);
        assert_eq!(mock.requests.len(), 1);
    }
    #[tokio::test]
    async fn invalid_cas_indices_and_empty_changes_keep_original_without_retry() {
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
            assert_eq!(out.accepted, matches!(case, 2 | 3 | 4 | 5 | 7), "{case}");
            assert_eq!(mock.requests.len(), 1);
            if matches!(case, 2 | 3 | 4 | 5 | 7) {
                assert_eq!(
                    out.lines[0],
                    match case {
                        4 => "は",
                        5 => "あさのいろ",
                        7 => "/////",
                        _ => "さくらいろ",
                    }
                );
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
    async fn generated_expression_can_reuse_materials_from_other_lines() {
        let mut i = input();
        let mut derived = i.basis.source_atoms[0].clone();
        derived.atom_id = "derived".into();
        derived.basis_atom_ids = vec!["s1".into()];
        i.basis.source_atoms.push(derived);
        let mut bad = edit();
        bad["lines"][0]["atom_ids"] = json!(["derived"]);
        let mut mock = Mock::new(vec![bad]);
        assert!(generate(&mut mock, i).await.unwrap().accepted);
        assert_eq!(mock.requests.len(), 1);
    }
}
