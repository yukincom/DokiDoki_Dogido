//! 明示操作で研究保全を固定し、設定された公開範囲でGitHubへ送る。
use super::{j_lens, research_archive as archive, store::Store};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::PathBuf, process::{Command, Stdio}, time::{Duration, Instant}};
#[cfg(unix)]
use std::os::unix::process::CommandExt;

struct Worker(std::process::Child);
impl Drop for Worker {
    fn drop(&mut self) {
        if self.0.try_wait().ok().flatten().is_none() {
            #[cfg(unix)]
            unsafe { libc::kill(-(self.0.id() as i32), libc::SIGKILL); }
            let _ = self.0.kill(); let _ = self.0.wait();
        }
    }
}

pub const REPOSITORY: &str = "yukincom/DokiDoki-Dogido-data";
pub const URL: &str = "https://github.com/yukincom/DokiDoki-Dogido-data";

#[derive(Clone, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub python: PathBuf, pub worker: PathBuf, pub checkout: PathBuf,
    #[serde(default)]
    pub mode: Mode,
}

#[derive(Clone, Default, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode { #[default] Private, Public }

fn hash(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn inline(files: &mut Vec<Value>, path: &str, bytes: Vec<u8>) -> Result<Value> {
    let meta = json!({"path":path,"sha256":hash(&bytes),"bytes":bytes.len()});
    let mut file = meta.clone(); file["content"] = String::from_utf8(bytes)?.into(); files.push(file); Ok(meta)
}
fn encoded(value: &Value) -> Result<Vec<u8>> { Ok(serde_json::to_vec(value)?) }

impl Store {
    pub(super) fn data_repository_plan(&self, lens: Option<&j_lens::Config>) -> Result<Value> {
        ensure!(self.sync_research_storage()?["state"] == "ready", "data_repository_archive_unready");
        let _lock = self.research_lock()?;
        let config = archive::load_config(&self.memory)?.context("archive_not_configured")?;
        let archive = archive::open(config.clone())?;
        let state = self.read()?;
        let heads = state["researchSnapshots"].as_object().context("data_repository_empty")?;
        let mut files = Vec::new();
        let mut poems = Vec::new();
        inline(&mut files, ".dogido-research-archive.json", encoded(&json!({"schema_version":config.schema_version,"archive_id":config.archive_id,"volume_uuid":config.volume_uuid}))?)?;
        for (id, value) in heads {
            let head: archive::ArchivedSnapshot = serde_json::from_value(value.clone())?;
            ensure!(head.archive_id == config.archive_id && head.poem_id == *id, "data_repository_snapshot_scope");
            let (record, bytes) = archive.read_snapshot_with_bytes(&head)?;
            let envelope: Value = serde_json::from_slice(&bytes)?;
            let snapshot = inline(&mut files, &head.relative_path, bytes)?;
            let base = head.relative_path.split('/').take(2).collect::<Vec<_>>().join("/");
            inline(&mut files, &format!("{base}/identity.json"), encoded(&envelope["identity"])?)?;
            let at = chrono::DateTime::parse_from_rfc3339(record["original"]["created_at"].as_str().context("archive_missing_original_date")?)?.with_timezone(&chrono::Utc);
            let scoped = archive.for_poem(id, at)?;
            let mut requests = Vec::new();
            for request in record["original"]["internal_capture"]["requests"].as_array().into_iter().flatten() {
                let index = request["request_index"].as_u64().context("capture_index_invalid")? as usize;
                let mut row = json!({"request_index":index,"response_id":request["response_id"],"capture_status":request["status"]});
                let saved = record["internalCaptureStorage"]["requests"].as_array().into_iter().flatten()
                    .find(|s| s["request_index"] == index && s["response_id"] == request["response_id"]);
                if let Some(saved) = saved.filter(|s| !s["receipt"].is_null()) {
                    let receipt = &saved["receipt"];
                    let manifest = scoped.internal_capture_file(request, receipt, "manifest")?;
                    let directory = format!("{base}/internal-representations/request-{index}-{}", receipt["request_sha256"].as_str().context("capture_receipt_invalid")?);
                    for (kind, name, expected) in [("manifest", "manifest.json", &receipt["manifest_sha256"]), ("hidden", "hidden.f32", &receipt["hidden_sha256"])] {
                        if expected.is_null() { continue; }
                        let captured = if kind == "manifest" { None } else { Some(scoped.internal_capture_file(request, receipt, kind)?) };
                        let length = captured.as_ref().map_or(manifest.bytes, |f| f.bytes);
                        let path = format!("{directory}/{name}");
                        let meta = json!({"path":path,"sha256":expected,"bytes":length});
                        let mut file = meta.clone(); file["source"] = json!(config.root.join(&path)); files.push(file);
                        row[kind] = meta;
                    }
                    inline(&mut files, &format!("{directory}/commit.json"), encoded(receipt)?)?;
                    if request["artifact"]["hidden_verified"] == true {
                        let lens = lens.context("j_lens_unconfigured")?;
                        let readout = self.read_j_lens(lens, true, |kind| scoped.internal_capture_file(request, receipt, kind))?;
                        ensure!(readout["manifest_sha256"] == receipt["manifest_sha256"] && readout["hidden_sha256"] == receipt["hidden_sha256"]
                            && readout["response_id"] == request["response_id"], "data_repository_readout_scope");
                        let bytes = encoded(&readout)?;
                        let readout_hash = hash(&bytes);
                        row["j_lens"] = inline(&mut files, &format!("{base}/j-lens/{readout_hash}.json"), bytes)?;
                        row["translations"] = self.export_translations(&readout_hash, &base, &mut files)?;
                    }
                } else {
                    ensure!(!request["artifact"].is_object(), "data_repository_capture_unarchived");
                    row["availability"] = "no_saved_capture".into();
                }
                requests.push(row);
            }
            poems.push(json!({"id":id,"snapshot":snapshot,"deleted_from_bookshelf":record["deletedFromBookshelf"],"requests":requests}));
        }
        let index = json!({"schema":"dogido.research_repository.v1","archive_id":config.archive_id,"repository":URL,
            "photos_included":false,"poems":poems});
        inline(&mut files, "index.json", encoded(&index)?)?;
        Ok(json!({"repository":REPOSITORY,"files":files,"poems":poems.len(),
            "captures":poems.iter().flat_map(|p| p["requests"].as_array().unwrap()).filter(|r| r["hidden"].is_object()).count()}))
    }

    fn export_translations(&self, readout: &str, base: &str, files: &mut Vec<Value>) -> Result<Value> {
        let dir = self.memory.join("bookshelf/j-lens-translations");
        if !dir.exists() { return Ok(json!([])); }
        ensure!(!fs::symlink_metadata(&dir)?.file_type().is_symlink(), "unsafe_directory");
        let mut saved = Vec::new();
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            ensure!(entry.file_type()?.is_file(), "translation_cache_invalid");
            ensure!(entry.metadata()?.len() < 64 * 1024, "translation_cache_invalid");
            let envelope: Value = serde_json::from_slice(&fs::read(entry.path())?)?;
            let result = &envelope["result"];
            ensure!(envelope["sha256"] == hash(&encoded(result)?), "translation_cache_invalid");
            if result["source"]["readout_sha256"] != readout { continue; }
            let bytes = encoded(result)?;
            saved.push(inline(files, &format!("{base}/j-lens/translations/{}.json", hash(&bytes)), bytes)?);
        }
        saved.sort_by(|a, b| a["path"].as_str().cmp(&b["path"].as_str()));
        Ok(json!(saved))
    }
}

