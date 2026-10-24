use super::*;
use crate::bookshelf::research_archive::{self as archive, PlayerPhoto, PlayerPhotoTarget};

fn f2(f: &Fixture) -> (String, PathBuf, Vec<u8>) {
    let (_, receipt) = capture(f);
    let bytes = fs::read(receipt.directory.join(receipt.filename)).unwrap();
    let path = f.store().screenshots.join("2026-10-24_10.20.30.png");
    fs::write(&path, &bytes).unwrap();
    let photos = f.store().photo_snapshot_now().unwrap();
    let row = photos["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["source"] == "minecraft_f2")
        .unwrap();
    (row["id"].as_str().unwrap().into(), path, bytes)
}
fn configured(f: &Fixture) -> PathBuf {
    let root = f.root.join("archive");
    fs::create_dir(&root).unwrap();
    let s = f.store();
    fs::create_dir_all(&s.memory).unwrap();
    archive::configure(
        &s.memory,
        &archive::Config::new(root.clone(), uuid::Uuid::new_v4().to_string(), None).unwrap(),
    )
    .unwrap();
    root
}
fn catalog_key() -> String {
    format!(
        "catalog:{}",
        crate::catalog_editor::entries()[0]["id"].as_str().unwrap()
    )
}
fn attach(s: &store::Store, id: &str, targets: &[&str]) -> Value {
    let placements: serde_json::Map<String, Value> = targets
        .iter()
        .map(|t| (t.to_string(), json!([id, null, null])))
        .collect();
    s.photo_edit(&json!({"revision":s.photo_snapshot_now().unwrap()["revision"],"titles":{},"placements":placements})).unwrap()
}
fn location(
    s: &store::Store,
    id: &str,
    index: usize,
) -> (archive::Archive, archive::ArchivedPlayerPhoto) {
    let data = s.read().unwrap();
    let row = &data["playerPhotos"][id]["locations"][index];
    (
        archive::open(serde_json::from_value(row["config"].clone()).unwrap()).unwrap(),
        serde_json::from_value(row["record"].clone()).unwrap(),
    )
}

#[test]
fn listing_f2_candidates_does_not_register_copy_or_delete_them() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let s = f.store();
    let before = s.read().unwrap();
    let snapshot = s.photo_snapshot_now().unwrap();
    assert_eq!(snapshot["items"].as_array().unwrap().len(), 1);
    assert_eq!(snapshot["items"][0]["storageState"], "source");
    assert_eq!(s.image_bytes(&id).unwrap(), bytes);
    assert!(path.exists());
    assert_eq!(s.read().unwrap(), before);
    assert!(!s.memory.join("bookshelf/state.json").exists());
    assert!(!f.root.join("archive").exists());
}

#[test]
fn unconfigured_paste_keeps_source_and_records_pending_only() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let s = f.store();
    let response = attach(&s, &id, &["poems:session:s:hk_new"]);
    assert_eq!(response["items"][0]["storageState"], "pending");
    assert_eq!(fs::read(path).unwrap(), bytes);
    assert!(
        s.read().unwrap()["playerPhotos"][&id]["locations"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}

#[test]
fn saved_poem_attachment_moves_after_verified_copy_and_keeps_photo_id() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let root = configured(&f);
    let s = f.store();
    let original = fs::read(s.memory.join("sessions/s/long_term/haiku_entries.jsonl")).unwrap();
    let response = attach(&s, &id, &["poems:session:s:hk_new"]);
    let copied = root
        .join("2026/10130900/player-images")
        .join(id.trim_start_matches("f2-"))
        .join("image.png");
    assert_eq!(fs::read(copied).unwrap(), bytes);
    assert!(!path.exists());
    assert_eq!(response["items"][0]["id"], id);
    assert_eq!(response["items"][0]["storageState"], "stored");
    assert_eq!(s.image_bytes(&id).unwrap(), bytes);
    assert_eq!(response["revision"], 1);
    assert_eq!(
        fs::read(s.memory.join("sessions/s/long_term/haiku_entries.jsonl")).unwrap(),
        original
    );
    s.sync_research_storage().unwrap();
    let before = s.read().unwrap();
    s.sync_research_storage().unwrap();
    assert_eq!(s.read().unwrap(), before);
    assert_eq!(s.read().unwrap()["photoRevision"], 1);
}

