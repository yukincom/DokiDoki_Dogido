//! Rustが所有するPython補助プロセスのstdio通信と寿命管理。
//! Helperは子プロセスの上限付きJSON通信・取消・回収に加え、Rust内の読み整形も持つ。
//! prepare/transformはRustで処理し、辞書が必要な場合だけtokenをworkerから受け取る。
//! Route/LiveBackend/runもRustで川柳生成を進める。Pythonへ生成・編集・保存判断は渡さない。
use crate::{
    haiku::{
        self, Backend, GroundedHaikuResult, Input, LineForm, StructuredRequest, TransformRequest,
    },
    haiku_response,
    llm::RigLlm,
    types::{ChatMessage, GenerationRequest},
};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{path::Path, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::watch,
};

const FRAME_LIMIT: u64 = 1_000_000;

pub struct Helper {
    child: Child,
    stdin: Option<ChildStdin>,
    stdout: BufReader<ChildStdout>,
    poisoned: bool,
}

impl Helper {
    pub fn start(python: &Path, script: &Path) -> Result<Self> {
        let mut child = Command::new(python)
            .arg(script)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true)
            .spawn()
            .context("cannot start Python worker")?;
        tracing::info!(event = "python_worker_started", pid = ?child.id());
        let stdin = child.stdin.take().context("Python worker stdin")?;
        let stdout = child.stdout.take().context("Python worker stdout")?;
        Ok(Self {
            child,
            stdin: Some(stdin),
            stdout: BufReader::new(stdout),
            poisoned: false,
        })
    }

    /// Seal the protocol before awaiting cleanup so late replies cannot be reused.
    async fn poison_and_reap(&mut self) {
        self.poisoned = true;
        self.stdin.take();
        let _ = self.child.kill().await;
        let _ = self.child.wait().await;
    }

    pub async fn exchange(&mut self, frame: Value) -> Result<Value> {
        self.exchange_with_timeout(frame, Duration::from_secs(15))
            .await
    }

    pub async fn exchange_with_timeout(
        &mut self,
        frame: Value,
        timeout: Duration,
    ) -> Result<Value> {
        ensure!(
            !self.poisoned,
            "Python worker protocol is closed after failure"
        );
        let request = serde_json::to_vec(&frame)?;
        ensure!(
            request.len() < FRAME_LIMIT as usize,
            "Python worker request too large"
        );
        let exchange = async {
            if frame["op"] == "reading" {
                let response = self.reading(&frame, timeout).await?;
                ensure!(
                    serde_json::to_vec(&response)?.len() < FRAME_LIMIT as usize,
                    "Python worker response too large"
                );
                Ok(response)
            } else {
                self.exchange_raw(&request).await
            }
        };
        let result = tokio::time::timeout(timeout, exchange)
            .await
            .unwrap_or_else(|_| Err(anyhow::anyhow!("Python worker timed out")));
        if result.is_err() {
            // 遅れて届く旧要求の返答を、次の行のpromptや読みとして使わない。
            self.poison_and_reap().await;
        }
        result
    }

    /// 読みの判定・整形はRust。Pythonへ送るのは必要時のtts_tokens要求だけ。
    /// 所有中workerの辞書を共有し、別workerの生成や旧Pythonの整形処理は行わない。
    async fn reading(&mut self, frame: &Value, timeout: Duration) -> Result<Value> {
        use crate::tts_reading::{self, Step, tokens};
        let deadline = tokio::time::Instant::now() + timeout;
        let text = frame["text"]
            .as_str()
            .context("reading text must be a string")?;
        let engine = match frame.get("reading_engine") {
            None => Some("auto"),
            Some(Value::Null) => None,
            Some(Value::String(value)) => Some(value.as_str()),
            _ => anyhow::bail!("reading engine must be a string or null"),
        };
        let environment = engine
            .is_none()
            .then(|| std::env::var("DOGIDO_TTS_READING_ENGINE").ok())
            .flatten();
        let spoken = match tts_reading::prepare(text, engine, environment.as_deref()) {
            Step::Ready(spoken) => spoken,
            Step::NeedsUnidic { source } => {
                let request_id = uuid::Uuid::new_v4().to_string();
                let request = serde_json::to_vec(&json!({
                    "op":"tts_tokens", "schema_version":1, "request_id":request_id, "text":source
                }))?;
                ensure!(
                    request.len() < FRAME_LIMIT as usize,
                    "Python worker request too large"
                );
                let response = self.exchange_raw(&request).await?;
                let dictionary = tokens::decode(response, &request_id)?;
                tts_reading::finish(&source, dictionary.as_deref())
            }
        };
        ensure!(
            tokio::time::Instant::now() < deadline,
            "Python worker timed out"
        );
        Ok(json!({"spoken_text":spoken}))
    }

    async fn exchange_raw(&mut self, request: &[u8]) -> Result<Value> {
        let stdin = self.stdin.as_mut().context("helper is closed")?;
        stdin.write_all(request).await?;
        stdin.write_all(b"\n").await?;
        stdin.flush().await?;
        let mut bytes = Vec::new();
        (&mut self.stdout)
            .take(FRAME_LIMIT + 1)
            .read_until(b'\n', &mut bytes)
            .await?;
        ensure!(
            bytes.len() <= FRAME_LIMIT as usize,
            "Python worker response too large"
        );
        ensure!(
            bytes.last() == Some(&b'\n'),
            "Python worker ended before a result"
        );
        let response: Value =
            serde_json::from_slice(&bytes).context("invalid Python worker JSON")?;
        ensure!(
            response.get("error").is_none(),
            "Python worker: {}",
            response["error"]
        );
        Ok(response)
    }

    pub async fn prepare(&mut self, request: &StructuredRequest) -> Result<Vec<ChatMessage>> {
        ensure!(
            !self.poisoned,
            "Python worker protocol is closed after failure"
        );
        ensure!(
            serde_json::to_vec(&json!({"op":"prepare", "request":request}))?.len()
                < FRAME_LIMIT as usize,
            "Python worker request too large"
        );
        let messages = crate::haiku_prompt::messages(request)?;
        ensure!(
            crate::text_format::spaced_json(&json!({"messages":messages})).len() < FRAME_LIMIT as usize,
            "Python worker response too large"
        );
        Ok(messages)
    }

    pub async fn transform(&mut self, request: TransformRequest) -> Result<LineForm> {
        use crate::{
            haiku::{TransformMode, lexical},
            tts_reading::has_kanji,
        };
        ensure!(
            !self.poisoned,
            "Python worker protocol is closed after failure"
        );
        ensure!(
            serde_json::to_vec(&json!({"op":"transform","request":request}))?.len()
                < FRAME_LIMIT as usize,
            "Python worker request too large"
        );
        let result = async {
            let result = match request.mode {
                TransformMode::Correct => {
                    let text =
                        lexical::correct(&request.text, &request.atom_ids, &request.source_atoms)
                            .map_or(request.text, |c| c.corrected);
                    lexical::form(text)
                }
                TransformMode::Normalize => {
                    let dictionary = if has_kanji(&request.text) {
                        Some(self.neutral_reading(&request.text).await?)
                    } else {
                        None
                    };
                    lexical::normalized_form(&request.text, dictionary.as_deref())
                }
            };
            ensure!(
                serde_json::to_vec(&result)?.len() < FRAME_LIMIT as usize,
                "Python worker response too large"
            );
            Ok(result)
        }
        .await;
        if result.is_err() {
            self.poison_and_reap().await;
        }
        result
    }

    /// Raw neutral reading: no line/quote trimming, same token correlation and
    /// singleton as Normalize. Optional dictionary failure preserves the source;
    /// IPC errors remain errors and the owned helper is reaped by exchange.
    pub async fn neutral_reading(&mut self, source: &str) -> Result<String> {
        use crate::tts_reading::{has_kanji, tokens};
        if !has_kanji(source) {
            return Ok(source.to_owned());
        }
        let request_id = uuid::Uuid::new_v4().to_string();
        let response = self
            .exchange(json!({"op":"tts_tokens","schema_version":1,
            "request_id":request_id,"text":source}))
            .await?;
        let result = tokens::decode_tokens(response, &request_id);
        if result.is_err() {
            self.poison_and_reap().await;
        }
        Ok(result?
            .map(|words| tokens::neutral(&words))
            .unwrap_or_else(|| source.into()))
    }

    /// 成否や取消を問わず所有するhelperを回収する。共有モデルは触らない。
    pub async fn finish(mut self, abort: bool) -> Result<()> {
        let pid = self.child.id();
        self.stdin.take();
        if abort {
            let _ = self.child.kill().await;
        }
        let status = match tokio::time::timeout(Duration::from_secs(2), self.child.wait()).await {
            Ok(result) => result?,
            Err(_) => {
                let _ = self.child.kill().await;
                self.child.wait().await?
            }
        };
        tracing::info!(event = "python_worker_stopped", ?pid, ?status);
        ensure!(abort || status.success(), "Python worker exit {status}");
        Ok(())
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RouteConfig {
    pub base_url: String,
    pub model: String,
    pub max_tokens: u64,
    pub timeout_ms: u64,
}

pub struct Route {
    pub llm: RigLlm,
    pub model: String,
    pub max_tokens: u64,
}

impl Route {
    pub fn new(config: RouteConfig, api_key: Option<&str>) -> Result<Self> {
        ensure!(
            !config.model.trim().is_empty() && config.max_tokens > 0,
            "invalid haiku route"
        );
        Ok(Self {
            llm: RigLlm::new(
                &config.base_url,
                api_key,
                Duration::from_millis(config.timeout_ms),
            )?,
            model: config.model,
            max_tokens: config.max_tokens,
        })
    }
}

pub struct LiveBackend<'a> {
    pub helper: &'a mut Helper,
    pub chat: &'a Route,
    pub haiku: &'a Route,
    pub requests: Vec<Value>,
    pub reports: Vec<Value>,
}

