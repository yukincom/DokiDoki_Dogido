//! 家庭からの伝言を、ゲームの発話・警告・川柳とは別に表示し、優先音声を受付順に届ける。
//! 本文は宛先の表示と音声のためにだけ保持し、ゲームの履歴・川柳材料に登録しない。
use super::{Data, Dialogue, Session};
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::watch;
use uuid::Uuid;

pub(super) const KIND: &str = "family_message";

pub(super) struct Receipt {
    id: Uuid,
    since: Instant,
    activity: Vec<&'static str>,
    pub(super) row: Value,
}

pub(super) struct AudioQueue {
    pending: VecDeque<Uuid>,
    // セッション終了・音声offで送信端を破棄すると、実行中のplayerと次の伝言を停止する。
    _lifetime: watch::Sender<bool>,
}

fn activity(session: &Session) -> Vec<&'static str> {
    let mut activity = Vec::new();
    if session.foreground.combat_active
        || session.latest.iter().chain(&session.audio_latest).any(|e| {
            e.combat.player_engaged == Some(true) || e.combat.combat_active_hint == Some(true)
        })
    {
        activity.push("combat");
    }
    if session.haiku.active.is_some()
        || session
            .haiku
            .workshop
            .as_ref()
            .is_some_and(|w| w.open && !w.combat_paused())
    {
        activity.push("senryu");
    }
    activity
}

