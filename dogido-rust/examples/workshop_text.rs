//! Local text window backed by the same workshop runtime as Minecraft.
use anyhow::{Context, Result, ensure};
use axum::{
    Json, Router,
    extract::{Request, State},
    http::{StatusCode, header},
    middleware::{self, Next},
    response::{Html, Response},
    routing::{get, post},
};
use clap::Parser;
use dogido_rust::{
    dialogue::{Dialogue, DialogueConfig, HaikuSettings, WebSettings},
    poem_book,
};
use serde_json::{Value, json};
use std::{net::SocketAddr, path::PathBuf, sync::Arc};
use tokio::sync::Notify;

#[derive(Parser)]
struct Args {
    #[arg(long, default_value = "127.0.0.1:5057")]
    listen: SocketAddr,
    #[arg(long)]
    memory_dir: PathBuf,
    #[arg(long)]
    python: PathBuf,
    #[arg(long)]
    model: String,
    #[arg(long, default_value = "http://127.0.0.1:8080/v1")]
    base_url: String,
    #[arg(long, default_value = "{}")]
    haiku_settings: String,
    #[arg(long, default_value_t = 72)]
    max_tokens: u64,
    #[arg(long, default_value_t = 60_000)]
    timeout_ms: u64,
    #[arg(long, default_value = ".dogido_tmp/workshop-text/prompts.json")]
    prompt_file: PathBuf,
    /// One-time recovery of the previous text window's completed conversation.
    #[arg(long)]
    resume: Option<PathBuf>,
}

#[derive(Clone)]
struct Window {
    dialogue: Arc<Dialogue>,
    memory_dir: PathBuf,
    stop: Arc<Notify>,
    port: u16,
    prompt_file: PathBuf,
    prompt_lock: Arc<std::sync::Mutex<()>>,
}

async fn access(State(w): State<Window>, request: Request, next: Next) -> Response {
    let allowed = [
        format!("127.0.0.1:{}", w.port),
        format!("localhost:{}", w.port),
    ];
    let host = request
        .headers()
        .get(header::HOST)
        .and_then(|h| h.to_str().ok())
        .unwrap_or("");
    let origin = request
        .headers()
        .get(header::ORIGIN)
        .and_then(|h| h.to_str().ok());
    if !allowed.iter().any(|a| a == host)
        || origin.is_some_and(|o| !allowed.iter().any(|a| o == format!("http://{a}")))
    {
        return Response::builder()
            .status(StatusCode::FORBIDDEN)
            .body("local window only".into())
            .unwrap();
    }
    let mut response = next.run(request).await;
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    response.headers_mut().insert(header::CONTENT_SECURITY_POLICY,
        "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'".parse().unwrap());
    response
}

async fn open(State(w): State<Window>, Json(body): Json<Value>) -> Result<Json<Value>, StatusCode> {
    let key = body["key"].as_str().ok_or(StatusCode::BAD_REQUEST)?;
    let poems = poem_book::load(&w.memory_dir).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let poem = poems.get(key).ok_or(StatusCode::NOT_FOUND)?;
    if let Some(previous) = body["previous_session"].as_str() {
        w.dialogue.close(previous);
    }
    if w.dialogue.snapshot(None)["sessions"]
        .as_array()
        .is_some_and(|s| s.len() >= 8)
    {
        return Err(StatusCode::TOO_MANY_REQUESTS);
    }
    let sid = format!("text_{}", uuid::Uuid::new_v4().simple());
    w.dialogue.register(&sid, "プレイヤー", true);
    if w.dialogue
        .open_saved_text_workshop(&sid, poem.clone())
        .is_err()
    {
        w.dialogue.close(&sid);
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }
    Ok(Json(json!({"session_id":sid})))
}

async fn snapshot(State(w): State<Window>, Json(body): Json<Value>) -> Json<Value> {
    let sid = body["session_id"].as_str().unwrap_or("");
    Json(
        json!({"dialogue":w.dialogue.snapshot(Some(sid)),"workshop":w.dialogue.workshop_snapshot(sid,0),"text_state":w.dialogue.text_workshop_state(sid)}),
    )
}

async fn input(State(w): State<Window>, Json(body): Json<Value>) -> Json<Value> {
    let sid = body["session_id"].as_str().unwrap_or("");
    let text = body["text"].as_str().unwrap_or("");
    if text.chars().count() > 1000 {
        return Json(json!({"accepted":false,"reason":"発言は1000文字までです。"}));
    }
    Json(w.dialogue.submit(Some(sid), text, "text"))
}

async fn displayed(State(w): State<Window>, Json(body): Json<Value>) -> Json<Value> {
    Json(json!({"accepted":w.dialogue.acknowledge_workshop_text(
        body["session_id"].as_str().unwrap_or(""),body["turn_id"].as_str().unwrap_or(""))}))
}

