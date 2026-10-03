//! 共同編集の相談段階。Rustがモデルの上限、実検査、取消と状態反映を所有する。
#[path = "../workshop_prompt/mod.rs"]
pub(super) mod prompt;
#[path = "../workshop_validation/mod.rs"]
mod validation;
use super::*;
use crate::{
    haiku_bridge::Helper,
    haiku_record::HaikuLine,
    types::GenerationRequest,
    workshop, workshop_candidate,
    workshop_edit::{self, Pending},
    workshop_followup::{self, Stage},
    workshop_projection,
};
use anyhow::{Context, ensure};

const CONVERSATION_PROTOCOL: &str = "現在の会話プロトコル（上の古い固定応答指示より優先）: 現在句・未採用案・直近対話・discussion_targetを読み、次のactionをあなたが選ぶ。編集、案の採用と保存、破棄、終了も、本人の意思を文脈から理解して選んでよい。コードが対象・元句一致・音数・保存結果を確定する。新しい対象を本人が指定するまではdiscussion_targetの行と語を保ち、話題が分かっているのに『どこを見たいか』を聞き直さない。意味・由来・連想は自由に話し、検査を必須にしない。記録された読み・音数・出典を確かめたいときはinspectを選ぶ。inspectとunrelated以外は、操作actionでもspeechに自然な返事を入れる（空にするという以前の指示は無効）。編集・採用はコードが実行に成功してからこの返事を再生する。まだ行っていない操作を別のactionのspeechで実行済みとは言わない。句本文はコードが付けるのでspeechに三行を再生成しない。acknowledge_meaningは納得への返事だけで、終了確認へ進めない。本人が話を続けている、訂正している、困っているだけなら、終了を勧めずその話に応じる。実行できない理由が返ってきたら同じ対象について説明・相談を続ける。";

