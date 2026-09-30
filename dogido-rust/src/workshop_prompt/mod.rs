//! 川柳相談のprompt組み立て。材料投影・根拠検査・編集権限を変更しない。
#[cfg(test)]
mod tests;
use crate::planner::python_json;
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use std::{collections::BTreeMap, sync::LazyLock};

static ASSETS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("templates.json")).expect("checked workshop prompt assets")
});

/// Only the fields read by consultation_messages. Other projected inspection
/// fields remain owned by the existing helper's semantic validator.
#[derive(Deserialize)]
struct Details {
    phase: String,
    conversation_stage: String,
    player_text: String,
    original_player_text: String,
    allowed_actions: Vec<String>,
    allowed_purposes: Vec<String>,
    allowed_problem_types: Vec<String>,
    turn_steps: Vec<Map<String, Value>>,
    tool_observation: Option<Map<String, Value>>,
    #[serde(default)]
    workshop_context: Map<String, Value>,
    #[serde(default)]
    pending_verse: Option<String>,
}
use crate::compat::json_truthy as truth;
fn render(parts: &Value, slots: &BTreeMap<&str, String>) -> Result<String> {
    parts
        .as_array()
        .context("invalid workshop prompt template")?
        .iter()
        .map(|part| {
            if let Some(text) = part["literal"].as_str() {
                Ok(text.to_owned())
            } else {
                Ok(slots
                    .get(
                        part["slot"]
                            .as_str()
                            .context("invalid workshop prompt slot")?,
                    )
                    .context("missing workshop prompt slot")?
                    .clone())
            }
        })
        .collect()
}

/// The prepared frame is code-owned projection, never a model response. A legacy
/// messages-only response is rejected rather than silently bypassing Rust.
/// Fixed edits still pass through the existing validation, and skip retry prose.
pub(super) fn prepare(prepared: &Value, retry: Option<&Value>) -> Result<Value> {
    prepare_with_assets(prepared, retry, &ASSETS)
}

pub(super) fn prepare_with_assets(
    prepared: &Value,
    retry: Option<&Value>,
    assets: &Value,
) -> Result<Value> {
    let projection = prepared.as_object().context("workshop prepared object")?;
    ensure!(
        projection
            .keys()
            .all(|k| matches!(k.as_str(), "details" | "fixed_payload")),
        "unexpected workshop prepared field"
    );
    let details: Details = serde_json::from_value(prepared["details"].clone())
        .context("invalid workshop prompt details")?;
    if let Some(fixed) = projection.get("fixed_payload") {
        ensure!(fixed.is_object(), "invalid workshop fixed payload");
    }
    let allowed = |name: &str| details.allowed_actions.iter().any(|a| a == name);
    let context = if details.workshop_context.is_empty() {
        String::new()
    } else {
        format!(
            "{}{}{}",
            assets["context_prefix"]
                .as_str()
                .context("context prefix")?,
            python_json(&Value::Object(details.workshop_context.clone())),
            assets["context_suffix"]
                .as_str()
                .context("context suffix")?
        )
    };
    let mut slots = BTreeMap::from([
        ("original", details.original_player_text.clone()),
        ("player", details.player_text.clone()),
        ("stage", details.conversation_stage.clone()),
        ("phase", details.phase.clone()),
        ("problems", details.allowed_problem_types.join(", ")),
        ("actions", details.allowed_actions.join(", ")),
        ("purposes", details.allowed_purposes.join(", ")),
        (
            "steps",
            python_json(&serde_json::to_value(&details.turn_steps)?),
        ),
        (
            "observation",
            python_json(&serde_json::to_value(&details.tool_observation)?),
        ),
        ("context", context),
    ]);
    let followup = [
        "acknowledge_meaning",
        "confirm_close",
        "continue_workshop",
        "resume_workshop",
        "decline_resume",
    ]
    .iter()
    .any(|a| allowed(a));
    let conditions = [
        (
            "raw_semantic",
            details.original_player_text != details.player_text,
        ),
        (
            "current_idea",
            details
                .workshop_context
                .get("current_player_idea")
                .is_some_and(truth),
        ),
        ("meaning_ack", allowed("acknowledge_meaning")),
        ("confirm_close", allowed("confirm_close")),
        ("resume", allowed("resume_workshop")),
        ("followup", followup),
        ("editing", allowed("stage_player_edit")),
        (
            "conversation_candidate",
            allowed("stage_conversation_candidate"),
        ),
        ("propose_revision", allowed("propose_revision")),
        (
            "pending",
            details
                .pending_verse
                .as_ref()
                .is_some_and(|s| !s.is_empty()),
        ),
        ("after_validation", details.phase == "after_validation"),
    ];
    let mut extra = String::new();
    for (key, include) in conditions {
        if include {
            extra.push_str(&render(&assets["extras"][key], &slots)?);
        }
    }
    slots.insert("extra", extra);
    let mut messages = vec![
        json!({"role":"system", "content":assets["system"].as_str().context("system prompt")?}),
        json!({"role":"user", "content":render(&assets["main"], &slots)?}),
    ];
    if projection.get("fixed_payload").is_none()
        && let Some(retry) = retry.filter(|v| truth(v))
    {
        slots.insert("retry", python_json(retry));
        messages.push(json!({"role":"user", "content":render(&assets["retry"], &slots)?}));
    }
    let mut output = json!({"messages":messages});
    if let Some(fixed) = projection.get("fixed_payload") {
        output["fixed_payload"] = fixed.clone();
    }
    // Keep the former Python response limit, including its trailing newline.
    ensure!(
        python_json(&output).len() < 1_000_000,
        "workshop prompt too large"
    );
    Ok(output)
}

