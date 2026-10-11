//! 候補を見せない依頼判定と、依頼後の一頭選択を構築する比較境界。
//! どちらも同じ原文検証を通す。API/JSONの比較後、敗者はGit履歴へ移す。
use super::contracts::{Choice, Prepared};
use crate::types::{GenerationRequest, Role};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::sync::LazyLock;
static TEXT: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("prompts.json")).expect("horse prompt assets")
});

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Transport {
    Json,
    Native,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    Intent,
    Selection,
}

/// 原文に基づく候補要求。これだけでは個体選択も世界操作も起こらない。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Intent {
    pub evidence: String,
    pub confidence: f64,
}
impl Intent {
    pub fn validate(&self, original: &str) -> Result<()> {
        super::contracts::validate_evidence(original, &self.evidence, self.confidence)
    }
}
pub enum Decision {
    List(Intent),
    Select(Choice),
    Conversation,
}

fn action(stage: Stage) -> &'static str {
    match stage {
        Stage::Intent => "list_horses",
        Stage::Selection => "select_horse",
    }
}

/// 一回目は候補の存在・名前を一切渡さない。依頼判定が成立した場合だけ二回目へ一覧を渡す。
/// 名前の表記ゆれと対象の対応はモデルが判断し、UUIDはコード側の固定表にのみ保持する。
pub fn request(
    mut base: GenerationRequest,
    prepared: &Prepared,
    transport: Transport,
    stage: Stage,
) -> Result<(GenerationRequest, Vec<Value>)> {
    let old = crate::companion_prompt::choice_contract();
    let system = base
        .messages
        .iter_mut()
        .find(|m| matches!(m.role, Role::System))
        .context("horse_system_required")?;
    ensure!(
        system.content.matches(old).count() == 1,
        "horse_choice_contract_required"
    );
    let key = format!(
        "{}_{}",
        if transport == Transport::Json {
            "json"
        } else {
            "native"
        },
        if stage == Stage::Intent {
            "intent"
        } else {
            "selection"
        }
    );
    system.content = system
        .content
        .replacen(old, TEXT[&key].as_str().unwrap(), 1);
    system.content.push('\n');
    system.content.push_str(
        TEXT[if stage == Stage::Intent {
            "intent"
        } else {
            "selection"
        }]
        .as_str()
        .unwrap(),
    );
    let user = base
        .messages
        .iter_mut()
        .rev()
        .find(|m| matches!(m.role, Role::User))
        .context("horse_user_required")?;
    user.content
        .push_str(&format!("\n今回発話の原文: {}\n", prepared.original));
    if stage == Stage::Selection {
        let candidates: Vec<Value> = prepared.candidates.iter().enumerate().map(|(i, horse)| json!({
            "candidate_id":format!("h{}",i+1), "name":horse.custom_name,
            "eligible":prepared.snapshot.eligible(horse),
            "recently_ridden":prepared.snapshot.recent_mount_id.as_ref()==Some(&horse.entity_id)
        })).collect();
        user.content.push_str(&format!("【依頼後に取得したMinecraftの所有馬候補・データ】\n{}\n一頭に決まらなければ、どの馬か聞いてな。まだ実行してへんで。",
            json!({"status":prepared.snapshot.status,"candidates":candidates})));
    } else {
        user.content.push_str("今回の本人が今ここへ呼び寄せるよう依頼したかだけ判断してな。能力や場所の質問・他人の発言の報告・名前だけの呼びかけは依頼に補わず、会話で返してな。");
    }
    let tools = if transport == Transport::Native {
        vec![definition(stage)]
    } else {
        vec![]
    };
    Ok((base, tools))
}
pub fn definition(stage: Stage) -> Value {
    let mut properties = json!({
        "evidence":{"type":"string","description":"今回の本人の発話全文を、そのまま省略せず返す。"},
        "confidence":{"type":"number","minimum":0,"maximum":1}
    });
    let mut required = vec!["evidence", "confidence"];
    if stage == Stage::Selection {
        properties["candidate_id"] = json!({"type":"string","description":"現在一覧にある一頭の短いID。名前やUUIDは返さない。"});
        required.push("candidate_id");
    }
    json!({"type":"function","function":{"name":action(stage),
        "description":if stage==Stage::Intent {"本人から今ここへ馬を呼ぶ依頼があった場合だけ、所有馬の候補一覧を要求する。まだ移動しない。"}
            else {"本人の呼び寄せ依頼に合う、呼び寄せ可能な一頭を一覧から選ぶ。曖昧なら使わず質問する。"},
        "parameters":{"type":"object","properties":properties,"required":required,"additionalProperties":false}}})
}
/// 不完全な操作や別段階の操作を補完しない。API本文のJSONをtool callへ読み替えない。
pub fn decode(
    transport: Transport,
    stage: Stage,
    message: &Value,
    finish: &str,
) -> Result<Decision> {
    let mut arguments = None;
    if let Some(calls) = message.get("tool_calls").filter(|v| !v.is_null()) {
        let calls = calls.as_array().context("horse_tool_calls_type")?;
        if !calls.is_empty() {
            ensure!(
                transport == Transport::Native && calls.len() == 1 && finish != "length",
                "horse_one_complete_call_required"
            );
            let c = &calls[0];
            ensure!(
                c["type"] == "function" && c["function"]["name"] == action(stage),
                "horse_wrong_stage_or_tool"
            );
            ensure!(
                c["id"]
                    .as_str()
                    .is_some_and(|v| !v.is_empty() && v.len() <= 128),
                "horse_call_id"
            );
            let args = c["function"]["arguments"]
                .as_str()
                .context("horse_arguments_type")?;
            ensure!(args.len() <= 16_384, "horse_arguments_size");
            arguments = Some(serde_json::from_str::<Value>(args)?);
        }
    }
    if arguments.is_none() {
        let content = message["content"].as_str().unwrap_or("");
        if let Ok(mut v) = serde_json::from_str::<Value>(content) {
            let a = v["action"].as_str().unwrap_or("");
            if a == "speak" || a == "silent" {
                return Ok(Decision::Conversation);
            }
            if !a.is_empty() {
                ensure!(
                    transport == Transport::Json && a == action(stage) && finish != "length",
                    "horse_wrong_stage_or_contract"
                );
                v.as_object_mut().context("horse_object")?.remove("action");
                arguments = Some(v);
            }
        }
    }
    let Some(args) = arguments else {
        return Ok(Decision::Conversation);
    };
    Ok(match stage {
        Stage::Intent => Decision::List(serde_json::from_value(args)?),
        Stage::Selection => Decision::Select(serde_json::from_value(args)?),
    })
}
pub fn conversation(transport: Transport, content: &str) -> crate::chat_validation::Outcome {
    if transport == Transport::Json {
        return crate::chat_validation::finish(Some(content));
    }
    if content.trim_start().starts_with('{') {
        return crate::chat_validation::finish(Some("{}"));
    }
    let action = if content.trim().is_empty() {
        "silent"
    } else {
        "speak"
    };
    crate::chat_validation::finish(Some(&json!({"action":action,"speech":content}).to_string()))
}
