//! 句に添付された採取記録を本棚・研究保全へ渡す。生成や句の変更は行わない。
use super::{research_archive as archive, store::Store};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::io::{Read, Seek, SeekFrom};

#[derive(Default, serde::Deserialize)]
#[serde(default, deny_unknown_fields)]
pub(super) struct ValuesQuery {
    pub position: u64,
    pub layer: u64,
    pub offset: u64,
}

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
    /// 一位置・一層から最大64次元だけを読む。元のfloat32値をJSONへ渡す。
    pub(super) fn internal_capture_values(&self, id: &str, index: usize, query: ValuesQuery) -> Result<Value> {
        let mut captured = self.internal_capture_file(id, index, "hidden")?;
        let manifest = &captured.manifest;
        let [positions, layers, width]: [u64; 3] = serde_json::from_value(manifest["shape"].clone())?;
        ensure!(query.position < positions && query.layer < layers && query.offset < width,
            "capture_values_out_of_bounds");
        let count = (width - query.offset).min(64) as usize;
        let start = query.position.checked_mul(layers).and_then(|n| n.checked_add(query.layer))
            .and_then(|n| n.checked_mul(width)).and_then(|n| n.checked_add(query.offset))
            .and_then(|n| n.checked_mul(4)).context("capture_shape_overflow")?;
        ensure!(start.checked_add((count * 4) as u64).is_some_and(|n| n <= captured.bytes),
            "capture_values_out_of_bounds");
        captured.file.seek(SeekFrom::Start(start))?;
        let mut bytes = vec![0u8; count * 4];
        captured.file.read_exact(&mut bytes)?;
        let values: Vec<f32> = bytes.as_chunks::<4>().0.iter().map(|b| f32::from_le_bytes(*b)).collect();
        ensure!(values.iter().all(|v| v.is_finite()), "capture_nonfinite_hidden");
        let position = manifest["positions"].as_array().and_then(|p| p.get(query.position as usize))
            .context("capture_position_missing")?;
        ensure!(position["generation_index"].as_u64() == Some(query.position), "capture_token_position_mismatch");
        Ok(json!({"response_id":manifest["response_id"],"shape":[positions,layers,width],
            "position":query.position,"layer":query.layer,"offset":query.offset,
            "input_position":position["input_position"],"predicted_token_id":position["predicted_token_id"],
            "values":values}))
    }

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
