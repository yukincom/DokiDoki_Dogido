//! 雑談とlook_player後の一回生成に、プレイヤーのそばへ行く／留まる二択を添える。
//! 本文の語句は解釈しない。選択を元の入力と身体へ照合し、実到着は既存の呼び寄せACKで決まる。
use super::{Dialogue, Session, body_request_runtime::active};
use crate::{
    body::contracts::Context,
    chat_validation::Outcome,
    types::{GenerationRequest, Role},
};
use anyhow::{Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::atomic::{AtomicBool, Ordering};
use std::{
    sync::{Arc, Weak},
    time::Instant,
};
use tokio::sync::{mpsc, watch};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Choice {
    action: Action,
}
#[derive(Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum Action {
    Recall,
    Stay,
}

/// 先頭のJSONオブジェクトのキーと値を順に読む。閉じていない値は未確定。
/// 本文中の引用・子オブジェクト・配列を探索せず、末尾の補完も使わない。
fn received_action(raw: &str) -> Option<Action> {
    const JSON_SPACE: [char; 4] = [' ', '\n', '\r', '\t'];
    let raw = raw.trim_start();
    let raw = if raw.starts_with("```") {
        raw.split_once('\n')?.1.trim_start()
    } else {
        raw
    };
    let mut rest = raw.strip_prefix('{')?;
    loop {
        let mut key = serde_json::Deserializer::from_str(rest).into_iter::<String>();
        let name = key.next()?.ok()?;
        rest = rest[key.byte_offset()..]
            .trim_start_matches(JSON_SPACE)
            .strip_prefix(':')?
            .trim_start_matches(JSON_SPACE);
        if name == "body_action" {
            // structの配列形式も受けるserdeの既定から、指定のオブジェクトだけに限定する。
            if !rest.starts_with('{') {
                return None;
            }
            return serde_json::Deserializer::from_str(rest)
                .into_iter::<Choice>()
                .next()?
                .ok()
                .map(|v| v.action);
        }
        let mut value = serde_json::Deserializer::from_str(rest).into_iter::<Value>();
        value.next()?.ok()?;
        rest = rest[value.byte_offset()..]
            .trim_start_matches(JSON_SPACE)
            .strip_prefix(',')?;
    }
}

pub(super) fn safe(s: &Session) -> bool {
    active(s)
        && s.chat_allowed
        && !s.foreground.combat_active
        && !s.haiku.foreground()
        && !super::reply_queue::warning_blocks_input(s)
}

#[derive(Clone)]
pub(super) struct Source {
    owner: Weak<Dialogue>,
    sid: String,
    instance: String,
    turn: String,
    epoch: u64,
    generation: u64,
    context: Context,
    look_command: Option<String>,
}

impl Source {
    pub fn new(
        owner: &Arc<Dialogue>,
        s: &Session,
        sid: &str,
        turn: &str,
        context: Context,
        look_command: Option<String>,
    ) -> Self {
        Self {
            owner: Arc::downgrade(owner),
            sid: sid.into(),
            instance: s.instance_id.clone(),
            turn: turn.into(),
            epoch: s.epoch,
            generation: s.input_generation,
            context,
            look_command,
        }
    }

    pub fn ordinary(owner: &Arc<Dialogue>, s: &Session, sid: &str, turn: &str) -> Option<Self> {
        if !safe(s) || !s.body_requests.available() {
            return None;
        }
        let context = s.body_observation.accepted(Instant::now())?.context.clone();
        Some(Self::new(owner, s, sid, turn, context, None))
    }

    pub fn follows_look(&self) -> bool {
        self.look_command.is_some()
    }

    fn executable(&self, s: &Session) -> bool {
        safe(s)
            && s.body_requests.available()
            && s.player_uuid.as_deref() == Some(&self.context.owner_id)
            && s.latest.as_ref().and_then(|e| e.player.dimension.as_ref())
                == Some(&self.context.dimension)
            && s.body_observation
                .accepted(Instant::now())
                .is_some_and(|b| b.context == self.context)
    }

    fn current(&self, s: &Session) -> bool {
        s.instance_id == self.instance
            && s.epoch == self.epoch
            && s.current_turn == self.turn
            && s.input_generation == self.generation
    }

    /// 固定返答・知識照会などの分岐を終えた生成直前、今も実行可能なら同じJSONに選択肢を添える。
    pub fn offer(&self, request: &mut GenerationRequest) -> bool {
        let Some(owner) = self.owner.upgrade() else {
            return false;
        };
        let d = owner.data.lock().unwrap();
        if d.stopped
            || !d
                .sessions
                .get(&self.sid)
                .is_some_and(|s| self.current(s) && self.executable(s))
        {
            return false;
        }
        let Some(system) = request.messages.iter_mut().find(|m| m.role == Role::System) else {
            return false;
        };
        let old = crate::companion_prompt::choice_contract();
        if !system.content.contains(old) {
            return false;
        }
        system.content =
            system
                .content
                .replacen(old, crate::companion_prompt::player_approach_contract(), 1);
        true
    }

