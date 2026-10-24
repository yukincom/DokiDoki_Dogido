//! 共同編集の相談段階。Rustがモデルの上限、実検査、取消と状態反映を所有する。
#[path = "../workshop_prompt/mod.rs"]
pub(super) mod prompt;
use super::*;
use crate::workshop_validation as validation;
use crate::{
    haiku_record::HaikuLine,
    python_worker::Helper,
    types::GenerationRequest,
    workshop,
    workshop::tools,
    workshop_candidate,
    workshop_edit::{self, Pending},
    workshop_editing::{Output, PlayerEditResult},
    workshop_followup::{self, Stage},
    workshop_projection,
};
use anyhow::{Context, ensure};

const CONVERSATION_PROTOCOL: &str = "現在の会話プロトコル: 現在句・未採用案・直近対話・discussion_targetを読み、次の返答またはfunction callをあなたが選ぶ。実行方法と可能な処理は、今回提示されたtoolsと本文JSONのaction一覧に従う。今回許可されている一手だけを選ぶ。対象行・具体変更・終了はBERTの分類結果をコードが渡すので再判定しない。終了を自分で選んだりclose_after_actionを追加しない。未採用案があり採否が不明な終了希望にはaskで採用か破棄かを確認する。採否の意思が明確ならaccept_pendingまたはreject_pendingにその意思の原文根拠を付ける。コードは出力を受け取り、既知情報を補完して対象へ適用し、元句・版・保存結果を管理する。新しい対象を本人が指定するまではdiscussion_targetの行と語を保ち、話題が分かっているのに『どこを見たいか』を聞き直さない。意味・由来・連想は自由に話す。本人の指定語はそのまま編集へ渡し、句の品質や表現の形式を実行の前提にしない。本人から読み方や保存材料を尋ねられて手元の情報で答えられないときだけ、inspect_verseで参照できる。function callでは本文の返事を付けず、propose_revisionのspeechも空文字列にして結果を待つ。ほかの本文JSONのactionでは、unrelatedを除いてspeechに自然な返事を入れる。採用の返事はコードが実行に成功してから再生する。まだ行っていない操作を別のactionのspeechで実行済みとは言わない。句本文はコードが付けるのでspeechに三行を再生成しない。acknowledge_meaningは納得への返事だけで、終了確認へ進めない。本人が話を続けている、訂正している、困っているだけなら、終了を勧めずその話に応じる。";

/// 行3種・具体変更3種・終了はBERTへ変更済み。旧判定へ戻さない。
/// 共通受付の一回の分類を使い、行の相談と具体的な置換を混同しない。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ClassifiedWorkshop {
    Line(usize),
    Change(usize),
    End,
}

impl ClassifiedWorkshop {
    fn from_input(input: &bridge::Input) -> Option<Self> {
        use crate::request_classifier::Label;
        let classification = input.request_classification.as_ref()?;
        if !classification.matches(input["text"].as_str()?, input["source"].as_str()?) {
            return None;
        }
        match classification.prediction.label {
            Label::SenryuLineUpper => Some(Self::Line(0)),
            Label::SenryuLineMiddle => Some(Self::Line(1)),
            Label::SenryuLineLower => Some(Self::Line(2)),
            Label::SenryuChangeUpper => Some(Self::Change(0)),
            Label::SenryuChangeMiddle => Some(Self::Change(1)),
            Label::SenryuChangeLower => Some(Self::Change(2)),
            Label::SenryuEnd => Some(Self::End),
            // not_targetには文脈依存の採否や意味の質問も含まれる。
            _ => None,
        }
    }

    fn line(self) -> Option<usize> {
        match self {
            Self::Line(index) | Self::Change(index) => Some(index),
            Self::End => None,
        }
    }

    fn instruction(self, lines: &[HaikuLine]) -> String {
        match self {
            Self::Line(index) => format!(
                "今回の対象は分類済みの{}や。対象行を再判定せず、この行について自然に返答してな。行の指定だけを修正依頼とは扱わへん。意味の質問、感想、新案の依頼は今回の相談内容に沿って返す。新案を求められた場合だけpropose_revisionを選ぶ。", lines[index].line_id),
            Self::Change(index) => format!(
                "今回の分類は{}の具体的な語句変更や。行と変更意思を再判定せず、stage_player_editには今回原文の旧語・新語・根拠だけを抜いてな。行IDと元句はコードが補完する。語句の範囲が決められへんときだけaskで不足を尋ねてな。", lines[index].line_id),
            Self::End => "今回の相談終了は分類済みや。終了意思は再判定せんでええ。未採用案について、今回の発話で採用か破棄が指定されていればaccept_pendingかreject_pendingを選び、その意思の根拠を原文から抜いてな。採否が不明ならaskで採用か破棄かだけ確認する。終了だけで案を採用・破棄せんといてな。".into(),
        }
    }
}

