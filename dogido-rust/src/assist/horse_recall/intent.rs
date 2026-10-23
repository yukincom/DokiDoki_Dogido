//! BERTで受理した依頼について、候補から一頭の選択をnative toolで受け取る。
//! BERTへ変更済み。旧判定へ戻さない。状態変更・移動はcontractsとqueueで検証後に行う。
use super::contracts::{Choice, MountKind, Prepared};
use crate::types::{GenerationRequest, Role};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::sync::LazyLock;
static TEXT: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("prompts.json")).expect("horse prompt assets")
});

pub enum Decision {
    Select(Choice),
    Conversation,
}

/// 原文とBERT受理に結び付いた候補を一回だけ渡す。
/// 名前の表記ゆれと対象の対応はモデルが判断し、UUIDはコード側の固定表にのみ保持する。
pub fn request(
    mut base: GenerationRequest,
    prepared: &Prepared,
) -> Result<(GenerationRequest, Vec<Value>)> {
    let selection_available = prepared
        .candidates
        .iter()
        .any(|horse| prepared.snapshot.eligible(horse));
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
    let key = if !selection_available {
        "native_unavailable"
    } else {
        "native_selection"
    };
    system.content = system.content.replacen(old, TEXT[key].as_str().unwrap(), 1);
    system.content.push('\n');
    system.content.push_str(TEXT["selection"].as_str().unwrap());
    let user = base
        .messages
        .iter_mut()
        .rev()
        .find(|m| matches!(m.role, Role::User))
        .context("horse_user_required")?;
    user.content
        .push_str(&format!("\n今回発話の原文: {}\n", prepared.original));
    let candidates: Vec<Value> = prepared.candidates.iter().enumerate().map(|(i, horse)| json!({
        "candidate_id":format!("h{}",i+1), "name":horse.custom_name, "entity_type":horse.entity_type,
        "eligible":prepared.snapshot.eligible(horse),
        "unavailable_reason":prepared.snapshot.unavailable_reason(horse),
        "recently_ridden":prepared.snapshot.recent_mount_id.as_ref()==Some(&horse.entity_id)
    })).collect();
    user.content.push_str(&format!("【依頼後に取得したMinecraftの乗れる動物候補・データ】\n{}\n{}",
        json!({"status":prepared.snapshot.status,"candidates":candidates,
            "supported_entity_types":MountKind::ALL,
            "unsupported_species_reason":"この一覧の対応種類以外の動物は、呼び寄せ操作の対象外。依頼は受けたが実行できない。名札の名前と動物の種類は別。",
            "observation_scope":"同じ次元で現在読み込まれている個体のみ。遠方のチャンクは読み込まない。",
            "absent_candidate_reason":"一覧にいない原因は未観測。遠距離・別次元・消失・資格不足のどれかは、この一覧だけでは特定できない。"}),
        TEXT[if selection_available { "selection_followup" } else { "selection_unavailable" }].as_str().unwrap()));
    // 呼べる個体が0頭なら操作用の道具を公開しない。同じ会話呼出しへ候補と理由は渡し、
    // 不在の案内・乗員や装備の説明を返させる。名前の対応をコードで決める条件ではない。
    let tools = if !selection_available {
        Vec::new()
    } else {
        vec![definition()]
    };
    Ok((base, tools))
}
pub fn definition() -> Value {
    json!({"type":"function","function":{"name":"select_horse",
        "description":"受付済みの呼び寄せ依頼に合う、呼び寄せ可能な一頭を一覧から選ぶ。曖昧なら使わず質問する。",
        "parameters":{"type":"object","properties":{
            "candidate_id":{"type":"string","description":"現在一覧にある一頭の短いID。名前やUUIDは返さない。"},
            "evidence":{"type":"string","description":"今回の本人の発話全文を、そのまま省略せず返す。"},
            "confidence":{"type":"number","minimum":0,"maximum":1}
        },"required":["candidate_id","evidence","confidence"],"additionalProperties":false}}})
}
/// 一件の完全なnative選択だけを復号し、引数型を検査する。
/// 本文JSONは操作へ変換せず、会話の外形不成立として既存の無発話処理へ渡す。
pub fn decode(message: &Value, finish: &str) -> Result<Decision> {
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
        call["type"] == "function" && call["function"]["name"] == "select_horse",
        "horse_wrong_tool"
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
    Ok(Decision::Select(serde_json::from_str(args)?))
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
