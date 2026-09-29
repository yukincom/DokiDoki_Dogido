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
    render_with_route(config, llm, input, cancel, |_| Ok(()), |_| Ok(false)).await
}

pub async fn render_with_route(
    config: &DialogueConfig,
    llm: &RigLlm,
    mut input: Value,
    cancel: &mut watch::Receiver<bool>,
    mut select_route: impl FnMut(crate::foreground::Route) -> Result<()>,
    mut hold_handoff: impl FnMut(&Value) -> Result<bool>,
) -> Result<Value> {
    if let Some(text) = input["text"].as_str() {
        let prepared = crate::player_text::prepare(text);
        let query = crate::knowledge::query::from_normalized(&prepared.normalized_text);
        if input["op"] == "assist_route" {
            ensure!(
                !*cancel.borrow() && cancel.has_changed().is_ok(),
                "cancelled"
            );
            // 質問候補の抽出だけならhelperも辞書・DBも起動しない。
            return Ok(json!({"knowledge_query":query.is_some()}));
        }
        input["prepared_knowledge_query"] = serde_json::to_value(query)?;
        input["language_requested"] =
            (input["language_active"] == true || prepared.explicit_language).into();
        input["prepared_input"] = serde_json::to_value(prepared)?;
    }
    ensure!(
        input["op"] != "assist_route",
        "knowledge routing needs current text"
    );
    input["reading_corrections"] = json!(super::reading_runtime::load_overlay(config).await?);
    let light_plan = input["op"] == "light_plan";
    let routing_only = matches!(input["op"].as_str(), Some("assist_route" | "address_route"));
    let address_reply = input["address_reply"].as_str().map(str::to_owned);
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
                    | "ambient"
                    | "weather_transition"
                    | "ender_eye_throw"
                    | "structure_entry"
                    | "light_source_gain"
                    | "darkness_escape"
                    | "occluded_entry_with_light"
                    | "occluded_entry_no_light"
                    | "dark_push_no_light"
                    | "dark_push_after_breath"
                    | "emergency_shelter_relief"
                    | "portal_appearance"
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
        let mut knowledge: Option<crate::knowledge::Reply> = None;
        let mut selected = false;
        let mut language: Option<crate::language::Turn> = None;
        while let Some(line) = stdout.next_line().await? {
            ensure!(line.len() < 1_000_000, "helper frame too large");
            let mut frame: Value = serde_json::from_str(&line).context("invalid helper JSON")?;
            let reply = match frame["op"].as_str() {
                Some("language") => {
                    ensure!(
                        address_reply.is_none()
                            && input["language_requested"] == true
                            && combat_kind.is_none()
                            && !light_plan
                            && !routing_only
                            && knowledge.is_none()
                            && input["workshop_fallback"] != true
                            && input["workshop"].is_null()
                            && plans == 0
                            && leaves == 0,
                        "language must precede ordinary chat"
                    );
                    if language.is_none() {
                        ensure!(frame["stage"] == "start", "language must start once");
                        language = Some(crate::language::Turn::new(&input)?);
                        select_route(crate::foreground::Route::Learning)?;
                        selected = true;
                    }
                    let turn = language.as_mut().unwrap();
                    let mut reply = turn.advance(&frame)?;
                    while reply["command"] == "generate" {
                        let request = turn.request(&config.model)?;
                        let kind = request.kind.as_str();
                        let generated = match llm.generate(&request).await {
                            Ok(report) => {
                                tracing::info!(kind,elapsed_ms=report.elapsed_ms as u64,completion_tokens=?report.generated.completion_tokens,finish_reason=?report.generated.finish_reason);
                                let output = serde_json::to_value(report)?;
                                reports.push(output.clone());
                                output["generated"].clone()
                            }
                            Err(error) => {
                                reports.push(json!({"kind":kind,"error":error.to_string()}));
                                json!({"text":"","finish_reason":"error"})
                            }
                        };
                        reply = turn.generated(generated)?;
                    }
                    // 解釈・回答の検査がRust内で完了しても、宛先の確認を省略しない。
                    if reply["command"] == "done"
                        && reply["status"] == "host_chat"
                        && hold_handoff(&reply)?
                    {
                        turn.outcome["status"] = "awaiting_address".into();
                        reply = turn.outcome.clone();
                    }
                    reply
                }
                Some("knowledge") => {
                    ensure!(
                        address_reply.is_none() && language.is_none(),
                        "unexpected knowledge operation"
                    );
                    ensure!(
                        combat_kind.is_none()
                            && !light_plan
                            && !routing_only
                            && input["workshop_fallback"] != true
                            && input["workshop"].is_null()
                            && plans == 0
                            && leaves == 0
                            && knowledge.is_none(),
                        "knowledge must be the only ordinary reply operation"
                    );
                    ensure!(
                        frame["request_text"].as_str().is_some()
                            && frame["request_text"] == input["text"],
                        "knowledge request mismatch"
                    );
                    let plan = crate::knowledge::render(&frame["lookup"]);
                    select_route(crate::foreground::Route::Learning)?;
                    selected = true;
                    tracing::info!(
                        event = "knowledge_reply",
                        lookup_status = plan.lookup_status,
                        references = plan.references.len()
                    );
                    let response = serde_json::to_value(&plan)?;
                    knowledge = Some(plan);
                    response
                }
                Some("plan") => {
                    ensure!(
                        language
                            .as_ref()
                            .is_none_or(|t| t.finished() && t.outcome["status"] == "host_chat"),
                        "language cannot invoke ordinary planner before handoff"
                    );
                    ensure!(
                        combat_kind.is_none()
                            && !light_plan
                            && !routing_only
                            && knowledge.is_none(),
                        "reaction cannot invoke chat planner"
                    );
                    ensure!(address_reply.is_none(), "fixed address reply cannot plan");
                    plans += 1;
                    ensure!(plans == 1, "planner step limit exceeded");
                    let request: PreparedPlan = serde_json::from_value(frame["input"].take())?;
                    ensure!(
                        request.model == config.model && !request.enable_thinking,
                        "unexpected planner model"
                    );
                    if !selected {
                        select_route(crate::foreground::Route::Casual)?;
                        selected = true;
                    }
                    let report = planner::run(llm, &request).await?;
                    for attempt in &report.attempts {
                        tracing::info!(kind="player_chat_plan", elapsed_ms=attempt.elapsed_ms as u64, completion_tokens=?attempt.generated.completion_tokens, finish_reason=?attempt.generated.finish_reason);
                    }
                    let output = serde_json::to_value(report)?;
                    reports.push(output.clone());
                    output
                }
                Some("generate") => {
                    ensure!(
                        language
                            .as_ref()
                            .is_none_or(|t| t.finished() && t.outcome["status"] == "host_chat"),
                        "language cannot invoke ordinary leaf before handoff"
                    );
                    ensure!(
                        !routing_only && knowledge.is_none(),
                        "input routing and knowledge cannot generate"
                    );
                    ensure!(
                        address_reply.is_none(),
                        "fixed address reply cannot generate"
                    );
                    leaves += 1;
                    ensure!(leaves <= 2, "leaf retry limit exceeded");
                    let request: GenerationRequest = serde_json::from_value(frame["input"].take())?;
                    ensure!(
                        request.kind
                            == (if light_plan {
                                "light_source_comment_plan"
                            } else {
                                combat_kind.as_deref().unwrap_or("player_chat")
                            })
                            && request.model == config.model
                            && request.max_tokens
                                == (if light_plan { 160 } else { config.max_tokens })
                            && !request.enable_thinking,
                        "unexpected helper generation"
                    );
                    if !selected && combat_kind.is_none() && !light_plan && !routing_only {
                        select_route(crate::foreground::Route::Casual)?;
                        selected = true;
                    }
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
                    if let Some(text) = &address_reply {
                        ensure!(frame["text"] == *text, "helper changed address repair");
                        frame["language_status"] = "address_confirmation_requested".into();
                    }
                    if let Some(turn) = &language {
                        ensure!(turn.finished(), "unfinished language turn");
                        if turn.outcome["status"] != "host_chat" {
                            ensure!(
                                frame["text"] == turn.outcome["text"],
                                "helper changed language reply"
                            );
                        }
                        frame["language_status"] = turn.outcome["status"].clone();
                        frame["language_state"] = serde_json::to_value(&turn.state)?;
                        frame["language_references"] = turn.outcome["references"].clone();
                    }
                    if let Some(plan) = knowledge {
                        ensure!(
                            frame["text"] == plan.text,
                            "helper changed canonical knowledge text"
                        );
                        frame["knowledge_status"] = plan.lookup_status.into();
                        frame["references"] = serde_json::to_value(plan.references)?;
                    } else {
                        ensure!(
                            frame.get("knowledge_status").is_none()
                                && frame.get("references").is_none(),
                            "references require a canonical knowledge reply"
                        );
                    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn knowledge_classification_needs_no_python_reader_or_model() {
        let config = DialogueConfig {
            python: "/missing/knowledge-test-python".into(),
            helper: "/missing/knowledge-test-helper.py".into(),
            ..Default::default()
        };
        let llm = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_secs(1)).unwrap();
        let (cancel, mut rx) = watch::channel(false);
        for (text, expected) in [
            ("枕詞って何？", true),
            ("剣の耐久値を教えて", true),
            ("マイクラで関圧番って何？", true),
            ("剣に持ち替えて", false),
            ("こんにちは", false),
            ("/say 枕詞って何？", false),
        ] {
            let result = render(
                &config,
                &llm,
                json!({"op":"assist_route","text":text}),
                &mut rx,
            )
            .await
            .unwrap();
            assert_eq!(result, json!({"knowledge_query":expected}));
        }
        assert!(
            render(&config, &llm, json!({"op":"assist_route"}), &mut rx)
                .await
                .is_err()
        );
        cancel.send(true).unwrap();
        assert!(
            render(
                &config,
                &llm,
                json!({"op":"assist_route","text":"枕詞って何？"}),
                &mut rx
            )
            .await
            .is_err()
        );
        let (owner, mut orphaned) = watch::channel(false);
        drop(owner);
        assert!(
            render(
                &config,
                &llm,
                json!({"op":"assist_route","text":"枕詞って何？"}),
                &mut orphaned
            )
            .await
            .is_err()
        );
    }
}