/// 本文生成へ渡す一手。BERT対応範囲の旧分類actionは公開しない。
fn allowed_workshop_actions(
    phase: &str,
    pending: bool,
    candidate: bool,
    stage: Stage,
    classified: Option<ClassifiedWorkshop>,
    has_target: bool,
) -> Vec<&'static str> {
    let mut allowed = workshop::allowed_actions(phase, pending, candidate);
    if phase == "decide" {
        allowed.extend(stage.actions(pending).iter().copied());
    }
    allowed.retain(|action| match *action {
        "close_workshop" => false,
        "stage_player_edit" => matches!(classified, Some(ClassifiedWorkshop::Change(_))),
        "propose_revision" => has_target,
        _ => true,
    });
    match classified {
        Some(ClassifiedWorkshop::Change(_)) => {
            allowed.retain(|a| matches!(*a, "stage_player_edit" | "ask"))
        }
        Some(ClassifiedWorkshop::End) => {
            allowed.retain(|a| matches!(*a, "accept_pending" | "reject_pending" | "ask"))
        }
        _ => {}
    }
    allowed
}

impl Dialogue {
    /// 現在句のsnapshotから、返答と検証済みの行為候補を作る。
    /// 句の適用・保存はworkshop_edits、終了や配送後の対話状態はDialogue::updateが担当する。
    /// 取消・時間切れでも、ここで起動した辞書補助プロセスの終了処理を待ってから戻る。
    pub(super) async fn render_workshop(
        &self,
        input: &bridge::Input,
        cancel: &mut watch::Receiver<bool>,
        permit: &mut Option<tokio::sync::SemaphorePermit<'_>>,
    ) -> Result<Value> {
        let script = self.config.helper.with_file_name("tts_tokens_worker.py");
        let mut helper = Helper::start(&self.config.python, &script)?;
        let reply = super::workshop_story::Reply::default();
        let mut result = {
            let body = tokio::time::timeout(
                Duration::from_secs(95),
                self.workshop_body(input, &mut helper, &reply),
            );
            tokio::pin!(body);
            tokio::select! {
                result = &mut body => result.unwrap_or_else(|_| Err(anyhow::anyhow!("workshop turn timed out"))),
                _ = bridge::cancelled(cancel) => {
                    if self.workshop_combat_priority(input) {
                        // 音声・生成に共通の直列枠をすぐ戦闘へ譲る。元の会話は
                        // 送信済みの生成だけ受け取り、句操作・追加推論・音声配送は行わない。
                        drop(permit.take());
                        reply.interrupted.store(true, std::sync::atomic::Ordering::Relaxed);
                        if !reply.waiting_for_model.load(std::sync::atomic::Ordering::Relaxed) {
                            Ok(reply.result(input))
                        } else {
                        loop {
                            tokio::select! {
                                result = &mut body => break result.unwrap_or_else(|_| Err(anyhow::anyhow!("workshop turn timed out"))),
                                _ = tokio::time::sleep(Duration::from_millis(100)) => {
                                    if !self.workshop_input_session_exists(input) {
                                        break Err(anyhow::anyhow!("session_closed"));
                                    }
                                }
                            }
                        }
                        }
                    } else if reply.has_received_reply() {
                        Ok(reply.superseded_result(input))
                    } else {
                        Err(anyhow::anyhow!("cancelled"))
                    }
                },
            }
        };
        if reply.interrupted() && result.is_err() {
            result = Ok(reply.result(input));
        }
        if let Err(error) = helper.finish(result.is_err() || *cancel.borrow()).await {
            if reply.interrupted() {
                tracing::warn!(event="unspoken_workshop_helper_failed", %error);
                return Ok(reply.result(input));
            }
            return Err(error);
        }
        result
    }