async fn prompts(
    State(w): State<Window>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let _guard = w.prompt_lock.lock().unwrap();
    let expected = body["expected_version"].as_u64().ok_or((
        StatusCode::BAD_REQUEST,
        Json(json!({"error":"expected_versionが必要です"})),
    ))?;
    if expected != w.dialogue.text_workshop_prompts()["version"] {
        return Err((
            StatusCode::CONFLICT,
            Json(json!({"error":"別の画面で変更されています。最新の設定を読み直してください。"})),
        ));
    }
    let version = w
        .dialogue
        .set_text_workshop_prompts(body["settings"].clone(), expected)
        .map_err(|e| {
            (
                StatusCode::BAD_REQUEST,
                Json(json!({"error":e.to_string()})),
            )
        })?;
    let save = (|| -> Result<()> {
        if let Some(parent) = w.prompt_file.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let temp = w.prompt_file.with_extension("tmp");
        std::fs::write(&temp, serde_json::to_vec_pretty(&body["settings"])?)?;
        std::fs::rename(temp, &w.prompt_file)?;
        Ok(())
    })();
    Ok(Json(
        json!({"version":version,"saved":save.is_ok(),"warning":save.err().map(|e|e.to_string())}),
    ))
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    ensure!(
        args.listen.ip().is_loopback(),
        "text window is localhost only"
    );
    let entries = poem_book::load(&args.memory_dir)?;
    ensure!(!entries.is_empty(), "no saved poems with line records");
    let listener = tokio::net::TcpListener::bind(args.listen).await?;
    let addr = listener.local_addr()?;
    let temporary =
        std::env::temp_dir().join(format!("dogido-workshop-text-{}", std::process::id()));
    let mut haiku: HaikuSettings = serde_json::from_str(&args.haiku_settings)?;
    haiku.enabled = false;
    haiku.memory_enabled = false;
    haiku.memory_dir = temporary;
    haiku.workshop_open_ms = 86_400_000;
    haiku.workshop_idle_ms = 86_400_000;
    let dialogue = Dialogue::new(DialogueConfig {
        python: args.python,
        model: args.model,
        base_url: args.base_url,
        timeout_ms: args.timeout_ms,
        max_tokens: args.max_tokens,
        audio_enabled: false,
        language_enabled: false,
        haiku,
        web: WebSettings {
            enabled: false,
            ..Default::default()
        },
        ..Default::default()
    })?;
    if args.prompt_file.exists() {
        dialogue.set_text_workshop_prompts(
            serde_json::from_slice(&std::fs::read(&args.prompt_file)?)?,
            0,
        )?;
    }
    if let Some(resume) = args.resume {
        let records: Vec<Value> = serde_json::from_slice(&std::fs::read(resume)?)?;
        for record in records {
            let sid = record["session_id"]
                .as_str()
                .context("missing recovery session")?;
            let key = record["key"].as_str().context("missing recovery poem")?;
            let mut poem = entries
                .get(key)
                .context("recovery poem no longer exists")?
                .clone();
            // Legacy handoffs predate persistent edits and refer to the original.
            if record["snapshot"]["text_state"].is_null() {
                poem.lines = poem.original.prepared.lines.clone();
                poem.revision_id = None;
            }
            dialogue.register(sid, "プレイヤー", true);
            dialogue.open_saved_text_workshop(sid, poem)?;
            dialogue.restore_text_conversation(sid, &record["snapshot"])?;
        }
    }
    let stop = Arc::new(Notify::new());
    let window = Window {
        dialogue: dialogue.clone(),
        memory_dir: args.memory_dir,
        stop: stop.clone(),
        port: addr.port(),
        prompt_file: args.prompt_file,
        prompt_lock: Arc::new(std::sync::Mutex::new(())),
    };
    let app = Router::new()
        .route(
            "/",
            get(|| async { Html(include_str!("../src/server/workshop-text.html")) }),
        )
        .route(
            "/api/poems",
            get(|State(w): State<Window>| async move {
                poem_book::load(&w.memory_dir)
                    .map(|poems| Json(poems.values().map(|p| p.public()).collect::<Vec<_>>()))
                    .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
            }),
        )
        .route("/api/open", post(open))
        .route("/api/snapshot", post(snapshot))
        .route("/api/input", post(input))
        .route("/api/displayed", post(displayed))
        .route(
            "/api/prompts",
            get(|State(w): State<Window>| async move { Json(w.dialogue.text_workshop_prompts()) })
                .post(prompts),
        )
        .route(
            "/api/last-prompt",
            post(|State(w): State<Window>, Json(b): Json<Value>| async move {
                Json(
                    w.dialogue
                        .text_workshop_last_request(b["session_id"].as_str().unwrap_or("")),
                )
            }),
        )
        .route(
            "/api/sessions",
            get(|State(w): State<Window>| async move {
                Json(w.dialogue.snapshot(None)["sessions"].clone())
            }),
        )
        .route(
            "/api/close",
            post(|State(w): State<Window>, Json(b): Json<Value>| async move {
                w.dialogue.close(b["session_id"].as_str().unwrap_or(""));
                Json(json!({"ok":true}))
            }),
        )
        .route(
            "/api/interrupt",
            post(|State(w): State<Window>, Json(b): Json<Value>| async move {
                w.dialogue.interrupt(b["session_id"].as_str().unwrap_or(""));
                Json(json!({"ok":true}))
            }),
        )
        .route(
            "/api/shutdown",
            post(|State(w): State<Window>, Json(_): Json<Value>| async move {
                w.stop.notify_one();
                Json(json!({"ok":true}))
            }),
        )
        .layer(middleware::from_fn_with_state(window.clone(), access))
        .with_state(window);
    println!("川柳の相談室: http://{addr} / 終了は画面の「窓口を終了」または Ctrl+C");
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let stopping = dialogue.clone();
    let result=axum::serve(listener,app).with_graceful_shutdown(async move {
        tokio::select!{_ = stop.notified()=>{},_ = tokio::signal::ctrl_c()=>{},_ = terminate.recv()=>{}}
        stopping.cancel_all();
    }).await;
    dialogue.shutdown().await;
    result?;
    Ok(())
}