impl Dialogue {
    pub(super) async fn render_workshop(
        &self,
        input: &Value,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Value> {
        let script = self.config.helper.with_file_name("workshop_helper.py");
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
        let mut editing = crate::workshop_editing::Engine::default();
        let text = input["text"].as_str().context("workshop input text")?;
        // 正本DBで答える一般知識を、句の相談・確認状態の消費より先に分ける。
        let knowledge = editing
            .run(
                helper,
                &json!({"op":"knowledge_route", "text":text,
            "interpreted_text":input["interpreted_text"].as_str().unwrap_or(text),
            "workshop":input["workshop"]}),
            )
            .await?;
        if knowledge["query"].is_object() {
            let prepared = crate::player_text::prepare(text);
            let query = crate::knowledge::query::from_normalized(&prepared.normalized_text)
                .context("workshop knowledge query missing from original input")?;
            ensure!(
                knowledge["query"] == serde_json::to_value(&query)?,
                "workshop knowledge query mismatch"
            );
            let lookup = crate::knowledge::provider::lookup_async(
                crate::knowledge::provider::Paths::from_helper(&self.config.helper)?,
                query,
            )
            .await?;
            let reply = crate::knowledge::render(&lookup);
            let reading = helper
                .exchange(json!({"op":"reading", "text":reply.text,
                "reading_engine":self.config.reading_engine}))
                .await?;
            return Ok(
                json!({"text":reply.text, "spoken_text":reading["spoken_text"],
                "knowledge_status":reply.lookup_status, "references":reply.references,
                "workshop_action":"knowledge", "workshop_id":input["workshop"]["workshop_id"],
                "workshop_version":input["workshop"]["version"], "llm_reports":[]}),
            );
        }
        // 一般知識の寄り道は確認・採否・時計を動かさない。本来の句入力だけ消費する。
        self.consume_workshop_input(input)?;
        if let Some(recall) = crate::recall_query::parse(text, &corrections, chrono::Utc::now()) {
            return Ok(json!({"memory_query":recall,"llm_reports":[]}));
        }
        let mut snapshot = input["workshop"].clone();
        let view = &input["workshop"];
        let current: Vec<HaikuLine> = serde_json::from_value(view["current_lines"].clone())?;
        let pending: Option<Pending> = serde_json::from_value(view["pending"].clone())?;
        let discussed = &view["conversation_candidate"];
        let lines = pending.as_ref().map_or(current.as_slice(), |p| &p.lines);
        let mut proposed = None;
        let mut discussion_target = crate::workshop_target::Target::from_view(view, lines);
        let explicit_discussion = editing
            .run(
                helper,
                &json!({"op":"explicit_discussion", "workshop":view,"text":text}),
            )
            .await?;
        let mut conversation_candidate: Option<workshop_candidate::Draft> = if pending.is_none() {
            serde_json::from_value(explicit_discussion["candidate"].clone()).ok()
        } else {
            None
        };
        snapshot["current_player_idea"] = serde_json::to_value(&conversation_candidate)?;
        let mut close_after = false;
        let mut observation = Value::Null;
        let mut steps = Vec::new();
        let mut feedback = Value::Null;
        let mut reports = Vec::new();
        let mut reason = "accepted".to_owned();
        let mut action: Option<String> = None;
        let mut speech = String::new();
        let stage: Stage = serde_json::from_value(view["followup"].clone())?;
        if action.is_none() {
            // 初手→必要時の実検査→修正検証後の返答。editorは同じturnで一度だけ。
            let mut phase = "decide";
            for _ in 0..3 {
                let mut allowed = workshop::allowed_actions(
                    phase,
                    snapshot["pending"].is_object(),
                    snapshot["conversation_candidate"].is_object(),
                );
                if explicit_discussion["candidate"].is_object() && pending.is_none() {
                    allowed.retain(|a| {
                        matches!(
                            *a,
                            "respond" | "ask" | "explain" | "inspect" | "show_current"
                        )
                    });
                }
                if phase == "decide" {
                    allowed.extend(stage.actions(pending.is_some()).iter().copied());
                }
                let mut frame = json!({"workshop":snapshot,"text":text,"phase":phase,
                    "interpreted_text":input["interpreted_text"].as_str().unwrap_or(text),
                    "observation":observation,"turn_steps":steps,"allowed_actions":allowed});
                let mut selected = None;
                for attempt in 0..2 {
                    let (mut prepared, details) =
                        prepare_consultation(helper, &mut editing, &mut frame).await?;
                    if let Some(assets) =
                        input.get("text_workshop_prompt").filter(|v| v.is_object())
                    {
                        let mut projection = json!({"details":details});
                        if let Some(fixed) = prepared.get("fixed_payload") {
                            projection["fixed_payload"] = fixed.clone();
                        }
                        prepared =
                            prompt::prepare_with_assets(&projection, frame.get("retry"), assets)?;
                    }
                    prepared["messages"]
                        .as_array_mut()
                        .context("workshop messages")?
                        .push(json!({
                        "role":"user", "content":CONVERSATION_PROTOCOL}));
                    prepared["messages"].as_array_mut().unwrap().push(json!({"role":"user",
                        "content":"差し替え案を相談するrespond/askでも、本人が今回言った置換語と対象をline_proposalへ入れてな。確認のあと『うん、変えて』のように案を省略した返事は、現在のconversation_candidateをstage_conversation_candidateで選んでな。行為のevidenceは今回の返事、置換語は保持された一案から使う。以前の発話を今回のevidenceへコピーしないでな。"}));
                    if input.get("text_workshop_prompt").is_some() {
                        prepared["messages"].as_array_mut().unwrap().push(json!({"role":"user",
                            "content":"ここはテキスト相談室。採用した変更はコードが句集へ保存する。保存や採用の成否を先回りして言わず、コードの実行結果に従ってな。"}));
                    }
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
                        self.record_text_workshop_request(input, &request, phase);
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
                            if attempt == 0 {
                                frame["retry"] = json!({"reason":reason,
                                    "instruction":"同じ会話の返答を指定JSONにまとめ直してな。必須6キーを入れ、speechを短めにして完成させてな。"});
                                continue;
                            }
                            break;
                        };
                        payload
                    };
                    frame["op"] = "validate".into();
                    frame["payload"] = payload.clone();
                    if phase == "decide"
                        && pending.is_none()
                        && let Some(candidate) = workshop_candidate::Candidate::from_model(
                            &payload,
                            &current,
                            view["version"].as_u64().unwrap_or(0),
                            text,
                            discussion_target.as_ref(),
                        )
                    {
                        // The question/confirmation may prevent immediate execution,
                        // but must not erase the player's concrete replacement idea.
                        conversation_candidate = Some(candidate.draft);
                        frame["workshop"]["current_player_idea"] =
                            serde_json::to_value(&conversation_candidate)?;
                    }
                    let validation = validation::validate(&frame, &details)?;
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
                        if frame["workshop"]["conversation_candidate"].is_object()
                            && validation["contract_errors"]
                                .as_array()
                                .unwrap()
                                .iter()
                                .any(|e| e.as_str().is_some_and(|e| e.starts_with("evidence:")))
                        {
                            frame["retry"]["instruction"] = "evidenceは今回の発話から抜いてな。直前の相談案を選ぶ返事ならstage_conversation_candidate。置換語を今回もう一度言わせる必要はないで。".into();
                        }
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
                            if attempt == 0 {
                                prepare_conversation_retry(
                                    &mut frame,
                                    &mut allowed,
                                    &reason,
                                    &payload,
                                );
                                continue;
                            }
                            break;
                        }
                        // 状態変更は補助の検査後にも原文・信頼度をRustで確認する。
                        if matches!(
                            a,
                            "close_workshop"
                                | "accept_pending"
                                | "reject_pending"
                                | "stage_player_edit"
                                | "stage_conversation_candidate"
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
                    } else if attempt == 0 {
                        if reason == "ungrounded_evidence"
                            && frame["workshop"]["conversation_candidate"].is_object()
                        {
                            frame["retry"] = json!({"reason":reason,"payload":payload,
                                "instruction":"evidenceは今回の発話から抜いてな。直前の相談案を今回の返事で選ぶならstage_conversation_candidate。前の置換語や根拠を今回の発話にある扱いにしないでな。"});
                            continue;
                        }
                        // A rejected action is not a missing topic. Use the existing
                        // second attempt to answer the player, without mutation rights.
                        prepare_conversation_retry(&mut frame, &mut allowed, &reason, &payload);
                        continue;
                    }
                    break;
                }
                let Some(step) = selected else {
                    break;
                };
                let a = step["action"].as_str().unwrap();
                if let Some(target) = crate::workshop_target::Target::from_reference(
                    &step["analysis"]["line_reference"],
                    text,
                    lines,
                ) && discussion_target
                    .as_ref()
                    .is_none_or(|t| t.line_index != target.line_index)
                {
                    discussion_target = Some(target);
                    snapshot["discussion_target"] = serde_json::to_value(&discussion_target)?;
                }
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
                if phase == "decide"
                    && pending.is_none()
                    && matches!(a, "respond" | "ask")
                    && step["purpose"] == "improve_wording"
                {
                    let validated = editing
                        .run(
                            helper,
                            &json!({"op":"discussion_candidate",
                        "workshop":view,"text":text,
                        "proposal":step["analysis"]["line_proposal"]}),
                        )
                        .await?;
                    if let Ok(candidate) = serde_json::from_value(validated["candidate"].clone()) {
                        conversation_candidate = Some(candidate);
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
                    let prepared = prepare_revision(
                        &json!({"op":"revision_input","workshop":view,
                        "findings":step["analysis"]["findings"],"max_tokens":self.config.haiku.structured_max_tokens,
                        "grounding_max_tokens":self.config.haiku.grounding_max_tokens}),
                    )?;
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
                    if matches!(a, "stage_player_edit" | "stage_conversation_candidate") {
                        let proposal = if a == "stage_conversation_candidate" {
                            &discussed["proposal"]
                        } else {
                            &step["analysis"]["line_proposal"]
                        };
                        if a == "stage_player_edit" {
                            ensure!(
                                proposal["replacement_text"]
                                    .as_str()
                                    .is_some_and(|t| !t.is_empty() && text.contains(t)),
                                "replacement not in original input"
                            );
                        }
                        let mut edit_view = snapshot.clone();
                        if a == "stage_conversation_candidate" {
                            // Selecting the validated candidate explicitly selects its
                            // own target, even after a question about another line.
                            edit_view["discussion_target"] = Value::Null;
                        }
                        let validated = editing
                            .run(
                                helper,
                                &json!({"op":"player_edit",
                            "workshop":edit_view,"text":if a=="stage_player_edit" {text} else {""},
                            "proposal":proposal}),
                            )
                            .await?;
                        if validated["text"].is_string() {
                            let new_lines = serde_json::from_value(validated["lines"].clone())?;
                            let target = validated["target_line_index"]
                                .as_u64()
                                .context("missing edit target")?
                                as usize;
                            match Pending::stage(&current, lines, new_lines, target) {
                                Ok(p) => {
                                    if a == "stage_conversation_candidate" {
                                        discussion_target = Some(crate::workshop_target::Target {
                                            line_index: target,
                                            line_id: lines[target].line_id.clone(),
                                            fragment: String::new(),
                                        });
                                    }
                                    proposed = Some(p);
                                }
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
                    if matches!(a, "stage_player_edit" | "stage_conversation_candidate")
                        && proposed.is_none()
                    {
                        observation = json!({"kind":"player_edit_validation","status":"rejected",
                            "validation_codes":steps.last().unwrap()["validation_codes"],
                            "canonical_unchanged":true,"discussion_target":view["discussion_target"]});
                        phase = "after_validation";
                        continue;
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
        if speech.is_empty()
            && let Some(fixed_speech) = workshop_followup::speech(&action)
        {
            speech = fixed_speech.into();
            if matches!(action.as_str(), "continue_workshop" | "resume_workshop")
                && let Some(target) = crate::workshop_target::Target::from_view(view, lines)
            {
                speech = format!("おけ、{}の話を続けよか。", target.label(lines));
            }
        }
        match action.as_str() {
            "close_workshop" if speech.is_empty() => {
                speech = if workshop::fixed_praise(text) {
                    "気にいってもらえてうれしいわ。"
                } else {
                    "ほな、この句はここまでにしよか。"
                }
                .into()
            }
            "show_current" => {
                let verse = workshop_edit::reading(lines);
                speech = if speech.is_empty() {
                    verse
                } else {
                    format!("{speech}\n{verse}")
                };
                if matches!(
                    text,
                    "直った？" | "直った?" | "修正できた？" | "修正できた?"
                ) {
                    speech = format!(
                        "{}\n{speech}",
                        if pending.is_some() {
                            "まだ採用前の案やで。"
                        } else {
                            "今の確定した句はこれやで。"
                        }
                    );
                }
            }
            "stage_player_edit" | "stage_conversation_candidate" => {
                if let Some(p) = &proposed {
                    let verse = workshop_edit::reading(&p.lines);
                    speech = if speech.is_empty() {
                        verse
                    } else {
                        format!("{speech}\n{verse}")
                    };
                } else if speech.is_empty() {
                    speech = workshop::fallback_for(None, view, lines);
                }
            }
            "accept_pending" if speech.is_empty() => {
                speech = if close_after {
                    "元の句と直し、覚えといたで。この句の話はここまでや。"
                } else {
                    "元の句と直し、覚えといたで。"
                }
                .into()
            }
            "reject_pending" if speech.is_empty() => {
                speech = if close_after {
                    "おけ、案は使わず、この句の話はここまでや。"
                } else {
                    "おけ、元の句はそのままにしとくで。"
                }
                .into()
            }
            "fallback" => {
                speech = workshop::fallback_for(
                    (!observation.is_null()).then_some(&observation),
                    view,
                    lines,
                )
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
            "workshop_conversation_candidate":conversation_candidate,
            "workshop_discussion_target":discussion_target,
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
        let candidate_is_current = w
            .conversation_candidate
            .as_ref()
            .is_some_and(|c| c.is_current(&w.current_lines, w.version, w.pending.is_some()));
        if !candidate_is_current {
            w.conversation_candidate = None;
        }
        let candidate = w.conversation_candidate.as_ref().map(|c| c.view());
        let lines = w
            .pending
            .as_ref()
            .map_or(w.current_lines.as_slice(), |p| &p.lines);
        crate::workshop_target::Target::observe(&mut w.discussion_target, text, lines);
        Some(
            json!({"workshop_id":w.hud_id,"emission":w.emission,"materials":w.materials,
            "entry_id":w.entry_id,
            "current_lines":w.current_lines,"pending":w.pending,"version":w.version,
            "conversation_candidate":candidate,
            "discussion_target":w.discussion_target,
            "provisional":w.provisional,"dialogue":w.dialogue,"agent_steps":w.agent_steps,"followup":w.followup,"text":text}),
        )
    }

    fn consume_workshop_input(&self, input: &Value) -> Result<()> {
        let mut d = self.data.lock().unwrap();
        ensure!(!d.stopped, "server stopped");
        let s = d
            .sessions
            .values_mut()
            .find(|s| s.current_turn == input["operation_id"])
            .context("superseded workshop input")?;
        ensure!(s.cancel.is_some(), "cancelled workshop input");
        let w = s.haiku.workshop.as_mut().context("missing workshop")?;
        ensure!(
            w.hud_id == input["workshop"]["workshop_id"]
                && w.version == input["workshop"]["version"],
            "changed workshop"
        );
        w.followup = Stage::Discussion;
        w.record_activity(Instant::now());
        Ok(())
    }
}

fn prepare_conversation_retry(
    frame: &mut Value,
    allowed: &mut Vec<&str>,
    reason: &str,
    payload: &Value,
) {
    allowed.retain(|a| {
        matches!(
            *a,
            "respond" | "explain" | "ask" | "inspect" | "compare" | "show_current"
        )
    });
    frame["allowed_actions"] = json!(allowed);
    frame["retry"] = json!({"reason":reason,"payload":payload,
        "instruction":"前の一手は実行していない。相談対象と直近の会話を引き継いで、今回の発言に返答してな。対象をもう一度聞き直さず、終了や採否には進まないでな。実際の検査が必要なら許可されたinspectを選んでな。"});
}

// Match the former helper request/response limits even when no IPC is needed.
const PROJECTION_FRAME_LIMIT: usize = 1_000_000;
fn projection_request(frame: &Value) -> Result<()> {
    ensure!(
        serde_json::to_vec(frame)?.len() < PROJECTION_FRAME_LIMIT,
        "workshop projection request too large"
    );
    Ok(())
}
fn projection_response(response: &Value) -> Result<()> {
    ensure!(
        crate::planner::python_json(response).len() < PROJECTION_FRAME_LIMIT,
        "workshop projection response too large"
    );
    Ok(())
}
fn prepare_revision(frame: &Value) -> Result<crate::haiku::revision::Input> {
    projection_request(frame)?;
    let prepared = workshop_projection::revision_input(frame)?;
    projection_response(&prepared)?;
    serde_json::from_value(prepared).context("workshop revision input")
}

// Details, saved sources, fixed edit extraction, prompts and validation are Rust
// owned. Only unresolved kanji requests neutral tokens from the existing child.
async fn prepare_consultation(
    _helper: &mut Helper,
    _editing: &mut crate::workshop_editing::Engine,
    frame: &mut Value,
) -> Result<(Value, Value)> {
    projection_request(frame)?;
    let details = workshop_projection::details_for(frame)?;
    let prepared = json!({"details":details});
    projection_response(&prepared)?;
    Ok((prompt::prepare(&prepared, frame.get("retry"))?, details))
}
