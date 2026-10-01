use super::{
    Backend, GroundedHaikuResult, Input, LineForm, LineSource, PROMPT_VARIANT, SourceAtom,
    StructuredRequest, TransformMode, TransformRequest, meter,
};
use anyhow::Result;
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
pub(super) type Indices = BTreeSet<usize>;
pub(super) type Failures = BTreeMap<usize, Vec<String>>;
pub(super) type Assessments = BTreeMap<usize, Assessment>;
#[derive(Clone, Debug)]
pub(super) struct Assessment {
    pub(super) atom_ids: Vec<String>,
    pub(super) meaning_retained: bool,
    pub(super) natural_japanese: bool,
    pub(super) assessed_text: String,
    pub(super) reason: String,
}

/// Pure bounded orchestration: no prompt/lexicon I/O, model calls or persistence here.
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
    let claims: BTreeSet<_> = input
        .source_atoms
        .iter()
        .flat_map(atom_reservations)
        .collect();
    if claims.len() < 3 {
        return Ok(failed(&input, "insufficient_source_atoms", 0));
    }
    let atom_by_id: BTreeMap<_, _> = input
        .source_atoms
        .iter()
        .map(|a| (a.atom_id.clone(), a.clone()))
        .collect();
    let mut details = input.details.clone();
    details.insert("source_atoms".into(), json!(input.source_atoms));
    details.insert(
        "generation_strategy".into(),
        json!(input.generation_strategy),
    );
    details.insert("generation_slot_groups".into(), json!(groups));
    let payload = request(
        backend,
        StructuredRequest {
            kind: "haiku_draft".into(),
            fallback_value: json!({"lines":[]}),
            details: details.clone(),
            temperature: 0.60,
            route: "haiku".into(),
            max_tokens: input.max_tokens,
        },
    )
    .await;
    let Some(raw_lines) = draft_lines(&payload) else {
        return Ok(failed(&input, "invalid_draft", 0));
    };
    let mut lines = Vec::new();
    for (index, text) in raw_lines.into_iter().enumerate() {
        lines.push(
            transform(
                backend,
                text,
                index,
                TransformMode::Normalize,
                vec![],
                vec![],
            )
            .await?,
        );
    }
    let mut accepted = Assessments::new();
    let mut failed_indices: Indices = (0..3).collect();
    let mut candidates: BTreeSet<_> = lines.iter().map(|l| l.signature.clone()).collect();
    let mut forced = duplicate_failures(&lines);
    // A repeated/missing rewrite leaves the displayed candidate unchanged. Keep
    // its diagnostic, never a different candidate's verdict or source evidence.
    let mut last_feedback: BTreeMap<usize, (LineForm, Vec<String>, String)> = BTreeMap::new();
    for round in 0..=limit {
        let used = used_claims(&accepted, &atom_by_id);
        let eligible = eligible_atoms(&input.source_atoms, &used);
        let indices: Indices = failed_indices
            .iter()
            .filter(|i| !forced.contains_key(i))
            .copied()
            .collect();
        let assessments = assess_lines(
            backend,
            &details,
            &lines,
            &indices,
            &eligible,
            input.grounding_max_tokens,
        )
        .await;
        if indices.iter().any(|i| !assessments.contains_key(i)) {
            return Ok(failed(&input, "grounding_unavailable", round));
        }
        let frozen: BTreeSet<_> = accepted
            .keys()
            .map(|i| lines[*i].signature.clone())
            .collect();
        let (tentative, mut failures) = accept_lines(
            backend,
            &mut lines,
            &indices,
            &assessments,
            &input.details,
            &atom_by_id,
            &used,
            frozen,
        )
        .await?;
        let mut comments: BTreeMap<_, _> = assessments
            .iter()
            .filter(|(i, a)| !a.reason.is_empty() && a.assessed_text == lines[**i].text)
            .map(|(i, a)| (*i, a.reason.clone()))
            .collect();
        for (index, codes) in forced {
            let mut reasons = Vec::new();
            if let Some((line, previous, comment)) = last_feedback.get(&index)
                && line == &lines[index]
            {
                reasons.extend(previous.iter().cloned());
                if !comment.is_empty() {
                    comments.insert(index, comment.clone());
                }
            }
            for code in codes {
                if !reasons.contains(&code) {
                    reasons.push(code);
                }
            }
            failures.insert(index, reasons);
        }
        let expanded = expand_failures(&failures, groups);
        failed_indices = expanded.keys().copied().collect();
        accepted.extend(
            tentative
                .into_iter()
                .filter(|(i, _)| !failed_indices.contains(i)),
        );
        tracing::info!(event="haiku_grounding_round",strategy=input.generation_strategy,round,accepted=?accepted.keys(),failed=?expanded);
        if failed_indices.is_empty() {
            return Ok(GroundedHaikuResult {
                text: lines
                    .iter()
                    .map(|l| l.text.as_str())
                    .collect::<Vec<_>>()
                    .join("\n"),
                accepted: true,
                line_sources: (0..3)
                    .map(|i| {
                        let a = &accepted[&i];
                        LineSource {
                            line_index: i,
                            text: lines[i].text.clone(),
                            atom_ids: a.atom_ids.clone(),
                            sources: a.atom_ids.iter().map(|id| atom_by_id[id].clone()).collect(),
                        }
                    })
                    .collect(),
                failure_reason: None,
                generation_strategy: input.generation_strategy.clone(),
                regeneration_rounds: round,
                prompt_variant: PROMPT_VARIANT.into(),
            });
        }
        if round >= limit {
            break;
        }
        let used = used_claims(&accepted, &atom_by_id);
        let remaining = eligible_atoms(&input.source_atoms, &used);
        let remaining_claims: BTreeSet<_> = remaining.iter().flat_map(atom_reservations).collect();
        if remaining_claims.len() < failed_indices.len() {
            return Ok(failed(&input, "insufficient_unused_atoms", round));
        }
        candidates.extend(lines.iter().map(|l| l.signature.clone()));
        last_feedback = expanded
            .iter()
            .map(|(i, reasons)| {
                (
                    *i,
                    (
                        lines[*i].clone(),
                        reasons.clone(),
                        comments.get(i).cloned().unwrap_or_default(),
                    ),
                )
            })
            .collect();
        let regenerated = regenerate(
            backend,
            &details,
            &lines,
            &failed_indices,
            &expanded,
            &remaining,
            input.max_tokens,
            &comments,
        )
        .await;
        forced = Failures::new();
        for index in &failed_indices {
            let Some(text) = regenerated.get(index) else {
                forced.insert(*index, vec!["missing_regenerated_line".into()]);
                continue;
            };
            let form = transform(
                backend,
                text.clone(),
                *index,
                TransformMode::Normalize,
                vec![],
                vec![],
            )
            .await?;
            if form.signature.is_empty() || candidates.contains(&form.signature) {
                forced.insert(*index, vec!["duplicate_candidate".into()]);
                continue;
            }
            candidates.insert(form.signature.clone());
            last_feedback.remove(index);
            lines[*index] = form;
        }
    }
    Ok(failed(&input, "max_regeneration_rounds", limit))
}