    async fn workshop_body(
        &self,
        input: &bridge::Input,
        helper: &mut Helper,
        reply: &super::workshop_story::Reply,
    ) -> Result<Value> {
        let mut editing = crate::workshop_editing::Engine::default();
        let text = input["text"].as_str().context("workshop input text")?;
        let classified = ClassifiedWorkshop::from_input(input);
        // 分類済みの川柳操作を知識語句で横取りしない。未分類の一般知識だけ先に分ける。
        if classified.is_none() {
            let Output::KnowledgeRoute { query: knowledge } = editing
                .run(
                    helper,
                    &json!({"op":"knowledge_route", "text":text,
            "interpreted_text":input["interpreted_text"].as_str().unwrap_or(text),
            "workshop":input["workshop"]}),
                )
                .await?
            else {
                anyhow::bail!("unexpected knowledge route output");
            };
            if let Some(knowledge) = knowledge {
                let prepared = crate::player_text::prepare(text);
                let query = crate::knowledge::query::from_normalized(&prepared.normalized_text)
                    .context("workshop knowledge query missing from original input")?;
                ensure!(knowledge == query, "workshop knowledge query mismatch");
                let lookup = crate::knowledge::provider::lookup_async(
                    crate::knowledge::provider::Paths::from_helper(&self.config.helper)?,
                    query,
                )
                .await?;
                let reply = crate::knowledge::render(&lookup);
                let reading = helper
                    .exchange(json!({"op":"reading", "text":reply.text,
                "reading_engine":self.config.reading_engine,
                "reading_corrections":self.reading_overlay().await?}))
                    .await?;
                return Ok(
                    json!({"text":reply.text, "spoken_text":reading["spoken_text"],
                "knowledge_status":reply.lookup_status, "references":reply.references,
                "workshop_action":"knowledge", "workshop_id":input["workshop"]["workshop_id"],
                "workshop_version":input["workshop"]["version"], "llm_reports":[]}),
                );
            }
        }
        // 一般知識の寄り道は確認・採否・時計を動かさない。本来の句入力だけ消費する。
        self.consume_workshop_input(input)?;
        let mut snapshot = input["workshop"].clone();
        let view = &input["workshop"];
        let current: Vec<HaikuLine> = serde_json::from_value(view["current_lines"].clone())?;
        let pending: Option<Pending> = serde_json::from_value(view["pending"].clone())?;
        let discussed = &view["conversation_candidate"];
        let lines = pending.as_ref().map_or(current.as_slice(), |p| &p.lines);
        let mut proposed = None;
        let mut discussion_target = crate::workshop_target::Target::from_view(view, lines);
        let classified_line = classified.and_then(ClassifiedWorkshop::line);
        if let Some(index) = classified_line {
            ensure!(lines.len() == 3, "invalid classified workshop lines");
            discussion_target = Some(crate::workshop_target::Target {
                line_index: index,
                line_id: lines[index].line_id.clone(),
                fragment: String::new(),
            });
            snapshot["discussion_target"] = serde_json::to_value(&discussion_target)?;
        }
        // 今回の原文A/Bを既存抽出で確定できる具体変更は、生成せず既存編集へ渡す。
        let mut direct_step = if let Some(ClassifiedWorkshop::Change(index)) = classified {
            crate::workshop_editing::verbatim_player_edit(&snapshot, text, index)?.map(|frame| {
                json!({"action":"stage_player_edit", "purpose":"improve_wording",
                    "speech":"", "evidence":frame["payload"]["evidence"], "checks":[],
                    "analysis":{"line_proposal":frame["proposal"], "findings":[]},
                    "close_after_action":false})
            })
        } else {
            None
        };
        // 候補と未分類の操作は既存の会話へ渡す。分類済みの対象を再抽出しない。
        let mut conversation_candidate: Option<workshop_candidate::Draft> = None;
        snapshot["current_player_idea"] = serde_json::to_value(&conversation_candidate)?;
        let mut close_after = false;
        let mut observation = Value::Null;
        let mut tool_messages = Vec::new();
        let mut steps = Vec::new();
        let mut feedback = Value::Null;
        let mut reports = Vec::new();
        let mut praise = Value::Null;
        let mut reason = "accepted".to_owned();
        let mut action: Option<String> = None;
        let mut speech = String::new();
        let stage: Stage = serde_json::from_value(view["followup"].clone())?;
        if matches!(classified, Some(ClassifiedWorkshop::End)) && pending.is_none() {
            action = Some("close_workshop".into());
            steps.push(json!({"phase":"decide", "action":"close_workshop",
                "transport":"modernbert", "outcome":"selected", "evidence":text,
                "checks":[], "validation_codes":[]}));
        }
        if action.is_none() {
            // 初手→必要時の実検査の返答まで。編集後の結果はコードで返し、再計画しない。
            let mut phase = "decide";
            for _ in 0..2 {
                let mut allowed = allowed_workshop_actions(
                    phase,
                    snapshot["pending"].is_object(),
                    snapshot["conversation_candidate"].is_object(),
                    stage,
                    classified,
                    discussion_target.is_some(),
                );
                let attempts = if matches!(
                    classified,
                    Some(ClassifiedWorkshop::Change(_) | ClassifiedWorkshop::End)
                ) {
                    1
                } else {
                    2
                };
                let mut frame = json!({"workshop":snapshot,"text":text,"phase":phase,
                    "interpreted_text":input["interpreted_text"].as_str().unwrap_or(text),
                    "observation":observation,"turn_steps":steps,"allowed_actions":allowed});
                let mut selected = direct_step.take();
                let classified_edit = selected.is_some();
                let mut selected_tool = None;
                for attempt in 0..attempts {
                    if selected.is_some() {
                        break;
                    }
                    if reply.interrupted() || self.workshop_combat_priority(input) {
                        return Ok(reply.result(input));
                    }
                    let (mut prepared, details) =
                        prepare_consultation(helper, &mut editing, &mut frame).await?;
                    if let Some(assets) =
                        input.get("text_workshop_prompt").filter(|v| v.is_object())
                    {
                        let projection = json!({"details":details});
                        prepared =
                            prompt::prepare_with_assets(&projection, frame.get("retry"), assets)?;
                    }
                    prepared["messages"]
                        .as_array_mut()
                        .context("workshop messages")?
                        .push(json!({
                        "role":"user", "content":CONVERSATION_PROTOCOL}));
                    prepared["messages"].as_array_mut().unwrap().push(json!({"role":"user",
                        "content":"差し替え案への感想を相談するだけならrespond/askとline_proposalで返してな。指定語への変更依頼は、今回toolsにあるときだけstage_player_editのfunction callで渡す。確認のあと『うん、変えて』のように案を省略した返事は、stage_conversation_candidateが今回のaction一覧にある場合だけ、現在のconversation_candidateを選んでな。行為のevidenceは今回の返事、置換語は保持された一案から使う。以前の発話を今回のevidenceへコピーしないでな。"}));
                    if let Some(classified) = classified {
                        prepared["messages"]
                            .as_array_mut()
                            .unwrap()
                            .push(json!({"role":"user",
                            "content":classified.instruction(lines)}));
                    }
                    // 一turnのnative toolは一件まで。結果後の返答では追加呼出しを止める。
                    let allow_tool_call = tool_messages.is_empty();
                    let tool_definitions = tools::definitions_for_target(
                        if allow_tool_call {
                            &allowed
                        } else {
                            &["inspect", "stage_player_edit"]
                        },
                        classified_line,
                    );
                    if allow_tool_call && !tool_definitions.is_empty() {
                        let mut instruction = String::from(
                            "function callは今回toolsにあるもの一件だけを呼べる。toolsにない操作は選ばず、respond/explain/askの本文JSONで相談を続けてな。本文JSONのactionでtoolを代用せんといてな。",
                        );
                        if allowed.contains(&"inspect") {
                            instruction
                                .push_str("本人から尋ねられた読み方・保存材料の参照はinspect_verseや。編集の前提確認には使わへん。");
                        }
                        if allowed.contains(&"stage_player_edit") {
                            if classified_line.is_some() {
                                instruction.push_str("stage_player_editの対象行と元句はコードが補完する。「AをBに変えて」ならtarget_fragmentはA、replacement_textはBだけで、変更後の行は作らない。evidenceも今回の原文から抜く。");
                            } else {
                                instruction.push_str("本人が今回、新しい語を指定してその語への変更を頼んだらstage_player_editを呼ぶ。置換語を指定せず新しい案を考えてほしい場合だけpropose_revisionの本文JSONや。expected_textは変更前の対象行全体。「AをBに変えて」ならtarget_fragmentはA、replacement_textはBだけで、変更後の行は作らない。evidenceも今回の原文から抜く。以前の案を選ぶだけの返事はstage_conversation_candidateや。");
                            }
                        }
                        instruction.push_str("本文JSONは会話の返答と、本文JSONで使えるaction一覧の処理に使う。tool呼出し時は本文の返事を付けず結果を待つ。コードが対象へ差分を適用し、保存結果を返すで。");
                        let system = prepared["messages"][0]["content"].as_str().unwrap_or("");
                        prepared["messages"][0]["content"] =
                            format!("{system}\n{instruction}").into();
                    } else if !allow_tool_call {
                        let system = prepared["messages"][0]["content"].as_str().unwrap_or("");
                        prepared["messages"][0]["content"] = format!("{system}\nこのターンのtoolは実行済みや。追加のfunction callはせず、返された実検査結果を使い、今回の本文JSONのaction一覧から会話の返答を選んでな。").into();
                    }
                    if input.get("text_workshop_prompt").is_some() {
                        prepared["messages"].as_array_mut().unwrap().push(json!({"role":"user",
                            "content":"ここはテキスト相談室。採用した変更はコードが句集へ保存する。保存や採用の成否を先回りして言わず、コードの実行結果に従ってな。"}));
                    }
                    if !tool_definitions.is_empty() {
                        // 補助指示を本人の発話と取り違えないよう、今回入力を最後に置く。
                        prepared["messages"]
                            .as_array_mut()
                            .unwrap()
                            .push(json!({"role":"user",
                            "content":format!("今回のプレイヤー発話: {text}")}));
                    }
                    let request = GenerationRequest {
                        schema_version: 1,
                        kind: "haiku_workshop_agent_step".into(),
                        model: self.config.model.clone(),
                        messages: serde_json::from_value(prepared["messages"].clone())?,
                        temperature: 0.25,
                        max_tokens: 420,
                        enable_thinking: false,
                    };
                    if reply.interrupted() || self.workshop_combat_priority(input) {
                        return Ok(reply.result(input));
                    }
                    self.record_text_workshop_request(input, &request, phase);
                    reply
                        .waiting_for_model
                        .store(true, std::sync::atomic::Ordering::Relaxed);
                    let generated = if !tool_definitions.is_empty() {
                        self.llm
                            .generate_with_tools(
                                &request,
                                &tool_definitions,
                                &tool_messages,
                                allow_tool_call,
                            )
                            .await
                    } else {
                        self.llm
                            .generate(&request)
                            .await
                            .map(|report| (report, None))
                    };
                    reply
                        .waiting_for_model
                        .store(false, std::sync::atomic::Ordering::Relaxed);
                    let (report, tool_message) = match generated {
                        Ok(report) => report,
                        Err(error) => {
                            if reply.interrupted() || self.workshop_combat_priority(input) {
                                return Ok(reply.result(input));
                            }
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
                    let native = tool_message.is_some();
                    reports.push(serde_json::to_value(&report)?);
                    let tool_call = if truncated {
                        None
                    } else if let Some(message) = tool_message {
                        let decoded = if allow_tool_call {
                            tools::Call::decode_for_target(
                                message,
                                &frame["workshop"],
                                text,
                                classified_line,
                            )
                        } else {
                            Err(anyhow::anyhow!("additional_tool_call_not_allowed"))
                        };
                        match decoded {
                            Ok(call) => Some(call),
                            Err(error) => {
                                if reply.interrupted() || self.workshop_combat_priority(input) {
                                    return Ok(reply.result(input));
                                }
                                reason = format!("native_tool_rejected:{error}");
                                tracing::warn!(event="workshop_tool_rejected",phase,attempt,%reason);
                                if attempt == 0 {
                                    prepare_conversation_retry(
                                        &mut frame,
                                        &mut allowed,
                                        &reason,
                                        &Value::Null,
                                    );
                                    continue;
                                }
                                break;
                            }
                        }
                    } else {
                        None
                    };
                    let payload = if native {
                        tool_call.as_ref().map(tools::Call::payload)
                    } else {
                        crate::text_format::extract_object(&report.generated.text)
                    };
                    let Some(mut payload) = payload.filter(|_| !truncated) else {
                        if reply.interrupted() || self.workshop_combat_priority(input) {
                            return Ok(reply.result(input));
                        }
                        reason = if truncated {
                            "output_truncated"
                        } else {
                            "invalid_json"
                        }
                        .into();
                        if attempt == 0 {
                            frame["retry"] = json!({"reason":reason,
                                "instruction":"同じ会話の返答をJSONにまとめ直してな。普通の返答はactionとspeechだけでええで。"});
                            continue;
                        }
                        break;
                    };
                    // 本文JSONに戻っても、検査・差し替え操作へは進めない。
                    // 候補の保持や意味検査より前に遮断し、同じAPI形式へ一度だけ訂正を求める。
                    if let Err(error) = tools::validate_transport(&payload, tool_call.as_ref()) {
                        reason = error.to_string();
                        tracing::warn!(event="workshop_tool_rejected",phase,attempt,
                            action=%payload["action"],transport="content_json",%reason);
                        steps.push(json!({"phase":phase,"action":payload["action"],
                            "outcome":"rejected","transport":"content_json",
                            "validation_codes":[reason],"checks":[],"evidence":""}));
                        if attempt == 0 {
                            frame["retry"] = json!({"reason":reason,
                                "instruction":"その本文JSONの操作は実行してへんで。読み方・保存材料の参照はinspect_verse、今回の語句変更はstage_player_editのfunction callで渡してな。今回toolsに無い操作はできへんから、必要ならrespond/explain/askで相談を続けてな。"});
                            continue;
                        }
                        break;
                    }
                    // 行概念はBERTへ変更済み。旧判定へ戻さない。文脈継続も保持済み対象を使う。
                    if let Some(payload) = payload.as_object_mut() {
                        payload.remove("line_reference");
                    }
                    if let Some(index) = discussion_target.as_ref().map(|target| target.line_index)
                    {
                        if payload["action"] == "propose_revision" {
                            payload["findings"] = json!([{"line_index":index,
                                "fragment":lines[index].reading_text, "problem":"other",
                                "note":text, "confidence":1.0}]);
                        }
                    }
                    if matches!(classified, Some(ClassifiedWorkshop::End))
                        && matches!(
                            payload["action"].as_str(),
                            Some("accept_pending" | "reject_pending")
                        )
                    {
                        payload["close_after_action"] = true.into();
                        payload["close_evidence"] = text.into();
                    } else {
                        // pending採否は文脈から選べるが、終了を付けられるのはBERT Endだけ。
                        payload["close_after_action"] = false.into();
                        payload["close_evidence"] = "".into();
                    }
                    if let Some(verified) = validation::feedback(&payload["feedback"], text) {
                        praise = json!({"action":"praise","findings":[],"evidence":verified["evidence"]});
                    }
                    reply.remember(payload["speech"].as_str().unwrap_or(""), &praise);
                    if reply.interrupted() || self.workshop_combat_priority(input) {
                        return Ok(reply.result(input));
                    }
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
                        tracing::warn!(event="workshop_contract_rejected",phase,attempt,
                            action=%payload["action"],errors=%validation["contract_errors"]);
                        frame["retry"] =
                            json!({"errors":validation["contract_errors"],"payload":payload});
                        if tool_call.is_some() {
                            frame["retry"] = json!({"errors":validation["contract_errors"],
                                "instruction":"toolの引数が検査を通らへんかった。今回の原文と現在の句を確かめて、許可されたfunction callの引数を一度だけ直してな。本文JSONの編集操作へ戻したらあかんで。"});
                        } else if payload["action"] == "stage_conversation_candidate"
                            && validation["contract_errors"]
                                .as_array()
                                .unwrap()
                                .iter()
                                .any(|e| {
                                    matches!(
                                        e.as_str(),
                                        Some("confidence:missing" | "evidence:missing")
                                    )
                                })
                        {
                            frame["retry"]["instruction"] = "相談案を選ぶ操作は、最上位にconfidenceと今回の原文evidenceが必要や。今回の返事で案を選んだ根拠を付けてな。今回別の置換語を言うてるなら、古い案を選ばずstage_player_editのfunction callへ渡してな。根拠が決められへんときだけaskで確認してな。".into();
                        } else if frame["workshop"]["conversation_candidate"].is_object()
                            && validation["contract_errors"]
                                .as_array()
                                .unwrap()
                                .iter()
                                .any(|e| {
                                    matches!(
                                        e.as_str(),
                                        Some("evidence:not_exact" | "evidence:not_in_original")
                                    )
                                })
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
                                workshop_followup::validate(step, stage, pending.is_some())
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
                        // 受け取った操作の根拠が今回原文に属することだけを実行前に確認する。
                        if matches!(
                            a,
                            "close_workshop"
                                | "accept_pending"
                                | "reject_pending"
                                | "stage_player_edit"
                                | "stage_conversation_candidate"
                                | "propose_revision"
                        ) {
                            let evidence = step["evidence"].as_str().unwrap_or("");
                            ensure!(
                                !evidence.is_empty() && text.contains(evidence),
                                "mutation not grounded in original input"
                            );
                        }
                        let mut step = step.clone();
                        if let Some(call) = &tool_call {
                            call.retain_verified_target(&mut step);
                        }
                        if let Some(index) =
                            discussion_target.as_ref().map(|target| target.line_index)
                        {
                            if let Some(findings) = step["analysis"]["findings"].as_array_mut() {
                                for finding in findings {
                                    finding["line_index"] = index.into();
                                }
                            }
                        }
                        selected = Some(step);
                        selected_tool = tool_call;
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
                let tool_trace = selected_tool.as_ref().map(tools::Call::trace);
                let candidate = json!({"action":a,"purpose":step["purpose"],"findings":step["analysis"]["findings"]});
                if step["analysis"]["feedback_allowed"] != false
                    && (feedback.is_null() || a == "propose_revision")
                    && crate::haiku_memory::feedback_kind(&candidate).is_some()
                {
                    feedback = candidate;
                }
                if a == "inspect" {
                    let checks: Vec<String> = serde_json::from_value(step["checks"].clone())?;
                    if let Some(target) = step["inspect_text"].as_str() {
                        let reading = if checks.iter().any(|c| c == "reading") {
                            Some(helper.neutral_reading(target).await?)
                        } else {
                            None
                        };
                        observation = workshop::inspect_text(
                            target,
                            &checks,
                            reading.as_deref(),
                            step["inspect_line_index"].as_u64().map(|i| i as usize),
                        );
                    } else {
                        observation = workshop::inspect(lines, &checks);
                        if pending.is_some() {
                            observation["verse_kind"] = "pending".into();
                        }
                    }
                    if let Some(call) = selected_tool.take() {
                        tool_messages = call.result_messages(&observation);
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
                    if let Output::DiscussionCandidate {
                        candidate: Some(candidate),
                    } = validated
                    {
                        conversation_candidate = Some(candidate);
                    }
                }

                steps.push(json!({"phase":phase,"action":a,"purpose":step["purpose"],
                    "transport":if classified_edit {"modernbert_verbatim"} else if tool_trace.is_some() {"native_tool"} else {"content_json"},
                    "tool_call":tool_trace,
                    "outcome":if a=="inspect" {"inspected"} else {"selected"},
                    "validation_codes":observation.get("validation_codes").cloned().unwrap_or(json!([])),
                    "checks":step["checks"],"evidence":step["evidence"],"close_after_action":step["close_after_action"],"close_evidence":step["close_evidence"]}));
                if a == "inspect" {
                    phase = "after_inspection";
                    continue;
                }
                if a == "propose_revision" {
                    ensure!(
                        pending.is_none() && proposed.is_none(),
                        "editor_already_used"
                    );
                    let prepared = prepare_revision(
                        &json!({"op":"revision_input","workshop":view,
                        "findings":step["analysis"]["findings"],"max_tokens":self.config.haiku.structured_max_tokens}),
                    )?;
                    let basis = prepared.basis.clone();
                    let mut backend = crate::python_worker::LiveBackend {
                        helper,
                        chat: &self.haiku_routes.chat,
                        haiku: &self.haiku_routes.haiku,
                        requests: vec![],
                        reports: vec![],
                        capture: None,
                    };
                    let revision = crate::haiku::revision::generate(&mut backend, prepared).await?;
                    reports.extend(backend.reports);
                    observation = json!({"kind":"revision_validation","status":if revision.accepted {"proposed"}else{"rejected"},
                        "base_text":workshop_edit::surface(&current),"proposed_verse":if revision.accepted {Some(revision.lines.join("\n"))}else{None},
                        "target_line_indices":basis.target_indices,
                        "validation_codes":if revision.accepted {json!(["edit_contract_passed","source_ids_passed"])}else{json!([revision.failure_reason])},
                        "retry_feedback":revision.feedback});
                    if revision.accepted {
                        let p = Pending::stage_generated(&current, revision, basis)?;
                        snapshot["pending"] = serde_json::to_value(&p)?;
                        proposed = Some(p);
                    }
                    steps.last_mut().unwrap()["outcome"] = observation["status"].clone();
                    steps.last_mut().unwrap()["validation_codes"] =
                        observation["validation_codes"].clone();
                    action = Some("propose_revision".into());
                    speech = if proposed.is_some() {
                        String::new()
                    } else {
                        "今回は直せんかったわ。元の句はそのままやで。".into()
                    };
                    break;
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
                        let Output::PlayerEdit(validated) = editing
                            .run(
                                helper,
                                &json!({"op":"player_edit",
                            "workshop":edit_view,"text":if a=="stage_player_edit" {text} else {""},
                            "proposal":proposal}),
                            )
                            .await?
                        else {
                            anyhow::bail!("unexpected player edit output");
                        };
                        let failure_reasons = validated.failure_reasons().to_vec();
                        if let PlayerEditResult::Validated(result) = validated
                            && result.text.is_some()
                        {
                            let target = result.target_line_index.context("missing edit target")?;
                            match Pending::stage(&current, lines, result.lines, target) {
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
                            steps.last_mut().unwrap()["validation_codes"] = json!(failure_reasons);
                        }
                    }
                    if matches!(a, "stage_player_edit" | "stage_conversation_candidate")
                        && proposed.is_none()
                    {
                        observation = json!({"kind":"player_edit_validation","status":"rejected",
                            "validation_codes":steps.last().unwrap()["validation_codes"],
                            "canonical_unchanged":true,"discussion_target":view["discussion_target"]});
                        // 対象や元句が一致せず実行できなかった編集は、元句を保ち結果を返す。
                        // 失敗理由の言い換えや方針の聞き直しのためにLLMを呼ばない。
                        steps.last_mut().unwrap()["outcome"] = "rejected".into();
                        action = Some("respond".into());
                        speech = "その変更は反映できんかったわ。元の句はそのままやで。".into();
                        break;
                    }
                    if selected_tool
                        .as_ref()
                        .is_some_and(tools::Call::is_player_edit)
                        && proposed.is_some()
                    {
                        steps.last_mut().unwrap()["outcome"] = "validated".into();
                    }
                    action = Some(a.into());
                    speech = step["speech"].as_str().unwrap_or("").into();
                    break;
                }
            }
        }
        if action.is_none() {
            match classified {
                Some(ClassifiedWorkshop::End) => {
                    action = Some("ask".into());
                    speech = "未採用の案があるで。採用するか、元の句に戻すか教えてな。".into();
                }
                Some(ClassifiedWorkshop::Change(_)) => {
                    action = Some("respond".into());
                    speech = "その変更は反映できんかったわ。元の句はそのままやで。".into();
                }
                _ => {}
            }
        }
        let action = action.unwrap_or_else(|| "fallback".into());
        if speech.is_empty()
            && let Some(fixed_speech) = workshop_followup::speech(&action)
        {
            speech = fixed_speech.into();
        }
        match action.as_str() {
            // 未採用案を残した終了は保存処理を通さない。案を採用・保存したという
            // モデルの返事で結果を取り違えないよう、実際に行う終了だけを伝える。
            "close_workshop" if pending.is_some() => {
                speech = "元の句はそのままにして、案は採用せずにここで終わるで。".into()
            }
            "close_workshop" if speech.is_empty() => {
                speech = "ほな、この句はここまでにしよか。".into()
            }
            "show_current" => {
                let verse = workshop_edit::surface(lines);
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
                    let verse = workshop_edit::surface(&p.lines);
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
                if speech.is_empty() || matches!(action.as_str(), "fallback" | "show_current") {
                    speech = "こんな案はどうやろ。".into();
                }
                speech = format!(
                    "{}\n{}\nよければ『その案で』って言ってな。",
                    speech,
                    workshop_edit::surface(&p.lines)
                );
            } else if matches!(action.as_str(), "fallback" | "show_current") {
                speech = format!(
                    "今回は案を反映できんかったわ。元の句はそのままやで。\n{}",
                    workshop_edit::surface(&current)
                );
            }
        }
        tracing::info!(event="workshop_step",workshop_id=view["workshop_id"].as_str().unwrap_or(""),%action,%reason);
        if !praise.is_null() {
            feedback = praise;
        }
        reply.remember(&speech, &feedback);
        if reply.interrupted() || self.workshop_combat_priority(input) {
            return Ok(reply.result(input));
        }
        let spoken = if action == "unrelated" || speech.is_empty() {
            String::new()
        } else {
            let response = helper
                .exchange(
                    json!({"op":"reading","text":speech,"reading_engine":self.config.reading_engine,
                "reading_corrections":self.reading_overlay().await?}),
                )
                .await?;
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
        let shared_context = super::story::workshop_context(s);
        let w = s
            .haiku
            .workshop
            .as_mut()
            .filter(|w| w.is_open() && !w.combat_paused())?;
        // 通常は保存成功後のentry/revision ID、テキスト相談室は
        // 保存句への接続を根拠にする。単独試験用の仮IDは保存の証拠にしない。
        let current_verse_saved = if s.text_workshop {
            s.text_poem.is_some()
        } else {
            w.entry_id.is_some() || w.revision_id.is_some()
        };
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
        // 現在対象の失効だけを処理する。新しい対象はBERTの分類結果から受け取る。
        crate::workshop_target::Target::observe(&mut w.discussion_target, "", lines);
        Some(
            json!({"workshop_id":w.hud_id,"emission":w.emission,"materials":w.materials,
            "entry_id":w.entry_id,"current_verse_saved":current_verse_saved,
            "current_lines":w.current_lines,"pending":w.pending,"version":w.version,
            "conversation_candidate":candidate,
            "discussion_target":w.discussion_target,
            "dialogue":w.dialogue,"shared_context":shared_context,"agent_steps":w.agent_steps,"followup":w.followup,"text":text}),
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
            "respond" | "explain" | "ask" | "compare" | "show_current"
        )
    });
    frame["allowed_actions"] = json!(allowed);
    frame["retry"] = json!({"reason":reason,"payload":payload,
        "instruction":"前の一手は実行していない。相談対象と直近の会話を引き継いで、今回の発言に返答してな。対象をもう一度聞き直さず、終了や採否には進まないでな。今回は追加の確認toolへ回らず、返された実行結果の範囲で答えてな。"});
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
        crate::text_format::spaced_json(response).len() < PROJECTION_FRAME_LIMIT,
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

// Consultation preparation projects saved context and builds the model prompt.
// It does not select an action or request dictionary tokens from the helper.
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

#[cfg(test)]
mod removed_check_tests {
    use super::*;

    #[test]
    fn rejected_edit_cannot_detour_into_another_inspection() {
        let mut frame = json!({});
        let mut allowed = workshop::allowed_actions("decide", true, false);
        prepare_conversation_retry(
            &mut frame,
            &mut allowed,
            "native_tool_rejected:stale_tool_edit",
            &Value::Null,
        );
        assert!(!allowed.contains(&"inspect"));
        assert!(tools::definitions_for_target(&allowed, None).is_empty());
        assert!(allowed.contains(&"respond"));
        assert!(allowed.contains(&"show_current"));
        assert!(!CONVERSATION_PROTOCOL.contains("音数"));
        assert!(!frame.to_string().contains("inspect_verse"));
    }
}

#[cfg(test)]
mod bert_tests {
    use super::*;
    use crate::request_classifier::{Classification, LABEL_COUNT, Label, Prediction};

    fn input(label: Label) -> bridge::Input {
        let mut input: bridge::Input = json!({"text":"原文", "source":"typed"}).into();
        input.request_classification = Some(Classification {
            text: "原文".into(),
            source: "typed".into(),
            manifest_sha256: "fixture".into(),
            prediction: Prediction {
                label,
                probabilities: [0.0; LABEL_COUNT],
                token_count: 3,
                inference_ms: 1.0,
            },
        });
        input
    }

    #[test]
    fn classifier_targets_are_not_inferred_from_raw_keywords_or_confidence() {
        for (label, expected) in [
            (Label::SenryuLineUpper, ClassifiedWorkshop::Line(0)),
            (Label::SenryuLineMiddle, ClassifiedWorkshop::Line(1)),
            (Label::SenryuLineLower, ClassifiedWorkshop::Line(2)),
            (Label::SenryuChangeUpper, ClassifiedWorkshop::Change(0)),
            (Label::SenryuChangeMiddle, ClassifiedWorkshop::Change(1)),
            (Label::SenryuChangeLower, ClassifiedWorkshop::Change(2)),
            (Label::SenryuEnd, ClassifiedWorkshop::End),
        ] {
            let mut classified = input(label);
            assert_eq!(ClassifiedWorkshop::from_input(&classified), Some(expected));
            classified["source"] = "voice".into();
            assert_eq!(ClassifiedWorkshop::from_input(&classified), None);
        }
        let mut unknown = input(Label::NotTarget);
        unknown["text"] = "上五を変えて、終了".into();
        assert_eq!(ClassifiedWorkshop::from_input(&unknown), None);
    }

    #[test]
    fn contextual_conversation_cannot_fall_back_to_line_change_or_end_classification() {
        for stage in [Stage::Discussion, Stage::MeaningExplained] {
            let allowed = allowed_workshop_actions("decide", false, true, stage, None, false);
            for forbidden in [
                "close_workshop",
                "confirm_close",
                "decline_resume",
                "stage_player_edit",
                "propose_revision",
            ] {
                assert!(!allowed.contains(&forbidden), "{forbidden}");
            }
            assert!(allowed.contains(&"respond"));
            assert!(allowed.contains(&"stage_conversation_candidate"));
        }
        let line = allowed_workshop_actions(
            "decide",
            false,
            false,
            Stage::Discussion,
            Some(ClassifiedWorkshop::Line(1)),
            true,
        );
        assert!(line.contains(&"propose_revision") && line.contains(&"explain"));
        assert!(!line.contains(&"stage_player_edit"));
        let change = allowed_workshop_actions(
            "decide",
            false,
            false,
            Stage::Discussion,
            Some(ClassifiedWorkshop::Change(1)),
            true,
        );
        assert_eq!(change, vec!["ask", "stage_player_edit"]);
        let end = allowed_workshop_actions(
            "decide",
            true,
            false,
            Stage::Discussion,
            Some(ClassifiedWorkshop::End),
            false,
        );
        assert_eq!(end, vec!["ask", "accept_pending", "reject_pending"]);
    }
}
