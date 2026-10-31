//! workshopのnative toolを、既存の検査・編集入力へ渡す閉じた境界。
//! 関数名や一度に実行できる件数を固定し、正本・採用・保存は所有しない。
use super::inspection_tool;
use crate::workshop_editing::normalize_player_edit_tool;
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};

fn player_edit_definition() -> Value {
    json!({"type":"function","function":{
        "name":"stage_player_edit",
        "description":"BERTが決めた行への具体変更を解釈し、変更後の一行全体を返す。本人の指定語か、今回選ばれた同じ対象行のconversation_candidateを使い、部分変更なら変更しない前後の言葉も含める。行ID・元句・差分比較はコードが担当する。音声認識の誤りや表記の違いは解釈してよい。指定のない表現は補作せず、不明ならaskで尋ねる。",
        "parameters":{"type":"object","properties":{
            "replacement_text":{"type":"string","description":"変更後の一行全体。部分変更でも残す言葉を含め、行IDや他の二行は含めない。元の行と同じかどうかはコードが比較する。"},
            "evidence":{"type":"string","description":"今回の変更依頼を解釈した根拠。記録用。"},
            "confidence":{"type":"number","description":"任意の自己申告値。記録用。"}},
            "required":["replacement_text"],
            "additionalProperties":false}
    }})
}

/// 行・具体変更はBERTへ変更済み。旧判定へ戻さない。対象不明の編集toolは公開しない。
pub fn definitions_for_target(allowed: &[&str], target: Option<usize>) -> Vec<Value> {
    let mut result = Vec::new();
    if allowed.contains(&"inspect") {
        result.push(inspection_tool::definition());
    }
    if allowed.contains(&"stage_player_edit") && target.is_some() {
        result.push(player_edit_definition());
    }
    result
}

/// APIへ移した操作は本文JSONから実行しない。内部の検査・保存用actionとは分ける。
pub fn requires_native_call(action: &str) -> bool {
    matches!(action, "inspect" | "stage_player_edit")
}

pub fn validate_transport(payload: &Value, call: Option<&Call>) -> Result<()> {
    if requires_native_call(payload["action"].as_str().unwrap_or("")) {
        ensure!(call.is_some(), "native_tool_required");
    }
    Ok(())
}

pub enum Call {
    Inspect(inspection_tool::Call),
    PlayerEdit {
        message: Value,
        id: String,
        frame: Value,
    },
}

impl Call {
    pub fn trace(&self) -> Value {
        match self {
            Self::Inspect(call) => call.trace(),
            Self::PlayerEdit { id, .. } => json!({"name":"stage_player_edit","id":id}),
        }
    }

    pub fn decode_for_target(
        message: Value,
        workshop: &Value,
        original: &str,
        target: Option<usize>,
    ) -> Result<Self> {
        ensure!(
            serde_json::to_vec(&message)?.len() < 32_768,
            "tool_message_too_large"
        );
        let calls = message["tool_calls"]
            .as_array()
            .context("missing_tool_calls")?;
        ensure!(calls.len() == 1, "one_workshop_tool_required");
        let call = &calls[0];
        ensure!(call["type"] == "function", "unsupported_workshop_tool");
        let id = call["id"]
            .as_str()
            .filter(|id| !id.is_empty() && id.len() <= 128)
            .context("invalid_tool_call_id")?
            .to_owned();
        match call["function"]["name"].as_str() {
            Some("inspect_verse") => Ok(Self::Inspect(inspection_tool::Call::decode(message)?)),
            Some("stage_player_edit") => {
                let mut args: Value = serde_json::from_str(
                    call["function"]["arguments"]
                        .as_str()
                        .context("invalid_tool_arguments")?,
                )
                .context("invalid_tool_arguments")?;
                let index = target.context("bert_edit_target_required")?;
                ensure!(
                    ["line_id", "expected_text", "line_reference_evidence"]
                        .iter()
                        .all(|name| args.get(*name).is_none()),
                    "classified_target_fields_are_code_owned"
                );
                crate::workshop_editing::fill_classified_edit_target(workshop, index, &mut args)?;
                let frame = normalize_player_edit_tool(workshop, original, &args)?;
                Ok(Self::PlayerEdit { message, id, frame })
            }
            _ => anyhow::bail!("unsupported_workshop_tool"),
        }
    }

    pub fn payload(&self) -> Value {
        match self {
            Self::Inspect(call) => call.payload(),
            Self::PlayerEdit { frame, .. } => frame["payload"].clone(),
        }
    }

    /// 受け取った一手へ、BERTと現在句から確定した行番号を保持する。
    /// 独自JSONのline_proposal schemaへ新しいmodel入力フィールドは足さない。
    pub fn retain_classified_target(&self, step: &mut Value) {
        if let Self::PlayerEdit { frame, .. } = self {
            step["analysis"]["line_proposal"] = frame["proposal"].clone();
        }
    }

    pub fn is_player_edit(&self) -> bool {
        matches!(self, Self::PlayerEdit { .. })
    }

    /// 検査結果を同じcall IDへ返す。編集成功時の発話・適用は既存コードで完結する。
    pub fn result_messages(self, observation: &Value) -> Vec<Value> {
        match self {
            Self::Inspect(call) => call.result_messages(observation),
            Self::PlayerEdit { message, id, .. } => vec![
                message,
                json!({"role":"tool","tool_call_id":id,"name":"stage_player_edit",
                    "content":observation.to_string()}),
            ],
        }
    }
}

#[cfg(test)]
mod tests;
