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
    if s.preview || !super::workshop_focus::quiet_observation(s) {
        return false;
    }
    // 最後の交戦相手の死亡が確定した。無関係な敵の在席や直前の被弾を再交戦と数えない。
    if s.haiku.workshop.as_ref().is_some_and(|w| w.recovery.ended_by_last_kill) {
        return true;
    }
    s.chat_allowed && (s.mode == Mode::Normal || s.mode == Mode::Alert && super::workshop_focus::quiet(s))
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
    s.haiku.workshop.as_ref().is_some_and(|w| {
        w.open && w.hud_id == id && w.version == version
            && if w.combat_paused() {
                w.recovery.generating && clear_for_resume(s)
            } else {
                // 同じ句へ戻した後の発話も、新たな交戦・死亡・観測失効でのみ打ち切る。
                !s.preview && super::workshop_focus::quiet_observation(s)
            }
    })
}

/// 呼出元は生成turnの所有権と安全性を同じロックで照合する。表示ACKや音声の成否は復帰を巻き戻さない。
pub(super) fn resume(s: &mut Session, id: &str, version: u64) -> Option<u64> {
    let w = s.haiku.workshop.as_mut().filter(|w| {
        w.open && w.hud_id == id && w.version == version && w.recovery.generating
    })?;
    if !w.resume(Instant::now()) {
        return None;
    }
    w.recovery = Recovery::default();
    w.drift_count = 0;
    w.followup = Stage::Discussion;
    Some(w.input_boundary)
}

/// 採用前に危険が戻れば、同じ句の材料を次の安全な時点まで保持する。
pub(super) fn cancel_notice(s: &mut Session, id: &str, version: u64, reason: &str) {
    if matches!(reason, "manual_interrupt" | "player_requested_quiet" | "new_player_input")
        && clear_for_resume(s)
    {
        resume(s, id, version);
    } else if let Some(w) = s.haiku.workshop.as_mut().filter(|w| w.hud_id == id && w.version == version) {
        w.recovery.generating = false;
    }
}

impl Dialogue {
    /// 同じ句・版の案内を一回予約し、生成結果の採用までは中断表示を維持する。
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
        let ended_by_kill = s.haiku.workshop.as_ref().is_some_and(|w| w.recovery.ended_by_last_kill);
        if s.foreground.combat_active && !ended_by_kill || !clear_for_resume(s)
            || s.warning.is_some()
            || s.pending_warning.is_some()
            || s.cancel.is_some() && !super::workshop_reply::paused(s)
            || s.assist_pending.is_some()
            || s.deferred_input.is_some()
            || s.haiku.active.is_some()
            || s.combat_input.is_some()
        {
            return;
        }
        let Some(w) = s.haiku.workshop.as_mut().filter(|w| {
            w.open && w.combat_paused() && !w.recovery.generating
        }) else {
            return;
        };
        let reason = w.recovery.reason.unwrap_or(Reason::Safe);
        let mut speech = Speech::new("workshop_resume", "");
        speech.delivery = Delivery::PlayerReply;
        speech.scope = Scope::Workshop {
            id: w.hud_id.clone(),
            version: w.version,
        };
        w.recovery.generating = true;
        tracing::info!(
            event = "workshop_combat_resume",
            session_id = sid,
            workshop_id = w.hud_id,
            ?reason
        );
        super::environment_runtime::prepare_workshop_resume(s, &mut speech);
        self.spawn_actions(d, jobs, sid, vec![speech], None);
    }
}
