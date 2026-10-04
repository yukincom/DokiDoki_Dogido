//! 音声入力が起動する録音・VAD・Whisperの子プロセスと、その出力パイプの寿命を管理する。
//! Commandを専用のプロセスグループで起動し、正常終了では出力を回収、取消・期限超過ではグループを停止する。
//! runは終了成否とstdout/stderrの末尾、またはTimeout/Cancelledを返す。外部で起動済みのサービスは対象に含めない。
use super::transport::Reporter;
use anyhow::{Context, Result};
use std::{process::Stdio, time::Duration};
use tokio::{
    io::{AsyncRead, AsyncReadExt},
    process::{Child, Command},
    sync::watch,
    task::JoinHandle,
};

/// stop=trueまたは全送信者の消滅まで待つ。制御元が消えた場合も、処理を続けず取消側へ進める。
pub async fn cancelled(stop: &mut watch::Receiver<bool>) {
    while !*stop.borrow_and_update() {
        if stop.changed().await.is_err() {
            break;
        }
    }
}

/// 自分が起動した直接の子と、そのPIDをグループIDとする子孫への停止権限を保持する。
pub struct OwnedChild {
    pub child: Child,
    group: Option<i32>,
}

impl OwnedChild {
    /// Unixのprocess_group(0)で子を新グループの先頭にする。標準入力を閉じ、出力2本を呼出元が読める形にする。
    pub fn spawn(mut command: Command) -> Result<Self> {
        use std::os::unix::process::CommandExt;
        command.as_std_mut().process_group(0);
        command
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let child = command
            .spawn()
            .context("音声入力の子プロセスを起動できません")?;
        let group = child.id().map(|id| id as i32);
        Ok(Self { child, group })
    }
    fn signal(&self, signal: i32) {
        if let Some(group) = self.group {
            // groupは自分がprocess_group(0)で起動した子のPIDだけ。0や既存PIDを受け取らない。
            unsafe {
                libc::kill(-group, signal);
            }
        }
    }
    /// グループへSIGTERMを送り、100ms待ってSIGKILLを送り、直接の子をwaitで回収する。
    /// stop途中ではグループIDを保持し、子を回収してから解除する。
    pub async fn stop(&mut self) {
        self.signal(libc::SIGTERM);
        // この猶予中はまだwaitせず、グループ先頭の子と子孫がTERMで終了処理をする時間を確保する。
        tokio::time::sleep(Duration::from_millis(100)).await;
        self.signal(libc::SIGKILL);
        let _ = self.child.wait().await;
        self.group = None;
    }
    /// waitで正常に終了状態を取得した後、Dropが既に終了したグループへ信号を送らないよう所有を解除する。
    pub fn completed(&mut self) {
        self.group = None;
    }
}

// awaitできない破棄経路では、所有が残るグループへ即SIGKILLを送る。通常の回収はstopまたはwait＋completedで行う。
impl Drop for OwnedChild {
    fn drop(&mut self) {
        self.signal(libc::SIGKILL);
    }
}

/// パイプを別taskで読み続け、書き手の詰まりを防ぎながら末尾limitバイトだけを保持する。
/// live指定時は改行済みの行をReporterへ渡す。長い未改行部分の保持は4096バイトを超えた時点で切る。
pub fn read_tail<R: AsyncRead + Unpin + Send + 'static>(
    mut reader: R,
    limit: usize,
    live: Option<Reporter>,
) -> JoinHandle<Vec<u8>> {
    tokio::spawn(async move {
        let mut tail = Vec::new();
        let mut pending = Vec::new();
        let mut buffer = [0; 4096];
        loop {
            match reader.read(&mut buffer).await {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if let Some(report) = &live {
                        for byte in &buffer[..n] {
                            pending.push(*byte);
                            if *byte == b'\n' {
                                report.capture_line(&pending);
                                pending.clear();
                            } else if pending.len() > 4096 {
                                pending.clear();
                            }
                        }
                    }
                    tail.extend_from_slice(&buffer[..n]);
                    if tail.len() > limit {
                        tail.drain(..tail.len() - limit);
                    }
                }
            }
        }
        tail
    })
}

/// 読取りtaskの終了を最大1秒待つ。パイプが閉じず残る場合はabortして回収し、診断用出力を空で返す。
pub async fn finish_reader(mut task: JoinHandle<Vec<u8>>) -> Vec<u8> {
    match tokio::time::timeout(Duration::from_secs(1), &mut task).await {
        Ok(result) => result.unwrap_or_default(),
        Err(_) => {
            task.abort();
            let _ = task.await;
            Vec::new()
        }
    }
}

/// 実行結果。Doneのsuccessは終了コードだけを示し、出力本文の検査はVAD／STTの呼出元が行う。
pub enum Output {
    Done {
        success: bool,
        stdout: Vec<u8>,
        stderr: Vec<u8>,
    },
    Timeout,
    Cancelled,
}

/// 一つのCLI実行を取消または指定期限まで待ち、stdoutは末尾1MiB、stderrは末尾4096バイトを回収する。
/// 取消済みなら起動せず、実行中の取消・期限超過・wait失敗では停止を待つ。両読取りtaskも戻る前に回収する。
pub async fn run(
    command: Command,
    timeout: Duration,
    stop: &mut watch::Receiver<bool>,
) -> Result<Output> {
    if *stop.borrow() {
        return Ok(Output::Cancelled);
    }
    let mut child = OwnedChild::spawn(command)?;
    let out = read_tail(child.child.stdout.take().unwrap(), 1_048_576, None);
    let err = read_tail(child.child.stderr.take().unwrap(), 4096, None);
    let status = tokio::select! {
        biased;
        _ = cancelled(stop) => None,
        result = tokio::time::timeout(timeout, child.child.wait()) => Some(result),
    };
    let output = match status {
        None => {
            child.stop().await;
            Output::Cancelled
        }
        Some(Err(_)) => {
            child.stop().await;
            Output::Timeout
        }
        Some(Ok(result)) => {
            match &result {
                Ok(_) => child.completed(),
                Err(_) => child.stop().await,
            }
            let stdout = finish_reader(out).await;
            let stderr = finish_reader(err).await;
            return Ok(Output::Done {
                success: result?.success(),
                stdout,
                stderr,
            });
        }
    };
    finish_reader(out).await;
    finish_reader(err).await;
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn hung_process_times_out_and_is_reaped() {
        let (_tx, mut stop) = watch::channel(false);
        let mut command = Command::new("/bin/sleep");
        command.arg("30");
        let started = std::time::Instant::now();
        assert!(matches!(
            run(command, Duration::from_millis(30), &mut stop)
                .await
                .unwrap(),
            Output::Timeout
        ));
        assert!(started.elapsed() < Duration::from_secs(3));
    }
    #[tokio::test]
    async fn already_cancelled_never_spawns() {
        let (_tx, mut stop) = watch::channel(true);
        assert!(matches!(
            run(
                Command::new("/nonexistent/never-spawn"),
                Duration::from_secs(1),
                &mut stop
            )
            .await
            .unwrap(),
            Output::Cancelled
        ));
    }
}
