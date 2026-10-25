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
