//! 準備済み材料から一句を生成する接続試験。音声・workshop・記憶は変更しない。
use anyhow::Result;
use clap::Parser;
use dogido_rust::{
    haiku::Input,
    haiku_bridge::{self, Route, RouteConfig, RunConfig},
};
use serde::Deserialize;
use std::{path::PathBuf, time::Duration};
use tokio::sync::watch;

#[derive(Parser)]
struct Args {
    request: PathBuf,
    #[arg(long)]
    python: PathBuf,
    #[arg(long, default_value_t = 180_000)]
    timeout_ms: u64,
    /// 取消の再現用。指定時はこの時間後に実行を中止する。
    #[arg(long)]
    cancel_after_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    input: Input,
    chat: RouteConfig,
    haiku: RouteConfig,
}

async fn stop_signal() {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                .expect("cannot install SIGTERM handler");
        tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = terminate.recv() => {} }
    }
    #[cfg(not(unix))]
    let _ = tokio::signal::ctrl_c().await;
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_ansi(false)
        .with_writer(std::io::stderr)
        .init();
    let args = Args::parse();
    let request: Request = serde_json::from_slice(&std::fs::read(args.request)?)?;
    let chat_key = std::env::var("DOGIDO_LLM_API_KEY").ok();
    let haiku_key = std::env::var("DOGIDO_HAIKU_API_KEY")
        .ok()
        .or_else(|| chat_key.clone());
    let chat = Route::new(request.chat, chat_key.as_deref())?;
    let haiku = Route::new(request.haiku, haiku_key.as_deref())?;
    let helper = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("scripts/haiku_tokens.py");
    let (tx, mut cancel) = watch::channel(false);
    let cancellation = tokio::spawn(async move {
        if let Some(ms) = args.cancel_after_ms {
            tokio::time::sleep(Duration::from_millis(ms)).await;
        } else {
            stop_signal().await;
        }
        let _ = tx.send(true);
    });
    let result = haiku_bridge::run(
        RunConfig {
            python: &args.python,
            helper: &helper,
            chat: &chat,
            haiku: &haiku,
            timeout: Duration::from_millis(args.timeout_ms),
        },
        request.input,
        &mut cancel,
    )
    .await;
    cancellation.abort();
    let _ = cancellation.await;
    println!("{}", serde_json::to_string(&result?)?);
    Ok(())
}
