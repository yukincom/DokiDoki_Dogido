use super::*;
use crate::haiku_record::MemoryStore;
use serde_json::json;
use std::fs;

#[path = "player_photos_tests.rs"]
mod player_photos_tests;
#[path = "internal_capture_tests.rs"]
mod internal_capture_tests;

fn poem_edit_request(id: &str, parent: Option<&str>, expected: &[&str], lines: [&str; 3]) -> Value {
    json!({"id":id,"requestId":uuid::Uuid::new_v4().to_string(),
        "expectedRevisionId":parent,"expectedLines":expected,"lines":lines})
}
fn save_poem(store: &store::Store, value: Value) -> anyhow::Result<Value> {
    poem_edit::save(store, &serde_json::from_value(value)?)
}

#[test]
fn bookshelf_poem_edit_keeps_original_history_metadata_and_source_isolation() {
    let f = Fixture::new();
    let dir = f.poem("root", "p");
    let other = f.poem("other", "p");
    let store = f.store();
    store
        .edit(&json!({"revision":0,"section":"poems","key":"root:p",
        "value":{"favorite":true,"note":"そのまま"}}))
        .unwrap();
    let original = fs::read(dir.join("long_term/haiku_entries.jsonl")).unwrap();
    let history = fs::read(dir.join("long_term/haiku_revisions.jsonl")).unwrap();
    let other_history = fs::read(other.join("long_term/haiku_revisions.jsonl")).unwrap();
    let metadata = fs::read(store.memory.join("bookshelf/state.json")).unwrap();
    let request = poem_edit_request(
        "root:p",
        Some("r1"),
        &["ひるのやま", "とりのこえする", "もりのみち"],
        ["　未知の漢字🙂  ", "a / b", "字数には合わせない"],
    );
    let result = save_poem(&store, request.clone()).unwrap();
    assert_eq!(result["poem"]["lines"], request["lines"]);
    assert_eq!(result["poem"]["originalLines"][0], "あさのやま");
    assert_eq!(result["poem"]["history"].as_array().unwrap().len(), 2);
    assert_eq!(result["poem"]["current"]["source"], "bookshelf_explicit");
    assert!(
        result["poem"]["current"]["lines"]
            .as_array()
            .unwrap()
            .iter()
            .all(|line| line["reading_text"] == "" && line["source_atom_ids"] == json!([]))
    );
    assert_eq!(
        fs::read(dir.join("long_term/haiku_entries.jsonl")).unwrap(),
        original
    );
    assert!(
        fs::read(dir.join("long_term/haiku_revisions.jsonl"))
            .unwrap()
            .starts_with(&history)
    );
    assert_eq!(
        fs::read(other.join("long_term/haiku_revisions.jsonl")).unwrap(),
        other_history
    );
    assert_eq!(
        fs::read(store.memory.join("bookshelf/state.json")).unwrap(),
        metadata
    );
    assert_eq!(save_poem(&store, request).unwrap(), result);
}

fn bookshelf_emission() -> crate::haiku_record::Emission {
    use crate::haiku_record::{HaikuLine, LinePosition, PreparedEmission};
    let lines: Vec<_> = ["さくらいろ", "くろいおのもつ", "あさひかる"]
        .iter()
        .enumerate()
        .map(|(i, s)| {
            let (id, position, name) = LinePosition::ALL[i].metadata();
            HaikuLine {
                line_id: id.into(),
                line_index: i,
                position: position.into(),
                canonical_name: name.into(),
                surface_text: (*s).into(),
                reading_text: (*s).into(),
                source_atom_ids: vec![format!("atom:{i}")],
                source_atoms: vec![],
                provenance: "generated".into(),
            }
        })
        .collect();
    PreparedEmission {
        text: "元の句".into(),
        surface_text: None,
        reading_text: None,
        lines,
        materials: Default::default(),
        interpretation: None,
        preface: None,
        biome: None,
        structure: None,
        time_phase: None,
        dimension: None,
        event_sequence: Some(42),
        route: None,
    }
    .complete(now())
    .unwrap()
}

#[test]
fn bookshelf_poem_edit_preserves_untouched_line_records_and_reloads_unknown_reading() {
    let f = Fixture::new();
    let store = f.store();
    let original = bookshelf_emission();
    MemoryStore::new(&store.memory)
        .save_agent_haiku("test", "player", &original)
        .unwrap();
    let id = format!("root:{}", original.entry_id());
    let request = poem_edit_request(
        &id,
        None,
        &["さくらいろ", "くろいおのもつ", "あさひかる"],
        ["表記を変える", "くろいおのもつ", "あさひかる"],
    );
    let result = save_poem(&store, request).unwrap();
    assert_eq!(
        result["poem"]["current"]["lines"][1],
        json!(original.prepared.lines[1])
    );
    assert_eq!(
        result["poem"]["current"]["lines"][2],
        json!(original.prepared.lines[2])
    );
    let loaded = crate::poem_book::load(&store.memory).unwrap();
    let poem = loaded.values().next().unwrap();
    assert_eq!(poem.lines[0].surface_text, "表記を変える");
    assert!(poem.lines[0].reading_text.is_empty());
    assert_eq!(poem.lines[1..], original.prepared.lines[1..]);
    // ゲーム側が古いpinを保存しようとしても、本棚で保存した版を押し出さない。
    let mut stale_lines = original.prepared.lines.clone();
    stale_lines[0].surface_text = "ゲームから古い編集".into();
    stale_lines[0].reading_text.clear();
    stale_lines[0].source_atom_ids.clear();
    stale_lines[0].source_atoms.clear();
    stale_lines[0].provenance = "player_explicit".into();
    let pending = crate::workshop_edit::Pending::stage(
        &original.prepared.lines,
        &original.prepared.lines,
        stale_lines,
        0,
    )
    .unwrap();
    assert_eq!(
        MemoryStore::new(&store.memory)
            .save_player_revision(&original, &pending, None)
            .unwrap_err()
            .to_string(),
        "saved_poem_changed"
    );
}

#[test]
fn bookshelf_poem_edit_rejects_stale_retry_conflicts_and_deleted_poems_without_appending() {
    let f = Fixture::new();
    let dir = f.poem("root", "p");
    let store = f.store();
    let request = poem_edit_request(
        "root:p",
        Some("r1"),
        &["ひるのやま", "とりのこえする", "もりのみち"],
        ["変更", "中の句", "下の句"],
    );
    let saved = save_poem(&store, request.clone()).unwrap();
    let before = fs::read(dir.join("long_term/haiku_revisions.jsonl")).unwrap();
    let mut stale = request.clone();
    stale["requestId"] = uuid::Uuid::new_v4().to_string().into();
    assert_eq!(
        save_poem(&store, stale).unwrap_err().to_string(),
        "saved_poem_changed"
    );
    let mut changed_retry = request.clone();
    changed_retry["lines"][0] = "再送ではない別内容".into();
    assert_eq!(
        save_poem(&store, changed_retry).unwrap_err().to_string(),
        "revision_id_conflict"
    );
    crate::poem_trash::change(&store.memory, "root:p", false, now()).unwrap();
    assert_eq!(
        save_poem(&store, request.clone()).unwrap_err().to_string(),
        "poem_deleted"
    );
    crate::poem_trash::change(&store.memory, "root:p", true, now()).unwrap();
    assert_eq!(save_poem(&store, request).unwrap(), saved);
    assert_eq!(
        fs::read(dir.join("long_term/haiku_revisions.jsonl")).unwrap(),
        before
    );
}

