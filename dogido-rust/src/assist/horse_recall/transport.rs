//! 馬の選択要求を本文JSON／native toolで同条件に構築・復号する比較境界。
//! 世界操作はせず、両方式の同じChoiceを同じ検証器へ渡す。勝者決定後は敗者をGit履歴へ移す。
use super::contracts::{Choice, Prepared};
use crate::types::{GenerationRequest, Role};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::sync::LazyLock;
static TEXT: LazyLock<Value> = LazyLock::new(|| serde_json::from_str(include_str!("prompts.json")).expect("horse prompt assets"));

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Transport { Json, Native }

/// 通常会話の人格・観測・履歴は保ち、既存の出力契約だけを今回利用可能な契約へ置き換える。
/// 一回の会話呼出しなので、馬に関係しない文を先に分類する推論や語句ゲートは置かない。
pub fn request(mut base: GenerationRequest, prepared: &Prepared, transport: Transport) -> Result<(GenerationRequest, Vec<Value>)> {
    let old = crate::companion_prompt::choice_contract();
    let system = base.messages.iter_mut().find(|m| matches!(m.role, Role::System)).context("horse_system_required")?;
    ensure!(system.content.matches(old).count() == 1, "horse_choice_contract_required");
    let key = if transport == Transport::Json { "json" } else { "native" };
    system.content = system.content.replacen(old, TEXT[key].as_str().unwrap(), 1);
    system.content.push('\n');
    system.content.push_str(TEXT["common"].as_str().unwrap());
    let candidates: Vec<Value> = prepared.candidates.iter().enumerate().map(|(i, horse)| json!({
        "candidate_id":format!("h{}",i+1), "name":horse.custom_name,
        "eligible":prepared.snapshot.eligible(horse),
        "recently_ridden":prepared.snapshot.recent_mount_id.as_ref()==Some(&horse.entity_id)
    })).collect();
    let user = base.messages.iter_mut().rev().find(|m| matches!(m.role, Role::User)).context("horse_user_required")?;
    user.content.push_str(&format!("\n【Minecraftの所有馬候補・データ】\n{}\n今回発話の原文: {}\nこの発話が情報を尋ねているだけなら、会話で回答してな。場所の質問を、呼んで見つける操作へ置き換えたらあかんで。", json!({"status":prepared.snapshot.status,"candidates":candidates}), prepared.original));
    let tools = if transport == Transport::Native { vec![definition()] } else { vec![] };
    Ok((base,tools))
}

/// NativeではAPIの通常本文を発話とし、意図的な空本文は沈黙へ写す。
/// 本文JSONへ誤って書かれた操作は発話にも命令にも変換しない。
pub fn conversation(transport: Transport, content: &str) -> crate::chat_validation::Outcome {
    if transport == Transport::Json {
        return crate::chat_validation::finish(Some(content));
    }
    if content.trim_start().starts_with('{') || content.contains("request_horse_recall") {
        return crate::chat_validation::finish(Some("{}"));
    }
    let action = if content.trim().is_empty() { "silent" } else { "speak" };
    crate::chat_validation::finish(Some(&json!({"action":action,"speech":content}).to_string()))
}

pub fn definition() -> Value {
    json!({"type":"function","function":{
        "name":"request_horse_recall","description":TEXT["tool_description"],
        "parameters":{"type":"object","properties":{
            "candidate_id":{"type":"string","description":"今回の候補のID。名前やUUIDではない。"},
            "evidence":{"type":"string","description":"今回発話の原文全文。省略・補作・過去の発話の引用はしない。"},
            "confidence":{"type":"number","minimum":0,"maximum":1}},
            "required":["candidate_id","evidence","confidence"],"additionalProperties":false}
    }})
}

/// 不完全な操作JSONは補完しない。普通の会話だけを既存の本文補完へ渡す。
pub fn decode(transport: Transport, message: &Value, finish_reason: &str) -> Result<Option<Choice>> {
    if let Some(calls) = message.get("tool_calls").filter(|v| !v.is_null()) {
        let calls = calls.as_array().context("horse_tool_calls_type")?;
        if !calls.is_empty() {
            ensure!(transport == Transport::Native, "horse_unexpected_native");
            ensure!(calls.len() == 1 && finish_reason != "length", "horse_one_complete_call_required");
            let c = &calls[0];
            ensure!(c["type"] == "function" && c["function"]["name"] == "request_horse_recall", "horse_unknown_tool");
            ensure!(c["id"].as_str().is_some_and(|v| !v.is_empty() && v.len() <= 128), "horse_call_id");
            let args = c["function"]["arguments"].as_str().context("horse_arguments_type")?;
            ensure!(args.len() <= 16_384, "horse_arguments_size");
            return Ok(Some(serde_json::from_str(args)?));
        }
    }
    let content = message["content"].as_str().unwrap_or("");
    let value: Value = match serde_json::from_str(content) { Ok(v) => v, Err(_) => return Ok(None) };
    if value["action"] != "recall_horse" { return Ok(None); }
    ensure!(transport == Transport::Json && finish_reason != "length", "horse_complete_json_required");
    let mut args = value.as_object().context("horse_object")?.clone();
    args.remove("action");
    Ok(Some(serde_json::from_value(Value::Object(args))?))
}