/// Editable text templates preserve code-owned placeholders and routing data.
pub(in crate::dialogue) fn editable_defaults() -> Value {
    fn flatten(parts: &Value) -> String {
        parts
            .as_array()
            .unwrap()
            .iter()
            .map(|p| {
                p["literal"]
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| format!("{{{{{}}}}}", p["slot"].as_str().unwrap()))
            })
            .collect()
    }
    json!({"system":ASSETS["system"],"main":flatten(&ASSETS["main"]),
        "extras":ASSETS["extras"].as_object().unwrap().iter().map(|(k,v)|(k.clone(),Value::String(flatten(v)))).collect::<Map<_,_>>(),
        "context_prefix":ASSETS["context_prefix"],"context_suffix":ASSETS["context_suffix"],"retry":flatten(&ASSETS["retry"])})
}

pub(in crate::dialogue) fn editable_assets(settings: &Value) -> Result<Value> {
    fn parts(text: &str, reference: &Value) -> Result<Value> {
        let mut out = vec![];
        let mut rest = text;
        while let Some(start) = rest.find("{{") {
            if start > 0 {
                out.push(json!({"literal":&rest[..start]}));
            }
            let tail = &rest[start + 2..];
            let end = tail
                .find("}}")
                .context("閉じていないプレースホルダーがあります")?;
            out.push(json!({"slot":&tail[..end]}));
            rest = &tail[end + 2..];
        }
        if !rest.is_empty() {
            out.push(json!({"literal":rest}));
        }
        let slots = |v: &Value| {
            let mut names = v
                .as_array()
                .unwrap()
                .iter()
                .filter_map(|p| p["slot"].as_str().map(str::to_owned))
                .collect::<Vec<_>>();
            names.sort();
            names
        };
        let out = Value::Array(out);
        ensure!(
            slots(&out) == slots(reference),
            "{{{{...}}}} のプレースホルダーを保ってください"
        );
        Ok(out)
    }
    let defaults = editable_defaults();
    ensure!(
        settings
            .as_object()
            .is_some_and(|o| o.len() == defaults.as_object().unwrap().len()),
        "プロンプト設定の項目が不正です"
    );
    ensure!(
        serde_json::to_vec(settings)?.len() <= 256_000,
        "プロンプト設定が長すぎます"
    );
    let mut assets = ASSETS.clone();
    for name in ["system", "context_prefix", "context_suffix"] {
        assets[name] = settings[name]
            .as_str()
            .context("プロンプトは文字列で指定してください")?
            .into();
    }
    for name in ["main", "retry"] {
        assets[name] = parts(
            settings[name]
                .as_str()
                .context("テンプレートは文字列で指定してください")?,
            &ASSETS[name],
        )?;
    }
    let extra = settings["extras"].as_object().context("extrasが不正です")?;
    ensure!(
        extra.len() == ASSETS["extras"].as_object().unwrap().len(),
        "行動別の指示が不足しています"
    );
    for (name, original) in ASSETS["extras"].as_object().unwrap() {
        assets["extras"][name] = parts(
            extra
                .get(name)
                .and_then(Value::as_str)
                .context("行動別の指示が不足しています")?,
            original,
        )?;
    }
    Ok(assets)
}