#[test]
fn bookshelf_poem_edit_only_one_concurrent_writer_can_advance_the_same_head() {
    let f = Fixture::new();
    let dir = f.poem("root", "p");
    let barrier = Arc::new(std::sync::Barrier::new(2));
    let jobs: Vec<_> = ["一方の変更", "もう一方の変更"]
        .into_iter()
        .map(|text| {
            let store = f.store();
            let barrier = barrier.clone();
            let request = poem_edit_request(
                "root:p",
                Some("r1"),
                &["ひるのやま", "とりのこえする", "もりのみち"],
                [text, "中の句", "下の句"],
            );
            std::thread::spawn(move || {
                barrier.wait();
                save_poem(&store, request)
            })
        })
        .collect();
    let results: Vec<_> = jobs.into_iter().map(|job| job.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(
        results
            .iter()
            .filter_map(|result| result.as_ref().err())
            .next()
            .unwrap()
            .to_string(),
        "saved_poem_changed"
    );
    assert_eq!(
        crate::poem_trash::raw_rows(&dir, "haiku_revisions.jsonl")
            .unwrap()
            .len(),
        2
    );
}

#[test]
fn bookshelf_poem_edit_response_rejects_a_newer_revision_after_its_own_save() {
    let f = Fixture::new();
    let dir = f.poem("root", "p");
    let store = f.store();
    let first = save_poem(
        &store,
        poem_edit_request(
            "root:p",
            Some("r1"),
            &["ひるのやま", "とりのこえする", "もりのみち"],
            ["最初の編集", "中", "下"],
        ),
    )
    .unwrap();
    let first_id = first["poem"]["current"]["id"].as_str().unwrap();
    assert_eq!(
        poem_edit::saved_projection(&store, &dir, "root:p", first_id).unwrap(),
        first
    );
    // 最初の保存が終わった直後、応答用の読取より先に別の編集が保存された状態。
    let second = save_poem(
        &store,
        poem_edit_request(
            "root:p",
            Some(first_id),
            &["最初の編集", "中", "下"],
            ["別の編集", "中", "下"],
        ),
    )
    .unwrap();
    assert_eq!(
        poem_edit::saved_projection(&store, &dir, "root:p", first_id)
            .unwrap_err()
            .to_string(),
        "saved_poem_changed"
    );
    assert_eq!(
        poem_edit::saved_projection(
            &store,
            &dir,
            "root:p",
            second["poem"]["current"]["id"].as_str().unwrap()
        )
        .unwrap(),
        second
    );
    assert_eq!(
        crate::poem_trash::raw_rows(&dir, "haiku_revisions.jsonl")
            .unwrap()
            .len(),
        3
    );
}

#[test]
fn bookshelf_poem_edit_supports_one_line_legacy_as_three_lines_and_checks_shape_only() {
    let f = Fixture::new();
    let dir = f.poem("root", "p");
    fs::write(dir.join("long_term/haiku_revisions.jsonl"), "").unwrap();
    let entry = json!({"id":"p","kind":"player_haiku","author":"player","text":"未分割の旧句","created_at":"2026-10-13T00:00:00Z"});
    fs::write(
        dir.join("long_term/haiku_entries.jsonl"),
        format!("{entry}\n"),
    )
    .unwrap();
    let request = poem_edit_request(
        "root:p",
        None,
        &["未分割の旧句"],
        ["上", "字数自由の中", "　下　"],
    );
    let result = save_poem(&f.store(), request).unwrap();
    assert_eq!(
        result["poem"]["lines"],
        json!(["上", "字数自由の中", "　下　"])
    );
    assert_eq!(result["poem"]["original"], entry);
    for invalid in ["", " ", "改\n行", "改\r行"] {
        let request = poem_edit_request("root:p", None, &["未分割の旧句"], [invalid, "中", "下"]);
        assert_eq!(
            save_poem(&f.store(), request).unwrap_err().to_string(),
            "invalid_poem_lines"
        );
    }
}

#[tokio::test]
async fn http_poem_edit_uses_existing_guard_and_reports_head_conflict() {
    let f = Fixture::new();
    f.poem("root", "p");
    let app = test_app(&f);
    let request = poem_edit_request(
        "root:p",
        Some("r1"),
        &["ひるのやま", "とりのこえする", "もりのみち"],
        ["変更", "中", "下"],
    );
    for (host, token, origin) in [
        ("evil.example", "secret-test-token", None),
        ("127.0.0.1:45678", "", None),
        (
            "127.0.0.1:45678",
            "secret-test-token",
            Some("https://evil.example"),
        ),
    ] {
        assert_eq!(
            call(
                app.clone(),
                "/api/poems/current",
                Some(request.clone()),
                host,
                token,
                origin
            )
            .await
            .0,
            StatusCode::FORBIDDEN
        );
    }
    let (status, body) = call(
        app.clone(),
        "/api/poems/current",
        Some(request.clone()),
        "127.0.0.1:45678",
        "secret-test-token",
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["poem"]["lines"], request["lines"]);
    let mut stale = request;
    stale["requestId"] = uuid::Uuid::new_v4().to_string().into();
    assert_eq!(
        call(
            app.clone(),
            "/api/poems/current",
            Some(stale.clone()),
            "127.0.0.1:45678",
            "secret-test-token",
            None
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    let mut disabled = (*app).clone();
    disabled.memory_enabled = false;
    assert_eq!(
        call(
            Arc::new(disabled),
            "/api/poems/current",
            Some(stale),
            "127.0.0.1:45678",
            "secret-test-token",
            None
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
}

struct Fixture {
    root: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let root =
            std::env::temp_dir().join(format!("dogido-bookshelf-tests-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let root = fs::canonicalize(root).unwrap();
        Self { root }
    }
    fn store(&self) -> store::Store {
        store::Store {
            memory: self.root.join("memory"),
            capture_dir: Some(self.root.join("internal-capture")),
            photos: self.root.join("photos"),
            screenshots: self.root.join("screenshots"),
        }
    }
    fn poem(&self, source: &str, id: &str) -> PathBuf {
        let mut dir = self.store().memory;
        if source != "root" {
            dir = dir.join("sessions").join(source);
        }
        fs::create_dir_all(dir.join("long_term")).unwrap();
        fs::write(dir.join("long_term/haiku_entries.jsonl"),format!("{}\n",json!({"id":id,"kind":"player_haiku","author":"player","text":"あさのやま / とりのこえする / もりのみち","created_at":"2026-10-13T00:00:00Z","world":{"biome":"forest","time_phase":"day"}}))).unwrap();
        fs::write(dir.join("long_term/haiku_revisions.jsonl"),format!("{}\n",json!({"id":"r1","haiku_id":id,"revised_text":"ひるのやま / とりのこえする / もりのみち","created_at":"2026-10-13T01:00:00Z"}))).unwrap();
        dir
    }
    fn photo(&self) -> String {
        let dir = self.store().photos;
        fs::create_dir_all(&dir).unwrap();
        // 読み取りテストの最小PNGヘッダー。実ブラウザー試験には完全なPNGを使う。
        let mut png = b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR".to_vec();
        png.extend(2u32.to_be_bytes());
        png.extend(2u32.to_be_bytes());
        fs::write(dir.join("20261013_01.png"), png).unwrap();
        "20261013_01".into()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}
fn now() -> chrono::DateTime<Utc> {
    "2026-10-13T00:00:00Z".parse().unwrap()
}

fn capture(f: &Fixture) -> (String, crate::screenshot::Receipt) {
    use sha2::{Digest, Sha256};
    f.poem("s", "hk_new");
    let s = f.store();
    fs::create_dir_all(&s.screenshots).unwrap();
    // CRC・終端まで含む1x1 RGB PNG。研究原本の完全性検査にも同じfixtureを使う。
    fn chunk(bytes: &mut Vec<u8>, kind: &[u8], body: &[u8]) {
        bytes.extend((body.len() as u32).to_be_bytes());
        let start = bytes.len();
        bytes.extend(kind); bytes.extend(body);
        let mut crc = !0u32;
        for byte in &bytes[start..] {
            crc ^= u32::from(*byte);
            for _ in 0..8 { crc = (crc >> 1) ^ (0xedb88320 & 0u32.wrapping_sub(crc & 1)); }
        }
        bytes.extend((!crc).to_be_bytes());
    }
    let mut bytes = b"\x89PNG\r\n\x1a\n".to_vec();
    chunk(&mut bytes, b"IHDR", &[0,0,0,1,0,0,0,1,8,2,0,0,0]);
    chunk(&mut bytes, b"IDAT", &[0x78,1,1,4,0,0xfb,0xff,0,0,0,0,0,4,0,1]);
    chunk(&mut bytes, b"IEND", &[]);
    let filename = "dogido_2026-10-20_12.00.00.png".to_owned();
    fs::write(s.screenshots.join(&filename), &bytes).unwrap();
    (
        format!("shot_{}", uuid::Uuid::new_v4()),
        crate::screenshot::Receipt {
            session_id: "s".into(),
            poem_id: "session:s:hk_new".into(),
            filename,
            directory: s.screenshots,
            sha256: format!("{:x}", Sha256::digest(&bytes)),
            captured_at: now(),
            capture_view: Some(crate::screenshot::CaptureView {
                viewpoint: "dogido_client_render_eye".into(),
                body_entity_uuid: "22222222-2222-4222-8222-222222222222".into(),
                body_epoch: "33333333-3333-4333-8333-333333333333".into(),
                owner_uuid: "11111111-1111-4111-8111-111111111111".into(),
                dimension: "minecraft:overworld".into(),
                camera_restored: true,
            }),
        },
    )
}
#[test]
fn minecraft_capture_registers_once_and_protects_original_without_changing_notes() {
    let f = Fixture::new();
    let (id, r) = capture(&f);
    let s = f.store();
    let before = s.player().unwrap();
    s.register_capture(&id, &r).unwrap();
    let p = s.photo_snapshot_now().unwrap();
    let photo = format!("minecraft-{id}");
    assert_eq!(
        p["placements"]["poems:session:s:hk_new"],
        json!([photo, null, null])
    );
    assert_eq!(p["items"][0]["source"], "minecraft");
    assert_eq!(p["items"][0]["filename"], "dogido_2026-10-20_12.00.00.png");
    assert_eq!(
        s.read().unwrap()["minecraftPhotos"][&photo]["receipt"]["capture_view"],
        json!(r.capture_view)
    );
    assert_eq!(s.player().unwrap(), before);
    assert!(!s.photos.exists());
    assert_eq!(
        s.image_bytes(&photo).unwrap(),
        fs::read(r.directory.join(&r.filename)).unwrap()
    );
    assert!(s.photo_edit(&json!({"revision":p["revision"],"titles":{},"placements":{"poems:session:s:hk_new":[null,null,null]}})).is_err());
    assert!(s.photo_edit(&json!({"revision":p["revision"],"titles":{photo.clone():"changed"},"placements":{}})).is_err());
    s.register_capture(&id, &r).unwrap();
    assert_eq!(
        s.photo_snapshot_now().unwrap()["placements"]["poems:session:s:hk_new"],
        json!([photo, null, null])
    );
}
#[test]
fn historical_minecraft_photo_without_capture_view_stays_readable_and_idempotent() {
    let f = Fixture::new();
    let (id, mut r) = capture(&f);
    let old_name = "2026-10-20_12.00.00.png";
    fs::rename(r.directory.join(&r.filename), r.directory.join(old_name)).unwrap();
    r.filename = old_name.into();
    r.capture_view = None;
    // 以前の保存形にはcapture_view自体がない。再読込と再登録でその形を変えない。
    let historical = json!(r);
    assert!(historical.get("capture_view").is_none());
    let r: crate::screenshot::Receipt = serde_json::from_value(historical.clone()).unwrap();
    let s = f.store();
    s.register_capture(&id, &r).unwrap();
    let photo = format!("minecraft-{id}");
    let before = s.read().unwrap();
    assert_eq!(before["minecraftPhotos"][&photo]["receipt"], historical);
    let reopened = f.store();
    assert_eq!(
        reopened.photo_snapshot_now().unwrap()["items"][0]["filename"],
        old_name
    );
    assert_eq!(
        reopened.image_bytes(&photo).unwrap(),
        fs::read(r.directory.join(old_name)).unwrap()
    );
    reopened.register_capture(&id, &r).unwrap();
    assert_eq!(reopened.read().unwrap(), before);
}
#[test]
fn minecraft_original_rejects_trash_restore_and_survives_expiry() {
    let f = Fixture::new();
    let (id, r) = capture(&f);
    let s = f.store();
    s.register_capture(&id, &r).unwrap();
    let photo = format!("minecraft-{id}");
    assert!(s.photo_change(&photo, false, now()).is_err());
    assert!(s.image_bytes(&photo).is_ok());
    s.register_capture(&id, &r).unwrap();
    assert_eq!(s.photo_snapshot_now().unwrap()["items"].as_array().unwrap().len(), 1);
    assert!(s.photo_change(&photo, true, now()).is_err());
    assert_eq!(
        s.photo_snapshot_now().unwrap()["placements"]["poems:session:s:hk_new"][0],
        photo
    );
    assert!(s.photo_change(&photo, false, now()).is_err());
    s.purge(now() + chrono::Duration::days(30)).unwrap();
    assert!(r.directory.join(&r.filename).is_file());
    s.register_capture(&id, &r).unwrap();
    assert_eq!(s.photo_snapshot_now().unwrap()["items"].as_array().unwrap().len(), 1);
    assert!(
        s.photo_change(&photo, true, now() + chrono::Duration::days(30))
            .is_err()
    );
}
#[test]
fn minecraft_capture_rejects_wrong_directory_changed_file_and_deleted_poem() {
    let f = Fixture::new();
    let (id, mut r) = capture(&f);
    let s = f.store();
    r.directory = f.root.clone();
    assert!(s.register_capture(&id, &r).is_err());
    r.directory = s.screenshots.clone();
    r.filename = "../bad.png".into();
    assert!(s.register_capture(&id, &r).is_err());
    r.filename = "dogido_2026-10-20_12.00.00.png".into();
    r.sha256 = "0".repeat(64);
    assert!(s.register_capture(&id, &r).is_err());
    crate::poem_trash::change(&s.memory, &r.poem_id, false, now()).unwrap();
    assert!(s.register_capture(&id, &r).is_err());
    assert_eq!(s.photo_snapshot_now().unwrap()["revision"], 0);
}
#[test]
fn minecraft_changed_original_is_not_served_or_restored() {
    let f = Fixture::new();
    let (id, r) = capture(&f);
    let s = f.store();
    s.register_capture(&id, &r).unwrap();
    let photo = format!("minecraft-{id}");
    fs::write(r.directory.join(&r.filename), b"changed").unwrap();
    assert!(s.image_bytes(&photo).is_err());
    assert!(s.photo_change(&photo, false, now()).is_err());
    assert!(s.photo_change(&photo, true, now()).is_err());
    s.purge(now() + chrono::Duration::days(31)).unwrap();
    assert_eq!(fs::read(r.directory.join(&r.filename)).unwrap(), b"changed");
}
#[test]
fn minecraft_full_or_trashed_slots_are_not_overwritten() {
    let f = Fixture::new();
    let (id, r) = capture(&f);
    let s = f.store();
    let mut d = s.read().unwrap();
    d["placements"]["poems:session:s:hk_new"] = json!(["occupied", "trashed", "third"]);
    s.save_photos(&mut d).unwrap();
    s.register_capture(&id, &r).unwrap();
    assert_eq!(
        s.read().unwrap()["placements"]["poems:session:s:hk_new"],
        json!(["occupied", "trashed", "third"])
    );
    assert_eq!(
        s.photo_snapshot_now().unwrap()["items"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}
#[tokio::test]
async fn library_refresh_uses_existing_read_guard_and_reports_new_capture() {
    let f = Fixture::new();
    let (id, r) = capture(&f);
    f.store().register_capture(&id, &r).unwrap();
    let app = test_app(&f);
    let (status, body) = call(
        app.clone(),
        "/api/library",
        None,
        "127.0.0.1:45678",
        "secret-test-token",
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["photos"]["items"][0]["id"], format!("minecraft-{id}"));
    assert_eq!(body["poems"][0]["id"], r.poem_id);
    let (status, _) = call(
        app,
        "/api/library",
        None,
        "127.0.0.1:45678",
        "",
        Some("http://other.invalid"),
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
}

#[test]
fn research_storage_copies_existing_records_once_and_keeps_changed_snapshots() {
    let f = Fixture::new();
    let (command, receipt) = capture(&f);
    let s = f.store();
    s.register_capture(&command, &receipt).unwrap();
    let original = fs::read(receipt.directory.join(&receipt.filename)).unwrap();
    let canonical = s.memory.join("sessions/s/long_term/haiku_entries.jsonl");
    let canonical_bytes = fs::read(&canonical).unwrap();
    let root = f.root.join("archive"); fs::create_dir(&root).unwrap();
    let status = s.select_research_storage(research_storage::Selection { root:root.clone(),expected_archive_id:None }).unwrap();
    assert_eq!(status["state"], "ready"); assert_eq!(status["poems"],1); assert_eq!(status["captures"],1);
    let folder = root.join("2026/10130900");
    assert!(folder.join("identity.json").is_file());
    assert!(folder.join("internal-representations").is_dir());
    let snapshot = fs::read_dir(folder.join("snapshots")).unwrap().next().unwrap().unwrap().path();
    let before_snapshot = fs::read(&snapshot).unwrap();
    let before_state = s.read().unwrap();
    s.sync_research_storage().unwrap();
    assert_eq!(s.read().unwrap(), before_state);
    s.edit(&json!({"revision":0,"section":"poems","key":receipt.poem_id,
        "value":{"favorite":true,"note":"研究メモ"}})).unwrap();
    s.sync_research_storage().unwrap();
    assert_eq!(fs::read_dir(folder.join("snapshots")).unwrap().count(),2);
    assert_eq!(fs::read(snapshot).unwrap(),before_snapshot);
    assert_eq!(fs::read(canonical).unwrap(),canonical_bytes);
    assert_eq!(fs::read(receipt.directory.join(&receipt.filename)).unwrap(),original);
    fs::remove_file(receipt.directory.join(&receipt.filename)).unwrap();
    assert_eq!(s.image_bytes(&format!("minecraft-{command}")).unwrap(),original);
    s.sync_research_storage().unwrap();
}

#[test]
fn research_storage_offline_blocks_expiry_and_reconnects_without_new_directory() {
    let f = Fixture::new(); let (command, receipt) = capture(&f); let s=f.store();
    s.register_capture(&command,&receipt).unwrap();
    let root=f.root.join("archive"); fs::create_dir(&root).unwrap();
    let status=s.select_research_storage(research_storage::Selection {root:root.clone(),expected_archive_id:None}).unwrap();
    assert_eq!(status["state"],"ready");
    crate::poem_trash::change(&s.memory,&receipt.poem_id,false,now()).unwrap();
    let detached=f.root.join("detached"); fs::rename(&root,&detached).unwrap();
    assert!(s.purge(now()+chrono::Duration::days(31)).is_err());
    assert!(!root.exists());
    assert_eq!(s.photo_snapshot_now().unwrap()["researchStorage"]["state"],"offline");
    assert_eq!(store::poems(&s.memory,true).unwrap().len(),1);
    fs::rename(&detached,&root).unwrap();
    assert_eq!(s.sync_research_storage().unwrap()["state"],"ready");
    s.purge(now()+chrono::Duration::days(31)).unwrap();
    assert!(store::poems(&s.memory,true).unwrap().is_empty());
    assert!(s.image_bytes(&format!("minecraft-{command}")).is_ok());
    assert!(root.join("2026/10130900/identity.json").is_file());
}

#[test]
fn research_storage_selection_uses_cas_and_keeps_previous_archive() {
    let f=Fixture::new(); let (command, receipt)=capture(&f); let s=f.store();
    s.register_capture(&command,&receipt).unwrap();
    let first=f.root.join("first"); let second=f.root.join("second");
    fs::create_dir(&first).unwrap(); fs::create_dir(&second).unwrap();
    let status=s.select_research_storage(research_storage::Selection {root:first.clone(),expected_archive_id:None}).unwrap();
    assert!(s.select_research_storage(research_storage::Selection {root:second.clone(),expected_archive_id:None}).is_err());
    assert_eq!(fs::read_dir(&second).unwrap().count(),0);
    let status2=s.select_research_storage(research_storage::Selection {root:second.clone(),expected_archive_id:Some(status["archiveId"].as_str().unwrap().into())}).unwrap();
    assert_eq!(status2["state"],"ready"); assert_ne!(status2["archiveId"],status["archiveId"]);
    assert!(first.join("2026/10130900/identity.json").is_file());
    let back=s.select_research_storage(research_storage::Selection {root:first,expected_archive_id:Some(status2["archiveId"].as_str().unwrap().into())}).unwrap();
    assert_eq!(back["archiveId"],status["archiveId"]);
}

#[test]
fn research_storage_new_capture_is_copied_before_registration_returns() {
    let f=Fixture::new(); let s=f.store();
    fs::create_dir_all(&s.memory).unwrap();
    let root=f.root.join("archive"); fs::create_dir(&root).unwrap();
    s.select_research_storage(research_storage::Selection {root:root.clone(),expected_archive_id:None}).unwrap();
    let (command,receipt)=capture(&f);
    s.register_capture(&command,&receipt).unwrap();
    let photo=format!("minecraft-{command}"); let state=s.read().unwrap();
    assert_eq!(state["minecraftPhotos"][&photo]["archiveStatus"]["state"],"saved");
    assert_eq!(state["photoRevision"],1);
    assert_eq!(state["researchStorage"]["state"],"pending");
    let image=root.join("2026/10130900/original-images").join(command.strip_prefix("shot_").unwrap()).join("image.png");
    assert_eq!(fs::read(image).unwrap(),fs::read(receipt.directory.join(receipt.filename)).unwrap());
    s.sync_research_storage().unwrap();
    assert_eq!(s.read().unwrap()["photoRevision"],1);
}

#[test]
fn library_view_reads_verified_ssd_poems_and_images_without_local_originals() {
    let f = Fixture::new(); let (command, receipt) = capture(&f); let s = f.store();
    s.register_capture(&command, &receipt).unwrap();
    let root = f.root.join("archive"); fs::create_dir(&root).unwrap();
    s.select_research_storage(research_storage::Selection {root: root.clone(), expected_archive_id: None}).unwrap();
    let original = s.library_view().unwrap();
    assert_eq!(original["photos"]["researchStorage"]["displaySource"], "archive");
    assert_eq!(original["poems"][0]["storageSource"], "archive");
    let png = fs::read(receipt.directory.join(&receipt.filename)).unwrap();
    // 試験fixtureだけのローカル句・画像を除き、SSD実読込であることを確認。
    fs::remove_file(s.memory.join("sessions/s/long_term/haiku_entries.jsonl")).unwrap();
    fs::remove_file(receipt.directory.join(&receipt.filename)).unwrap();
    let before = fs::read(s.memory.join("bookshelf/state.json")).unwrap();
    let view = s.library_view().unwrap();
    for key in ["original", "current", "history", "materials", "dialogue", "lines"] {
        assert_eq!(view["poems"][0][key], original["poems"][0][key]);
    }
    assert_eq!(view["poems"][0]["readOnly"], true);
    assert_eq!(s.image_bytes(&format!("minecraft-{command}")).unwrap(), png);
    assert_eq!(fs::read(s.memory.join("bookshelf/state.json")).unwrap(), before);
}

#[test]
fn library_view_keeps_pending_edits_and_deletions_until_ssd_sync() {
    let f = Fixture::new(); f.poem("root", "p"); let s = f.store();
    let root = f.root.join("archive"); fs::create_dir(&root).unwrap();
    s.select_research_storage(research_storage::Selection {root, expected_archive_id: None}).unwrap();
    save_poem(&s, poem_edit_request("root:p", Some("r1"),
        &["ひるのやま", "とりのこえする", "もりのみち"], ["編集した句", "元へ戻さない", "保存待ち"])).unwrap();
    let view = s.library_view().unwrap();
    assert_eq!(view["photos"]["researchStorage"]["displaySource"], "mixed");
    assert_eq!(view["poems"][0]["lines"][0], "編集した句");
    s.sync_research_storage().unwrap();
    assert_eq!(s.library_view().unwrap()["poems"][0]["storageSource"], "archive");
    crate::poem_trash::change(&s.memory, "root:p", false, now()).unwrap();
    assert!(s.library_view().unwrap()["poems"].as_array().unwrap().is_empty());
    s.sync_research_storage().unwrap();
    s.purge(now() + chrono::Duration::days(31)).unwrap();
    assert!(s.library_view().unwrap()["poems"].as_array().unwrap().is_empty());
}

#[test]
fn library_view_reports_corrupt_snapshot_and_never_masks_corrupt_ssd_image() {
    let f = Fixture::new(); let (command, receipt) = capture(&f); let s = f.store();
    s.register_capture(&command, &receipt).unwrap();
    let root = f.root.join("archive"); fs::create_dir(&root).unwrap();
    s.select_research_storage(research_storage::Selection {root:root.clone(), expected_archive_id: None}).unwrap();
    let data = s.read().unwrap();
    let path = data["researchSnapshots"][&receipt.poem_id]["relative_path"].as_str().unwrap();
    fs::write(root.join(path), b"{}").unwrap();
    let view = s.library_view().unwrap();
    assert_eq!(view["photos"]["researchStorage"]["displaySource"], "unavailable");
    assert!(view["photos"]["researchStorage"]["readError"].as_str().unwrap().contains("archive_snapshot_changed"));
    assert_eq!(view["poems"][0]["storageSource"], "local_fallback");
    fs::write(root.join("2026/10130900/original-images").join(command.strip_prefix("shot_").unwrap()).join("image.png"), b"broken").unwrap();
    assert!(receipt.directory.join(&receipt.filename).is_file());
    assert!(s.image_bytes(&format!("minecraft-{command}")).is_err());
    assert!(s.sync_research_storage().is_err());
    assert!(s.image_bytes(&format!("minecraft-{command}")).is_err());
    fs::rename(&root, f.root.join("detached")).unwrap();
    assert_eq!(s.library_view().unwrap()["photos"]["researchStorage"]["displaySource"], "unavailable");
    assert!(!root.exists());
}

#[test]
fn archive_reconnect_preserves_semantically_identical_pretty_config() {
    let f = Fixture::new(); f.poem("root", "p"); let s = f.store();
    let root = f.root.join("archive"); fs::create_dir(&root).unwrap();
    let status = s.select_research_storage(research_storage::Selection {root: root.clone(), expected_archive_id: None}).unwrap();
    let config = research_archive::load_config(&s.memory).unwrap().unwrap();
    let path = s.memory.join("bookshelf/research-archive.json");
    let pretty = serde_json::to_vec_pretty(&config).unwrap(); fs::write(&path, &pretty).unwrap();
    s.select_research_storage(research_storage::Selection {root, expected_archive_id: Some(status["archiveId"].as_str().unwrap().into())}).unwrap();
    assert_eq!(fs::read(path).unwrap(), pretty);
}

#[test]
fn never_archived_capture_remains_visible_locally_after_initial_copy_failure() {
    let f = Fixture::new(); let s = f.store(); f.poem("s", "hk_new");
    let root = f.root.join("archive"); fs::create_dir(&root).unwrap();
    s.select_research_storage(research_storage::Selection {root:root.clone(), expected_archive_id:None}).unwrap();
    // 原本画像の保存用directoryを作れないfixture。句snapshotは保存できる。
    fs::write(root.join("2026/10130900/original-images"), b"blocked").unwrap();
    let (command, receipt) = capture(&f); s.register_capture(&command, &receipt).unwrap();
    assert!(s.sync_research_storage().is_err());
    let photo = format!("minecraft-{command}");
    assert_eq!(s.read().unwrap()["minecraftPhotos"][&photo]["archiveStatus"]["state"], "error");
    assert_eq!(s.image_bytes(&photo).unwrap(), fs::read(receipt.directory.join(&receipt.filename)).unwrap());
}

#[test]
fn archive_snapshot_rejects_wrong_identity_and_unsafe_index_paths() {
    let f = Fixture::new(); f.poem("root", "p"); let s = f.store();
    let root = f.root.join("archive"); fs::create_dir(&root).unwrap();
    s.select_research_storage(research_storage::Selection {root, expected_archive_id: None}).unwrap();
    let archive = research_archive::load(&s.memory).unwrap().unwrap();
    let head: research_archive::ArchivedSnapshot = serde_json::from_value(s.read().unwrap()["researchSnapshots"]["root:p"].clone()).unwrap();
    assert!(archive.read_snapshot(&head).is_ok());
    let mut wrong = head.clone(); wrong.poem_id = "other:p".into();
    assert!(archive.read_snapshot(&wrong).is_err());
    let mut wrong = head.clone(); wrong.relative_path = format!("2026/../../snapshots/{}.json", head.sha256);
    assert!(archive.read_snapshot(&wrong).is_err());
    let mut wrong = head; wrong.sha256 = "../bad".into();
    assert!(archive.read_snapshot(&wrong).is_err());
}

#[tokio::test]
async fn research_storage_http_requires_same_host_token_and_enabled_memory() {
    let f=Fixture::new(); f.poem("root","p");
    let app=test_app(&f); let root=f.root.join("archive"); fs::create_dir(&root).unwrap();
    let value=json!({"root":root,"expectedArchiveId":null});
    for (host,token,origin) in [("evil.invalid","secret-test-token",None),("127.0.0.1:45678","",None),("127.0.0.1:45678","secret-test-token",Some("https://evil.invalid"))] {
        assert_eq!(call(app.clone(),"/api/research-storage",Some(value.clone()),host,token,origin).await.0,StatusCode::FORBIDDEN);
    }
    assert_eq!(fs::read_dir(&root).unwrap().count(),0);
    let mut disabled=(*app).clone(); disabled.memory_enabled=false;
    assert_eq!(call(Arc::new(disabled),"/api/research-storage",Some(value.clone()),"127.0.0.1:45678","secret-test-token",None).await.0,StatusCode::CONFLICT);
    let (status,body)=call(app,"/api/research-storage",Some(value),"127.0.0.1:45678","secret-test-token",None).await;
    assert_eq!(status,StatusCode::OK); assert_eq!(body["researchStorage"]["state"],"ready");
}
#[cfg(unix)]
#[test]
fn minecraft_symlink_cannot_register() {
    let f = Fixture::new();
    let (id, r) = capture(&f);
    let s = f.store();
    let original = s.screenshots.join(&r.filename);
    let other = f.root.join("original.png");
    fs::rename(&original, &other).unwrap();
    std::os::unix::fs::symlink(other, original).unwrap();
    assert!(s.register_capture(&id, &r).is_err());
}

#[test]
fn offline_reads_are_real_and_do_not_create_storage() {
    let f = Fixture::new();
    let s = f.store();
    assert!(store::poems(&s.memory, false).unwrap().is_empty());
    assert_eq!(s.player().unwrap()["revision"], 0);
    assert!(
        s.photo_snapshot_now().unwrap()["items"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert!(!s.memory.exists());
    assert!(!s.photos.exists());
    let catalog = store::catalog();
    assert!(catalog.len() > 100);
    assert!(catalog.iter().any(|e| e["id"] == "mob:creeper"));
    assert!(catalog.iter().all(|e| e["raw"]["label"].is_string()));
}
#[test]
fn poem_preface_shows_original_inspiration_instead_of_recitation_cue() {
    let f = Fixture::new();
    let dir = f.poem("root", "p1");
    let path = dir.join("long_term/haiku_entries.jsonl");
    let mut row: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    let spoken = "黒い石の冷たさと、ブタのぬくもりが対照的やな、なんか浮かんできたわ。";
    row["author"] = "dogido".into();
    row["kind"] = "agent_haiku".into();
    row["preface"] = "ここで一句。".into();
    row["interpretation"] = "発話とは別の解釈".into();
    row["materials_snapshot"] = json!({"preface_spoken":spoken});
    let original = format!("{row}\n");
    fs::write(&path, &original).unwrap();
    let s = f.store();

    let poems = store::poems(&s.memory, false).unwrap();
    assert_eq!(poems[0]["preface"], spoken);
    assert_eq!(poems[0]["revised"], true);
    assert_eq!(poems[0]["lines"][0], "ひるのやま");
    // 直し句とごみ箱でも、最初の発句に結び付いた同じ言葉を表示する。
    crate::poem_trash::change(&s.memory, "root:p1", false, now()).unwrap();
    assert_eq!(store::poems(&s.memory, true).unwrap()[0]["preface"], spoken);
    assert_eq!(fs::read_to_string(&path).unwrap(), original);
}

#[test]
fn missing_inspiration_is_not_filled_with_cue_or_unspoken_interpretation() {
    let f = Fixture::new();
    let dir = f.poem("root", "p1");
    let path = dir.join("long_term/haiku_entries.jsonl");
    let mut row: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    row["preface"] = "ここで一句。".into();
    row["interpretation"] = "発話されていない見どころ".into();
    for materials in [
        Value::Null,
        json!({}),
        json!({"preface_spoken":null}),
        json!({"preface_spoken":""}),
        json!({"preface_spoken":" \n "}),
        json!({"preface_spoken":{"text":"不正な形式"}}),
    ] {
        row["materials_snapshot"] = materials;
        let original = format!("{row}\n");
        fs::write(&path, &original).unwrap();
        assert!(store::poems(&f.store().memory, false).unwrap()[0]["preface"].is_null());
        assert_eq!(fs::read_to_string(&path).unwrap(), original);
    }
}

#[test]
fn poem_delete_restore_and_expiry_use_exact_source_and_hide_core_recall() {
    let f = Fixture::new();
    let root = f.poem("root", "same");
    let other = f.poem("another", "same");
    let s = f.store();
    crate::poem_trash::change(&s.memory, "root:same", false, now()).unwrap();
    assert!(
        MemoryStore::new(&root)
            .local_poem_rows("haiku_entries.jsonl")
            .unwrap()
            .is_empty()
    );
    assert!(
        MemoryStore::new(&root)
            .local_poem_rows("haiku_revisions.jsonl")
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        MemoryStore::new(&other)
            .local_poem_rows("haiku_entries.jsonl")
            .unwrap()
            .len(),
        1
    );
    let rows = MemoryStore::new(&s.memory)
        .poem_rows("haiku_entries.jsonl")
        .unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(store::poems(&s.memory, false).unwrap().len(), 1);
    let deleted = store::poems(&s.memory, true).unwrap();
    assert_eq!(deleted.len(), 1);
    assert_eq!(deleted[0]["history"].as_array().unwrap().len(), 1);
    // 二度削除しても期限を延長しない。
    crate::poem_trash::change(
        &s.memory,
        "root:same",
        false,
        now() + chrono::Duration::days(2),
    )
    .unwrap();
    assert_eq!(
        deleted[0]["expiresAt"],
        store::poems(&s.memory, true).unwrap()[0]["expiresAt"]
    );
    crate::poem_trash::change(
        &s.memory,
        "root:same",
        true,
        now() + chrono::Duration::days(29),
    )
    .unwrap();
    assert_eq!(store::poems(&s.memory, false).unwrap().len(), 2);
    crate::poem_trash::change(&s.memory, "root:same", false, now()).unwrap();
    assert!(
        crate::poem_trash::change(
            &s.memory,
            "root:same",
            true,
            now() + chrono::Duration::days(30)
        )
        .is_err()
    );
    s.purge(now() + chrono::Duration::days(30)).unwrap();
    assert!(
        crate::poem_trash::raw_rows(&root, "haiku_entries.jsonl")
            .unwrap()
            .is_empty()
    );
    assert!(
        crate::poem_trash::raw_rows(&root, "haiku_revisions.jsonl")
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        crate::poem_trash::raw_rows(&other, "haiku_entries.jsonl")
            .unwrap()
            .len(),
        1
    );
    assert!(crate::poem_trash::is_deleted(&root, "same").unwrap());
}
#[test]
fn poem_keys_are_unique_even_when_session_is_named_root() {
    let f = Fixture::new();
    f.poem("root", "same");
    let s = f.store();
    fs::create_dir_all(s.memory.join("sessions/root/long_term")).unwrap();
    fs::copy(
        s.memory.join("long_term/haiku_entries.jsonl"),
        s.memory.join("sessions/root/long_term/haiku_entries.jsonl"),
    )
    .unwrap();
    let rows = store::poems(&s.memory, false).unwrap();
    assert_ne!(rows[0]["id"], rows[1]["id"]);
    for row in rows {
        let (dir, id) = crate::poem_trash::resolve(&s.memory, row["id"].as_str().unwrap()).unwrap();
        assert!(dir.join("long_term/haiku_entries.jsonl").exists());
        assert_eq!(id, "same");
    }
    assert!(crate::poem_trash::resolve(&s.memory, "root:../../outside").is_err());
}
#[test]
fn metadata_and_photo_saves_have_independent_cas_and_restore_placements() {
    let f = Fixture::new();
    f.poem("root", "p1");
    let id = f.photo();
    let s = f.store();
    let edited=s.edit(&json!({"revision":0,"section":"poems","key":"root:p1","value":{"favorite":true,"note":"大事な句"}})).unwrap();
    assert_eq!(edited["revision"], 1);
    assert!(s.edit(&json!({"revision":0,"section":"poems","key":"root:p1","value":{"favorite":false,"note":""}})).is_err());
    let photos=s.photo_edit(&json!({"revision":0,"titles":{&id:"森の写真"},"placements":{"poems:root:p1":[id,null,null]}})).unwrap();
    assert_eq!(photos["revision"], 1);
    s.photo_change(&id, false, now()).unwrap();
    assert!(
        s.photo_snapshot_now().unwrap()["items"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        s.photo_snapshot_now().unwrap()["placements"]["poems:root:p1"][0],
        Value::Null
    );
    assert!(s.image(&id).is_err());
    assert!(s.photos.join(format!("{id}.png")).exists());
    s.photo_change(&id, true, now() + chrono::Duration::days(29))
        .unwrap();
    assert_eq!(
        s.photo_snapshot_now().unwrap()["items"][0]["title"],
        "森の写真"
    );
    assert_eq!(
        s.photo_snapshot_now().unwrap()["placements"]["poems:root:p1"][0],
        id
    );
    crate::poem_trash::change(&s.memory, "root:p1", false, now()).unwrap();
    assert_eq!(
        s.photo_snapshot_now().unwrap()["items"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    s.photo_change(&id, false, now()).unwrap();
    assert!(
        s.photo_change(&id, true, now() + chrono::Duration::days(30))
            .is_err()
    );
    s.purge(now() + chrono::Duration::days(30)).unwrap();
    assert!(!s.photos.join(format!("{id}.png")).exists());
}
#[test]
fn changed_photo_file_is_never_purged() {
    let f = Fixture::new();
    let id = f.photo();
    let s = f.store();
    s.photo_change(&id, false, now()).unwrap();
    let path = s.photos.join(format!("{id}.png"));
    fs::write(&path, b"replacement file").unwrap();
    assert!(s.purge(now() + chrono::Duration::days(31)).is_err());
    assert_eq!(fs::read(path).unwrap(), b"replacement file");
}

#[test]
fn editing_another_slot_keeps_deleted_photo_for_restore() {
    let f = Fixture::new();
    f.poem("root", "p1");
    let a = f.photo();
    let s = f.store();
    fs::copy(
        s.photos.join(format!("{a}.png")),
        s.photos.join("20261013_02.png"),
    )
    .unwrap();
    let b = "20261013_02";
    s.photo_edit(&json!({"revision":0,"titles":{},"placements":{"poems:root:p1":[a,null,null]}}))
        .unwrap();
    s.photo_change(&a, false, now()).unwrap();
    s.photo_edit(&json!({"revision":2,"titles":{},"placements":{"poems:root:p1":[null,b,null]}}))
        .unwrap();
    s.photo_change(&a, true, now() + chrono::Duration::days(1))
        .unwrap();
    assert_eq!(
        s.photo_snapshot_now().unwrap()["placements"]["poems:root:p1"],
        json!([a, b, null])
    );
    // 削除写真の枠自体を別写真に置き換えた場合は、復元しても上書きしない。
    s.photo_change(&a, false, now()).unwrap();
    s.photo_edit(&json!({"revision":5,"titles":{},"placements":{"poems:root:p1":[b,null,null]}}))
        .unwrap();
    s.photo_change(&a, true, now() + chrono::Duration::days(1))
        .unwrap();
    assert_eq!(
        s.photo_snapshot_now().unwrap()["placements"]["poems:root:p1"],
        json!([b, null, null])
    );
}

#[cfg(unix)]
#[test]
fn linked_parent_directories_cannot_purge_another_store() {
    let f = Fixture::new();
    let outside = Fixture::new();
    let dir = outside.poem("another", "p");
    crate::poem_trash::change(&outside.store().memory, "session:another:p", false, now()).unwrap();
    let s = f.store();
    fs::create_dir_all(&s.memory).unwrap();
    std::os::unix::fs::symlink(
        outside.store().memory.join("sessions"),
        s.memory.join("sessions"),
    )
    .unwrap();
    assert_eq!(crate::poem_book::directories(&s.memory).unwrap().len(), 1);
    assert!(crate::poem_trash::change(&s.memory, "session:another:p", false, now()).is_err());
    s.purge(now() + chrono::Duration::days(31)).unwrap();
    assert_eq!(
        crate::poem_trash::raw_rows(&dir, "haiku_entries.jsonl")
            .unwrap()
            .len(),
        1
    );
    outside.photo();
    std::os::unix::fs::symlink(outside.store().photos, &s.photos).unwrap();
    assert!(s.photo_change("20261013_01", false, now()).is_err());
    assert!(outside.store().photos.join("20261013_01.png").exists());
}
#[test]
fn corrupt_poem_records_are_not_discarded_by_cleanup() {
    let f = Fixture::new();
    let root = f.poem("root", "p");
    let s = f.store();
    crate::poem_trash::change(&s.memory, "root:p", false, now()).unwrap();
    let path = root.join("long_term/haiku_entries.jsonl");
    fs::write(&path, b"{broken\n").unwrap();
    assert!(s.purge(now() + chrono::Duration::days(31)).is_err());
    assert_eq!(fs::read(path).unwrap(), b"{broken\n");
}
#[cfg(unix)]
#[test]
fn photo_and_session_symlinks_are_not_followed() {
    let f = Fixture::new();
    let s = f.store();
    fs::create_dir_all(&s.photos).unwrap();
    let outside = f.root.join("outside");
    fs::write(&outside, b"preserve").unwrap();
    std::os::unix::fs::symlink(&outside, s.photos.join("20261013_01.png")).unwrap();
    assert!(
        s.photo_snapshot_now().unwrap()["items"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert!(s.photo_change("20261013_01", false, now()).is_err());
    fs::create_dir_all(s.memory.join("sessions")).unwrap();
    std::os::unix::fs::symlink(&f.root, s.memory.join("sessions/link")).unwrap();
    assert_eq!(crate::poem_book::directories(&s.memory).unwrap().len(), 1);
}
fn test_app(f: &Fixture) -> Arc<App> {
    Arc::new(App {
        store: f.store(),
        ui: f.root.join("ui"),
        csrf: "secret-test-token".into(),
        origin: "http://127.0.0.1:45678".into(),
        authority: "127.0.0.1:45678".into(),
        memory_enabled: true,
        j_lens: None,
        readout_lock: Arc::new(std::sync::Mutex::new(())),
        translation_lock: Arc::new(tokio::sync::Mutex::new(())),
        game_port: 5055,
        data_repository: None,
        publish_lock: Arc::new(std::sync::Mutex::new(())),
    })
}

#[test]
fn repository_plan_preserves_snapshot_bytes_and_rejects_unconfigured_archive() {
    use sha2::{Digest, Sha256};
    let f = Fixture::new(); f.poem("root", "p"); let s = f.store();
    assert!(s.data_repository_plan(None).is_err());
    let root = f.root.join("archive"); fs::create_dir(&root).unwrap();
    s.select_research_storage(research_storage::Selection {root:root.clone(),expected_archive_id:None}).unwrap();
    let plan = s.data_repository_plan(None).unwrap();
    assert_eq!(plan["poems"], 1); assert_eq!(plan["captures"], 0);
    let head = s.read().unwrap()["researchSnapshots"]["root:p"].clone();
    let saved = plan["files"].as_array().unwrap().iter().find(|f| f["path"] == head["relative_path"]).unwrap();
    assert_eq!(saved["content"].as_str().unwrap().as_bytes(), fs::read(root.join(head["relative_path"].as_str().unwrap())).unwrap());
    assert_eq!(saved["sha256"], format!("{:x}", Sha256::digest(saved["content"].as_str().unwrap().as_bytes())));
    let index = plan["files"].as_array().unwrap().iter().find(|f| f["path"] == "index.json").unwrap();
    let index: Value = serde_json::from_str(index["content"].as_str().unwrap()).unwrap();
    assert_eq!(index["poems"][0]["snapshot"]["sha256"], head["sha256"]);
    assert!(index.to_string().find(root.to_str().unwrap()).is_none());
}

#[tokio::test]
async fn repository_upload_and_translation_require_existing_write_guard() {
    use tower::ServiceExt;
    let f = Fixture::new(); let app = test_app(&f);
    for route in ["/api/research-storage", "/api/poems/root%3Ap/internal-capture/1/j-lens-translation"] {
        for origin in [None, Some("https://evil.invalid")] {
            let mut req = axum::http::Request::builder().uri(route).method("POST").header("host", "127.0.0.1:45678");
            if let Some(origin) = origin { req = req.header("origin",origin).header("x-dogido-library","secret-test-token"); }
            let response = router(app.clone()).oneshot(req.body(Body::empty()).unwrap()).await.unwrap();
            assert_eq!(response.status(), StatusCode::FORBIDDEN);
        }
    }
    assert!(!f.store().memory.exists());
}
async fn call(
    app: Arc<App>,
    path: &str,
    body: Option<Value>,
    host: &str,
    token: &str,
    origin: Option<&str>,
) -> (StatusCode, Value) {
    use tower::ServiceExt;
    let mut req = axum::http::Request::builder()
        .uri(path)
        .header("Host", host)
        .header("Content-Type", "application/json");
    if let Some(origin) = origin {
        req = req.header("Origin", origin);
    }
    if !token.is_empty() {
        req = req.header("X-Dogido-Library", token);
    }
    let req = if let Some(body) = body {
        req.method(if path == "/api/poems/export" {
            "POST"
        } else {
            "PUT"
        })
        .body(Body::from(body.to_string()))
        .unwrap()
    } else {
        req.body(Body::empty()).unwrap()
    };
    let response = router(app).oneshot(req).await.unwrap();
    let status = response.status();
    let bytes = axum::body::to_bytes(response.into_body(), 1024 * 1024)
        .await
        .unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}
#[tokio::test]
async fn http_bootstrap_rejects_cross_origin_scripts_even_on_same_local_host() {
    use tower::ServiceExt;
    let f = Fixture::new();
    let app = test_app(&f);
    for site in ["same-site", "cross-site", "unexpected"] {
        let req = axum::http::Request::builder()
            .uri("/bootstrap.js")
            .header(header::HOST, "127.0.0.1:45678")
            .header("sec-fetch-site", site)
            .body(Body::empty())
            .unwrap();
        assert_eq!(
            router(app.clone()).oneshot(req).await.unwrap().status(),
            StatusCode::FORBIDDEN
        );
    }
    for site in [None, Some("same-origin"), Some("none")] {
        let mut req = axum::http::Request::builder()
            .uri("/bootstrap.js")
            .header(header::HOST, "127.0.0.1:45678");
        if let Some(site) = site {
            req = req.header("sec-fetch-site", site);
        }
        let response = router(app.clone())
            .oneshot(req.body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers()["cross-origin-resource-policy"],
            "same-origin"
        );
    }
}
#[tokio::test]
async fn http_write_requires_same_host_and_token_and_updates_shared_reading() {
    let f = Fixture::new();
    let app = test_app(&f);
    let edit = json!({"surface":"草地","reading":"くさち","entry_id":null,"wrong_reading":null,"expected_id":null});
    for (host, token, origin) in [
        ("evil.example", "secret-test-token", None),
        ("127.0.0.1:45678", "", None),
        (
            "127.0.0.1:45678",
            "secret-test-token",
            Some("https://evil.example"),
        ),
    ] {
        assert_eq!(
            call(
                app.clone(),
                "/api/readings",
                Some(edit.clone()),
                host,
                token,
                origin
            )
            .await
            .0,
            StatusCode::FORBIDDEN
        );
    }
    let (status, body) = call(
        app.clone(),
        "/api/readings",
        Some(edit.clone()),
        "127.0.0.1:45678",
        "secret-test-token",
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["correction"]["reading"], "くさち");
    let rows = MemoryStore::new(&app.store.memory)
        .reading_corrections()
        .unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["reading"], "くさち");
    assert_eq!(
        call(
            app.clone(),
            "/api/readings",
            Some(edit),
            "127.0.0.1:45678",
            "secret-test-token",
            None
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    assert_eq!(
        call(app, "/.env", None, "127.0.0.1:45678", "", None)
            .await
            .0,
        StatusCode::NOT_FOUND
    );
}
#[tokio::test]
async fn http_poem_trash_works_without_game_session() {
    let f = Fixture::new();
    f.poem("root", "p1");
    let app = test_app(&f);
    let (status, _) = call(
        app.clone(),
        "/api/trash",
        Some(json!({"kind":"poem","id":"root:p1","restore":false})),
        "127.0.0.1:45678",
        "secret-test-token",
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert!(store::poems(&app.store.memory, false).unwrap().is_empty());
    let (_, body) = call(app.clone(), "/api/trash", None, "127.0.0.1:45678", "", None).await;
    assert_eq!(body["items"].as_array().unwrap().len(), 1);
    assert_eq!(
        call(
            app.clone(),
            "/api/trash",
            Some(json!({"kind":"poem","id":"root:p1","restore":true})),
            "127.0.0.1:45678",
            "secret-test-token",
            None
        )
        .await
        .0,
        StatusCode::OK
    );
    assert_eq!(store::poems(&app.store.memory, false).unwrap().len(), 1);
}

fn records(path: &std::path::Path, rows: &[Value]) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(
        path,
        rows.iter().map(|v| format!("{v}\n")).collect::<String>(),
    )
    .unwrap();
}
fn talk(session: &str, wid: &str, turn: &str, player: &str) -> Value {
    json!({"type":"dialogue_event","kind":"workshop_dialogue","time":"2026-10-13T02:00:00Z",
        "session_id":session,"session_instance":"instance", "event_id":format!("workshop:{turn}"),"workshop_id":wid,
        "details":{"input_turn_id":turn,"player_text":player,"dogido_text":"返答"}})
}
#[test]
fn original_materials_and_delivered_dialogue_are_projected_without_mutation() {
    let f = Fixture::new();
    let dir = f.poem("a", "same");
    f.poem("b", "same");
    let path = dir.join("long_term/haiku_entries.jsonl");
    let mut row: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    row["materials_snapshot"] =
        json!({"interpretation":"元の解釈","source_atoms":[{"atom_id":"one","text":"材料"}]});
    records(&path, &[row.clone()]);
    let before = fs::read(&path).unwrap();
    records(
        &dir.join("long_term/haiku_workshop_turns.jsonl"),
        &[
            json!({"session_id":"a","state_after":{"workshop_id":"w","entry_id":"same"},"player_text":"操作のみ"}),
            json!({"session_id":"a","state_before":{"workshop_id":"w","entry_id":"same"}}),
        ],
    );
    let first = talk("a", "w", "t1", "同じ言葉");
    let second = talk("a", "w", "t2", "同じ言葉");
    let mut unspoken = talk("a", "w", "t3", "未発話");
    unspoken["kind"] = "workshop_unspoken".into();
    records(
        &dir.join("short_term/current_session.jsonl"),
        &[
            first.clone(),
            first,
            second,
            unspoken,
            talk("other", "w", "t4", "別session"),
        ],
    );
    let poems = store::poems(&f.store().memory, false).unwrap();
    let p = poems.iter().find(|p| p["id"] == "session:a:same").unwrap();
    assert_eq!(p["originalLines"][0], "あさのやま");
    assert_eq!(p["lines"][0], "ひるのやま");
    assert_eq!(p["original"], row);
    assert_eq!(p["materials"]["interpretation"], "元の解釈");
    assert_eq!(p["dialogue"].as_array().unwrap().len(), 2);
    assert!(
        poems.iter().find(|p| p["id"] == "session:b:same").unwrap()["dialogue"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(fs::read(path).unwrap(), before);
    assert!(!f.store().memory.join("bookshelf").exists());
}
#[test]
fn ambiguous_workshop_links_are_not_guessed_and_conflicting_dialogue_fails() {
    let f = Fixture::new();
    let dir = f.poem("a", "p");
    let mapping = dir.join("long_term/haiku_workshop_turns.jsonl");
    records(
        &mapping,
        &[
            json!({"session_id":"a","workshop_id":"w","entry_id":"p","state_after":{"workshop_id":"w","entry_id":"other"}}),
        ],
    );
    records(
        &dir.join("short_term/current_session.jsonl"),
        &[talk("a", "w", "t", "質問")],
    );
    assert!(research::dialogue(&dir).unwrap().is_empty());
    records(
        &mapping,
        &[json!({"session_id":"a","workshop_id":"w","entry_id":"p"})],
    );
    records(
        &dir.join("short_term/current_session.jsonl"),
        &[
            talk("a", "w", "t", "質問"),
            talk("a", "w", "t", "異なる質問"),
        ],
    );
    assert!(research::dialogue(&dir).is_err());
}
#[cfg(unix)]
#[test]
fn dialogue_reader_rejects_links_and_corruption_without_creating_missing_files() {
    let f = Fixture::new();
    let dir = f.poem("a", "p");
    assert!(research::dialogue(&dir).unwrap().is_empty());
    assert!(!dir.join("short_term").exists());
    let outside = f.root.join("external");
    fs::create_dir_all(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, dir.join("short_term")).unwrap();
    assert!(research::dialogue(&dir).is_err());
    fs::remove_file(dir.join("short_term")).unwrap();
    fs::create_dir(dir.join("short_term")).unwrap();
    let path = dir.join("short_term/current_session.jsonl");
    fs::write(outside.join("data"), "{}\n").unwrap();
    std::os::unix::fs::symlink(outside.join("data"), &path).unwrap();
    assert!(research::dialogue(&dir).is_err());
    fs::remove_file(&path).unwrap();
    fs::write(&path, "{broken\n").unwrap();
    assert!(research::dialogue(&dir).is_err());
    assert_eq!(fs::read_to_string(path).unwrap(), "{broken\n");
}
fn period(start: &str, end: &str) -> research::Period {
    research::Period {
        start_month: start.into(),
        end_month: end.into(),
    }
}
#[test]
fn month_bounds_are_jst_half_open_and_validate_calendar_months() {
    let (start, end) = period("2026-10", "2026-10").bounds().unwrap();
    assert_eq!(
        start.with_timezone(&Utc).to_rfc3339(),
        "2026-09-30T15:00:00+00:00"
    );
    assert_eq!(
        end.with_timezone(&Utc).to_rfc3339(),
        "2026-10-31T15:00:00+00:00"
    );
    assert_eq!(
        period("2024-02", "2024-02")
            .bounds()
            .unwrap()
            .1
            .to_rfc3339(),
        "2024-03-01T00:00:00+09:00"
    );
    assert_eq!(
        period("2026-12", "2026-12")
            .bounds()
            .unwrap()
            .1
            .to_rfc3339(),
        "2027-01-01T00:00:00+09:00"
    );
    for (a, b) in [
        ("2026-11", "2026-10"),
        ("2026-13", "2026-13"),
        ("2026-1", "2026-10"),
        ("wrongxx", "2026-10"),
        ("2026-00", "2026-01"),
    ] {
        assert!(period(a, b).bounds().is_err());
    }
}
#[test]
fn export_selects_original_month_with_all_history_notes_and_photo_references() {
    let f = Fixture::new();
    let dir = f.poem("root", "p");
    let s = f.store();
    let photo = f.photo();
    let path = dir.join("long_term/haiku_entries.jsonl");
    let mut row: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    let revision = json!({"id":"r2","haiku_id":"p","created_at":"2026-11-20T00:00:00Z","revised_text":"あとから / 修正した句 / 残します"});
    records(
        &dir.join("long_term/haiku_revisions.jsonl"),
        std::slice::from_ref(&revision),
    );
    s.edit(&json!({"revision":0,"section":"poems","key":"root:p","value":{"favorite":true,"note":"研究メモ"}})).unwrap();
    s.photo_edit(&json!({"revision":0,"titles":{&photo:"写真"},"placements":{"poems:root:p":[photo,null,null]}})).unwrap();
    let state = fs::read(s.memory.join("bookshelf/state.json")).unwrap();
    for (created, count) in [
        ("2026-09-30T14:59:59Z", 0),
        ("2026-09-30T15:00:00Z", 1),
        ("2026-10-01T00:00:00+09:00", 1),
        ("2026-10-31T14:59:59Z", 1),
        ("2026-10-31T15:00:00Z", 0),
    ] {
        row["created_at"] = created.into();
        records(&path, &[row.clone()]);
        let export = research::export(&s, &period("2026-10", "2026-10"), now()).unwrap();
        assert_eq!(export["poems"].as_array().unwrap().len(), count);
        if count == 1 {
            let p = &export["poems"][0];
            assert_eq!(p["current"], revision);
            assert_eq!(p["revisions"].as_array().unwrap().len(), 1);
            assert_eq!(p["player"]["note"], "研究メモ");
            assert_eq!(p["photos"]["references"][0]["id"], photo);
            assert!(p["photos"]["references"][0].get("url").is_none());
        }
    }
    row["created_at"] = "2026-10-10T00:00:00Z".into();
    records(&path, &[row.clone()]);
    crate::poem_trash::change(&s.memory, "root:p", false, now()).unwrap();
    assert!(
        research::export(&s, &period("2026-10", "2026-10"), now()).unwrap()["poems"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    crate::poem_trash::change(&s.memory, "root:p", true, now()).unwrap();
    assert_eq!(
        research::export(&s, &period("2026-10", "2026-10"), now()).unwrap()["poems"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    row["created_at"] = "invalid".into();
    records(&path, &[row]);
    assert!(research::export(&s, &period("2026-10", "2026-10"), now()).is_err());
    assert_eq!(
        fs::read(s.memory.join("bookshelf/state.json")).unwrap(),
        state
    );
}
#[test]
fn metadata_contract_rejects_removed_ratings_and_preserves_note_on_failure() {
    let f = Fixture::new();
    f.poem("root", "p");
    let s = f.store();
    s.edit(&json!({"revision":0,"section":"poems","key":"root:p","value":{"favorite":true,"note":"保持"}})).unwrap();
    let before = s.player().unwrap();
    assert!(s.edit(&json!({"revision":1,"section":"poems","key":"root:p","value":{"rating":5,"favorite":false,"note":""}})).is_err());
    assert_eq!(s.player().unwrap(), before);
    assert!(
        !serde_json::to_string(
            &research::export(&s, &period("2026-10", "2026-10"), now()).unwrap()
        )
        .unwrap()
        .contains("rating")
    );
}
#[tokio::test]
async fn export_requires_same_origin_token_and_valid_period() {
    let f = Fixture::new();
    f.poem("root", "p");
    let app = test_app(&f);
    let body = json!({"startMonth":"2026-10","endMonth":"2026-10"});
    assert_eq!(
        call(
            app.clone(),
            "/api/poems/export",
            Some(body.clone()),
            "127.0.0.1:45678",
            "",
            None
        )
        .await
        .0,
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        call(
            app.clone(),
            "/api/poems/export",
            Some(body.clone()),
            "127.0.0.1:45678",
            "secret-test-token",
            Some("http://elsewhere.invalid")
        )
        .await
        .0,
        StatusCode::FORBIDDEN
    );
    let (status, value) = call(
        app.clone(),
        "/api/poems/export",
        Some(body),
        "127.0.0.1:45678",
        "secret-test-token",
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(value["poems"].as_array().unwrap().len(), 1);
    assert_eq!(
        call(
            app,
            "/api/poems/export",
            Some(json!({"startMonth":"2026-13","endMonth":"2026-10"})),
            "127.0.0.1:45678",
            "secret-test-token",
            None
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
}
