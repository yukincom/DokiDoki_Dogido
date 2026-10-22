//! 照明への反応選択と本文を一回で生成する。観測・発話権限・クールダウンは既存ambientが所有する。
//! 返答はactionとspeechだけ。外形不正・生成失敗は無言にし、モデルへ再試行しない。
#[cfg(test)]
mod tests;
mod validation;

use crate::{
    llm::RigLlm,
    planner::extract_object,
    text_format::spaced_json,
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
/// 既存の共有人格と、反応選択・本文をまとめた一回分の指示を作る。
/// 個数や入手方法を推定せず、コードが段階化した観測だけを渡す。
pub fn messages(details: &Value) -> Vec<ChatMessage> {
    vec![
        ChatMessage {
            role: Role::System,
            content: [
                crate::companion_prompt::base(),
                crate::companion_prompt::grounding(),
                PROMPTS["instruction"].as_str().unwrap(),
                PROMPTS["contract"].as_str().unwrap(),
            ]
            .join("\n\n"),
        },
        ChatMessage {
            role: Role::User,
            content: spaced_json(details),
        },
    ]
}

fn request(model: &str, details: &Value) -> GenerationRequest {
    GenerationRequest {
        schema_version: 1,
        kind: KIND.into(),
        model: model.into(),
        messages: messages(details),
        temperature: 0.48,
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
    F: FnOnce(GenerationRequest) -> Fut,
    Fut: Future<Output = Result<GenerationReport>>,
{
    ensure!(
        details.is_object() && fallback.is_object(),
        "invalid light planner input"
    );
    let result = tokio::select! {
        biased;
        _ = async {
            loop {
                if *cancel.borrow_and_update() || cancel.changed().await.is_err() { break; }
            }
        } => anyhow::bail!("cancelled"),
        result = tokio::time::timeout(deadline, run_inner(model, details, fallback, generate)) =>
            result.context("light planner timed out")?,
    };
    // 生成futureの同じpoll内で取消が届いても、完成結果を配送へ返さない。
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    result
}

async fn run_inner<F, Fut>(
    model: &str,
    details: &Value,
    fallback: &Value,
    generate: F,
) -> Result<Value>
where
    F: FnOnce(GenerationRequest) -> Fut,
    Fut: Future<Output = Result<GenerationReport>>,
{
    let report = match generate(request(model, details)).await {
        Ok(report) => report,
        Err(error) => {
            return Ok(frame(
                fallback.clone(),
                "generation_error",
                vec![json!({"error":error.to_string()})],
            ));
        }
    };
    tracing::info!(kind=KIND, elapsed_ms=report.elapsed_ms as u64,
        completion_tokens=?report.generated.completion_tokens, finish_reason=?report.generated.finish_reason);
    let generated = &report.generated;
    let reports = vec![serde_json::to_value(&report)?];
    if matches!(
        generated.finish_reason.as_deref(),
        Some("length" | "max_tokens" | "MAX_TOKENS")
    ) {
        return Ok(frame(fallback.clone(), "output_truncated", reports));
    }
    let Some(payload) = extract_object(&generated.text) else {
        return Ok(frame(fallback.clone(), "invalid_json", reports));
    };
    let errors = contract_errors(&payload, details);
    if !errors.is_empty() {
        tracing::info!(kind = KIND, result = "schema_contract_error", ?errors);
        return Ok(frame(fallback.clone(), "schema_contract_error", reports));
    }
    Ok(frame(payload, "accepted", reports))
}
