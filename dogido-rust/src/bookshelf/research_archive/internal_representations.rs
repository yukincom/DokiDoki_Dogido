//! 発句entryの検証済み採取物だけを既存の一句フォルダへ保全する。
//! パスは固定名から組み立て、entryやmanifestに書かれたパスは開かない。
use super::*;
use crate::haiku_capture::RequestObservation;
use std::{fs::File, io::{Read, Seek, Write}};

pub struct CaptureFile {
    pub file: File,
    pub bytes: u64,
    pub name: &'static str,
    pub manifest: Value,
}

fn request(value: &Value) -> Result<RequestObservation> {
    let row: RequestObservation = serde_json::from_value(value.clone())?;
    let id = row.response_id.as_deref().context("capture_response_missing")?;
    ensure!(!id.is_empty() && id.len() <= 200
        && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'),
        "invalid_capture_response_id");
    ensure!(row.request_index > 0, "capture_index_invalid");
    let a = row.artifact.as_ref().context("capture_artifact_missing")?;
    ensure!(valid_hash(&a.manifest_sha256) && row.status == a.status
        && matches!(a.status.as_str(), "complete" | "partial" | "failed" | "cancelled"),
        "capture_artifact_invalid");
    if matches!(a.status.as_str(), "complete" | "partial") {
        let shape = a.shape.context("capture_shape_missing")?;
        let bytes = shape.into_iter().try_fold(4u64, |n, d| n.checked_mul(d))
            .context("capture_shape_overflow")?;
        ensure!(a.hidden_verified && shape.iter().all(|d| *d > 0)
            && a.hidden_bytes == Some(bytes)
            && a.hidden_sha256.as_deref().is_some_and(valid_hash), "capture_hidden_unverified");
    } else {
        ensure!(!a.hidden_verified, "capture_failed_has_numbers");
    }
    Ok(row)
}

fn receipt(poem: &PoemArchive, value: &Value, row: &RequestObservation) -> Value {
    let a = row.artifact.as_ref().unwrap();
    json!({"schema_version":1,"archive_id":poem.archive.config.archive_id,
        "poem_id":poem.poem_id,"request_index":row.request_index,"response_id":row.response_id,
        "request_sha256":hash(&encoded(value).unwrap()),
        "manifest_sha256":a.manifest_sha256,
        "hidden_sha256":if a.hidden_verified {a.hidden_sha256.clone()} else {None},
        "hidden_bytes":if a.hidden_verified {a.hidden_bytes} else {None}})
}

fn verify_file(file: &mut File, bytes: u64, expected: &str) -> Result<()> {
    ensure!(valid_hash(expected), "capture_hash_invalid");
    let before = file.metadata()?;
    ensure!(before.len() == bytes, "capture_file_size_mismatch");
    file.rewind()?;
    let mut sha = Sha256::new();
    let mut remaining = bytes;
    let mut buffer = [0u8; 64 * 1024];
    while remaining > 0 {
        let count = remaining.min(buffer.len() as u64) as usize;
        file.read_exact(&mut buffer[..count])?;
        sha.update(&buffer[..count]);
        remaining -= count as u64;
    }
    let after = file.metadata()?;
    ensure!(after.len() == bytes && before.modified()? == after.modified()?
        && format!("{:x}", sha.finalize()) == expected, "capture_file_changed");
    file.rewind()?;
    Ok(())
}

#[cfg(unix)]
fn files(dir: &safe::Dir, row: &RequestObservation, open_hidden: bool, verify_hidden: bool) -> Result<(Vec<u8>, Option<File>)> {
    let a = row.artifact.as_ref().unwrap();
    let bytes = dir.read("manifest.json", MAX_RECORD)?.context("capture_manifest_missing")?;
    ensure!(hash(&bytes) == a.manifest_sha256, "capture_manifest_changed");
    let m: Value = serde_json::from_slice(&bytes)?;
    ensure!(m["schema"] == "dogido.internal_capture.v1"
        && m["response_id"].as_str() == row.response_id.as_deref()
        && m["choice_index"] == 0 && m["status"] == a.status
        && m["backend"] == a.backend && m["boundary"] == a.boundary,
        "capture_manifest_identity_mismatch");
    let hidden = if a.hidden_verified && open_hidden {
        ensure!(m["dtype"] == "float32-le" && m["hidden_file"] == "hidden.f32"
            && m["shape"] == json!(a.shape) && m["hidden_bytes"] == json!(a.hidden_bytes)
            && m["hidden_sha256"] == json!(a.hidden_sha256), "capture_manifest_layout_mismatch");
        let mut f = dir.existing_file("hidden.f32")?;
        ensure!(f.metadata()?.len() == a.hidden_bytes.unwrap(), "capture_file_size_mismatch");
        if verify_hidden { verify_file(&mut f, a.hidden_bytes.unwrap(), a.hidden_sha256.as_deref().unwrap())?; }
        Some(f)
    } else { None };
    Ok((bytes, hidden))
}

