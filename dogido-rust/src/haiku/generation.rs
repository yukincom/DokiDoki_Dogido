//! 前置きで選んだ元材料を三行共通で渡し、候補の形式・音数を確認する。
//! 行への展開と意味は生成モデルが担う。再生成でも同じ材料と調理方針を保つ。
//! 保存する行別材料はコードが渡した資料の記録であり、モデルが実際に使った根拠の認定ではない。
use super::{
    Backend, GroundedHaikuResult, Input, LineForm, LineSource, PROMPT_VARIANT, SourceAtom,
    StructuredRequest, TransformMode, TransformRequest, line_format, meter,
};
use anyhow::Result;
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
pub(super) type Indices = BTreeSet<usize>;
pub(super) type Failures = BTreeMap<usize, Vec<String>>;

/// 選択済みの一次材料を三行へ渡し、初案＋設定回数以内の再生成を行う。
/// 合格行を固定し、失敗した生成グループを同じ材料で作り直す。採否と保存は呼出元が所有する。
pub async fn generate<B: Backend>(backend: &mut B, input: Input) -> Result<GroundedHaikuResult> {
    let groups: &[&[usize]] = match input.generation_strategy.as_str() {
        "whole_poem" => &[&[0, 1, 2]],
        "three_slot" => &[&[0], &[1], &[2]],
        "one_plus_two" => &[&[0], &[1, 2]],
        "two_plus_one" => &[&[0, 1], &[2]],
        _ => return Ok(failed(&input, "invalid_generation_strategy", 0)),
    };
    let limit = input.max_regeneration_rounds.clamp(0, 8) as usize;
    if !input.llm_enabled {
        return Ok(failed(&input, "llm_unavailable", 0));
    }
    let mut lines = vec![
        LineForm {
            text: String::new(),
            signature: String::new()
        };
        3
    ];
    let atoms: Vec<_> = input
        .source_atoms
        .iter()
        .filter(|a| {
            a.basis_atom_ids.is_empty()
                && !matches!(a.kind.as_str(), "preface_clause" | "poetic_interpretation")
        })
        .cloned()
        .collect();
    if atoms.is_empty() {
        return Ok(failed(
            &input,
            if input.details.contains_key("material_selection_error") {
                "invalid_material_selection"
            } else {
                "insufficient_source_atoms"
            },
            0,
        ));
    }
    let materials = poem_materials(&atoms);
    let mut failed_indices: Indices = (0..3).collect();
    let mut failures = Failures::new();
    for round in 0..=limit {
        let mut details = input.details.clone();
        details.insert(
            "generation_strategy".into(),
            json!(input.generation_strategy),
        );
        details.insert("generation_slot_groups".into(), json!(groups));
        // 管理IDは保存用。モデルには選択済みの元材料の本文だけを三行共通で提示する。
        details.insert("available_poem_materials".into(), materials.clone());
        details.remove("source_atoms");
        let raw = if round == 0 {
            let payload = request(
                backend,
                StructuredRequest {
                    kind: "haiku_draft".into(),
                    fallback_value: json!({"lines":[]}),
                    details,
                    temperature: 0.60,
                    route: "haiku".into(),
                    max_tokens: input.max_tokens,
                },
            )
            .await;
            if structured_accepted(&payload) {
                line_format::three(&payload)
                    .unwrap_or_default()
                    .into_iter()
                    .enumerate()
                    .collect()
            } else {
                BTreeMap::new()
            }
        } else {
            regenerate(
                backend,
                &details,
                &lines,
                &failed_indices,
                &failures,
                input.max_tokens,
            )
            .await
        };
        let mut current_failures = Failures::new();
        for i in &failed_indices {
            let Some(text) = raw.get(i) else {
                current_failures.insert(*i, vec!["missing_regenerated_line".into()]);
                continue;
            };
            // 辞書による読みの整形だけを行う。意味評価を前提とした語句の自動置換は行わない。
            lines[*i] = transform(
                backend,
                text.clone(),
                *i,
                TransformMode::Normalize,
                vec![],
                vec![],
            )
            .await?;
            let reasons = meter::line_failure_reasons(&lines[*i].text, *i, &input.details);
            if !reasons.is_empty() {
                current_failures.insert(*i, reasons);
            }
        }
        failures = expand_failures(&current_failures, groups);
        failed_indices = failures.keys().copied().collect();
        tracing::info!(event="haiku_generation_round", round,
            candidate=%json!(lines.iter().map(|l| &l.text).collect::<Vec<_>>()), failures=%json!(failures),
            "川柳: 形式と音数の確認結果");
        if failed_indices.is_empty() {
            return Ok(GroundedHaikuResult {
                text: lines
                    .iter()
                    .map(|l| l.text.as_str())
                    .collect::<Vec<_>>()
                    .join("\n"),
                accepted: true,
                line_sources: (0..3)
                    .map(|i| LineSource {
                        line_index: i,
                        text: lines[i].text.clone(),
                        atom_ids: atoms.iter().map(|a| a.atom_id.clone()).collect(),
                        sources: atoms.clone(),
                    })
                    .collect(),
                failure_reason: None,
                generation_strategy: input.generation_strategy.clone(),
                regeneration_rounds: round,
                prompt_variant: PROMPT_VARIANT.into(),
            });
        }
    }
    Ok(failed(&input, "max_regeneration_rounds", limit))
}

