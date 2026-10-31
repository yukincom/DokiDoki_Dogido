//! 音声入力プロセスが所有するwhisper.cppの常駐補助。
//! 発話の取消は推論だけを中断して応答を回収し、モデルは次の発話へ使い回す。
//! 録音の休止とモデルの寿命を分け、音声入力終了時に所有する子を回収する。
use super::{
    Settings,
    process::{self, Output, OwnedChild},
};
use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{path::Path, sync::Arc, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines},
    process::{ChildStdin, ChildStdout, Command},
    sync::watch,
    task::JoinHandle,
    time::{Instant, timeout_at},
};

struct Worker {
    child: OwnedChild,
    input: ChildStdin,
    output: Lines<BufReader<ChildStdout>>,
    stderr: JoinHandle<Vec<u8>>,
}
impl Worker {
    async fn send(&mut self, request: Value) -> Result<()> {
        let mut bytes = serde_json::to_vec(&request)?;
        bytes.push(b'\n');
        self.input.write_all(&bytes).await?;
        self.input.flush().await?;
        Ok(())
    }

    async fn receive(&mut self) -> Result<Value> {
        // next_lineは取消で読みかけの行を失わない。取消後も同じ応答を最後まで回収する。
        let line = self
            .output
            .next_line()
            .await?
            .context("Whisper補助との通信が終了しました")?;
        serde_json::from_str(&line).context("Whisper補助との通信が終了しました")
    }

    async fn close(mut self) -> Vec<u8> {
        let _ = self.input.shutdown().await;
        // EOFで推論を中断してモデルを解放できる。既存の100msの猶予後は強制回収。
        if matches!(
            tokio::time::timeout(Duration::from_millis(100), self.child.child.wait()).await,
            Ok(Ok(_))
        ) {
            self.child.completed();
        } else {
            self.child.stop().await;
        }
        process::finish_reader(self.stderr).await
    }
}

#[derive(Deserialize)]
struct Response {
    success: bool,
    stdout: String,
    stderr: String,
}

pub(super) struct Whisper {
    settings: Arc<Settings>,
    shutdown: watch::Receiver<bool>,
    worker: Option<Worker>,
    next_id: u64,
}
impl Whisper {
    /// 起動時にモデルを読み込む。voice off／セッション未接続でも一度準備する。
    pub async fn start(
        settings: Arc<Settings>,
        shutdown: watch::Receiver<bool>,
    ) -> Result<Option<Self>> {
        let mut this = Self {
            settings,
            shutdown,
            worker: None,
            next_id: 0,
        };
        let mut stop = this.shutdown.clone();
        if this.load(&mut stop).await? {
            Ok(Some(this))
        } else {
            Ok(None)
        }
    }

    async fn load(&mut self, stop: &mut watch::Receiver<bool>) -> Result<bool> {
        if *stop.borrow() || *self.shutdown.borrow() {
            return Ok(false);
        }
        let mut command = Command::new(&self.settings.whisper_worker);
        command.arg("--model").arg(&self.settings.whisper_model);
        if !self.settings.use_gpu {
            command.arg("--no-gpu");
        }
        let mut child = OwnedChild::interactive(command)?;
        let input = child.child.stdin.take().unwrap();
        let output = BufReader::new(child.child.stdout.take().unwrap()).lines();
        let stderr = process::read_tail(child.child.stderr.take().unwrap(), 4096, None);
        let mut worker = Worker {
            child,
            input,
            output,
            stderr,
        };
        // 旧CLIのモデル読込を含む60秒の期限を、起動時の読込にも使う。
        let result = tokio::select! { biased;
            _ = process::cancelled(stop) => Ok(false),
            _ = process::cancelled(&mut self.shutdown) => Ok(false),
            result = tokio::time::timeout(Duration::from_secs(60), worker.receive()) => {
                result.context("Whisperモデルの読込が時間内に完了しませんでした").and_then(|value| value).map(|_| true)
            },
        };
        match result {
            Ok(true) => {
                self.worker = Some(worker);
                Ok(true)
            }
            other => {
                let stderr = worker.close().await;
                other.with_context(|| String::from_utf8_lossy(&stderr).into_owned())
            }
        }
    }

    pub async fn recognize(
        &mut self,
        wav: &Path,
        prompt: &str,
        threshold: f64,
        stop: &mut watch::Receiver<bool>,
    ) -> Result<Output> {
        if *stop.borrow() || *self.shutdown.borrow() {
            return Ok(Output::Cancelled);
        }
        // 異常終了・従来の期限超過だけは次の発話で復旧する。通常取消では閉じない。
        if self.worker.is_none() && !self.load(stop).await? {
            return Ok(Output::Cancelled);
        }
        self.next_id += 1;
        let id = self.next_id;
        let worker = self.worker.as_mut().unwrap();
        let request = json!({"op":"transcribe", "id":id, "wav":wav,
            "prompt":prompt, "no_speech_threshold":threshold});
        let deadline = Instant::now() + Duration::from_secs(60);
        let result = async {
            worker.send(request).await?;
            tokio::select! { biased;
                _ = process::cancelled(&mut self.shutdown) => Ok((Output::Cancelled, false)),
                _ = process::cancelled(stop) => {
                    worker.send(json!({"op":"cancel", "id":id})).await?;
                    // 取消済みの応答を読み切ってから次の入力へ進む。モデルは保持する。
                    let drained = tokio::select! { biased;
                        _ = process::cancelled(&mut self.shutdown) => false,
                        result = timeout_at(deadline, worker.receive()) => matches!(result, Ok(Ok(_))),
                    };
                    Ok((Output::Cancelled, drained))
                },
                result = timeout_at(deadline, worker.receive()) => match result {
                    Err(_) => Ok((Output::Timeout, false)),
                    Ok(result) => {
                        let response: Response = serde_json::from_value(result?)?;
                        Ok((Output::Done { success: response.success,
                            stdout: response.stdout.into_bytes(), stderr: response.stderr.into_bytes() }, true))
                    },
                },
            }
        }.await;
        if !matches!(&result, Ok((_, true))) {
            self.close().await;
        }
        result.map(|(output, _)| output)
    }

    pub async fn close(&mut self) {
        if let Some(worker) = self.worker.take() {
            worker.close().await;
        }
    }
}