#[test]
fn one_photo_is_copied_to_encyclopedia_and_poem_before_source_removal() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let root = configured(&f);
    let s = f.store();
    let catalog = catalog_key();
    attach(&s, &id, &[&catalog, "poems:session:s:hk_new"]);
    assert!(!path.exists());
    assert!(root.join("encyclopedia").is_dir());
    assert_eq!(
        s.read().unwrap()["playerPhotos"][&id]["locations"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    for index in 0..2 {
        let (archive, record) = location(&s, &id, index);
        assert_eq!(archive.read_player_photo(&record).unwrap(), bytes);
    }
    s.photo_edit(
        &json!({"revision":1,"titles":{&id:"手動写真"},"placements":{catalog:[null,null,null]}}),
    )
    .unwrap();
    assert_eq!(
        s.photo_snapshot_now().unwrap()["items"][0]["title"],
        "手動写真"
    );
    assert_eq!(s.image_bytes(&id).unwrap(), bytes);
}

#[test]
fn moved_image_can_be_pasted_elsewhere_and_archive_change_keeps_old_copy() {
    let f = Fixture::new();
    let (id, _, bytes) = f2(&f);
    let first = configured(&f);
    let s = f.store();
    attach(&s, &id, &["poems:session:s:hk_new"]);
    let catalog = catalog_key();
    attach(&s, &id, &[&catalog]);
    let previous = archive::load_config(&s.memory).unwrap().unwrap();
    let second = f.root.join("second");
    fs::create_dir(&second).unwrap();
    let status = s
        .select_research_storage(research_storage::Selection {
            root: second.clone(),
            expected_archive_id: Some(previous.archive_id),
        })
        .unwrap();
    assert_eq!(status["state"], "ready");
    assert_eq!(s.image_bytes(&id).unwrap(), bytes);
    assert!(first.join("encyclopedia").exists());
    assert!(second.join("encyclopedia").exists());
}

#[test]
fn disconnected_drive_keeps_original_then_worker_finishes_after_reconnect() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let root = configured(&f);
    let s = f.store();
    let away = f.root.join("away");
    fs::rename(&root, &away).unwrap();
    attach(&s, &id, &["poems:session:s:hk_new"]);
    assert_eq!(fs::read(&path).unwrap(), bytes);
    assert!(!root.exists());
    assert!(s.sync_research_storage().is_err());
    assert!(!root.exists());
    assert!(path.exists());
    fs::rename(away, &root).unwrap();
    s.sync_research_storage().unwrap();
    assert!(!path.exists());
    assert_eq!(s.image_bytes(&id).unwrap(), bytes);
    assert_eq!(s.read().unwrap()["photoRevision"], 1);
}

#[test]
fn changed_source_or_broken_png_is_not_removed() {
    let f = Fixture::new();
    let (id, path, mut bytes) = f2(&f);
    let s = f.store();
    attach(&s, &id, &["poems:session:s:hk_new"]);
    configured(&f);
    bytes[40] ^= 1;
    fs::write(&path, &bytes).unwrap();
    assert!(s.sync_research_storage().is_err());
    assert_eq!(fs::read(&path).unwrap(), bytes);
    let newer = s.photo_snapshot_now().unwrap()["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["id"] != id)
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .to_owned();
    attach(&s, &newer, &["poems:session:s:hk_new"]);
    assert_eq!(fs::read(path).unwrap(), bytes);
    assert_eq!(
        s.read().unwrap()["playerPhotos"][newer]["storageState"],
        "error"
    );
}

#[test]
fn rejected_or_cancelled_placement_does_not_move_a_photo() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    configured(&f);
    let s = f.store();
    for request in [
        json!({"revision":99,"titles":{},"placements":{"poems:session:s:hk_new":[id,null,null]}}),
        json!({"revision":0,"titles":{},"placements":{"catalog:unknown":[id,null,null]}}),
    ] {
        assert!(s.photo_edit(&request).is_err());
        assert_eq!(fs::read(&path).unwrap(), bytes);
    }
    attach(&s, &id, &[]);
    assert!(path.exists());
    assert!(s.read().unwrap()["playerPhotos"].is_null());
}