/// 名称と説明を一つの材料として渡す。1〜2材料でも三つへ分割・水増ししない。
fn poem_materials(atoms: &[SourceAtom]) -> Value {
    let mut groups: Vec<Vec<&SourceAtom>> = vec![];
    let mut by_source = BTreeMap::new();
    for atom in atoms {
        let key = if atom.source_ref.is_empty() {
            &atom.atom_id
        } else {
            &atom.source_ref
        };
        let group = *by_source.entry(key.clone()).or_insert_with(|| {
            groups.push(vec![]);
            groups.len() - 1
        });
        groups[group].push(atom);
    }
    json!(
        groups
            .iter()
            .map(|g| json!({"facts":g.iter().map(|a| json!({
        "text":a.text,"claim_class":a.claim_class,"claim_scopes":a.claim_scopes
    })).collect::<Vec<_>>()}))
            .collect::<Vec<_>>()
    )
}

fn expand_failures(failures: &Failures, groups: &[&[usize]]) -> Failures {
    let mut expanded = Failures::new();
    for group in groups {
        if group.iter().any(|i| failures.contains_key(i)) {
            for i in *group {
                expanded.insert(
                    *i,
                    failures
                        .get(i)
                        .cloned()
                        .unwrap_or_else(|| vec!["slot_dependency".into()]),
                );
            }
        }
    }
    expanded
}

async fn regenerate<B: Backend>(
    backend: &mut B,
    details: &Map<String, Value>,
    lines: &[LineForm],
    failed: &Indices,
    failures: &Failures,
    max_tokens: Option<u64>,
) -> BTreeMap<usize, String> {
    let mut details = details.clone();
    let rows:Vec<_> = lines.iter().enumerate().map(|(i,l)| {
        let count = meter::count_japanese_sounds(&l.text); let target = meter::TARGETS[i];
        json!({"line_index":i,"text":l.text,"frozen":!failed.contains(&i),
            "sound_count":count,"target_sound_count":target,"allowed_sound_min":target-1,
            "allowed_sound_max":target+1,"meter_status":if count<target-1 {"too_short"} else if count>target+1 {"too_long"} else {"within_range"},
            "failure_reasons":failures.get(&i).cloned().unwrap_or_default()})
    }).collect();
    details.insert("current_lines".into(), json!(rows));
    details.insert("failed_line_indices".into(), json!(failed));
    let payload = request(
        backend,
        StructuredRequest {
            kind: "haiku_line_regeneration".into(),
            fallback_value: json!({"lines":[]}),
            details,
            temperature: 0.30,
            route: "haiku".into(),
            max_tokens,
        },
    )
    .await;
    parse_regeneration(&payload, failed)
}
fn parse_regeneration(payload: &Value, failed: &Indices) -> BTreeMap<usize, String> {
    let parse = || -> Option<BTreeMap<usize, String>> {
        if !structured_accepted(payload) {
            return None;
        }
        let rows = payload.get("lines")?.as_array()?;
        let mut out = BTreeMap::new();
        for row in rows {
            let i: usize = row.get("line_index")?.as_u64()?.try_into().ok()?;
            let text = line_format::one(row.get("text")?.as_str()?)?;
            if !failed.contains(&i) || out.insert(i, text).is_some() {
                return None;
            }
        }
        (out.keys().copied().collect::<Indices>() == *failed).then_some(out)
    };
    parse().unwrap_or_default()
}
/// 生成失敗時は要求のfallbackと失敗状態を返す。consumerは句案・検査結果として採用しない。
pub(super) async fn request<B: Backend>(backend: &mut B, request: StructuredRequest) -> Value {
    let fallback = request.fallback_value.clone();
    match backend.generate(request).await {
        Ok(value) => value,
        Err(error) => {
            tracing::warn!(event="haiku_generation_error",error=%error);
            let mut value = fallback;
            if let Some(map) = value.as_object_mut() {
                map.insert("__dogido_status".into(), json!("generation_error"));
            }
            value
        }
    }
}
pub(super) async fn transform<B: Backend>(
    backend: &mut B,
    text: String,
    line_index: usize,
    mode: TransformMode,
    atom_ids: Vec<String>,
    source_atoms: Vec<SourceAtom>,
) -> Result<LineForm> {
    backend
        .transform(TransformRequest {
            text,
            mode,
            line_index,
            atom_ids,
            source_atoms,
        })
        .await
}
pub(super) use crate::compat::json_truthy as truthy;
pub(super) fn structured_accepted(payload: &Value) -> bool {
    payload.is_object()
        && payload
            .get("__dogido_status")
            .filter(|v| truthy(v))
            .is_none_or(|v| v.as_str() == Some("accepted"))
}
fn failed(input: &Input, reason: &str, round: usize) -> GroundedHaikuResult {
    tracing::warn!(
        event = "haiku_generation_failed",
        result = "fallback",
        reason,
        round
    );
    GroundedHaikuResult {
        text: input.fallback_text.clone(),
        accepted: false,
        line_sources: vec![],
        failure_reason: Some(reason.into()),
        generation_strategy: input.generation_strategy.clone(),
        regeneration_rounds: round,
        prompt_variant: PROMPT_VARIANT.into(),
    }
}
