//! パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
//! 通常返答と川柳相談の可視JSON本文を文ごとに読み補正・合成・再生へ渡す。
//! 一文先の音声と上限付き一返答の文字だけを待ち列に持ち、同じturnの取消・終端を所有する。辞書はDialogueが返答をまたいで共有する。
//! 先に聞こえた文は取り消せない。生成の後半の失敗では全文を確定せず、受理済み全文の表示ACKは従来どおり扱う。
use super::{
    Dialogue, DialogueConfig, audio::Audio, bridge, chat_leaf_runtime::Run, tts_runtime::Reader,
};
use crate::{chat_validation, llm::RigLlm, playback::Status, types::GenerationRequest};
use anyhow::{Context, Result, ensure};
use serde_json::Value;
use std::sync::{
    Arc, Weak,
    atomic::{AtomicBool, Ordering},
};
use tokio::{
    sync::{mpsc, oneshot, watch},
    time::{Duration, Instant},
};

mod pipeline;
pub(super) use pipeline::{Sink, run as run_pipeline};

#[derive(Clone)]
pub(super) struct Source {
    owner: Weak<Dialogue>,
    sid: String,
    turn: String,
    epoch: u64,
    generated: Arc<AtomicBool>,
    generation_permit: Option<Arc<super::workshop_reply::Permit>>,
}
impl Source {
    pub(super) fn new(owner: &Arc<Dialogue>, sid: &str, turn: &str, epoch: u64) -> Option<Self> {
        owner.output_config(sid).audio_enabled.then(|| Self {
            owner: Arc::downgrade(owner),
            sid: sid.into(),
            turn: turn.into(),
            epoch,
            generated: Arc::new(AtomicBool::new(false)),
            generation_permit: None,
        })
    }
    pub(super) fn with_generation_permit(
        mut self,
        permit: Arc<super::workshop_reply::Permit>,
    ) -> Self {
        self.generation_permit = Some(permit);
        self
    }
    /// 全文の受理と表示への引渡しまで完了したら、生成期限を外して音声配送を続ける。
    pub(super) fn generated(&self) -> bool {
        self.generated.load(Ordering::Acquire)
    }
    pub(super) async fn render(
        &self,
        mut request: GenerationRequest,
        llm: &RigLlm,
        image: Option<&crate::chat_image::Attachment>,
        corrections: Vec<Value>,
        revision: Value,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Run> {
        let owner = self.owner.upgrade().context("speech owner ended")?;
        let config = owner.output_config(&self.sid);
        ensure!(config.audio_enabled, "audio_disabled");
        if image.is_some()
            && let Some(system) = request
                .messages
                .iter_mut()
                .find(|m| m.role == crate::types::Role::System)
        {
            system.content.push('\n');
            system
                .content
                .push_str(crate::companion_prompt::chat_image());
        }
        pipeline(
            &config,
            &owner.reading_dictionary,
            &owner.audio,
            &owner.serial,
            llm,
            SpeechInput {
                request: &request,
                image,
                corrections,
            },
            cancel,
            || {
                // 最初の実player起動時だけStartedへ進める。部分本文を表示ACKへ公開しない。
                if owner.update(&self.sid, &self.turn, self.epoch, Status::Started, None) {
                    let mut data = owner.data.lock().unwrap();
                    let row = data.rows.iter().find(|r| r["turn_id"] == self.turn);
                    let input_occurred_at =
                        row.map_or(serde_json::Value::Null, |r| r["created_at"].clone());
                    let text = row
                        .and_then(|r| r["player_input_text"].as_str())
                        .unwrap_or("")
                        .to_owned();
                    if let Some(session) = data.sessions.get_mut(&self.sid).filter(|s| {
                        s.epoch == self.epoch
                            || super::reply_output::current(s, &self.turn, self.epoch)
                    }) {
                        session
                            .history
                            .push_input(&self.turn, &text, input_occurred_at);
                    }
                }
            },
            |output, reports| {
                // 完成して受理された全文だけを公開する。音声の終わりは待たず、
                // 既存の表示ACKまたは全音声完了が同じ履歴を一度だけ確定する。
                let result = serde_json::json!({"text":output.text,"dialogue_action":output.action,
                "dialogue_outcome":output.status,"dialogue_reason":output.reason,
                "llm_reports":reports,"observation_revision":revision,"audio_streamed":true});
                ensure!(
                    owner.update(
                        &self.sid,
                        &self.turn,
                        self.epoch,
                        Status::Queued,
                        Some(&result)
                    ),
                    "speech turn changed"
                );
                self.generated.store(true, Ordering::Release);
                if let Some(permit) = &self.generation_permit {
                    permit.release();
                }
                Ok(())
            },
        )
        .await
    }
}

/// JSON途中の確定済み文字だけを取り出す。未完escape/Unicodeは次の差分を待つ。
/// action後置はspeakが届くまで保持し、最終結果との不一致は既に話した文を書き換えず失敗へ返す。
#[derive(Default)]
struct Prefix {
    raw: String,
    sent: String,
}
impl Prefix {
    #[cfg(test)]
    fn feed(&mut self, delta: &str) -> Result<Vec<String>> {
        self.feed_actions(delta, &["speak".into()])
    }
    fn feed_actions(&mut self, delta: &str, actions: &[String]) -> Result<Vec<String>> {
        self.raw.push_str(delta);
        let mut raw = self.raw.trim();
        if raw.starts_with("```") {
            let Some((_, body)) = raw.split_once('\n') else {
                return Ok(vec![]);
            };
            raw = body.trim_end().strip_suffix("```").unwrap_or(body).trim();
        }
        let Some(value) = chat_validation::complete_json(raw) else {
            return Ok(vec![]);
        };
        let Some(action) = confirmed_action(raw) else {
            return Ok(vec![]);
        };
        if value["action"] != action || !actions.contains(&action) {
            return Ok(vec![]);
        }
        let Some(text) = value["speech"].as_str() else {
            return Ok(vec![]);
        };
        self.take(text.trim_start(), false)
    }
    fn check_final(&self, text: &str) -> Result<()> {
        ensure!(
            text.starts_with(&self.sent) || self.sent.trim_end() == text,
            "streamed speech changed after delivery"
        );
        Ok(())
    }
    fn take(&mut self, text: &str, flush: bool) -> Result<Vec<String>> {
        if flush && self.sent.trim_end() == text {
            return Ok(vec![]);
        }
        ensure!(
            text.starts_with(&self.sent),
            "streamed speech changed after delivery"
        );
        let tail = &text[self.sent.len()..];
        let mut chunks = Vec::new();
        let mut offset = 0;
        for chunk in super::sentences::sentences(tail) {
            let complete = chunk.chars().count() >= 180
                || chunk.ends_with(['。', '！', '？', '\n'])
                || chunk.chars().next_back().is_some_and(char::is_whitespace)
                    && chunk.trim_end().ends_with(['.', '!', '?']);
            if !flush && !complete {
                break;
            }
            offset += chunk.len();
            if !chunk.trim().is_empty() {
                chunks.push(chunk.into());
            }
        }
        self.sent.push_str(&tail[..offset]);
        Ok(chunks)
    }
}

/// 原文で閉じ引用符まで届いた、最上位actionだけを返す。
/// speechの末尾補完をactionへ流用せず、本文中の見かけのキーや子objectを拾わない。
fn confirmed_action(raw: &str) -> Option<String> {
    let mut depth = 0;
    let mut string_start = None;
    let mut escaped = false;
    let mut previous = '\0';
    let mut key = None;
    let mut action = None;
    for (offset, ch) in raw.char_indices() {
        if let Some(start) = string_start {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                if depth == 1 {
                    let token = serde_json::from_str::<String>(&raw[start..offset + 1]).ok()?;
                    if matches!(previous, '{' | ',') {
                        key = Some(token);
                    } else if previous == ':' && key.as_deref() == Some("action") {
                        action = Some(token);
                    }
                }
                string_start = None;
                previous = '"';
            }
        } else {
            match ch {
                '"' => string_start = Some(offset),
                '{' | '[' => {
                    depth += 1;
                    previous = ch;
                }
                '}' | ']' => {
                    depth -= 1;
                    previous = ch;
                }
                c if !c.is_whitespace() => previous = c,
                _ => {}
            }
        }
    }
    action
}

