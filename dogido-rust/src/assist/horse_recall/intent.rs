//! 候補を見せない依頼判定と、依頼後の一頭選択をnative toolで受け取る。
//! 関数は候補要求と選択に限定し、状態変更・移動はcontractsとqueueで検証後に行う。
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
    original: &str,
    prepared: Option<&Prepared>,
    stage: Stage,
) -> Result<(GenerationRequest, Vec<Value>)> {
    let selection_available =
        prepared.is_some_and(|p| p.candidates.iter().any(|horse| p.snapshot.eligible(horse)));
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
    let key = if stage == Stage::Intent {
        "native_intent"
    } else if !selection_available {
        "native_unavailable"
    } else {
        "native_selection"
    };
    system.content = system.content.replacen(old, TEXT[key].as_str().unwrap(), 1);
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
        .push_str(&format!("\n今回発話の原文: {original}\n"));
    if stage == Stage::Selection {
        let prepared = prepared.context("horse_selection_snapshot_required")?;
        let candidates: Vec<Value> = prepared.candidates.iter().enumerate().map(|(i, horse)| json!({
            "candidate_id":format!("h{}",i+1), "name":horse.custom_name, "entity_type":horse.entity_type,
            "eligible":prepared.snapshot.eligible(horse),
            "unavailable_reason":prepared.snapshot.unavailable_reason(horse),
            "recently_ridden":prepared.snapshot.recent_mount_id.as_ref()==Some(&horse.entity_id)
        })).collect();
        user.content.push_str(&format!("【依頼後に取得したMinecraftの乗れる動物候補・データ】\n{}\n{}",
            json!({"status":prepared.snapshot.status,"candidates":candidates,
                "observation_scope":"同じ次元で現在読み込まれている個体のみ。遠方のチャンクは読み込まない。",
                "absent_candidate_reason":"一覧にいない原因は未観測。遠距離・別次元・消失・資格不足のどれかは、この一覧だけでは特定できない。"}),
            TEXT[if selection_available { "selection_followup" } else { "selection_unavailable" }].as_str().unwrap()));
    } else {
        user.content.push_str("今回の本人が今ここへ呼び寄せるよう依頼したかだけ判断してな。能力や場所の質問・他人の発言の報告・名前だけの呼びかけは依頼に補わず、会話で返してな。");
    }
    // 呼べる個体が0頭なら操作用の道具を公開しない。同じ会話呼出しへ候補と理由は渡し、
    // 不在の案内・乗員や装備の説明を返させる。名前の対応をコードで決める条件ではない。
    let tools = if stage == Stage::Selection && !selection_available {
        Vec::new()
    } else {
        vec![definition(stage)]
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
        "description":if stage==Stage::Intent {"本人から今ここへ乗れる動物を呼ぶ依頼があった場合だけ、乗れる動物の候補一覧を要求する。まだ移動しない。"}
            else {"本人の呼び寄せ依頼に合う、呼び寄せ可能な一頭を一覧から選ぶ。曖昧なら使わず質問する。"},
        "parameters":{"type":"object","properties":properties,"required":required,"additionalProperties":false}}})
}
/// 一件の完全なnative呼出しだけを復号し、段階ごとの引数型を検査する。
/// 本文JSONは操作へ変換せず、会話の外形不成立として既存の無発話処理へ渡す。
pub fn decode(stage: Stage, message: &Value, finish: &str) -> Result<Decision> {
    let Some(calls) = message.get("tool_calls").filter(|v| !v.is_null()) else {
        return Ok(Decision::Conversation);
    };
    let calls = calls.as_array().context("horse_tool_calls_type")?;
    if calls.is_empty() {
        return Ok(Decision::Conversation);
    }
    ensure!(
        calls.len() == 1 && finish != "length",
        "horse_one_complete_call_required"
    );
    let call = &calls[0];
    ensure!(
        call["type"] == "function" && call["function"]["name"] == action(stage),
        "horse_wrong_stage_or_tool"
    );
    ensure!(
        call["id"]
            .as_str()
            .is_some_and(|v| !v.is_empty() && v.len() <= 128),
        "horse_call_id"
    );
    let args = call["function"]["arguments"]
        .as_str()
        .context("horse_arguments_type")?;
    ensure!(args.len() <= 16_384, "horse_arguments_size");
    Ok(match stage {
        Stage::Intent => Decision::List(serde_json::from_str(args)?),
        Stage::Selection => Decision::Select(serde_json::from_str(args)?),
    })
}

/// APIの本文を既存の会話検査へ渡す。toolの代わりに返された本文JSONを発話しない。
pub fn conversation(content: &str) -> crate::chat_validation::Outcome {
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
