//! 既存の撮影結果を登録し、初回画像の出自を通常の貼付けとは独立して固定する。
use super::store::Store;
use crate::{poem_trash as trash, screenshot::Receipt};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs, io::Read};

impl Store {
    /// 開いた同一ファイルから検証と配信を行う。標準名・固定ルート・内容hashを照合する。
    pub(super) fn minecraft_bytes(&self, filename: &str, expected: &str) -> Result<Vec<u8>> {
        ensure!(
            crate::screenshot::valid_filename(filename),
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
            options.custom_flags(libc::O_NOFOLLOW);
        }
        let path = self.screenshots.join(filename);
        trash::regular(&path)?;
        let mut file = options.open(&path)?;
        let meta = file.metadata()?;
        ensure!(
            meta.is_file() && meta.len() <= 64 * 1024 * 1024,
            "invalid_image"
        );
        let mut bytes = Vec::new();
        (&mut file)
            .take(64 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        ensure!(bytes.len() <= 64 * 1024 * 1024, "invalid_image");
        dimensions(&bytes)?;
        ensure!(
            format!("{:x}", Sha256::digest(&bytes)) == expected,
            "photo_file_changed"
        );
        Ok(bytes)
    }
    /// 状態→対象句の固定ロック順で、削除と自動貼付けの競合を直列化する。
    /// 登録の再送は元のreceiptを照合し、原本画像・初回対応・貼付けを変更しない。
    pub fn register_capture(&self, command: &str, receipt: &Receipt) -> Result<()> {
        let uuid = command
            .strip_prefix("shot_")
            .context("invalid_capture_id")?;
        uuid::Uuid::parse_str(uuid).context("invalid_capture_id")?;
        ensure!(
            receipt
                .poem_id
                .starts_with(&format!("session:{}:", receipt.session_id)),
            "invalid_capture_target"
        );
        ensure!(
            receipt.sha256.len() == 64 && receipt.sha256.bytes().all(|c| c.is_ascii_hexdigit()),
            "invalid_capture_hash"
        );
        let id = format!("minecraft-{command}");
        let _state_lock = self.write_lock()?;
        let mut d = self.read()?;
        if let Some(existing) = d["minecraftPhotos"].get(&id) {
            ensure!(existing["receipt"] == json!(receipt), "capture_conflict");
            return Ok(());
        }
        let (root, poem_id) = trash::resolve(&self.memory, &receipt.poem_id)?;
        let _poem_lock = trash::lock(&root)?;
        let original_poem = trash::raw_rows(&root, "haiku_entries.jsonl")?
            .into_iter().find(|p| p["id"] == poem_id);
        ensure!(
            !trash::is_deleted(&root, &poem_id)? && original_poem.is_some(),
            "capture_target_missing"
        );
        ensure!(
            fs::canonicalize(&receipt.directory)? == fs::canonicalize(&self.screenshots)?,
            "capture_directory_mismatch"
        );
        let bytes = self.minecraft_bytes(&receipt.filename, &receipt.sha256)?;
        let (width, height) = dimensions(&bytes)?;
        d["researchOriginals"] = super::research_storage::originals(&d);
        let has_prior = d["minecraftPhotos"].as_object().into_iter().flatten()
            .any(|(_, photo)| photo["poemId"] == receipt.poem_id);
        if !d["minecraftPhotos"].is_object() {
            d["minecraftPhotos"] = json!({});
        }
        d["minecraftPhotos"][&id] = json!({"receipt":receipt,"id":id,"source":"minecraft",
            "filename":receipt.filename,"sha256":receipt.sha256,"createdAt":receipt.captured_at,
            "capturedAt":receipt.captured_at,"poemId":receipt.poem_id,"requestId":command,
            "width":width,"height":height,"kind":"haiku","title":"",
            "url":format!("/api/photos/{id}/image")});
        d["minecraftPhotos"][&id]["researchRecord"] = super::research_storage::provenance(
            &d["minecraftPhotos"][&id], original_poem.unwrap());
        let target = format!("poems:{}", receipt.poem_id);
        if !has_prior {
            d["researchOriginals"][&target] = json!(id);
        }
        if d["placements"].get(&target).is_none() {
            d["placements"][&target] = json!([null, null, null]);
        }
        let slots = d["placements"][&target]
            .as_array_mut()
            .context("invalid_placements")?;
        ensure!(slots.len() == 3, "invalid_slots");
        // 満杯なら写真一覧への登録のみ。ごみ箱内の参照も空枠とは扱わない。
        if let Some(slot) = slots.iter_mut().find(|s| s.is_null()) {
            *slot = json!(id);
        }
        self.save_photos(&mut d)?;
        let row = d["minecraftPhotos"][&id].clone();
        drop(_poem_lock);
        drop(_state_lock);
        self.archive_capture_now(&id, &row, &bytes);
        Ok(())
    }
    pub(super) fn minecraft_rows(&self, d: &Value) -> Vec<Value> {
        d["minecraftPhotos"]
            .as_object()
            .into_iter()
            .flat_map(|m| m.values())
            .map(|row| {
                let mut row = row.clone();
                row.as_object_mut().unwrap().remove("receipt");
                row.as_object_mut().unwrap().remove("researchRecord");
                row["protected"] = true.into();
                row
            })
            .collect()
    }
    pub fn image_bytes(&self, id: &str) -> Result<Vec<u8>> {
        let d = self.read()?;
        ensure!(super::research_storage::protected(&d, id) || d["photoTrash"].get(id).is_none(), "photo_deleted");
        if let Some(row) = d["minecraftPhotos"].get(id) {
            if let Some(config) = super::research_archive::load_config(&self.memory)?
                && row["archiveStatus"]["archiveId"] == config.archive_id
                && (row["archiveStatus"]["state"] == "saved"
                    || row["archiveStatus"]["record"]["archive_id"] == config.archive_id) {
                // 設定済み保存先を優先する。切断・破損をMac画像の成功で隠さない。
                return self.archived_image(row);
            }
            return self.minecraft_bytes(
                row["filename"].as_str().context("invalid_filename")?,
                row["sha256"].as_str().context("invalid_capture_hash")?,
            ).or_else(|_| self.archived_image(row));
        }
        if id.starts_with("f2-") {
            return self.player_photo_image(&d, id);
        }
        Ok(fs::read(self.image(id)?)?)
    }
}
pub(super) fn dimensions(bytes: &[u8]) -> Result<(u32, u32)> {
    ensure!(
        bytes.len() >= 24 && &bytes[..8] == b"\x89PNG\r\n\x1a\n" && &bytes[12..16] == b"IHDR",
        "invalid_image"
    );
    let w = u32::from_be_bytes(bytes[16..20].try_into()?);
    let h = u32::from_be_bytes(bytes[20..24].try_into()?);
    ensure!(w > 0 && h > 0 && w <= 16384 && h <= 16384, "invalid_image");
    Ok((w, h))
}

/// 入力・画像・対象の不整合は再撮影しない。OSの一時I/Oエラーだけ再送を待つ。
pub fn terminal_error(error: &anyhow::Error) -> bool {
    !error
        .chain()
        .any(|e| e.downcast_ref::<std::io::Error>().is_some())
}