async fn send_sentences(
    config: &DialogueConfig,
    audio: &Audio,
    reader: &mut Reader<'_>,
    chunks: Vec<String>,
    tx: &mpsc::Sender<(usize, Arc<Vec<u8>>)>,
    cancel: &mut watch::Receiver<bool>,
    index: &mut usize,
) -> Result<()> {
    for text in chunks {
        // 合成前に一文分を予約する。playerが遅くても音声を無制限に先読みしない。
        let permit = tokio::select! { biased;
            _ = bridge::cancelled(cancel) => anyhow::bail!("cancelled"),
            permit = tx.reserve() => permit.context("speech player ended")?,
        };
        let reading = reader
            .read(
                config,
                &text,
                cancel,
                Instant::now() + Duration::from_secs(35),
            )
            .await?;
        if reading.trim().is_empty() {
            continue;
        }
        let bytes = tokio::select! { biased;
            _ = bridge::cancelled(cancel) => anyhow::bail!("cancelled"),
            result = audio.prepare(config, &reading, *index, 0) => result?,
        };
        permit.send((*index, bytes));
        *index += 1;
    }
    Ok(())
}

/// 一返答の生成入力と読み補正。画像は借用し、寿命と取消は呼出元が管理する。
struct SpeechInput<'a> {
    request: &'a GenerationRequest,
    image: Option<&'a crate::chat_image::Attachment>,
    corrections: Vec<Value>,
}

