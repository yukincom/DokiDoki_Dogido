use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    System,
    Developer,
    User,
    Assistant,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ChatMessage {
    pub role: Role,
    pub content: String,
}

/// 移行比較用の要求。Fabricイベントやドギドの全設定を代用しない。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GenerationRequest {
    pub schema_version: u32,
    pub kind: String,
    pub model: String,
    pub messages: Vec<ChatMessage>,
    pub temperature: f64,
    pub max_tokens: u64,
    pub enable_thinking: bool,
}

impl GenerationRequest {
    pub fn validate(&self) -> Result<()> {
        ensure!(self.schema_version == 1, "unsupported schema_version");
        ensure!(!self.kind.trim().is_empty(), "kind is empty");
        ensure!(!self.model.trim().is_empty(), "model is empty");
        ensure!(!self.messages.is_empty(), "messages is empty");
        ensure!(self.temperature.is_finite(), "temperature is not finite");
        ensure!(self.max_tokens > 0, "max_tokens must be positive");
        Ok(())
    }
}

/// 本文が途切れていても終了情報を残す。採否・再生成はドギド側で判断する。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct GeneratedText {
    pub text: String,
    pub finish_reason: Option<String>,
    pub completion_tokens: Option<u64>,
    pub prompt_tokens: Option<u64>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct GenerationReport {
    pub schema_version: u32,
    pub provider: String,
    pub kind: String,
    pub requested_model: String,
    pub response_model: String,
    pub response_id: String,
    pub elapsed_ms: u128,
    pub generated: GeneratedText,
}
