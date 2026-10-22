//! 共有の環境判断へ安心度と身体行動を添える。独立したモデル呼出しは持たない。
//! 警告turnと入力世代に選択を結び、配送前に現在の安全・身体世代を再照合する。
use super::Session;
use crate::{body::{autonomy::Prepared, contracts::Context}, combat::model::{Delivery, Speech}};
use serde_json::{Value, json};
use std::time::Instant;

pub(super) const KIND: &str = "reassurance_notice";

/// 同じ身体世代・通常追従距離の実測で、安心範囲から外へ出た出来事を一件だけ保持する。
/// 最初の観測は比較基準。範囲へ戻ると未通知を捨て、次の退出を受け付ける。
#[derive(Default)]
pub(super) struct BoundaryNotice {
    context: Option<Context>,
    inside: bool,
    pending: bool,
    range_blocks: f64,
}
impl BoundaryNotice {
    fn observe(&mut self, context: &Context, distance: f64, range_blocks: f64) {
        let inside = distance <= range_blocks;
        // 設定変更で境界だけが動いても退出を作らず、新しい距離を比較基準にする。
        if self.context.as_ref() != Some(context) || self.range_blocks != range_blocks {
            self.context = Some(context.clone());
            self.range_blocks = range_blocks;
            self.inside = inside;
            self.pending = false;
            return;
        }
        if inside { self.pending = false; }
        else if self.inside { self.pending = true; }
        self.inside = inside;
    }
    fn considered(&mut self) { self.pending = false; }
}

/// 会話や警告で生成できない間も、受理済みの距離だけを比較する。モデルは呼ばない。
/// 一時保留は一件へまとめる。身体消失・休止・制御モード変更では古い境界を持ち越さない。
pub(super) fn observe_boundary(s: &mut Session, now: Instant) {
    if !s.body_interest_enabled || s.preview || s.foreground.game_paused {
        s.body_autonomy.cancel_owner_arrival();
    }
    let current = s.body_observation.accepted(now).zip(s.body_autonomy.reassurance(now))
        .zip(s.body_autonomy.follow_distance(now));
    if s.body_interest_enabled && !s.preview && !s.foreground.game_paused
        && s.body_autonomy.available(now)
        && let Some(((sample, reassurance), range_blocks)) = current {
        s.body_interest_notice.observe(&sample.context, reassurance.owner_distance, range_blocks);
    } else {
        s.body_interest_notice = BoundaryNotice::default();
    }
}

/// 一つの環境生成だけの候補。終了後は破棄し、次のturnへ持ち越さない。
pub(super) struct Pending {
    turn: String,
    instance: String,
    generation: u64,
    image_choice: bool,
    prepared: Option<Prepared>,
}

fn eligible(s: &Session, now_ms: u64, turn: Option<&str>) -> bool {
    s.body_interest_enabled && !s.preview && !s.foreground.game_paused
        && !s.foreground.combat_active
        && s.warning.as_ref().is_none_or(|w| turn == Some(w.turn.as_str()))
        && s.pending_warning.is_none() && s.cancel.is_none() && s.assist_pending.is_none()
        && s.light_cancel.is_none() && s.pending_vocalization.is_none() && !s.haiku.foreground()
        && s.deferred_input.is_none() && s.reply_queue.is_empty()
        && s.latest.as_ref().is_some_and(|e| super::warnings::interruption_reason(e).is_none())
        && s.audio_latest.as_ref().is_none_or(|e| super::warnings::interruption_reason(e).is_none())
        && matches!(s.foreground.route, crate::foreground::Route::None | crate::foreground::Route::Casual)
        && s.last_player_input.is_none_or(|at| now_ms.saturating_sub(at) >= 5000)
        && s.body_observation.accepted(Instant::now()).is_some()
        && s.body_autonomy.available(Instant::now())
}

/// 既存環境候補が無い時だけ、同じ判断を起こす。周辺に変化があったとは記録しない。
pub(super) fn opportunity(s: &Session, now_ms: u64) -> Option<Speech> {
    if !(s.body_interest_notice.pending || s.body_autonomy.owner_arrival_pending(Instant::now()))
        || !eligible(s, now_ms, None) { return None; }
    s.body_autonomy.ready_reassurance(Instant::now())?;
    let mut action = Speech::new(KIND, "");
    action.scope = crate::combat::model::Scope::Safe;
    Some(action)
}

/// 同じ環境turn自身は割込みに数えない。別の警告・入力・危険なら未決定の選択を失効する。
pub(super) fn refresh(s: &mut Session, now_ms: u64) {
    let turn = s.body_interest_pending.as_ref().map(|p| p.turn.as_str());
    if !eligible(s, now_ms, turn) {
        cancel(s);
    }
}

/// 未決定の候補と未配送命令を失効する。既に身体へ受理された移動の物理的な停止は身体Modが所有する。
pub(super) fn cancel(s: &mut Session) {
    s.body_interest_pending = None;
    s.body_autonomy.cancel();
}

