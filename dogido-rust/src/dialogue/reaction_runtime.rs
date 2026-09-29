//! Reaction wording has no Python judgement round trip. Only pronunciation uses
//! the existing bounded helper; its child is reaped on success, failure or cancel.
use super::{DialogueConfig, bridge};
use crate::{
    haiku_bridge::Helper,
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
    read_speech(config, input, result, cancel, deadline).await
}
async fn read_speech(
    config: &DialogueConfig,
    input: &Value,
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
    let script = config
        .helper
        .parent()
        .context("dialogue helper directory")?
        .join("workshop_helper.py");
    let mut helper = Helper::start(&config.python, &script)?;
    let body = async {
        helper
            .exchange(json!({"op":"reading_overlay","rows":input["reading_corrections"]}))
            .await?;
        let reading=helper.exchange(json!({"op":"reading","text":result["text"],"reading_engine":config.reading_engine})).await?;
        result["spoken_text"] = reading["spoken_text"]
            .as_str()
            .context("reaction pronunciation result")?
            .into();
        Ok(result)
    };
    let result: Result<Value> = tokio::select! {biased;
        _=bridge::cancelled(cancel)=>Err(anyhow::anyhow!("cancelled")),
        result=tokio::time::timeout_at(reading_deadline,body)=>result.unwrap_or_else(|_|Err(anyhow::anyhow!("reaction reading timed out"))),
    };
    helper.finish(result.is_err()).await?;
    result
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
    #[cfg(unix)]
    #[tokio::test]
    async fn pronunciation_only_helper_is_reaped_on_success_cancel_and_deadline() {
        for mode in ["success", "cancel", "timeout"] {
            let interrupted = mode != "success";
            let dir =
                std::env::temp_dir().join(format!("dogido-reaction-{}", uuid::Uuid::new_v4()));
            std::fs::create_dir_all(&dir).unwrap();
            let path = dir.join("workshop_helper.py");
            let script = format!(
                "echo $$ > '{}/pid'\nread -r line\nprintf '%s\\n' \"$line\" >> '{}/requests'\nprintf '{{\"ok\":true}}\\n'\nread -r line\nprintf '%s\\n' \"$line\" >> '{}/requests'\n{}\n",
                dir.display(),
                dir.display(),
                dir.display(),
                if interrupted {
                    "read -r blocked"
                } else {
                    "printf '{\"spoken_text\":\"よみをなおしたで。\"}\\n'"
                }
            );
            std::fs::write(&path, script).unwrap();
            let config = DialogueConfig {
                python: "/bin/sh".into(),
                helper: dir.join("dialogue_helper.py"),
                ..DialogueConfig::default()
            };
            let (tx, mut rx) = watch::channel(false);
            let budget = if mode == "timeout" {
                Duration::from_millis(100)
            } else {
                Duration::from_secs(3)
            };
            let task = tokio::spawn(async move {
                read_speech(
                    &config,
                    &json!({"reading_corrections":[]}),
                    json!({"text":"読みを直したで。"}),
                    &mut rx,
                    tokio::time::Instant::now() + budget,
                )
                .await
            });
            if mode == "cancel" {
                for _ in 0..200 {
                    if std::fs::read_to_string(dir.join("requests"))
                        .unwrap_or_default()
                        .lines()
                        .count()
                        == 2
                    {
                        break;
                    }
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }
                tx.send(true).unwrap();
            }
            let result = task.await.unwrap();
            if interrupted {
                assert!(result.is_err());
            } else {
                assert_eq!(result.unwrap()["spoken_text"], "よみをなおしたで。");
            }
            let requests = std::fs::read_to_string(dir.join("requests")).unwrap();
            let ops: Vec<Value> = requests
                .lines()
                .map(|s| serde_json::from_str(s).unwrap())
                .collect();
            assert_eq!(ops.len(), 2);
            assert_eq!(ops[0]["op"], "reading_overlay");
            assert_eq!(ops[1]["op"], "reading");
            let pid: i32 = std::fs::read_to_string(dir.join("pid"))
                .unwrap()
                .trim()
                .parse()
                .unwrap();
            assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
            assert_eq!(
                std::io::Error::last_os_error().raw_os_error(),
                Some(libc::ESRCH)
            );
            std::fs::remove_dir_all(&dir).unwrap();
        }
    }
}
