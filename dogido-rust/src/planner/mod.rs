//! 通常会話の限定planner。入力投影・catalog検索は呼出側、採否はこのコードが所有する。
//! dialogueがRustの現在観測・実再生済み履歴から入力を作る。plannerはread actionを一件だけ選び、
//! 発話・操作・状態変更・保存はしない。旧Pythonとの比較は保存済みfixtureの来歴。
mod grounding;
pub mod handoff;
pub mod prepare;
mod prompts;
pub mod repair;
mod runner;
mod validation;

pub use grounding::{Grounding, fixed_reply, ground};
pub use prompts::messages;
pub(crate) use prompts::python_json;
pub use runner::{PlannerReport, run};
pub use validation::{contract_errors, extract_object, parse_model_plan};

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    ContinueConversation,
    CheckEntityPresence,
    IdentifyEntity,
    AnswerObservation,
    ClarifyReference,
    CorrectPreviousReply,
    RepairConversation,
    ClarifyRepair,
}

impl Action {
    pub fn is_entity(self) -> bool {
        matches!(
            self,
            Self::CheckEntityPresence | Self::IdentifyEntity | Self::CorrectPreviousReply
        )
    }
    pub fn is_repair(self) -> bool {
        matches!(self, Self::RepairConversation | Self::ClarifyRepair)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Evidence {
    pub turn_id: String,
    pub quote: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    pub action: Action,
    pub focus: String,
    pub entity_query: String,
    pub evidence: Vec<Evidence>,
    pub confidence: f64,
    pub source: String,
    pub status: String,
    #[serde(default)]
    pub repair: Option<repair::Repair>,
    #[serde(default)]
    pub presence_challenged: bool,
}
impl Plan {
    pub fn requests_catalog(&self) -> bool {
        self.action.is_entity() && !self.entity_query.is_empty()
    }
}

/// 状態機械からの読み取り用投影。モデルの出力とは別の入口。
/// historyには実再生済みの発話だけ、observationsには現在観測だけを渡す。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Details {
    pub allowed_actions: Vec<Action>,
    pub history: Vec<Value>,
    #[serde(default = "empty_object")]
    pub pending_repair: Value,
    #[serde(default = "empty_object")]
    pub observations: Value,
    #[serde(default = "empty_object")]
    pub routing_hints: Value,
    pub current: Value,
}
fn empty_object() -> Value {
    serde_json::json!({})
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreparedPlan {
    pub schema_version: u32,
    pub model: String,
    pub enable_thinking: bool,
    pub details: Details,
    /// 現行のカタログ・明示質問判定で作るコード側fallback。モデルからは受け取らない。
    pub fallback: Plan,
}
impl PreparedPlan {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            self.schema_version == 1 && !self.model.trim().is_empty(),
            "invalid planner request version/model"
        );
        anyhow::ensure!(
            self.fallback.source == "fallback"
                && self.fallback.status == "fallback"
                && self.fallback.repair.is_none(),
            "fallback must be code-owned"
        );
        anyhow::ensure!(
            self.details.current["turn_id"] == "current"
                && self.details.current["role"] == "user"
                && self.details.current["text"].is_string(),
            "invalid current turn"
        );
        anyhow::ensure!(
            self.details.history.len() <= 10
                && self.details.history.iter().all(|row| row.is_object()
                    && row["turn_id"].is_string()
                    && row["text"].is_string()
                    && (matches!(row["role"].as_str(), Some("user" | "assistant"))
                        || row["role"] == "event" && row["reaction"] == "silent")),
            "invalid completed history projection"
        );
        anyhow::ensure!(
            self.details.observations.is_object() && self.details.routing_hints.is_object(),
            "invalid observation projection"
        );
        anyhow::ensure!(
            self.details.pending_repair == repair::pending(&self.details.history)
                || self.details.pending_repair == empty_object(),
            "pending repair does not match completed history"
        );
        let raw = self
            .details
            .current
            .get("raw_text")
            .unwrap_or(&self.details.current["text"]);
        anyhow::ensure!(raw.is_string(), "invalid raw input");
        anyhow::ensure!(
            !self.details.allowed_actions.iter().any(|a| a.is_repair())
                || repair::has_signal(raw.as_str().unwrap())
                || self.details.pending_repair != empty_object(),
            "repair requires a signal or completed clarification"
        );
        Ok(())
    }
}

// Python str.split/stripのUnicode空白にはU+001C..U+001Fも含まれる。
pub(super) fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}
pub(super) fn clean(text: &str, limit: usize) -> String {
    text.split(is_space)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(limit)
        .collect()
}
pub(super) fn string<'a>(row: &'a Value, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}
