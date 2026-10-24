//! 研究原本の識別・保全。通常の貼付けやごみ箱とは独立して出自を保持する。
use super::research_archive as archive;
use super::store::Store;
use crate::poem_trash as trash;
use anyhow::{Context, Result, ensure};
use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::path::PathBuf;

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Selection {
    pub root: PathBuf,
    pub expected_archive_id: Option<String>,
}

fn created(record: &Value) -> Result<DateTime<Utc>> {
    Ok(DateTime::parse_from_rfc3339(
        record["created_at"]
            .as_str()
            .context("archive_missing_original_date")?,
    )?
    .with_timezone(&Utc))
}

pub(super) fn mark_pending(data: &mut Value) {
    if data["researchStorage"].is_object() && data["researchStorage"]["state"] == "ready" {
        data["researchStorage"]["state"] = "pending".into();
        data["researchStorage"]["detail"] = "新しい変更の保全を待っています。".into();
    }
}

pub(super) fn protected(data: &Value, id: &str) -> bool {
    data["minecraftPhotos"].get(id).is_some()
}

pub(super) fn originals(data: &Value) -> Value {
    let mut result = data["researchOriginals"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    // 旧記録は一件に確定できる場合だけ初回とする。時刻や現在の貼付け枠では推測しない。
    let mut candidates: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for (id, row) in data["minecraftPhotos"].as_object().into_iter().flatten() {
        if let Some(poem) = row["poemId"].as_str() {
            candidates.entry(poem).or_default().push(id);
        }
    }
    for (poem, ids) in candidates {
        if ids.len() == 1 {
            result
                .entry(format!("poems:{poem}"))
                .or_insert_with(|| json!(ids[0]));
        }
    }
    Value::Object(result)
}

pub(super) fn provenance(photo: &Value, original_poem: Value) -> Value {
    json!({"schemaVersion":1,"photoId":photo["id"],"poemId":photo["poemId"],
        "requestId":photo["requestId"],"receipt":photo["receipt"],
        "width":photo["width"],"height":photo["height"],"originalPoem":original_poem,
        "observationScope":"captured_and_registered_not_proof_of_human_viewing"})
}

impl Store {
    pub(super) fn research_lock(&self) -> Result<std::fs::File> {
        let path = self.memory.join("bookshelf/.research-sync.lock");
        trash::regular(&path)?;
        crate::haiku_record::locked_file(&path)
    }

    /// 設定値はMacに保持し、GETからSSDへアクセス／保存しない。
    pub(super) fn research_status(&self, data: &Value) -> Result<Value> {
        let Some(config) = archive::load_config(&self.memory)? else {
            return Ok(json!({"state":"unconfigured","root":null,"archiveId":null}));
        };
        let mut status = if data["researchStorage"]["archiveId"] == config.archive_id {
            data["researchStorage"].clone()
        } else {
            json!({"state":"pending","detail":"保全を待っています。"})
        };
        status["root"] = json!(config.root);
        status["archiveId"] = json!(config.archive_id);
        Ok(status)
    }

    pub(super) fn select_research_storage(&self, selection: Selection) -> Result<Value> {
        // rootは利用者がフォルダ選択で作成／選択した既存directoryだけ。切断時に再作成しない。
        let _state = self.write_lock()?;
        drop(_state);
        let lock = self.research_lock()?;
        let current = archive::load_config(&self.memory)?;
        ensure!(
            current.as_ref().map(|c| &c.archive_id) == selection.expected_archive_id.as_ref(),
            "archive_config_conflict"
        );
        let next = if current.as_ref().is_some_and(|c| c.root == selection.root) {
            current.clone().unwrap()
        } else {
            archive::existing_config(&selection.root)?.unwrap_or(archive::Config::new(
                selection.root,
                uuid::Uuid::new_v4().to_string(),
                None,
            )?)
        };
        archive::configure_with_expected(&self.memory, &next, current.as_ref())?;
        drop(lock);
        // 設定成功と保全成功を分ける。未完了でも設定は保持し、次回workerで再試行する。
        let _ = self.sync_research_storage();
        self.research_status(&self.read()?)
    }

    /// 正本は移動せず、全句・対応する対話／メモ／原本画像を内容別に追記する。
    /// 外付けI/O中に本棚state lockやゲーム状態mutexを保持しない。
    pub fn sync_research_storage(&self) -> Result<Value> {
        self.protect_research_captures()?;
        let Some(config) = archive::load_config(&self.memory)? else {
            return Ok(json!({"state":"unconfigured"}));
        };
        let _lock = self.research_lock()?;
        // 設定変更と同じlockで、古い設定による書込みを始めない。
        let config = archive::load_config(&self.memory)?.unwrap_or(config);
        let source = self.read()?;
        let mut captures = BTreeMap::new();
        let mut snapshots = BTreeMap::new();
        let mut failures = Vec::new();
        let result = (|| -> Result<()> {
            let archive = archive::load(&self.memory)?.context("archive_not_configured")?;
            if let Err(error) = self.sync_player_photos_locked(&archive) {
                failures.push(format!("{error:#}"));
            }
            let records = super::research::archive_records(self, &source)?;
            for mut record in records {
                let id = record["id"].as_str().context("archive_missing_poem_id")?.to_string();
                let saved = (|| -> Result<archive::ArchivedSnapshot> {
                    let poem = archive.for_poem(&id, created(&record["original"])?)?;
                    if record["original"]["internal_capture"].is_object() {
                        let head = &source["researchSnapshots"][&id];
                        let previous = if head["archive_id"] == config.archive_id {
                            archive.read_snapshot(&serde_json::from_value(head.clone())?)?
                        } else { Value::Null };
                        record["internalCaptureStorage"] = super::internal_capture::archive_requests(
                            self, &poem, &record, &previous, &config.archive_id, &mut failures);
                    }
                    poem.snapshot(&record)
                })();
                match saved {
                    Ok(saved) => {
                        snapshots.insert(id.clone(), json!(saved));
                    }
                    Err(error) => failures.push(format!("{id}: {error:#}")),
                }
            }
            for (id, row) in source["minecraftPhotos"].as_object().into_iter().flatten() {
                let saved = (|| -> Result<Value> {
                    let receipt: crate::screenshot::Receipt =
                        serde_json::from_value(row["receipt"].clone())?;
                    let command = row["requestId"].as_str().context("invalid_capture_id")?;
                    let provenance = &row["researchRecord"];
                    let scoped = archive
                        .for_poem(&receipt.poem_id, created(&provenance["originalPoem"])?)?;
                    // 保存済みでも全hashを再確認。元PNGが欠損しても検証済み保全先から読める。
                    let bytes = self
                        .minecraft_bytes(&receipt.filename, &receipt.sha256)
                        .or_else(|_| scoped.read(command, &receipt.sha256))?;
                    let archived = scoped.store(command, &receipt, provenance, &bytes)?;
                    Ok(json!({"state":"saved","archiveId":config.archive_id,"record":archived}))
                })();
                match saved {
                    Ok(status) => {
                        captures.insert(id.clone(), status);
                    }
                    Err(error) => {
                        failures.push(format!("{id}: {error:#}"));
                        // 一度保存済みの画像は、再検査失敗後もSSD原本として扱う。
                        // 初回コピー失敗と区別して、破損をMacのコピーで隠さない。
                        let committed = if row["archiveStatus"]["archiveId"] == config.archive_id {
                            row["archiveStatus"]["record"].clone()
                        } else { Value::Null };
                        captures.insert(
                            id.clone(),
                            json!({"state":"error","archiveId":config.archive_id,"record":committed}),
                        );
                    }
                }
            }
            Ok(())
        })();
        if let Err(error) = &result {
            failures.push(format!("{error:#}"));
        }
        let state = if failures.is_empty() {
            "ready"
        } else if !config.root.is_dir() {
            "offline"
        } else {
            "error"
        };
        let mut status = json!({"root":config.root,"archiveId":config.archive_id,"state":state,
            "poems":snapshots.len(),"captures":captures.values().filter(|s| s["state"] == "saved").count(),
            "lastError":failures.first(),
            "detail":if failures.is_empty(){"研究記録を保全しました。"}else{"保全できていない記録があります。保存先の接続と空き容量を確認してください。"}});
        {
            let _state = self.write_lock()?;
            let mut data = self.read()?;
            let before = data.clone();
            if data["revision"] != source["revision"]
                || data["photoRevision"] != source["photoRevision"]
            {
                failures.push("research_source_changed".into());
                status["state"] = "pending".into();
                status["detail"] = "保全中に加わった変更を、次に保全します。".into();
            }
            let old_time = data["researchStorage"]["lastSync"].clone();
            status["lastSync"] = old_time;
            for (id, saved) in captures {
                data["minecraftPhotos"][&id]["archiveStatus"] = saved;
            }
            for (id, saved) in snapshots {
                if !data["researchSnapshots"].is_object() {
                    data["researchSnapshots"] = json!({});
                }
                data["researchSnapshots"][id] = saved;
            }
            data["researchStorage"] = status.clone();
            if data != before {
                if failures.is_empty() {
                    status["lastSync"] = json!(Utc::now().to_rfc3339());
                }
                data["researchStorage"] = status.clone();
                // 保全状態の更新だけで通常写真編集のCASを失効させない。
                trash::atomic(
                    &self.memory.join("bookshelf/state.json"),
                    &serde_json::to_vec(&data)?,
                )?;
            }
        }
        ensure!(
            failures.is_empty(),
            "research_sync_failed: {}",
            failures.join("; ")
        );
        Ok(status)
    }

    /// 画像GET用の読み取り専用経路。新規保存／修復はworkerだけが行う。
    pub(super) fn archived_image(&self, row: &Value) -> Result<Vec<u8>> {
        let archive = archive::load(&self.memory)?.context("archive_not_configured")?;
        archive
            .for_poem(
                row["poemId"].as_str().context("invalid_capture_target")?,
                created(&row["researchRecord"]["originalPoem"])?,
            )?
            .read(
                row["requestId"].as_str().context("invalid_capture_id")?,
                row["sha256"].as_str().context("invalid_capture_hash")?,
            )
    }

    /// 新規撮影の検証済みbytesを、登録jobが完了する前にコピーする。失敗時もMacの登録は保持。
    pub(super) fn archive_capture_now(&self, id: &str, row: &Value, bytes: &[u8]) {
        let result = (|| -> Result<()> {
            if archive::load_config(&self.memory)?.is_none() {
                return Ok(());
            }
            let _sync = self.research_lock()?;
            let archive = archive::load(&self.memory)?.context("archive_not_configured")?;
            let receipt: crate::screenshot::Receipt =
                serde_json::from_value(row["receipt"].clone())?;
            let saved = archive
                .for_poem(
                    &receipt.poem_id,
                    created(&row["researchRecord"]["originalPoem"])?,
                )?
                .store(
                    row["requestId"].as_str().context("invalid_capture_id")?,
                    &receipt,
                    &row["researchRecord"],
                    bytes,
                )?;
            let _state = self.write_lock()?;
            let mut data = self.read()?;
            ensure!(
                data["minecraftPhotos"][id]["receipt"] == row["receipt"],
                "capture_conflict"
            );
            data["minecraftPhotos"][id]["archiveStatus"] =
                json!({"state":"saved","archiveId":archive.config().archive_id,"record":saved});
            trash::atomic(
                &self.memory.join("bookshelf/state.json"),
                &serde_json::to_vec(&data)?,
            )?;
            Ok(())
        })();
        if let Err(error) = result {
            tracing::warn!(event="research_capture_pending",photo_id=id,error=%error);
        }
    }
    pub(super) fn protect_research_captures(&self) -> Result<()> {
        if !self.memory.join("bookshelf/state.json").exists() {
            return Ok(());
        }
        let _lock = self.write_lock()?;
        let mut data = self.read()?;
        let before = data.clone();
        data["researchOriginals"] = originals(&data);
        let ids: Vec<String> = data["minecraftPhotos"]
            .as_object()
            .into_iter()
            .flat_map(|rows| rows.keys().cloned())
            .collect();
        for id in ids {
            if data["minecraftPhotos"][&id].get("researchRecord").is_none() {
                let photo = data["minecraftPhotos"][&id].clone();
                let poem_id = photo["poemId"].as_str().context("invalid_capture_target")?;
                let original = match trash::resolve(&self.memory, poem_id) {
                    Ok((dir, entry_id)) => {
                        let _poem_lock = trash::lock(&dir)?;
                        trash::raw_rows(&dir, "haiku_entries.jsonl")?
                            .into_iter()
                            .find(|row| row["id"] == entry_id)
                            .unwrap_or(Value::Null)
                    }
                    Err(error) if error.to_string() == "unknown_poem" => Value::Null,
                    Err(error) => return Err(error),
                };
                data["minecraftPhotos"][&id]["researchRecord"] = provenance(&photo, original);
            }
            // 過去の手動削除からも保護へ移行する。元のreceipt・PNG・タイトルは変えない。
            if let Some(trash) = data["photoTrash"].as_object_mut() {
                trash.remove(&id);
            }
        }
        if data != before {
            self.save_photos(&mut data)?;
        }
        Ok(())
    }

    pub(super) fn validate_original_slots(
        &self,
        data: &Value,
        target: &str,
        slots: &[Value],
    ) -> Result<()> {
        let first = originals(data);
        let Some(id) = first[target].as_str() else {
            return Ok(());
        };
        if let Some(previous) = data["placements"][target].as_array() {
            for (index, value) in previous.iter().enumerate() {
                if value == id {
                    ensure!(
                        slots.get(index).is_some_and(|next| next == id),
                        "research_original_protected"
                    );
                }
            }
        }
        Ok(())
    }
}
