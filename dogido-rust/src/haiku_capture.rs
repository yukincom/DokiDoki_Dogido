//! 自動発句の要求と、同じ応答IDでbackendが保存した数値採取物を照合する。
//! 入力は既存の生成要求・応答と明示設定された保存先。出力は発句entryに添える観測記録だけ。
//! 推論・再試行・数値ファイルの作成は行わず、採取失敗を会話失敗へ伝播させない。
//! 保存句との結合はhaiku_runtimeの現行世代確認とMemoryStoreの保存が所有する。
use crate::{
    haiku::StructuredRequest,
    types::{GeneratedText, GenerationReport, GenerationRequest},
};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
};

const MANIFEST_LIMIT: u64 = 8 * 1024 * 1024;
const SCHEMA: &str = "dogido.internal_capture.v1";
const BOUNDARY: &str = "decoder_layer_output_pre_final_norm";

/// 一件の自動発句の観測。最終句は整形・再生成を経るため、raw tokenの直接対応とは扱わない。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Observation {
    pub schema_version: u32,
    pub session_id: String,
    pub job_id: String,
    pub token_scope: String,
    pub requests: Vec<RequestObservation>,
}

/// request_indexはLiveBackendが使う1始まりの実要求順。異なる再生成をkindだけで合流しない。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct RequestObservation {
    pub request_id: String,
    pub request_index: usize,
    pub kind: String,
    pub route: String,
    pub requested_model: String,
    pub request_sha256: String,
    pub status: String,
    pub response_id: Option<String>,
    pub response_model: Option<String>,
    pub api_output: Option<GeneratedText>,
    pub artifact: Option<Artifact>,
    pub error: Option<String>,
}

/// backendファイルの相対参照と検査結果。配列はこの記録や川柳材料へ複製しない。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Artifact {
    pub manifest: String,
    pub manifest_sha256: String,
    pub backend: String,
    pub status: String,
    pub boundary: String,
    pub model: Value,
    pub identity: Option<Value>,
    pub shape: Option<[u64; 3]>,
    pub hidden_file: Option<String>,
    pub hidden_sha256: Option<String>,
    pub hidden_bytes: Option<u64>,
    pub hidden_verified: bool,
    pub api_text_matches_rendered: Option<bool>,
    pub error: Option<String>,
}

/// 設定された採取先を一件の発句だけに保持する。workshopや通常会話へは渡さない。
pub struct Capture {
    root: PathBuf,
    observation: Observation,
}

pub fn supports(kind: &str) -> bool {
    matches!(
        kind,
        "haiku_irony" | "haiku_scene" | "haiku_draft" | "haiku_line_regeneration"
    )
}

impl Capture {
    pub fn new(root: PathBuf, session_id: &str, job_id: &str) -> Self {
        Self {
            root,
            observation: Observation {
                schema_version: 1,
                session_id: session_id.into(),
                job_id: job_id.into(),
                token_scope: "raw_generation_before_domain_parse_and_reading_normalization".into(),
                requests: vec![],
            },
        }
    }

    /// 同じHTTP要求を送る直前に採取要求を記録する。promptへ識別子を混ぜない。
    pub fn begin(
        &mut self,
        index: usize,
        request: &StructuredRequest,
        input: &GenerationRequest,
    ) -> Result<usize> {
        ensure!(supports(&request.kind), "unsupported_capture_kind");
        let slot = self.observation.requests.len();
        self.observation.requests.push(RequestObservation {
            request_id: format!("{}:request:{index}", self.observation.job_id),
            request_index: index,
            kind: request.kind.clone(),
            route: request.route.clone(),
            requested_model: input.model.clone(),
            request_sha256: hash(&serde_json::to_vec(input)?),
            status: "requested".into(),
            response_id: None,
            response_model: None,
            api_output: None,
            artifact: None,
            error: None,
        });
        Ok(slot)
    }

