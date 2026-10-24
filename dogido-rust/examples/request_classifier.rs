//! 採用済みローカルモデルを既存Helperで検査する診断CLI。本体・ゲームへ接続しない。
//! JSONLの原文履歴を一件ずつ分類し、ID付き結果だけをstdoutへ返す。
use anyhow::{Context, Result};
use clap::Parser;
use dogido_rust::request_classifier::{Classifier, Input, Mode};
use serde_json::{Value, json};
use std::{path::PathBuf, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    sync::watch,
};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    manifest: PathBuf,
    /// 本体が選ぶ領域を明示する。manifestと不一致なら失敗する。
    #[arg(long, value_parser = ["normal", "senryu"])]
    mode: String,
    /// checkpoint_pathを相対解決するモデル保管リポジトリ。
    #[arg(long)]
    model_root: PathBuf,
    #[arg(long)]
    python: PathBuf,
    #[arg(long)]
    worker: PathBuf,
    #[arg(long)]
    input: PathBuf,
    #[arg(long, default_value_t = 60)]
    load_timeout_seconds: u64,
    #[arg(long, default_value_t = 5)]
    timeout_seconds: u64,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    let (cancel_tx, mut cancel_rx) = watch::channel(false);
    tokio::spawn(async move {
        let _ = tokio::signal::ctrl_c().await;
        let _ = cancel_tx.send(true);
    });
    let mut model = Classifier::start(
        &args.python,
        &args.worker,
        &args.manifest,
        &args.model_root,
        if args.mode == "senryu" {
            Mode::Senryu
        } else {
            Mode::Normal
        },
        Duration::from_secs(args.load_timeout_seconds),
        &mut cancel_rx,
    )
    .await?;
    let result = async {
        let mut lines = BufReader::new(tokio::fs::File::open(&args.input).await?).lines();
        while let Some(line) = lines.next_line().await? {
            let row: Value = serde_json::from_str(&line)?;
            let id = row["id"].as_str().context("each case needs a string id")?;
            let input: Input = serde_json::from_value(row.clone())?;
            let started = std::time::Instant::now();
            let prediction = model.classify(&input, Duration::from_secs(args.timeout_seconds), &mut cancel_rx).await?;
            println!("{}", json!({"id":id, "prediction":prediction, "round_trip_ms":started.elapsed().as_secs_f64()*1000.0}));
        }
        Ok::<_, anyhow::Error>(())
    }.await;
    let close = model.close().await;
    result?;
    close
}
