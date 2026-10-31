//! パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
//! 画面へ実際に挿入した返答の確認。音声の再生状態とは独立して、一度だけ履歴へ反映する。
//! 入力・生成・句の操作は既存経路を使い、ここは配送先と表示済みの証拠だけを所有する。
use super::{Data, Dialogue, DialogueConfig, bridge, id};
use serde_json::{Value, json};
use std::{collections::VecDeque, time::Duration};
use tokio::sync::watch;

#[cfg(test)]
mod tests;

#[derive(Clone)]
pub(super) enum Kind {
    Reply { epoch: u64, result: Value },
    Warning,
    Haiku { part: String },
}

#[derive(Clone)]
struct Pending {
    id: String,
    turn: String,
    generation: String,
    kind: Kind,
}

pub(super) struct State {
    pub enabled: bool,
    pub voice_enabled: bool,
    pub generation: String,
    pub voice_inputs: VecDeque<Value>,
    pending: VecDeque<Pending>,
}

impl State {
    /// 同じturnを新しいepochで回答し直す前に、旧回答の表示ACK宛先を取り除く。
    pub(super) fn forget_reply(&mut self, turn: &str) {
        self.pending.retain(|p| p.turn != turn);
    }

    /// 保留中の旧ACKを失効させ、再開時の再公開に必要な配送内容だけを返す。
    pub(super) fn suspend_reply(&mut self, turn: &str) -> Option<Kind> {
        let kind = self
            .pending
            .iter()
            .find(|p| p.turn == turn)
            .map(|p| p.kind.clone());
        self.forget_reply(turn);
        kind
    }

    pub fn new(voice_enabled: bool) -> Self {
        Self {
            enabled: false,
            voice_enabled,
            generation: id("voice"),
            voice_inputs: VecDeque::new(),
            pending: VecDeque::new(),
        }
    }
}

/// 音声受付と診断が共有する読み取り検査。呼出側が最終受付のdataロックを所有する。
pub(super) fn voice_rejection(s: &super::Session, generation: Option<&str>) -> Option<Value> {
    if s.delivery.enabled && !s.delivery.voice_enabled {
        return Some(json!({"accepted":false,"reason":"voice_disabled"}));
    }
    if generation != Some(super::request_runtime::voice_generation(s).as_str()) {
        return Some(json!({"accepted":false,"reason":"stale_voice_generation"}));
    }
    None
}

impl Dialogue {
    /// 外部から受けた認識原文を一度だけ表示へ渡す。再振り分けや会話履歴とは別の有界な写し。
    pub(super) fn record_voice_input(&self, selected: Option<&str>, text: &str, source: &str) {
        if source != "voice" || text.trim().is_empty() || text.chars().count() > 1000 {
            return;
        }
        let mut d = self.data.lock().unwrap();
        Self::record_voice_input_locked(&mut d, selected, text, source);
    }

    /// 世代照合後の同じlock内で表示原文を登録する。古いSTTをMinecraftへも漏らさない。
    pub(super) fn record_voice_input_locked(
        d: &mut super::Data,
        selected: Option<&str>,
        text: &str,
        source: &str,
    ) {
        if d.stopped || source != "voice" || text.trim().is_empty() || text.chars().count() > 1000 {
            return;
        }
        let sid = selected
            .map(str::to_owned)
            .or_else(|| (d.sessions.len() == 1).then(|| d.sessions.keys().next().unwrap().clone()));
        let Some(s) = sid.as_deref().and_then(|sid| d.sessions.get_mut(sid)) else {
            return;
        };
        if !s.delivery.enabled || !s.delivery.voice_enabled {
            return;
        }
        if s.delivery.voice_inputs.len() == 200 {
            s.delivery.voice_inputs.pop_front();
        }
        s.delivery
            .voice_inputs
            .push_back(json!({"input_id":id("recognized"),"text":text}));
        d.revision += 1;
    }

