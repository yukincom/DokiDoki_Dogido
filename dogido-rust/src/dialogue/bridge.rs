use super::DialogueConfig;
use crate::{
    llm::RigLlm,
    planner::{self, PreparedPlan},
    types::GenerationRequest,
};
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::{process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::Command,
    sync::watch,
};

pub async fn cancelled(cancel: &mut watch::Receiver<bool>) {
    loop {
        if *cancel.borrow_and_update() {
            return;
        }
        if cancel.changed().await.is_err() {
            return;
        }
    }
}

pub async fn render(
    config: &DialogueConfig,
    llm: &RigLlm,
    input: Value,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Value> {
    let combat_kind =
        (input["op"] == "combat_leaf").then(|| input["kind"].as_str().unwrap_or("").to_owned());
    if let Some(kind) = combat_kind.as_deref() {
        ensure!(
            matches!(
                kind,
                "death"
                    | "aftermath"
                    | "daylight_water_skeleton"
                    | "newly_burning_visual"
                    | "deep_dark_ominous_sound"
                    | "occluded_hostile_presence"
            ),
            "unexpected combat leaf"
        );
    }
    let mut child = Command::new(&config.python)
        .arg(&config.helper)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .context("cannot start dialogue helper")?;
    let pid = child.id();
    tracing::info!(event = "helper_started", ?pid);
    let mut stdin = child.stdin.take().context("helper stdin")?;
    let mut stdout = BufReader::new(child.stdout.take().context("helper stdout")?).lines();
    let protocol = async {
        stdin.write_all(format!("{input}\n").as_bytes()).await?;
        let (mut plans, mut leaves) = (0, 0);
        let mut reports = Vec::new();
        while let Some(line) = stdout.next_line().await? {
            ensure!(line.len() < 1_000_000, "helper frame too large");
            let mut frame: Value = serde_json::from_str(&line).context("invalid helper JSON")?;
            let reply = match frame["op"].as_str() {
                Some("plan") => {
                    ensure!(combat_kind.is_none(), "combat leaf cannot invoke planner");
                    plans += 1;
                    ensure!(plans == 1, "planner step limit exceeded");
                    let request: PreparedPlan = serde_json::from_value(frame["input"].take())?;
                    ensure!(
                        request.model == config.model && !request.enable_thinking,
                        "unexpected planner model"
                    );
                    let report = planner::run(llm, &request).await?;
                    for attempt in &report.attempts {
                        tracing::info!(kind="player_chat_plan", elapsed_ms=attempt.elapsed_ms as u64, completion_tokens=?attempt.generated.completion_tokens, finish_reason=?attempt.generated.finish_reason);
                    }
                    let output = serde_json::to_value(report)?;
                    reports.push(output.clone());
                    output
                }
                Some("generate") => {
                    leaves += 1;
                    ensure!(leaves <= 2, "leaf retry limit exceeded");
                    let request: GenerationRequest = serde_json::from_value(frame["input"].take())?;
                    ensure!(
                        request.kind == combat_kind.as_deref().unwrap_or("player_chat")
                            && request.model == config.model
                            && request.max_tokens == config.max_tokens
                            && !request.enable_thinking,
                        "unexpected helper generation"
                    );
                    match llm.generate(&request).await {
                        Ok(report) => {
                            tracing::info!(kind=request.kind, elapsed_ms=report.elapsed_ms as u64, completion_tokens=?report.generated.completion_tokens, finish_reason=?report.generated.finish_reason);
                            let output = serde_json::to_value(report)?;
                            reports.push(output.clone());
                            output
                        }
                        Err(error) => {
                            let output = json!({"error":error.to_string()});
                            reports.push(output.clone());
                            output
                        }
                    }
                }
                Some("result") => {
                    frame["llm_reports"] = json!(reports);
                    return Ok(frame);
                }
                Some("error") => bail!("dialogue helper: {}", frame["error"]),
                _ => bail!("unexpected helper operation"),
            };
            stdin.write_all(format!("{reply}\n").as_bytes()).await?;
        }
        bail!("dialogue helper ended before a result")
    };
    let result: Result<Value> = tokio::select! {
        _ = cancelled(cancel) => Err(anyhow::anyhow!("cancelled")),
        result = tokio::time::timeout(Duration::from_secs(95), protocol) => result.unwrap_or_else(|_| Err(anyhow::anyhow!("dialogue helper timed out"))),
    };
    drop(stdin);
    // 成功でも異常でも所有するhelperを回収してから返す。
    if result.is_err() {
        let _ = child.kill().await;
    }
    match tokio::time::timeout(Duration::from_secs(2), child.wait()).await {
        Ok(status) => {
            let status = status?;
            if result.is_ok() {
                ensure!(status.success(), "helper exit {status}");
            }
        }
        Err(_) => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            bail!("helper did not exit");
        }
    }
    tracing::info!(event = "helper_stopped", ?pid);
    result
}