#[cfg(unix)]
fn selected(dir: &safe::Dir, row: &RequestObservation, kind: &str) -> Result<CaptureFile> {
    ensure!(matches!(kind, "manifest" | "hidden"), "capture_file_kind_invalid");
    let (manifest, hidden) = files(dir, row, kind == "hidden", kind == "hidden")?;
    let metadata = serde_json::from_slice(&manifest)?;
    if kind == "hidden" {
        Ok(CaptureFile {file:hidden.context("capture_numbers_unavailable")?,
            bytes:row.artifact.as_ref().unwrap().hidden_bytes.unwrap(), name:"hidden.f32", manifest:metadata})
    } else {
        let mut file = dir.existing_file("manifest.json")?;
        verify_file(&mut file, manifest.len() as u64, &row.artifact.as_ref().unwrap().manifest_sha256)?;
        Ok(CaptureFile {file, bytes:manifest.len() as u64, name:"manifest.json", manifest:metadata})
    }
}

pub fn local_capture_file(root: &Path, value: &Value, kind: &str) -> Result<CaptureFile> {
    let row = request(value)?;
    #[cfg(unix)]
    { selected(&safe::Dir::open(root)?.child(row.response_id.as_deref().unwrap(), false)?, &row, kind) }
    #[cfg(not(unix))]
    { let _ = (root, row, kind); anyhow::bail!("archive_platform_unsupported") }
}

impl PoemArchive {
    /// 既存完了札は元ファイルを必要としない。再同期は札・manifest・長さを検査し、
    /// 大きな配列の全hashは初回コピー時とダウンロード時に照合する。
    pub fn store_internal_capture(&self, root: Option<&Path>, value: &Value, previous: &Value) -> Result<Value> {
        let row = request(value)?;
        let wanted = receipt(self, value, &row);
        #[cfg(unix)]
        {
            let root_dir = self.archive.checked_root()?;
            let _lock = root_dir.lock(LOCK, false)?;
            let (poem, _) = self.poem_root(&root_dir, true)?;
            let parent = poem.child("internal-representations", true)?;
            let key = format!("request-{}-{}", row.request_index, wanted["request_sha256"].as_str().unwrap());
            // 保存済み札があれば欠損をローカルコピーで修復・隠蔽しない。
            let committed = !previous.is_null();
            if committed { ensure!(previous == &wanted, "capture_receipt_conflict"); }
            let bundle = parent.child(&key, !committed)?;
            if let Some(bytes) = bundle.read("commit.json", MAX_RECORD)? {
                ensure!(serde_json::from_slice::<Value>(&bytes)? == wanted, "capture_commit_conflict");
                files(&bundle, &row, true, false)?;
                return Ok(wanted);
            }
            ensure!(!committed, "capture_commit_missing");
            let source = safe::Dir::open(root.context("capture_directory_unconfigured")?)?
                .child(row.response_id.as_deref().unwrap(), false)?;
            let (manifest, hidden) = files(&source, &row, true, true)?;
            bundle.once("manifest.json", &manifest)?;
            if let Some(mut input) = hidden {
                let a = row.artifact.as_ref().unwrap();
                match bundle.existing_file("hidden.f32") {
                    Ok(mut existing) => {
                        verify_file(&mut existing, a.hidden_bytes.unwrap(), a.hidden_sha256.as_deref().unwrap())?;
                        existing.sync_all()?;
                    }
                    Err(error) if not_found(&error) => {
                        let mut output = bundle.new_file("hidden.f32")?;
                        let copied = std::io::copy(&mut Read::by_ref(&mut input).take(a.hidden_bytes.unwrap()), &mut output)?;
                        ensure!(copied == a.hidden_bytes.unwrap(), "capture_copy_incomplete");
                        output.flush()?;
                        output.sync_all()?;
                        let mut saved = bundle.existing_file("hidden.f32")?;
                        verify_file(&mut saved, copied, a.hidden_sha256.as_deref().unwrap())?;
                    }
                    Err(error) => return Err(error),
                }
            }
            bundle.sync()?;
            bundle.once("commit.json", &encoded(&wanted)?)?;
            bundle.sync()?;
            parent.sync()?;
            poem.sync()?;
            Ok(wanted)
        }
        #[cfg(not(unix))]
        { let _ = (root, previous, wanted); anyhow::bail!("archive_platform_unsupported") }
    }

    /// 読み取りのみ。保存札と原句・要求を照合してから、同じ開いたファイルを返す。
    pub fn internal_capture_file(&self, value: &Value, saved: &Value, kind: &str) -> Result<CaptureFile> {
        let row = request(value)?;
        let wanted = receipt(self, value, &row);
        ensure!(saved == &wanted, "capture_receipt_conflict");
        #[cfg(unix)]
        {
            let root = self.archive.checked_root()?;
            let (poem, _) = self.poem_root(&root, false)?;
            let bundle = poem.child("internal-representations", false)?.child(
                &format!("request-{}-{}", row.request_index, wanted["request_sha256"].as_str().unwrap()), false)?;
            let commit = bundle.read("commit.json", MAX_RECORD)?.context("capture_commit_missing")?;
            ensure!(serde_json::from_slice::<Value>(&commit)? == wanted, "capture_commit_conflict");
            selected(&bundle, &row, kind)
        }
        #[cfg(not(unix))]
        { let _ = kind; anyhow::bail!("archive_platform_unsupported") }
    }
}
