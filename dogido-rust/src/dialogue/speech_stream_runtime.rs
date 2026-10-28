//! 通常返答の可視JSON本文を文ごとに読み補正・合成・再生へ渡す。
//! 一文先の音声と上限付き一返答の文字だけを待ち列に持ち、同じturnの取消・終端・辞書回収を所有する。
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

#[derive(Clone)]
pub(super) struct Source {
    owner: Weak<Dialogue>,
    sid: String,
    turn: String,
    epoch: u64,
    generated: Arc<AtomicBool>,
}
impl Source {
    pub(super) fn new(owner: &Arc<Dialogue>, sid: &str, turn: &str, epoch: u64) -> Option<Self> {
        owner.output_config(sid).audio_enabled.then(|| Self {
            owner: Arc::downgrade(owner),
            sid: sid.into(),
            turn: turn.into(),
            epoch,
            generated: Arc::new(AtomicBool::new(false)),
        })
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
            &owner.audio,
            llm,
            SpeechInput { request: &request, image, corrections },
            cancel,
            || {
                // 最初の実player起動時だけStartedへ進める。部分本文を表示ACKへ公開しない。
                if owner.update(&self.sid, &self.turn, self.epoch, Status::Started, None) {
                    let mut data = owner.data.lock().unwrap();
                    let text = data
                        .rows
                        .iter()
                        .find(|r| r["turn_id"] == self.turn)
                        .and_then(|r| r["player_input_text"].as_str())
                        .unwrap_or("")
                        .to_owned();
                    if let Some(session) = data
                        .sessions
                        .get_mut(&self.sid)
                        .filter(|s| s.epoch == self.epoch)
                    {
                        session.history.push(&self.turn, "user", &text);
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
    fn feed(&mut self, delta: &str) -> Result<Vec<String>> {
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
        if value["action"] != "speak" || confirmed_action(raw).as_deref() != Some("speak") {
            return Ok(vec![]);
        }
        let Some(text) = value["speech"].as_str() else {
            return Ok(vec![]);
        };
        self.take(text.trim_start(), false)
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
    reader: &mut Reader,
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
    audio: &Audio,
    llm: &RigLlm,
    input: SpeechInput<'_>,
    cancel: &mut watch::Receiver<bool>,
    started: impl FnOnce(),
    ready: impl FnOnce(&chat_validation::Outcome, &[Value]) -> Result<()>,
) -> Result<Run> {
    let SpeechInput { request, image, corrections } = input;
    let (stop, stop_rx) = watch::channel(false);
    // 空deltaは送らず、各要素は最低1 byte。LLM側で同じ総byte上限を守るため、
    // 音声待ち中も一返答を受信でき、要素数も本文byte数もこの上限を越えない。
    let (delta_tx, mut delta_rx) = mpsc::channel(crate::llm::stream_text_limit(request.max_tokens));
    let (report_tx, report_rx) = oneshot::channel::<Run>();
    let (audio_tx, audio_rx) = mpsc::channel(1);
    let did_start = AtomicBool::new(false);
    let audio_failed = AtomicBool::new(false);
    let generate = async {
        let mut abort = stop_rx.clone();
        let report = tokio::select! { biased;
            _ = bridge::cancelled(&mut abort) => Err(anyhow::anyhow!("cancelled")),
            report = llm.generate_stream(request, image, delta_tx) => report,
        };
        let (output, reports) = match report {
            Ok(report) => (
                chat_validation::finish(Some(&report.generated.text)),
                vec![serde_json::to_value(report)?],
            ),
            Err(error) => (
                chat_validation::finish(None),
                vec![serde_json::json!({"kind":"player_chat","error":error.to_string()})],
            ),
        };
        // 最終JSONを検査するのはここ一度だけ。本文の受理と音声完了は分離する。
        if output.action == "silent" {
            let _ = stop.send(true);
        } else if let Err(error) = ready(&output, &reports) {
            let _ = stop.send(true);
            return Err(error);
        }
        tracing::info!(event="chat_leaf",implementation="rust_stream",status=output.status,reason=?output.reason,attempts=1);
        let run = Run {
            output,
            reports,
            streamed: true,
        };
        let _ = report_tx.send(run.clone());
        Ok::<_, anyhow::Error>(run)
    };
    let produce = async {
        let mut abort = stop_rx.clone();
        let mut reader = Reader::new(corrections);
        let mut prefix = Prefix::default();
        let mut index = 0;
        let work = async {
            loop {
                let next = tokio::select! { biased;
                    _ = bridge::cancelled(&mut abort) => anyhow::bail!("cancelled"),
                    next = delta_rx.recv() => next,
                };
                let Some(mut delta) = next else { break };
                // 合成待ち中に届いた文字はまとめて読む。先頭文のための待機は足さず、
                // 同じ蓄積JSONを待ち列の小片ごとに解析し直す処理を避ける。
                while let Ok(next) = delta_rx.try_recv() {
                    delta.push_str(&next);
                }
                send_sentences(
                    config,
                    audio,
                    &mut reader,
                    prefix.feed(&delta)?,
                    &audio_tx,
                    &mut abort,
                    &mut index,
                )
                .await?;
            }
            let run = report_rx.await.context("LLM report missing")?;
            if run.output.action != "silent" {
                send_sentences(
                    config,
                    audio,
                    &mut reader,
                    prefix.take(&run.output.text, true)?,
                    &audio_tx,
                    &mut abort,
                    &mut index,
                )
                .await?;
            }
            Ok::<_, anyhow::Error>(())
        }
        .await;
        if work.is_err() {
            if !*stop_rx.borrow() {
                audio_failed.store(true, Ordering::SeqCst);
            }
            let _ = stop.send(true);
        }
        let cleanup = reader.finish(work.is_err() || *stop_rx.borrow()).await;
        drop(audio_tx);
        work?;
        cleanup
    };
    let play = async {
        let mut abort = stop_rx.clone();
        let result = audio
            .play_stream(config, audio_rx, &mut abort, || {
                did_start.store(true, Ordering::SeqCst);
                started();
            })
            .await;
        if result.is_err() {
            if !*stop_rx.borrow() {
                audio_failed.store(true, Ordering::SeqCst);
            }
            let _ = stop.send(true);
        }
        result
    };
    let joined = async { tokio::join!(generate, produce, play) };
    tokio::pin!(joined);
    let (generated, produced, played) = tokio::select! { biased;
        _ = bridge::cancelled(cancel) => { let _ = stop.send(true); joined.await },
        result = &mut joined => result,
    };
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    let run = generated?;
    if run.output.action == "silent" {
        ensure!(
            !audio_failed.load(Ordering::SeqCst),
            "speech audio pipeline failed"
        );
        ensure!(
            !did_start.load(Ordering::SeqCst),
            "speech stream failed after partial playback"
        );
    } else {
        produced?;
        played?;
    }
    Ok(run)
}

#[cfg(test)]
mod tests {
    use super::*;
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
