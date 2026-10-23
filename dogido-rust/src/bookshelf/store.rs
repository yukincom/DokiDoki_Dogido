//! 本棚専用の投影と個人メモ。句・読み訂正の正本を複製しない。
use crate::poem_trash as trash;
use crate::haiku_record::verse::record_surfaces as lines;
use anyhow::{Context, Result, ensure};
use chrono::{DateTime, Duration, Utc};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
};
#[derive(Clone)]
pub struct Store {
    pub memory: PathBuf,
    pub photos: PathBuf,
    pub screenshots: PathBuf,
}
impl Store {
    fn path(&self) -> PathBuf {
        self.memory.join("bookshelf/state.json")
    }
    pub(super) fn read(&self) -> Result<Value> {
        let value = trash::read_json(
            &self.path(),
            json!({"revision":0,"photoRevision":0,"catalog":{},"poems":{},"photoTitles":{},"placements":{},"photoTrash":{}}),
        )?;
        ensure!(
            value["poems"]
                .as_object()
                .is_some_and(
                    |poems| poems
                        .values()
                        .all(|row| row.as_object().is_some_and(|fields| fields
                            .keys()
                            .all(|key| matches!(key.as_str(), "favorite" | "note"))))
                ),
            "invalid_poem_metadata"
        );
        Ok(value)
    }
    pub(super) fn write_lock(&self) -> Result<fs::File> {
        let dir = self.memory.join("bookshelf");
        fs::create_dir_all(&dir)?;
        ensure!(
            !fs::symlink_metadata(&dir)?.file_type().is_symlink(),
            "unsafe_directory"
        );
        let path = dir.join(".state.lock");
        trash::regular(&path)?;
        crate::haiku_record::locked_file(&path)
    }
    fn save(&self, data: &mut Value) -> Result<()> {
        data["revision"] = (data["revision"].as_u64().context("invalid_revision")? + 1).into();
        trash::atomic(&self.path(), &serde_json::to_vec(data)?)
    }
    pub(super) fn save_photos(&self, data: &mut Value) -> Result<()> {
        data["photoRevision"] = (data["photoRevision"]
            .as_u64()
            .context("invalid_photo_revision")?
            + 1)
        .into();
        trash::atomic(&self.path(), &serde_json::to_vec(data)?)
    }
    pub fn player(&self) -> Result<Value> {
        let d = self.read()?;
        Ok(json!({"revision":d["revision"],"catalog":d["catalog"],"poems":d["poems"]}))
    }
    pub fn edit(&self, value: &Value) -> Result<Value> {
        let _lock = self.write_lock()?;
        let mut d = self.read()?;
        ensure!(value["revision"] == d["revision"], "changed");
        let section = value["section"].as_str().context("invalid_section")?;
        let key = value["key"].as_str().context("invalid_key")?;
        let v = &value["value"];
        if section == "catalog" {
            ensure!(
                crate::catalog_editor::entries()
                    .iter()
                    .any(|r| r["id"] == key),
                "unknown_entry"
            );
            let keys = v["keys"].as_array().context("invalid_keys")?;
            ensure!(
                keys.len() <= 10
                    && keys.iter().all(|k| k
                        .as_str()
                        .is_some_and(|s| !s.trim().is_empty() && s.chars().count() <= 120)),
                "invalid_keys"
            );
            ensure!(
                v["readings"].as_array().is_some_and(|r| r.is_empty()),
                "use_reading_editor"
            );
            d[section][key] = json!({"keys":keys,"readings":[]});
        } else {
            ensure!(
                section == "poems" && poems(&self.memory, false)?.iter().any(|p| p["id"] == key),
                "unknown_poem"
            );
            ensure!(
                v.as_object().is_some_and(|v| v.len() == 2
                    && v.contains_key("favorite")
                    && v.contains_key("note"))
                    && v["favorite"].is_boolean()
                    && v["note"]
                        .as_str()
                        .is_some_and(|s| s.chars().count() <= 4000),
                "invalid_note"
            );
            d[section][key] = json!({"favorite":v["favorite"],"note":v["note"]});
        }
        self.save(&mut d)?;
        Ok(json!({"revision":d["revision"],"catalog":d["catalog"],"poems":d["poems"]}))
    }
    fn photo_rows(&self, d: &Value) -> Result<Vec<Value>> {
        self.check_photo_directory()?;
        let mut result = self.minecraft_rows(d);
        let entries = match fs::read_dir(&self.photos) {
            Ok(e) => e,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                result.sort_by(|a, b| b["filename"].as_str().cmp(&a["filename"].as_str()));
                return Ok(result);
            }
            Err(e) => return Err(e.into()),
        };
        let names = regex::Regex::new(r"^[0-9]{8}_[0-9]{2,}\.png$")?;
        for entry in entries {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if !entry.file_type()?.is_file() || !names.is_match(&name) {
                continue;
            }
            // 専用フォルダの管理名PNGだけ読む。他の写真・外部リンクは採用しない。
            use std::io::Read;
            let mut header = [0u8; 24];
            if fs::File::open(entry.path())?
                .read_exact(&mut header)
                .is_err()
                || &header[..8] != b"\x89PNG\r\n\x1a\n"
                || &header[12..16] != b"IHDR"
            {
                continue;
            }
            let width = u32::from_be_bytes(header[16..20].try_into()?);
            let height = u32::from_be_bytes(header[20..24].try_into()?);
            if width == 0 || height == 0 || width > 16384 || height > 16384 {
                continue;
            }
            let id = name.trim_end_matches(".png");
            let created: DateTime<Utc> = entry.metadata()?.modified()?.into();
            result.push(json!({"id":id,"source":"library","filename":name,"title":"","createdAt":created.to_rfc3339(),"kind":"unknown","width":width,"height":height,"url":format!("/api/photos/{id}/image")}));
        }
        result.sort_by(|a, b| b["filename"].as_str().cmp(&a["filename"].as_str()));
        Ok(result)
    }
    fn photo_snapshot(&self, d: &Value) -> Result<Value> {
        let mut items = self.photo_rows(d)?;
        items.retain(|p| d["photoTrash"].get(p["id"].as_str().unwrap()).is_none());
        for p in &mut items {
            p["title"] = d["photoTitles"][p["id"].as_str().unwrap()]
                .as_str()
                .unwrap_or("")
                .into();
        }
        let mut placements = d["placements"].clone();
        if let Some(map) = placements.as_object_mut() {
            for slots in map.values_mut() {
                for id in slots.as_array_mut().context("invalid_placements")? {
                    if !items.iter().any(|p| p["id"] == *id) {
                        *id = Value::Null;
                    }
                }
            }
        }
        Ok(json!({"revision":d["photoRevision"],"items":items,"placements":placements}))
    }
    pub fn photo_snapshot_now(&self) -> Result<Value> {
        self.photo_snapshot(&self.read()?)
    }
    pub fn image(&self, id: &str) -> Result<PathBuf> {
        ensure!(
            self.read()?["photoTrash"].get(id).is_none(),
            "photo_deleted"
        );
        let rows = self.photo_rows(&self.read()?)?;
        let row = rows
            .iter()
            .find(|r| r["id"] == id)
            .context("unknown_photo")?;
        let path = self.photos.join(row["filename"].as_str().unwrap());
        trash::regular(&path)?;
        Ok(path)
    }
    pub fn photo_edit(&self, edit: &Value) -> Result<Value> {
        let _lock = self.write_lock()?;
        let mut d = self.read()?;
        ensure!(edit["revision"] == d["photoRevision"], "changed");
        let snapshot = self.photo_snapshot(&d)?;
        let ids = snapshot["items"].as_array().unwrap();
        let known = |id: &Value| ids.iter().any(|p| p["id"] == *id);
        let poems = poems(&self.memory, false)?;
        for (id, title) in edit["titles"].as_object().context("invalid_titles")? {
            ensure!(
                known(&json!(id)) && title.as_str().is_some_and(|s| s.chars().count() <= 200),
                "invalid_title"
            );
            d["photoTitles"][id] = title.clone();
        }
        for (target, slots) in edit["placements"]
            .as_object()
            .context("invalid_placements")?
        {
            let valid_target = target
                .strip_prefix("poems:")
                .is_some_and(|id| poems.iter().any(|p| p["id"] == id))
                || target.strip_prefix("catalog:").is_some_and(|id| {
                    crate::catalog_editor::entries()
                        .iter()
                        .any(|e| e["id"] == id)
                });
            let slots = slots.as_array().context("invalid_slots")?;
            ensure!(
                valid_target
                    && slots.len() == 3
                    && slots.iter().all(|id| id.is_null() || known(id)),
                "invalid_slots"
            );
            let selected: Vec<_> = slots.iter().filter(|v| !v.is_null()).collect();
            ensure!(
                selected
                    .iter()
                    .enumerate()
                    .all(|(i, v)| !selected[..i].contains(v)),
                "duplicate_photo"
            );
            // 非表示の削除写真はnullとして届く。同じページの別枠の編集では失わない。
            let previous = &d["placements"][target];
            let merged: Vec<Value> = slots
                .iter()
                .enumerate()
                .map(|(i, next)| {
                    let old = &previous[i];
                    if next.is_null()
                        && old
                            .as_str()
                            .is_some_and(|id| d["photoTrash"].get(id).is_some())
                    {
                        old.clone()
                    } else {
                        next.clone()
                    }
                })
                .collect();
            d["placements"][target] = json!(merged);
        }
        self.save_photos(&mut d)?;
        self.photo_snapshot(&d)
    }
    pub fn photo_change(&self, id: &str, restore: bool, now: DateTime<Utc>) -> Result<()> {
        let _lock = self.write_lock()?;
        let mut d = self.read()?;
        if restore {
            let row = d["photoTrash"].get(id).context("not_in_trash")?;
            let at =
                DateTime::parse_from_rfc3339(row["expiresAt"].as_str().context("trash_time")?)?;
            ensure!(now < at && row["purged"] != true, "recovery_expired");
            ensure!(
                self.photo_rows(&d)?.iter().any(|p| p["id"] == id),
                "photo_file_missing"
            );
            if row["source"] == "minecraft" {
                self.minecraft_bytes(
                    row["filename"].as_str().context("invalid_filename")?,
                    row["sha256"].as_str().context("invalid_capture_hash")?,
                )?;
            } else {
                ensure!(
                    hash(
                        &self
                            .photos
                            .join(row["filename"].as_str().context("invalid_filename")?)
                    )? == row["sha256"],
                    "photo_file_changed"
                );
            }
            d["photoTrash"].as_object_mut().unwrap().remove(id);
        } else if d["photoTrash"].get(id).is_none() {
            let rows = self.photo_rows(&d)?;
            let row = rows
                .iter()
                .find(|p| p["id"] == id)
                .context("unknown_photo")?;
            let path = self.photos.join(row["filename"].as_str().unwrap());
            let sha = if row["source"] == "minecraft" {
                row["sha256"]
                    .as_str()
                    .context("invalid_capture_hash")?
                    .to_owned()
            } else {
                hash(&path)?
            };
            d["photoTrash"][id] = json!({"deletedAt":now.to_rfc3339(),"expiresAt":(now+Duration::days(trash::RETENTION_DAYS)).to_rfc3339(),"filename":row["filename"],"source":row["source"],"sha256":sha,"purged":false});
        }
        self.save_photos(&mut d)
    }
    fn check_photo_directory(&self) -> Result<()> {
        match fs::symlink_metadata(&self.photos) {
            Ok(m) => ensure!(
                m.is_dir() && !m.file_type().is_symlink(),
                "unsafe_directory"
            ),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e.into()),
        }
        Ok(())
    }
    pub fn purge(&self, now: DateTime<Utc>) -> Result<()> {
        trash::purge(&self.memory, now)?;
        if !self.path().exists() {
            return Ok(());
        }
        let _lock = self.write_lock()?;
        let mut d = self.read()?;
        let mut changed = false;
        let mut notes_changed = false;
        // 期限後は句の個人メモと貼り付け参照も除く。写真そのものは別の削除操作で管理する。
        for dir in crate::poem_book::directories(&self.memory)? {
            for (id, row) in trash::journal(&dir)? {
                if row["purged"] != true {
                    continue;
                }
                let key = format!("{}:{id}", trash::source(&self.memory, &dir));
                notes_changed |= d["poems"]
                    .as_object_mut()
                    .context("invalid_notes")?
                    .remove(&key)
                    .is_some();
                changed |= d["placements"]
                    .as_object_mut()
                    .context("invalid_placements")?
                    .remove(&format!("poems:{key}"))
                    .is_some();
            }
        }
        let ids: Vec<_> = d["photoTrash"]
            .as_object()
            .context("invalid_trash")?
            .keys()
            .cloned()
            .collect();
        if !ids.is_empty() {
            self.check_photo_directory()?;
        }
        for id in ids {
            let r = &d["photoTrash"][&id];
            let at = DateTime::parse_from_rfc3339(r["expiresAt"].as_str().context("trash_time")?)?;
            if now < at || r["purged"] == true {
                continue;
            }
            let filename = r["filename"].as_str().context("invalid_filename")?;
            // Minecraft所有の原本には触れない。本棚の登録・タイトル・貼付けだけを消す。
            if r["source"] != "minecraft" {
                ensure!(
                    filename == format!("{id}.png")
                        && !filename.contains(['/', '\\'])
                        && !id.starts_with('.'),
                    "unsafe_filename"
                );
                let path = self.photos.join(filename);
                trash::regular(&path)?;
                if path.exists() {
                    ensure!(hash(&path)? == r["sha256"], "photo_file_changed");
                    fs::remove_file(path)?;
                }
            }
            d["photoTrash"][&id]["purged"] = true.into();
            d["photoTitles"].as_object_mut().unwrap().remove(&id);
            for slots in d["placements"].as_object_mut().unwrap().values_mut() {
                for v in slots.as_array_mut().context("invalid_placements")? {
                    if *v == id {
                        *v = Value::Null;
                    }
                }
            }
            changed = true;
        }
        if notes_changed {
            d["revision"] = (d["revision"].as_u64().context("invalid_revision")? + 1).into();
        }
        if changed {
            self.save_photos(&mut d)?;
        } else if notes_changed {
            trash::atomic(&self.path(), &serde_json::to_vec(&d)?)?;
        }
        Ok(())
    }
    pub fn trash(&self, now: DateTime<Utc>) -> Result<Value> {
        let mut out = vec![];
        for p in poems(&self.memory, true)? {
            if p["expiresAt"]
                .as_str()
                .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
                .is_some_and(|t| now < t)
            {
                out.push(p);
            }
        }
        let d = self.read()?;
        for (id, r) in d["photoTrash"].as_object().context("invalid_trash")? {
            if r["purged"] == true {
                continue;
            }
            let at = DateTime::parse_from_rfc3339(r["expiresAt"].as_str().context("trash_time")?)?;
            if now < at {
                out.push(json!({"kind":"photo","id":id,"label":d["photoTitles"][id].as_str().filter(|s|!s.is_empty()).unwrap_or(id),"deletedAt":r["deletedAt"],"expiresAt":r["expiresAt"]}));
            }
        }
        Ok(json!({"items":out}))
    }
}
fn hash(path: &Path) -> Result<String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    trash::regular(path)?;
    let mut file = fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut chunk = [0u8; 65536];
    loop {
        let n = file.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        h.update(&chunk[..n]);
    }
    Ok(format!("{:x}", h.finalize()))
}
pub fn poems(root: &Path, deleted: bool) -> Result<Vec<Value>> {
    let mut out = vec![];
    for dir in crate::poem_book::directories(root)? {
        let journal = trash::journal(&dir)?;
        let revisions = trash::raw_rows(&dir, "haiku_revisions.jsonl")?;
        let dialogue = super::research::dialogue(&dir)?;
        for row in trash::raw_rows(&dir, "haiku_entries.jsonl")? {
            let id = row["id"].as_str().context("poem_id_missing")?;
            let deletion = journal.get(id);
            if deletion.is_some() != deleted {
                continue;
            }
            let history: Vec<_> = revisions.iter().filter(|r| r["haiku_id"] == id).collect();
            let latest = history.last().copied().unwrap_or(&row);
            let created = row["created_at"].as_str().unwrap_or("");
            let biome = row["world"]["biome"].as_str().map(|s| {
                if s.contains(':') {
                    s.into()
                } else {
                    format!("minecraft:{s}")
                }
            });
            // 句のきっかけとして伝えた見どころを元の発句記録から読む。
            // prefaceは朗読の合図、interpretationは未発話の説明も含むため代用しない。
            let preface = row["materials_snapshot"]["preface_spoken"]
                .as_str()
                .filter(|text| !text.trim().is_empty());
            let mut item = json!({"id":format!("{}:{id}",trash::source(root,&dir)),"kind":"poem","lines":lines(latest),"author":if row["author"]=="player"{"プレイヤー"}else{"ドギド"},"date":created.get(..10).unwrap_or(""),"createdAt":created,"biomeId":biome,"timeOfDay":row["world"]["time_phase"],"preface":preface,"revised":!history.is_empty(),"history":history,"catalog":null,"label":lines(latest).join(" / ")});
            item["originalLines"] = json!(lines(&row));
            item["original"] = row.clone();
            item["current"] = latest.clone();
            item["materials"] = row["materials_snapshot"].clone();
            item["dialogue"] = json!(dialogue.get(id).cloned().unwrap_or_default());
            item["recordStatus"] = json!({"materials":if row["materials_snapshot"].is_object(){"recorded"}else{"missing"},
                "dialogue":if dialogue.contains_key(id){"recorded"}else{"not_found"}});
            if let Some(d) = deletion {
                item["deletedAt"] = d["deletedAt"].clone();
                item["expiresAt"] = d["expiresAt"].clone();
            }
            out.push(item);
        }
    }
    out.sort_by(|a, b| b["createdAt"].as_str().cmp(&a["createdAt"].as_str()));
    Ok(out)
}
pub fn catalog() -> Vec<Value> {
    let w = crate::world_catalog::catalog();
    let c = crate::chat_catalog::catalog();
    crate::catalog_editor::entries()
        .iter()
        .map(|e| {
            let category = e["kind"].as_str().unwrap();
            let id = e["id"].as_str().unwrap().split_once(':').unwrap().1;
            let raw = match category {
                "mob" => c.all_mob_entries().get(id),
                "structure" => c.structure_entries().get(id),
                "biome" => w.biome_entries().get(id),
                "item" => w.item_entry(id),
                "block" => w.block_entry(id),
                _ => None,
            };
            let mut raw = raw.cloned().unwrap_or(json!({}));
            if !raw["label"].is_string() {
                raw["label"] = e["surface"].clone();
            }
            json!({"id":e["id"],"category":category,"catalogId":id,"raw":raw})
        })
        .collect()
}
