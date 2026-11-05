//! パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
//! 発話本文を文ごとに音声合成し、一文先を準備しながら順番に再生する。
//! 合成・再生プロセスと取消を扱い、履歴や句の状態は呼出側が配送結果から確定する。
use super::{DialogueConfig, bridge::cancelled, sentences::sentences};
use anyhow::{Result, ensure};
use serde_json::Value;
use std::{
    collections::VecDeque,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::{
    process::Command,
    sync::{RwLock, mpsc, watch},
};

const MAX_WAV_BYTES: usize = 10_000_000;

type Cache = VecDeque<(String, Arc<Vec<u8>>)>;
pub struct Audio {
    http: reqwest::Client,
    cache: Mutex<Cache>,
    // 通常playerは共有、家族の伝言は合成開始から全ての文の終了まで専有する。
    speaker: RwLock<()>,
    family_pending: watch::Sender<usize>,
}

struct Playback<'a> {
    audio: &'a Audio,
    family: bool,
}

// 待機中の取消・futureの破棄でも件数を戻し、通常音声を再開できるようにする。
struct FamilyRequest<'a>(&'a watch::Sender<usize>);
impl Drop for FamilyRequest<'_> {
    fn drop(&mut self) {
        self.0.send_modify(|n| *n -= 1);
    }
}

fn cache_key(config: &DialogueConfig, spoken: &str) -> String {
    format!(
        "{}|{}|{}|{}|{}|{:?}|{}",
        config.voicevox_url,
        config.speaker,
        config.speed,
        config.pitch,
        config.volume,
        config.output_sampling_rate,
        spoken
    )
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
            speaker: RwLock::new(()),
            family_pending: watch::Sender::new(0),
        })
    }
    fn normal(&self) -> Playback<'_> {
        Playback {
            audio: self,
            family: false,
        }
    }

    /// 通常音声の停止とplayerの回収を待って伝言を読み切る。startedは最初のplayer起動時だけ。
    pub(super) async fn speak_family(
        &self,
        config: &DialogueConfig,
        spoken: &str,
        cancel: &mut watch::Receiver<bool>,
        started: impl FnOnce(),
    ) -> Result<()> {
        ensure!(config.audio_enabled, "audio_disabled");
        ensure!(!cancelled_now(cancel), "cancelled");
        ensure!(!spoken.trim().is_empty(), "empty_spoken_text");
        self.family_pending.send_modify(|n| *n += 1);
        let _request = FamilyRequest(&self.family_pending);
        let _speaker = tokio::select! {
            biased;
            _ = cancelled(cancel) => anyhow::bail!("cancelled"),
            guard = self.speaker.write() => guard,
        };
        let delivery = Playback {
            audio: self,
            family: true,
        }
        .speak_from(config, spoken, 0, cancel, started, |_| {})
        .await;
        // reqwestのaudio_queryエラーにはクエリ文字列（私信の原文）があり得る。
        delivery.map_err(|_| {
            if cancelled_now(cancel) {
                anyhow::anyhow!("cancelled")
            } else {
                anyhow::anyhow!("family_audio_failed")
            }
        })
    }

    /// 最初の再生プロセス起動時に開始通知を一度だけ返し、全文の正常終了でOkを返す。
    /// 途中の失敗・取消は完了扱いにしない。これはプロセスの結果であり、スピーカーの聞こえ方は実機確認する。
    pub async fn speak(
        &self,
        config: &DialogueConfig,
        spoken: &str,
        cancel: &mut watch::Receiver<bool>,
        started: impl FnOnce(),
    ) -> Result<()> {
        self.speak_from(config, spoken, 0, cancel, started, |_| {})
            .await
    }

    /// 完了済み文数から再開し、playerが正常終了した文だけをcompletedへ通知する。
    pub(super) async fn speak_from(
        &self,
        config: &DialogueConfig,
        spoken: &str,
        completed: usize,
        cancel: &mut watch::Receiver<bool>,
        started: impl FnOnce(),
        sentence_completed: impl Fn(usize),
    ) -> Result<()> {
        self.normal()
            .speak_from(
                config,
                spoken,
                completed,
                cancel,
                started,
                sentence_completed,
            )
            .await
    }
}