    /// 数値ファイルの検査はblocking workerへ分離する。欠落・不整合は観測結果にだけ残す。
    /// 取消でこのfutureが破棄された場合、ファイルが完成しても次の発句に引き継がれない。
    pub async fn finish(&mut self, slot: usize, report: &GenerationReport) {
        let duplicates: Vec<_> = self
            .observation
            .requests
            .iter()
            .enumerate()
            .filter(|(index, row)| {
                *index != slot && row.response_id.as_deref() == Some(&report.response_id)
            })
            .map(|(index, _)| index)
            .collect();
        if !duplicates.is_empty() {
            for index in duplicates.into_iter().chain(std::iter::once(slot)) {
                let row = &mut self.observation.requests[index];
                row.status = "invalid".into();
                row.response_id = Some(report.response_id.clone());
                row.artifact = None;
                row.error = Some("capture_response_id_reused".into());
            }
            return;
        }
        let row = &mut self.observation.requests[slot];
        row.response_id = Some(report.response_id.clone());
        row.response_model = Some(report.response_model.clone());
        row.api_output = Some(report.generated.clone());
        let root = self.root.clone();
        let response_id = report.response_id.clone();
        let text = report.generated.text.clone();
        match tokio::task::spawn_blocking(move || inspect(&root, &response_id, &text)).await {
            Ok(Ok(artifact)) => {
                row.status = artifact.status.clone();
                row.artifact = Some(artifact);
            }
            Ok(Err(error)) => {
                row.status = if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound)
                {
                    "missing"
                } else {
                    "invalid"
                }
                .into();
                row.error = Some(error.to_string());
            }
            Err(error) => {
                row.status = "invalid".into();
                row.error = Some(error.to_string());
            }
        }
    }

    pub fn failed(&mut self, slot: usize, error: &anyhow::Error) {
        let row = &mut self.observation.requests[slot];
        row.status = "request_failed".into();
        row.error = Some(error.to_string());
    }

    pub fn into_observation(self) -> Observation {
        self.observation
    }
}

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn regular(path: &Path) -> Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path)?;
    ensure!(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        "capture_file_not_regular"
    );
    Ok(metadata)
}

fn directory(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    ensure!(
        metadata.is_dir() && !metadata.file_type().is_symlink(),
        "capture_directory_not_regular"
    );
    Ok(())
}

