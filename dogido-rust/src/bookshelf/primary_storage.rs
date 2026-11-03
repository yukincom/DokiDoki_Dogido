//! SSDを正本に切り替えた保管庫だけで、検証済みの入力ファイルを整理する。
use super::{player_photos::rename_without_replace, research_archive as archive, store::Store};
use anyhow::{Context, Result, ensure};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{fs, path::{Path, PathBuf}};

fn holding(path: &Path) -> PathBuf {
    path.with_file_name(format!(".dogido-moved-{}", path.file_name().unwrap().to_string_lossy()))
}

fn pending(path: &Path) -> Result<bool> {
    Ok(path.try_exists()? || holding(path).try_exists()?)
}

fn digest(path: &Path) -> Result<String> {
    crate::poem_trash::regular(path)?;
    let mut file = fs::File::open(path)?;
    let mut hash = Sha256::new();
    std::io::copy(&mut file, &mut hash)?;
    Ok(format!("{:x}", hash.finalize()))
}

// 退避名は固定。移動途中の再起動でも、別内容のファイルを消さない。
fn retire(path: &Path, expected: &str) -> Result<()> {
    let temporary = holding(path);
    if !temporary.try_exists()? {
        if !path.try_exists()? { return Ok(()); }
        ensure!(digest(path)? == expected, "primary_source_changed");
        rename_without_replace(path, &temporary)?;
    }
    if digest(&temporary)? != expected {
        let _ = rename_without_replace(&temporary, path);
        anyhow::bail!("primary_source_changed");
    }
    fs::remove_file(&temporary)?;
    fs::File::open(path.parent().context("primary_source_parent")?)?.sync_all()?;
    Ok(())
}

impl Store {
    pub(super) fn retire_archived_sources(&self, archive: &archive::Archive, data: &Value) -> Result<()> {
        ensure!(archive.config().root == self.memory && archive.config().primary, "primary_archive_scope");
        for row in data["minecraftPhotos"].as_object().into_iter().flatten().map(|(_, row)| row) {
            let name = row["filename"].as_str().context("missing_capture_filename")?;
            ensure!(crate::screenshot::valid_filename(name), "invalid_capture_filename");
            let source = self.screenshots.join(name);
            if pending(&source)? {
                ensure!(self.screenshots.canonicalize()? == self.screenshots, "unsafe_screenshots_directory");
                self.archived_image(row)?;
                retire(&source, row["sha256"].as_str().context("missing_capture_hash")?)?;
            }
        }
        let Some(input) = &self.capture_dir else { return Ok(()); };
        ensure!(input == &self.memory.join("capture-inbox") && input.canonicalize()? == *input, "primary_capture_directory");
        for head in data["researchSnapshots"].as_object().into_iter().flatten().map(|(_, h)| h) {
            let head: archive::ArchivedSnapshot = serde_json::from_value(head.clone())?;
            let record = archive.read_snapshot(&head)?;
            let statuses = &record["internalCaptureStorage"]["requests"];
            for req in record["original"]["internal_capture"]["requests"].as_array().into_iter().flatten() {
                let Some(saved) = statuses.as_array().into_iter().flatten().find(|s|
                    s["request_index"] == req["request_index"] && s["response_id"] == req["response_id"]
                    && s["state"] == "saved") else { continue; };
                let response = req["response_id"].as_str().context("missing_capture_id")?;
                ensure!(!response.is_empty() && response.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_')), "invalid_capture_id");
                let source = input.join(response);
                if !source.try_exists()? { continue; }
                ensure!(source.canonicalize()? == source, "unsafe_capture_directory");
                let at = chrono::DateTime::parse_from_rfc3339(record["original"]["created_at"].as_str().context("missing_date")?)?;
                let poem = archive.for_poem(&head.poem_id, at.into())?;
                let receipt = &saved["receipt"];
                for (kind, name, field) in [("hidden", "hidden.f32", "hidden_sha256"), ("manifest", "manifest.json", "manifest_sha256")] {
                    let path = source.join(name);
                    if let Some(expected) = receipt[field].as_str() && pending(&path)? {
                        poem.internal_capture_file(req, receipt, kind)?;
                        retire(&path, expected)?;
                    }
                }
                if fs::read_dir(&source)?.next().is_none() { fs::remove_dir(&source)?; }
            }
        }
        Ok(())
    }
}
