//! 共通BERT受付の身体依頼を、既存event配送と実行結果ACKへ接続する。
use super::{Data, Dialogue, Session, id, observation_fresh};
use crate::{
    body::requests::{Mode, Report},
    combat::model::{LeafRequest, Speech},
    events::GameEvent,
    request_classifier::Label,
};
use serde_json::{Value, json};
use std::{sync::Arc, time::Instant};
use tokio::task::JoinHandle;

// BERTへ変更済み。旧判定へ戻さない。語句・別LLM・OS AIで同じ依頼を再判定しない。
fn mode(label: Label) -> Option<Mode> {
    Some(match label {
        Label::DogidoRecall => Mode::Recall,
        Label::LookDirection => Mode::LookDirection,
        Label::LookTarget => Mode::LookTarget,
        Label::LookPlayer => Mode::LookPlayer,
        Label::LookRelativeFront => Mode::LookRelativeFront,
        Label::LookRelativeBack => Mode::LookRelativeBack,
        Label::LookRelativeLeft => Mode::LookRelativeLeft,
        Label::LookRelativeRight => Mode::LookRelativeRight,
        Label::LookRelativeFrontLeft => Mode::LookRelativeFrontLeft,
        Label::LookRelativeFrontRight => Mode::LookRelativeFrontRight,
        Label::LookRelativeBackLeft => Mode::LookRelativeBackLeft,
        Label::LookRelativeBackRight => Mode::LookRelativeBackRight,
        _ => return None,
    })
}

fn active(s: &Session) -> bool {
    !s.preview
        && !s.foreground.game_paused
        && observation_fresh(s)
        && s.latest
            .as_ref()
            .is_some_and(|e| e.player.health.is_none_or(|health| health > 0.0))
}

impl Dialogue {
    /// 向き変更の成功ACKから、同じ入力の通常返答へ一度だけ進める。
    /// BERTと操作受付には戻さず、元の行・原文・入力世代を引き継ぐ。画像待機と生成はrun_turnの取消枠で行う。
    pub(super) fn continue_look_reply(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        report: &Report,
    ) -> bool {
        let Some(look) = &report.completed_look else {
            return false;
        };
        let Some(s) = d.sessions.get(sid) else {
            return true;
        };
        if s.input_generation != look.input_generation || !active(s) {
            return true;
        }
        if !self.config.llm_enabled
            || !self.config.chat_image.enabled
            || !s.visual.after_look_enabled
            || !s.visual.enabled
        {
            return false;
        }
        let Some(row) = d.rows.iter().find(|r| {
            r["session_id"] == sid
                && r["session_instance"] == s.instance_id
                && r["body_request_command_id"] == report.command_id
        }) else {
            return true;
        };
        let turn = row["turn_id"].as_str().unwrap().to_owned();
        let source = row["source"].as_str().unwrap_or("typed").to_owned();
        Self::cancel_chat(d, sid, "completed_body_look");
        let s = d.sessions.get_mut(sid).unwrap();
        let Some(event) = s.latest.clone() else {
            return false;
        };
        let history = crate::chat_materials::CompletedHistory {
            conversation_history: s.history.lines(), conversation_turns: s.history.rows(),
            event_digest: s.history.situation_lines().iter().map(|n| format!("- {n}"))
                .chain(s.combat_digest.iter().map(|n| format!("- {n}")))
                .chain(std::iter::once(format!("- 今回のプレイヤーの依頼に従い、ドギドは指定された向きへの変更を完了した。実行結果: {}", report.value)))
                .collect::<Vec<_>>().join("\n"),
        };
        let Ok(native) =
            super::chat_context::capture(s, &event, &self.config.combat, history.clone(), None)
        else {
            return false;
        };
        let Some(capture) = s
            .visual
            .arm_after_look(sid, look.clone(), chrono::Utc::now())
        else {
            return false;
        };
        s.epoch += 1;
        let epoch = s.epoch;
        s.current_turn = turn.clone();
        s.status = crate::playback::Status::Generating;
        let mut input = super::bridge::Input::native(
            json!({"text":report.original,"source":source,
            "session_instance":s.instance_id,"operation_id":turn,"event":event,
            "history":history.conversation_turns,"conversation_history":history.conversation_history,
            "event_digest":history.event_digest}),
            event,
            native,
        );
        input.retained_history_count = s.history.retained_count();
        input.after_look = Some(capture.clone());
        input.request_classification =
            super::request_runtime::classification(s, &report.original, &source).cloned();
        s.history.push(&turn, "user", &report.original);
        let (cancel, rx) = tokio::sync::watch::channel(false);
        s.cancel = Some(cancel);
        let binding = super::request_runtime::Binding::capture(s);
        let row = d.rows.iter_mut().find(|r| r["turn_id"] == turn).unwrap();
        row["classifier_binding"] = json!(binding);
        row["look_context"] = json!(look.context);
        row["category"] = json!("speech");
        row["epoch"] = json!(epoch);
        row["playback_status"] = json!("generating");
        row["output_mode"] = json!("both");
        row["look_capture_id"] = json!(capture.capture_id);
        // 操作済みの入力は警告後に再受付しない。取消後の操作再実行を防ぐ。
        row["combat_resumable"] = json!(false);
        let owner = self.clone();
        let sid = sid.to_owned();
        jobs.push(tokio::spawn(async move {
            owner.run_turn(sid, turn, epoch, input, rx).await;
        }));
        true
    }

