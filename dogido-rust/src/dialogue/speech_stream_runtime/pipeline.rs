//! 通常雑談と川柳相談が共有する、文の取り出し・読み補正・一文先の合成・順次再生。
//! 各ドメインは最終結果を受け取り、表示・操作・保存を確定してから全文だけを返す。
use super::*;
use crate::types::GenerationReport;
use std::future::Future;

enum Event {
    Begin(Vec<String>, Option<Arc<AtomicBool>>),
    Delta(String),
    Barrier(oneshot::Sender<()>),
    Validate(String, oneshot::Sender<Result<()>>),
}

#[derive(Clone)]
pub(in crate::dialogue) struct Sink {
    tx: mpsc::Sender<Event>,
    sent: Arc<AtomicBool>,
    stop: watch::Receiver<bool>,
    audio_stop: watch::Receiver<bool>,
    pub(super) approach: Option<super::super::player_approach::Source>,
}
impl Sink {
    pub(in crate::dialogue) fn has_speech(&self) -> bool {
        self.sent.load(Ordering::Acquire)
    }

    pub(in crate::dialogue) async fn validate_text(&self, text: &str) -> Result<()> {
        let (done, received) = oneshot::channel();
        self.tx
            .send(Event::Validate(text.into(), done))
            .await
            .context("speech stream ended")?;
        received.await.context("speech stream ended")?
    }

    /// 同じstream接続を使う。一度発話へ渡した後は別の生成へ置き換えない。
    pub(in crate::dialogue) async fn generate(
        &self,
        llm: &RigLlm,
        request: &GenerationRequest,
        image: Option<&crate::chat_image::Attachment>,
        tools: &[Value],
        messages: &[Value],
        allow_call: bool,
        actions: Vec<String>,
    ) -> Result<(GenerationReport, Option<Value>)> {
        ensure!(!self.has_speech(), "cannot regenerate streamed speech");
        let speech_ready = self.approach.as_ref().map(|_| Arc::new(AtomicBool::new(false)));
        self.tx
            .send(Event::Begin(actions, speech_ready.clone()))
            .await
            .context("speech stream ended")?;
        let (tx, mut rx) = mpsc::channel(crate::llm::stream_text_limit(request.max_tokens));
        let mut stop = self.stop.clone();
        let generation = async {
            tokio::select! { biased;
                _ = bridge::cancelled(&mut stop) => anyhow::bail!("cancelled"),
                result = async {
                    if let Some(approach) = &self.approach {
                        approach.generate(llm, request, image, Some(tx), &self.stop, speech_ready.as_deref()).await.map(|r| (r, None))
                    } else if tools.is_empty() {
                        llm.generate_stream(request, image, Some(tx), |_| Ok(())).await.map(|r| (r, None))
                    } else {
                        llm.generate_stream_with_tools(request, tools, messages, allow_call, tx).await
                    }
                } => result,
            }
        };
        let relay = async {
            let mut stop = self.stop.clone();
            loop {
                let next = tokio::select! { biased;
                    _ = bridge::cancelled(&mut stop) => break,
                    next = rx.recv() => next,
                };
                let Some(delta) = next else { break };
                if !*self.audio_stop.borrow() {
                    let _ = self.tx.send(Event::Delta(delta)).await;
                }
            }
            if *self.audio_stop.borrow() {
                return Ok(());
            }
            let (done, received) = oneshot::channel();
            self.tx
                .send(Event::Barrier(done))
                .await
                .context("speech stream ended")?;
            received.await.context("speech stream ended")
        };
        let (result, relayed) = tokio::join!(generation, relay);
        if !*self.audio_stop.borrow() {
            relayed?;
        }
        result
    }
}

