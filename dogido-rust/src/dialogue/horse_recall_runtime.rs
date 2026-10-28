//! BERTで呼び寄せ依頼を受けた後に候補を見せ、一回のLLM選択を現在のSessionへ照合する。
//! BERTへ変更済み。旧判定へ戻さない。
//! LLMは発話と対象選択だけを返す。通常turnはepoch、戦闘支援は入力世代を照合し、実移動はFabricが担当する。
//! 要求の発行とMinecraftの実行結果を出来事として分け、未確認の成功を発話履歴へ入れない。
use super::{Dialogue, Session, bridge, observation_fresh};
use crate::{
    assist::horse_recall::{
        contracts::{Availability, Choice, Prepared, Snapshot},
        intent::{self, Decision},
    },
    events::{AdapterCommandResultCommandType, AdapterCommandResultStatus, EventName, GameEvent},
    llm::RigLlm,
    types::GenerationRequest,
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use tokio::sync::watch;

mod assist;

/// 馬一覧の正本。戦況イベントの視認一式と、statusだけが持つ馬一覧を区別する。
#[derive(Default)]
pub(super) struct State {
    snapshot: Option<Snapshot>,
    /// 同じBERT受理依頼を重複して候補選択へ回さないための入力世代。
    checked_generation: Option<u64>,
}
impl State {
    /// 非重複イベントだけを受ける。部分通知では鮮度を延長せず、次元変更・一覧撤去で破棄する。
    pub(super) fn observe(&mut self, event: &GameEvent, recent: bool) {
        if event.event.name == EventName::StatusSnapshot || event.owned_horses.is_some() {
            self.snapshot = event
                .owned_horses
                .as_ref()
                .filter(|snapshot| {
                    recent
                        && event
                            .player
                            .dimension
                            .as_ref()
                            .is_none_or(|d| d == &snapshot.dimension)
                        && self
                            .snapshot
                            .as_ref()
                            .is_none_or(|old| snapshot.observed_at >= old.observed_at)
                })
                .cloned();
        } else if recent
            && self.snapshot.as_ref().is_some_and(|snapshot| {
                event
                    .player
                    .dimension
                    .as_ref()
                    .is_some_and(|d| d != &snapshot.dimension)
            })
        {
            self.snapshot = None;
        }
    }
}

/// BERTで依頼を受理していても、現在状態によって候補選択へ進めない場合もある。
/// 停止理由と候補を生成直前に固定し、実行時はSessionの現在状態へ再照合する。
#[derive(Clone)]
pub(super) struct Request {
    original: String,
    prepared: Option<Prepared>,
    blocked: Option<Blocked>,
}
#[derive(Clone, Copy)]
enum Blocked {
    Paused,
    Missing,
    Stale,
    Unavailable,
}
impl Blocked {
    fn code(self) -> &'static str {
        match self {
            Self::Paused => "horse_game_paused",
            Self::Missing => "horse_snapshot_missing",
            Self::Stale => "horse_stale_snapshot",
            Self::Unavailable => "horse_adapter_unavailable",
        }
    }
    fn speech(self) -> &'static str {
        match self {
            Self::Paused => {
                "ゲームが止まってるから、まだ呼んでへんで。再開してから、もう一度頼んでな。"
            }
            Self::Missing | Self::Stale => {
                "今の乗れる動物の一覧を確認できへんから、まだ呼んでへんで。少し待ってから、もう一度頼んでな。"
            }
            Self::Unavailable => {
                "今のワールドでは呼び戻せる動物の一覧を取得できへんから、まだ呼んでへんで。"
            }
        }
    }
}

fn available(s: &Session) -> bool {
    !s.preview
        && observation_fresh(s)
        && !s.foreground.game_paused
        && s.assist.supports_horse_recall()
}

/// BERTへ変更済み。旧判定へ戻さない。分類済み原文に生成直前の候補を固定する。
pub(super) fn capture(
    s: &Session,
    raw: &str,
    classification: Option<&crate::request_classifier::Classification>,
) -> Option<Request> {
    let classification = classification?;
    if classification.text != raw
        || classification.prediction.label != crate::request_classifier::Label::RecallMount
        || s.horse_recall.checked_generation == Some(s.input_generation)
    {
        return None;
    }
    let blocked = if s.preview || !s.assist.supports_horse_recall() {
        Some(Blocked::Unavailable)
    } else {
        blocking_reason(s)
    };
    let prepared = if blocked.is_none() {
        Some(
            Prepared::from_classified(s.horse_recall.snapshot.as_ref()?.clone(), classification)
                .ok()?,
        )
    } else {
        None
    };
    Some(Request {
        original: raw.to_owned(),
        prepared,
        blocked,
    })
}

