//! 戦闘へ譲った相談を、受け取った言葉・返答予定・その後の出来事として保持する。
//! 音声の優先制御と句の操作は既存経路が担当し、ここでは未発話を発話済みへ昇格させない。
use super::{Data, Dialogue};
use serde_json::{Value, json};
use std::sync::{
    Mutex,
    atomic::{AtomicBool, Ordering},
};

/// 一回の生成と、その後の出力を分けるためのturn内の返答予定。
/// 戦闘へ譲った後は送信済みの会話生成だけを受け取り、追加の編集生成は始めない。
#[derive(Default)]
pub(super) struct Reply {
    pub waiting_for_model: AtomicBool,
    pub interrupted: AtomicBool,
    intended: Mutex<(String, Value)>,
}

impl Reply {
    pub fn has_received_reply(&self) -> bool {
        let intended = self.intended.lock().unwrap();
        !intended.0.is_empty() || intended.1["action"] == "praise"
    }
    pub fn superseded_result(&self, input: &Value) -> Value {
        let mut result = self.result(input);
        result["reply_delivery"] = "superseded".into();
        result
    }
    pub fn remember(&self, speech: &str, praise: &Value) {
        let mut intended = self.intended.lock().unwrap();
        if !speech.is_empty() {
            intended.0 = speech.into();
        }
        if praise["action"] == "praise" {
            intended.1 = praise.clone();
        }
    }
    pub fn interrupted(&self) -> bool {
        self.interrupted.load(Ordering::Relaxed)
    }
    pub fn result(&self, input: &Value) -> Value {
        let intended = self.intended.lock().unwrap();
        Dialogue::unspoken_workshop_result(input, &intended.0, &intended.1)
    }
}

fn narrative(player: &str, reply: &str) -> String {
    let mut text = format!("プレイヤーが「{player}」と言った。");
    if !reply.is_empty() {
        text.push_str(&format!("\nドギドは「{reply}」と返そうとした。"));
    }
    text.push_str("\n言葉を飲み込んで、戦いに挑んだ。\n川柳の話は戦いが終わってからにしよう。");
    text
}

impl Dialogue {
    /// 発言が属する接続の寿命を確かめる。返答の世代や戦闘状態とは分ける。
    pub(super) fn workshop_input_session_exists(&self, input: &Value) -> bool {
        let d = self.data.lock().unwrap();
        !d.stopped
            && input["session_instance"]
                .as_str()
                .is_some_and(|instance| d.sessions.values().any(|s| s.instance_id == instance))
    }
    /// 戦闘判断が実際に会話音声を止める直前に、その会話だけへ未発話の印を付ける。
    /// 通常の新入力、接続終了、環境の予兆による取消からは呼ばない。
    pub(super) fn mark_workshop_combat_priority(&self, d: &mut Data, sid: &str) {
        let Some(session) = d.sessions.get(sid).filter(|s| s.cancel.is_some()) else {
            return;
        };
        let turn = session.current_turn.clone();
        let Some(row) = d.rows.iter_mut().find(|r| {
            r["session_id"] == sid && r["turn_id"] == turn && r["workshop_id"].is_string()
        }) else {
            return;
        };
        // 文字で既に届いた返答を、後から音声が中断されたことだけで未発話へ戻さない。
        if row["text_displayed"] == true {
            return;
        }
        row["reply_delivery"] = "unspoken_combat".into();
        row["unspoken_text"] = row["text"].clone();
        self.retain_workshop_story(d, sid, &turn);
    }

