//! 共同編集の相談段階。Rustがモデルの上限、実検査、取消と状態反映を所有する。
use super::*;
use crate::{
    haiku_bridge::Helper,
    haiku_record::HaikuLine,
    types::GenerationRequest,
    workshop,
    workshop_edit::{self, Pending},
    workshop_followup::{self, Stage},
};
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
        let corrections = self.reading_overlay().await?;
        helper
            .exchange(json!({"op":"reading_overlay", "rows":corrections}))
            .await?;
        let text = input["text"].as_str().context("workshop input text")?;
        // Reuse this helper's existing catalog/date interpretation; no extra
        // model call or process for a normal workshop question.
        let recall = helper
            .exchange(json!({"op":"memory_query","text":text,"now":chrono::Utc::now()}))
            .await?;
        if recall["query"].is_object() {
            return Ok(json!({"memory_query":recall["query"],"llm_reports":[]}));
        }
        let mut snapshot = input["workshop"].clone();
        let view = &input["workshop"];
        let current: Vec<HaikuLine> = serde_json::from_value(view["current_lines"].clone())?;
        let pending: Option<Pending> = serde_json::from_value(view["pending"].clone())?;
        let lines = pending.as_ref().map_or(current.as_slice(), |p| &p.lines);
        let mut proposed = None;
        let mut close_after = false;
        let mut observation = Value::Null;
        let mut steps = Vec::new();
        let mut feedback = Value::Null;
        let mut reports = Vec::new();
        let mut reason = "accepted".to_owned();
        let mut action = workshop::fixed_action(text).map(str::to_owned);
        let mut speech = String::new();
        let stage: Stage = serde_json::from_value(view["followup"].clone())?;
        if action.is_none() {
            let fixed = helper
                .exchange(json!({"op":"fixed_followup", "text":text,
                "stage":stage,"pending":pending.is_some()}))
                .await?;
            action = fixed["action"]
                .as_str()
                .filter(|a| stage.actions(pending.is_some()).contains(a))
                .map(str::to_owned);
            if let Some(a) = &action {
                reason = "fixed_followup".into();
                steps.push(json!({"phase":"decide","action":a,"outcome":"selected",
                    "evidence":text,"checks":[],"validation_codes":[],
                    "purpose":match a.as_str() {"acknowledge_meaning"=>"understand_meaning",
                        "confirm_close"|"decline_resume"=>"finish_workshop",_=>"continue_discussion"}}));
            }
        }
        if matches!(action.as_deref(), Some("close_workshop" | "decline_resume"))
            && pending.is_some()
        {
            action = Some("ask".into());
            speech = "未採用の案があるで。採用するか、元の句に戻すか教えてな。".into();
        }
        if action.is_none() && pending.is_some() {
            action = match text.trim().trim_end_matches(['。', '！', '!']) {
                "採用して" | "その案で" | "その案でいい" | "その案でお願い" => {
                    Some("accept_pending".into())
                }
                "却下して" | "元の句に戻して" | "その案は使わない" => {
                    Some("reject_pending".into())
                }
                _ => None,
            };
        }
        if action.is_none() {
            // 初手→必要時の実検査→修正検証後の返答。editorは同じturnで一度だけ。
            let mut phase = "decide";
            for _ in 0..3 {
                let mut allowed = workshop::allowed_actions(phase, snapshot["pending"].is_object());
                if phase == "decide" {
                    allowed.extend(stage.actions(pending.is_some()).iter().copied());
                }
                let mut frame = json!({"workshop":snapshot,"text":text,"phase":phase,
                    "observation":observation,"turn_steps":steps,"allowed_actions":allowed});
                let mut selected = None;
                for attempt in 0..2 {
                    frame["op"] = "prepare".into();
                    let prepared = helper.exchange(frame.clone()).await?;
                    let payload = if let Some(fixed) = prepared.get("fixed_payload") {
                        fixed.clone()
                    } else {
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
                                reports
                                    .push(json!({"kind":request.kind,"error":error.to_string()}));
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
                        payload
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
                        if workshop_followup::is_action(a)
                            && let Err(error) =
                                workshop_followup::validate(step, text, stage, pending.is_some())
                        {
                            reason = error.to_string();
                            break;
                        }
                        // 状態変更は補助の検査後にも原文・信頼度をRustで確認する。
                        if matches!(
                            a,
                            "close_workshop"
                                | "accept_pending"
                                | "reject_pending"
                                | "stage_player_edit"
                                | "propose_revision"
                        ) {
                            let required_purpose = match a {
                                "close_workshop" => "finish_workshop",
                                "accept_pending" => "adopt_pending",
                                "reject_pending" => "discard_pending",
                                _ => "improve_wording",
                            };
                            let evidence = step["evidence"].as_str().unwrap_or("");
                            ensure!(
                                step["confidence"].as_f64().is_some_and(|c| c >= 0.85)
                                    && step["purpose"] == required_purpose
                                    && !evidence.is_empty()
                                    && text.contains(evidence),
                                "mutation not grounded in original input"
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
                let candidate = json!({"action":a,"purpose":step["purpose"],"findings":step["analysis"]["findings"]});
                if (feedback.is_null() || a == "propose_revision")
                    && crate::haiku_memory::feedback_kind(&candidate).is_some()
                {
                    feedback = candidate;
                }
                if a == "inspect" {
                    let checks: Vec<String> = serde_json::from_value(step["checks"].clone())?;
                    observation = workshop::inspect(lines, &checks);
                    if pending.is_some() {
                        observation["verse_kind"] = "pending".into();
                    }
                }
                steps.push(json!({"phase":phase,"action":a,"purpose":step["purpose"],
                    "outcome":if a=="inspect" {"inspected"} else {"selected"},
                    "validation_codes":observation.get("validation_codes").cloned().unwrap_or(json!([])),
                    "checks":step["checks"],"evidence":step["evidence"],"close_after_action":step["close_after_action"],"close_evidence":step["close_evidence"]}));
                if a == "inspect" {
                    phase = "after_inspection";
                    continue;
                }
                if a == "propose_revision" {
                    ensure!(
                        pending.is_none() && proposed.is_none() && phase != "after_validation",
                        "editor_already_used"
                    );
                    let prepared=helper.exchange(json!({"op":"revision_input","workshop":view,
                        "findings":step["analysis"]["findings"],"max_tokens":self.config.haiku.structured_max_tokens,
                        "grounding_max_tokens":self.config.haiku.grounding_max_tokens})).await?;
                    let prepared: crate::haiku::revision::Input = serde_json::from_value(prepared)?;
                    let basis = prepared.basis.clone();
                    let mut backend = crate::haiku_bridge::LiveBackend {
                        helper,
                        chat: &self.haiku_routes.chat,
                        haiku: &self.haiku_routes.haiku,
                        requests: vec![],
                        reports: vec![],
                    };
                    let revision = crate::haiku::revision::generate(&mut backend, prepared).await?;
                    reports.extend(backend.reports);
                    observation = json!({"kind":"revision_validation","status":if revision.accepted {"proposed"}else{"rejected"},
                        "base_text":workshop_edit::reading(&current),"proposed_verse":if revision.accepted {Some(revision.lines.join("\n"))}else{None},
                        "target_line_indices":basis.target_indices,
                        "validation_codes":if revision.accepted {json!(["edit_contract_passed","grounding_passed","meter_passed"])}else{json!([revision.failure_reason])},
                        "retry_feedback":revision.feedback});
                    if revision.accepted {
                        let p = Pending::stage_generated(&current, revision, basis)?;
                        snapshot["pending"] = serde_json::to_value(&p)?;
                        proposed = Some(p);
                    }
                    steps.last_mut().unwrap()["outcome"] = observation["status"].clone();
                    steps.last_mut().unwrap()["validation_codes"] =
                        observation["validation_codes"].clone();
                    phase = "after_validation";
                    continue;
                }
                if a != "inspect" {
                    close_after = step["close_after_action"] == true;
                    if close_after {
                        ensure!(
                            step["close_evidence"]
                                .as_str()
                                .is_some_and(|e| !e.is_empty() && text.contains(e)),
                            "close evidence absent"
                        );
                    }
                    if a == "stage_player_edit" {
                        let proposal = &step["analysis"]["line_proposal"];
                        ensure!(
                            proposal["replacement_text"]
                                .as_str()
                                .is_some_and(|t| !t.is_empty() && text.contains(t)),
                            "replacement not in original input"
                        );
                        let validated = helper
                            .exchange(
                                json!({"op":"player_edit","workshop":view,"proposal":proposal}),
                            )
                            .await?;
                        if validated["text"].is_string() {
                            let new_lines = serde_json::from_value(validated["lines"].clone())?;
                            let target = validated["target_line_index"]
                                .as_u64()
                                .context("missing edit target")?
                                as usize;
                            match Pending::stage(&current, lines, new_lines, target) {
                                Ok(p) => proposed = Some(p),
                                Err(error) => {
                                    reason = "player_edit_rejected".into();
                                    steps.last_mut().unwrap()["validation_codes"] =
                                        json!([error.to_string()]);
                                }
                            }
                        } else {
                            reason = "player_edit_rejected".into();
                            steps.last_mut().unwrap()["validation_codes"] =
                                validated["failure_reasons"].clone();
                        }
                    }
                    action = Some(a.into());
                    speech = step["speech"].as_str().unwrap_or("").into();
                    if a == "decline_resume" && pending.is_some() {
                        action = Some("ask".into());
                        speech = "未採用の案があるで。採用するか、元の句に戻すか教えてな。".into();
                    }
                    break;
                }
            }
        }
        let action = action.unwrap_or_else(|| "fallback".into());
        if action == "close_workshop" && workshop::fixed_praise(text) {
            feedback = json!({"action":"praise","findings":[]});
        }
        if let Some(fixed_speech) = workshop_followup::speech(&action) {
            speech = fixed_speech.into();
        }
        match action.as_str() {
            "close_workshop" => {
                speech = if workshop::fixed_praise(text) {
                    "気にいってもらえてうれしいわ。"
                } else {
                    "ほな、この句はここまでにしよか。"
                }
                .into()
            }
            "show_current" => speech = workshop_edit::reading(lines),
            "stage_player_edit" => {
                speech = proposed.as_ref().map_or_else(
                    || "その一行はまだ使えんかったわ。行の指定と読み、音数を確認してな。".into(),
                    |p| workshop_edit::reading(&p.lines),
                )
            }
            "accept_pending" => {
                speech = if close_after {
                    "元の句と直し、覚えといたで。この句の話はここまでや。"
                } else {
                    "元の句と直し、覚えといたで。"
                }
                .into()
            }
            "reject_pending" => {
                speech = if close_after {
                    "おけ、案は使わず、この句の話はここまでや。"
                } else {
                    "おけ、元の句はそのままにしとくで。"
                }
                .into()
            }
            "fallback" => {
                speech = workshop::fallback((!observation.is_null()).then_some(&observation))
            }
            "unrelated" => {}
            _ => {}
        }
        if observation["kind"] == "revision_validation" {
            if let Some(p) = &proposed {
                if matches!(action.as_str(), "fallback" | "show_current") {
                    speech = "検査に通った未採用の案はこれや。".into();
                }
                speech = format!(
                    "{}\n{}\nよければ『その案で』って言ってな。",
                    speech,
                    workshop_edit::reading(&p.lines)
                );
            } else if matches!(action.as_str(), "fallback" | "show_current") {
                speech = format!(
                    "今回は検査に通る案を作れんかったわ。元の句はそのままやで。\n{}",
                    workshop_edit::reading(&current)
                );
            }
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
            "workshop_action":action,"workshop_version":view["version"],"workshop_proposed":proposed,"workshop_close_after":close_after,
            "workshop_followup":Stage::after_completed(&action, steps.last().and_then(|s|s["purpose"].as_str()).unwrap_or(""), pending.is_some() || proposed.is_some()),
            "workshop_steps":steps,"workshop_reason":reason,"workshop_feedback":feedback,"llm_reports":reports}),
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
            "entry_id":w.entry_id,
            "current_lines":w.current_lines,"pending":w.pending,"version":w.version,
            "provisional":w.provisional,"dialogue":w.dialogue,"agent_steps":w.agent_steps,"followup":w.followup,"text":text}),
        )
    }
}