/// 生成直前の同じ観測に行動候補を添える。準備はこのturnの一回だけで、別のモデルは呼ばない。
pub(super) fn attach(s: &mut Session, turn: &str, action: &mut Speech, now_ms: u64) {
    if action.delivery != Delivery::Ambient || !crate::environment::reaction::is_model_reaction(action)
        || s.body_interest_pending.is_some() || !eligible(s, now_ms, Some(turn)) { return; }
    // 共有の生成枠を待つ間にも帰還できる。退出・到着通知は、生成直前にも未消費を確かめる。
    if action.kind == KIND && !(s.body_interest_notice.pending
        || s.body_autonomy.owner_arrival_pending(Instant::now())) { return; }
    let Some(mut prepared) = s.body_autonomy.prepare(Instant::now()) else { return; };
    // 添付された当回の画像だけを移動申請へ使える。撮影IDはコードが埋め、モデルへUUIDや座標を作らせない。
    if action.image.is_some() && !prepared.after_owner_arrival
        && let Some(capture)=action.leaf.as_ref().map(|l| &l.details["visual_capture"])
        && let (Some(id),Some(at))=(capture["capture_id"].as_str(),capture["captured_at"].as_str())
        && let Ok(at)=chrono::DateTime::parse_from_rfc3339(at) {
        prepared.image_capture=Some((id.into(),at.with_timezone(&chrono::Utc)));
        prepared.available_actions.push("inspect_image".into());
    }
    // 周囲の変化による判断へ同時に添えた場合も、この退出は一回で消費する。
    // stay・沈黙・生成失敗の後に、同じ範囲外滞在を時刻だけで再試行しない。
    s.body_interest_notice.considered();
    let details = &mut action.leaf.as_mut().unwrap().details["reaction_context"];
    if action.kind == KIND {
        details["trigger"] = if prepared.after_owner_arrival { json!({"kind":"owner_arrived_near"}) }
            else { json!({"kind":"owner_left_reassurance_range","range_blocks":s.body_interest_notice.range_blocks}) };
    }
    tracing::info!(event="environment_body_prepared",turn_id=turn,after_owner_arrival=prepared.after_owner_arrival,
        available_actions=%json!(prepared.available_actions),owner_working=%prepared.reassurance["owner_working"]);
    details["body_options"] = json!({"available_actions":prepared.model_actions(),
        "candidates":prepared.model_candidates,"recent_choices":prepared.recent_choices});
    if prepared.after_owner_arrival { details["body_options"]["after_owner_arrival"] = json!(true); }
    details["world_context"]["observations"]["reassurance"] = super::body_runtime::reassurance_context(prepared.reassurance.clone());
    s.body_interest_pending = Some(Pending { turn: turn.to_owned(), instance: s.instance_id.clone(),
        generation: s.input_generation, image_choice: action.image.is_some(), prepared: Some(prepared) });
}

/// 返った行動だけを既存の身体検証器へ渡す。不正な行動は捨て、正常な発話や沈黙は維持する。
pub(super) fn apply(s: &mut Session, turn: &str, result: Option<&Value>, now_ms: u64) {
    if !s.body_interest_pending.as_ref().is_some_and(|p| p.turn == turn) { return; }
    let valid = eligible(s, now_ms, Some(turn));
    let pending = s.body_interest_pending.as_mut().unwrap();
    let Some(prepared) = pending.prepared.take() else { return; };
    if !valid || pending.instance != s.instance_id || pending.generation != s.input_generation
        || !s.warning.as_ref().is_some_and(|w| w.turn == turn)
        || !s.body_observation.accepted(Instant::now()).is_some_and(|sample| sample.context == prepared.context) {
        s.body_autonomy.cancel_preparation();
        return;
    }
    let Some(choice) = result.and_then(|r| r.get("body_action")) else {
        s.body_autonomy.cancel_preparation();
        return;
    };
    // 画像から移動する判断には相手への一言も必要。語句や理由の意味は検査せず、発話なしなら移動も保留する。
    if pending.image_choice && choice["action"] != "stay"
        && result.and_then(|r| r["text"].as_str()).is_none_or(|text| text.trim().is_empty()) {
        s.body_autonomy.cancel_preparation();return;
    }
    match s.body_autonomy.choose(&prepared, &choice.to_string(), Instant::now(), chrono::Utc::now()) {
        Ok(command) => tracing::info!(event="environment_body_selected",turn_id=turn,command=%json!(command)),
        Err(error) => tracing::info!(event="environment_body_rejected",turn_id=turn,%error),
    }
}

pub(super) fn finish(s: &mut Session, turn: &str) {
    if s.body_interest_pending.as_ref().is_some_and(|p| p.turn == turn) {
        s.body_interest_pending = None;
        s.body_autonomy.cancel_preparation();
    }
}

#[cfg(test)]
mod tests;
