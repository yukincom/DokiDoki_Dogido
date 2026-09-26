//! 型付き持ち替え命令の配送。自然文の限定抽出はevent workerの外で行う。
use super::{Data, Dialogue, bridge, id, observation_fresh};
use crate::{
    assist::{self, Input, InputSource, PreparedIntent, Submission},
    combat::model::{Delivery, Speech},
    types::GenerationRequest,
};
use serde_json::{Value, json};
use std::sync::Arc;
use tokio::{sync::watch, task::JoinHandle};

pub(super) struct Pending {
    pub input: Input,
    pub turn: String,
    pub cancel: watch::Sender<bool>,
    pub generation: u64,
}

impl Dialogue {
    pub fn set_execution_capabilities(&self, sid: &str, capabilities: &[String]) {
        if let Some(s) = self.data.lock().unwrap().sessions.get_mut(sid) {
            s.assist = assist::AssistState::new(capabilities.iter().cloned());
        }
    }
    pub(super) fn cancel_assist(d: &mut Data, sid: &str) {
        if let Some(s) = d.sessions.get_mut(sid) {
            s.assist.invalidate_intent();
            if let Some(p) = s.assist_pending.take() {
                let _ = p.cancel.send(true);
                if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == p.turn) {
                    row["playback_status"] = "cancelled".into();
                    row["assist_status"] = "cancelled".into();
                }
                d.revision += 1;
            }
        }
    }
    pub(super) fn queue_fixed_reply(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        text: String,
        input: Option<&str>,
    ) {
        let s = d.sessions.get_mut(sid).unwrap();
        if s.warning.is_some() || s.pending_warning.is_some() {
            return;
        }
        let mut speech = Speech::new("assist_feedback", text);
        speech.delivery = Delivery::PlayerReply;
        s.pending_warning = Some(vec![speech]);
        s.pending_input = input.map(str::to_owned);
        self.start_pending(d, jobs, sid);
    }
    pub(super) fn assist_input(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        text: &str,
        source: &str,
    ) -> Option<Value> {
        let s = d.sessions.get_mut(sid).unwrap();
        let event = s.latest.as_ref()?;
        let input = Input {
            raw_text: text.to_owned(),
            source: if source == "voice" {
                InputSource::Voice
            } else {
                InputSource::Text
            },
            ..Input::default()
        };
        let has_speech = s.warning.is_some() || s.pending_warning.is_some();
        match s
            .assist
            .submit(input.clone(), event, chrono::Utc::now(), has_speech)
        {
            Submission::NotHandled => None,
            Submission::Handled(dispatch) => {
                Self::cancel_chat(d, sid, "assist_input");
                if let Some(feedback) = dispatch.feedback.clone() {
                    self.queue_fixed_reply(d, jobs, sid, feedback, Some(text));
                }
                tracing::info!(event="assist_dispatch", session_id=sid, dispatch=%json!(dispatch));
                Some(
                    json!({"accepted":true,"session_id":sid,"reason":"assist_input","assist":dispatch}),
                )
            }
            Submission::NeedsIntent(prepared) => {
                Self::cancel_chat(d, sid, "assist_intent");
                let turn = id("assist");
                let (cancel, rx) = watch::channel(false);
                let s = d.sessions.get_mut(sid).unwrap();
                s.assist_pending = Some(Pending {
                    input,
                    turn: turn.clone(),
                    cancel,
                    generation: s.input_generation,
                });
                if d.rows.len() == 200 {
                    d.rows.pop_front();
                }
                d.rows.push_back(
                    json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":sid,
                    "category":"assist","text":"","source":source,"player_input_text":text,
                    "created_at":chrono::Utc::now(),"reference_ids":[],"output_mode":"text",
                    "playback_status":"generating","assist_status":"extracting"}),
                );
                d.revision += 1;
                let this = self.clone();
                let session = sid.to_owned();
                let tid = turn.clone();
                jobs.push(tokio::spawn(async move {
                    this.run_assist(session, tid, prepared, rx).await;
                }));
                Some(
                    json!({"accepted":true,"session_id":sid,"reason":"assist_intent","turn_id":turn}),
                )
            }
        }
    }
    async fn run_assist(
        self: Arc<Self>,
        sid: String,
        turn: String,
        prepared: PreparedIntent,
        mut cancel: watch::Receiver<bool>,
    ) {
        let routing = bridge::render(
            &self.config,
            &self.llm,
            json!({"op":"assist_route","text":prepared.player_text}),
            &mut cancel,
        )
        .await;
        let generate = routing
            .as_ref()
            .is_ok_and(|r| r["knowledge_query"] == false);
        let mut request = GenerationRequest {
            schema_version: 1,
            kind: "assist_select_sword_intent".into(),
            model: self.config.model.clone(),
            messages: serde_json::from_value(json!(prepared.messages))
                .expect("closed assist messages"),
            temperature: prepared.temperature,
            max_tokens: prepared.max_tokens.into(),
            enable_thinking: false,
        };
        let mut payload = Value::Null;
        for attempt in 0..2 {
            if !generate {
                break;
            }
            let report = tokio::select! {
                _=bridge::cancelled(&mut cancel)=>return,
                result=self.llm.generate(&request)=>result,
            };
            let report = match report {
                Ok(report) => report,
                Err(error) => {
                    tracing::warn!(event="assist_intent_failed",session_id=sid,error=%error);
                    break;
                }
            };
            tracing::info!(event="assist_intent_result",session_id=sid,attempt,elapsed_ms=report.elapsed_ms as u64,
                finish_reason=?report.generated.finish_reason,completion_tokens=?report.generated.completion_tokens);
            let Some(value) = crate::planner::extract_object(&report.generated.text) else {
                break;
            };
            let errors = assist::intent::contract_errors(&value);
            if errors.is_empty() {
                payload = value;
                break;
            }
            if attempt == 0 {
                request.messages = serde_json::from_value(json!(assist::intent::repair_messages(
                    &prepared.player_text,
                    &value.to_string(),
                    &errors
                )))
                .expect("closed assist repair messages");
            }
        }
        let fallback = {
            let mut jobs = self.jobs.lock().unwrap();
            jobs.retain(|j| !j.is_finished());
            let mut d = self.data.lock().unwrap();
            if d.stopped || *cancel.borrow() {
                return;
            }
            let Some(s) = d.sessions.get_mut(&sid) else {
                return;
            };
            if !s.assist_pending.as_ref().is_some_and(|p| p.turn == turn) {
                return;
            }
            let p = s.assist_pending.take().unwrap();
            let submission = if observation_fresh(s) {
                let event = s.latest.as_ref().expect("observed assist snapshot");
                s.assist.complete_intent(
                    prepared,
                    &payload,
                    &p.input,
                    event,
                    chrono::Utc::now(),
                    s.warning.is_some() || s.pending_warning.is_some(),
                )
            } else {
                s.assist.invalidate_intent();
                Submission::NotHandled
            };
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["playback_status"] = "quiet".into();
                row["assist_status"] = if matches!(submission, Submission::Handled(_)) {
                    "handled"
                } else {
                    "not_handled"
                }
                .into();
                row["assist_result"] = json!(submission);
            }
            d.revision += 1;
            if let Submission::Handled(dispatch) = submission {
                tracing::info!(event="assist_dispatch",session_id=sid,dispatch=%json!(dispatch));
                if let Some(text) = dispatch.feedback {
                    self.queue_fixed_reply(&mut d, &mut jobs, &sid, text, Some(&p.input.raw_text));
                }
                None
            } else {
                Some((p.input, p.generation))
            }
        };
        if let Some((input, generation)) = fallback {
            self.submit_inner(
                Some(&sid),
                &input.raw_text,
                if input.source == InputSource::Voice {
                    "voice"
                } else {
                    "text"
                },
                true,
                Some((generation, None)),
                false,
            );
        }
    }
}