    /// 同じstreamで一度だけ、受信済みの二択を本文生成中に適用する。
    /// 音声なしも同じ応答を読み、画像添付・世代・取消・命令の期限は共通のまま。
    pub async fn generate(
        &self,
        llm: &crate::llm::RigLlm,
        request: &GenerationRequest,
        image: Option<&crate::chat_image::Attachment>,
        deltas: Option<mpsc::Sender<String>>,
        cancel: &watch::Receiver<bool>,
        speech_ready: Option<&AtomicBool>,
    ) -> Result<crate::types::GenerationReport> {
        let mut selected = false;
        llm.generate_stream(request, image, deltas, |text| {
            if !selected && let Some(action) = received_action(text) {
                let rejected = self.select(Some(action), cancel)?;
                selected = true;
                if let Some(ready) = speech_ready {
                    ready.store(!rejected, Ordering::Release);
                }
            }
            Ok(())
        })
        .await
    }

    /// 生成の終端では台詞を仕上げる。未確定の操作を全文補完から発行しない。
    pub fn finish(&self, output: &mut Outcome, cancel: &watch::Receiver<bool>) -> Result<()> {
        if self.select(None, cancel)? {
            output.text = "今はそっちへ行けへんわ。".into();
            output.action = "speak";
            output.status = "accepted";
            output.reason = None;
        }
        Ok(())
    }

    /// 既存台帳への発行・重複抑止はここだけで行う。戻り値は発行段階での拒否。
    fn select(&self, action: Option<Action>, cancel: &watch::Receiver<bool>) -> Result<bool> {
        let owner = self
            .owner
            .upgrade()
            .ok_or_else(|| anyhow::anyhow!("player_approach_owner_ended"))?;
        let mut d = owner.data.lock().unwrap();
        let row = d.rows.iter().position(|row| {
            row["turn_id"] == self.turn
                && row["session_id"] == self.sid
                && row["session_instance"] == self.instance
                && row["epoch"] == self.epoch
                && self
                    .look_command
                    .as_ref()
                    .is_none_or(|command| row["body_request_command_id"].as_str() == Some(command))
        });
        ensure!(
            !*cancel.borrow() && cancel.has_changed().is_ok() && !d.stopped && row.is_some(),
            "player_approach_input_changed"
        );
        let row = row.unwrap();
        let previous = &d.rows[row];
        let rejection = (!previous["player_approach_status"].is_null()).then(|| {
            previous["player_approach_status"] == "rejected"
                && previous["player_approach_command_id"].is_null()
        });
        let original = previous["player_input_text"]
            .as_str()
            .unwrap_or("")
            .to_owned();
        let s = d
            .sessions
            .get_mut(&self.sid)
            .ok_or_else(|| anyhow::anyhow!("player_approach_session_ended"))?;
        if !self.current(s) {
            if s.epoch == self.epoch
                && s.current_turn == self.turn
                && s.instance_id == self.instance
                && let Some(cancel) = s.cancel.as_ref()
            {
                let _ = cancel.send(true);
            }
            // 先行再生済みの旧turnは台詞だけを完走できる。新入力に移動を付け替えない。
            ensure!(
                self.look_command.is_none()
                    && super::reply_output::current(s, &self.turn, self.epoch),
                "player_approach_input_changed"
            );
            return Ok(false);
        }
        if let Some(rejected) = rejection {
            return Ok(rejected);
        }
        let mut command = Value::Null;
        let mut detail = String::new();
        let status = match action {
            None => "not_selected",
            Some(Action::Stay) => "stay",
            Some(Action::Recall) => {
                let issued = if self.executable(s) {
                    s.body_requests.conversation_recall(
                        self.generation,
                        &self.context,
                        &original,
                        Instant::now(),
                    )
                } else {
                    Err(anyhow::anyhow!("body_request_observation_unavailable"))
                };
                match issued {
                    Ok(issued) => {
                        command = issued;
                        "queued"
                    }
                    Err(error) => {
                        detail = error.to_string();
                        "rejected"
                    }
                }
            }
        };
        let row = &mut d.rows[row];
        row["player_approach_status"] = json!(status);
        row["player_approach_command_id"] = command["command_id"].clone();
        row["player_approach_detail"] = json!(detail);
        if status == "queued" {
            row["combat_resumable"] = json!(false);
        }
        tracing::info!(
            event = "player_approach_selected",
            session_id = self.sid,
            turn_id = self.turn,
            status,
            detail
        );
        d.revision += 1;
        Ok(status == "rejected")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 見込み: 閉じた最上位の選択だけが動作を決める。合格: 閉じ括弧到着でrecall一件、その前・引用・入れ子・配列は選択なし。
    // 確認範囲: ストリーム途中の実文字列をJSONとして読む境界。発行時刻は別の模擬HTTP試験で扱う。
    #[test]
    fn only_the_complete_top_level_choice_can_start_moving() {
        let raw = r#"{"speech":"引用\"body_action\"やで","extra":[{"body_action":{"action":"stay"}}],"body_action":{"action":"recall"}"#;
        assert!(received_action(raw) == Some(Action::Recall));
        assert!(received_action(&raw[..raw.len() - 1]).is_none());
        for invalid in [
            r#"{"child":{"body_action":{"action":"recall"}}"#,
            r#"{"speech":"{\"body_action\":{\"action\":\"recall\"}}""#,
            r#"{"body_action":["recall"]"#,
            r#"{"body_action":{"action":"recall","other":true}"#,
            r#"{"body_action":{"action":"recall","action":"stay"}"#,
            r#"{"body_action":{"action":"recall""#,
            r#"{"x":1 "body_action":{"action":"recall"}"#,
        ] {
            assert!(received_action(invalid).is_none(), "{invalid}");
        }
    }
}
