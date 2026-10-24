//! 句に添付された採取記録を本棚・研究保全へ渡す。生成や句の変更は行わない。
use super::{research_archive as archive, store::Store};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};

pub(super) fn archive_requests(
    store: &Store, poem: &archive::PoemArchive, record: &Value, previous: &Value,
    archive_id: &str, failures: &mut Vec<String>,
) -> Value {
    let mut results = Vec::new();
    for request in record["original"]["internal_capture"]["requests"].as_array().into_iter().flatten() {
        let old = previous["internalCaptureStorage"]["requests"].as_array().into_iter().flatten()
            .find(|r| r["request_index"] == request["request_index"] && r["response_id"] == request["response_id"]);
        let prior = old.map(|r| &r["receipt"]).unwrap_or(&Value::Null);
        let mut status = json!({"request_index":request["request_index"],"response_id":request["response_id"],
            "state":"unavailable","receipt":prior});
        if request["artifact"].is_object() || !prior.is_null() {
            match poem.store_internal_capture(store.capture_dir.as_deref(), request, prior) {
                Ok(receipt) => { status["state"] = "saved".into(); status["receipt"] = receipt; }
                Err(error) => {
                    status["state"] = "error".into();
                    status["error"] = json!(error.to_string());
                    failures.push(format!("{} capture {}: {error:#}", record["id"], request["request_index"]));
                }
            }
        }
        results.push(status);
    }
    json!({"archive_id":archive_id,"requests":results})
}

impl Store {
    /// 句ID・要求順・固定ファイル種だけを受け付ける。GETは同期・修復を行わない。
    pub(super) fn internal_capture_file(&self, id: &str, index: usize, kind: &str) -> Result<archive::CaptureFile> {
        ensure!(matches!(kind, "manifest" | "hidden"), "capture_file_kind_invalid");
        let view = self.library_view()?;
        let poem = view["poems"].as_array().context("missing_poems")?.iter()
            .find(|p| p["id"] == id).context("unknown_poem")?;
        let matches: Vec<_> = poem["internalCapture"]["requests"].as_array().into_iter().flatten()
            .filter(|r| r["request_index"].as_u64() == Some(index as u64)).collect();
        ensure!(matches.len() == 1, "capture_request_not_unique");
        let request = matches[0];
        let data = self.read()?;
        if let Some(config) = archive::load_config(&self.memory)? {
            let head = &data["researchSnapshots"][id];
            if head["archive_id"] == config.archive_id {
                let archive = archive::open(config)?;
                let head: archive::ArchivedSnapshot = serde_json::from_value(head.clone())?;
                let saved = archive.read_snapshot(&head)?;
                if let Some(status) = saved["internalCaptureStorage"]["requests"].as_array().into_iter().flatten()
                    .find(|r| r["request_index"] == request["request_index"] && r["response_id"] == request["response_id"])
                    .filter(|r| !r["receipt"].is_null()) {
                    let at = chrono::DateTime::parse_from_rfc3339(saved["original"]["created_at"].as_str()
                        .context("archive_missing_original_date")?)?.with_timezone(&chrono::Utc);
                    return archive.for_poem(id, at)?.internal_capture_file(request, &status["receipt"], kind);
                }
            }
        }
        archive::local_capture_file(self.capture_dir.as_deref().context("capture_directory_unconfigured")?, request, kind)
    }
}
