use super::process::cancelled;
use anyhow::Result;
use reqwest::{Client, RequestBuilder};
use serde_json::{Value, json};
use std::time::Duration;
use tokio::sync::{mpsc, watch};

#[derive(Clone)]
pub struct Reporter(mpsc::Sender<Value>);

impl Reporter {
    pub fn capture_line(&self, bytes: &[u8]) {
        let text = String::from_utf8_lossy(bytes);
        let text: String = text.trim_end().chars().take(500).collect();
        eprintln!("[AEC] {text}");
        if let Ok(value) = serde_json::from_slice::<Value>(bytes)
            && let Some(reason) = value["reason"].as_str().filter(|r| r.starts_with("aec_"))
        {
            self.event(
                "capture",
                if reason == "aec_failed" {
                    "error"
                } else {
                    "info"
                },
                Some(reason),
                json!({"detail":text}),
            );
        }
    }
    pub fn event(&self, event: &str, level: &str, reason: Option<&str>, details: Value) {
        let mut value = json!({"schema_version":1,"event":event,"level":level,
            "recognized_text":null,"reason":reason,"detail":null,"prompt_mode":null,"duration_ms":null});
        if let Some(fields) = details.as_object() {
            value.as_object_mut().unwrap().extend(fields.clone());
        }
        for (key, limit) in [("recognized_text", 500), ("reason", 120), ("detail", 800)] {
            if let Some(text) = value[key].as_str() {
                value[key] = Value::String(text.chars().take(limit).collect());
            }
        }
        if let Some(ms) = value["duration_ms"].as_u64() {
            value["duration_ms"] = json!(ms.min(60_000));
        }
        println!("[VOICE] {value}");
        // HTTP診断が遅くてもマイクの読み取りは止めない。
        let _ = self.0.try_send(value);
    }
}

#[derive(Clone)]
pub struct Transport {
    client: Client,
    base: String,
    token: Option<String>,
}

impl Transport {
    pub fn new(base: &str) -> Result<Self> {
        Ok(Self {
            client: Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .retry(reqwest::retry::never())
                .build()?,
            base: base.trim_end_matches('/').to_owned(),
            token: std::env::var("DOGIDO_AUTH_TOKEN")
                .ok()
                .filter(|s| !s.is_empty()),
        })
    }
    fn auth(&self, request: RequestBuilder) -> RequestBuilder {
        match &self.token {
            Some(token) => request.bearer_auth(token),
            None => request,
        }
    }
    pub async fn context(&self) -> Result<String> {
        let value: Value = self
            .auth(
                self.client
                    .get(format!("{}/api/v1/voice-input/context", self.base)),
            )
            .timeout(Duration::from_secs(1))
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        Ok(if value["prompt_mode"] == "haiku_workshop" {
            "haiku_workshop"
        } else {
            "normal"
        }
        .into())
    }
    pub async fn deliver(&self, text: &str) -> Result<Value> {
        Ok(self
            .auth(
                self.client
                    .post(format!("{}/api/v1/player-input", self.base)),
            )
            .json(&json!({"text":text,"source":"voice"}))
            .timeout(Duration::from_secs(3))
            .send()
            .await?
            .json()
            .await?)
    }
    pub fn diagnostics(
        &self,
        mut stop: watch::Receiver<bool>,
    ) -> (Reporter, tokio::task::JoinHandle<()>) {
        let (tx, mut rx) = mpsc::channel::<Value>(32);
        let transport = self.clone();
        let task = tokio::spawn(async move {
            loop {
                let value = tokio::select! { biased;
                    _ = cancelled(&mut stop) => break,
                    value = rx.recv() => match value { Some(v) => v, None => break },
                };
                let request = transport
                    .auth(
                        transport
                            .client
                            .post(format!("{}/api/v1/voice-input/diagnostics", transport.base)),
                    )
                    .json(&value)
                    .timeout(Duration::from_millis(1500))
                    .send();
                tokio::select! { biased;
                    _ = cancelled(&mut stop) => break,
                    _ = request => {},
                }
            }
        });
        (Reporter(tx), task)
    }
}