fn blocking_reason(s: &Session) -> Option<Blocked> {
    if !available(s) {
        return Some(if s.foreground.game_paused {
            Blocked::Paused
        } else {
            Blocked::Stale
        });
    }
    let Some(snapshot) = &s.horse_recall.snapshot else {
        return Some(Blocked::Missing);
    };
    if !snapshot.fresh(chrono::Utc::now()) {
        return Some(Blocked::Stale);
    }
    (snapshot.status == Availability::Unavailable).then_some(Blocked::Unavailable)
}

/// 既存の診断snapshotへ観測と停止理由を添える。
pub(super) fn snapshot(s: &Session) -> Value {
    json!({"supported":s.assist.supports_horse_recall(),
        "blocked_reason":blocking_reason(s).map(Blocked::code),
        "observation":s.horse_recall.snapshot})
}

/// BERTへ変更済み。旧判定へ戻さない。
/// 受付済みの依頼について、候補選択と必要な返答を一回で生成する。
pub(super) async fn render(
    mut request: GenerationRequest,
    llm: &RigLlm,
    context: &Request,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Value> {
    let mut reports = Vec::new();
    if let Some(reason) = context.blocked {
        return Ok(
            json!({"op":"result","text":reason.speech(),"horse_recall_requested":true,
            "horse_recall_status":"blocked","horse_recall_reason":reason.code(),"llm_reports":reports}),
        );
    }
    let prepared = context
        .prepared
        .as_ref()
        .context("horse_prepared_missing")?;
    request.max_tokens = request.max_tokens.max(192);
    let (request, tools) = intent::request(request, prepared)?;
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    let response = tokio::select! { biased;
        _ = bridge::cancelled(cancel) => anyhow::bail!("cancelled"),
        r = llm.generate_with_tools(&request, &tools, &[], true) => r,
    };
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    let (report, native_message) = match response {
        Ok(value) => value,
        Err(error) => {
            reports.push(
                json!({"kind":"player_chat","horse_stage":"Selection","error":error.to_string()}),
            );
            return Ok(selection_failure(reports));
        }
    };
    let message = native_message.unwrap_or_else(|| json!({"content":report.generated.text}));
    let decoded = intent::decode(
        &message,
        report.generated.finish_reason.as_deref().unwrap_or(""),
    );
    reports.push(json!(report));
    let mut result = match decoded {
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
        _ => rejected(anyhow::anyhow!("horse_invalid_selection")),
    };
    result["llm_reports"] = json!(reports);
    result["horse_recall_requested"] = json!(true);
    Ok(result)
}
fn selection_failure(reports: Vec<Value>) -> Value {
    // 依頼を検証した後の通信失敗を通常雑談へ戻すと、未実施の操作を約束できてしまう。
    json!({"op":"result","text":"呼ぶ相手を確認できへんかったから、まだ呼んでへんで。",
        "horse_recall_requested":true,"horse_recall_status":"rejected",
        "horse_recall_reason":"horse_selection_unavailable","llm_reports":reports})
}
fn rejected(error: anyhow::Error) -> Value {
    let code = error.to_string();
    let text = match code.as_str() {
        "horse_no_longer_loaded"
        | "horse_target_changed"
        | "horse_not_eligible"
        | "horse_snapshot_changed" => crate::assist::horse_recall::feedback::failure(&code).1,
        _ => "呼ぶ相手を確かめられへんかったわ。どの子を呼ぼか？",
    };
    json!({"op":"result","text":text,"horse_recall_requested":true,"horse_recall_status":"rejected","horse_recall_reason":code})
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
            .and_then(|request| request.prepared.as_ref())
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
        let input_occurred_at = row["created_at"].clone();
        let s = d.sessions.get_mut(sid).context("horse_session_missing")?;
        ensure!(
            s.epoch == epoch && s.current_turn == turn && available(s),
            "horse_turn_no_longer_current"
        );
        let command = enqueue(s, turn, prepared, &choice, input_occurred_at)?;
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

/// 会話・操作支援のどちらから来ても、同じ候補再照合と命令queueを使う。
/// 敵の有無は逃走用途を妨げるため条件にせず、鮮度・一時停止・実体は検査する。
fn enqueue(
    s: &mut Session,
    turn: &str,
    prepared: &Prepared,
    choice: &Choice,
    input_occurred_at: Value,
) -> Result<crate::assist::horse_recall::queue::Command> {
    ensure!(available(s), "horse_session_unavailable");
    let latest = s
        .horse_recall
        .snapshot
        .as_ref()
        .context("horse_snapshot_missing")?;
    let command = match s
        .assist
        .enqueue_horse(prepared, choice, latest, chrono::Utc::now())
    {
        Ok(command) => command,
        Err(error) => {
            s.history.story(
                &format!("{turn}:horse_rejected"),
                &crate::assist::horse_recall::feedback::failure_story(&error.to_string()),
            );
            return Err(error);
        }
    };
    s.history.push_input(turn, &prepared.original, input_occurred_at);
    s.history.story(
        &format!("{turn}:horse_request"),
        "プレイヤーの依頼で乗れる動物の呼び戻しを要求した。移動結果はまだ確認していない。",
    );
    Ok(command)
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
        let story = if succeeded {
            "Minecraftで、依頼された乗れる動物の呼び戻しが成功した。".to_owned()
        } else {
            crate::assist::horse_recall::feedback::failure_story(&r.detail_code)
        };
        s.history
            .story(&format!("horse_result:{}", r.command_id), &story);
        arrived |= succeeded
            && s.horse_recall.snapshot.as_ref().is_some_and(|snapshot| {
                r.extra.get("world_id").and_then(Value::as_str) == Some(snapshot.world_id.as_str())
            });
    }
    (arrived
        && available(s)
        && s.cancel.is_none()
        && s.warning.is_none()
        && s.pending_warning.is_none())
    .then(|| "こっちへ来たで。".to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration, Utc};

    #[test]
    fn selection_failure_keeps_validated_request_out_of_ordinary_chat() {
        let result = selection_failure(vec![json!({"error":"connection reset"})]);
        assert_eq!(result["horse_recall_requested"], true);
        assert_eq!(result["horse_recall_status"], "rejected");
        assert!(result["text"].as_str().unwrap().contains("まだ呼んでへん"));
        assert!(result.get("horse_choice").is_none());
    }

    fn frame(at: chrono::DateTime<Utc>, horses: bool) -> Value {
        let mut value = super::super::empty_event("試験");
        value["player"]["dimension"] = json!("minecraft:overworld");
        if horses {
            value["owned_horses"] = json!({"schema_version":2,"status":"available",
                "world_id":"10000000-0000-0000-0000-000000000001","revision":1,
                "observed_at":at,"player_uuid":"20000000-0000-0000-0000-000000000001",
                "dimension":"minecraft:overworld","candidates":[]});
        }
        value
    }

    #[test]
    fn partial_events_keep_original_horse_freshness() {
        let at = Utc::now();
        let mut state = State::default();
        state.observe(&GameEvent::parse(frame(at, true)).unwrap(), true);
        for name in [
            EventName::ThreatApproaching,
            EventName::CombatEnded,
            EventName::HostileDefeated,
            EventName::HostileAudioDetected,
        ] {
            let mut partial = frame(at + Duration::seconds(2), false);
            partial["event"]["name"] = json!(name);
            state.observe(&GameEvent::parse(partial).unwrap(), true);
            let snapshot = state.snapshot.as_ref().unwrap();
            assert_eq!(snapshot.observed_at, at);
            assert!(snapshot.fresh(at + Duration::seconds(3)));
            assert!(!snapshot.fresh(at + Duration::milliseconds(3001)));
        }
    }

    #[test]
    fn missing_stale_or_different_dimension_invalidates_cached_horses() {
        let at = Utc::now();
        let mut state = State::default();
        for kind in ["missing", "stale", "dimension", "older"] {
            state.observe(&GameEvent::parse(frame(at, true)).unwrap(), true);
            let mut event = frame(at, kind != "missing");
            if kind == "dimension" {
                event["event"]["name"] = json!("combat_ended");
                event.as_object_mut().unwrap().remove("owned_horses");
                event["player"]["dimension"] = json!("minecraft:the_nether");
            }
            if kind == "older" {
                event["owned_horses"]["observed_at"] = json!(at - Duration::seconds(1));
            }
            state.observe(&GameEvent::parse(event).unwrap(), kind != "stale");
            assert!(state.snapshot.is_none(), "{kind}");
        }
        let mut event = frame(at, true);
        event["owned_horses"]["status"] = json!("unavailable");
        state.observe(&GameEvent::parse(event.clone()).unwrap(), true);
        assert_eq!(
            state.snapshot.as_ref().unwrap().status,
            Availability::Unavailable
        );
        event["owned_horses"]["world_id"] = json!(uuid::Uuid::new_v4().to_string());
        state.observe(&GameEvent::parse(event.clone()).unwrap(), true);
        assert_eq!(
            state.snapshot.unwrap().world_id,
            event["owned_horses"]["world_id"].as_str().unwrap()
        );
    }
}
