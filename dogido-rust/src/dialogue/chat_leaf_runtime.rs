//! Ordinary speech candidates: model calls, rejection and one repair are Rust-owned.
use super::bridge;
use crate::{
    chat_validation::Turn,
    llm::RigLlm,
    types::{GenerationReport, GenerationRequest},
};
use anyhow::{Result, ensure};
use serde_json::{Value, json};
use std::future::Future;
use tokio::sync::watch;

pub(super) struct Run {
    pub text: String,
    pub final_text: String,
    pub reports: Vec<Value>,
}
pub(super) async fn render(
    turn: Turn,
    llm: &RigLlm,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Run> {
    render_with(turn, cancel, |request| async move {
        llm.generate(&request).await
    })
    .await
}
async fn render_with<F: Future<Output = Result<GenerationReport>>>(
    mut turn: Turn,
    cancel: &mut watch::Receiver<bool>,
    mut generate: impl FnMut(GenerationRequest) -> F,
) -> Result<Run> {
    let mut reports = vec![];
    loop {
        ensure!(
            !*cancel.borrow() && cancel.has_changed().is_ok(),
            "cancelled"
        );
        let request = turn.request()?;
        let response = tokio::select! {biased;_=bridge::cancelled(cancel)=>anyhow::bail!("cancelled"),result=generate(request)=>result};
        let raw = match response {
            Ok(report) => {
                tracing::info!(kind="player_chat",elapsed_ms=report.elapsed_ms as u64,completion_tokens=?report.generated.completion_tokens,finish_reason=?report.generated.finish_reason);
                let text = report.generated.text.clone();
                reports.push(serde_json::to_value(report)?);
                Some(text)
            }
            Err(error) => {
                reports.push(json!({"kind":"player_chat","error":error.to_string()}));
                None
            }
        };
        if turn.complete(raw.as_deref())? {
            break;
        }
    }
    // Cancellation never commits a fallback or a just-completed old candidate.
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    let outcome = turn.take_outcome()?;
    for (i, v) in outcome.validations.iter().enumerate() {
        tracing::info!(event="chat_leaf_validation",attempt=i+1,reason=?v.reason,issue=?v.issue,corrections=?v.corrections);
    }
    tracing::info!(
        event = "chat_leaf",
        implementation = "rust",
        status = outcome.status,
        attempts = outcome.attempts
    );
    Ok(Run {
        text: outcome.text,
        final_text: outcome.final_text,
        reports,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::GeneratedText;
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };
    fn turn() -> Turn {
        Turn::new(serde_json::from_value(json!({"prompt":{"schema_version":1,"kind":"player_chat","model":"fixture","temperature":0.65,"max_tokens":72,"enable_thinking":false,"details":{}},"validation":{},"fallback_text":"話を聞いとるで。"})).unwrap(),"fixture",72).unwrap()
    }
    fn report(text: &str) -> GenerationReport {
        GenerationReport {
            schema_version: 1,
            provider: "fake".into(),
            kind: "player_chat".into(),
            requested_model: "fixture".into(),
            response_model: "fixture".into(),
            response_id: "fake".into(),
            elapsed_ms: 1,
            generated: GeneratedText {
                text: text.into(),
                finish_reason: Some("stop".into()),
                completion_tokens: Some(4),
                prompt_tokens: Some(2),
            },
        }
    }
    #[tokio::test]
    async fn model_failure_does_not_retry_and_invalid_text_is_bounded_to_two() {
        for first_error in [true, false] {
            let (tx, mut rx) = watch::channel(false);
            let mut calls = 0;
            let result = render_with(turn(), &mut rx, |_| {
                calls += 1;
                async move {
                    if first_error {
                        Err(anyhow::anyhow!("network failure"))
                    } else {
                        Ok(report("English explanation"))
                    }
                }
            })
            .await
            .unwrap();
            assert_eq!(calls, if first_error { 1 } else { 2 });
            assert_eq!(result.reports.len(), calls);
            assert_eq!(result.text, "話を聞いとるで。");
            drop(tx);
        }
    }
    #[tokio::test]
    async fn second_network_failure_returns_original_fallback_once() {
        let (_tx, mut rx) = watch::channel(false);
        let mut calls = 0;
        let result = render_with(turn(), &mut rx, |_| {
            calls += 1;
            let n = calls;
            async move {
                if n == 1 {
                    Ok(report("English explanation"))
                } else {
                    Err(anyhow::anyhow!("network failure"))
                }
            }
        })
        .await
        .unwrap();
        assert_eq!(calls, 2);
        assert_eq!(result.text, "話を聞いとるで。");
    }
    #[tokio::test]
    async fn cancellation_before_during_and_between_attempts_never_speaks() {
        let (tx, mut rx) = watch::channel(true);
        let mut calls = 0;
        assert!(
            render_with(turn(), &mut rx, |_| {
                calls += 1;
                async { Ok(report("ありがとうやで。")) }
            })
            .await
            .is_err()
        );
        assert_eq!(calls, 0);
        drop(tx);
        let (tx, mut rx) = watch::channel(false);
        let count = Arc::new(AtomicUsize::new(0));
        let c = count.clone();
        let task = tokio::spawn(async move {
            render_with(turn(), &mut rx, |_| {
                c.fetch_add(1, Ordering::SeqCst);
                std::future::pending::<Result<GenerationReport>>()
            })
            .await
        });
        while count.load(Ordering::SeqCst) == 0 {
            tokio::task::yield_now().await;
        }
        tx.send(true).unwrap();
        assert!(task.await.unwrap().is_err());
        assert_eq!(count.load(Ordering::SeqCst), 1);
        let (tx, mut rx) = watch::channel(false);
        let mut calls = 0;
        assert!(
            render_with(turn(), &mut rx, |_| {
                calls += 1;
                let tx = tx.clone();
                async move {
                    tx.send(true).unwrap();
                    Ok(report("English explanation"))
                }
            })
            .await
            .is_err()
        );
        assert_eq!(calls, 1);
    }
}
