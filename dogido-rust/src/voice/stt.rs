use super::{
    Settings,
    process::{self, Output, cancelled},
    segment::Segment,
    transport::{Reporter, Transport},
};
use anyhow::Result;
use serde_json::json;
use std::{
    fs::{File, OpenOptions},
    io::Write,
    os::unix::fs::OpenOptionsExt,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use tokio::{process::Command, sync::watch};

struct Wav(PathBuf);
impl Wav {
    fn create(pcm: &[u8]) -> Result<Self> {
        let path = std::env::temp_dir().join(format!("dogido-voice-{}.wav", uuid::Uuid::new_v4()));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)?;
        let wav = Self(path);
        write_wav(&mut file, pcm)?;
        Ok(wav)
    }
}
impl Drop for Wav {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

fn write_wav(file: &mut File, pcm: &[u8]) -> Result<()> {
    let size = u32::try_from(pcm.len())?;
    file.write_all(b"RIFF")?;
    file.write_all(&(size + 36).to_le_bytes())?;
    file.write_all(b"WAVEfmt ")?;
    file.write_all(&16u32.to_le_bytes())?;
    for n in [1u16, 1] {
        file.write_all(&n.to_le_bytes())?;
    }
    for n in [16000u32, 32000] {
        file.write_all(&n.to_le_bytes())?;
    }
    for n in [2u16, 16] {
        file.write_all(&n.to_le_bytes())?;
    }
    file.write_all(b"data")?;
    file.write_all(&size.to_le_bytes())?;
    file.write_all(pcm)?;
    Ok(())
}

fn remove_tokens(mut line: &str) -> String {
    let mut text = String::new();
    while let Some(start) = line.find("[_") {
        text.push_str(&line[..start]);
        let rest = &line[start..];
        if let Some(end) = rest.find("_]")
            && end > 2
            && rest[2..end]
                .bytes()
                .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
        {
            line = &rest[end + 2..];
            continue;
        }
        text.push_str("[_");
        line = &rest[2..];
    }
    text.push_str(line);
    text
}

pub fn transcript(stdout: &[u8]) -> std::result::Result<String, &'static str> {
    let output = String::from_utf8_lossy(stdout);
    let text = output
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            if !line.starts_with('[') || !line.contains("-->") {
                return None;
            }
            let (_, body) = line.split_once(']')?;
            let body = remove_tokens(body);
            let body = body.trim();
            (!body.is_empty()).then(|| body.to_owned())
        })
        .collect::<Vec<_>>()
        .join(" ");
    if text.contains('\u{fffd}') {
        return Err("whisper_invalid_utf8");
    }
    if text.is_empty() {
        return Err("empty_transcript");
    }
    if ["ごおおお", "ごーーー", "ざーーー"]
        .iter()
        .any(|noise| text.contains(noise))
    {
        return Err("noise_pattern");
    }
    let english: String = text
        .chars()
        .flat_map(char::to_lowercase)
        .filter(char::is_ascii_lowercase)
        .collect();
    if ["thank", "thanks", "thankyou"].contains(&english.as_str()) {
        return Err("known_noise_text");
    }
    Ok(text)
}

fn detail(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    let chars: Vec<char> = text.trim().chars().collect();
    chars[chars.len().saturating_sub(600)..].iter().collect()
}

fn vad_count(stdout: &[u8]) -> Option<usize> {
    let text = String::from_utf8_lossy(stdout);
    let words: Vec<_> = text.split_whitespace().collect();
    words.windows(4).find_map(|w| {
        (w[0] == "Detected" && w[2] == "speech" && w[3].starts_with("segments"))
            .then(|| w[1].parse().ok())
            .flatten()
    })
}

