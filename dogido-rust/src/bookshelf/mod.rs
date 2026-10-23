//! ゲーム非依存の本棚。loopback専用で、他のUIへの導線は作らない。
mod research;
mod poem_edit;
pub mod captures;
pub mod store;
#[cfg(test)]
mod tests;
use anyhow::{Context, Result, ensure};
use axum::{
    Json, Router,
    body::Body,
    extract::{DefaultBodyLimit, Path, Request, State},
    http::{StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, put},
};
use chrono::Utc;
use serde_json::{Value, json};
use std::{io::Write, path::PathBuf, sync::Arc};
#[derive(Clone)]
struct App {
    store: store::Store,
    ui: PathBuf,
    csrf: String,
    origin: String,
    authority: String,
    memory_enabled: bool,
}
#[derive(Clone, serde::Deserialize)]
pub struct Config {
    pub memory: PathBuf,
    pub photos: PathBuf,
    #[serde(default = "crate::screenshot::default_directory")]
    pub screenshots: PathBuf,
    pub ui: PathBuf,
    #[serde(default = "enabled")]
    pub memory_enabled: bool,
}
fn enabled() -> bool {
    true
}
type ApiResult = Result<Json<Value>, ApiError>;
struct ApiError(anyhow::Error);
impl<E: Into<anyhow::Error>> From<E> for ApiError {
    fn from(e: E) -> Self {
        Self(e.into())
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let reason = self.0.to_string();
        let (status, detail) = match reason.as_str() {
            "changed" | "reading_changed" | "saved_poem_changed" | "source_poem_changed"
            | "parent_revision_mismatch" | "revision_id_conflict" => (
                StatusCode::CONFLICT,
                "別の画面から更新されています。入力を控えて本棚を開き直してください。",
            ),
            "recovery_expired" => (StatusCode::GONE, "復元期限の30日を過ぎています。"),
            "photo_file_missing" => (
                StatusCode::NOT_FOUND,
                "写真ファイルが見つからないため復元できません。",
            ),
            "memory_disabled" => (
                StatusCode::CONFLICT,
                "本体設定で記憶が無効になっています。保存できません。",
            ),
            _ => (
                StatusCode::BAD_REQUEST,
                "保存できませんでした。入力内容または保存先を確認してください。",
            ),
        };
        tracing::warn!(event="bookshelf_request_failed",error=%self.0);
        (status, Json(json!({"detail":detail}))).into_response()
    }
}
async fn blocking<T: Send + 'static>(
    f: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T, ApiError> {
    tokio::task::spawn_blocking(f)
        .await
        .map_err(anyhow::Error::from)?
        .map_err(ApiError)
}
async fn guard(State(app): State<Arc<App>>, req: Request, next: Next) -> Response {
    let headers = req.headers();
    let trusted = headers.get(header::HOST).and_then(|v| v.to_str().ok())
        == Some(app.authority.as_str())
        && headers
            .get(header::ORIGIN)
            .is_none_or(|v| v == app.origin.as_str())
        && headers
            .get("sec-fetch-site")
            .is_none_or(|v| v == "same-origin" || v == "none");
    let write = !matches!(
        *req.method(),
        axum::http::Method::GET | axum::http::Method::HEAD
    );
    if !trusted
        || (write
            && headers
                .get("x-dogido-library")
                .and_then(|v| v.to_str().ok())
                != Some(app.csrf.as_str()))
    {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail":"この本棚の画面から操作してください。"})),
        )
            .into_response();
    }
    let mut response = next.run(req).await;
    let h = response.headers_mut();
    h.insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    h.insert("x-content-type-options", "nosniff".parse().unwrap());
    h.insert("referrer-policy", "no-referrer".parse().unwrap());
    // 同じlocalhostでも別ポートのscript/imageから個人データを読み出させない。
    h.insert(
        "cross-origin-resource-policy",
        "same-origin".parse().unwrap(),
    );
    h.insert(header::CONTENT_SECURITY_POLICY,"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'".parse().unwrap());
    response
}
fn bootstrap(app: &App) -> Result<Value> {
    let catalog = store::catalog();
    let mut biomes = serde_json::Map::new();
    let mut english = serde_json::Map::new();
    for e in catalog.iter().filter(|e| e["category"] == "biome") {
        let id = e["catalogId"].as_str().unwrap();
        biomes.insert(format!("minecraft:{id}"), e["raw"]["label"].clone());
        let fallback = id
            .split('_')
            .map(|word| {
                let mut chars = word.chars();
                chars
                    .next()
                    .map(|c| c.to_uppercase().collect::<String>() + chars.as_str())
                    .unwrap_or_default()
            })
            .collect::<Vec<_>>()
            .join(" ");
        english.insert(
            format!("minecraft:{id}"),
            e["raw"]["english"].as_str().unwrap_or(&fallback).into(),
        );
    }
    Ok(
        json!({"catalog":catalog,"poems":store::poems(&app.store.memory,false)?,"player":app.store.player()?,"photos":app.store.photo_snapshot_now()?,"corrections":crate::haiku_record::MemoryStore::new(&app.store.memory).reading_corrections()?,"status":{"active":false,"memoryEnabled":app.memory_enabled,"message":"Minecraftを閉じた状態でも閲覧・編集できます。"},"csrf":app.csrf,"biomes":biomes,"biomeEnglish":english}),
    )
}
async fn boot(State(app): State<Arc<App>>) -> Result<Response, ApiError> {
    let value = blocking(move || bootstrap(&app)).await?;
    let code = format!(
        "window.DOGIDO_BOOTSTRAP={};window.DOGIDO_CATALOG_SAMPLE=window.DOGIDO_BOOTSTRAP.catalog;",
        serde_json::to_string(&value)?
            .replace('<', "\\u003c")
            .replace('\u{2028}', "\\u2028")
            .replace('\u{2029}', "\\u2029")
    );
    Ok((
        [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
        code,
    )
        .into_response())
}
async fn edit(State(app): State<Arc<App>>, Json(value): Json<Value>) -> ApiResult {
    Ok(Json(blocking(move || app.store.edit(&value)).await?))
}
async fn edit_poem(State(app): State<Arc<App>>, Json(edit): Json<poem_edit::Edit>) -> ApiResult {
    Ok(Json(blocking(move || {
        ensure!(app.memory_enabled, "memory_disabled");
        poem_edit::save(&app.store, &edit)
    }).await?))
}
async fn photos(State(app): State<Arc<App>>, Json(value): Json<Value>) -> ApiResult {
    Ok(Json(blocking(move || app.store.photo_edit(&value)).await?))
}
async fn export_poems(
    State(app): State<Arc<App>>,
    Json(period): Json<research::Period>,
) -> ApiResult {
    Ok(Json(
        blocking(move || research::export(&app.store, &period, Utc::now())).await?,
    ))
}
async fn reading(
    State(app): State<Arc<App>>,
    Json(value): Json<crate::catalog_editor::Edit>,
) -> ApiResult {
    Ok(Json(
        blocking(move || {
            ensure!(app.memory_enabled, "memory_disabled");
            let row = crate::haiku_record::MemoryStore::new(&app.store.memory)
                .edit_catalog_reading(&value, Utc::now())?;
            Ok(json!({"correction":row}))
        })
        .await?,
    ))
}
async fn remove_reading(
    State(app): State<Arc<App>>,
    Json(value): Json<crate::catalog_editor::Remove>,
) -> ApiResult {
    Ok(Json(
        blocking(move || {
            ensure!(app.memory_enabled, "memory_disabled");
            crate::haiku_record::MemoryStore::new(&app.store.memory)
                .remove_catalog_reading(&value, Utc::now())?;
            Ok(json!({"removed":true}))
        })
        .await?,
    ))
}
async fn trash_list(State(app): State<Arc<App>>) -> ApiResult {
    Ok(Json(blocking(move || app.store.trash(Utc::now())).await?))
}
async fn library_snapshot(State(app): State<Arc<App>>) -> ApiResult {
    Ok(Json(blocking(move || Ok(json!({
        "poems": store::poems(&app.store.memory, false)?,
        "player": app.store.player()?,
        "photos": app.store.photo_snapshot_now()?,
    }))).await?))
}
async fn trash_change(State(app): State<Arc<App>>, Json(v): Json<Value>) -> ApiResult {
    Ok(Json(
        blocking(move || {
            let id = v["id"].as_str().context("invalid_id")?;
            let restore = v["restore"].as_bool().context("invalid_restore")?;
            match v["kind"].as_str() {
                Some("poem") => {
                    crate::poem_trash::change(&app.store.memory, id, restore, Utc::now())?
                }
                Some("photo") => app.store.photo_change(id, restore, Utc::now())?,
                _ => anyhow::bail!("invalid_kind"),
            }
            app.store.trash(Utc::now())
        })
        .await?,
    ))
}
async fn photo_image(
    State(app): State<Arc<App>>,
    Path(id): Path<String>,
) -> Result<Response, ApiError> {
    let bytes = blocking(move || app.store.image_bytes(&id)).await?;
    Ok(([(header::CONTENT_TYPE, "image/png")], bytes).into_response())
}
const PUBLIC: &[&str] = &[
    "poem-editor.js",
    "poem-editor.css",
    "index.html",
    "styles.css",
    "photo-layout.css",
    "poem-metadata.css",
    "library-navigation.css",
    "readability.css",
    "catalog-player-notes.css",
    "cover.css",
    "photo-library.css",
    "live-library.css",
    "bookshelf-controls.css",
    "poem-record.css",
    "poem-record.js",
    "poem-export.js",
    "model.js",
    "catalog-player-notes.js",
    "app.js",
    "photo-model.js",
    "photo-library-model.js",
    "live-library.js",
    "photo-library.js",
    "bookshelf-controls.js",
    "reading-model.js",
    "assets/bookshelf-cover-v1.png",
    "assets/bookshelf-open-scroll-v4.png",
    "assets/poems-cover-pixel-v1.png",
    "assets/catalog-cover-pixel-v1.png",
    "assets/photos-cover-pixel-v1.png",
    "assets/character.png",
];
async fn static_file(State(app): State<Arc<App>>, req: Request) -> Response {
    if req.method() != axum::http::Method::GET && req.method() != axum::http::Method::HEAD {
        return StatusCode::METHOD_NOT_ALLOWED.into_response();
    }
    let path = req.uri().path().trim_start_matches('/');
    let path = if path.is_empty() { "index.html" } else { path };
    if !PUBLIC.contains(&path) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let mime = if path.ends_with(".html") {
        "text/html; charset=utf-8"
    } else if path.ends_with(".css") {
        "text/css; charset=utf-8"
    } else if path.ends_with(".js") {
        "text/javascript; charset=utf-8"
    } else {
        "image/png"
    };
    match tokio::fs::read(app.ui.join(path)).await {
        Ok(bytes) => ([(header::CONTENT_TYPE, mime)], Body::from(bytes)).into_response(),
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}
fn router(app: Arc<App>) -> Router {
    Router::new()
        .route("/bootstrap.js", get(boot))
        .route("/api/library", get(library_snapshot).put(edit))
        .route("/api/poems/current", put(edit_poem))
        .route("/api/poems/export", axum::routing::post(export_poems))
        .route("/api/photos", put(photos))
        .route("/api/readings", put(reading).delete(remove_reading))
        .route("/api/trash", get(trash_list).put(trash_change))
        .route("/api/photos/{id}/image", get(photo_image))
        .fallback(static_file)
        .layer(DefaultBodyLimit::max(128 * 1024))
        .layer(middleware::from_fn_with_state(app.clone(), guard))
        .with_state(app)
}
pub async fn run(config: Config, port: u16) -> Result<()> {
    ensure!(
        config.ui.join("index.html").is_file(),
        "bookshelf UI missing"
    );
    // 旧試作DBを見つけた場合、空の新保管庫で隠さず移行を求める。
    if let Some(home) = std::env::var_os("HOME") {
        let old = PathBuf::from(home).join(".dogido/players/default");
        ensure!(
            !old.join("bookshelf.sqlite3").exists() && !old.join("photos.sqlite3").exists(),
            "旧本棚データがあります。先に移行が必要です。"
        );
    }
    let store = store::Store {
        screenshots: config.screenshots,
        memory: config.memory,
        photos: config.photos,
    };
    let upkeep = store.clone();
    if let Err(e) = blocking(move || upkeep.purge(Utc::now())).await {
        tracing::warn!(event="bookshelf_cleanup_failed",error=%e.0);
    }
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    let authority = listener.local_addr()?.to_string();
    let origin = format!("http://{authority}");
    let app = Arc::new(App {
        store,
        ui: config.ui,
        csrf: uuid::Uuid::new_v4().to_string(),
        origin: origin.clone(),
        authority,
        memory_enabled: config.memory_enabled,
    });
    let worker = app.store.clone();
    let cleanup = tokio::spawn(async move {
        let mut timer = tokio::time::interval(std::time::Duration::from_secs(3600));
        timer.tick().await;
        loop {
            timer.tick().await;
            let s = worker.clone();
            if let Err(e) = blocking(move || s.purge(Utc::now())).await {
                tracing::warn!(event="bookshelf_cleanup_failed",error=%e.0);
            }
        }
    });
    println!("DOGIDO_LIBRARY_URL={origin}/");
    std::io::stdout().flush()?;
    let result = axum::serve(listener, router(app))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await;
    cleanup.abort();
    result?;
    Ok(())
}
