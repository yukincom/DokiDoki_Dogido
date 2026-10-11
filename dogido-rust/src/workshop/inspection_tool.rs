//! 読み・音数・保存済み出典の確認を、一つのnative toolと既存inspectの間で変換する。
//! 検査そのものはworkshop::inspect、実行時期と再判断の上限はworkshop_runtimeが所有する。
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};

pub fn definition() -> Value {
    json!({"type":"function","function":{
        "name":"inspect_verse",
        "description":"今相談している句の三行について、保存された読み・音数・出典を確認する。checksに必要な項目を一つ以上指定する。読みはreading、音数はmeter、出典はsource。相談中の語や候補はinspect_textへ入れると辞書の読み・音数を確認でき、置く行が決まっていればinspect_line_indexも指定する。結果を受け取ってから答える。句の変更や保存はしない。",
        "parameters":{"type":"object","properties":{
            "checks":{"type":"array","items":{"type":"string","enum":["reading","meter","source"]},"minItems":1,"maxItems":3,"uniqueItems":true},
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
            (1..=3).contains(&args.checks.len())
                && args
                    .checks
                    .iter()
                    .all(|check| matches!(check.as_str(), "reading" | "meter" | "source")),
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