    pub(super) fn body_request_input(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        text: &str,
        source: &str,
    ) -> Option<Value> {
        let s = d.sessions.get_mut(sid)?;
        let classified = super::request_runtime::classification(s, text, source)?;
        let mode = mode(classified.prediction.label)?;
        let classification = json!(classified);
        let now = Instant::now();
        let context = s
            .body_observation
            .accepted(now)
            .map(|sample| sample.context.clone());
        let result = if active(s) {
            s.body_requests.issue(
                mode,
                s.input_generation,
                s.player_uuid.as_deref().unwrap_or(""),
                s.latest
                    .as_ref()
                    .and_then(|e| e.player.dimension.as_deref())
                    .unwrap_or(""),
                context,
                text,
                now,
                chrono::Utc::now(),
            )
        } else {
            Err(anyhow::anyhow!("body_request_observation_unavailable"))
        };
        let turn = id("body_request");
        let instance = s.instance_id.clone();
        let generation = s.input_generation;
        Self::cancel_chat(d, sid, "body_request");
        let (status, command, detail) = match result {
            Ok(command) => ("queued", command, String::new()),
            Err(error) => ("rejected", Value::Null, error.to_string()),
        };
        if d.rows.len() >= 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(
            json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":sid,
            "session_instance":instance,"category":"body_request","text":"","source":source,
            "player_input_text":text,"created_at":chrono::Utc::now(),"reference_ids":[],
            "output_mode":"text","playback_status":"quiet","request_classification":classification,
            "body_request_status":status,"body_request_command_id":command["command_id"],
            "body_request_detail":detail,"body_request_mode":mode,"input_generation":generation}),
        );
        if status == "queued" && mode == Mode::Recall {
            // 命令を受け付けた時点で呼びかけへの返事を始める。到着ACKは待たず、
            // 命令の配送は既存event経路で並行する。到着したという事実はここで作らない。
            let mut reply = Speech::new("body_recall", "今行くで。");
            reply.leaf = Some(LeafRequest {
                kind: "body_recall".into(),
                details: json!({"player_input":text}),
                temperature: 0.65,
            });
            self.queue_reply(d, jobs, sid, reply, Some(text));
        } else if status == "rejected" {
            self.queue_fixed_reply(
                d,
                jobs,
                sid,
                if mode == Mode::Recall {
                    "今はそっちへ行けへんわ。".into()
                } else {
                    "今はそっちを向けへんわ。".into()
                },
                Some(text),
            );
        }
        d.revision += 1;
        Some(
            json!({"accepted":true,"session_id":sid,"turn_id":turn,"reason":"body_request",
            "body_request_status":status,"command_id":command["command_id"],
            "body_request_generation":generation}),
        )
    }
}

/// 新しい入力・休止を反映した後で結果を照合する。重複eventでも一度だけ消費する。
pub(super) fn observe(s: &mut Session, event: &GameEvent) -> Vec<Report> {
    let now = Instant::now();
    let context = s
        .body_observation
        .accepted(now)
        .map(|sample| sample.context.clone());
    let active = active(s);
    s.body_requests.observe(
        &event.command_results,
        s.input_generation,
        s.player_uuid.as_deref(),
        s.latest
            .as_ref()
            .and_then(|e| e.player.dimension.as_deref()),
        context.as_ref(),
        active,
        now,
        chrono::Utc::now(),
    )
}

pub(super) fn commands(s: &mut Session) -> Vec<Value> {
    let now = Instant::now();
    let context = s
        .body_observation
        .accepted(now)
        .map(|sample| sample.context.clone());
    let active = active(s);
    s.body_requests.commands(
        s.input_generation,
        s.player_uuid.as_deref(),
        s.latest
            .as_ref()
            .and_then(|e| e.player.dimension.as_deref()),
        context.as_ref(),
        active,
        now,
        chrono::Utc::now(),
    )
}

#[cfg(test)]
#[path = "body_request_runtime_tests.rs"]
mod reply_tests;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn twelve_body_labels_map_directly_to_their_existing_body_modes() {
        let expected = [
            (Label::DogidoRecall, Mode::Recall),
            (Label::LookDirection, Mode::LookDirection),
            (Label::LookTarget, Mode::LookTarget),
            (Label::LookPlayer, Mode::LookPlayer),
            (Label::LookRelativeFront, Mode::LookRelativeFront),
            (Label::LookRelativeBack, Mode::LookRelativeBack),
            (Label::LookRelativeLeft, Mode::LookRelativeLeft),
            (Label::LookRelativeRight, Mode::LookRelativeRight),
            (Label::LookRelativeFrontLeft, Mode::LookRelativeFrontLeft),
            (Label::LookRelativeFrontRight, Mode::LookRelativeFrontRight),
            (Label::LookRelativeBackLeft, Mode::LookRelativeBackLeft),
            (Label::LookRelativeBackRight, Mode::LookRelativeBackRight),
        ];
        for (label, body_mode) in expected {
            assert_eq!(mode(label), Some(body_mode));
        }
        assert_eq!(
            crate::request_classifier::NORMAL_LABELS
                .into_iter()
                .filter(|label| mode(*label).is_some())
                .count(),
            12
        );
    }

    #[test]
    fn other_classifier_labels_never_start_body_actions() {
        for label in [
            Label::NotTarget,
            Label::EnemyInformation,
            Label::EquipSword,
            Label::RecallMount,
            Label::SenryuLineUpper,
            Label::SenryuLineMiddle,
            Label::SenryuLineLower,
            Label::SenryuChangeUpper,
            Label::SenryuChangeMiddle,
            Label::SenryuChangeLower,
            Label::SenryuEnd,
        ] {
            assert_eq!(mode(label), None, "{label:?}");
        }
    }
}
