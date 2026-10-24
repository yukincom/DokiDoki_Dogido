use super::*;
use std::io::Read;
use sha2::{Digest, Sha256};

fn captured(f: &Fixture, status: &str) -> (Value, Vec<u8>) {
    let dir = f.poem("root", "p");
    let source = f.store().capture_dir.unwrap().join("reply_1");
    fs::create_dir_all(&source).unwrap();
    let bytes: Vec<u8> = (0..12).flat_map(|n| (n as f32).to_le_bytes()).collect();
    let digest = format!("{:x}", Sha256::digest(&bytes));
    let numbers = matches!(status, "complete" | "partial");
    let manifest = json!({"schema":"dogido.internal_capture.v1","response_id":"reply_1",
        "choice_index":0,"backend":"llama.cpp","boundary":"decoder_layer_output_pre_final_norm",
        "status":status,"dtype":"float32-le","shape":[2,2,3],"hidden_file":"hidden.f32",
        "hidden_bytes":bytes.len(),"hidden_sha256":digest});
    let manifest_bytes = serde_json::to_vec(&manifest).unwrap();
    fs::write(source.join("manifest.json"), &manifest_bytes).unwrap();
    if numbers { fs::write(source.join("hidden.f32"), &bytes).unwrap(); }
    let artifact = json!({"manifest":"reply_1/manifest.json",
        "manifest_sha256":format!("{:x}", Sha256::digest(&manifest_bytes)),
        "backend":"llama.cpp","status":status,"boundary":"decoder_layer_output_pre_final_norm",
        "model":{"name":"test"},"identity":null,"shape":if numbers {json!([2,2,3])} else {Value::Null},
        "hidden_file":if numbers {json!("reply_1/hidden.f32")} else {Value::Null},
        "hidden_sha256":if numbers {json!(digest)} else {Value::Null},
        "hidden_bytes":if numbers {json!(bytes.len())} else {Value::Null},
        "hidden_verified":numbers,"api_text_matches_rendered":false,"error":null});
    let request = json!({"request_id":"job:request:1","request_index":1,"kind":"haiku_draft",
        "route":"haiku","requested_model":"test","request_sha256":"abcd","status":status,
        "response_id":"reply_1","response_model":"test","api_output":null,"artifact":artifact,"error":null});
    let capture = json!({"schema_version":1,"session_id":"test","job_id":"job",
        "token_scope":"raw_generation_before_domain_parse_and_reading_normalization","requests":[request]});
    let path = dir.join("long_term/haiku_entries.jsonl");
    let mut original: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    original["internal_capture"] = capture.clone();
    fs::write(&path, format!("{original}\n")).unwrap();
    (capture, bytes)
}

fn archive(f: &Fixture) -> PathBuf {
    let root = f.root.join("archive");
    fs::create_dir(&root).unwrap();
    f.store().select_research_storage(research_storage::Selection {root:root.clone(),expected_archive_id:None}).unwrap();
    root
}

fn read(s: &store::Store, kind: &str) -> anyhow::Result<Vec<u8>> {
    let mut file = s.internal_capture_file("root:p", 1, kind)?;
    let mut bytes = Vec::new(); file.file.read_to_end(&mut bytes)?;
    assert_eq!(file.bytes, bytes.len() as u64);
    Ok(bytes)
}

fn bundle(root: &std::path::Path) -> PathBuf {
    fs::read_dir(root.join("2026/10130900/internal-representations")).unwrap().next().unwrap().unwrap().path()
}

#[test]
fn capture_is_projected_and_archived_once_then_read_without_local_sources() {
    let f = Fixture::new(); let (capture, bytes) = captured(&f, "partial"); let s = f.store();
    let original_path = s.memory.join("long_term/haiku_entries.jsonl");
    let original = fs::read(&original_path).unwrap();
    assert_eq!(s.library_view().unwrap()["poems"][0]["internalCapture"], capture);
    assert_eq!(read(&s, "hidden").unwrap(), bytes);
    let root = archive(&f); let bundle = bundle(&root);
    // 既存写真台帳の初回保護処理も済ませてから不変性を比較する。
    s.sync_research_storage().unwrap();
    let before = s.read().unwrap();
    let modified = fs::metadata(bundle.join("hidden.f32")).unwrap().modified().unwrap();
    assert_eq!(s.library_view().unwrap()["poems"][0]["internalCaptureStorage"]["requests"][0]["state"], "saved");
    assert_eq!(read(&s, "hidden").unwrap(), bytes);
    assert_eq!(fs::read(&original_path).unwrap(), original);
    fs::remove_dir_all(s.capture_dir.as_ref().unwrap()).unwrap();
    s.sync_research_storage().unwrap();
    assert_eq!(s.read().unwrap(), before);
    assert_eq!(fs::metadata(bundle.join("hidden.f32")).unwrap().modified().unwrap(), modified);
    fs::remove_file(original_path).unwrap();
    let view = s.library_view().unwrap();
    assert_eq!(view["poems"][0]["internalCapture"], capture);
    assert_eq!(view["poems"][0]["readOnly"], true);
    assert_eq!(read(&s, "hidden").unwrap(), bytes);
    assert_eq!(s.read().unwrap(), before);
}

