//! BERTで受理済みの剣依頼を、現在のhotbar検査と型付き命令の配送へ渡す。
//! BERTへ変更済み。旧判定へ戻さない。
use super::{Data, Dialogue, observation_fresh};
use crate::{
    assist::{self, Input, Submission},
    combat::model::{Delivery, Speech},
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
            s.screenshots.enabled = capabilities
                .iter()
                .any(|c| c == crate::screenshot::CAPABILITY);
            s.visual.enabled = capabilities
                .iter()
                .any(|c| c == super::visual_runtime::CAPABILITY);
            s.visual.after_look_enabled = capabilities
                .iter()
                .any(|c| c == super::visual_runtime::AFTER_LOOK_CAPABILITY);
            s.body_interest_enabled = capabilities.iter().any(|c| c == "body.inspect_target.v3");
            s.body_requests.set_enabled(
                capabilities
                    .iter()
                    .any(|c| c == crate::body::requests::CAPABILITY),
            );
            if !s.body_interest_enabled {
                super::body_interest_runtime::cancel(s);
            }
        }
    }
    pub(super) fn cancel_assist(d: &mut Data, sid: &str) {
        if let Some(s) = d.sessions.get_mut(sid)
            && let Some(p) = s.assist_pending.take()
        {
            let _ = p.cancel.send(true);
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == p.turn) {
                row["playback_status"] = "cancelled".into();
                row["assist_status"] = "cancelled".into();
            }
            d.revision += 1;
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
        self.queue_reply(d, jobs, sid, Speech::new("assist_feedback", text), input);
    }

    /// 固定文・一回生成の返事を共通の配送へ載せる。履歴は実出力の完了後に確定する。
    pub(super) fn queue_reply(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        mut speech: Speech,
        input: Option<&str>,
    ) {
        let s = d.sessions.get_mut(sid).unwrap();
        if super::reply_queue::warning_blocks_input(s) {
            return;
        }
        speech.delivery = Delivery::PlayerReply;
        let source = input.and_then(|text| {
            ["voice", "text"].into_iter().find_map(|source| {
                s.request_classification
                    .get(s.input_generation, text, source)
                    .map(|c| c.source.clone())
            })
        });
        let binding = super::request_runtime::Binding::capture(s);
        s.pending_replies.push_back(super::reply_output::Pending {
            speech,
            input: input.map(str::to_owned),
            source,
            binding,
        });
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
        let s = d.sessions.get_mut(sid)?;
        // 共通受付の原文・入力元・世代に一致するBERT結果がない入力は操作にしない。
        let classification = super::request_runtime::classification(s, text, source)?.clone();
        if !observation_fresh(s) {
            return None;
        }
        let event = s.latest.as_ref()?;
        let has_speech = s.warning.is_some() || s.pending_warning.is_some();
        match s
            .assist
            .submit_classified(&classification, event, chrono::Utc::now(), has_speech)
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
        }
    }
}