/// response_idとchoiceを照合してから実ファイルを読む。本文一致はchat parserの変換に左右されるため根拠にしない。
fn inspect(root: &Path, response_id: &str, api_text: &str) -> Result<Artifact> {
    ensure!(
        !response_id.is_empty()
            && response_id.len() <= 200
            && response_id
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'),
        "invalid_capture_response_id"
    );
    directory(root)?;
    let folder = root.join(response_id);
    directory(&folder)?;
    let path = folder.join("manifest.json");
    ensure!(
        regular(&path)?.len() <= MANIFEST_LIMIT,
        "capture_manifest_too_large"
    );
    let mut bytes = vec![];
    File::open(&path)?
        .take(MANIFEST_LIMIT + 1)
        .read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() as u64 <= MANIFEST_LIMIT,
        "capture_manifest_too_large"
    );
    let manifest: Value = serde_json::from_slice(&bytes)?;
    ensure!(
        manifest["schema"] == SCHEMA
            && manifest["response_id"] == response_id
            && manifest["choice_index"].as_u64() == Some(0),
        "capture_identity_mismatch"
    );
    let backend = manifest["backend"]
        .as_str()
        .context("capture_backend_missing")?;
    ensure!(
        matches!(backend, "llama.cpp" | "mlx-lm"),
        "capture_backend_unknown"
    );
    let status = manifest["status"]
        .as_str()
        .context("capture_status_missing")?;
    ensure!(
        matches!(status, "complete" | "partial" | "failed" | "cancelled"),
        "capture_status_unknown"
    );
    ensure!(
        manifest["boundary"] == BOUNDARY,
        "capture_boundary_mismatch"
    );
    let mut artifact = Artifact {
        manifest: format!("{response_id}/manifest.json"),
        manifest_sha256: hash(&bytes),
        backend: backend.into(),
        status: status.into(),
        boundary: BOUNDARY.into(),
        model: manifest["model"].clone(),
        identity: manifest.get("identity").cloned(),
        shape: None,
        hidden_file: None,
        hidden_sha256: None,
        hidden_bytes: None,
        hidden_verified: false,
        api_text_matches_rendered: manifest["rendered_text"].as_str().map(|s| s == api_text),
        error: manifest["error"].as_str().map(str::to_owned),
    };
    // 失敗札は失敗として保存する。数値を検査していない札を採取成功と表示しない。
    if matches!(status, "failed" | "cancelled") {
        return Ok(artifact);
    }
    // 準備段階の失敗ではmodel情報が未確定でも元の理由を保持する。
    // 数値を採取済みとして扱う場合だけ、モデル識別を必須とする。
    ensure!(
        manifest["model"].as_object().is_some_and(|m| !m.is_empty()),
        "capture_model_missing"
    );
    ensure!(
        manifest["dtype"] == "float32-le" && manifest["hidden_file"] == "hidden.f32",
        "capture_layout_mismatch"
    );
    let shape: [u64; 3] = serde_json::from_value(manifest["shape"].clone())?;
    ensure!(shape.iter().all(|d| *d > 0), "capture_empty_dimensions");
    let total = manifest["total_generated_tokens"]
        .as_u64()
        .context("capture_total_tokens_missing")?;
    ensure!(
        total >= shape[0] && (status != "complete" || total == shape[0]),
        "capture_completion_count_mismatch"
    );
    let positions = manifest["positions"]
        .as_array()
        .context("capture_positions_missing")?;
    let predicted = manifest["predicted_token_ids"]
        .as_array()
        .context("capture_tokens_missing")?;
    let prompt = manifest["prompt_token_ids"]
        .as_array()
        .context("capture_prompt_missing")?;
    ensure!(
        !prompt.is_empty() && prompt.iter().all(|v| v.as_u64().is_some()),
        "capture_invalid_prompt"
    );
    ensure!(
        positions.len() as u64 == shape[0] && predicted.len() == positions.len(),
        "capture_position_count_mismatch"
    );
    for (i, position) in positions.iter().enumerate() {
        ensure!(
            position["generation_index"].as_u64() == Some(i as u64)
                && position["input_position"].as_u64() == Some((prompt.len() - 1 + i) as u64)
                && predicted[i].as_u64().is_some()
                && position["predicted_token_id"] == predicted[i],
            "capture_token_position_mismatch"
        );
    }
    let expected = shape
        .into_iter()
        .try_fold(4u64, |n, size| n.checked_mul(size))
        .context("capture_shape_overflow")?;
    let hidden_path = folder.join("hidden.f32");
    ensure!(
        regular(&hidden_path)?.len() == expected
            && manifest["hidden_bytes"].as_u64() == Some(expected),
        "capture_hidden_size_mismatch"
    );
    let mut hidden = File::open(hidden_path)?;
    let before = hidden.metadata()?;
    let mut sha = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut remaining = expected;
    while remaining > 0 {
        let count = remaining.min(buffer.len() as u64) as usize;
        hidden.read_exact(&mut buffer[..count])?;
        ensure!(
            buffer[..count]
                .as_chunks::<4>()
                .0
                .iter()
                .all(|b| f32::from_le_bytes(*b).is_finite()),
            "capture_nonfinite_hidden"
        );
        sha.update(&buffer[..count]);
        remaining -= count as u64;
    }
    let actual_hash = format!("{:x}", sha.finalize());
    let after = hidden.metadata()?;
    ensure!(
        after.len() == expected && before.modified()? == after.modified()?,
        "capture_hidden_changed"
    );
    ensure!(
        manifest["hidden_sha256"].as_str() == Some(actual_hash.as_str()),
        "capture_hidden_hash_mismatch"
    );
    artifact.shape = Some(shape);
    artifact.hidden_file = Some(format!("{response_id}/hidden.f32"));
    artifact.hidden_bytes = Some(expected);
    artifact.hidden_sha256 = Some(actual_hash);
    artifact.hidden_verified = true;
    Ok(artifact)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root =
                std::env::temp_dir().join(format!("dogido-capture-test-{}", uuid::Uuid::new_v4()));
            fs::create_dir(&root).unwrap();
            Self(root)
        }
        fn write(&self, backend: &str, status: &str) -> Value {
            let folder = self.0.join("reply_1");
            fs::create_dir_all(&folder).unwrap();
            let bytes: Vec<_> = (0..12).flat_map(|i| (i as f32).to_le_bytes()).collect();
            fs::write(folder.join("hidden.f32"), &bytes).unwrap();
            let manifest = json!({"schema":SCHEMA,"response_id":"reply_1","choice_index":0,
                "backend":backend,"status":status,"boundary":BOUNDARY,"dtype":"float32-le",
                "shape":[2,2,3],"hidden_file":"hidden.f32","hidden_bytes":bytes.len(),"hidden_sha256":hash(&bytes),
                "model":{"name":"fixture"},"identity":{"model":{"sha256":"fixture-weight-hash"}},
                "prompt_token_ids":[10,11,12],"predicted_token_ids":[20,1],
                "positions":[{"generation_index":0,"input_position":2,"predicted_token_id":20},
                             {"generation_index":1,"input_position":3,"predicted_token_id":1}],
                "total_generated_tokens":if status == "partial" {4} else {2},"rendered_text":"backend text","finish_reason":"stop"});
            self.manifest(&manifest);
            manifest
        }
        fn manifest(&self, value: &Value) {
            fs::write(
                self.0.join("reply_1/manifest.json"),
                serde_json::to_vec(value).unwrap(),
            )
            .unwrap();
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn both_backends_preserve_raw_boundary_and_partial_prefix_without_text_gate() {
        for backend in ["mlx-lm", "llama.cpp"] {
            for status in ["complete", "partial"] {
                let fixture = Fixture::new();
                fixture.write(backend, status);
                let artifact = inspect(&fixture.0, "reply_1", "API parser text").unwrap();
                assert!(artifact.hidden_verified);
                assert_eq!(artifact.status, status);
                assert_eq!(artifact.api_text_matches_rendered, Some(false));
                assert_eq!(artifact.shape, Some([2, 2, 3]));
                assert_eq!(artifact.hidden_bytes, Some(48));
                assert_eq!(artifact.manifest, "reply_1/manifest.json");
            }
        }
    }

    #[test]
    fn wrong_identity_positions_layout_and_hash_never_become_verified() {
        let fixture = Fixture::new();
        let original = fixture.write("llama.cpp", "complete");
        for (key, value) in [
            ("response_id", json!("other")),
            ("choice_index", json!(1)),
            ("boundary", json!("final_logits")),
            ("shape", json!([3, 2, 3])),
            ("hidden_bytes", json!(44)),
            ("hidden_sha256", json!("wrong")),
            ("hidden_file", json!("../hidden.f32")),
            ("dtype", json!("float16")),
            ("prompt_token_ids", json!([])),
            ("predicted_token_ids", json!([20])),
            (
                "positions",
                json!([{"generation_index":1,"input_position":2,"predicted_token_id":20},
                                  {"generation_index":2,"input_position":3,"predicted_token_id":1}]),
            ),
        ] {
            let mut manifest = original.clone();
            manifest[key] = value;
            fixture.manifest(&manifest);
            assert!(inspect(&fixture.0, "reply_1", "").is_err(), "{key}");
        }
        assert!(inspect(&fixture.0, "../reply_1", "").is_err());
    }

    #[test]
    fn nonfinite_values_are_invalid_even_when_their_hash_matches() {
        let fixture = Fixture::new();
        let mut manifest = fixture.write("mlx-lm", "complete");
        let mut bytes = fs::read(fixture.0.join("reply_1/hidden.f32")).unwrap();
        bytes[..4].copy_from_slice(&f32::NAN.to_le_bytes());
        fs::write(fixture.0.join("reply_1/hidden.f32"), &bytes).unwrap();
        manifest["hidden_sha256"] = hash(&bytes).into();
        fixture.manifest(&manifest);
        assert!(
            inspect(&fixture.0, "reply_1", "")
                .unwrap_err()
                .to_string()
                .contains("nonfinite")
        );
    }

    #[test]
    fn failure_manifest_does_not_claim_numeric_capture() {
        let fixture = Fixture::new();
        let mut manifest = fixture.write("mlx-lm", "failed");
        manifest["model"] = json!({});
        manifest["error"] = "unsupported model class".into();
        fixture.manifest(&manifest);
        fs::remove_file(fixture.0.join("reply_1/hidden.f32")).unwrap();
        let artifact = inspect(&fixture.0, "reply_1", "").unwrap();
        assert!(!artifact.hidden_verified);
        assert_eq!(artifact.status, "failed");
        assert_eq!(artifact.error.as_deref(), Some("unsupported model class"));
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_data_is_not_followed() {
        let fixture = Fixture::new();
        fixture.write("llama.cpp", "complete");
        let hidden = fixture.0.join("reply_1/hidden.f32");
        let other = fixture.0.join("other.f32");
        fs::rename(&hidden, &other).unwrap();
        std::os::unix::fs::symlink(other, hidden).unwrap();
        assert!(inspect(&fixture.0, "reply_1", "").is_err());
    }

    fn request() -> (StructuredRequest, GenerationRequest) {
        let request = StructuredRequest {
            kind: "haiku_draft".into(),
            details: Default::default(),
            route: "haiku".into(),
            temperature: 0.6,
            max_tokens: Some(20),
            fallback_value: json!({}),
        };
        let input = GenerationRequest {
            schema_version: 1,
            kind: request.kind.clone(),
            model: "fixture".into(),
            messages: vec![crate::types::ChatMessage {
                role: crate::types::Role::User,
                content: "fixture".into(),
            }],
            temperature: 0.6,
            max_tokens: 20,
            enable_thinking: false,
        };
        (request, input)
    }
    fn report() -> GenerationReport {
        GenerationReport {
            schema_version: 1,
            provider: "fixture".into(),
            kind: "haiku_draft".into(),
            requested_model: "fixture".into(),
            response_model: "fixture".into(),
            response_id: "reply_1".into(),
            elapsed_ms: 1,
            generated: GeneratedText {
                text: "API text".into(),
                finish_reason: Some("stop".into()),
                prompt_tokens: Some(3),
                completion_tokens: Some(2),
            },
        }
    }

    #[tokio::test]
    async fn missing_artifacts_and_reused_response_ids_do_not_fake_observations() {
        let fixture = Fixture::new();
        let (request, input) = request();
        let mut missing = Capture::new(fixture.0.clone(), "session", "job");
        let slot = missing.begin(3, &request, &input).unwrap();
        missing.finish(slot, &report()).await;
        let row = &missing.observation.requests[0];
        assert_eq!(row.status, "missing");
        assert!(row.artifact.is_none());
        assert_eq!(row.request_index, 3);
        assert_eq!(row.request_id, "job:request:3");
        assert_eq!(row.api_output.as_ref().unwrap().text, "API text");
        fixture.write("llama.cpp", "complete");
        let mut captured = Capture::new(fixture.0.clone(), "session", "job");
        let first = captured.begin(3, &request, &input).unwrap();
        captured.finish(first, &report()).await;
        let second = captured.begin(4, &request, &input).unwrap();
        captured.finish(second, &report()).await;
        assert!(
            captured
                .observation
                .requests
                .iter()
                .all(|r| r.status == "invalid" && r.artifact.is_none())
        );
        let mut next = Capture::new(fixture.0.clone(), "session", "next");
        let slot = next.begin(1, &request, &input).unwrap();
        next.failed(slot, &anyhow::anyhow!("network failed"));
        assert_eq!(next.observation.requests[0].status, "request_failed");
        assert!(next.observation.requests[0].response_id.is_none());
    }
}