impl Backend for LiveBackend<'_> {
    async fn generate(&mut self, request: StructuredRequest) -> Result<Value> {
        self.requests.push(serde_json::to_value(&request)?);
        let route = match (request.kind.as_str(), request.route.as_str()) {
            ("haiku_irony" | "haiku_scene" | "haiku_line_grounding", "chat") => self.chat,
            ("haiku_draft" | "haiku_line_regeneration" | "haiku_workshop_revision", "haiku") => {
                self.haiku
            }
            _ => anyhow::bail!("unexpected haiku generation route"),
        };
        let messages = self.helper.prepare(&request).await?;
        let input = GenerationRequest {
            schema_version: 1,
            kind: request.kind.clone(),
            model: route.model.clone(),
            messages,
            temperature: request.temperature,
            max_tokens: request.max_tokens.unwrap_or(route.max_tokens),
            enable_thinking: false,
        };
        match route.llm.generate(&input).await {
            Ok(report) => {
                let parsed = haiku_response::parse(
                    &request.kind,
                    &report.generated,
                    &request.fallback_value,
                );
                self.reports.push(serde_json::to_value(report)?);
                parsed
            }
            Err(error) => {
                self.reports
                    .push(json!({"kind":request.kind,"error":error.to_string()}));
                Err(error)
            }
        }
    }

    async fn transform(&mut self, request: TransformRequest) -> Result<LineForm> {
        self.helper.transform(request).await
    }
}

