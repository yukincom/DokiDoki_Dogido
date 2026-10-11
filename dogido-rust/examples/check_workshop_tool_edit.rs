//! 保存済みnative tool引数を既存Rust検証へ渡し、変更行だけを標準出力へ返す。
//! 辞書tokenだけ既存Python補助へ問い合わせる。サーバー・モデル・記憶保存は起動しない。
use anyhow::{Context, Result};
use clap::Parser;
use dogido_rust::{
    python_worker::Helper,
    workshop_edit::Pending,
    workshop_editing::{Engine, Output, PlayerEditResult, normalize_player_edit_tool},
    workshop_projection::Snapshot,
};
use serde_json::{Value, json};
use std::{
    io::{self, BufRead},
    path::PathBuf,
};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    python: PathBuf,
    #[arg(long)]
    helper: PathBuf,
}

/// 原文と句は試験fixture、argumentsだけがモデル出力。正規化・編集検査・CASを順に確認する。
async fn validate(helper: &mut Helper, request: &Value) -> Result<Value> {
    let frame = normalize_player_edit_tool(
        &request["workshop"],
        request["text"]
            .as_str()
            .context("missing raw player text")?,
        &request["arguments"],
    )?;
    let Output::PlayerEdit(result) = Engine::default().run(helper, &frame).await? else {
        anyhow::bail!("unexpected edit result");
    };
    if !result.failure_reasons().is_empty() {
        return Ok(
            json!({"validated":false,"saved":false,"failure_reasons":result.failure_reasons()}),
        );
    }
    let PlayerEditResult::Validated(result) = result else {
        anyhow::bail!("edit rejected")
    };
    let snapshot = Snapshot::from_view(&request["workshop"])?;
    let working = if snapshot.pending_revision_lines.len() == 3 {
        &snapshot.pending_revision_lines
    } else {
        &snapshot.current_lines
    };
    let pending = Pending::stage(
        &snapshot.current_lines,
        working,
        result.lines,
        result.target_line_index.context("missing edit target")?,
    )?;
    Ok(
        json!({"validated":true,"saved":false,"expected_text":frame["expected_text"],
        "text":result.text,"surface_text":result.surface_text,"edits":pending.edits()}),
    )
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    let mut helper = Helper::start(&args.python, &args.helper)?;
    let result = async {
        for line in io::stdin().lock().lines() {
            let request: Value = serde_json::from_str(&line?)?;
            let output = match validate(&mut helper, &request).await {
                Ok(output) => output,
                Err(error) => json!({"validated":false,"saved":false,"reason":error.to_string()}),
            };
            println!("{output}");
        }
        Ok(())
    }
    .await;
    helper.finish(result.is_err()).await?;
    result
}
