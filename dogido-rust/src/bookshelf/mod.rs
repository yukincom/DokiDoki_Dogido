//! ゲーム非依存の本棚。loopback専用で、他のUIへの導線は作らない。
mod research;
mod research_storage;
mod primary_storage;

pub(crate) fn uses_primary_archive(memory: &std::path::Path) -> anyhow::Result<bool> {
    crate::poem_trash::verify_primary_volume(memory)?;
    if !memory.join(".dogido-research-archive.json").try_exists()? { return Ok(false); }
    let archive = research_archive::load(memory)?.ok_or_else(|| anyhow::anyhow!("primary_archive_unconfigured"))?;
    anyhow::ensure!(archive.config().primary && archive.config().root == memory, "primary_archive_scope");
    Ok(true)
}

mod internal_capture;
mod j_lens;
mod j_lens_translation;
mod data_repository;
mod library_view;
mod player_photos;
pub mod research_archive;
mod poem_edit;
pub mod captures;
pub mod store;
#[cfg(test)]
mod tests;
use anyhow::{Context, Result, ensure};
use axum::{
    Json, Router,
    body::Body,
    extract::{DefaultBodyLimit, Path, Query, Request, State},
    http::{HeaderMap, StatusCode, header},
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
    child_mode: bool,
    admin_token: Option<String>,
    j_lens: Option<j_lens::Config>,
    readout_lock: Arc<std::sync::Mutex<()>>,
    translation_lock: Arc<tokio::sync::Mutex<()>>,
    game_port: u16,
    data_repository: Option<data_repository::Config>,
    publish_lock: Arc<std::sync::Mutex<()>>,
}
#[derive(Clone, serde::Deserialize)]
pub struct Config {
    pub memory: PathBuf,
    #[serde(default)]
    pub capture_dir: Option<PathBuf>,
    #[serde(default)]
    pub j_lens: Option<j_lens::Config>,
    #[serde(default = "default_game_port")]
    pub game_port: u16,
    #[serde(default)]
    pub data_repository: Option<data_repository::Config>,
    pub photos: PathBuf,
    #[serde(default = "crate::screenshot::default_directory")]
    pub screenshots: PathBuf,
    pub ui: PathBuf,
    #[serde(default = "enabled")]
    pub memory_enabled: bool,
    /// 子の原本を開くときは削除・辞書・保存先変更を制限する。省略時は従来の本人用。
    #[serde(default)]
    pub child_mode: bool,
    /// 家庭内の接続係だけが保持する実行ごとの秘密値。画面へは渡さない。
    #[serde(default)]
    pub admin_token: Option<String>,
}
fn enabled() -> bool {
    true
}
fn default_game_port() -> u16 { 5055 }
type ApiResult = Result<Json<Value>, ApiError>;
struct ApiError(anyhow::Error);
#[derive(Clone)]
struct FailureReason(String);
impl<E: Into<anyhow::Error>> From<E> for ApiError {
    fn from(e: E) -> Self {
        Self(e.into())
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let reason = self.0.to_string();
        let diagnostic = (reason == "research_storage_connection_failed").then(|| format!("{:#}", self.0));
        let (status, detail) = match reason.as_str() {
            "translation_not_prepared" => (StatusCode::NOT_FOUND, "翻訳はまだ実行していません。"),
            "translation_busy" => (StatusCode::CONFLICT, "翻訳中です。完了までお待ちください。"),
            "translation_game_running" => (StatusCode::CONFLICT, "ドギド本体を停止してから翻訳してください。"),
            "translation_failed" => (StatusCode::SERVICE_UNAVAILABLE, "翻訳できませんでした。8080のモデルを確認してから再度ボタンを押してください。"),
            "data_repository_unconfigured" => (StatusCode::SERVICE_UNAVAILABLE, "データリポジトリの接続が未設定です。"),
            "data_repository_busy" => (StatusCode::CONFLICT, "アップロード中です。完了までお待ちください。"),
            "data_repository_archive_unready" => (StatusCode::CONFLICT, "研究保存先への保全を完了してからアップロードしてください。"),
            _ if reason.starts_with("data_repository_failed: ") => (StatusCode::BAD_GATEWAY, reason.trim_start_matches("data_repository_failed: ")),
            "j_lens_unconfigured" => (StatusCode::SERVICE_UNAVAILABLE, "このモデルのJ-lensはまだ設定されていません。"),
            "j_lens_not_prepared" => (StatusCode::NOT_FOUND, "候補語はまだ読み出していません。"),
            "j_lens_busy" => (StatusCode::CONFLICT, "別の候補語を読み出しています。少し待って開き直してください。"),
            "j_lens_unsupported_capture" => (StatusCode::UNPROCESSABLE_ENTITY, "この採取記録に対応するJ-lensはありません。"),
            "j_lens_worker_failed" | "j_lens_timeout" => (StatusCode::SERVICE_UNAVAILABLE, "候補語を読み出せませんでした。モデルとJ-lensの設定・対応を確認してください。"),
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
            "player_photo_unavailable" => (StatusCode::SERVICE_UNAVAILABLE, "写真を読めません。保存先SSDの接続を確認してください。"),
            "player_photo_changed" => (StatusCode::CONFLICT, "選択後に元画像が変わりました。写真を選び直してください。"),
            "memory_disabled" => (
                StatusCode::CONFLICT,
                "本体設定で記憶が無効になっています。保存できません。",
            ),
            "reading_read_only" => (
                StatusCode::FORBIDDEN,
                "この読み辞書は閲覧専用です。",
            ),
            "research_original_protected" => (
                StatusCode::CONFLICT,
                "ドギドから届いた研究原本は変更・削除できません。",
            ),
            _ => (
                StatusCode::BAD_REQUEST,
                "保存できませんでした。入力内容または保存先を確認してください。",
            ),
        };
        tracing::warn!(event="bookshelf_request_failed",error=%self.0);
        let mut response = (status, Json(json!({"detail":detail,"diagnostic":diagnostic}))).into_response();
        response.extensions_mut().insert(FailureReason(reason));
        response
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
fn parent_access(app: &App, headers: &HeaderMap) -> bool {
    app.child_mode && app.admin_token.as_ref().is_some_and(|token| {
        !token.is_empty() && headers.get("x-dogido-bookshelf-admin")
            .and_then(|value| value.to_str().ok()) == Some(token.as_str())
    })
}

// UIの表示可否とは別に、保存入口でも家庭内の役割を照合する。
fn family_refusal(app: &App, path: &str, write: bool, parent: bool) -> Option<&'static str> {
    if !app.child_mode { return None; }
    if path == "/api/readings" {
        return Some("読み方の辞書は、おうちの人の本棚で直してもらってな。");
    }
    if write && (path == "/api/research-storage" || path == "/api/poems/export"
        || path.contains("/internal-capture/")) {
        return Some("この本棚では、今ある保存先の句をそのまま使うで。");
    }
    if path == "/api/trash" && !parent {
        return Some("この本棚では句や写真を消されへんねん。消したいときは、おうちの人に頼んでな。");
    }
    None
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
            Json(json!({"detail":if app.child_mode { "本棚の画面を開き直して、もう一度試してな。" } else { "この本棚の画面から操作してください。" }})),
        )
            .into_response();
    }
    let parent = parent_access(&app, headers);
    if let Some(detail) = family_refusal(&app, req.uri().path(), write, parent) {
        return (StatusCode::FORBIDDEN, Json(json!({"detail":detail}))).into_response();
    }
    let mut response = next.run(req).await;
    // 子の画面の失敗は固定文で案内する。保存済みと断言せず、入力の保持をUIへ任せる。
    if app.child_mode && !parent && (response.status().is_client_error() || response.status().is_server_error()) {
        let status = response.status();
        let reason = response.extensions().get::<FailureReason>().map(|value| value.0.as_str());
        let detail = match reason {
            Some("memory_disabled") => "記憶を残す設定がお休み中みたいや。おうちの人に設定を見てもらってな。",
            Some("player_photo_unavailable" | "photo_file_missing") => "写真が見つからへんみたいや。保存先がつながっているか、おうちの人に見てもらってな。",
            Some("research_original_protected") => "オレが撮った元の写真は、そのまま残しておくで。",
            Some("j_lens_not_prepared" | "j_lens_unconfigured" | "translation_not_prepared") => "この記録を読む準備がまだできてへんみたいや。おうちの人に見てもらってな。",
            Some("changed" | "reading_changed" | "saved_poem_changed" | "source_poem_changed"
                | "parent_revision_mismatch" | "revision_id_conflict" | "player_photo_changed") =>
                "別のところで先に更新されたみたいや。書いた内容を控えてから、もう一度開いてな。",
            _ => match status {
                StatusCode::NOT_FOUND => "今はその記録が見つからへんみたいや。少し待って、もう一度開いてな。",
                StatusCode::FORBIDDEN => "その操作はここではできへんねん。おうちの人に見てもらってな。",
                StatusCode::UNPROCESSABLE_ENTITY | StatusCode::BAD_REQUEST => "うまく受け取れへんかったみたいや。入力した内容を確かめて、もう一度試してな。",
                _ => "今はうまく確認できへんみたいや。少し待って、もう一度試してな。",
            },
        };
        response = (status, Json(json!({"detail":detail}))).into_response();
    }
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
#[cfg(test)]
fn bootstrap(app: &App) -> Result<Value> { bootstrap_for(app, false) }
fn bootstrap_for(app: &App, parent: bool) -> Result<Value> {
    let view = app.store.library_view()?;
    let readings = crate::haiku_record::MemoryStore::new(&app.store.memory);
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
        json!({"catalog":catalog,"poems":view["poems"],"journeys":view["journeys"],"player":view["player"],"photos":view["photos"],"corrections":if app.child_mode { Vec::new() } else { readings.reading_corrections()? },"readings_writable":!app.child_mode && app.memory_enabled && readings.readings_writable(),"child_mode":app.child_mode,"can_delete":!app.child_mode || parent,"show_readings":!app.child_mode,"parent_view":parent,"status":{"active":false,"memoryEnabled":app.memory_enabled,"message":"Minecraftを閉じた状態でも閲覧・編集できます。"},"csrf":app.csrf,"biomes":biomes,"biomeEnglish":english,"dataRepository":app.data_repository.as_ref().map(|c|json!({"url":data_repository::URL,"mode":c.mode}))}),
    )
}
async fn boot(State(app): State<Arc<App>>, headers: HeaderMap) -> Result<Response, ApiError> {
    let parent = parent_access(&app, &headers);
    let value = blocking(move || bootstrap_for(&app, parent)).await?;
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
    Ok(Json(blocking(move || {
        ensure!(app.memory_enabled, "memory_disabled");
        app.store.photo_edit(&value)
    }).await?))
}
async fn research_storage(
    State(app): State<Arc<App>>, Json(selection): Json<research_storage::Selection>,
) -> ApiResult {
    Ok(Json(blocking(move || {
        ensure!(app.memory_enabled, "memory_disabled");
        Ok(json!({"researchStorage":app.store.select_research_storage(selection).context("research_storage_connection_failed")?}))
    }).await?))
}
async fn export_poems(
    State(app): State<Arc<App>>,
    Json(period): Json<research::Period>,
) -> ApiResult {
    Ok(Json(
        blocking(move || research::export(&app.store, &period, Utc::now())).await?,
    ))
}
async fn publish_research(State(app): State<Arc<App>>) -> ApiResult {
    Ok(Json(blocking(move || {
        let config = app.data_repository.as_ref().context("data_repository_unconfigured")?;
        let _publish = app.publish_lock.try_lock().map_err(|_| anyhow::anyhow!("data_repository_busy"))?;
        let _readout = app.readout_lock.try_lock().map_err(|_| anyhow::anyhow!("j_lens_busy"))?;
        data_repository::publish(&app.store, app.j_lens.as_ref(), config)
    }).await?))
}
async fn reading(
    State(app): State<Arc<App>>,
    Json(value): Json<crate::catalog_editor::Edit>,
) -> ApiResult {
    Ok(Json(
        blocking(move || {
            ensure!(app.memory_enabled, "memory_disabled");
            let value = value.validate()?;
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
            let value = value.validate()?;
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
    Ok(Json(blocking(move || app.store.library_view()).await?))
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
async fn internal_capture_file(
    State(app): State<Arc<App>>,
    Path((id, index, kind)): Path<(String, usize, String)>,
    Query(query): Query<internal_capture::ValuesQuery>,
) -> Result<Response, ApiError> {
    if kind == "j-lens-translation" {
        let value = blocking(move || {
            let selection = j_lens_translation::Selection {phase:query.phase.context("translation_selection_invalid")?, layer:query.layer as usize};
            let p = app.store.translation_source(&id, index, app.j_lens.as_ref().context("j_lens_unconfigured")?, &selection)?;
            Ok(app.store.translation_cache(&p)?.unwrap_or_else(|| j_lens_translation::originals(&p)))
        }).await?;
        return Ok(Json(value).into_response());
    }
    if kind == "j-lens" {
        let value = blocking(move || app.store.j_lens(&id, index,
            app.j_lens.as_ref().context("j_lens_unconfigured")?, false)).await?;
        return Ok(Json(value).into_response());
    }
    if kind == "values" {
        let values = blocking(move || app.store.internal_capture_values(&id, index, query)).await?;
        return Ok(Json(values).into_response());
    }
    let result = blocking(move || app.store.internal_capture_file(&id, index, &kind)).await?;
    let mime = if result.name == "manifest.json" { "application/json" } else { "application/octet-stream" };
    let stream = tokio_util::io::ReaderStream::new(tokio::fs::File::from_std(result.file));
    Ok(([(header::CONTENT_TYPE, mime.to_string()),
        (header::CONTENT_LENGTH, result.bytes.to_string()),
        (header::CONTENT_DISPOSITION, format!("attachment; filename=\"{}\"", result.name))],
        Body::from_stream(stream)).into_response())
}
async fn prepare_capture_readout(
    State(app): State<Arc<App>>, Path((id, index, kind)): Path<(String, usize, String)>,
    body: axum::body::Bytes,
) -> ApiResult {
    if kind == "j-lens-translation" {
        let selection: j_lens_translation::Selection = serde_json::from_slice(&body)?;
        let _lock = app.translation_lock.clone().try_lock_owned().map_err(|_| anyhow::anyhow!("translation_busy"))?;
        let selected = app.clone();
        let (p, cached) = blocking(move || {
            let p = selected.store.translation_source(&id,index,selected.j_lens.as_ref().context("j_lens_unconfigured")?,&selection)?;
            let cached = selected.store.translation_cache(&p)?;
            Ok((p,cached))
        }).await?;
        if let Some(value) = cached { return Ok(Json(value)); }
        let value = j_lens_translation::translate(&p, app.game_port).await.map_err(|e| {
            if e.to_string() == "translation_game_running" { e } else { e.context("translation_failed") }
        })?;
        return Ok(Json(blocking(move || { app.store.save_translation(&p,&value)?; Ok(value) }).await?));
    }
    Ok(Json(blocking(move || {
        ensure!(kind == "j-lens", "capture_file_kind_invalid");
        let config = app.j_lens.as_ref().context("j_lens_unconfigured")?;
        let _lock = app.readout_lock.try_lock().map_err(|_| anyhow::anyhow!("j_lens_busy"))?;
        app.store.j_lens(&id, index, config, true)
    }).await?))
}
const PUBLIC: &[&str] = &[
    "journeys.js",
    "journeys.css",
    "research-settings.js",
    "research-settings.css",
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
    "internal-capture-viewer.js",
    "j-lens-viewer.js",
    "data-repository.js",
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
        .route("/api/research-storage", put(research_storage).post(publish_research))
        .route("/api/readings", put(reading).delete(remove_reading))
        .route("/api/trash", get(trash_list).put(trash_change))
        .route("/api/photos/{id}/image", get(photo_image))
        .route("/api/poems/{id}/internal-capture/{index}/{kind}", get(internal_capture_file).post(prepare_capture_readout))
        .fallback(static_file)
        .layer(DefaultBodyLimit::max(128 * 1024))
        .layer(middleware::from_fn_with_state(app.clone(), guard))
        .with_state(app)
}
async fn purge_for_mode(store: store::Store, child_mode: bool) {
    if child_mode { return; }
    if let Err(e) = blocking(move || store.purge(Utc::now())).await {
        tracing::warn!(event="bookshelf_cleanup_failed",error=%e.0);
    }
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
        capture_dir: config.capture_dir,
        screenshots: config.screenshots,
        memory: config.memory,
        photos: config.photos,
    };
    // 子の本棚を開くだけで別保存先への複製や期限整理を始めない。
    let research = (!config.child_mode).then(|| spawn_research_worker(store.clone()));
    purge_for_mode(store.clone(), config.child_mode).await;
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
        child_mode: config.child_mode,
        admin_token: config.admin_token,
        j_lens: config.j_lens,
        readout_lock: Arc::new(std::sync::Mutex::new(())),
        translation_lock: Arc::new(tokio::sync::Mutex::new(())),
        game_port: config.game_port,
        data_repository: config.data_repository,
        publish_lock: Arc::new(std::sync::Mutex::new(())),
    });
    let worker = app.store.clone();
    let child_mode = app.child_mode;
    let cleanup = tokio::spawn(async move {
        let mut timer = tokio::time::interval(std::time::Duration::from_secs(3600));
        timer.tick().await;
        loop {
            timer.tick().await;
            purge_for_mode(worker.clone(), child_mode).await;
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
    if let Some(research) = research { research.abort(); }
    result?;
    Ok(())
}

/// 本棚と本体で共有する保全worker。保存判断やゲーム処理から独立し、設定無しは何もしない。
pub fn spawn_research_worker(store: store::Store) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut timer = tokio::time::interval(std::time::Duration::from_secs(30));
        timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            timer.tick().await;
            let worker = store.clone();
            match tokio::task::spawn_blocking(move || worker.sync_research_storage()).await {
                Ok(Ok(_)) => {},
                Ok(Err(error)) => tracing::warn!(event="research_archive_pending",error=%error),
                Err(error) => tracing::warn!(event="research_archive_worker_failed",error=%error),
            }
        }
    })
}

/// 明示的な初回保全と保全再確認。通常起動と同じ設定／同じ保存経路を使う。
pub fn sync_research(config: Config, root: Option<PathBuf>) -> Result<Value> {
    ensure!(config.memory_enabled, "memory_disabled");
    let store = store::Store { memory:config.memory,capture_dir:config.capture_dir,photos:config.photos,screenshots:config.screenshots };
    if let Some(root) = root {
        let current = research_archive::load_config(&store.memory)?;
        let status = store.select_research_storage(research_storage::Selection {
            root, expected_archive_id:current.map(|c| c.archive_id),
        })?;
        ensure!(status["state"] == "ready", "research_archive_pending: {status}");
        Ok(status)
    } else { store.sync_research_storage() }
}
