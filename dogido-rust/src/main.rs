use std::{path::PathBuf, time::Duration};

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use dogido_rust::{llm::RigLlm, types::GenerationRequest};

#[derive(Parser)]
#[command(version, about = "ドギドRust移植用。既存MLXへの一回生成と要求検査。")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// 要求の型だけを確認する。ネットワーク・モデル生成なし。
    Check { request: PathBuf },
    /// RigからChat Completionsを一回だけ呼ぶ。サーバーの起動・停止はしない。
    Generate {
        request: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8080/v1")]
        base_url: String,
        #[arg(long, default_value_t = 20_000)]
        timeout_ms: u64,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Check { request } => {
            let input = read_request(&request)?;
            println!(
                "{}",
                serde_json::json!({"ok": true, "kind": input.kind, "messages": input.messages.len()})
            );
        }
        Command::Generate {
            request,
            base_url,
            timeout_ms,
        } => {
            let input = read_request(&request)?;
            let api_key = std::env::var("DOGIDO_LLM_API_KEY")
                .ok()
                .filter(|s| !s.is_empty());
            let client = RigLlm::new(
                &base_url,
                api_key.as_deref(),
                Duration::from_millis(timeout_ms),
            )?;
            let report = client.generate(&input).await?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
    }
    Ok(())
}

fn read_request(path: &PathBuf) -> Result<GenerationRequest> {
    let bytes = std::fs::read(path).with_context(|| format!("cannot read {}", path.display()))?;
    let input: GenerationRequest =
        serde_json::from_slice(&bytes).context("invalid generation request JSON")?;
    input.validate()?;
    Ok(input)
}
