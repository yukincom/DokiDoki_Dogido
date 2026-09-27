//! この入力プロセスが起動した子だけを、取消・timeout時にも回収する。
use super::transport::Reporter;
use anyhow::{Context, Result};
use std::{process::Stdio, time::Duration};
use tokio::{
    io::{AsyncRead, AsyncReadExt},
    process::{Child, Command},
    sync::watch,
    task::JoinHandle,
};

pub async fn cancelled(stop: &mut watch::Receiver<bool>) {
    while !*stop.borrow_and_update() {
        if stop.changed().await.is_err() {
            break;
        }
    }
}

pub struct OwnedChild {
    pub child: Child,
    group: Option<i32>,
}

impl OwnedChild {
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
    pub async fn stop(&mut self) {
        self.signal(libc::SIGTERM);
        // leaderをまだreapせず、group IDを保持したままdescendantにも終了を待つ。
        tokio::time::sleep(Duration::from_millis(100)).await;
        self.signal(libc::SIGKILL);
        let _ = self.child.wait().await;
        self.group = None;
    }
    pub fn completed(&mut self) {
        self.group = None;
    }
}

impl Drop for OwnedChild {
    fn drop(&mut self) {
        self.signal(libc::SIGKILL);
    }
}

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

pub enum Output {
    Done {
        success: bool,
        stdout: Vec<u8>,
        stderr: Vec<u8>,
    },
    Timeout,
    Cancelled,
}

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
