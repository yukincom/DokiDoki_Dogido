//! 安全な観測と発声の境界で、保持した一句の再開確認を配送する。
use super::*;
use crate::{
    combat::model::{Delivery, Mode, Scope, Speech},
    workshop_combat::{Reason, Recovery},
    workshop_followup::Stage,
};

pub(super) fn clear_for_resume(s: &Session) -> bool {
    if !s.preview && super::workshop_focus::quiet(s) {
        // 敵との戦闘後の安堵は待つ。暗さだけのalertには句の復帰を妨げさせない。
        return s.mode != Mode::Aftermath;
    }
    !s.preview
        && fresh(s)
        && s.mode == Mode::Normal
        && s.latest.as_ref().is_some_and(|e| {
            warnings::interruption_reason(e).is_none()
                && e.player.health.is_none_or(|h| h > 0.0)
                && [
                    e.combat.hostiles_within_7,
                    e.combat.hostiles_within_10,
                    e.combat.hostiles_within_scan_ground,
                ]
                .iter()
                .all(|n| n.is_none_or(|n| n == 0))
        })
        && s.audio_latest
            .as_ref()
            .is_none_or(|e| warnings::interruption_reason(e).is_none())
}

pub(super) fn applicable(s: &Session, id: &str, version: u64) -> bool {
    clear_for_resume(s)
        && s.haiku.workshop.as_ref().is_some_and(|w| {
            w.open
                && !w.combat_paused()
                && w.hud_id == id
                && w.version == version
                && w.recovery.announcing
        })
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum NoticeEnd {
    Completed,
    Consumed,
    Retry,
}

/// 新入力・手動停止は声かけを消費する。それ以外の配送失敗は句を保持して再度待つ。
pub(super) fn finish_notice(s: &mut Session, id: &str, version: u64, status: NoticeEnd) {
    let Some(w) = s
        .haiku
        .workshop
        .as_mut()
        .filter(|w| w.open && w.hud_id == id && w.version == version && w.recovery.announcing)
    else {
        return;
    };
    let reason = w.recovery.reason;
    w.recovery = Recovery::default();
    if status == NoticeEnd::Completed {
        w.followup = Stage::CombatResumeConfirmation;
        w.record_activity(Instant::now());
    } else if status == NoticeEnd::Retry {
        w.pause(Instant::now());
        w.recovery.reason = reason;
        w.recovery.retry_at = Some(Instant::now() + Duration::from_secs(2));
    }
}

impl Dialogue {
    /// 復帰できる観測と発声の空きを待ち、保持した三行を再掲する。
    /// 句を再開して案内を配送し、案内完了後にfinish_noticeが継続確認へ進める。
    /// 未採用案があればその案を再掲するが、再掲だけで採用・保存は行わない。
    pub(super) fn start_workshop_recovery(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<tokio::task::JoinHandle<()>>,
        sid: &str,
    ) {
        if d.stopped || jobs.len() >= 16 {
            return;
        }
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        if !clear_for_resume(s)
            || s.warning.is_some()
            || s.pending_warning.is_some()
            || s.cancel.is_some()
            || s.assist_pending.is_some()
            || s.deferred_input.is_some()
            || s.haiku.active.is_some()
            || s.combat_input.is_some()
        {
            return;
        }
        let now = Instant::now();
        let Some(w) = s.haiku.workshop.as_mut().filter(|w| {
            w.open && w.combat_paused() && w.recovery.retry_at.is_none_or(|t| now >= t)
        }) else {
            return;
        };
        let reason = w.recovery.reason.unwrap_or(Reason::Safe);
        let lines = w.pending.as_ref().map_or(&w.current_lines, |p| &p.lines);
        let text = reason.prompt(&crate::workshop_edit::reading(lines));
        let mut speech = Speech::new("workshop_resume", text);
        speech.delivery = Delivery::PlayerReply;
        speech.scope = Scope::Workshop {
            id: w.hud_id.clone(),
            version: w.version,
        };
        w.resume(now);
        w.drift_count = 0;
        w.recovery.announcing = true;
        tracing::info!(
            event = "workshop_combat_resume",
            session_id = sid,
            workshop_id = w.hud_id,
            ?reason
        );
        self.spawn_actions(d, jobs, sid, vec![speech], None);
    }
}
