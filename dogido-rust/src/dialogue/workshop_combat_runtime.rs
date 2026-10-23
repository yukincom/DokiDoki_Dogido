//! 安全な観測と発声の境界で、保持した一句を自動で再開する。
use super::*;
use crate::{
    combat::model::{Delivery, Mode, Scope, Speech},
    workshop_combat::{Reason, Recovery},
    workshop_followup::Stage,
};

// 意図分類のBERT移行は完了し、旧OSAI・chat分類fallbackの経路は撤去済み。
// 再開・継続はBERTにも委ねず、敵の現在観測と警戒解除条件でコードが決める。旧AI判定を復活させない。
pub(super) fn clear_for_resume(s: &Session) -> bool {
    !s.preview
        && fresh(s)
        // 敵との戦闘後の安堵は待つ。暗さだけのalertには句の復帰を妨げさせない。
        && (s.mode == Mode::Normal || s.mode == Mode::Alert && super::workshop_focus::quiet(s))
        && s.latest.as_ref().is_some_and(|e| {
            e.visual_threats.is_empty()
                && e.auditory_threats.is_empty()
                && e.combat.combat_active_hint != Some(true)
                && warnings::interruption_reason(e).is_none()
                && e.player.health.is_none_or(|h| h > 0.0)
                && [e.combat.hostiles_within_7, e.combat.hostiles_within_10, e.combat.hostiles_within_scan_ground]
                    .iter().all(|n| n.is_none_or(|n| n == 0))
        })
        && s.audio_latest.as_ref().is_none_or(|e| e.auditory_threats.is_empty()
            && warnings::interruption_reason(e).is_none())
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
        w.followup = Stage::Discussion;
        w.record_activity(Instant::now());
    } else if status == NoticeEnd::Retry {
        w.pause(Instant::now());
        w.recovery.reason = reason;
        w.recovery.retry_at = Some(Instant::now() + Duration::from_secs(2));
    }
}

impl Dialogue {
    /// 復帰できる観測と発声の空きを待ち、保持した三行を再掲する。
    /// 句を再開して案内を配送し、案内完了後にfinish_noticeが通常相談へ戻す。
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
        let text = reason.prompt(&crate::workshop_edit::surface(lines));
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