async fn vad(
    settings: &Settings,
    wav: &Path,
    report: &Reporter,
    stop: &mut watch::Receiver<bool>,
) -> bool {
    let Some(config) = &settings.vad else {
        return true;
    };
    let mut command = Command::new(&config.cli);
    command
        .arg("--vad-model")
        .arg(&config.model)
        .arg("--file")
        .arg(wav)
        .args([
            "--vad-threshold",
            &config.threshold.to_string(),
            "--vad-min-speech-duration-ms",
            "250",
            "--vad-min-silence-duration-ms",
            "500",
            "--vad-speech-pad-ms",
            "200",
            "--no-prints",
        ]);
    let error = match process::run(command, Duration::from_secs(10), stop).await {
        Ok(Output::Cancelled) => return false,
        Ok(Output::Done {
            success,
            stdout,
            stderr,
        }) => {
            if success && let Some(count) = vad_count(&stdout) {
                if count == 0 {
                    report.event("vad_rejected", "info", Some("no_speech_segment"), json!({}));
                }
                return count > 0;
            }
            detail(if stderr.is_empty() { &stdout } else { &stderr })
        }
        Ok(Output::Timeout) => "Silero VAD timeout".into(),
        Err(error) => error.to_string(),
    };
    report.event(
        "vad_error",
        "warning",
        Some("silero_probe_failed_open"),
        json!({"detail":error}),
    );
    true
}

