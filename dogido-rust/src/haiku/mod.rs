//! 観測由来の材料から三行の川柳を生成し、音数・出典・重複を検査する。
//! 不合格行の再生成回数と材料の使い分けはRustが管理し、Backendへ生成と読み整形を依頼する。
//! ここで返すのは採否と行ごとの根拠。発句時刻、現在句の更新、保存、音声配送はdialogue側が担当する。
pub mod companions;
mod generation;
pub mod lexical;
pub mod meter;
pub mod revision;
#[cfg(test)]
mod tests;
pub use generation::generate;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::future::Future;

pub const PROMPT_VARIANT: &str = "source_atoms_slots_v2_kana_normalize";
/// 一つの材料とその出典。似た文面でも、同じ事実を複数行で使い回したかをIDと参照先で検査する。
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceAtom {
    pub atom_id: String,
    pub text: String,
    pub source_ref: String,
    pub field_path: String,
    pub observation_role: String,
    pub kind: String,
    pub claim_class: String,
    pub claim_scopes: Vec<String>,
    #[serde(default)]
    pub basis_atom_ids: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StructuredRequest {
    pub kind: String,
    pub details: Map<String, Value>,
    pub route: String,
    pub temperature: f64,
    pub max_tokens: Option<u64>,
    pub fallback_value: Value,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TransformMode {
    Normalize,
    Correct,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TransformRequest {
    pub text: String,
    pub mode: TransformMode,
    pub line_index: usize,
    pub atom_ids: Vec<String>,
    pub source_atoms: Vec<SourceAtom>,
}
/// Signature is Python's NFKC + casefold + kana folding/filtering of this text.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct LineForm {
    pub text: String,
    pub signature: String,
}
/// 生成器から外部処理を呼ぶ境界。返答は未検査の候補であり、このtraitに採用・保存を委ねない。
pub trait Backend {
    fn generate(
        &mut self,
        request: StructuredRequest,
    ) -> impl Future<Output = anyhow::Result<Value>> + Send;
    fn transform(
        &mut self,
        request: TransformRequest,
    ) -> impl Future<Output = anyhow::Result<LineForm>> + Send;
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Input {
    pub details: Map<String, Value>,
    pub source_atoms: Vec<SourceAtom>,
    pub fallback_text: String,
    pub max_tokens: Option<u64>,
    pub grounding_max_tokens: u64,
    pub generation_strategy: String,
    pub max_regeneration_rounds: i64,
    pub llm_enabled: bool,
}
impl Default for Input {
    fn default() -> Self {
        Self {
            details: Map::new(),
            source_atoms: vec![],
            fallback_text: String::new(),
            max_tokens: None,
            grounding_max_tokens: 512,
            generation_strategy: "three_slot".into(),
            max_regeneration_rounds: 6,
            llm_enabled: true,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct LineSource {
    pub line_index: usize,
    pub text: String,
    pub atom_ids: Vec<String>,
    pub sources: Vec<SourceAtom>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct GroundedHaikuResult {
    pub text: String,
    pub accepted: bool,
    pub line_sources: Vec<LineSource>,
    pub failure_reason: Option<String>,
    pub generation_strategy: String,
    pub regeneration_rounds: usize,
    pub prompt_variant: String,
}

pub mod source_atoms;

pub mod context;

pub mod materials;

pub mod preparation;
