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

/// 同じ通常返答に二択だけを添える。別の意図分類・生成は呼ばない。
pub(super) fn prepare_player_approach(request: &mut crate::types::GenerationRequest) {
    if let Some(system) = request.messages.iter_mut().find(|m| m.role == crate::types::Role::System) {
        system.content = system.content.replacen(
            crate::companion_prompt::choice_contract(),
            crate::companion_prompt::look_player_contract(),
            1,
        );
    }
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ApproachChoice { action: ApproachAction }
#[derive(serde::Deserialize)]
#[serde(rename_all = "snake_case")]
enum ApproachAction { Recall, Stay }

impl Dialogue {
    /// 同じlook_player返答の選択を既存の呼び寄せへ接続する。
    /// 台詞を出す前に受付を確定し、到着は後続の既存ACKだけで確定する。
    pub(super) fn apply_player_approach(
        &self, sid: &str, turn: &str, epoch: u64, input: &super::bridge::Input,
        result: &mut Value, cancelled: bool,
    ) -> anyhow::Result<()> {
        let Some(look) = &input.player_approach else { return Ok(()); };
        let mut d = self.data.lock().unwrap();
        let row = d.rows.iter().find(|r| r["turn_id"] == turn);
        anyhow::ensure!(!cancelled && !d.stopped && d.sessions.get(sid).is_some_and(|s| {
            s.epoch == epoch && s.current_turn == turn && s.input_generation == look.input_generation
                && row.is_some_and(|r| r["session_id"] == sid && r["session_instance"] == s.instance_id
                    && r["epoch"] == epoch && r["player_approach_enabled"] == true
                    && r["body_request_command_id"] == look.command_id)
        }), "player_approach_input_changed");
        // 同じ結果を再適用しても、移動命令を発行し直さない。
        if !row.unwrap()["player_approach_status"].is_null() { return Ok(()); }
        let choice = serde_json::from_value::<ApproachChoice>(result["body_action"].clone()).ok();
        let s = d.sessions.get_mut(sid).unwrap();
        let mut command = Value::Null;
        let mut detail = String::new();
        let status = match choice.map(|v| v.action) {
            None => "not_selected",
            Some(ApproachAction::Stay) => "stay",
            Some(ApproachAction::Recall) => {
                let now = Instant::now();
                let context = s.body_observation.accepted(now).map(|sample| sample.context.clone());
                let issued = if active(s) && context.as_ref() == Some(&look.context) {
                    s.body_requests.issue(Mode::Recall, look.input_generation,
                        s.player_uuid.as_deref().unwrap_or(""),
                        s.latest.as_ref().and_then(|e| e.player.dimension.as_deref()).unwrap_or(""),
                        context, input["text"].as_str().unwrap_or(""), now, chrono::Utc::now())
                } else { Err(anyhow::anyhow!("body_request_observation_unavailable")) };
                match issued {
                    Ok(issued) => { command = issued; "queued" },
                    Err(error) => {
                        detail = error.to_string();
                        result["text"] = json!("今はそっちへ行けへんわ。");
                        result["spoken_text"] = result["text"].clone();
                        result["dialogue_action"] = json!("speak");
                        "rejected"
                    }
                }
            }
        };
        let row = d.rows.iter_mut().find(|r| r["turn_id"] == turn).unwrap();
        row["player_approach_status"] = json!(status);
        row["player_approach_command_id"] = command["command_id"].clone();
        row["player_approach_detail"] = json!(detail);
        result["player_approach_status"] = json!(status);
        result["player_approach_command_id"] = command["command_id"].clone();
        tracing::info!(event="player_approach_selected",session_id=sid,turn_id=turn,status,detail);
        d.revision += 1;
        Ok(())
    }

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
        let Some(row) = d.rows.iter().find(|r| {
            r["session_id"] == sid
                && r["session_instance"] == s.instance_id
                && r["body_request_command_id"] == report.command_id
        }) else {
            return true;
        };
        let player_approach = row["body_request_mode"] == "look_player";
        let capture_enabled = self.config.chat_image.enabled
            && s.visual.after_look_enabled && s.visual.enabled;
        if !self.config.llm_enabled || (!player_approach && !capture_enabled) {
            return false;
        }
        let turn = row["turn_id"].as_str().unwrap().to_owned();
        let source = row["source"].as_str().unwrap_or("typed").to_owned();
        let input_occurred_at = row["created_at"].clone();
        Self::cancel_chat(d, sid, "completed_body_look");
        let s = d.sessions.get_mut(sid).unwrap();
        let Some(event) = s.latest.clone() else {
            return false;
        };
        let turns = s.model_history();
        let history = crate::chat_materials::CompletedHistory {
            conversation_history: super::history::History::lines_for(&turns), conversation_turns: turns,
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
        let capture = capture_enabled.then(||
            s.visual.arm_after_look(sid, look.clone(), chrono::Utc::now())).flatten();
        if capture.is_none() && !player_approach { return false; }
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
        input.after_look = capture.clone();
        input.player_approach = player_approach.then(|| look.clone());
        input.request_classification =
            super::request_runtime::classification(s, &report.original, &source).cloned();
        s.history.push_input(&turn, &report.original, input_occurred_at);
        let (cancel, rx) = tokio::sync::watch::channel(false);
        s.cancel = Some(cancel);
        let binding = super::request_runtime::Binding::capture(s);
        let row = d.rows.iter_mut().find(|r| r["turn_id"] == turn).unwrap();
        row["classifier_binding"] = json!(binding);
        row["category"] = json!("speech");
        row["epoch"] = json!(epoch);
        row["playback_status"] = json!("generating");
        row["output_mode"] = json!("both");
        row["look_capture_id"] = json!(capture.as_ref().map(|v| &v.capture_id));
        row["player_approach_enabled"] = json!(player_approach);
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
            // 到着後に生成を始めると返事が遅れるため、受付中から到着済みの場面として台詞を準備する。
            // 生成は移動と並行して始める。到着ACKを待って生成する形へ戻さない。
            // 台詞の想定時点と実行状態は別。実到着の状態は従来どおりACKで確定する。
            let mut reply = Speech::new("body_recall", "来たで。");
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
    let reports=s.body_requests.observe(
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
    );
    // 重複ACKはbody_requestsがreportへ再昇格しないため、古い成功で周期を延長し続けない。
    if reports.iter().any(|report| report.completed_look.is_some()) {
        s.body_autonomy.explicit_look_completed(now);
    }
    reports
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
#[path = "player_approach_tests.rs"]
mod player_approach_tests;

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