#[test]
fn interrupted_local_cleanup_resumes_from_recorded_private_name() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let s = f.store();
    attach(&s, &id, &["poems:session:s:hk_new"]);
    configured(&f);
    let mut data = s.read().unwrap();
    let row = &data["playerPhotos"][&id];
    let photo: PlayerPhoto=serde_json::from_value(json!({"id":id,"filename":row["filename"],"sha256":row["sha256"],"width":row["width"],"height":row["height"]})).unwrap();
    let archive = archive::load(&s.memory).unwrap().unwrap();
    let record = archive
        .store_player_photo(
            &PlayerPhotoTarget::Poem {
                poem_id: "session:s:hk_new".into(),
                original_created_at: now(),
            },
            &photo,
            &bytes,
        )
        .unwrap();
    let holding = format!(".dogido-f2-moving-{}.png", uuid::Uuid::new_v4());
    data["playerPhotos"][&id]["locations"] = json!([{"config":archive.config(),"record":record}]);
    data["playerPhotos"][&id]["transferName"] = json!(holding);
    fs::write(
        s.memory.join("bookshelf/state.json"),
        serde_json::to_vec(&data).unwrap(),
    )
    .unwrap();
    fs::rename(&path, s.screenshots.join(&holding)).unwrap();
    s.sync_research_storage().unwrap();
    assert!(!path.exists());
    assert!(!s.screenshots.join(holding).exists());
    assert_eq!(s.image_bytes(&id).unwrap(), bytes);
}

#[test]
fn moved_f2_trash_and_restore_keeps_the_ssd_file() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    configured(&f);
    let s = f.store();
    attach(&s, &id, &["poems:session:s:hk_new"]);
    s.photo_change(&id, false, now()).unwrap();
    assert!(s.image_bytes(&id).is_err());
    s.photo_change(&id, true, now()).unwrap();
    assert_eq!(s.image_bytes(&id).unwrap(), bytes);
    s.photo_change(&id, false, now()).unwrap();
    s.purge(now() + chrono::Duration::days(31)).unwrap();
    let (archive, record) = location(&s, &id, 0);
    assert_eq!(archive.read_player_photo(&record).unwrap(), bytes);
    assert!(!path.exists());
}

#[test]
fn changed_screenshot_directory_never_marks_an_old_source_as_removed() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let s = f.store();
    attach(&s, &id, &["poems:session:s:hk_new"]);
    configured(&f);
    let mut data = s.read().unwrap();
    let row = &data["playerPhotos"][&id];
    let photo: PlayerPhoto = serde_json::from_value(json!({"id":id,"filename":row["filename"],"sha256":row["sha256"],"width":row["width"],"height":row["height"]})).unwrap();
    let archive = archive::load(&s.memory).unwrap().unwrap();
    let record = archive
        .store_player_photo(
            &PlayerPhotoTarget::Poem {
                poem_id: "session:s:hk_new".into(),
                original_created_at: now(),
            },
            &photo,
            &bytes,
        )
        .unwrap();
    data["playerPhotos"][&id]["locations"] = json!([{"config":archive.config(),"record":record}]);
    data["playerPhotos"][&id]["storageState"] = "copied".into();
    fs::write(
        s.memory.join("bookshelf/state.json"),
        serde_json::to_vec(&data).unwrap(),
    )
    .unwrap();
    let mut switched = s.clone();
    switched.screenshots = f.root.join("another-screenshots");
    fs::create_dir(&switched.screenshots).unwrap();
    assert!(switched.sync_research_storage().is_err());
    assert_eq!(fs::read(&path).unwrap(), bytes);
    assert_ne!(
        s.read().unwrap()["playerPhotos"][&id]["sourceRemoved"],
        true
    );
    assert_eq!(
        s.read().unwrap()["playerPhotos"][&id]["storageState"],
        "error"
    );
    assert!(
        switched.photo_snapshot_now().unwrap()["items"][0]
            .get("sourceDirectory")
            .is_none()
    );
    s.sync_research_storage().unwrap();
    assert!(!path.exists());
    assert_eq!(s.image_bytes(&id).unwrap(), bytes);
}