impl Playback<'_> {
    async fn speak_from(
        &self,
        config: &DialogueConfig,
        spoken: &str,
        completed: usize,
        cancel: &mut watch::Receiver<bool>,
        started: impl FnOnce(),
        sentence_completed: impl Fn(usize),
    ) -> Result<()> {
        ensure!(config.audio_enabled, "audio_disabled");
        let chunks = sentences(spoken)
            .filter(|s| !s.trim().is_empty())
            .collect::<Vec<_>>();
        ensure!(!chunks.is_empty(), "empty_spoken_text");
        let count = chunks.len();
        ensure!(completed <= count, "invalid completed sentence count");
        if completed == count {
            return Ok(());
        }
        let began = Instant::now();
        let mut producer_cancel = cancel.clone();
        let (tx, mut rx) = mpsc::channel::<Result<(usize, Arc<Vec<u8>>)>>(1);

        // 合成前に待ち列の枠を予約する。再生中の一文に加え、先読みは一文だけ。
        // spawnしないため、speak終了時に合成futureだけが残ることはない。
        let produce = async move {
            for (index, text) in chunks.into_iter().enumerate().skip(completed) {
                let permit = tokio::select! {
                    biased;
                    _ = cancelled(&mut producer_cancel) => break,
                    permit = tx.reserve() => match permit { Ok(p) => p, Err(_) => break },
                };
                let result = tokio::select! {
                    biased;
                    _ = cancelled(&mut producer_cancel) => break,
                    _ = tx.closed() => break,
                    result = self.audio.prepare(config, text, index, count) => result,
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
                    sentence_completed(index + 1);
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
}

impl Audio {
    pub(super) async fn prepare(
        &self,
        config: &DialogueConfig,
        spoken: &str,
        index: usize,
        count: usize,
    ) -> Result<Arc<Vec<u8>>> {
        let began = Instant::now();
        let key = cache_key(config, spoken);
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
        if let Some(rate) = config.output_sampling_rate {
            query["outputSamplingRate"] = rate.into();
        }
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

    pub(super) async fn play(
        &self,
        config: &DialogueConfig,
        bytes: &[u8],
        cancel: &mut watch::Receiver<bool>,
        index: usize,
        started: impl FnOnce(),
    ) -> Result<()> {
        self.normal()
            .play(config, bytes, cancel, index, started)
            .await
    }

    /// 既存cueもTTSも同じplayer所有権で停止・回収する。cue原本は削除しない。
    pub async fn play_file(
        &self,
        config: &DialogueConfig,
        path: &Path,
        cancel: &mut watch::Receiver<bool>,
        index: usize,
        started: impl FnOnce(),
    ) -> Result<()> {
        self.play_file_at_rate(config, path, cancel, index, 1.0, started)
            .await
    }

    /// 録音済みの数コールだけを指定倍率で再生する。afplay互換playerへrateを渡し、取消と回収は共通。
    pub async fn play_file_at_rate(
        &self,
        config: &DialogueConfig,
        path: &Path,
        cancel: &mut watch::Receiver<bool>,
        index: usize,
        rate: f64,
        started: impl FnOnce(),
    ) -> Result<()> {
        self.normal()
            .play_file_at_rate(config, path, cancel, index, rate, started)
            .await
    }
}

fn cancelled_now(cancel: &watch::Receiver<bool>) -> bool {
    *cancel.borrow() || cancel.has_changed().is_err()
}

impl Playback<'_> {
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
        self.play_file_at_rate(config, &path.0, cancel, index, 1.0, started)
            .await
    }

    async fn play_file_at_rate(
        &self,
        config: &DialogueConfig,
        path: &Path,
        cancel: &mut watch::Receiver<bool>,
        index: usize,
        rate: f64,
        started: impl FnOnce(),
    ) -> Result<()> {
        ensure!(
            rate.is_finite() && rate > 0.0 && rate <= 2.0,
            "invalid playback rate"
        );
        ensure!(config.audio_enabled, "audio_disabled");
        ensure!(!cancelled_now(cancel), "cancelled");
        let mut pending = self.audio.family_pending.subscribe();
        // 伝言は外側で専有済み。通常のcue・合成音声は伝言待ちも既存の取消を受ける。
        let _normal = if self.family {
            None
        } else {
            Some(tokio::select! {
                biased;
                _ = cancelled(cancel) => anyhow::bail!("cancelled"),
                guard = async {
                    loop {
                        let _ = pending.wait_for(|&count| count == 0).await;
                        let guard = self.audio.speaker.read().await;
                        if *pending.borrow() == 0 {
                            break guard;
                        }
                    }
                } => guard,
            })
        };
        let mut command = Command::new(&config.player);
        command.arg(path);
        if rate != 1.0 {
            command.args(["-r", &rate.to_string(), "-q", "1"]);
        }
        let mut child = command.kill_on_drop(true).spawn()?;
        let pid = child.id();
        tracing::info!(event = "audio_started", ?pid, sentence = index + 1);
        started();
        let stop = async {
            tokio::select! {
                _ = cancelled(cancel) => {},
                _ = pending.wait_for(|&count| count != 0), if !self.family => {},
            }
        };
        let outcome = tokio::select! {
            biased;
            _ = stop => {
                let _ = child.kill().await;
                child.wait().await?;
                Err(anyhow::anyhow!("cancelled"))
            }
            status = child.wait() => { ensure!(status?.success(), "audio player failed"); Ok(()) },
        };
        tracing::info!(event = "audio_stopped", ?pid, sentence = index + 1);
        outcome
    }
}

#[cfg(all(test, unix))]
mod priority_tests;