    /// 登録済みの表示クライアントだけが、表示確認を送る接続として参加する。
    pub fn enable_text_delivery(&self, sid: &str, voice_enabled: Option<bool>) {
        let mut d = self.data.lock().unwrap();
        if let Some(s) = d.sessions.get_mut(sid) {
            s.delivery.enabled = true;
            if let Some(enabled) = voice_enabled {
                s.delivery.voice_enabled = enabled;
            }
            d.revision += 1;
        }
    }

    pub fn set_voice_enabled(&self, sid: &str, enabled: bool) -> Value {
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return json!({"accepted":false,"reason":"server_stopping"});
        }
        let Some(s) = d.sessions.get(sid) else {
            return json!({"accepted":false,"reason":"unknown_session_id"});
        };
        if !s.delivery.enabled {
            return json!({"accepted":false,"reason":"text_display_required"});
        }
        if s.delivery.voice_enabled != enabled {
            // 同じ切替を再送しても、次の会話や新しく始まった録音を取り消さない。
            self.cancel_reply_queue(&mut d, sid, "voice_mode_changed");
            Self::cancel_haiku(&mut d, sid, "voice_mode_changed");
            Self::cancel_chat(&mut d, sid, "voice_mode_changed");
            Self::cancel_combat_input(&mut d, sid);
            Self::cancel_warning(&mut d, sid, "voice_mode_changed");
            Self::cancel_assist(&mut d, sid);
            Self::cancel_light(&mut d, sid);
            let s = d.sessions.get_mut(sid).unwrap();
            s.delivery.voice_enabled = enabled;
            s.delivery.generation = id("voice");
            s.delivery.pending.clear();
            s.input_generation = s.input_generation.wrapping_add(1);
            s.pending_vocalization = None;
            s.deferred_input = None;
            d.revision += 1;
        }
        let s = &d.sessions[sid];
        json!({"accepted":true,"voice_enabled":s.delivery.voice_enabled,"voice_generation":super::request_runtime::voice_generation(s)})
    }

    /// actor内の入力受付直前に使う。切替前に認識した音声を、新しい有効期間へ持ち越さない。
    pub fn validate_voice_input(
        &self,
        selected: Option<&str>,
        generation: Option<&str>,
    ) -> Option<Value> {
        let d = self.data.lock().unwrap();
        let sid = selected
            .map(str::to_owned)
            .or_else(|| (d.sessions.len() == 1).then(|| d.sessions.keys().next().unwrap().clone()));
        let s = sid.as_deref().and_then(|sid| d.sessions.get(sid))?;
        voice_rejection(s, generation)
    }

    pub(super) fn output_config(&self, sid: &str) -> DialogueConfig {
        let mut config = self.config.clone();
        if let Some(s) = self
            .data
            .lock()
            .unwrap()
            .sessions
            .get(sid)
            .filter(|s| s.delivery.enabled)
        {
            config.audio_enabled = s.delivery.voice_enabled;
        }
        config
    }

    pub(super) fn publish_text(d: &mut Data, sid: &str, turn: &str, kind: Kind) -> Option<String> {
        let s = d.sessions.get(sid)?;
        if !s.delivery.enabled && !s.text_workshop {
            return None;
        }
        let row = d
            .rows
            .iter_mut()
            .find(|r| r["session_id"] == sid && r["turn_id"] == turn)?;
        if row["text"]
            .as_str()
            .is_none_or(|text| text.trim().is_empty())
            || row["playback_status"] == "cancelled"
        {
            return None;
        }
        if let Some(display_id) = row["display_id"].as_str() {
            return Some(display_id.into());
        }
        let display_id = id("display");
        row["display_id"] = display_id.clone().into();
        row["text_displayed"] = false.into();
        row["output_mode"] = if s.delivery.voice_enabled && !s.text_workshop {
            "both"
        } else {
            "text"
        }
        .into();
        let pending = Pending {
            id: display_id.clone(),
            turn: turn.into(),
            generation: s.delivery.generation.clone(),
            kind,
        };
        let s = d.sessions.get_mut(sid).unwrap();
        s.delivery
            .pending
            .retain(|p| d.rows.iter().any(|r| r["turn_id"] == p.turn));
        while s.delivery.pending.len() >= 200 {
            s.delivery.pending.pop_front();
        }
        s.delivery.pending.push_back(pending);
        d.revision += 1;
        Some(display_id)
    }

    /// session・公開済みID・現在の返答を照合する。本文や操作をクライアントから受け取らない。
    pub fn acknowledge_display(&self, sid: &str, display_ids: &[String]) -> Value {
        if display_ids.len() > 200 {
            return json!({"accepted":false,"reason":"too_many_display_ids"});
        }
        let mut d = self.data.lock().unwrap();
        let mut displayed = Vec::new();
        let mut rejected = Vec::new();
        for display_id in display_ids {
            let pending = d
                .sessions
                .get(sid)
                .and_then(|s| {
                    s.delivery
                        .pending
                        .iter()
                        .find(|p| p.id == *display_id && p.generation == s.delivery.generation)
                })
                .cloned();
            let Some(pending) = pending.filter(|_| !d.stopped) else {
                rejected.push(display_id);
                continue;
            };
            let row = d.rows.iter().find(|r| {
                r["session_id"] == sid
                    && r["turn_id"] == pending.turn
                    && r["display_id"] == *display_id
            });
            let Some(row) = row else {
                rejected.push(display_id);
                continue;
            };
            if row["text_displayed"] == true {
                displayed.push(display_id);
                continue;
            }
            if row["playback_status"] == "cancelled" {
                rejected.push(display_id);
                continue;
            }
            let accepted = match &pending.kind {
                Kind::Reply { epoch, result } => {
                    self.confirm_reply_delivery(&mut d, sid, &pending.turn, *epoch, result, true)
                }
                Kind::Warning => self.confirm_warning_delivery(&mut d, sid, &pending.turn, true),
                Kind::Haiku { part } => {
                    self.confirm_haiku_delivery(&mut d, sid, &pending.turn, part, true)
                }
            };
            if accepted {
                if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == pending.turn) {
                    row["text_displayed"] = true.into();
                    row["text_displayed_at"] = chrono::Utc::now().to_rfc3339().into();
                }
                d.revision += 1;
                displayed.push(display_id);
            } else {
                rejected.push(display_id);
            }
        }
        json!({"accepted":true,"displayed":displayed,"rejected":rejected})
    }

    /// 文字だけの配送では表示確認を待つ。未表示を再生済みにせず、切断時も待ち続けない。
    pub(super) async fn wait_for_text(
        &self,
        sid: &str,
        turn: &str,
        cancel: &mut watch::Receiver<bool>,
    ) -> bool {
        let mut deadline = tokio::time::Instant::now() + Duration::from_secs(5);
        let mut display = Value::Null;
        loop {
            {
                let d = self.data.lock().unwrap();
                let Some(s) = d.sessions.get(sid) else {
                    return false;
                };
                if !s.delivery.enabled {
                    return false;
                }
                let Some(row) = d
                    .rows
                    .iter()
                    .find(|r| r["session_id"] == sid && r["turn_id"] == turn)
                else {
                    return false;
                };
                if row["text_displayed"] == true {
                    return true;
                }
                if super::workshop_reply::paused(s) || row["display_id"] != display {
                    deadline = tokio::time::Instant::now() + Duration::from_secs(5);
                    display = row["display_id"].clone();
                } else if tokio::time::Instant::now() >= deadline {
                    return false;
                }
                if row["playback_status"] == "cancelled" || d.stopped {
                    return false;
                }
            }
            tokio::select! { biased;
                _ = bridge::cancelled(cancel) => return false,
                _ = tokio::time::sleep(Duration::from_millis(25)) => {},
            }
        }
    }
}
