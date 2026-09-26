//! 共同編集の相談段階。Rustがモデルの上限、実検査、取消と状態反映を所有する。
use super::*;
use crate::{haiku_bridge::Helper, haiku_record::HaikuLine, types::GenerationRequest, workshop};
use anyhow::{Context, ensure};

impl Dialogue {
    pub(super) async fn render_workshop(
        &self,
        input: &Value,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Value> {
        let script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("scripts/workshop_helper.py");
        let mut helper = Helper::start(&self.config.python, &script)?;
        let body = self.workshop_body(input, &mut helper);
        let result = tokio::select! {
            _ = bridge::cancelled(cancel) => Err(anyhow::anyhow!("cancelled")),
            result = tokio::time::timeout(Duration::from_secs(95), body) =>
                result.unwrap_or_else(|_| Err(anyhow::anyhow!("workshop turn timed out"))),
        };
        let finished = helper.finish(result.is_err()).await;
        finished?;
        result
    }

    async fn workshop_body(&self, input: &Value, helper: &mut Helper) -> Result<Value> {
        let text = input["text"].as_str().context("workshop input text")?;
        let view = &input["workshop"];
        let lines: Vec<HaikuLine> = serde_json::from_value(view["emission"]["lines"].clone())?;
        let mut observation = Value::Null;
        let mut steps = Vec::new();
        let mut reports = Vec::new();
        let mut reason = "accepted".to_owned();
        let mut action = workshop::fixed_action(text).map(str::to_owned);
        let mut speech = String::new();
        if action.is_none() {
            // 初手 + 実検査後の一手だけ。契約再試行は各一手につき最大一回。
            for phase in ["decide", "after_inspection"] {
                let allowed = workshop::allowed_actions(phase == "after_inspection");
                let mut frame = json!({"workshop":view,"text":text,"phase":phase,
                    "observation":observation,"turn_steps":steps,"allowed_actions":allowed});
                let mut selected = None;
                for attempt in 0..2 {
                    frame["op"] = "prepare".into();
                    let prepared = helper.exchange(frame.clone()).await?;
                    let request = GenerationRequest {
                        schema_version: 1,
                        kind: "haiku_workshop_agent_step".into(),
                        model: self.config.model.clone(),
                        messages: serde_json::from_value(prepared["messages"].clone())?,
                        temperature: 0.25,
                        max_tokens: 420,
                        enable_thinking: false,
                    };
                    let report = match self.llm.generate(&request).await {
                        Ok(report) => report,
                        Err(error) => {
                            reports.push(json!({"kind":request.kind,"error":error.to_string()}));
                            reason = "generation_error".into();
                            break;
                        }
                    };
                    tracing::info!(kind="haiku_workshop_agent_step",phase,attempt,
                        elapsed_ms=report.elapsed_ms as u64,completion_tokens=?report.generated.completion_tokens,
                        finish_reason=?report.generated.finish_reason);
                    let truncated = matches!(
                        report.generated.finish_reason.as_deref(),
                        Some("length" | "max_tokens" | "MAX_TOKENS")
                    );
                    let payload = crate::planner::extract_object(&report.generated.text);
                    reports.push(serde_json::to_value(report)?);
                    let Some(payload) = payload.filter(|_| !truncated) else {
                        reason = if truncated {
                            "output_truncated"
                        } else {
                            "invalid_json"
                        }
                        .into();
                        break;
                    };
                    frame["op"] = "validate".into();
                    frame["payload"] = payload.clone();
                    let validation = helper.exchange(frame.clone()).await?;
                    reason = validation["reason"]
                        .as_str()
                        .unwrap_or("invalid_validation")
                        .into();
                    if validation["contract_errors"]
                        .as_array()
                        .is_some_and(|e| !e.is_empty())
                    {
                        frame["retry"] =
                            json!({"errors":validation["contract_errors"],"payload":payload});
                        continue;
                    }
                    if validation["step"].is_object() {
                        let step = &validation["step"];
                        let a = step["action"].as_str().context("missing workshop action")?;
                        ensure!(
                            allowed.contains(&a),
                            "workshop action outside migration scope"
                        );
                        // 状態変更は補助の検査後にも原文・信頼度をRustで確認する。
                        if a == "close_workshop" {
                            let evidence = step["evidence"].as_str().unwrap_or("");
                            ensure!(
                                step["confidence"].as_f64().is_some_and(|c| c >= 0.85)
                                    && step["purpose"] == "finish_workshop"
                                    && !evidence.is_empty()
                                    && text.contains(evidence),
                                "close not grounded in original input"
                            );
                        }
                        selected = Some(step.clone());
                    }
                    break;
                }
                let Some(step) = selected else {
                    break;
                };
                let a = step["action"].as_str().unwrap();
                if a == "inspect" {
                    let checks: Vec<String> = serde_json::from_value(step["checks"].clone())?;
                    observation = workshop::inspect(&lines, &checks);
                }
                steps.push(json!({"phase":phase,"action":a,"purpose":step["purpose"],
                    "outcome":if a=="inspect" {"inspected"} else {"selected"},
                    "validation_codes":observation.get("validation_codes").cloned().unwrap_or(json!([])),
                    "checks":step["checks"],"evidence":step["evidence"],"close_after_action":false,"close_evidence":""}));
                if a != "inspect" {
                    action = Some(a.into());
                    speech = step["speech"].as_str().unwrap_or("").into();
                    break;
                }
            }
        }
        let action = action.unwrap_or_else(|| "fallback".into());
        match action.as_str() {
            "close_workshop" => speech = "ほな、この句はここまでにしよか。".into(),
            "show_current" => {
                speech = view["emission"]["reading_text"]
                    .as_str()
                    .unwrap_or("")
                    .into()
            }
            "fallback" => {
                speech = workshop::fallback((!observation.is_null()).then_some(&observation))
            }
            "unrelated" => {}
            _ => {}
        }
        tracing::info!(event="workshop_step",workshop_id=view["workshop_id"].as_str().unwrap_or(""),%action,%reason);
        let spoken = if action == "unrelated" {
            String::new()
        } else {
            let response = helper.exchange(json!({"op":"reading","text":speech,"reading_engine":self.config.reading_engine})).await?;
            response["spoken_text"]
                .as_str()
                .context("workshop reading result")?
                .to_owned()
        };
        Ok(
            json!({"text":speech,"spoken_text":spoken,"workshop_id":view["workshop_id"],
            "workshop_action":action,"workshop_steps":steps,"workshop_reason":reason,"llm_reports":reports}),
        )
    }

    /// 現在の一句の読み取りsnapshotだけ。保存・句の正本はhelperへ委譲しない。
    pub(super) fn workshop_view(s: &mut Session, text: &str) -> Option<Value> {
        let w = s
            .haiku
            .workshop
            .as_mut()
            .filter(|w| w.is_open() && !w.combat_paused())?;
        w.record_activity(Instant::now());
        Some(
            json!({"workshop_id":w.hud_id,"emission":w.emission,"materials":w.materials,
            "dialogue":w.dialogue,"agent_steps":w.agent_steps,"text":text}),
        )
    }
}