impl Dialogue {
    pub(crate) fn family_message<'a>(
        self: &Arc<Self>,
        mut games: impl Iterator<Item = &'a str>,
        message: Option<(&str, &str)>,
    ) -> Value {
        let message = message.map(|(id, text)| {
            (
                Uuid::parse_str(id).expect("validated family message ID"),
                text,
            )
        });
        let mut jobs = self.jobs.lock().unwrap();
        jobs.retain(|job| !job.is_finished());
        let mut data = self.data.lock().unwrap();
        data.family_messages
            .retain(|r| r.since.elapsed() < Duration::from_secs(900));
        if let Some((id, _)) = message
            && let Some(r) = data.family_messages.iter().find(|r| r.id == id)
        {
            let status = if r.row["delivery_confirmed"] == true {
                "delivered"
            } else if data.stopped
                || r.row["session_id"]
                    .as_str()
                    .and_then(|sid| data.sessions.get(sid))
                    .is_none_or(|s| r.row["session_instance"] != s.instance_id)
            {
                "failed"
            } else {
                "queued"
            };
            return json!({"accepted":true,"duplicate":true,"status":status,"activity":r.activity});
        }
        let unavailable = |reason: &str| json!({"ready":false,"accepted":false,"reason":reason});
        if data.stopped {
            return unavailable("server_stopping");
        }
        // Runtimeが同じ時刻の接続状態を照合済み。複数接続をゲームの忙しさで絞らない。
        let Some(sid) = games.next() else {
            return unavailable("no_active_game");
        };
        if games.next().is_some() {
            return unavailable("ambiguous_game");
        }
        let Some(session) = data.sessions.get(sid) else {
            return unavailable("no_active_game");
        };
        if !session.delivery.enabled {
            return unavailable("delivery_failed");
        }
        let Some((id, text)) = message else {
            return json!({"ready":true});
        };
        let activity = activity(session);
        let voice = session.delivery.voice_enabled;
        let receipt = Receipt {
            id,
            since: Instant::now(),
            activity: activity.clone(),
            row: json!({"utterance_id":super::id("utt"), "turn_id":super::id("family"), "session_id":sid,
                "session_instance":session.instance_id, "display_id":super::id("family-display"),
                "category":"speech", "source":KIND, "text":text, "created_at":chrono::Utc::now(),
                "combat_actions":[], "reference_ids":[], "output_mode":if voice {"both"} else {"text"},
                "playback_status":if voice {"queued"} else {"audio_disabled"}, "text_displayed":false, "delivery_confirmed":false}),
        };
        data.family_messages.push_back(receipt);
        data.revision += 1;
        if voice {
            let queue = &mut data.sessions.get_mut(sid).unwrap().family_audio;
            if let Some(q) = queue {
                q.pending.push_back(id);
            } else {
                let (lifetime, cancel) = watch::channel(false);
                *queue = Some(AudioQueue {
                    pending: VecDeque::from([id]),
                    _lifetime: lifetime,
                });
                jobs.push(tokio::spawn(
                    self.clone().run_family_audio(sid.to_owned(), cancel),
                ));
            }
        }
        json!({"accepted":true,"duplicate":false,"status":"queued","activity":activity})
    }

    async fn run_family_audio(self: Arc<Self>, sid: String, mut cancel: watch::Receiver<bool>) {
        loop {
            let (id, text) = {
                let mut data = self.data.lock().unwrap();
                if cancel.has_changed().is_err() || data.stopped {
                    return;
                }
                let Some(s) = data.sessions.get_mut(&sid) else {
                    return;
                };
                let Some(q) = &mut s.family_audio else { return };
                let Some(id) = q.pending.pop_front() else {
                    s.family_audio = None;
                    return;
                };
                let Some(receipt) = data.family_messages.iter().find(|r| r.id == id) else {
                    continue;
                };
                (id, receipt.row["text"].as_str().unwrap().to_owned())
            };
            let config = self.output_config(&sid);
            let result = self
                .audio
                .speak_family(&config, &text, &mut cancel, || {
                    self.update_family_audio(id, "started");
                })
                .await;
            self.update_family_audio(
                id,
                if result.is_ok() {
                    "completed"
                } else {
                    "audio_failed"
                },
            );
        }
    }

    fn update_family_audio(&self, id: Uuid, status: &str) {
        let mut data = self.data.lock().unwrap();
        if let Some(receipt) = data.family_messages.iter_mut().find(|r| r.id == id) {
            receipt.row["playback_status"] = status.into();
            if status == "completed" {
                receipt.row["delivery_confirmed"] = true.into();
            }
            data.revision += 1;
        }
    }

    /// 表示世代はゲーム接続に属する。音声切替・戦闘・川柳の取消世代を私信に適用しない。
    pub(super) fn acknowledge_family(data: &mut Data, sid: &str, display: &str) -> bool {
        let Some(s) = data.sessions.get(sid) else {
            return false;
        };
        if data.stopped {
            return false;
        }
        if let Some(r) = data.family_messages.iter_mut().find(|r| {
            r.row["display_id"] == display
                && r.row["session_id"] == sid
                && r.row["session_instance"] == s.instance_id
        }) {
            if r.row["text_displayed"] != true {
                r.row["text_displayed"] = true.into();
                r.row["delivery_confirmed"] = true.into();
                r.row["text_displayed_at"] = chrono::Utc::now().to_rfc3339().into();
                data.revision += 1;
            }
            return true;
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::super::workshop_record::tests::fixture;
    use serde_json::json;

    // 開いた相談室とゲームの音声切替を実際に通す。伝言が一件だけ先に表示され、
    // ACKで成功・受付時の川柳状態を返し、句や相談履歴に本文が入らなければ合格。外部音声は使わない。
    #[tokio::test]
    async fn family_message_is_displayed_in_an_open_workshop_and_survives_a_voice_toggle() {
        let fixture = fixture();
        let d = &fixture.d;
        let message = ("10000000000000000000000000000000", "お母さんが呼んでるで。");
        d.enable_text_delivery("s", Some(false));
        let deliver = || d.family_message(std::iter::once("s"), Some(message));
        let first = deliver();
        assert_eq!(first["status"], "queued");
        assert_eq!(first["activity"], json!(["senryu"]));
        assert_eq!(deliver()["duplicate"], true);
        let snapshot = d.snapshot(Some("s"));
        let messages = snapshot["utterances"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|row| row["source"] == "family_message")
            .collect::<Vec<_>>();
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0]["text"], message.1);
        let display = messages[0]["display_id"].as_str().unwrap().to_owned();
        d.set_voice_enabled("s", true);
        d.set_voice_enabled("s", false);
        assert_eq!(
            d.acknowledge_display("s", std::slice::from_ref(&display))["displayed"],
            json!([display])
        );
        assert_eq!(deliver()["status"], "delivered");
        assert_eq!(deliver()["activity"], json!(["senryu"]));
        assert!(
            d.data.lock().unwrap().sessions["s"]
                .haiku
                .workshop
                .as_ref()
                .unwrap()
                .open
        );
        assert!(!d.snapshot(None).to_string().contains(message.1));
        assert!(
            !d.snapshot(Some("s"))["sessions"]
                .to_string()
                .contains(message.1)
        );
        fixture.finish().await;
    }
}
