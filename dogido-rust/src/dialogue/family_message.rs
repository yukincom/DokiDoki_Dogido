//! 共有Macから届いた私信を、安全に話せる一人のゲーム接続へ一度だけ配送する。
//! 呼出元がHTTPの認証・文字数・ID形式を検証し、この受付は接続・観測・配送枠を照合する。
use super::{Dialogue, Session, observation_fresh, warnings, workshop_focus};
use crate::combat::model::{Delivery, Mode, Speech};
use serde_json::{Value, json};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};

pub(super) const KIND: &str = "family_message";

fn ready(s: &Session) -> bool {
    s.delivery.enabled
        && s.chat_allowed
        && s.mode == Mode::Normal
        && !s.foreground.game_paused
        && !s.foreground.combat_active
        && workshop_focus::quiet_observation(s)
        && s.latest
            .iter()
            .chain(&s.audio_latest)
            .all(|e| warnings::interruption_reason(e).is_none())
        && !s.haiku.foreground()
        && s.request_classification.pending.is_none()
        && s.cancel.is_none()
        && s.warning.is_none()
        && s.pending_warning.is_none()
        && s.reply_queue.is_empty()
        && s.reply_checked.is_none()
        && s.pending_replies.is_empty()
        && s.reply_outputs.is_empty()
        && s.assist_pending.is_none()
        && s.deferred_input.is_none()
}

impl Dialogue {
    pub(crate) fn family_message<'a>(
        self: &Arc<Self>,
        games: impl Iterator<Item = &'a str>,
        message: Option<(&str, &str)>,
    ) -> Value {
        let message = message.map(|(id, text)| {
            (
                uuid::Uuid::parse_str(id).expect("validated family message ID"),
                text,
            )
        });
        let mut jobs = self.jobs.lock().unwrap();
        jobs.retain(|j| !j.is_finished());
        let mut data = self.data.lock().unwrap();
        data.family_messages
            .retain(|_, (_, at)| at.elapsed() < Duration::from_secs(900));
        if let Some((id, _)) = message
            && let Some((turn, _)) = data.family_messages.get(&id)
        {
            // 音声が失敗してもFabricの表示ACKが遅れて届くことがある。表示可能なら結果を待つ。
            let status = match data.rows.iter().find(|row| row["turn_id"] == turn.as_str()) {
                Some(row) if row["delivery_confirmed"] == true || row["text_displayed"] == true => {
                    "delivered"
                }
                Some(row) if row["playback_status"] == "cancelled" || data.stopped => "failed",
                Some(row)
                    if row["session_id"]
                        .as_str()
                        .is_none_or(|sid| !data.sessions.contains_key(sid)) =>
                {
                    "failed"
                }
                Some(_) => "queued",
                None => "unknown",
            };
            return json!({"accepted":true,"duplicate":true,"status":status});
        }
        let unavailable = |reason: &str| json!({"ready":false,"accepted":false,"reason":reason});
        if data.stopped {
            return unavailable("server_stopping");
        }
        // 安全・音声ON等で先に絞ると、二人目が戦闘中のときに誤宛先へ届いてしまう。
        let mut games = games.filter_map(|id| {
            let s = data.sessions.get(id)?;
            (!s.preview && !s.text_workshop && observation_fresh(s)).then_some((id, s))
        });
        let Some((sid, session)) = games.next() else {
            return unavailable("no_active_game");
        };
        if games.next().is_some() {
            return unavailable("ambiguous_game");
        }
        if !ready(session) || jobs.len() >= 16 {
            return unavailable("game_busy");
        }
        let Some((id, text)) = message else {
            return json!({"ready":true});
        };
        let mut speech = Speech::new(KIND, text);
        speech.delivery = Delivery::PlayerReply;
        self.spawn_actions(&mut data, &mut jobs, sid, vec![speech], None);
        let turn = data.sessions[sid].warning.as_ref().unwrap().turn.clone();
        data.family_messages
            .insert(id, (turn.clone(), Instant::now()));
        // ゲーム画面へは受付と同時に公開する。表示済みの証拠はFabricのACKが所有する。
        Self::publish_text(&mut data, sid, &turn, super::text_delivery::Kind::Warning);
        json!({"accepted":true,"duplicate":false,"status":"queued"})
    }
}