#[cfg(unix)]
#[test]
fn f2_symlinks_and_encyclopedia_directory_links_are_not_followed() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let root = configured(&f);
    let s = f.store();
    symlink(&path, s.screenshots.join("2026-10-24_10.20.31.png")).unwrap();
    assert_eq!(
        s.photo_snapshot_now().unwrap()["items"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let outside = f.root.join("outside");
    fs::create_dir(&outside).unwrap();
    symlink(&outside, root.join("encyclopedia")).unwrap();
    attach(&s, &id, &[&catalog_key()]);
    assert_eq!(fs::read(path).unwrap(), bytes);
    assert_eq!(fs::read_dir(outside).unwrap().count(), 0);
}

#[test]
fn automatic_legacy_f2_filenames_stay_protected_not_player_candidates() {
    let f = Fixture::new();
    let (command, mut receipt) = capture(&f);
    let old = receipt.directory.join(&receipt.filename);
    receipt.filename = "2026-10-20_12.00.00.png".into();
    fs::rename(old, receipt.directory.join(&receipt.filename)).unwrap();
    let s = f.store();
    s.register_capture(&command, &receipt).unwrap();
    let photos = s.photo_snapshot_now().unwrap();
    assert_eq!(photos["items"].as_array().unwrap().len(), 1);
    assert_eq!(photos["items"][0]["protected"], true);
}

#[cfg(unix)]
#[test]
fn failure_at_a_second_target_keeps_source_even_after_first_copy_succeeded() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let root = configured(&f);
    let s = f.store();
    s.sync_research_storage().unwrap();
    let outside = f.root.join("outside");
    fs::create_dir(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, root.join("2026/10130900/player-images")).unwrap();
    attach(&s, &id, &[&catalog_key(), "poems:session:s:hk_new"]);
    assert!(root.join("encyclopedia").is_dir());
    assert_eq!(fs::read(path).unwrap(), bytes);
    assert_eq!(fs::read_dir(outside).unwrap().count(), 0);
    assert_eq!(
        s.read().unwrap()["playerPhotos"][id]["storageState"],
        "error"
    );
}

#[test]
fn modified_archived_image_is_never_served_as_the_original() {
    let f = Fixture::new();
    let (id, path, bytes) = f2(&f);
    let root = configured(&f);
    let s = f.store();
    attach(&s, &id, &["poems:session:s:hk_new"]);
    assert!(!path.exists());
    let image = root
        .join("2026/10130900/player-images")
        .join(id.trim_start_matches("f2-"))
        .join("image.png");
    let mut modified = bytes;
    modified[40] ^= 1;
    fs::write(&image, &modified).unwrap();
    assert!(s.image_bytes(&id).is_err());
    assert!(s.sync_research_storage().is_err());
    assert_eq!(fs::read(image).unwrap(), modified);
}

#[tokio::test]
async fn f2_save_requires_current_host_token_and_memory_enabled() {
    let f = Fixture::new();
    let (id, path, _) = f2(&f);
    configured(&f);
    let app = test_app(&f);
    let body =
        json!({"revision":0,"titles":{},"placements":{"poems:session:s:hk_new":[id,null,null]}});
    for (host, token, origin) in [
        ("bad.invalid", "secret-test-token", None),
        ("127.0.0.1:45678", "", None),
        (
            "127.0.0.1:45678",
            "secret-test-token",
            Some("https://other.invalid"),
        ),
    ] {
        assert_eq!(
            call(
                app.clone(),
                "/api/photos",
                Some(body.clone()),
                host,
                token,
                origin
            )
            .await
            .0,
            StatusCode::FORBIDDEN
        );
        assert!(path.exists());
    }
    let mut disabled = (*app).clone();
    disabled.memory_enabled = false;
    assert_eq!(
        call(
            Arc::new(disabled),
            "/api/photos",
            Some(body),
            "127.0.0.1:45678",
            "secret-test-token",
            None
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    assert!(path.exists());
}