/// Match the Python frontend's error fallback; consumer never accepts fallback as a draft/evaluation.
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
fn draft_lines(payload: &Value) -> Option<Vec<String>> {
    if !structured_accepted(payload) {
        return None;
    }
    let rows = payload.get("lines")?.as_array()?;
    if rows.len() != 3 {
        return None;
    }
    rows.iter().map(|v| clean_line(v.as_str()?)).collect()
}
pub(super) async fn assess_lines<B: Backend>(
    backend: &mut B,
    details: &Map<String, Value>,
    lines: &[LineForm],
    indices: &Indices,
    atoms: &[SourceAtom],
    max_tokens: u64,
) -> Assessments {
    if indices.is_empty() {
        return Assessments::new();
    }
    let mut assessments =
        request_assessments(backend, details, lines, indices, atoms, max_tokens).await;
    let missing: Vec<_> = indices
        .iter()
        .filter(|i| !assessments.contains_key(i))
        .copied()
        .collect();
    for i in missing {
        tracing::info!(
            event = "haiku_grounding",
            result = "assessment_retry",
            line_index = i
        );
        assessments.extend(
            request_assessments(
                backend,
                details,
                lines,
                &BTreeSet::from([i]),
                atoms,
                max_tokens,
            )
            .await,
        );
    }
    assessments
}
async fn request_assessments<B: Backend>(
    backend: &mut B,
    details: &Map<String, Value>,
    lines: &[LineForm],
    indices: &Indices,
    atoms: &[SourceAtom],
    max_tokens: u64,
) -> Assessments {
    let mut details = details.clone();
    details.insert(
        "grounding_lines".into(),
        json!(
            indices
                .iter()
                .map(|i| json!({"line_index":i,"text":lines[*i].text}))
                .collect::<Vec<_>>()
        ),
    );
    details.insert("source_atoms".into(), json!(atoms));
    // Insertion order matches Python: eligible IDs first, then basis IDs. Basis-only
    // numbers are printed for provenance but cannot be returned as eligible evidence.
    let mut numbers = Map::new();
    for id in atoms
        .iter()
        .map(|a| &a.atom_id)
        .chain(atoms.iter().flat_map(|a| a.basis_atom_ids.iter()))
    {
        if !numbers.contains_key(id) {
            let next = numbers.len() + 1;
            numbers.insert(id.clone(), json!(next));
        }
    }
    details.insert(
        "grounding_atom_numbers".into(),
        Value::Object(numbers.clone()),
    );
    let value = request(
        backend,
        StructuredRequest {
            kind: "haiku_line_grounding".into(),
            fallback_value: json!({"assessments":[]}),
            details,
            temperature: 0.0,
            route: "chat".into(),
            max_tokens: Some(max_tokens),
        },
    )
    .await;
    parse_assessments(&value, lines, indices, atoms, &numbers)
}
fn parse_assessments(
    payload: &Value,
    lines: &[LineForm],
    indices: &Indices,
    atoms: &[SourceAtom],
    numbers: &Map<String, Value>,
) -> Assessments {
    let mut out = Assessments::new();
    if !structured_accepted(payload) {
        return out;
    }
    let single;
    let rows = if let Some(rows) = payload.get("assessments").and_then(Value::as_array) {
        rows
    } else if payload.get("line_index").and_then(Value::as_i64).is_some() {
        single = vec![payload.clone()];
        &single
    } else {
        return out;
    };
    let eligible: BTreeSet<_> = atoms.iter().map(|a| a.atom_id.as_str()).collect();
    let by_number: BTreeMap<_, _> = eligible
        .iter()
        .filter_map(|id| numbers.get(*id).and_then(Value::as_u64).map(|n| (n, *id)))
        .collect();
    let verdicts = payload.get("verdicts").filter(|v| !v.is_null());
    for row in rows {
        let Some(index) = line_index(row.get("line_index")) else {
            continue;
        };
        if !indices.contains(&index) || out.contains_key(&index) {
            continue;
        }
        let (meaning, natural) = if let Some(verdicts) = verdicts {
            let pair = match verdicts.get(index.to_string()).and_then(Value::as_str) {
                Some("pass") => (true, true),
                Some("meaning_fail") => (false, true),
                Some("japanese_fail") => (true, false),
                Some("both_fail") => (false, false),
                _ => continue,
            };
            if row
                .get("meaning_retained")
                .is_some_and(|v| v.as_bool() != Some(pair.0))
                || row
                    .get("natural_japanese")
                    .is_some_and(|v| v.as_bool() != Some(pair.1))
            {
                continue;
            }
            pair
        } else {
            let Some(meaning) = row.get("meaning_retained").and_then(Value::as_bool) else {
                continue;
            };
            let Some(natural) = row.get("natural_japanese").and_then(Value::as_bool) else {
                continue;
            };
            (meaning, natural)
        };
        let Some(raw_ids) = row.get("atom_ids").and_then(Value::as_array) else {
            continue;
        };
        let ids: Option<Vec<String>> = raw_ids
            .iter()
            .map(|v| {
                if let Some(n) = v.as_u64() {
                    by_number.get(&n).map(|id| (*id).to_owned())
                } else {
                    v.as_str()
                        .filter(|id| eligible.contains(id))
                        .map(str::to_owned)
                }
            })
            .collect();
        let Some(ids) = ids else {
            continue;
        };
        if ids.is_empty() && meaning || ids.iter().collect::<BTreeSet<_>>().len() != ids.len() {
            continue;
        }
        let reason = payload
            .get("failure_reasons")
            .and_then(Value::as_object)
            .and_then(|m| m.get(&index.to_string()))
            .or_else(|| row.get("reason"));
        let reason = assessment_reason(reason, &lines[index].text, meaning, natural);
        out.insert(
            index,
            Assessment {
                atom_ids: ids,
                meaning_retained: meaning,
                natural_japanese: natural,
                assessed_text: lines[index].text.clone(),
                reason,
            },
        );
    }
    out
}

