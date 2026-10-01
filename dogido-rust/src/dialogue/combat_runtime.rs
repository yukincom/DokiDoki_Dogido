use super::{Data, Dialogue, warnings};
use crate::combat::{core::Decision, model::Speech};
use std::sync::Arc;
use tokio::task::JoinHandle;

impl Dialogue {
    pub(super) fn refresh_combat_audio(&self, d: &mut Data, sid: &str) {
        let s = &d.sessions[sid];
        let query = s
            .warning
            .as_ref()
            .filter(|w| warnings::is_query(&w.actions))
            .and_then(|w| w.input.clone());
        let query_actions = query.as_deref().and_then(|text| {
            s.latest
                .as_ref()
                .filter(|_| super::observation_fresh(s))
                .map(|_| {
                    vec![warnings::answer_fixed_query(
                        s,
                        text,
                        s.warning.as_ref().unwrap().actions[0].kind,
                        self.clock.elapsed().as_millis() as u64,
                        &self.config.combat,
                    )]
                })
        });
        let query_changed = query.is_some()
            && s.warning.as_ref().is_some_and(|w| {
                !w.started
                    && query_actions
                        .as_ref()
                        .is_none_or(|a| a[0].text != w.actions[0].text)
            });
        let invalid = query_changed
            || s.warning.as_ref().is_some_and(|w| {
                !warnings::active_applicable(w, s, &self.config.warnings, &self.config.combat)
            });
        let refreshed = if query_changed {
            query_actions
        } else if invalid {
            s.warning.as_ref().and_then(|w| {
                warnings::refresh(
                    &w.actions,
                    s,
                    &self.config.warnings,
                    &self.config.combat,
                    w.started,
                )
            })
        } else {
            None
        };
        if invalid {
            tracing::info!(event="warning_invalidated",session_id=sid,
                source_event=?s.latest.as_ref().map(|e|e.event.name),
                visual_count=s.latest.as_ref().map_or(0,|e|e.visual_threats.len()),
                recent=super::observation_fresh(s));
            // 未配送の質問は、先行する警告の無効化で捨てない。
            let pending_question = d
                .sessions
                .get_mut(sid)
                .filter(|s| s.pending_input.is_some())
                .and_then(|s| {
                    s.pending_warning
                        .take()
                        .map(|a| (a, s.pending_input.take()))
                });
            Self::cancel_warning(
                d,
                sid,
                if super::observation_fresh(&d.sessions[sid]) {
                    "target_changed_or_gone"
                } else {
                    "stale_observation"
                },
            );
            let s = d.sessions.get_mut(sid).unwrap();
            if let Some((actions, input)) = pending_question {
                s.pending_warning = Some(actions);
                s.pending_input = input;
            } else {
                s.pending_warning = refreshed;
                s.pending_input = query;
            }
        }
        let s = d.sessions.get_mut(sid).unwrap();
        if let Some(pending) = s.pending_warning.take() {
            s.pending_warning = if warnings::is_query(&pending) && s.pending_input.is_some() {
                s.latest
                    .as_ref()
                    .filter(|_| super::observation_fresh(s))
                    .map(|_| {
                        vec![warnings::answer_fixed_query(
                            s,
                            s.pending_input.as_deref().unwrap(),
                            pending[0].kind,
                            self.clock.elapsed().as_millis() as u64,
                            &self.config.combat,
                        )]
                    })
            } else {
                warnings::refresh(
                    &pending,
                    s,
                    &self.config.warnings,
                    &self.config.combat,
                    false,
                )
            };
        }
    }

    pub(super) fn apply_combat_decision(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        mut decision: Decision,
        input: Option<&str>,
        reason: Option<&str>,
    ) {
        let s = d.sessions.get_mut(sid).unwrap();
        for note in s.combat.take_notes() {
            let note = note.chars().take(80).collect::<String>();
            if s.combat_digest.back() != Some(&note) {
                if s.combat_digest.len() == 8 {
                    s.combat_digest.pop_front();
                }
                s.combat_digest.push_back(note);
            }
        }
        s.mode = decision.mode;
        s.chat_allowed = decision.chat_allowed;
        if super::workshop_focus::quiet(s) && !decision.dimension_changed {
            // mode/観測は更新し続けるが、環境由来のpanicや予兆では相談を奪わない。
            // 敵離脱後のpause中も同じ。撃破・爆散・安堵の実結果は復帰の前に配送する。
            decision
                .actions
                .retain(|a| super::workshop_focus::combat_completion(a.kind));
            if decision.actions.is_empty() {
                decision.stop_audio = false;
                if super::workshop_focus::owns_input(s) {
                    decision.chat_allowed = true;
                }
            }
        }
        // 戦闘後の安堵は緊急警告ではない。始めた台詞の後へ回す。
        let defer_relief = s.warning.as_ref().is_some_and(warnings::Active::finishing)
            && !decision.actions.is_empty()
            && decision.actions.iter().all(|a| a.kind == "aftermath");
        if (!decision.chat_allowed && !super::workshop_combat_input::provisional(s))
            || !decision.actions.is_empty()
            || decision.stop_audio
            || decision.dimension_changed
        {
            Self::cancel_haiku(d, sid, reason.unwrap_or("combat_priority"));
            Self::cancel_chat(d, sid, reason.unwrap_or("combat_priority"));
        }
        if decision.dimension_changed || (decision.stop_audio && !defer_relief) {
            Self::cancel_warning(
                d,
                sid,
                if decision.dimension_changed {
                    "dimension_changed"
                } else if !decision.actions.is_empty() {
                    "higher_priority_warning"
                } else {
                    "combat_priority"
                },
            );
        }
        if !decision.actions.is_empty() {
            let protected = d.sessions[sid]
                .warning
                .as_ref()
                .is_some_and(warnings::Active::protected);
            let interrupt = decision.actions.iter().any(|a| a.interrupt);
            if d.sessions[sid].warning.is_some() && (!protected || interrupt) && !defer_relief {
                Self::cancel_warning(d, sid, "higher_priority_warning");
            }
            let s = d.sessions.get_mut(sid).unwrap();
            s.pending_warning = Some(decision.actions);
            s.pending_input = input.map(str::to_owned);
        }
        self.start_pending(d, jobs, sid);
    }
    pub(super) fn start_pending(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
    ) {
        let s = d.sessions.get_mut(sid).unwrap();
        if s.warning.is_some() || jobs.len() >= 16 {
            return;
        }
        let Some(actions) = s.pending_warning.take() else {
            return;
        };
        let input = s.pending_input.take();
        self.spawn_actions(d, jobs, sid, actions, input.as_deref());
    }
    pub(super) fn spawn_actions(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        actions: Vec<Speech>,
        input: Option<&str>,
    ) {
        let (turn, rx) = Self::queue_actions(d, sid, &actions, input);
        let this = self.clone();
        let sid = sid.to_owned();
        jobs.push(tokio::spawn(async move {
            this.run_warning(sid, turn, actions, rx).await;
        }));
    }
}
