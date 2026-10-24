//! F2候補は読むだけ。貼り付けの確定後だけSSDへコピー・照合し、元画像を移す。
use super::{research_archive as archive, store::Store};
use anyhow::{Context, Result, ensure};
use archive::{ArchivedPlayerPhoto, PlayerPhoto, PlayerPhotoTarget};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{Read, Seek, SeekFrom},
    path::Path,
};

const LIMIT: u64 = 64 * 1024 * 1024;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Location {
    config: archive::Config,
    record: ArchivedPlayerPhoto,
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn is_f2(name: &str) -> bool {
    !name.starts_with("dogido_") && crate::screenshot::valid_filename(name)
}
fn identity(name: &str, metadata: &fs::Metadata) -> Result<String> {
    let modified = metadata
        .modified()?
        .duration_since(std::time::UNIX_EPOCH)?
        .as_nanos();
    #[cfg(unix)]
    let inode = {
        use std::os::unix::fs::MetadataExt;
        format!("{}:{}", metadata.dev(), metadata.ino())
    };
    #[cfg(not(unix))]
    let inode = String::new();
    Ok(format!(
        "f2-{}",
        sha(format!("{name}\0{}\0{modified}\0{inode}", metadata.len()).as_bytes())
    ))
}
fn information(row: &Value) -> Result<PlayerPhoto> {
    Ok(serde_json::from_value(
        json!({"id":row["id"],"filename":row["filename"],"sha256":row["sha256"],"width":row["width"],"height":row["height"]}),
    )?)
}
fn locations(row: &Value) -> Result<Vec<Location>> {
    Ok(serde_json::from_value(
        row.get("locations").cloned().unwrap_or_else(|| json!([])),
    )?)
}
fn attached(data: &Value, id: &str) -> BTreeSet<String> {
    if data["photoTrash"].get(id).is_some() {
        return BTreeSet::new();
    }
    data["placements"]
        .as_object()
        .into_iter()
        .flatten()
        .filter(|(_, slots)| {
            slots
                .as_array()
                .is_some_and(|slots| slots.iter().any(|v| v == id))
        })
        .map(|(target, _)| target.clone())
        .collect()
}
fn save_state(store: &Store, data: &Value) -> Result<()> {
    // 移動状態だけで、編集中のタイトル・貼付けのCASを失効させない。
    crate::poem_trash::atomic(
        &store.memory.join("bookshelf/state.json"),
        &serde_json::to_vec(data)?,
    )
}

impl Store {
    fn f2_file(&self, name: &str) -> Result<fs::File> {
        ensure!(
            !name.contains(['/', '\\']) && !name.is_empty(),
            "unsafe_filename"
        );
        let dir = fs::symlink_metadata(&self.screenshots)?;
        ensure!(
            dir.is_dir() && !dir.file_type().is_symlink(),
            "unsafe_directory"
        );
        let mut options = fs::OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let file = options.open(self.screenshots.join(name))?;
        let metadata = file.metadata()?;
        ensure!(
            metadata.is_file() && metadata.len() <= LIMIT,
            "invalid_image"
        );
        Ok(file)
    }
    fn f2_candidate(&self, filename: &str) -> Result<Value> {
        ensure!(is_f2(filename), "invalid_player_photo_filename");
        let mut file = self.f2_file(filename)?;
        let metadata = file.metadata()?;
        let mut header = [0; 24];
        file.read_exact(&mut header)?;
        let (width, height) = super::captures::dimensions(&header)?;
        let id = identity(filename, &metadata)?;
        let created: DateTime<Utc> = metadata.modified()?.into();
        Ok(
            json!({"id":id,"source":"minecraft_f2","filename":filename,"sha256":null,
            "width":width,"height":height,"createdAt":created.to_rfc3339(),"kind":"player","title":"",
            "storageState":"source","url":format!("/api/photos/{id}/image")}),
        )
    }
    fn f2_source_bytes(&self, row: &Value, actual_name: Option<&str>) -> Result<Vec<u8>> {
        if let Some(directory) = row["sourceDirectory"].as_str() {
            ensure!(
                fs::canonicalize(&self.screenshots)? == Path::new(directory),
                "player_photo_source_directory_changed"
            );
        }
        let name = row["filename"]
            .as_str()
            .context("invalid_player_photo_filename")?;
        ensure!(is_f2(name), "invalid_player_photo_filename");
        let mut file = self.f2_file(actual_name.unwrap_or(name))?;
        let before = file.metadata()?;
        ensure!(
            row["id"] == identity(name, &before)?,
            "player_photo_changed"
        );
        file.seek(SeekFrom::Start(0))?;
        let mut bytes = Vec::new();
        (&mut file).take(LIMIT + 1).read_to_end(&mut bytes)?;
        ensure!(
            bytes.len() as u64 == before.len()
                && bytes.len() as u64 <= LIMIT
                && identity(name, &before)? == identity(name, &file.metadata()?)?,
            "player_photo_changed"
        );
        let (width, height) = super::captures::dimensions(&bytes)?;
        ensure!(
            row["width"] == width && row["height"] == height,
            "player_photo_changed"
        );
        if let Some(expected) = row["sha256"].as_str() {
            ensure!(sha(&bytes) == expected, "player_photo_changed");
        }
        Ok(bytes)
    }

    /// 自動撮影で登録済みの旧F2名を、プレイヤー写真として再登録しない。
    pub(super) fn player_photo_rows(&self, data: &Value) -> Result<Vec<Value>> {
        let mut rows: Vec<Value> = data["playerPhotos"]
            .as_object()
            .into_iter()
            .flat_map(|m| m.values())
            .cloned()
            .collect();
        let ids: BTreeSet<_> = rows
            .iter()
            .filter_map(|r| r["id"].as_str().map(str::to_owned))
            .collect();
        let automatic: BTreeSet<_> = data["minecraftPhotos"]
            .as_object()
            .into_iter()
            .flat_map(|m| m.values())
            .filter_map(|r| r["filename"].as_str())
            .collect();
        let entries = match fs::symlink_metadata(&self.screenshots) {
            Ok(meta) if meta.is_dir() && !meta.file_type().is_symlink() => {
                fs::read_dir(&self.screenshots).ok()
            }
            _ => None,
        };
        if let Some(entries) = entries {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                if !is_f2(&name)
                    || automatic.contains(name.as_str())
                    || !entry.file_type().is_ok_and(|t| t.is_file())
                {
                    continue;
                }
                if let Ok(row) = self.f2_candidate(&name)
                    && !ids.contains(row["id"].as_str().unwrap())
                {
                    rows.push(row);
                }
            }
        }
        for row in &mut rows {
            for private in [
                "locations",
                "transferName",
                "sourceRemoved",
                "sourceDirectory",
            ] {
                row.as_object_mut().unwrap().remove(private);
            }
        }
        Ok(rows)
    }

    pub(super) fn player_photo_image(&self, data: &Value, id: &str) -> Result<Vec<u8>> {
        let row = data["playerPhotos"]
            .get(id)
            .cloned()
            .or_else(|| {
                self.player_photo_rows(data)
                    .ok()?
                    .into_iter()
                    .find(|r| r["id"] == id)
            })
            .context("unknown_photo")?;
        self.player_photo_bytes(&row)
    }
    fn player_photo_bytes(&self, row: &Value) -> Result<Vec<u8>> {
        if let Ok(bytes) = self.f2_source_bytes(row, None) {
            return Ok(bytes);
        }
        let current = archive::load_config(&self.memory)?;
        for mut location in locations(row)? {
            if let Some(config) = current
                .as_ref()
                .filter(|c| c.archive_id == location.config.archive_id)
            {
                location.config = config.clone();
            }
            if let Ok(bytes) =
                archive::open(location.config).and_then(|a| a.read_player_photo(&location.record))
            {
                return Ok(bytes);
            }
        }
        anyhow::bail!("player_photo_unavailable")
    }

    /// 写真の変更が全て検証された後に呼ぶ。候補一覧を見ただけでは登録・移動しない。
    pub(super) fn stage_player_photos(&self, data: &mut Value, snapshot: &Value) -> Result<()> {
        let ids: BTreeSet<String> = data["placements"]
            .as_object()
            .into_iter()
            .flat_map(|m| m.values())
            .flat_map(|v| v.as_array().into_iter().flatten())
            .filter_map(|id| id.as_str())
            .filter(|id| id.starts_with("f2-"))
            .map(str::to_owned)
            .collect();
        if !ids.is_empty() {
            data["researchOriginals"] = super::research_storage::originals(data);
        }
        for id in ids {
            if data["photoTrash"].get(&id).is_some() || data["playerPhotos"].get(&id).is_some() {
                continue;
            }
            let mut row = snapshot["items"]
                .as_array()
                .context("invalid_photos")?
                .iter()
                .find(|r| r["id"] == id)
                .context("unknown_photo")?
                .clone();
            let bytes = self.f2_source_bytes(&row, None)?;
            row["sha256"] = sha(&bytes).into();
            row["locations"] = json!([]);
            row["sourceRemoved"] = false.into();
            row["sourceDirectory"] = json!(fs::canonicalize(&self.screenshots)?);
            row["storageState"] = "pending".into();
            if !data["playerPhotos"].is_object() {
                data["playerPhotos"] = json!({});
            }
            data["playerPhotos"][id] = row;
        }
        Ok(())
    }

    pub(super) fn sync_player_photos_now(&self) -> Result<()> {
        if archive::load_config(&self.memory)?.is_none() {
            return Ok(());
        }
        let _sync = self.research_lock()?;
        let archive = archive::load(&self.memory)?.context("archive_not_configured")?;
        self.sync_player_photos_locked(&archive)
    }

    /// 既存の保全workerと貼付け保存が共有する。外付けI/O中はstate lockを保持しない。
    pub(super) fn sync_player_photos_locked(&self, archive: &archive::Archive) -> Result<()> {
        let source = self.read()?;
        let active: Vec<_> = source["playerPhotos"]
            .as_object()
            .into_iter()
            .flatten()
            .filter_map(|(id, row)| {
                let keys = attached(&source, id);
                (!keys.is_empty()).then_some((id.clone(), row.clone(), keys))
            })
            .collect();
        if active.is_empty() {
            return Ok(());
        }
        let poems: BTreeMap<String, Value> = super::store::poems(&self.memory, false)?
            .into_iter()
            .chain(super::store::poems(&self.memory, true)?)
            .map(|p| (p["id"].as_str().unwrap().to_owned(), p))
            .collect();
        let catalog: BTreeSet<String> = crate::catalog_editor::entries()
            .into_iter()
            .filter_map(|e| e["id"].as_str().map(str::to_owned))
            .collect();
        let mut failures = Vec::new();
        for (id, row, keys) in active {
            let result = (|| -> Result<()> {
                let photo = information(&row)?;
                let bytes = self.player_photo_bytes(&row)?;
                let mut copied = Vec::new();
                for key in &keys {
                    let target = if let Some(poem_id) = key.strip_prefix("poems:") {
                        let poem = poems.get(poem_id).context("capture_target_missing")?;
                        PlayerPhotoTarget::Poem {
                            poem_id: poem_id.into(),
                            original_created_at: DateTime::parse_from_rfc3339(
                                poem["createdAt"].as_str().context("missing_created_at")?,
                            )?
                            .with_timezone(&Utc),
                        }
                    } else {
                        let entry_id = key
                            .strip_prefix("catalog:")
                            .filter(|id| catalog.contains(*id))
                            .context("unknown_entry")?;
                        PlayerPhotoTarget::Encyclopedia {
                            entry_id: entry_id.into(),
                        }
                    };
                    copied.push(Location {
                        config: archive.config().clone(),
                        record: archive.store_player_photo(&target, &photo, &bytes)?,
                    });
                }
                let _state = self.write_lock()?;
                let mut data = self.read()?;
                let before = data.clone();
                ensure!(
                    information(&data["playerPhotos"][&id])? == photo,
                    "player_photo_changed"
                );
                let stable = attached(&data, &id) == keys;
                let mut saved = locations(&data["playerPhotos"][&id])?;
                for location in copied {
                    saved.retain(|old| {
                        !(old.config.archive_id == location.config.archive_id
                            && old.record.target == location.record.target)
                    });
                    saved.push(location);
                }
                data["playerPhotos"][&id]["locations"] = serde_json::to_value(saved)?;
                data["playerPhotos"][&id]["storageState"] = if !stable {
                    "pending"
                } else if data["playerPhotos"][&id]["sourceRemoved"] == true {
                    "stored"
                } else {
                    "copied"
                }
                .into();
                // コピー先と検証結果をMacに確定してから、元ファイルに触れる。
                if data != before {
                    save_state(self, &data)?;
                }
                let persisted = data.clone();
                if !stable {
                    return Ok(());
                }
                if data["playerPhotos"][&id]["sourceRemoved"] != true {
                    if data["playerPhotos"][&id]["transferName"].as_str().is_none() {
                        data["playerPhotos"][&id]["transferName"] =
                            format!(".dogido-f2-moving-{}.png", uuid::Uuid::new_v4()).into();
                        save_state(self, &data)?;
                    }
                    self.remove_copied_f2(&data["playerPhotos"][&id])?;
                    data["playerPhotos"][&id]["sourceRemoved"] = true.into();
                    data["playerPhotos"][&id]
                        .as_object_mut()
                        .unwrap()
                        .remove("transferName");
                }
                data["playerPhotos"][&id]["storageState"] = "stored".into();
                data["playerPhotos"][&id]
                    .as_object_mut()
                    .unwrap()
                    .remove("storageError");
                if data != persisted {
                    save_state(self, &data)?;
                }
                Ok(())
            })();
            if let Err(error) = result {
                tracing::warn!(event="player_photo_transfer_pending",photo_id=id,error=%error);
                let _state = self.write_lock()?;
                let mut data = self.read()?;
                if data["playerPhotos"].get(&id).is_some() {
                    data["playerPhotos"][&id]["storageState"] = "error".into();
                    data["playerPhotos"][&id]["storageError"] =
                        "SSDへの移動を完了できていません。元画像は、コピーを検証するまで残します。"
                            .into();
                    save_state(self, &data)?;
                }
                failures.push(format!("{id}: {error:#}"));
            }
        }
        ensure!(
            failures.is_empty(),
            "player_photo_transfer_pending: {}",
            failures.join("; ")
        );
        Ok(())
    }

    fn remove_copied_f2(&self, row: &Value) -> Result<()> {
        let directory = row["sourceDirectory"]
            .as_str()
            .context("missing_player_photo_source_directory")?;
        ensure!(
            fs::canonicalize(&self.screenshots)? == Path::new(directory),
            "player_photo_source_directory_changed"
        );
        let name = row["filename"]
            .as_str()
            .context("invalid_player_photo_filename")?;
        let holding = row["transferName"]
            .as_str()
            .context("missing_transfer_name")?;
        let uuid = holding
            .strip_prefix(".dogido-f2-moving-")
            .and_then(|s| s.strip_suffix(".png"))
            .context("invalid_transfer_name")?;
        ensure!(
            uuid::Uuid::parse_str(uuid)?.to_string() == uuid,
            "invalid_transfer_name"
        );
        let source = self.screenshots.join(name);
        let temporary = self.screenshots.join(holding);
        if !temporary.try_exists()? {
            if !source.try_exists()? {
                return Ok(());
            }
            self.f2_source_bytes(row, None)?;
            rename_without_replace(&source, &temporary)?;
        }
        // renameの前後で外部が同名画像を差替えた場合、別画像を削除せず元へ戻す。
        if let Err(error) = self.f2_source_bytes(row, Some(holding)) {
            let _ = rename_without_replace(&temporary, &source);
            return Err(error);
        }
        fs::remove_file(&temporary)?;
        fs::File::open(&self.screenshots)?.sync_all()?;
        Ok(())
    }
}

fn rename_without_replace(source: &Path, destination: &Path) -> Result<()> {
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        use std::os::unix::ffi::OsStrExt;
        let source = std::ffi::CString::new(source.as_os_str().as_bytes())?;
        let destination = std::ffi::CString::new(destination.as_os_str().as_bytes())?;
        #[cfg(target_os = "macos")]
        let result = unsafe {
            libc::renameatx_np(
                libc::AT_FDCWD,
                source.as_ptr(),
                libc::AT_FDCWD,
                destination.as_ptr(),
                libc::RENAME_EXCL,
            )
        };
        #[cfg(target_os = "linux")]
        let result = unsafe {
            libc::renameat2(
                libc::AT_FDCWD,
                source.as_ptr(),
                libc::AT_FDCWD,
                destination.as_ptr(),
                libc::RENAME_NOREPLACE,
            )
        };
        if result != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(())
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        let _ = (source, destination);
        anyhow::bail!("player_photo_move_unsupported")
    }
}
