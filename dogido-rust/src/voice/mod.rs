//! 既存Core Audio/WebRTC AEC3とwhisper.cppを使う、音声入力の制御部分。
mod process;
mod segment;
mod stt;
mod transport;

use anyhow::{Context, Result, bail, ensure};
use segment::{FRAME_BYTES, Segment, Segmenter};
use serde::Deserialize;
use serde_json::json;
use std::{
    collections::VecDeque,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    io::AsyncReadExt,
    process::Command,
    sync::{Notify, watch},
};
use transport::{Reporter, Transport};

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VadSettings {
    pub cli: PathBuf,
    pub model: PathBuf,
    pub threshold: f64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub capture_command: Vec<String>,
    pub whisper_cli: PathBuf,
    pub whisper_model: PathBuf,
    pub vad: Option<VadSettings>,
    pub base_url: String,
    pub rms_threshold: u32,
    pub silence_ms: usize,
    pub minimum_ms: usize,
    pub maximum_ms: usize,
    pub max_pending: usize,
    pub max_age_sec: f64,
    pub no_speech_threshold: f64,
    pub retry_threshold: Option<f64>,
    pub wake_word: String,
    pub normal_prompt: String,
    pub workshop_prompt: String,
    pub use_gpu: bool,
}

impl Settings {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.capture_command.is_empty(),
            "AECキャプチャの起動指定がありません"
        );
        for file in [
            &PathBuf::from(&self.capture_command[0]),
            &self.whisper_cli,
            &self.whisper_model,
        ] {
            ensure!(
                file.is_file(),
                "必要なファイルがありません: {}",
                file.display()
            );
        }
        if let Some(vad) = &self.vad {
            ensure!(
                vad.cli.is_file() && vad.model.is_file(),
                "VADの実行ファイル／モデルがありません"
            );
            ensure!(
                (0.0..=1.0).contains(&vad.threshold),
                "VADしきい値が不正です"
            );
        }
        ensure!(
            (1..=4).contains(&self.max_pending) && (1.0..=60.0).contains(&self.max_age_sec),
            "STT待ち列の設定が不正です"
        );
        ensure!(
            self.rms_threshold <= 32768
                && self.silence_ms > 0
                && self.minimum_ms > 0
                && (30..=300_000).contains(&self.maximum_ms),
            "発話区切りの設定が不正です"
        );
        ensure!(
            (0.0..=1.0).contains(&self.no_speech_threshold)
                && self
                    .retry_threshold
                    .is_none_or(|n| (0.0..=1.0).contains(&n)),
            "Whisperしきい値が不正です"
        );
        let url = reqwest::Url::parse(&self.base_url)?;
        ensure!(
            matches!(url.scheme(), "http" | "https") && url.host_str().is_some(),
            "配送先URLが不正です"
        );
        ensure!(
            url.username().is_empty() && url.password().is_none(),
            "認証値はURLでなく環境変数を使ってください"
        );
        Ok(())
    }
}

/// 録音と音声認識の速度差を吸収する、上限つきの発話待ち列。
/// 満杯時は最古の未処理区間を外し、workerも認識前に経過時間を確認して古い発話を捨てる。
/// 遅れて届いた昔の発話を、現在の会話への入力として処理し続けないための制御。
struct Pending {
    items: Mutex<VecDeque<Segment>>,
    notify: Notify,
    capacity: usize,
}
impl Pending {
    fn new(capacity: usize) -> Self {
        Self {
            items: Mutex::new(VecDeque::new()),
            notify: Notify::new(),
            capacity,
        }
    }
    fn push(&self, segment: Segment) -> bool {
        let mut items = self.items.lock().unwrap();
        let replaced = items.len() >= self.capacity;
        if replaced {
            items.pop_front();
        }
        items.push_back(segment);
        drop(items);
        self.notify.notify_one();
        replaced
    }
    fn pop(&self) -> Option<Segment> {
        self.items.lock().unwrap().pop_front()
    }
}