#[test]
fn capture_missing_source_keeps_poem_snapshot_and_can_retry() {
    let f = Fixture::new(); let (_, bytes) = captured(&f, "complete"); let s = f.store();
    let file = s.capture_dir.as_ref().unwrap().join("reply_1/hidden.f32");
    fs::remove_file(&file).unwrap();
    let root = archive(&f);
    assert_eq!(s.read().unwrap()["researchStorage"]["state"], "error");
    let view = s.library_view().unwrap();
    assert_eq!(view["poems"][0]["internalCapture"]["requests"][0]["status"], "complete");
    assert_eq!(view["poems"][0]["internalCaptureStorage"]["requests"][0]["state"], "error");
    assert!(!bundle(&root).join("commit.json").exists());
    fs::write(file, bytes).unwrap();
    assert_eq!(s.sync_research_storage().unwrap()["state"], "ready");
}

#[test]
fn capture_failed_manifest_is_preserved_without_publishing_numbers() {
    let f = Fixture::new(); captured(&f, "failed"); let s = f.store();
    let root = archive(&f);
    assert_eq!(s.read().unwrap()["researchStorage"]["state"], "ready");
    assert!(!bundle(&root).join("hidden.f32").exists());
    assert_eq!(serde_json::from_slice::<Value>(&read(&s, "manifest").unwrap()).unwrap()["status"], "failed");
    assert!(read(&s, "hidden").is_err());
}

#[test]
fn capture_corruption_and_lost_commit_are_not_repaired_or_hidden_by_local_copy() {
    let f = Fixture::new(); let (_, bytes) = captured(&f, "complete"); let s = f.store();
    let root = archive(&f); let bundle = bundle(&root);
    fs::write(bundle.join("hidden.f32"), vec![0u8; bytes.len()]).unwrap();
    assert!(read(&s, "hidden").is_err());
    assert_eq!(fs::read(s.capture_dir.as_ref().unwrap().join("reply_1/hidden.f32")).unwrap(), bytes);
    fs::remove_file(bundle.join("hidden.f32")).unwrap();
    assert!(read(&s, "manifest").is_ok());
    fs::remove_file(bundle.join("commit.json")).unwrap();
    assert!(s.sync_research_storage().is_err());
    assert!(read(&s, "hidden").is_err());
    assert!(!bundle.join("commit.json").exists());
    let view = s.library_view().unwrap();
    assert_eq!(view["poems"][0]["internalCaptureStorage"]["requests"][0]["state"], "error");
    assert!(view["poems"][0]["internalCaptureStorage"]["requests"][0]["receipt"].is_object());
}

#[cfg(unix)]
#[test]
fn capture_local_symlinks_tampering_and_arbitrary_paths_are_rejected() {
    let f = Fixture::new(); let (capture, bytes) = captured(&f, "complete"); let s = f.store();
    let path = s.capture_dir.as_ref().unwrap().join("reply_1/hidden.f32");
    fs::write(&path, vec![0u8; bytes.len()]).unwrap();
    assert!(read(&s, "hidden").is_err());
    fs::remove_file(&path).unwrap();
    let other = f.root.join("other"); fs::write(&other, bytes).unwrap();
    std::os::unix::fs::symlink(&other, &path).unwrap();
    assert!(read(&s, "hidden").is_err());
    assert!(read(&s, "../../other").is_err());
    let mut request = capture["requests"][0].clone();
    request["response_id"] = "../other".into();
    assert!(research_archive::local_capture_file(s.capture_dir.as_deref().unwrap(), &request, "manifest").is_err());
    assert!(s.internal_capture_file("root:p", 2, "manifest").is_err());
    assert!(s.internal_capture_file("root:missing", 1, "manifest").is_err());
}

#[tokio::test]
async fn capture_download_uses_existing_origin_guard_and_streams_exact_bytes() {
    use tower::ServiceExt;
    let f = Fixture::new(); let (_, bytes) = captured(&f, "complete"); let app = test_app(&f);
    let route = "/api/poems/root%3Ap/internal-capture/1/hidden";
    let response = router(app.clone()).oneshot(axum::http::Request::builder().uri(route)
        .header("host", "127.0.0.1:45678").body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()[header::CONTENT_LENGTH], bytes.len().to_string());
    assert_eq!(response.headers()[header::CONTENT_DISPOSITION], "attachment; filename=\"hidden.f32\"");
    assert_eq!(axum::body::to_bytes(response.into_body(), 1024).await.unwrap().as_ref(), bytes);
    for (host, origin) in [("evil.invalid", "http://127.0.0.1:45678"), ("127.0.0.1:45678", "https://evil.invalid")] {
        let response = router(app.clone()).oneshot(axum::http::Request::builder().uri(route)
            .header("host", host).header("origin", origin).body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
    }
}