/// Reasons explain an already validated verdict; absent or malformed reasons
/// never turn a valid verdict into a pass or an unavailable assessment. Legacy
/// strings remain compatible with completed-prefix recovery and older models.
fn assessment_reason(value: Option<&Value>, line: &str, meaning: bool, natural: bool) -> String {
    if meaning && natural {
        return String::new();
    }
    let Some(value) = value else {
        return String::new();
    };
    if let Some(reason) = value.as_str() {
        return reason.trim().chars().take(240).collect();
    }
    let Some(rows) = value.as_array() else {
        return String::new();
    };
    let mut reasons = Vec::new();
    for row in rows.iter().take(3) {
        let Some(row) = row.as_object().filter(|r| r.len() == 3) else {
            continue;
        };
        let label = match row.get("kind").and_then(Value::as_str) {
            Some("meaning") if !meaning => "意味",
            Some("japanese") if !natural => "日本語",
            _ => continue,
        };
        let Some(fragment) = row.get("fragment").and_then(Value::as_str).map(str::trim) else {
            continue;
        };
        let Some(reason) = row.get("reason").and_then(Value::as_str).map(str::trim) else {
            continue;
        };
        if fragment.is_empty()
            || fragment.chars().count() > 24
            || !line.contains(fragment)
            || reason.is_empty()
            || reason.chars().count() > 48
            || fragment.chars().chain(reason.chars()).any(char::is_control)
        {
            continue;
        }
        // Three fully rendered reasons fit the existing 240-character feedback
        // limit, so no valid third reason disappears in prompt truncation.
        let reason = format!("{label}「{fragment}」:{reason}");
        if !reasons.contains(&reason) {
            reasons.push(reason);
        }
    }
    reasons.join("、")
}
#[allow(clippy::too_many_arguments)]
pub(super) async fn accept_lines<B: Backend>(
    backend: &mut B,
    lines: &mut [LineForm],
    indices: &Indices,
    assessments: &Assessments,
    details: &Map<String, Value>,
    atoms: &BTreeMap<String, SourceAtom>,
    already_used: &BTreeSet<String>,
    mut seen: BTreeSet<String>,
) -> Result<(Assessments, Failures)> {
    let mut accepted = Assessments::new();
    let mut failures = Failures::new();
    let mut used = already_used.clone();
    for i in indices {
        let assessment = &assessments[i];
        if assessment.meaning_retained {
            lines[*i] = transform(
                backend,
                lines[*i].text.clone(),
                *i,
                TransformMode::Correct,
                assessment.atom_ids.clone(),
                atoms.values().cloned().collect(),
            )
            .await?;
        }
        let mut reasons = Vec::<String>::new();
        if !assessment.meaning_retained {
            reasons.push("meaning_not_retained".into());
        }
        if !assessment.natural_japanese {
            reasons.push("unnatural_japanese".into());
        }
        let claims = reservations(&assessment.atom_ids, atoms);
        if !used.is_disjoint(&claims) {
            reasons.push("source_reused".into());
        }
        reasons.extend(meter::line_failure_reasons(&lines[*i].text, *i, details));
        let signature = &lines[*i].signature;
        if signature.is_empty() || seen.contains(signature) {
            reasons.push("duplicate_line".into());
        }
        if !reasons.is_empty() {
            failures.insert(*i, reasons);
            continue;
        }
        accepted.insert(*i, assessment.clone());
        used.extend(claims);
        seen.insert(signature.clone());
    }
    Ok((accepted, failures))
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
#[allow(clippy::too_many_arguments)]
async fn regenerate<B: Backend>(
    backend: &mut B,
    details: &Map<String, Value>,
    lines: &[LineForm],
    failed: &Indices,
    failures: &Failures,
    atoms: &[SourceAtom],
    max_tokens: Option<u64>,
    comments: &BTreeMap<usize, String>,
) -> BTreeMap<usize, String> {
    let mut details = details.clone();
    let rows: Vec<_> = lines
        .iter()
        .enumerate()
        .map(|(index, line)| {
            let mut row =
                json!({"line_index": index, "text": line.text, "frozen": !failed.contains(&index)});
            if failed.contains(&index) {
                let count = meter::count_japanese_sounds(&line.text);
                let target = meter::TARGETS[index];
                let meter_status = if count < target - 1 {
                    "too_short"
                } else if count > target + 1 {
                    "too_long"
                } else {
                    "within_range"
                };
                let feedback = json!({
                    "sound_count": count, "target_sound_count": target,
                    "allowed_sound_min": target - 1, "allowed_sound_max": target + 1,
                    "meter_status": meter_status,
                    "failure_reasons": failures.get(&index).cloned().unwrap_or_default(),
                    "assessment_comment": comments.get(&index).map(String::as_str).unwrap_or("")
                });
                row.as_object_mut()
                    .unwrap()
                    .extend(feedback.as_object().unwrap().clone());
            }
            row
        })
        .collect();
    details.insert("current_lines".into(), json!(rows));
    details.insert("failed_line_indices".into(), json!(failed));
    details.insert("source_atoms".into(), json!(atoms));
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
            let i = line_index(row.get("line_index"))?;
            let text = clean_line(row.get("text")?.as_str()?)?;
            if !failed.contains(&i) || out.contains_key(&i) {
                return None;
            }
            out.insert(i, text);
        }
        (out.keys().copied().collect::<Indices>() == *failed).then_some(out)
    };
    parse().unwrap_or_default()
}
fn line_index(value: Option<&Value>) -> Option<usize> {
    value?.as_u64()?.try_into().ok()
}
fn duplicate_failures(lines: &[LineForm]) -> Failures {
    let mut seen = BTreeSet::new();
    let mut out = Failures::new();
    for (i, line) in lines.iter().enumerate() {
        if line.signature.is_empty() || !seen.insert(line.signature.clone()) {
            out.insert(i, vec!["duplicate_line".into()]);
        }
    }
    out
}
fn atom_reservations(atom: &SourceAtom) -> BTreeSet<String> {
    if atom.kind == "poetic_interpretation" {
        BTreeSet::new()
    } else if atom.basis_atom_ids.is_empty() {
        BTreeSet::from([atom.atom_id.clone()])
    } else {
        atom.basis_atom_ids.iter().cloned().collect()
    }
}
pub(super) fn reservations(
    ids: &[String],
    atoms: &BTreeMap<String, SourceAtom>,
) -> BTreeSet<String> {
    ids.iter()
        .filter_map(|id| atoms.get(id))
        .flat_map(atom_reservations)
        .collect()
}
fn used_claims(accepted: &Assessments, atoms: &BTreeMap<String, SourceAtom>) -> BTreeSet<String> {
    accepted
        .values()
        .flat_map(|a| reservations(&a.atom_ids, atoms))
        .collect()
}
pub(super) fn eligible_atoms(atoms: &[SourceAtom], used: &BTreeSet<String>) -> Vec<SourceAtom> {
    atoms
        .iter()
        .filter(|a| used.is_disjoint(&atom_reservations(a)))
        .cloned()
        .collect()
}
pub(super) fn clean_line(text: &str) -> Option<String> {
    let text = text
        .trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
        .trim_matches(['「', '」', '"', '\'', ' ']);
    (!text.is_empty() && !text.contains(['\n', '\r'])).then(|| text.to_owned())
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
        event = "haiku_grounding",
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