async fn worker(
    settings: Arc<Settings>,
    pending: Arc<Pending>,
    transport: Transport,
    report: Reporter,
    mut stop: watch::Receiver<bool>,
) {
    let mut last_mode = String::new();
    loop {
        if *stop.borrow() {
            break;
        }
        let Some(segment) = pending.pop() else {
            tokio::select! { biased;
                _ = process::cancelled(&mut stop) => break,
                _ = pending.notify.notified() => continue,
            }
        };
        if segment.captured_at.elapsed().as_secs_f64() > settings.max_age_sec {
            report.event("stt_queue", "warning", Some("stale_segment_dropped"),
                json!({"duration_ms":segment.duration_ms,"detail":format!("age_ms={}", segment.captured_at.elapsed().as_millis())}));
            continue;
        }
        if let Err(error) = stt::process_segment(
            &settings,
            segment,
            &transport,
            &report,
            &mut stop,
            &mut last_mode,
        )
        .await
        {
            report.event(
                "stt_error",
                "error",
                Some("worker_processing_error"),
                json!({"detail":error.to_string()}),
            );
        }
    }
}

async fn capture(
    settings: &Settings,
    pending: &Pending,
    report: &Reporter,
    mut stop: watch::Receiver<bool>,
    stop_tx: &watch::Sender<bool>,
) -> Result<()> {
    let mut command = Command::new(&settings.capture_command[0]);
    command.args(&settings.capture_command[1..]);
    let mut child = process::OwnedChild::spawn(command)?;
    let mut stdout = child.child.stdout.take().unwrap();
    let stderr = process::read_tail(
        child.child.stderr.take().unwrap(),
        4096,
        Some(report.clone()),
    );
    let mut segmenter = Segmenter::new(
        settings.rms_threshold,
        settings.silence_ms,
        settings.minimum_ms,
        settings.maximum_ms,
    );
    // AEC側のヘッダ待ち30秒・フレーム待ち2秒より長くし、停止したpipeを放置しない。
    let mut deadline = Duration::from_secs(35);
    let result = loop {
        let mut frame = [0; FRAME_BYTES];
        let result = tokio::select! { biased;
            _ = process::cancelled(&mut stop) => break Ok(()),
            result = tokio::time::timeout(deadline, stdout.read_exact(&mut frame)) => result,
        };
        match result {
            Ok(Ok(_)) => {}
            Ok(Err(error)) => {
                break Err(anyhow::anyhow!(
                    "AECの音声入力が終了、またはフレームが途中で切れました: {error}"
                ));
            }
            Err(_) => {
                break Err(anyhow::anyhow!(
                    "AECの音声入力が停止しました（フレーム待ちtimeout）"
                ));
            }
        }
        deadline = Duration::from_secs(3);
        if let Some(segment) = segmenter.push(frame) {
            let duration = segment.duration_ms;
            report.event(
                "capture",
                "info",
                Some("speech_segment"),
                json!({"duration_ms":duration,"detail":format!("voiced_ms={}", segment.voiced_ms)}),
            );
            if pending.push(segment) {
                report.event(
                    "stt_queue",
                    "warning",
                    Some("pending_segment_replaced"),
                    json!({"duration_ms":duration}),
                );
            }
        }
    };
    // EOFを知った時点で、進行中STT／配送を取り消す。captureの回収待ちへ持ち越さない。
    let _ = stop_tx.send(true);
    child.stop().await;
    let tail = process::finish_reader(stderr).await;
    if let Err(error) = &result {
        report.event(
            "capture",
            "error",
            Some("aec_failed"),
            json!({"detail":format!("{error}; {}", String::from_utf8_lossy(&tail))}),
        );
    }
    result
}

