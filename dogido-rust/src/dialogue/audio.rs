use super::{DialogueConfig, bridge::cancelled};
use anyhow::{Result, ensure};
use serde_json::Value;
use std::{
    collections::VecDeque,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{process::Command, sync::watch};

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
        let prepare = async {
            if let Some(bytes) = cached {
                return Ok::<_, anyhow::Error>(bytes);
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
            let bytes = self
                .http
                .post(format!("{base}/synthesis"))
                .query(&[("speaker", config.speaker)])
                .json(&query)
                .send()
                .await?
                .error_for_status()?
                .bytes()
                .await?
                .to_vec();
            ensure!(
                bytes.len() <= 10_000_000
                    && bytes.starts_with(b"RIFF")
                    && bytes.get(8..12) == Some(b"WAVE"),
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
            Ok(bytes)
        };
        let bytes = tokio::select! { _=cancelled(cancel)=>anyhow::bail!("cancelled"), result=prepare=>result? };
        tokio::fs::create_dir_all(&config.audio_dir).await?;
        let path = Temporary(
            config
                .audio_dir
                .join(format!("{}.wav", uuid::Uuid::new_v4())),
        );
        tokio::fs::write(&path.0, &*bytes).await?;
        if *cancel.borrow() {
            anyhow::bail!("cancelled");
        }
        let mut child = Command::new(&config.player)
            .arg(&path.0)
            .kill_on_drop(true)
            .spawn()?;
        let pid = child.id();
        tracing::info!(event = "audio_started", ?pid);
        started();
        let outcome = tokio::select! {
            _=cancelled(cancel) => { let _=child.kill().await; let _=child.wait().await; Err(anyhow::anyhow!("cancelled")) },
            status=child.wait() => { ensure!(status?.success(), "audio player failed"); Ok(()) },
        };
        tracing::info!(event = "audio_stopped", ?pid);
        outcome
    }
}
