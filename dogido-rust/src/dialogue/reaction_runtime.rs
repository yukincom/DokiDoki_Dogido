//! Reaction wording has no Python judgement round trip. Only pronunciation uses
//! a minimal optional UniDic adapter; Ready text needs no child process.
use super::{DialogueConfig, bridge};
use crate::{
    llm::RigLlm,
    reaction_leaf::Leaf,
    types::{GenerationReport, GenerationRequest},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{future::Future, time::Duration};
use tokio::sync::watch;

async fn generate_once<F: Future<Output = Result<GenerationReport>>>(
    leaf: &Leaf,
    cancel: &mut watch::Receiver<bool>,
    generate: impl FnOnce(GenerationRequest) -> F,
) -> Result<Value> {
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    // FnOnce deliberately makes retry/repair impossible for the reaction contract.
    let response = tokio::select! {biased;
        _=bridge::cancelled(cancel)=>anyhow::bail!("cancelled"),
        result=generate(leaf.request.clone())=>result,
    };
    let (raw, report) = match response {
        Ok(report) => {
            tracing::info!(kind=leaf.request.kind,elapsed_ms=report.elapsed_ms as u64,completion_tokens=?report.generated.completion_tokens,finish_reason=?report.generated.finish_reason);
            (
                Some(report.generated.text.clone()),
                serde_json::to_value(report)?,
            )
        }
        Err(error) => (
            None,
            json!({"kind":leaf.request.kind,"error":error.to_string()}),
        ),
    };
    let (text, outcome) = leaf.finish(raw.as_deref());
    tracing::info!(
        event = "reaction_leaf",
        kind = leaf.request.kind,
        implementation = "rust",
        outcome
    );
    Ok(json!({"op":"result","text":text,"llm_reports":[report],"reaction_leaf_outcome":outcome}))
}
pub(super) async fn render(
    config: &DialogueConfig,
    llm: &RigLlm,
    input: &Value,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Value> {
    // Keep the former bridge's whole-turn budget even though wording is now native.
    let deadline = tokio::time::Instant::now() + Duration::from_secs(95);
    let leaf = Leaf::prepare(input, &config.model, config.max_tokens)?;
    let result = tokio::time::timeout_at(
        deadline,
        generate_once(&leaf, cancel, |request| async move {
            llm.generate(&request).await
        }),
    )
    .await
    .context("reaction generation timed out")??;
    read_speech(config, result, cancel, deadline).await
}
async fn read_speech(
    config: &DialogueConfig,
    mut result: Value,
    cancel: &mut watch::Receiver<bool>,
    deadline: tokio::time::Instant,
) -> Result<Value> {
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    ensure!(tokio::time::Instant::now() < deadline, "reaction timed out");
    let reading_deadline = deadline.min(tokio::time::Instant::now() + Duration::from_secs(35));
    result["spoken_text"] = super::tts_runtime::read(
        config,
        result["text"].as_str().context("reaction text")?,
        cancel,
        reading_deadline,
    )
    .await?
    .into();
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };
    fn leaf() -> Leaf {
        Leaf::prepare(&json!({"kind":"aftermath","model":"fixture","max_tokens":72,"temperature":0.65,"details":{},"fallback_text":"一段落したな。"}),"fixture",72).unwrap()
    }
    #[tokio::test]
    async fn backend_error_falls_back_once_and_cancel_never_falls_back_or_retries() {
        let (tx, mut rx) = watch::channel(false);
        let count = AtomicUsize::new(0);
        let result = generate_once(&leaf(), &mut rx, |_| {
            count.fetch_add(1, Ordering::SeqCst);
            async { Err(anyhow::anyhow!("test generation error")) }
        })
        .await
        .unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
        assert_eq!(result["text"], "一段落したな。");
        tx.send(true).unwrap();
        assert!(
            generate_once(&leaf(), &mut rx, |_| {
                count.fetch_add(1, Ordering::SeqCst);
                async { Err(anyhow::anyhow!("must not run")) }
            })
            .await
            .is_err()
        );
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }
    #[tokio::test]
    async fn inflight_cancel_drops_generation_without_fallback_speech() {
        let (tx, mut rx) = watch::channel(false);
        let count = Arc::new(AtomicUsize::new(0));
        let calls = count.clone();
        let task = tokio::spawn(async move {
            generate_once(&leaf(), &mut rx, |_| {
                calls.fetch_add(1, Ordering::SeqCst);
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
    }
    #[tokio::test]
    async fn native_reading_preserves_display_and_generation_report() {
        let config = DialogueConfig {
            python: "/missing/reaction-python".into(),
            helper: "/missing/reaction-helper.py".into(),
            reading_engine: "off".into(),
            ..Default::default()
        };
        let (_owner, mut cancel) = watch::channel(false);
        let result = read_speech(
            &config,
            json!({"text":" 今朝は元気や。 ","llm_reports":[{"kind":"aftermath"}]}),
            &mut cancel,
            tokio::time::Instant::now() + Duration::from_secs(1),
        )
        .await
        .unwrap();
        assert_eq!(result["text"], " 今朝は元気や。 ");
        // Preserve the canonical manual table order (朝は precedes 今朝).
        assert_eq!(result["spoken_text"], "今あさは元気や。");
        assert_eq!(result["llm_reports"], json!([{"kind":"aftermath"}]));
    }
}
