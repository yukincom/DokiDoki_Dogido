//! パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
//! 音声入口の叫声・STT文脈。叫声は診断へだけ記録し、会話や句の操作へ流さない。
use super::{Data, Dialogue, Session};
use crate::{events::GameEvent, vocalization};
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
                    && (e.observed_at.with_timezone(&Utc) - self.at)
                        .num_milliseconds()
                        .abs()
                        <= 5000
            })
            .map_or(vocalization::UNKNOWN, vocalization::observed_note)
    }
}

impl Dialogue {
    pub(super) fn accept_vocalization(&self, d: &mut Data, sid: &str, text: &str) -> Value {
        if !super::reply_queue::playback_started(d, sid) {
            Self::cancel_chat(d, sid, "voice_vocalization");
            Self::cancel_haiku(d, sid, "voice_vocalization");
            Self::cancel_warning(d, sid, "voice_vocalization");
        }
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

    /// 現在観測を持つ接続が一つのときだけ録音先を返す読み取り専用投影。
    /// 退出済みの登録は残っていても数えず、複数の有効接続がある場合は選ばない。
    pub fn voice_context(&self) -> Value {
        let d = self.data.lock().unwrap();
        let disabled = json!({"prompt_mode":"normal", "session_id":null,"voice_enabled":false,"voice_generation":null});
        if d.stopped {
            return disabled;
        }
        // 入り直したワールドの観測だけを、分類受付と同じ有効性で選ぶ。
        // voice offの接続も候補に数え、別プレイヤーへ録音先を付け替えない。
        let mut current = d.sessions.iter().filter_map(|(sid, s)| {
            super::request_runtime::Binding::capture(s).map(|binding| (sid, s, binding))
        });
        let Some((sid, s, binding)) = current.next() else {
            return disabled;
        };
        if current.next().is_some() {
            return disabled;
        }
        let workshop = binding.mode == crate::request_classifier::Mode::Senryu;
        json!({"prompt_mode":if workshop {"haiku_workshop"} else {"normal"}, "session_id":sid,
            "voice_enabled":!s.delivery.enabled || s.delivery.voice_enabled,
            "voice_generation":super::request_runtime::voice_generation(s)})
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

    fn observe_for_voice(dialogue: &Dialogue, sid: &str, age_seconds: u64) {
        let mut data = dialogue.data.lock().unwrap();
        let session = data.sessions.get_mut(sid).unwrap();
        session.latest = Some(event(Utc::now()));
        session.received = Some(Instant::now() - Duration::from_secs(age_seconds));
    }

    #[tokio::test]
    async fn voice_rejoins_current_world_without_reusing_old_recording() {
        let dialogue = Dialogue::new(super::super::DialogueConfig::default()).unwrap();
        dialogue.register("old", "試験", false);
        dialogue.enable_text_delivery("old", Some(true));
        observe_for_voice(&dialogue, "old", 0);
        let old = dialogue.voice_context();
        assert_eq!(old["voice_enabled"], true);
        observe_for_voice(&dialogue, "old", 11);
        dialogue.register("new", "試験", false);
        dialogue.enable_text_delivery("new", Some(true));
        assert_eq!(dialogue.voice_context()["session_id"], Value::Null);
        observe_for_voice(&dialogue, "new", 0);
        let current = dialogue.voice_context();
        assert_eq!(current["session_id"], "new");
        assert_eq!(current["voice_enabled"], true);
        assert_ne!(current["voice_generation"], old["voice_generation"]);
        for sid in ["old", "new"] {
            assert_eq!(
                dialogue.submit_voice(Some(sid), "うおおお", old["voice_generation"].as_str())["reason"],
                "stale_voice_generation"
            );
        }
        {
            let data = dialogue.data.lock().unwrap();
            assert_eq!(data.sessions.len(), 2);
            assert!(
                data.sessions
                    .values()
                    .all(|s| s.delivery.voice_inputs.is_empty())
            );
        }
        assert_eq!(
            dialogue.submit_voice(
                Some("new"),
                "うおおお",
                current["voice_generation"].as_str()
            )["accepted"],
            true
        );
        assert_eq!(
            dialogue.data.lock().unwrap().sessions["new"]
                .delivery
                .voice_inputs
                .len(),
            1
        );
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn voice_does_not_choose_between_two_current_worlds_even_if_one_is_muted() {
        let dialogue = Dialogue::new(super::super::DialogueConfig::default()).unwrap();
        for sid in ["a", "b"] {
            dialogue.register(sid, "試験", false);
            dialogue.enable_text_delivery(sid, Some(sid == "a"));
            observe_for_voice(&dialogue, sid, 0);
        }
        assert_eq!(dialogue.voice_context()["session_id"], Value::Null);
        observe_for_voice(&dialogue, "a", 11);
        let muted = dialogue.voice_context();
        assert_eq!(muted["session_id"], "b");
        assert_eq!(muted["voice_enabled"], false);
        dialogue.set_voice_enabled("b", true);
        assert_eq!(dialogue.voice_context()["voice_enabled"], true);
        dialogue.shutdown().await;
        assert_eq!(dialogue.voice_context()["voice_enabled"], false);
    }

    #[tokio::test]
    async fn voice_requires_recent_observed_time_as_well_as_recent_receipt() {
        let dialogue = Dialogue::new(super::super::DialogueConfig::default()).unwrap();
        dialogue.register("s", "試験", false);
        observe_for_voice(&dialogue, "s", 0);
        dialogue
            .data
            .lock()
            .unwrap()
            .sessions
            .get_mut("s")
            .unwrap()
            .latest = Some(event(Utc::now() - chrono::Duration::seconds(11)));
        assert_eq!(dialogue.voice_context()["voice_enabled"], false);
        dialogue.shutdown().await;
    }
}
