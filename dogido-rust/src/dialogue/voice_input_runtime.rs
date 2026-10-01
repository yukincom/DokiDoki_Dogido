//! 音声入口の叫声・STT文脈。叫声は診断へだけ記録し、会話や句の操作へ流さない。
use super::{Data, Dialogue, Session, observation_fresh};
use crate::{
    events::{EventTime, GameEvent},
    vocalization,
};
use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use std::time::{Duration, Instant};

pub(super) struct Pending {
    received: Instant,
    at: DateTime<Utc>,
}
impl Pending {
    fn new() -> Self {
        Self {
            received: Instant::now(),
            at: Utc::now(),
        }
    }
    fn note(&self, event: Option<&GameEvent>) -> &'static str {
        event
            .filter(|e| {
                self.received.elapsed() <= Duration::from_secs(5)
                    && matches!(&e.observed_at, EventTime::Aware(at)
                    if (at.with_timezone(&Utc) - self.at).num_milliseconds().abs() <= 5000)
            })
            .map_or(vocalization::UNKNOWN, vocalization::observed_note)
    }
}

impl Dialogue {
    pub(super) fn accept_vocalization(&self, d: &mut Data, sid: &str, text: &str) -> Value {
        Self::cancel_chat(d, sid, "voice_vocalization");
        Self::cancel_haiku(d, sid, "voice_vocalization");
        Self::cancel_warning(d, sid, "voice_vocalization");
        Self::cancel_light(d, sid);
        let s = d.sessions.get_mut(sid).unwrap();
        s.history.begin_danger();
        s.pending_vocalization = Some(Pending::new());
        let now = self.clock.elapsed().as_millis() as u64;
        s.last_player_input = Some(now);
        s.ambient.note_player_input(now);
        // 観測を持たない試験sessionでは原因不明だけを記録して通常の保持期限へ。
        if s.preview {
            self.resolve_vocalization(s, None);
        }
        d.revision += 1;
        tracing::info!(
            event = "player_vocalization",
            session_id = sid,
            recognized_text = text
        );
        json!({"accepted":true, "session_id":sid, "reason":"situation_vocalization"})
    }

    pub(super) fn resolve_vocalization(&self, s: &mut Session, event: Option<&GameEvent>) -> bool {
        if event.is_none()
            && !s.preview
            && s.pending_vocalization
                .as_ref()
                .is_some_and(|p| p.received.elapsed() <= Duration::from_secs(5))
        {
            return false;
        }
        let Some(pending) = s.pending_vocalization.take() else {
            return false;
        };
        let note = pending.note(event);
        s.history.note_situation(note);
        if !s.foreground.combat_active {
            s.history.end_danger(
                self.config
                    .combat
                    .ms("conversation_post_danger_player_turns"),
            );
        }
        tracing::info!(event = "voice_situation", note);
        true
    }

    /// 読み取り専用。観測が失効したsessionや戦闘中の句へSTTを寄せない。
    pub fn voice_context(&self) -> Value {
        let d = self.data.lock().unwrap();
        if d.stopped || d.sessions.len() != 1 {
            return json!({"prompt_mode":"normal", "session_id":null});
        }
        let (sid, s) = d.sessions.iter().next().unwrap();
        let workshop = observation_fresh(s)
            && super::workshop_combat_input::allowed(s)
            && s.haiku
                .workshop
                .as_ref()
                .is_some_and(|w| w.is_open() && !w.combat_paused());
        json!({"prompt_mode":if workshop {"haiku_workshop"} else {"normal"}, "session_id":sid})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(at: DateTime<Utc>) -> GameEvent {
        GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"test",
            "observed_at":at,"event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "visual_threats":[{"type":"zombie","distance":4}]}))
        .unwrap()
    }

    #[test]
    fn only_nearby_observation_can_explain_vocalization() {
        let mut p = Pending::new();
        assert!(p.note(Some(&event(p.at))).contains("敵対モブ"));
        for offset in [-5001, 5001] {
            assert_eq!(
                p.note(Some(&event(p.at + chrono::Duration::milliseconds(offset)))),
                vocalization::UNKNOWN
            );
        }
        p.received -= Duration::from_millis(5001);
        assert_eq!(p.note(Some(&event(p.at))), vocalization::UNKNOWN);
        assert_eq!(p.note(None), vocalization::UNKNOWN);
    }

    #[tokio::test]
    async fn missing_observation_expires_without_turn_or_foreground_change() {
        let dialogue = Dialogue::new(super::super::DialogueConfig::default()).unwrap();
        dialogue.register("s", "試験", false);
        {
            let mut d = dialogue.data.lock().unwrap();
            dialogue.accept_vocalization(&mut d, "s", "うおおお");
            let s = d.sessions.get_mut("s").unwrap();
            assert!(!dialogue.resolve_vocalization(s, None));
            s.pending_vocalization.as_mut().unwrap().received -= Duration::from_secs(6);
            assert!(dialogue.resolve_vocalization(s, None));
            assert_eq!(s.history.situation_lines(), [vocalization::UNKNOWN]);
            assert_eq!(s.history.retention_status()["remaining_player_turns"], 3);
            assert!(!s.foreground.combat_active);
            assert!(s.history.rows().is_empty());
            assert!(d.rows.is_empty());
        }
        dialogue.shutdown().await;
    }
}
