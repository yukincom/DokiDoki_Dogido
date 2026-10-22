//! 辞書の読み・保存済み資料の参照を、一つのnative toolと既存inspectの間で変換する。
//! 参照内容はworkshop::inspect、実行時期と会話の上限はworkshop_runtimeが所有する。句の合否は扱わない。
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};

pub fn definition() -> Value {
    json!({"type":"function","function":{
        "name":"inspect_verse",
        "description":"本人から読み方や保存された材料を尋ねられ、手元の情報だけで答えられないときに参照する。checksはreadingまたはsource。語の辞書読みはinspect_textで指定し、行が決まっていればinspect_line_indexを添えられる。編集の前提確認には使わず、句の変更・採否・保存は行わない。",
        "parameters":{"type":"object","properties":{
            "checks":{"type":"array","items":{"type":"string","enum":["reading","source"]},"minItems":1,"maxItems":2,"uniqueItems":true},
            "inspect_text":{"type":"string","minLength":1,"maxLength":160},
            "inspect_line_index":{"type":"integer","minimum":0,"maximum":2}},
            "required":["checks"],"additionalProperties":false}
    }})
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Arguments {
    checks: Vec<String>,
    inspect_text: Option<String>,
    inspect_line_index: Option<usize>,
}

pub struct Call {
    message: Value,
    id: String,
    args: Arguments,
}
impl Call {
    /// API応答の一件だけを読み、既存inspectの入力へ変換する。任意関数の実行器は持たない。
    pub fn decode(message: Value) -> Result<Self> {
        let calls = message["tool_calls"]
            .as_array()
            .context("missing tool calls")?;
        ensure!(calls.len() == 1, "one inspection call required");
        let call = &calls[0];
        ensure!(
            call["type"] == "function" && call["function"]["name"] == "inspect_verse",
            "unsupported workshop tool"
        );
        let id = call["id"]
            .as_str()
            .filter(|id| !id.is_empty())
            .context("missing tool call id")?
            .to_owned();
        let args: Arguments = serde_json::from_str(
            call["function"]["arguments"]
                .as_str()
                .context("invalid tool arguments")?,
        )?;
        ensure!(
            (1..=2).contains(&args.checks.len())
                && args
                    .checks
                    .iter()
                    .all(|check| matches!(check.as_str(), "reading" | "source")),
            "invalid inspection checks"
        );
        ensure!(
            args.inspect_text
                .as_ref()
                .is_none_or(|text| { !text.trim().is_empty() && text.chars().count() <= 160 })
                && args.inspect_line_index.is_none_or(|index| index < 3),
            "invalid inspection target"
        );
        Ok(Self { message, id, args })
    }

    pub fn payload(&self) -> Value {
        let mut payload = json!({"action":"inspect","checks":self.args.checks});
        if let Some(text) = &self.args.inspect_text {
            payload["inspect_text"] = text.clone().into();
        }
        if let Some(index) = self.args.inspect_line_index {
            payload["inspect_line_index"] = index.into();
        }
        payload
    }

    pub fn trace(&self) -> Value {
        json!({"name":"inspect_verse","id":self.id})
    }

    /// コードが測定した結果を同じ呼出しIDへ返す。会話履歴や長期記憶へは保存しない。
    pub fn result_messages(self, observation: &Value) -> Vec<Value> {
        vec![
            self.message,
            json!({"role":"tool","tool_call_id":self.id,
            "name":"inspect_verse","content":observation.to_string()}),
        ]
    }
}
