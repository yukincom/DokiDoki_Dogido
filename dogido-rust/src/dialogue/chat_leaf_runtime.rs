//! 通常対話を一回だけ生成し、コードで整形した本文または沈黙を返す。
//! 内容の照合・再生成・定型の代替発話は行わない。取消結果の履歴確定は所有者に委ねる。
use super::bridge;
use crate::{
    chat_validation,
    llm::RigLlm,
    types::{GenerationReport, GenerationRequest},
};
use anyhow::{Result, ensure};
use serde_json::{Value, json};
use std::future::Future;
use tokio::sync::watch;

pub(super) struct Run {
    pub output: chat_validation::Outcome,
    pub reports: Vec<Value>,
}
pub(super) async fn render(
    request: GenerationRequest,
    llm: &RigLlm,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Run> {
    render_with(request, cancel, |request| async move {
        llm.generate(&request).await
    })
    .await
}
async fn render_with<F: Future<Output = Result<GenerationReport>>>(
    request: GenerationRequest,
    cancel: &mut watch::Receiver<bool>,
    generate: impl FnOnce(GenerationRequest) -> F,
) -> Result<Run> {
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    let response = tokio::select! {biased;_=bridge::cancelled(cancel)=>anyhow::bail!("cancelled"),result=generate(request)=>result};
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    let (output, report) = match response {
        Ok(report) => {
            tracing::info!(kind="player_chat",elapsed_ms=report.elapsed_ms as u64,completion_tokens=?report.generated.completion_tokens,finish_reason=?report.generated.finish_reason);
            // 打切りでも届いた本文はコード補完へ渡す。続きをモデルへ要求しない。
            let output = chat_validation::finish(Some(&report.generated.text));
            (output, serde_json::to_value(report)?)
        }
        Err(error) => (
            chat_validation::finish(None),
            json!({"kind":"player_chat","error":error.to_string()}),
        ),
    };
    tracing::info!(event="chat_leaf",implementation="rust",status=output.status,reason=?output.reason,attempts=1);
    Ok(Run {
        output,
        reports: vec![report],
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
    fn request() -> GenerationRequest {
        crate::chat_prompt::Input {
            schema_version: 1,
            kind: "player_chat".into(),
            model: "fixture".into(),
            details: json!({"dialogue_choice":true}),
            temperature: 0.65,
            max_tokens: 72,
            enable_thinking: false,
        }
        .into_request()
        .unwrap()
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
    async fn failure_and_broken_output_make_one_call_and_no_fallback() {
        for fail in [true, false] {
            let (_owner, mut cancel) = watch::channel(false);
            let mut calls = 0;
            let result = render_with(request(), &mut cancel, |_| {
                calls += 1;
                async move {
                    if fail {
                        Err(anyhow::anyhow!("network failure"))
                    } else {
                        Ok(report(
                            r#"{"action":"speak","speech":"同じや。同じや。同じや。"}"#,
                        ))
                    }
                }
            })
            .await
            .unwrap();
            assert_eq!(calls, 1);
            assert_eq!(result.reports.len(), 1);
            assert!(result.output.text.is_empty());
            assert_eq!(result.output.action, "silent");
        }
    }
    #[tokio::test]
    async fn truncated_body_is_completed_in_code_without_retry() {
        let (_owner, mut cancel) = watch::channel(false);
        let result = render_with(request(), &mut cancel, |_| async {
            let mut output = report(r#"{"action":"speak","speech":"そや"#);
            output.generated.finish_reason = Some("length".into());
            Ok(output)
        })
        .await
        .unwrap();
        assert_eq!(result.output.text, "そや");
        assert_eq!(result.reports.len(), 1);
    }
    #[tokio::test]
    async fn cancelled_generation_never_yields_speech_or_silence_history() {
        let (tx, mut rx) = watch::channel(true);
        let mut calls = 0;
        assert!(
            render_with(request(), &mut rx, |_| {
                calls += 1;
                async { Ok(report("{}")) }
            })
            .await
            .is_err()
        );
        assert_eq!(calls, 0);
        drop(tx);
        let (tx, mut rx) = watch::channel(false);
        let calls = Arc::new(AtomicUsize::new(0));
        let count = calls.clone();
        let task = tokio::spawn(async move {
            render_with(request(), &mut rx, |_| {
                count.fetch_add(1, Ordering::SeqCst);
                std::future::pending::<Result<GenerationReport>>()
            })
            .await
        });
        while calls.load(Ordering::SeqCst) == 0 {
            tokio::task::yield_now().await;
        }
        tx.send(true).unwrap();
        assert!(task.await.unwrap().is_err());
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        let (tx, mut rx) = watch::channel(false);
        assert!(
            render_with(request(), &mut rx, |_| async move {
                tx.send(true).unwrap();
                Ok(report("{}"))
            })
            .await
            .is_err()
        );
    }
}
