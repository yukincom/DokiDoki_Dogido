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
        approach: Option<&super::player_approach::Source>,
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
                approach,
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
    // 二択を提示した場合は、その選択を受理した時点から先行発話できる。
    speech_ready: Option<Arc<AtomicBool>>,
}
impl Prefix {
    #[cfg(test)]
    fn feed(&mut self, delta: &str) -> Result<Vec<String>> {
        self.feed_actions(delta, &["speak".into()])
    }
    fn feed_actions(&mut self, delta: &str, actions: &[String]) -> Result<Vec<String>> {
        self.raw.push_str(delta);
        let mut raw = self.raw.trim_start();
        if raw.starts_with("```") {
            let Some((_, body)) = raw.split_once('\n') else {
                return Ok(vec![]);
            };
            raw = body.trim_start();
        }
        if self.speech_ready.as_ref().is_some_and(|ready| !ready.load(Ordering::Acquire)) {
            return Ok(vec![]);
        }
        let Some((action, text, closed)) = speech_fields(raw) else {
            return Ok(vec![]);
        };
        if !actions.contains(&action) {
            return Ok(vec![]);
        }
        // 相談の本文が閉じた後の評価・材料は音声に使わない。コードが前置きや句を
        // 組み立てるshow_currentと通常雑談は、従来どおり末尾を最終本文で照合する。
        let flush = closed && matches!(
            action.as_str(),
            "respond" | "explain" | "ask" | "compare" | "acknowledge_meaning"
        );
        self.take(if flush { text.trim() } else { text.trim_start() }, flush)
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

/// 最上位のactionとspeechだけを受信順に読む。後続フィールドや外側の閉じ括弧は待たない。
/// action後置・tool呼出し・本文内に引用されたJSONを、確定した会話と取り違えない。
fn speech_fields(raw: &str) -> Option<(String, String, bool)> {
    use serde::de::IgnoredAny;

    let mut rest = raw.strip_prefix('{')?;
    let mut action = None;
    let mut speech = None;
    loop {
        let mut keys = serde_json::Deserializer::from_str(rest).into_iter::<String>();
        let key = keys.next()?.ok()?;
        rest = rest[keys.byte_offset()..].trim_start().strip_prefix(':')?.trim_start();
        let consumed = match key.as_str() {
            "action" | "speech" => {
                let mut value = serde_json::Deserializer::from_str(rest).into_iter::<String>();
                match value.next()? {
                    Ok(text) => {
                        if key == "action" {
                            action = Some(text);
                        } else {
                            speech = Some(text);
                        }
                        value.byte_offset()
                    }
                    Err(_) if key == "speech" => return Some((action?, open_speech(rest)?, false)),
                    Err(_) => return None,
                }
            }
            _ => {
                let mut value = serde_json::Deserializer::from_str(rest).into_iter::<IgnoredAny>();
                value.next()?.ok()?;
                value.byte_offset()
            }
        };
        if action.is_some() && speech.is_some() {
            return Some((action?, speech?, true));
        }
        rest = rest[consumed..].trim_start().strip_prefix(',')?;
    }
}

/// 未完の文字列はすでに到着した文字まで。末尾で分割されたエスケープまたは
/// サロゲート対だけを次の受信に残す。復号は通常のJSON文字列と同じserdeに任せる。
fn open_speech(raw: &str) -> Option<String> {
    let mut body = raw.strip_prefix('"')?;
    for _ in 0..=2 {
        if let Ok(text) = serde_json::from_str::<String>(&format!("\"{body}\"")) {
            return Some(text);
        }
        let (prefix, escape) = body.rsplit_once('\\')?;
        let partial_unicode = escape.strip_prefix('u').is_some_and(|digits| {
            digits.len() <= 4 && digits.bytes().all(|b| b.is_ascii_hexdigit())
        });
        if !escape.is_empty() && !partial_unicode {
            return None;
        }
        body = prefix;
    }
    None
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
    approach: Option<&'a super::player_approach::Source>,
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
        approach,
    } = input;
    let selection_cancel = cancel.clone();
    run_pipeline(
        config, dictionary, audio, serial, corrections,
        crate::llm::stream_text_limit(request.max_tokens), cancel, started,
        |mut sink| async move {
            sink.approach = approach.cloned();
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
        |mut run, sink| async move {
            if let Some(approach) = approach {
                approach.finish(&mut run.output, &selection_cancel)?;
            }
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

    // 見込み: 二択の受付後なら本文生成中の一文目を読み、拒否時は台詞を差し替えられる。
    // 合格: 同じ本文は受付前に零文、受付後に一文。確認範囲: 音声差分の抽出。実発行はHTTP試験で確認。
    #[test]
    fn approach_speech_waits_only_for_a_movement_decision() {
        let ready = Arc::new(AtomicBool::new(false));
        let mut prefix = Prefix { speech_ready: Some(ready.clone()), ..Default::default() };
        assert!(prefix.feed(r#"{"action":"speak","speech":"行くで。"#).unwrap().is_empty());
        assert!(prefix.check_final("今はそっちへ行けへんわ。").is_ok());
        ready.store(true, Ordering::Release);
        assert_eq!(prefix.feed(r#""}"#).unwrap(), ["行くで。"]);
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
            speech_fields(r#"{"speech":"\"action\":\"speak\"。"}"#),
            None
        );
        assert_eq!(
            speech_fields(r#"{"child":{"action":"speak"},"speech":"うん。"}"#),
            None
        );
    }
    // 見込み: 次の文字のUnicodeエスケープが割れても前の句点は確定済み。
    // 合格: 分割中に一文、残りが届くと絵文字を一度だけ出す。範囲は音声へ渡す本文の抽出。
    #[test]
    fn workshop_sentence_precedes_split_surrogate_and_unfinished_feedback() {
        let mut prefix = Prefix::default();
        let actions = ["explain".into()];
        let first = r#"{"extra":{"speech":"読まない。"},"action":"explain","speech":"一文目。\uD83D\u"#;
        assert_eq!(prefix.feed_actions(first, &actions).unwrap(), ["一文目。"]);
        assert_eq!(prefix.feed_actions(r#"DC36やで","feedback":"#, &actions).unwrap(), ["🐶やで"]);
        assert!(prefix.feed_actions("null}", &actions).unwrap().is_empty());
        assert!(prefix.take("一文目。🐶やで", true).unwrap().is_empty());
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
