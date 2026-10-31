//! パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
//! 次の入力が届いても、始まった返答の配送と取消権を元turnに残す。
//! 入力・生成の所有者とは分け、実再生結果と表示ACKは既存の履歴確定へ返す。
use super::{Data, Session, workshop_reply};
use tokio::sync::watch;

pub(super) struct Pending {
    pub speech: crate::combat::model::Speech,
    pub input: Option<String>,
    pub source: Option<String>,
    pub binding: Option<super::request_runtime::Binding>,
}

pub(super) struct Active {
    pub turn: String,
    pub epoch: u64,
    pub cancel: watch::Sender<bool>,
    pub workshop: Option<workshop_reply::State>,
}

pub(super) fn current(s: &Session, turn: &str, epoch: u64) -> bool {
    s.reply_outputs
        .iter()
        .any(|output| output.turn == turn && output.epoch == epoch && !*output.cancel.borrow())
}

pub(super) fn workshop<'a>(
    s: &'a Session,
    turn: &str,
    epoch: u64,
) -> Option<&'a workshop_reply::State> {
    s.reply_outputs
        .iter()
        .find(|output| output.turn == turn && output.epoch == epoch && !*output.cancel.borrow())
        .and_then(|output| output.workshop.as_ref())
}

/// 同じdataロック内で、新入力の各routeによる取消より先に配送の所有権を移す。
pub(super) fn preserve(d: &mut Data, sid: &str) {
    let Some(s) = d.sessions.get_mut(sid) else {
        return;
    };
    if !s.cancel.as_ref().is_some_and(|cancel| !*cancel.borrow())
        || !d.rows.iter().any(|row| {
            row["session_id"] == sid
                && row["turn_id"] == s.current_turn
                && matches!(row["playback_status"].as_str(), Some("started" | "queued"))
        })
    {
        return;
    }
    s.reply_outputs.push(Active {
        turn: s.current_turn.clone(),
        epoch: s.epoch,
        cancel: s.cancel.take().unwrap(),
        workshop: s.workshop_reply.take(),
    });
}

/// 新入力では呼ばない。明示停止・終了・緊急割込みは残った配送にも届く。
/// 戦闘pause済みの川柳は同じworkerで再開するためkeep_pausedで保持する。
pub(super) fn cancel(d: &mut Data, sid: &str, reason: &str, keep_paused: bool) {
    let Some(s) = d.sessions.get_mut(sid) else {
        return;
    };
    s.pending_replies.clear();
    s.reply_outputs.retain(|output| {
        if keep_paused
            && output
                .workshop
                .as_ref()
                .is_some_and(workshop_reply::State::paused)
        {
            return true;
        }
        let _ = output.cancel.send(true);
        if let Some(row) = d.rows.iter_mut().find(|row| {
            row["session_id"] == sid
                && row["turn_id"] == output.turn
                && row["epoch"] == output.epoch
        }) {
            row["playback_status"] = "cancelled".into();
            row["cancel_reason"] = reason.into();
        }
        false
    });
}