#[derive(Debug, Serialize)]
pub struct RunReport {
    pub result: GroundedHaikuResult,
    pub requests: Vec<Value>,
    pub reports: Vec<Value>,
}

pub struct RunConfig<'a> {
    pub python: &'a Path,
    pub helper: &'a Path,
    pub chat: &'a Route,
    pub haiku: &'a Route,
    pub timeout: Duration,
}

async fn cancelled(cancel: &mut watch::Receiver<bool>) {
    loop {
        if *cancel.borrow_and_update() || cancel.changed().await.is_err() {
            return;
        }
    }
}

/// 生成単体の有界実行。会話、再生、workshop、JSONLへは副作用を持たない。
/// 所有者はwatchで取消し、このfutureの完了までawaitすること。外側からabortしない。
pub async fn run(
    config: RunConfig<'_>,
    input: Input,
    cancel: &mut watch::Receiver<bool>,
) -> Result<RunReport> {
    ensure!(
        !config.timeout.is_zero(),
        "haiku run timeout must be positive"
    );
    let mut helper = Helper::start(config.python, config.helper)?;
    let mut backend = LiveBackend {
        helper: &mut helper,
        chat: config.chat,
        haiku: config.haiku,
        requests: Vec::new(),
        reports: Vec::new(),
    };
    let result = tokio::select! {
        _ = cancelled(cancel) => Err(anyhow::anyhow!("haiku cancelled")),
        result = tokio::time::timeout(config.timeout, haiku::generate(&mut backend, input)) =>
            result.unwrap_or_else(|_| Err(anyhow::anyhow!("haiku generation timed out"))),
    };
    let requests = std::mem::take(&mut backend.requests);
    let reports = std::mem::take(&mut backend.reports);
    drop(backend);
    let cleanup = helper.finish(result.is_err()).await;
    let result = result?;
    cleanup?;
    Ok(RunReport {
        result,
        requests,
        reports,
    })
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    fn prompt_request() -> StructuredRequest {
        StructuredRequest {
            kind: "haiku_line_grounding".into(),
            details: serde_json::Map::new(),
            route: "chat".into(),
            temperature: 0.0,
            max_tokens: Some(512),
            fallback_value: json!({}),
        }
    }

    #[tokio::test]
    async fn prepare_is_native_and_does_not_consume_the_dictionary_reply() {
        let path =
            std::env::temp_dir().join(format!("dogido-native-haiku-{}.sh", uuid::Uuid::new_v4()));
        // This helper understands only the subsequent lexical transform, never prepare.
        std::fs::write(
            &path,
            r#"read -r line
request_id=${line#*\"request_id\":\"}
request_id=${request_id%%\"*}
printf '{"schema_version":1,"request_id":"%s","status":"ok","tokens":[{"surface":"仮名","goshu":"漢","pos1":"名詞","kana":"カナ","pron":"カナ"}]}\n' "$request_id"
"#,
        )
        .unwrap();
        let mut helper = Helper::start(Path::new("/bin/sh"), &path).unwrap();
        let request = prompt_request();
        let messages = helper.prepare(&request).await.unwrap();
        assert_eq!(messages, crate::haiku_prompt::messages(&request).unwrap());
        let mut too_large = request.clone();
        too_large.details.insert(
            "interpretation".into(),
            "あ".repeat(FRAME_LIMIT as usize).into(),
        );
        assert!(
            helper
                .prepare(&too_large)
                .await
                .unwrap_err()
                .to_string()
                .contains("request too large")
        );
        let transformed = helper
            .transform(TransformRequest {
                text: "仮名".into(),
                mode: crate::haiku::TransformMode::Normalize,
                line_index: 0,
                atom_ids: vec![],
                source_atoms: vec![],
            })
            .await
            .unwrap();
        assert_eq!(transformed.text, "かな");
        assert_eq!(transformed.signature, "かな");
        helper.finish(false).await.unwrap();
        std::fs::remove_file(path).unwrap();
    }

    #[tokio::test]
    async fn broken_or_timed_out_protocol_is_reaped_and_cannot_supply_a_late_reply() {
        for script in [
            "read -r line\nprintf 'not-json\\n'\nread -r line\nprintf '{\"text\":\"late\"}\\n'\n",
            // readがブロックするので子sleepプロセスを作らず期限切れを再現する。
            "read -r line\nread -r line\nprintf '{\"text\":\"late\"}\\n'\n",
        ] {
            let path = std::env::temp_dir()
                .join(format!("dogido-haiku-helper-{}.sh", uuid::Uuid::new_v4()));
            std::fs::write(&path, script).unwrap();
            let mut helper = Helper::start(Path::new("/bin/sh"), &path).unwrap();
            let first = helper
                .exchange_with_timeout(json!({"op":"test"}), Duration::from_millis(100))
                .await;
            assert!(first.is_err());
            assert!(helper.child.try_wait().unwrap().is_some());
            assert!(
                helper
                    .prepare(&prompt_request())
                    .await
                    .unwrap_err()
                    .to_string()
                    .contains("closed after failure")
            );
            assert!(
                helper
                    .exchange(json!({"op":"test_again"}))
                    .await
                    .unwrap_err()
                    .to_string()
                    .contains("closed after failure")
            );
            helper.finish(true).await.unwrap();
            std::fs::remove_file(path).unwrap();
        }
    }
}

#[cfg(all(test, unix))]
#[path = "python_worker/reading_tests.rs"]
mod reading_tests;
