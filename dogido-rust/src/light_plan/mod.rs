//! 照明コメントの限定判定。観測・発話権限・クールダウンは既存ambientが所有する。
//! Pythonのstructured契約だけを置換し、JSON不正時の無言と契約一回再試行を保つ。
#[cfg(test)]
mod tests;
mod validation;

use crate::{
    llm::RigLlm,
    planner::{extract_object, python_json},
    types::{ChatMessage, GenerationReport, GenerationRequest, Role},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{collections::BTreeSet, future::Future, sync::LazyLock, time::Duration};
use tokio::sync::watch;

pub use validation::contract_errors;
pub const KIND: &str = "light_source_comment_plan";
const DEADLINE: Duration = Duration::from_secs(95);
static PROMPTS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("prompts.json")).expect("checked light planner prompts")
});

fn actions(details: &Value) -> BTreeSet<&str> {
    details["allowed_actions"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|s| !s.is_empty())
        .collect()
}
fn basis(details: &Value) -> BTreeSet<&str> {
    details["facts"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|v| v["basis_id"].as_str())
        .filter(|s| !s.is_empty())
        .collect()
}
// Python's retry constraints use json.dumps default ensure_ascii=True.
fn ascii_json(value: &Value) -> String {
    let mut out = String::new();
    for ch in python_json(value).chars() {
        if ch.is_ascii() {
            out.push(ch);
        } else {
            for unit in ch.encode_utf16(&mut [0; 2]) {
                use std::fmt::Write;
                write!(out, "\\u{unit:04x}").expect("string write");
            }
        }
    }
    out
}

pub fn messages(details: &Value, retry: Option<(&[String], &Value)>) -> Vec<ChatMessage> {
    let mut slots = json!({
        "actions":python_json(details.get("allowed_actions").filter(|v| !v.is_null()).unwrap_or(&json!([]))),
        "facts":python_json(details.get("facts").filter(|v| !v.is_null()).unwrap_or(&json!([])))
    });
    if let Some((errors, previous)) = retry {
        slots["errors"] = errors.join("、").into();
        slots["previous"] = python_json(previous)
            .chars()
            .take(2400)
            .collect::<String>()
            .into();
        slots["sorted_actions"] = ascii_json(&json!(actions(details))).into();
        slots["sorted_basis"] = ascii_json(&json!(basis(details))).into();
    }
    PROMPTS
        .as_array()
        .unwrap()
        .iter()
        .take(if retry.is_some() { 3 } else { 2 })
        .map(|template| ChatMessage {
            role: if template["role"] == "system" {
                Role::System
            } else {
                Role::User
            },
            content: template["segments"]
                .as_array()
                .unwrap()
                .iter()
                .map(|part| {
                    part["literal"]
                        .as_str()
                        .unwrap_or_else(|| slots[part["slot"].as_str().unwrap()].as_str().unwrap())
                })
                .collect(),
        })
        .collect()
}

fn request(model: &str, details: &Value, retry: Option<(&[String], &Value)>) -> GenerationRequest {
    GenerationRequest {
        schema_version: 1,
        kind: KIND.into(),
        model: model.into(),
        messages: messages(details, retry),
        temperature: 0.0,
        max_tokens: 160,
        enable_thinking: false,
    }
}
fn frame(mut payload: Value, status: &str, reports: Vec<Value>) -> Value {
    payload["__dogido_status"] = status.into();
    tracing::info!(kind = KIND, result = status);
    json!({"op":"result", "payload":payload, "llm_reports":reports})
}

/// 既存helperと同じ全体上限。取消は待機中の生成futureもdropし、追加要求を送らない。
pub async fn run(
    llm: &RigLlm,
    model: &str,
    details: &Value,
    fallback: &Value,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Value> {
    run_bounded(
        model,
        details,
        fallback,
        cancel,
        DEADLINE,
        |request| async move { llm.generate(&request).await },
    )
    .await
}

async fn run_bounded<F, Fut>(
    model: &str,
    details: &Value,
    fallback: &Value,
    cancel: &mut watch::Receiver<bool>,
    deadline: Duration,
    generate: F,
) -> Result<Value>
where
    F: FnMut(GenerationRequest) -> Fut,
    Fut: Future<Output = Result<GenerationReport>>,
{
    ensure!(
        details.is_object() && fallback.is_object(),
        "invalid light planner input"
    );
    tokio::select! {
        biased;
        _ = async {
            loop {
                if *cancel.borrow_and_update() || cancel.changed().await.is_err() { break; }
            }
        } => anyhow::bail!("cancelled"),
        result = tokio::time::timeout(deadline, run_inner(model, details, fallback, generate)) =>
            result.context("light planner timed out")?,
    }
}

async fn run_inner<F, Fut>(
    model: &str,
    details: &Value,
    fallback: &Value,
    mut generate: F,
) -> Result<Value>
where
    F: FnMut(GenerationRequest) -> Fut,
    Fut: Future<Output = Result<GenerationReport>>,
{
    let mut reports = Vec::new();
    let mut retry: Option<(Vec<String>, Value)> = None;
    for attempt in 0..2 {
        let request = request(
            model,
            details,
            retry.as_ref().map(|(e, p)| (e.as_slice(), p)),
        );
        let generated = match generate(request).await {
            Ok(report) => {
                tracing::info!(kind=KIND, elapsed_ms=report.elapsed_ms as u64,
                    completion_tokens=?report.generated.completion_tokens, finish_reason=?report.generated.finish_reason);
                let generated = report.generated.clone();
                reports.push(serde_json::to_value(report)?);
                generated
            }
            Err(error) => {
                reports.push(json!({"error":error.to_string()}));
                return Ok(frame(
                    fallback.clone(),
                    if attempt == 0 {
                        "generation_error"
                    } else {
                        "schema_contract_error"
                    },
                    reports,
                ));
            }
        };
        let Some(payload) = extract_object(&generated.text) else {
            let status = if attempt > 0 {
                "schema_contract_error"
            } else if matches!(
                generated.finish_reason.as_deref(),
                Some("length" | "max_tokens" | "MAX_TOKENS")
            ) {
                "output_truncated"
            } else {
                "invalid_json"
            };
            return Ok(frame(fallback.clone(), status, reports));
        };
        let errors = contract_errors(&payload, details);
        if errors.is_empty() {
            return Ok(frame(payload, "accepted", reports));
        }
        tracing::info!(
            kind = KIND,
            result = "schema_contract_error",
            attempt,
            ?errors
        );
        retry = Some((errors, payload));
        // Give cancellation a scheduling boundary before the single permitted retry.
        tokio::task::yield_now().await;
    }
    Ok(frame(fallback.clone(), "schema_contract_error", reports))
}
