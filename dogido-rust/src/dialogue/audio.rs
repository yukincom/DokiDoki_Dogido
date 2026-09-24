use super::{DialogueConfig, bridge::cancelled, sentences::sentences};
use anyhow::{Result, ensure};
use serde_json::Value;
use std::{
    collections::VecDeque,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::{
    process::Command,
    sync::{mpsc, watch},
};

const MAX_WAV_BYTES: usize = 10_000_000;

type Cache = VecDeque<(String, Arc<Vec<u8>>)>;
pub struct Audio {
    http: reqwest::Client,
    cache: Mutex<Cache>,
}
struct Temporary(PathBuf);
impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
impl Audio {
    pub fn new() -> Result<Self> {
        Ok(Self {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(15))
                .redirect(reqwest::redirect::Policy::none())
                .retry(reqwest::retry::never())
                .build()?,
            cache: Mutex::new(VecDeque::new()),
        })
    }
    pub async fn speak(
        &self,
        config: &DialogueConfig,
        spoken: &str,
        cancel: &mut watch::Receiver<bool>,
        started: impl FnOnce(),
    ) -> Result<()> {
        ensure!(config.audio_enabled, "audio_disabled");
        let chunks = sentences(spoken)
            .filter(|s| !s.trim().is_empty())
            .collect::<Vec<_>>();
        ensure!(!chunks.is_empty(), "empty_spoken_text");
        let count = chunks.len();
        let began = Instant::now();
        let mut producer_cancel = cancel.clone();
        let (tx, mut rx) = mpsc::channel::<Result<(usize, Arc<Vec<u8>>)>>(1);

        // 合成前に待ち列の枠を予約する。再生中の一文に加え、先読みは一文だけ。
        // spawnしないため、speak終了時に合成futureだけが残ることはない。
        let produce = async move {
            for (index, text) in chunks.into_iter().enumerate() {
                let permit = tokio::select! {
                    biased;
                    _ = cancelled(&mut producer_cancel) => break,
                    permit = tx.reserve() => match permit { Ok(p) => p, Err(_) => break },
                };
                let result = tokio::select! {
                    biased;
                    _ = cancelled(&mut producer_cancel) => break,
                    _ = tx.closed() => break,
                    result = self.prepare(config, text, index, count) => result,
                };
                let failed = result.is_err();
                permit.send(result.map(|bytes| (index, bytes)));
                if failed {
                    break;
                }
            }
        };
        let play = async {
            let mut started = Some(started);
            let outcome = async {
                loop {
                    let next = tokio::select! {
                        biased;
                        _ = cancelled(cancel) => anyhow::bail!("cancelled"),
                        next = rx.recv() => next,
                    };
                    let Some(next) = next else { break };
                    let (index, bytes) = next?;
                    self.play(config, &bytes, cancel, index, || {
                        if let Some(started) = started.take() {
                            tracing::info!(
                                event = "audio_first_sentence",
                                sentences = count,
                                prepare_ms = began.elapsed().as_millis() as u64
                            );
                            started();
                        }
                    })
                    .await?;
                }
                Ok(())
            }
            .await;
            // player失敗時も先読みHTTPを捨てる。produceのtx.closed()を必ず起こす。
            rx.close();
            outcome
        };
        let (_, result) = tokio::join!(produce, play);
        result
    }

    async fn prepare(
        &self,
        config: &DialogueConfig,
        spoken: &str,
        index: usize,
        count: usize,
    ) -> Result<Arc<Vec<u8>>> {
        let began = Instant::now();
        let key = format!(
            "{}|{}|{}|{}|{}|{}",
            config.voicevox_url, config.speaker, config.speed, config.pitch, config.volume, spoken
        );
        let cached = self
            .cache
            .lock()
            .unwrap()
            .iter()
            .find(|(k, _)| k == &key)
            .map(|(_, v)| v.clone());
        if let Some(bytes) = cached {
            tracing::info!(
                event = "audio_sentence_ready",
                sentence = index + 1,
                sentences = count,
                synthesis_ms = 0,
                cached = true
            );
            return Ok(bytes);
        }
        let base = config.voicevox_url.trim_end_matches('/');
        let mut query: Value = self
            .http
            .post(format!("{base}/audio_query"))
            .query(&[
                ("speaker", config.speaker.to_string()),
                ("text", spoken.into()),
            ])
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        ensure!(query.is_object(), "VOICEVOX query is not an object");
        query["speedScale"] = config.speed.into();
        query["pitchScale"] = config.pitch.into();
        query["volumeScale"] = config.volume.into();
        let mut response = self
            .http
            .post(format!("{base}/synthesis"))
            .query(&[("speaker", config.speaker)])
            .json(&query)
            .send()
            .await?
            .error_for_status()?;
        ensure!(
            response
                .content_length()
                .is_none_or(|size| size <= MAX_WAV_BYTES as u64),
            "VOICEVOX WAV exceeds size limit"
        );
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await? {
            ensure!(
                chunk.len() <= MAX_WAV_BYTES - bytes.len(),
                "VOICEVOX WAV exceeds size limit"
            );
            bytes.extend_from_slice(&chunk);
        }
        ensure!(
            bytes.starts_with(b"RIFF") && bytes.get(8..12) == Some(b"WAVE"),
            "VOICEVOX did not return a bounded WAV"
        );
        let bytes = Arc::new(bytes);
        let mut cache = self.cache.lock().unwrap();
        while cache.len() >= 128
            || cache.iter().map(|(_, v)| v.len()).sum::<usize>() + bytes.len() > 32_000_000
        {
            if cache.pop_front().is_none() {
                break;
            }
        }
        cache.push_back((key, bytes.clone()));
        tracing::info!(
            event = "audio_sentence_ready",
            sentence = index + 1,
            sentences = count,
            synthesis_ms = began.elapsed().as_millis() as u64,
            cached = false
        );
        Ok(bytes)
    }

    async fn play(
        &self,
        config: &DialogueConfig,
        bytes: &[u8],
        cancel: &mut watch::Receiver<bool>,
        index: usize,
        started: impl FnOnce(),
    ) -> Result<()> {
        tokio::fs::create_dir_all(&config.audio_dir).await?;
        let path = Temporary(
            config
                .audio_dir
                .join(format!("{}.wav", uuid::Uuid::new_v4())),
        );
        tokio::fs::write(&path.0, bytes).await?;
        if *cancel.borrow() {
            anyhow::bail!("cancelled");
        }
        let mut child = Command::new(&config.player)
            .arg(&path.0)
            .kill_on_drop(true)
            .spawn()?;
        let pid = child.id();
        tracing::info!(event = "audio_started", ?pid, sentence = index + 1);
        started();
        let outcome = tokio::select! {
            _=cancelled(cancel) => { let _=child.kill().await; let _=child.wait().await; Err(anyhow::anyhow!("cancelled")) },
            status=child.wait() => { ensure!(status?.success(), "audio player failed"); Ok(()) },
        };
        tracing::info!(event = "audio_stopped", ?pid, sentence = index + 1);
        outcome
    }
}
