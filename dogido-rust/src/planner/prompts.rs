use super::{Action, Details};
use crate::types::{ChatMessage, Role};
use serde::Serialize;
use serde_json::{
    Value, json,
    ser::{CompactFormatter, Formatter},
};
use std::{io, sync::LazyLock};

static ASSETS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("prompts.json")).expect("checked planner assets")
});

// JSONのカンマ・コロン直後に空白を置く共有表記。Python実行への依存はない。
struct PythonSpaces;
impl Formatter for PythonSpaces {
    fn begin_array_value<W: ?Sized + io::Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }
    fn begin_object_key<W: ?Sized + io::Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }
    fn begin_object_value<W: ?Sized + io::Write>(&mut self, writer: &mut W) -> io::Result<()> {
        writer.write_all(b": ")
    }
    fn write_f64<W: ?Sized + io::Write>(&mut self, writer: &mut W, value: f64) -> io::Result<()> {
        CompactFormatter.write_f64(writer, value)
    }
}
/// planner以外の会話・川柳・国語・workshopも使う空白付きJSON。
/// null/boolはJSON表記を維持する。返り値は一部のサイズ上限検査にも使うため、
/// 空白を削る変更は単なる改名ではなくprompt・境界値の変更になる。
pub(crate) fn python_json(value: &Value) -> String {
    let mut output = Vec::new();
    value
        .serialize(&mut serde_json::Serializer::with_formatter(
            &mut output,
            PythonSpaces,
        ))
        .expect("JSON value serializes");
    String::from_utf8(output).expect("JSON is UTF-8")
}

pub fn messages(details: &Details, retry: Option<(&[String], &Value)>) -> Vec<ChatMessage> {
    let pending = &details.pending_repair;
    let slots = json!({"allowed_actions": details.allowed_actions, "history": details.history,
        "current": details.current, "pending_repair": pending, "routing_hints": details.routing_hints,
        "observations": details.observations,
        "target_evidence": {"turn_id": pending["repair_target_turn_id"], "quote": pending["repair_target_quote"]}});
    let render = |template: &Value| ChatMessage {
        role: if template["role"] == "system" {
            Role::System
        } else {
            Role::User
        },
        content: template["segments"]
            .as_array()
            .unwrap()
            .iter()
            .map(|part| {
                if let Some(literal) = part["literal"].as_str() {
                    literal.into()
                } else {
                    python_json(&slots[part["slot"].as_str().unwrap()])
                }
            })
            .collect::<String>(),
    };
    let name = if details
        .allowed_actions
        .contains(&Action::RepairConversation)
    {
        "repair"
    } else {
        "normal"
    };
    let mut result: Vec<_> = ASSETS[name]
        .as_array()
        .unwrap()
        .iter()
        .map(render)
        .collect();
    if pending.as_object().is_some_and(|p| !p.is_empty()) {
        result.push(render(&ASSETS["pending"]));
    }
    if let Some((errors, previous)) = retry {
        let mut actions: Vec<_> = details
            .allowed_actions
            .iter()
            .map(|a| {
                serde_json::to_value(a)
                    .unwrap()
                    .as_str()
                    .unwrap()
                    .to_owned()
            })
            .collect();
        actions.sort();
        actions.dedup();
        let mut constraints = vec![format!("actionは次から選ぶ: {}", python_json(&json!(actions))), "evidence.turn_idとquoteはhistory/currentにある発話IDと連続部分を正確にコピーし、currentを必ず含める。entity_queryもevidence.quote内の連続部分にする".into()];
        if details.routing_hints["inventory_question"] == true
            || details.routing_hints["sound_question"] == true
        {
            constraints.push("routing_hintsで確定済みの所持品または音の問いなので、actionはanswer_observationにする".into());
        }
        if details.routing_hints["presence_question"] == true {
            constraints.push("routing_hintsで明示的な在否問いと確定済みなので、actionはcheck_entity_presenceにする".into());
        }
        if details.routing_hints["plain_presence_report"] == true {
            constraints.push("routing_hintsでplayerの平叙存在報告と確定済みなので、check_entity_presenceまたはidentify_entityへ変えない".into());
        }
        let previous: String = python_json(previous).chars().take(2400).collect();
        result.push(ChatMessage { role: Role::User, content: format!("前回の返答は、内容の採否以前に現行JSON契約へ一致しなかった。\n契約不一致: {}\n前回のJSON: {previous}\n現行JSON Schema:\n{}\nリクエスト固有制約:\n{}\n最初に示した同じ入力をもう一度判定し、プロンプトに記載された現行の形で、JSONオブジェクトを1つだけ返す。旧形式、説明文、コードフェンスは禁止。候補にないIDや値を補作しない。", errors.join("、"), ASSETS["schema"], constraints.join("\n")) });
    }
    result
}