    /// 音声とは別に保持した本文を、元の入力と元の一句へ結ぶ。
    /// 同じturnの生成が後から終わった場合も一件を補完し、別の句へ持ち越さない。
    pub(super) fn retain_workshop_story(&self, d: &mut Data, sid: &str, turn: &str) {
        let Some(row) = d.rows.iter_mut().find(|r| {
            r["session_id"] == sid
                && r["turn_id"] == turn
                && r["reply_delivery"] == "unspoken_combat"
        }) else {
            return;
        };
        let player = row["player_input_text"].as_str().unwrap_or("");
        let reply = row["unspoken_text"].as_str().unwrap_or("");
        let story = narrative(player, reply);
        let entry = json!({"turn_id":turn,"player_text":player,"dogido_text":"",
            "unspoken_text":reply,"reply_delivery":"unspoken_combat","narrative":story,
            "workshop_version":row["workshop_state_before"]["version"]});
        row["narrative"] = story.clone().into();
        let private = row["workshop_record_private"] == true;
        let wid = row["workshop_id"].clone();
        let instance = row["session_instance"].clone();
        if d.stopped {
            return;
        }
        let Some(session) = d
            .sessions
            .get_mut(sid)
            .filter(|s| instance == s.instance_id)
        else {
            return;
        };
        if private {
            return;
        }
        self.share_story(
            sid,
            session,
            &format!("unspoken:{turn}"),
            wid.as_str(),
            "workshop_unspoken",
            &story,
            json!({"workshop_id":wid,"input_turn_id":turn,"unspoken_text":entry["unspoken_text"]}),
        );
        if let Some(workshop) = session.haiku.workshop.as_mut().filter(|w| wid == w.hud_id) {
            if let Some(saved) = workshop.dialogue.iter_mut().find(|r| r["turn_id"] == turn) {
                *saved = entry;
            } else {
                workshop.dialogue.push_back(entry);
                while workshop.dialogue.len() > 4 {
                    workshop.dialogue.pop_front();
                }
            }
        }
    }

    /// 対象を記録した元turnだけが戦闘による未発話かを確認する。
    /// 新しいturnのepochが進んでも、こちらの発言・本文の記録は取り消さない。
    pub(super) fn workshop_combat_priority(&self, input: &Value) -> bool {
        let d = self.data.lock().unwrap();
        d.rows.iter().any(|r| {
            r["turn_id"] == input["operation_id"]
                && r["session_instance"] == input["session_instance"]
                && r["reply_delivery"] == "unspoken_combat"
        })
    }

    /// 既存の一回の生成で得た本文と評価だけを返す。句操作・追加生成・音声合成はしない。
    pub(super) fn unspoken_workshop_result(input: &Value, speech: &str, praise: &Value) -> Value {
        json!({"text":speech,"spoken_text":"","workshop_action":"unspoken",
            "workshop_id":input["workshop"]["workshop_id"],"workshop_version":input["workshop"]["version"],
            "reply_delivery":"unspoken_combat","unspoken_text":speech,
            "workshop_feedback":praise,"output_suppressed":true,"llm_reports":[]})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{dialogue::DialogueConfig, playback::Status};

    #[tokio::test]
    async fn late_reply_cannot_commit_to_reregistered_session_with_same_id_and_epoch() {
        let mut config = DialogueConfig {
            audio_enabled: false,
            llm_enabled: false,
            ..Default::default()
        };
        config.haiku.enabled = false;
        config.haiku.memory_enabled = false;
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "試験", true);
        let input = {
            let mut data = dialogue.data.lock().unwrap();
            let session = &data.sessions["s"];
            let input = json!({"session_instance":session.instance_id,"operation_id":"old"});
            data.rows.push_back(json!({"session_id":"s","session_instance":input["session_instance"],
                "turn_id":"old","epoch":0,"player_input_text":"この句好きだよ","reply_delivery":"unspoken_combat"}));
            input
        };
        assert!(dialogue.workshop_input_session_exists(&input));
        dialogue.register("s", "試験", true);
        assert!(!dialogue.workshop_input_session_exists(&input));
        assert!(!dialogue.update(
            "s",
            "old",
            0,
            Status::Completed,
            Some(&json!({"text":"うれしいわ。"}))
        ));
        {
            let data = dialogue.data.lock().unwrap();
            assert!(data.sessions["s"].history.completed_pairs().is_empty());
            assert_eq!(data.rows.back().unwrap()["playback_status"], "cancelled");
            assert_eq!(data.rows.back().unwrap()["unspoken_text"], "うれしいわ。");
        }
        dialogue.shutdown().await;
    }
}
