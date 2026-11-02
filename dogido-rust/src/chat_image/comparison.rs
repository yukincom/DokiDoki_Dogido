//! Fabricが選んだ比較元と変化の有無を照合する。マスは内部計測であり、画像には描かれない。
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ComparisonStatus {
    NotCompared,
    Unchanged,
    Changed,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ComparisonReference {
    pub capture_id: String,
    pub captured_at: String,
    pub sha256: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneComparison {
    pub status: ComparisonStatus,
    pub reference: Option<ComparisonReference>,
}
impl SceneComparison {
    pub(crate) fn validate(&self, captured_at: &str) -> Result<()> {
        ensure!(
            (self.status == ComparisonStatus::NotCompared) == self.reference.is_none(),
            "chat_image_comparison_status"
        );
        if let Some(reference) = &self.reference {
            let id = reference.capture_id.strip_prefix("view_").and_then(|id| {
                uuid::Uuid::parse_str(id)
                    .ok()
                    .filter(|uuid| uuid.to_string() == id)
            });
            ensure!(id.is_some(), "chat_image_comparison_capture_id");
            ensure!(
                reference.sha256.len() == 64
                    && reference.sha256.bytes().all(|v| v.is_ascii_hexdigit()),
                "chat_image_comparison_sha256"
            );
            let time = chrono::DateTime::parse_from_rfc3339(&reference.captured_at).ok();
            let current = chrono::DateTime::parse_from_rfc3339(captured_at).ok();
            ensure!(
                reference.captured_at.len() <= 64
                    && time.zip(current).is_some_and(|(old, now)| old < now),
                "chat_image_comparison_time"
            );
        }
        Ok(())
    }
}