/// 信号を登録してから録音を開始し、取消後も子の回収が終わるまで待つ。
pub async fn run(settings: Settings) -> Result<()> {
    settings.validate()?;
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut interrupt = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;
    let transport = Transport::new(&settings.base_url)?;
    let (stop_tx, stop_rx) = watch::channel(false);
    let (diagnostic_stop, diagnostic_rx) = watch::channel(false);
    let (report, mut diagnostic_task) = transport.diagnostics(diagnostic_rx);
    let settings = Arc::new(settings);
    let pending = Arc::new(Pending::new(settings.max_pending));
    let worker_task = tokio::spawn(worker(
        settings.clone(),
        pending.clone(),
        transport,
        report.clone(),
        stop_rx.clone(),
    ));
    println!(
        "[VOICE] Rust音声入力 / WebRTC AEC3 / 無音 {}ms / 配送先 {}",
        settings.silence_ms, settings.base_url
    );
    println!("[VOICE] 待機中…話しかけてや（終了は Ctrl+C）");
    let result = {
        let capture = capture(&settings, &pending, &report, stop_rx, &stop_tx);
        tokio::pin!(capture);
        tokio::select! { biased;
            _ = term.recv() => { let _ = stop_tx.send(true); capture.await },
            _ = interrupt.recv() => { let _ = stop_tx.send(true); capture.await },
            result = &mut capture => result,
        }
    };
    let _ = stop_tx.send(true);
    let worker_result = worker_task.await;
    drop(report);
    // 最後のAECエラーも診断先へ渡すが、終了を通信待ちで引き延ばさない。
    let diagnostic_result =
        match tokio::time::timeout(Duration::from_secs(2), &mut diagnostic_task).await {
            Ok(result) => result,
            Err(_) => {
                let _ = diagnostic_stop.send(true);
                diagnostic_task.await
            }
        };
    worker_result.context("音声認識workerが異常終了しました")?;
    diagnostic_result.context("音声診断workerが異常終了しました")?;
    println!("[VOICE] 音声入力を停止しました。録音・認識プロセスを回収しました。");
    if let Err(error) = result {
        bail!("{error}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn voice_segments_and_transcripts_match_fixture() {
        let data: serde_json::Value =
            serde_json::from_str(include_str!("../../fixtures/voice-parity.json")).unwrap();
        for case in data["segments"].as_array().unwrap() {
            let mut segmenter = Segmenter::new(
                case["threshold"].as_u64().unwrap() as u32,
                case["silence_ms"].as_u64().unwrap() as usize,
                case["minimum_ms"].as_u64().unwrap() as usize,
                case["maximum_ms"].as_u64().unwrap() as usize,
            );
            let mut actual = Vec::new();
            for run in case["runs"].as_array().unwrap() {
                let sample = run[0].as_i64().unwrap() as i16;
                let mut frame = [0; FRAME_BYTES];
                for bytes in frame.as_chunks_mut::<2>().0 {
                    bytes.copy_from_slice(&sample.to_le_bytes());
                }
                for _ in 0..run[1].as_u64().unwrap() {
                    if let Some(segment) = segmenter.push(frame) {
                        actual.push(json!({"duration_ms":segment.duration_ms,"voiced_ms":segment.voiced_ms,
                            "samples":segment.pcm.as_chunks::<FRAME_BYTES>().0.iter().map(|f| i16::from_le_bytes([f[0],f[1]])).collect::<Vec<_>>()}));
                    }
                }
            }
            assert_eq!(json!(actual), case["expected"], "{}", case["name"]);
        }
        for case in data["transcripts"].as_array().unwrap() {
            let stdout: Vec<u8> = case["stdout"]
                .as_array()
                .unwrap()
                .iter()
                .map(|b| b.as_u64().unwrap() as u8)
                .collect();
            match stt::transcript(&stdout) {
                Ok(text) => assert_eq!(json!(text), case["text"]),
                Err(reason) => {
                    assert!(case["text"].is_null());
                    assert_eq!(json!(reason), case["reason"]);
                }
            }
        }
    }
    #[test]
    fn pending_replaces_oldest_without_touching_active_job() {
        let q = Pending::new(2);
        let segment = |n| Segment {
            pcm: vec![],
            duration_ms: n,
            voiced_ms: 30,
            captured_at: std::time::Instant::now(),
        };
        assert!(!q.push(segment(1)));
        let active = q.pop().unwrap();
        assert!(!q.push(segment(2)));
        assert!(!q.push(segment(3)));
        assert!(q.push(segment(4)));
        assert_eq!(active.duration_ms, 1);
        assert_eq!(q.pop().unwrap().duration_ms, 3);
        assert_eq!(q.pop().unwrap().duration_ms, 4);
        assert!(q.pop().is_none());
    }
}
