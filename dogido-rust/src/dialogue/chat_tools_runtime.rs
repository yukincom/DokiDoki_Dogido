//! 通常会話一回に既存の呼び寄せ入口を公開する。位置は現在観測から読み取る。
//! 本人が呼び寄せを依頼した場合だけ候補選択へ進み、実行前検証を既存runtimeへ委ねる。
use super::{bridge, horse_recall_runtime};
use crate::{
    assist::horse_recall::intent::{self, Decision, Stage},
    llm::RigLlm,
    types::{GenerationRequest, Role},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use tokio::sync::watch;

fn prepare(
    mut request: GenerationRequest,
    input: &bridge::Input,
) -> Result<(GenerationRequest, Vec<Value>)> {
    let system = request
        .messages
        .iter_mut()
        .find(|m| m.role == Role::System)
        .context("chat_system_required")?;
    let old = crate::companion_prompt::choice_contract();
    ensure!(
        system.content.matches(old).count() == 1,
        "chat_choice_contract_required"
    );
    system.content = system
        .content
        .replacen(old, crate::companion_prompt::chat_tools(), 1);
    let mut tools = Vec::new();
    // TODO(request-classifier): recall_mount 接続後は通常会話へ list_horses を公開する
    // この分岐と、下の render の Stage::Intent 復号／Decision::List 分岐を撤去する。
    // 受理済み依頼は既存の個体選択へ接続し、通常会話の本文生成は維持する。
    if input.horse_recall.is_some() {
        tools.push(intent::definition(Stage::Intent));
        system.content.push('\n');
        system.content.push_str(&intent::guidance());
    }
    let user = request
        .messages
        .iter_mut()
        .rev()
        .find(|m| m.role == Role::User)
        .context("chat_user_required")?;
    user.content.push_str(&format!(
        "\n今回発話の原文: {}\n",
        input["text"].as_str().unwrap_or("")
    ));
    request.max_tokens = request.max_tokens.max(192);
    Ok((request, tools))
}
fn output(out: crate::chat_validation::Outcome, reports: Vec<Value>) -> Value {
    json!({"op":"result","text":out.text,"dialogue_action":out.action,
        "dialogue_outcome":out.status,"dialogue_reason":out.reason,"llm_reports":reports})
}
fn live(cancel: &watch::Receiver<bool>) -> Result<()> {
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    Ok(())
}
pub(super) async fn render(
    request: GenerationRequest,
    llm: &RigLlm,
    input: &bridge::Input,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Value> {
    live(cancel)?;
    let (first, tools) = prepare(request.clone(), input)?;
    let response = tokio::select! {biased;
        _=bridge::cancelled(cancel)=>anyhow::bail!("cancelled"),
        r=llm.generate_with_tools(&first,&tools,&[],true)=>r,
    };
    live(cancel)?;
    let (report, native) = match response {
        Ok(v) => v,
        Err(e) => {
            return Ok(output(
                crate::chat_validation::finish(None),
                vec![json!({"kind":"player_chat","error":e.to_string()})],
            ));
        }
    };
    let message = native.unwrap_or_else(|| json!({"content":report.generated.text}));
    let finish = report.generated.finish_reason.as_deref().unwrap_or("");
    let horse = intent::decode(Stage::Intent, &message, finish);
    let reports = vec![json!(report)];
    match horse {
        // 未分類のまま警告で中断した入力も再解釈するが、操作だと判明したら終端する。
        // 元の会話一回生成を使い、候補選択や実行を自動再開しない。
        Ok(Decision::List(_)) if input["resumed_reply"] == true => {
            Ok(json!({"op":"result","unsupported":"interrupted_operation","llm_reports":reports}))
        }
        Ok(Decision::List(intent)) if input.horse_recall.is_some() => {
            horse_recall_runtime::render_selection(
                request,
                llm,
                input.horse_recall.as_ref().unwrap(),
                &intent,
                reports,
                cancel,
            )
            .await
        }
        Ok(Decision::Conversation) => Ok(output(
            intent::conversation(message["content"].as_str().unwrap_or("")),
            reports,
        )),
        _ => Ok(output(crate::chat_validation::finish(Some("{}")), reports)),
    }
}
