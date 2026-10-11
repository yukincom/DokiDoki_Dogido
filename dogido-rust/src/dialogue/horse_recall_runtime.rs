//! 通常会話で呼び寄せ依頼を受けた後に候補を見せ、LLMの選択を現在のSessionへ照合する。
//! LLMは発話と対象選択だけを返す。命令発行は同じturn/epochの再検査後、実移動はFabricが担当する。
//! 要求の発行とMinecraftの実行結果を出来事として分け、未確認の成功を発話履歴へ入れない。
use super::{Dialogue, Session, bridge, fresh, warnings};
use crate::{
    assist::horse_recall::{
        contracts::{Choice, Prepared},
        intent::{self, Decision, Stage},
    },
    events::{AdapterCommandResultCommandType, AdapterCommandResultStatus},
    foreground::Route,
    llm::RigLlm,
    types::GenerationRequest,
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use tokio::sync::watch;

fn available(s: &Session) -> bool {
    !s.preview
        && fresh(s)
        && s.mode == crate::combat::model::Mode::Normal
        && !s.foreground.game_paused
        && !s.foreground.combat_active
        && matches!(s.foreground.route, Route::None | Route::Casual)
        && !s.haiku.foreground()
        && !s.web.state.active()
        && s.assist.supports_horse_recall()
        && s.latest
            .as_ref()
            .is_some_and(|e| warnings::interruption_reason(e).is_none())
        && s.audio_latest
            .as_ref()
            .is_none_or(|e| warnings::interruption_reason(e).is_none())
}

/// 生成直前の馬候補を固定する。名前や操作語による入口の絞込みは行わない。
pub(super) fn capture(s: &Session, raw: &str) -> Option<Prepared> {
    if !available(s) {
        return None;
    }
    let snapshot = s.latest.as_ref()?.owned_horses.as_ref()?;
    if !snapshot.fresh(chrono::Utc::now()) {
        return None;
    }
    Prepared::new(snapshot.clone(), raw).ok()
}

/// 通常会話の一回目には候補を渡さず、依頼検証後にだけ二回目で一頭を選ばせる。
/// 各段階は一回限り。取消をまたいだ結果は次段階にも命令発行にも使わない。
pub(super) async fn render(
    mut request: GenerationRequest,
    llm: &RigLlm,
    prepared: &Prepared,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Value> {
    request.max_tokens = request.max_tokens.max(192);
    let mut reports = Vec::new();
    for stage in [Stage::Intent, Stage::Selection] {
        ensure!(
            !*cancel.borrow() && cancel.has_changed().is_ok(),
            "cancelled"
        );
        let (stage_request, tools) = intent::request(request.clone(), prepared, stage)?;
        let response = tokio::select! { biased;
            _ = bridge::cancelled(cancel) => anyhow::bail!("cancelled"),
            r = llm.generate_with_tools(&stage_request, &tools, &[], true) => r,
        };
        ensure!(
            !*cancel.borrow() && cancel.has_changed().is_ok(),
            "cancelled"
        );
        let (report, native_message) = match response {
            Ok(value) => value,
            Err(error) => {
                reports.push(json!({"kind":"player_chat","horse_stage":format!("{stage:?}"),"error":error.to_string()}));
                let output = crate::chat_validation::finish(None);
                return Ok(
                    json!({"op":"result","text":output.text,"dialogue_action":output.action,
                    "dialogue_outcome":output.status,"dialogue_reason":output.reason,"llm_reports":reports}),
                );
            }
        };
        let message = native_message.unwrap_or_else(|| json!({"content":report.generated.text}));
        let decoded = intent::decode(
            stage,
            &message,
            report.generated.finish_reason.as_deref().unwrap_or(""),
        );
        reports.push(json!(report));
        let mut result = match decoded {
            Ok(Decision::List(intent)) => match intent.validate(&prepared.original) {
                Ok(()) => continue,
                Err(error) => rejected(error),
            },
            Ok(Decision::Select(choice)) => match prepared.resolve(&choice) {
                Ok(_) => json!({"op":"result","text":"","dialogue_action":"recall_horse",
                    "horse_choice":choice,"horse_recall_status":"selected"}),
                Err(error) => rejected(error),
            },
            Ok(Decision::Conversation) => {
                let output = intent::conversation(message["content"].as_str().unwrap_or(""));
                json!({"op":"result","text":output.text,"dialogue_action":output.action,
                    "dialogue_outcome":output.status,"dialogue_reason":output.reason})
            }
            Err(error) => rejected(error),
        };
        result["llm_reports"] = json!(reports);
        return Ok(result);
    }
    anyhow::bail!("horse_selection_missing")
}
fn rejected(error: anyhow::Error) -> Value {
    json!({"op":"result","text":"馬の指定を確かめられへんかったわ。どの馬を呼ぼか？",
        "horse_recall_status":"rejected","horse_recall_reason":error.to_string()})
}

impl Dialogue {
    /// 同じ入力世代・原文・最新個体である場合にだけ既存assist配送へ積む。
    /// rowの命令IDはturn単位の発行済み印。ACK後に同じ生成結果が再適用されても二度発行しない。
    pub(super) fn apply_horse_recall(
        &self,
        sid: &str,
        turn: &str,
        epoch: u64,
        input: &bridge::Input,
        result: &mut Value,
        cancelled: bool,
    ) -> Result<()> {
        let Some(value) = result.get("horse_choice") else {
            return Ok(());
        };
        ensure!(!cancelled, "cancelled");
        let choice: Choice = serde_json::from_value(value.clone())?;
        let prepared = input
            .horse_recall
            .as_ref()
            .context("horse_prepared_missing")?;
        let mut d = self.data.lock().unwrap();
        ensure!(!d.stopped, "server_stopping");
        let row = d
            .rows
            .iter()
            .find(|r| r["turn_id"] == turn && r["session_id"] == sid)
            .context("horse_turn_missing")?;
        ensure!(
            row["horse_command_id"].is_null(),
            "horse_turn_already_dispatched"
        );
        ensure!(
            row["player_input_text"] == prepared.original,
            "horse_input_changed"
        );
        let s = d.sessions.get_mut(sid).context("horse_session_missing")?;
        ensure!(
            s.epoch == epoch && s.current_turn == turn && available(s),
            "horse_turn_no_longer_current"
        );
        let latest = s
            .latest
            .as_ref()
            .and_then(|e| e.owned_horses.as_ref())
            .context("horse_snapshot_missing")?;
        let command = s
            .assist
            .enqueue_horse(prepared, &choice, latest, chrono::Utc::now())?;
        s.history.push(turn, "user", &prepared.original);
        s.history.story(
            &format!("{turn}:horse_request"),
            "プレイヤーの依頼で馬の呼び戻しを要求した。移動結果はまだ確認していない。",
        );
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["horse_command_id"] = command.command_id.clone().into();
        }
        result["horse_recall_status"] = "issued".into();
        result["horse_command"] = json!(command);
        d.revision += 1;
        tracing::info!(event="horse_recall_dispatched",session_id=sid,turn_id=turn,command=%json!(command));
        Ok(())
    }
}

/// ACKの重複除去・対象照合後の結果だけを短期の出来事へ残す。
/// 発行後に会話が進んでも結果は記録するが、成功発話は現在の同じワールドで安全に話せる場合だけ。
pub(super) fn observe_results(s: &mut Session, results: &crate::assist::Results) -> Option<String> {
    let mut arrived = false;
    for observed in results.observed.iter().filter(|o| {
        o.known_command && o.result.command_type == AdapterCommandResultCommandType::RecallHorse
    }) {
        let r = &observed.result;
        let succeeded = r.status == AdapterCommandResultStatus::Succeeded;
        s.history.story(
            &format!("horse_result:{}", r.command_id),
            if succeeded {
                "Minecraftで、依頼された馬の呼び戻しが成功した。"
            } else {
                "依頼された馬の呼び戻しは成功を確認できなかった。"
            },
        );
        arrived |= succeeded
            && s.latest
                .as_ref()
                .and_then(|e| e.owned_horses.as_ref())
                .is_some_and(|snapshot| {
                    r.extra.get("world_id").and_then(Value::as_str)
                        == Some(snapshot.world_id.as_str())
                });
    }
    (arrived
        && available(s)
        && s.cancel.is_none()
        && s.warning.is_none()
        && s.pending_warning.is_none())
    .then(|| "馬、こっちへ来たで。".to_owned())
}