enum Recognition {
    Text(String),
    Rejected(&'static str),
    Stopped,
}

async fn recognize(
    settings: &Settings,
    wav: &Path,
    prompt: &str,
    threshold: f64,
    report: &Reporter,
    stop: &mut watch::Receiver<bool>,
) -> Recognition {
    let mut command = Command::new(&settings.whisper_cli);
    command
        .arg("-m")
        .arg(&settings.whisper_model)
        .arg("-f")
        .arg(wav)
        .args([
            "-l",
            "ja",
            "--prompt",
            prompt,
            "--no-speech-thold",
            &threshold.to_string(),
        ]);
    if !settings.use_gpu {
        command.arg("--no-gpu");
    }
    let result = process::run(command, Duration::from_secs(60), stop).await;
    match result {
        Ok(Output::Cancelled) => Recognition::Stopped,
        Ok(Output::Timeout) => {
            report.event("stt_error", "error", Some("whisper_timeout"), json!({}));
            Recognition::Rejected("whisper_timeout")
        }
        Err(error) => {
            report.event(
                "stt_error",
                "error",
                Some("whisper_process_error"),
                json!({"detail":error.to_string()}),
            );
            Recognition::Rejected("whisper_process_error")
        }
        Ok(Output::Done {
            success,
            stdout,
            stderr,
        }) => {
            if !success {
                report.event(
                    "stt_error",
                    "error",
                    Some("whisper_nonzero_exit"),
                    json!({"detail":detail(&stderr)}),
                );
            }
            match transcript(&stdout) {
                Ok(text) => {
                    report.event("stt_result", "info", None, json!({"recognized_text":text}));
                    Recognition::Text(text)
                }
                Err(reason) => {
                    report.event(
                        "stt_rejected",
                        "warning",
                        Some(reason),
                        json!({"detail":detail(&stderr)}),
                    );
                    Recognition::Rejected(reason)
                }
            }
        }
    }
}

pub async fn process_segment(
    settings: &Settings,
    segment: Segment,
    transport: &Transport,
    report: &Reporter,
    stop: &mut watch::Receiver<bool>,
    last_mode: &mut String,
) -> Result<()> {
    let wav = Wav::create(&segment.pcm)?;
    if !vad(settings, &wav.0, report, stop).await {
        return Ok(());
    }
    let mode = tokio::select! { biased;
        _ = cancelled(stop) => return Ok(()),
        result = transport.context() => match result {
            Ok(mode) => mode,
            Err(error) => {
                report.event("context", "warning", Some("context_unavailable"), json!({"detail":error.to_string()}));
                "normal".into()
            }
        },
    };
    if mode != *last_mode {
        report.event(
            "context",
            "info",
            Some("prompt_mode_selected"),
            json!({"prompt_mode":mode}),
        );
        *last_mode = mode.clone();
    }
    let prompt = if mode == "haiku_workshop" {
        &settings.workshop_prompt
    } else {
        &settings.normal_prompt
    };
    let started = Instant::now();
    report.event(
        "stt_started",
        "info",
        None,
        json!({"duration_ms":segment.duration_ms,
        "detail":format!("attempt=primary voiced_ms={}", segment.voiced_ms)}),
    );
    let mut result = recognize(
        settings,
        &wav.0,
        prompt,
        settings.no_speech_threshold,
        report,
        stop,
    )
    .await;
    if matches!(result, Recognition::Rejected("empty_transcript"))
        && let Some(retry) = settings
            .retry_threshold
            .filter(|v| *v > settings.no_speech_threshold)
    {
        report.event(
            "stt_started",
            "info",
            Some("empty_transcript_retry"),
            json!({"detail":format!("attempt=retry prompt=none no_speech_thold={retry}")}),
        );
        result = recognize(settings, &wav.0, "", retry, report, stop).await;
    }
    if *stop.borrow() || matches!(result, Recognition::Stopped) {
        return Ok(());
    }
    let text = match result {
        Recognition::Text(text) => Some(text),
        _ => None,
    };
    report.event(
        "stt_finished",
        "info",
        Some(if text.is_some() {
            "recognized"
        } else {
            "no_result"
        }),
        json!({"recognized_text":text,"duration_ms":started.elapsed().as_millis()}),
    );
    let Some(text) = text else {
        return Ok(());
    };
    println!("[VOICE] 認識: {text}");
    if !settings.wake_word.is_empty() && !text.contains(&settings.wake_word) {
        report.event(
            "wake_word_rejected",
            "warning",
            Some("wake_word_missing"),
            json!({"recognized_text":text}),
        );
        return Ok(());
    }
    let delivery = tokio::select! { biased;
        _ = cancelled(stop) => return Ok(()),
        result = transport.deliver(&text) => result,
    };
    match delivery {
        Ok(value) if value["accepted"] == true => {
            println!(
                "[VOICE] → 届けた (session={})",
                value["session_id"].as_str().unwrap_or("-")
            );
            report.event(
                "delivery",
                "info",
                Some("accepted"),
                json!({"recognized_text":text}),
            );
        }
        Ok(value) => {
            let reason = value["reason"].as_str().unwrap_or("rejected");
            println!("[VOICE] → サーバが受け取らず: {reason}（マイクラ接続待ち？）");
            report.event(
                "delivery",
                "warning",
                Some(reason),
                json!({"recognized_text":text}),
            );
        }
        Err(error) => report.event(
            "delivery",
            "error",
            Some("server_unreachable"),
            json!({"recognized_text":text,"detail":error.to_string()}),
        ),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn whisper_text_and_tokens() {
        assert_eq!(
            transcript(
                "log\n[00 --> 01] [_BEG_]ドギド\n[01 --> 02] おはよう[_TT_12_]\n".as_bytes()
            )
            .unwrap(),
            "ドギド おはよう"
        );
        assert_eq!(transcript(b"[log] no timestamps"), Err("empty_transcript"));
        assert_eq!(
            transcript(b"[00 --> 01] Thank you!"),
            Err("known_noise_text")
        );
        assert_eq!(
            transcript("[00 --> 01] ざーーー".as_bytes()),
            Err("noise_pattern")
        );
    }
    #[test]
    fn utf8_error_in_logs_is_not_error_in_speech() {
        let mut data = b"log \xff\n[00 --> 01] ".to_vec();
        data.extend_from_slice("こんにちは".as_bytes());
        assert_eq!(transcript(&data).unwrap(), "こんにちは");
        data.push(0xff);
        assert_eq!(transcript(&data), Err("whisper_invalid_utf8"));
    }
    #[test]
    fn temporary_wav_header_and_cleanup() {
        let wav = Wav::create(&[0, 0, 1, 0]).unwrap();
        let bytes = std::fs::read(&wav.0).unwrap();
        assert_eq!(&bytes[..4], b"RIFF");
        assert_eq!(&bytes[8..16], b"WAVEfmt ");
        assert_eq!(u32::from_le_bytes(bytes[24..28].try_into().unwrap()), 16000);
        assert_eq!(&bytes[44..], &[0, 0, 1, 0]);
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&wav.0).unwrap().permissions().mode() & 0o777,
            0o600
        );
        let path = wav.0.clone();
        drop(wav);
        assert!(!path.exists());
    }
    #[test]
    fn silero_count() {
        assert_eq!(vad_count(b"Detected 0 speech segments"), Some(0));
        assert_eq!(
            vad_count(b"log\nDetected 2 speech segments in file"),
            Some(2)
        );
        assert_eq!(vad_count(b"unknown format"), None);
    }
}