pub(in crate::dialogue) async fn run<T, G, GF, R, RF>(
    config: &DialogueConfig,
    dictionary: &super::super::tts_runtime::Dictionary,
    audio: &Audio,
    serial: &tokio::sync::Semaphore,
    corrections: Vec<Value>,
    text_limit: usize,
    cancel: &mut watch::Receiver<bool>,
    started: impl FnOnce(),
    generate: G,
    ready: R,
    on_cancel: impl FnOnce() -> bool,
    workshop: Option<&super::super::workshop_reply::Source<'_>>,
) -> Result<T>
where
    G: FnOnce(Sink) -> GF,
    GF: Future<Output = Result<T>>,
    R: FnOnce(T, Sink) -> RF,
    RF: Future<Output = Result<(T, Option<String>)>>,
{
    let (stop_model, model_stop) = watch::channel(false);
    let (stop_audio, audio_stop) = watch::channel(false);
    let (tx, mut rx) = mpsc::channel(text_limit);
    let (final_tx, final_rx) = oneshot::channel();
    let (text_tx, mut text_rx) = mpsc::channel(text_limit);
    let (audio_tx, mut audio_rx) = mpsc::channel(1);
    let sent = Arc::new(AtomicBool::new(false));
    let did_start = AtomicBool::new(false);
    let wanted_audio = AtomicBool::new(false);
    let audio_failed = AtomicBool::new(false);
    let sink = Sink {
        tx,
        sent: sent.clone(),
        stop: model_stop,
        audio_stop: audio_stop.clone(),
        approach: None,
    };
    let generation = async {
        let result = generate(sink.clone()).await;
        let result = match result {
            Ok(result) => ready(result, sink).await,
            Err(error) => Err(error),
        };
        match result {
            Ok((result, speech)) => {
                wanted_audio.store(speech.is_some(), Ordering::Release);
                if speech.is_none() {
                    let _ = stop_audio.send(true);
                }
                let _ = final_tx.send(speech);
                Ok(result)
            }
            Err(error) => {
                let _ = stop_audio.send(true);
                Err(error)
            }
        }
    };
    let parse = async {
        let mut abort = audio_stop.clone();
        let mut prefix = Prefix::default();
        let mut actions = Vec::new();
        let work = async {
            loop {
                let next = tokio::select! { biased;
                    _ = bridge::cancelled(&mut abort) => anyhow::bail!("cancelled"),
                    next = rx.recv() => next,
                };
                match next {
                    Some(Event::Begin(allowed, speech_ready)) => {
                        ensure!(prefix.sent.is_empty(), "cannot replace streamed speech");
                        prefix = Prefix { speech_ready, ..Default::default() };
                        actions = allowed;
                    }
                    Some(Event::Delta(delta)) => {
                        let chunks = prefix.feed_actions(&delta, &actions)?;
                        if !chunks.is_empty() {
                            sent.store(true, Ordering::Release);
                        }
                        for chunk in chunks {
                            text_tx.send(chunk).await.context("speech reader ended")?;
                        }
                    }
                    Some(Event::Barrier(done)) => {
                        let _ = done.send(());
                    }
                    Some(Event::Validate(text, done)) => {
                        let _ = done.send(prefix.check_final(&text));
                    }
                    None => break,
                }
            }
            if let Some(text) = final_rx.await.context("speech final result missing")? {
                for chunk in prefix.take(&text, true)? {
                    text_tx.send(chunk).await.context("speech reader ended")?;
                }
            }
            Ok::<_, anyhow::Error>(())
        }
        .await;
        if work.is_err() && !*audio_stop.borrow() {
            audio_failed.store(true, Ordering::Release);
            let _ = stop_model.send(true);
            let _ = stop_audio.send(true);
        }
        drop(rx);
        drop(text_tx);
        work
    };
    let produce = async {
        let mut abort = audio_stop.clone();
        let mut reader = Reader::new(dictionary, corrections);
        let mut index = 0;
        let work = async {
            loop {
                let next = tokio::select! { biased;
                    _ = bridge::cancelled(&mut abort) => anyhow::bail!("cancelled"),
                    next = text_rx.recv() => next,
                };
                let Some(chunk) = next else { break };
                send_sentences(
                    config,
                    audio,
                    &mut reader,
                    vec![chunk],
                    &audio_tx,
                    &mut abort,
                    &mut index,
                )
                .await?;
            }
            Ok::<_, anyhow::Error>(())
        }
        .await;
        if work.is_err() && !*audio_stop.borrow() {
            audio_failed.store(true, Ordering::Release);
            let _ = stop_model.send(true);
            let _ = stop_audio.send(true);
        }
        drop(text_rx);
        drop(audio_tx);
        work
    };
    let play = async {
        let mut abort = audio_stop.clone();
        let mut started = Some(started);
        let mut permit = None;
        let work = async {
            loop {
                let next = tokio::select! { biased;
                    _ = bridge::cancelled(&mut abort) => anyhow::bail!("cancelled"),
                    next = audio_rx.recv() => next,
                };
                let Some((index, bytes)) = next else { break };
                let notify = || {
                    if let Some(started) = started.take() {
                        did_start.store(true, Ordering::Release);
                        started();
                    }
                };
                if let Some(source) = workshop {
                    source
                        .play_prepared(config, &bytes, index, &mut abort, notify)
                        .await?;
                } else {
                    if permit.is_none() {
                        permit = Some(tokio::select! { biased;
                            _ = bridge::cancelled(&mut abort) => anyhow::bail!("cancelled"),
                            permit = serial.acquire() => permit.unwrap(),
                        });
                    }
                    audio
                        .play(config, &bytes, &mut abort, index, notify)
                        .await?;
                }
            }
            Ok::<_, anyhow::Error>(())
        }
        .await;
        if work.is_err() && !*audio_stop.borrow() {
            audio_failed.store(true, Ordering::Release);
            let _ = stop_model.send(true);
            let _ = stop_audio.send(true);
        }
        drop(audio_rx);
        work
    };
    let joined = async { tokio::join!(generation, parse, produce, play) };
    tokio::pin!(joined);
    let mut retained_generation = false;
    let (generated, parsed, produced, played) = tokio::select! { biased;
        _ = bridge::cancelled(cancel) => {
            retained_generation = on_cancel();
            let _ = stop_audio.send(true);
            if !retained_generation { let _ = stop_model.send(true); }
            joined.await
        },
        result = &mut joined => result,
    };
    ensure!(
        retained_generation || !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    let result = generated?;
    if wanted_audio.load(Ordering::Acquire) {
        parsed?;
        produced?;
        played?;
    } else if !retained_generation {
        ensure!(
            !audio_failed.load(Ordering::Acquire),
            "speech audio pipeline failed"
        );
        ensure!(
            !did_start.load(Ordering::Acquire),
            "speech stream failed after partial playback"
        );
    }
    Ok(result)
}
