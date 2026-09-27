use std::{io::Write, net::SocketAddr, path::PathBuf, time::Duration};

use anyhow::{Context, Result, ensure};
use clap::{Parser, Subcommand};
use dogido_rust::{
    dialogue::{Dialogue, DialogueConfig},
    llm::RigLlm,
    planner::{self, PreparedPlan},
    server::{Application, ServerConfig},
    types::GenerationRequest,
};

#[derive(Parser)]
#[command(
    version,
    about = "ドギドRust移植用。接続専用サーバー、LLM接続試験、要求検査。"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
// 一度だけ解析する起動引数。サーバーの反復処理・待ち列へこのenumは保持しない。
#[allow(clippy::large_enum_variant)]
enum Command {
    /// ローカル知識検索結果の回答投影を確認する。ネットワーク・モデル生成なし。
    RenderKnowledge { request: PathBuf },
    /// 通常雑談plannerだけを比較。状態機械の投影fixtureを読み、発話・操作・保存はしない。
    PlanChat {
        request: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8080/v1")]
        base_url: String,
        #[arg(long, default_value_t = 20_000)]
        timeout_ms: u64,
    },
    /// 接続専用HTTPサーバー。AI・音声・記憶には接続しない。
    Serve {
        #[arg(long, default_value = "127.0.0.1:5056")]
        listen: SocketAddr,
    },
    /// 冒険会話と自動川柳の試験。Python補助、既存モデル、VOICEVOXを使う。
    ServeDialogue {
        #[arg(long, default_value = "127.0.0.1:5056")]
        listen: SocketAddr,
        #[arg(long, default_value = "python3")]
        python: PathBuf,
        #[arg(long, default_value = "default_model")]
        model: String,
        #[arg(long, default_value = "http://127.0.0.1:8080/v1")]
        base_url: String,
        #[arg(long, default_value = "http://127.0.0.1:50021")]
        voicevox_url: String,
        #[arg(long, default_value_t = 21)]
        speaker: u32,
        #[arg(long, default_value_t = 0.88)]
        speed: f64,
        /// 既存設定から移植済み警告に必要な値だけを受け取るJSON。
        #[arg(long, default_value = "{}")]
        warning_settings: String,
        #[arg(long, default_value = "{}")]
        combat_settings: String,
        #[arg(long, default_value = "{}")]
        haiku_settings: String,
        #[arg(long, default_value = "/usr/bin/afplay")]
        audio_player: PathBuf,
        #[arg(long, default_value = ".dogido_tmp/rust-dialogue")]
        audio_dir: PathBuf,
        #[arg(long, default_value_t = 72)]
        max_tokens: u64,
        #[arg(long, default_value_t = 20_000)]
        timeout_ms: u64,
        #[arg(long, default_value = "auto")]
        reading_engine: String,
        #[arg(long, default_value_t = 0.0)]
        pitch: f64,
        #[arg(long, default_value_t = 1.0)]
        volume: f64,
        #[arg(long)]
        no_audio: bool,
    },
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
        Command::RenderKnowledge { request } => {
            let value = serde_json::from_slice(&std::fs::read(request)?)?;
            println!(
                "{}",
                serde_json::to_string(&dogido_rust::knowledge::render(&value))?
            );
        }
        Command::PlanChat {
            request,
            base_url,
            timeout_ms,
        } => {
            let input: PreparedPlan = serde_json::from_slice(&std::fs::read(&request)?)?;
            let api_key = std::env::var("DOGIDO_LLM_API_KEY")
                .ok()
                .filter(|s| !s.is_empty());
            let client = RigLlm::new(
                &base_url,
                api_key.as_deref(),
                Duration::from_millis(timeout_ms),
            )?;
            let report = planner::run(&client, &input).await?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::Serve { listen } => serve(listen, None).await?,
        Command::ServeDialogue {
            listen,
            python,
            model,
            base_url,
            voicevox_url,
            speaker,
            speed,
            warning_settings,
            combat_settings,
            haiku_settings,
            audio_player,
            audio_dir,
            no_audio,
            max_tokens,
            timeout_ms,
            reading_engine,
            pitch,
            volume,
        } => {
            let dialogue = Dialogue::new(DialogueConfig {
                python,
                model,
                base_url,
                voicevox_url,
                speaker,
                speed,
                warnings: serde_json::from_str(&warning_settings)?,
                combat: dogido_rust::combat::model::Settings::merged(&serde_json::from_str(
                    &combat_settings,
                )?)?,
                haiku: serde_json::from_str(&haiku_settings)?,
                player: audio_player,
                audio_dir,
                audio_enabled: !no_audio,
                max_tokens,
                timeout_ms,
                reading_engine,
                pitch,
                volume,
                ..DialogueConfig::default()
            })?;
            serve(listen, Some(dialogue)).await?;
        }
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

async fn serve(listen: SocketAddr, dialogue: Option<std::sync::Arc<Dialogue>>) -> Result<()> {
    ensure!(
        listen.ip().is_loopback(),
        "connection-test server requires a loopback address"
    );
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .with_target(false)
        .with_ansi(false)
        .with_writer(std::io::stderr)
        .try_init()
        .ok();
    // 停止signalを先に登録し、起動完了表示の直後のSIGTERMも回収する。
    #[cfg(unix)]
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    #[cfg(unix)]
    let mut interrupt = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())?;
    let listener = tokio::net::TcpListener::bind(listen)
        .await
        .context("cannot bind Rust server")?;
    let address = listener.local_addr()?;
    let enabled = dialogue.is_some();
    let app = Application::new(ServerConfig {
        dialogue,
        auth_token: std::env::var("DOGIDO_AUTH_TOKEN").ok(),
        ..ServerConfig::default()
    });
    println!(
        "{}",
        serde_json::json!({"event": "server_listening", "address": address.to_string(),
        "phase": if enabled {"dialogue_preview"} else {"connection_only"}, "llm_enabled": enabled})
    );
    std::io::stdout().flush()?;
    if enabled {
        tracing::info!("会話入力: http://{address}/rust-chat — 会話・戦闘・自動川柳の移行モード");
    }
    tracing::info!("接続画面: http://{address}/dogido — 終了は Ctrl+C");
    let shutdown = async move {
        #[cfg(unix)]
        tokio::select! {
            _ = interrupt.recv() => {},
            _ = terminate.recv() => {},
        }
        #[cfg(not(unix))]
        {
            let _ = tokio::signal::ctrl_c().await;
        }
    };
    let result = axum::serve(listener, app.router())
        .with_graceful_shutdown(shutdown)
        .await;
    app.shutdown().await?;
    result.context("Rust HTTP server failed")
}

fn read_request(path: &PathBuf) -> Result<GenerationRequest> {
    let bytes = std::fs::read(path).with_context(|| format!("cannot read {}", path.display()))?;
    let input: GenerationRequest =
        serde_json::from_slice(&bytes).context("invalid generation request JSON")?;
    input.validate()?;
    Ok(input)
}
