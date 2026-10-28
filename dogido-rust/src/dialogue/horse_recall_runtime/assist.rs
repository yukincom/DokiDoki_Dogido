//! BERTで受理した呼び寄せを既存assistの一件枠で受け、一回の個体選択を進める。
//! BERTへ変更済み。旧判定へ戻さない。
//! 通常会話のepochや音声serialから独立する一方、新入力・停止による取消と入力世代は共有する。
use super::*;
use crate::assist::{Input, InputSource};
use std::sync::Arc;

impl Dialogue {
    /// 分類済みの呼び寄せだけを、戦闘・通常会話で共通の候補選択へ渡す。
    pub(in super::super) fn horse_assist_input(
        self: &Arc<Self>,
        d: &mut super::super::Data,
        jobs: &mut Vec<tokio::task::JoinHandle<()>>,
        sid: &str,
        text: &str,
        source: &str,
    ) -> Option<Value> {
        let s = d.sessions.get_mut(sid)?;
        let classification = super::super::request_runtime::classification(s, text, source)?;
        if classification.prediction.label != crate::request_classifier::Label::RecallMount {
            return None;
        }
        if !self.config.llm_enabled {
            return Some(json!({"accepted":false,"reason":"horse_selection_unavailable"}));
        }
        if super::super::workshop_focus::quiet(s) {
            return None;
        }
        let context = capture(s, text, Some(classification))?;
        let request = crate::chat_prompt::Input {
            schema_version: 1,
            kind: "player_chat".into(),
            model: self.config.model.clone(),
            details: json!({"dialogue_choice":true,"user_text":text,
                "conversation_history":s.model_history_lines()}),
            temperature: 0.65,
            max_tokens: self.config.max_tokens.max(192),
            enable_thinking: false,
        }
        .into_request()
        .ok()?;
        let turn = super::super::id("assist_horse");
        let (cancel, rx) = watch::channel(false);
        let instance = s.instance_id.clone();
        s.assist_pending = Some(super::super::assist_runtime::Pending {
            input: Input {
                raw_text: text.into(),
                source: if source == "voice" {
                    InputSource::Voice
                } else {
                    InputSource::Text
                },
            },
            turn: turn.clone(),
            cancel,
            generation: s.input_generation,
        });
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(
            json!({"utterance_id":super::super::id("utt"),"turn_id":turn,
            "session_id":sid,"session_instance":instance,"category":"assist","text":"",
            "source":source,"player_input_text":text,"created_at":chrono::Utc::now(),
            "reference_ids":[],"output_mode":"text","playback_status":"generating",
            "assist_status":"selecting","horse_recall_status":"selecting"}),
        );
        d.revision += 1;
        let this = self.clone();
        let session = sid.to_owned();
        let tid = turn.clone();
        jobs.push(tokio::spawn(async move {
            this.run_horse_assist(session, tid, instance, context, request, rx)
                .await;
        }));
        Some(
            json!({"accepted":true,"session_id":sid,"reason":"horse_assist_selection","turn_id":turn}),
        )
    }

    async fn run_horse_assist(
        self: Arc<Self>,
        sid: String,
        turn: String,
        instance: String,
        context: Request,
        request: GenerationRequest,
        mut cancel: watch::Receiver<bool>,
    ) {
        // 戦闘警告の音声枠を待たず、一回の個体選択と同じ命令queueを使う。
        let generated = render(request, &self.llm, &context, &mut cancel).await;
        {
            let mut jobs = self.jobs.lock().unwrap();
            jobs.retain(|j| !j.is_finished());
            let mut d = self.data.lock().unwrap();
            if d.stopped || *cancel.borrow() {
                return;
            }
            let input_occurred_at = d.rows.iter()
                .find(|r| r["session_id"] == sid && r["turn_id"] == turn)
                .map_or(Value::Null, |r| r["created_at"].clone());
            let Some(s) = d.sessions.get_mut(&sid) else {
                return;
            };
            if s.instance_id != instance
                || !s.assist_pending.as_ref().is_some_and(|p| {
                    p.turn == turn
                        && p.generation == s.input_generation
                        && p.input.raw_text == context.original
                })
            {
                return;
            }
            let pending = s.assist_pending.take().unwrap();
            s.horse_recall.checked_generation = Some(pending.generation);
            let mut result = generated.unwrap_or_else(rejected);
            if let Some(choice) = result.get("horse_choice") {
                let dispatch = (|| -> Result<_> {
                    let choice = serde_json::from_value(choice.clone())?;
                    enqueue(
                        s,
                        &turn,
                        context
                            .prepared
                            .as_ref()
                            .context("horse_prepared_missing")?,
                        &choice,
                        input_occurred_at,
                    )
                })();
                match dispatch {
                    Ok(command) => {
                        result["horse_recall_status"] = "issued".into();
                        result["horse_command_id"] = command.command_id.clone().into();
                        result["horse_command"] = json!(command);
                        tracing::info!(event="horse_recall_dispatched",session_id=sid,turn_id=turn,command=%json!(command));
                    }
                    Err(error) => result = rejected(error),
                }
            }
            let text = result["text"].as_str().unwrap_or("").to_owned();
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["playback_status"] = "quiet".into();
                row["assist_status"] = "handled".into();
                for key in [
                    "horse_recall_status",
                    "horse_recall_reason",
                    "horse_command_id",
                    "horse_command",
                    "llm_reports",
                ] {
                    if let Some(value) = result.get(key) {
                        row[key] = value.clone();
                    }
                }
            }
            d.revision += 1;
            if !text.is_empty() {
                self.queue_fixed_reply(
                    &mut d,
                    &mut jobs,
                    &sid,
                    text,
                    Some(&pending.input.raw_text),
                );
            }
        }
    }
}