async fn pipeline(
    config: &DialogueConfig,
    dictionary: &super::tts_runtime::Dictionary,
    audio: &Audio,
    serial: &tokio::sync::Semaphore,
    llm: &RigLlm,
    input: SpeechInput<'_>,
    cancel: &mut watch::Receiver<bool>,
    started: impl FnOnce(),
    ready: impl FnOnce(&chat_validation::Outcome, &[Value]) -> Result<()>,
) -> Result<Run> {
    let SpeechInput {
        request,
        image,
        corrections,
    } = input;
    run_pipeline(
        config, dictionary, audio, serial, corrections,
        crate::llm::stream_text_limit(request.max_tokens), cancel, started,
        |sink| async move {
            let report = sink.generate(llm, request, image, &[], &[], false, vec!["speak".into()]).await;
            let (output, reports) = match report {
                Ok((report, _)) => (
                    chat_validation::finish(Some(&report.generated.text)),
                    vec![serde_json::to_value(report)?],
                ),
                Err(error) => (
                    chat_validation::finish(None),
                    vec![serde_json::json!({"kind":"player_chat","error":error.to_string()})],
                ),
            };
            Ok(Run { output, reports, streamed: true })
        },
        |run, sink| async move {
            let speech = if run.output.action == "silent" { None } else {
                sink.validate_text(&run.output.text).await?;
                ready(&run.output, &run.reports)?;
                Some(run.output.text.clone())
            };
            tracing::info!(event="chat_leaf",implementation="rust_stream",status=run.output.status,reason=?run.output.reason,attempts=1);
            Ok((run, speech))
        },
        || false,
        None,
    ).await
}

#[cfg(test)]
mod tests {
    use super::*;
    // 見込み: 共通の本文抽出へ相談の会話actionを渡せば、一文目だけを先行できる。
    // 合格: explainの確定文はJSON終端前に一件、編集actionと本文内の見かけのactionはゼロ。音声・通信はHTTP試験で確認。
    #[test]
    fn workshop_conversation_uses_same_prefix_without_reading_operation_speech() {
        let actions = vec!["respond".to_owned(), "explain".to_owned()];
        let mut prefix = Prefix::default();
        assert_eq!(
            prefix
                .feed_actions(r#"{"action":"explain","speech":"ひとつめやで。"#, &actions)
                .unwrap(),
            vec!["ひとつめやで。"]
        );
        assert_eq!(
            prefix
                .feed_actions(r#"ふたつめやで。"}"#, &actions)
                .unwrap(),
            vec!["ふたつめやで。"]
        );
        assert!(
            prefix
                .take("ひとつめやで。ふたつめやで。", true)
                .unwrap()
                .is_empty()
        );
        let mut operation = Prefix::default();
        assert!(
            operation
                .feed_actions(
                    r#"{"action":"accept_pending","speech":"保存したで。"}"#,
                    &actions
                )
                .unwrap()
                .is_empty()
        );
        let mut quoted = Prefix::default();
        assert!(
            quoted
                .feed_actions(
                    r#"{"speech":"action=explain。","evidence":{"action":"explain"}}"#,
                    &actions
                )
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn escaped_unicode_action_last_and_split_deltas_preserve_exact_speech() {
        for raw in [
            r#"{"action":"speak","speech":"うん。引用は\"はい\"！\u732b\ud83d\udc36。末尾"}"#,
            r#"{"speech":"うん。引用は\"はい\"！\u732b\ud83d\udc36。末尾","action":"speak"}"#,
        ] {
            let expected = chat_validation::finish(Some(raw)).text;
            let mut prefix = Prefix::default();
            let mut chunks = Vec::new();
            for ch in raw.chars() {
                chunks.extend(prefix.feed(&ch.to_string()).unwrap());
            }
            chunks.extend(prefix.take(&expected, true).unwrap());
            assert_eq!(chunks.concat(), expected);
        }
    }
    #[test]
    fn action_needs_original_closing_quote_and_does_not_match_speech_or_children() {
        let mut prefix = Prefix::default();
        assert!(
            prefix
                .feed(r#"{"speech":"うん。","action":"speak"#)
                .unwrap()
                .is_empty()
        );
        assert!(prefix.feed(r#"_other"}"#).unwrap().is_empty());
        let mut prefix = Prefix::default();
        assert!(
            prefix
                .feed(r#"{"speech":"うん。","action":"speak"#)
                .unwrap()
                .is_empty()
        );
        assert_eq!(prefix.feed(r#""}"#).unwrap(), ["うん。"]);
        assert_eq!(
            confirmed_action(r#"{"speech":"\"action\":\"speak\"。"}"#),
            None
        );
        assert_eq!(
            confirmed_action(r#"{"child":{"action":"speak"},"speech":"うん。"}"#),
            None
        );
    }
    #[test]
    fn first_sentence_is_available_without_complete_json_and_silent_stays_empty() {
        let mut prefix = Prefix::default();
        assert_eq!(
            prefix
                .feed(r#"{"action":"speak","speech":"うん。"#)
                .unwrap(),
            ["うん。"]
        );
        assert!(prefix.feed("続き").unwrap().is_empty());
        let mut prefix = Prefix::default();
        assert!(
            prefix
                .feed(r#"{"action":"silent","speech":""}"#)
                .unwrap()
                .is_empty()
        );
    }
}