/// workerは固定configで起動し、認証を画面へ渡さない。HTTP待機中にstate/保全lockを保持しない。
pub(super) fn publish(store: &Store, lens: Option<&j_lens::Config>, config: &Config) -> Result<Value> {
    let mut plan = store.data_repository_plan(lens)?;
    plan["checkout"] = json!(config.checkout);
    plan["mode"] = json!(config.mode);
    let dir = store.memory.join("bookshelf");
    let input = dir.join(format!(".publish-{}.json", uuid::Uuid::new_v4()));
    let output = input.with_extension("result");
    let result = (|| -> Result<Value> {
        let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&input)?;
        file.write_all(&encoded(&plan)?)?; file.sync_all()?;
        let stdout = fs::OpenOptions::new().write(true).create_new(true).open(&output)?;
        let mut command = Command::new(&config.python);
        command.arg(&config.worker).arg("--plan").arg(&input).stdin(Stdio::null()).stdout(stdout).stderr(Stdio::null());
        #[cfg(unix)]
        command.process_group(0);
        let mut child = Worker(command.spawn()?);
        let start = Instant::now();
        let status = loop {
            if let Some(status) = child.0.try_wait()? { break status; }
            if start.elapsed() > Duration::from_secs(1800) { anyhow::bail!("data_repository_timeout"); }
            std::thread::sleep(Duration::from_millis(100));
        };
        ensure!(fs::metadata(&output)?.len() < 32 * 1024, "data_repository_output_invalid");
        let result: Value = serde_json::from_slice(&fs::read(&output)?)?;
        ensure!(status.success() && matches!(result["status"].as_str(), Some("uploaded" | "submitted" | "up_to_date")), "data_repository_failed: {}", result["detail"].as_str().unwrap_or("アップロード結果を確認できませんでした"));
        Ok(result)
    })();
    let _ = fs::remove_file(input); let _ = fs::remove_file(output);
    result
}
