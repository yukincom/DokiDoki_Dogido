//! workshopのnative toolを、既存の検査・編集入力へ渡す閉じた境界。
//! 関数名や一度に実行できる件数を固定し、正本・採用・保存は所有しない。
use super::inspection_tool;
use crate::workshop_editing::normalize_player_edit_tool;
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};

fn player_edit_definition() -> Value {
    json!({"type":"function","function":{
        "name":"stage_player_edit",
        "description":"本人が今回の発話で頼んだ語句の差し替えをコードへ渡す。部分変更では旧語と新語だけを渡し、変更後の一行を自分で作らない。「AをBに変えて」ならtarget_fragmentはA、replacement_textはB。行の組み立てはコードが行う。現在句（未採用案があればその案）の対象行をline_idとexpected_textで指定し、置換語と行為のevidenceは今回の原文から抜く。音数・読み・他行が変わらないこと・保存の成否はコードが検査する。単なる相談、疑問、否定、引用、以前の案を選ぶだけの返事では呼ばない。",
        "parameters":{"type":"object","properties":{
            "line_id":{"type":"string","enum":["line_1","line_2","line_3"]},
            "line_reference_evidence":{"type":"string","description":"今回の原文に行の呼び名がある場合だけ、その部分（例: 冒頭の五音）。対象を省略した発話なら省略する。以前の呼び名をコピーしない。"},
            "expected_text":{"type":"string","description":"変更前の対象一行。表示表記か確定読みをそのまま写す。"},
            "target_fragment":{"type":"string","description":"差し替える旧語だけ。expected_text内の連続部分。「AをBに変えて」ならA。本人が一行全体の置換を指定したときだけ空文字。"},
            "replacement_text":{"type":"string","description":"本人が今回指定した新しい語句だけ。「AをBに変えて」ならBだけ。変更後の一行を作って渡さない。一行全体を渡せるのは、その全文が今回の原文にある場合だけ。自分で補作・読み変換しない。"},
            "evidence":{"type":"string","description":"今回のプレイヤー発話から、変更意思を含む部分をそのまま引用する。「AをBに変えて」ならevidenceも「AをBに変えて」。現在句の行や語、過去の会話から取らない。"},
            "confidence":{"type":"number","minimum":0,"maximum":1}},
            "required":["line_id","expected_text","target_fragment","replacement_text","evidence","confidence"],
            "additionalProperties":false}
    }})
}

pub fn definitions(allowed: &[&str]) -> Vec<Value> {
    let mut result = Vec::new();
    if allowed.contains(&"inspect") {
        result.push(inspection_tool::definition());
    }
    if allowed.contains(&"stage_player_edit") {
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

    pub fn decode(message: Value, workshop: &Value, original: &str) -> Result<Self> {
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
                let args: Value = serde_json::from_str(
                    call["function"]["arguments"]
                        .as_str()
                        .context("invalid_tool_arguments")?,
                )
                .context("invalid_tool_arguments")?;
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

    /// schema検証済みの一手へ、元句・原文との照合を終えた行番号を保持する。
    /// 独自JSONのline_proposal schemaへ新しいmodel入力フィールドは足さない。
    pub fn retain_verified_target(&self, step: &mut Value) {
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
