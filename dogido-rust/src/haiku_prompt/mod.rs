//! 自動川柳6種の読み取り専用prompt組立。生成・検査・再生成・辞書は呼出側の所有。
use crate::text_format::{self, ContainerFormat::SpacedJson};
mod blocks;
#[cfg(test)]
mod tests;
use crate::{
    haiku::StructuredRequest,
    text_format::spaced_json,
    types::{ChatMessage, Role},
};
use anyhow::{Context, Result, ensure};
use blocks::*;
use serde_json::{Map, Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
};

static ASSETS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("templates.json")).expect("checked haiku prompt assets")
});
fn asset(key: &str) -> &'static str {
    ASSETS[key].as_str().expect("checked literal")
}
fn render(parts: &Value, slots: &BTreeMap<String, String>) -> Result<String> {
    parts
        .as_array()
        .context("invalid prompt template")?
        .iter()
        .map(|p| {
            if let Some(s) = p["literal"].as_str() {
                Ok(s.to_owned())
            } else {
                Ok(slots
                    .get(p["slot"].as_str().context("invalid prompt slot")?)
                    .context("missing prompt slot")?
                    .clone())
            }
        })
        .collect()
}
fn row_lines(value: &Value, f: impl Fn(&Value) -> String) -> String {
    if value.is_array() {
        array(value)
            .iter()
            .filter(|r| r.is_object())
            .map(f)
            .collect::<Vec<_>>()
            .join("\n")
    } else {
        "なし".into()
    }
}
fn grounding_example(d: &Value) -> Value {
    let requested: Vec<_> = array(&d["grounding_lines"])
        .iter()
        .filter(|r| r.is_object() && integer(&r["line_index"]))
        .map(|r| r["line_index"].clone())
        .collect();
    let numbers = d["grounding_atom_numbers"].as_object();
    let mut example_numbers: Vec<_> = array(&d["source_atoms"])
        .iter()
        .filter_map(|r| r["atom_id"].as_str())
        .filter_map(|id| numbers.and_then(|n| n.get(id)).cloned())
        .collect();
    if example_numbers.is_empty() {
        example_numbers.push(json!(1));
    }
    let mut verdicts = Map::new();
    for index in &requested {
        verdicts.insert(text_format::value_text(index, SpacedJson), json!("pass"));
    }
    let assessments:Vec<_>=requested.iter().enumerate().map(|(p,i)|json!({"line_index":i,"atom_ids":[example_numbers[p.min(example_numbers.len()-1)].clone()]})).collect();
    json!({"verdicts":verdicts,"assessments":assessments,"failure_reasons":{}})
}

pub fn messages(request: &StructuredRequest) -> Result<Vec<ChatMessage>> {
    let kind = request.kind.as_str();
    ensure!(
        matches!(
            kind,
            "haiku_irony"
                | "haiku_scene"
                | "haiku_draft"
                | "haiku_line_grounding"
                | "haiku_line_regeneration"
                | "haiku_workshop_revision"
        ),
        "unsupported haiku kind"
    );
    // Read the existing StructuredRequest fields without changing model or token settings.
    let d = Value::Object(request.details.clone());
    let mut slots = BTreeMap::new();
    let structure = structure(&d);
    let poetic = array(&d["source_atoms"])
        .iter()
        .any(|a| a["kind"] == "poetic_interpretation");
    slots.insert(
        "spirit".into(),
        ASSETS["spirit"][format!("{}{}", u8::from(structure), u8::from(poetic))]
            .as_str()
            .unwrap()
            .into(),
    );
    slots.insert("form".into(), asset("form").into());
    let strategy = if truth(&d["generation_strategy"]) {
        text_format::value_text(&d["generation_strategy"], SpacedJson)
    } else {
        "three_slot".into()
    };
    slots.insert(
        "generation_strategy".into(),
        ASSETS["strategy"][if ASSETS["strategy"].get(&strategy).is_some() {
            &strategy
        } else {
            "three_slot"
        }]
        .as_str()
        .unwrap()
        .into(),
    );
    let nudge = match (kind, structure) {
        ("haiku_irony", true) => "いまいる場所の空気を大切に。",
        ("haiku_irony", false) => "いきものや自然の手触りを大切に。",
        (_, true) => "場所の気配をひと場面に。",
        (_, false) => "なんでもない午後でも、空気が見えればそれでよい。",
    };
    slots.insert("place_nudge".into(), nudge.into());
    slots.insert("materials".into(), materials(&d));
    slots.insert("candidates".into(), list_lines(&d["feature_candidates"]));
    slots.insert("tensions".into(), list_lines(&d["candidate_tensions"]));
    slots.insert("scene_block".into(), scene(&d));
    let irony = &d["irony"];
    let irony_text = if irony.is_object() && truth(&irony["description"]) {
        let focus = join(&irony["focus"], "、");
        let focus = if focus.is_empty() { "—" } else { &focus };
        if kind == "haiku_draft" {
            format!(
                "{}（焦点: {focus}）",
                text_format::value_text(&irony["description"], SpacedJson)
            )
        } else {
            format!(
                "{} / 焦点: {focus}",
                text_format::value_text(&irony["description"], SpacedJson)
            )
        }
    } else {
        if kind == "haiku_scene" {
            "まだなし"
        } else {
            "なし"
        }
        .into()
    };
    slots.insert("irony_block".into(), irony_text);
    slots.insert("constraint_section".into(), constraint_section(&d));
    slots.insert("constraints".into(), constraint_section(&d));
    slots.insert("source_atoms".into(), source_atoms(&d));
    slots.insert("atoms".into(), source_atoms(&d));
    slots.insert("grounding_scene".into(), grounding_scene(&d));
    slots.insert("workshop_context".into(), context(&d));
    slots.insert(
        "revision_block".into(),
        if d["revision_edits"].is_array() {
            format!(
                "【今回の修正差分】\n{}{}",
                spaced_json(&d["revision_edits"]),
                asset("grounding_revision_note")
            )
        } else {
            String::new()
        },
    );
    slots.insert(
        "line_block".into(),
        row_lines(&d["grounding_lines"], |r| {
            format!(
                "- {}: {}",
                text_format::value_text(&r["line_index"], SpacedJson),
                text_format::value_text(&r["text"], SpacedJson)
            )
        }),
    );
    slots.insert(
        "json_tail".into(),
        format!(
            "返事は JSON オブジェクト1つだけ。\n形: {}",
            spaced_json(&grounding_example(&d))
        ),
    );
    slots.insert(
        "current".into(),
        if kind == "haiku_workshop_revision" {
            row_lines(&d["current_lines"], |r| {
                format!(
                    "- {}: {} ({})",
                    text_format::value_text(&r["line_index"], SpacedJson),
                    text_format::value_text(&r["text"], SpacedJson),
                    if truth(&r["frozen"]) {
                        "固定"
                    } else {
                        "修正対象"
                    }
                )
            })
        } else {
            row_lines(&d["current_lines"], regeneration_line)
        },
    );
    let targets = array(&d["failed_line_indices"])
        .iter()
        .filter(|v| integer(v) || v.is_boolean())
        .map(|v| {
            if let Some(b) = v.as_bool() {
                u8::from(b).to_string()
            } else {
                text_format::value_text(v, SpacedJson)
            }
        })
        .collect::<Vec<_>>()
        .join(", ");
    slots.insert("targets".into(), or_none(targets));
    slots.insert(
        "target_text".into(),
        if d["target_line_indices"].is_array() {
            array(&d["target_line_indices"])
                .iter()
                .map(|value| text_format::value_text(value, SpacedJson))
                .collect::<Vec<_>>()
                .join(", ")
        } else {
            "なし".into()
        },
    );
    slots.insert(
        "finding_lines".into(),
        row_lines(&d["workshop_findings"], |r| {
            format!(
                "- 行{}: {} / {}",
                text_format::value_text(&r["line_index"], SpacedJson),
                text_format::value_text(&r["problem"], SpacedJson),
                text_format::value_text(&r["note"], SpacedJson)
            )
        }),
    );
    slots.insert(
        "atom_lines".into(),
        row_lines(&d["source_atoms"], |r| {
            format!(
                "- [{}] {}",
                text_format::value_text(&r["atom_id"], SpacedJson),
                text_format::value_text(&r["text"], SpacedJson)
            )
        }),
    );
    slots.insert("retry_block".into(), edit_retry(&d));
    let mut result: Vec<ChatMessage> = ASSETS[kind]
        .as_array()
        .context("missing haiku template")?
        .iter()
        .map(|t| {
            Ok(ChatMessage {
                role: if t["role"] == "system" {
                    Role::System
                } else {
                    Role::User
                },
                content: crate::companion_prompt::expand(&render(&t["segments"], &slots)?),
            })
        })
        .collect::<Result<_>>()?;
    // The pinned grounding builder intentionally bypasses generic structured retry additions.
    let retry = &d["__dogido_structured_contract_retry"];
    if kind != "haiku_line_grounding" && retry.is_object() {
        let errors = if retry["errors"].is_array() {
            join(&retry["errors"], "、")
        } else {
            "現行JSON契約との不一致".into()
        };
        slots.insert("errors".into(), errors);
        let previous = if truth(&retry["previous_payload"]) {
            text_format::value_text(&retry["previous_payload"], SpacedJson)
        } else {
            String::new()
        };
        slots.insert("previous".into(), cut(&previous, 2400));
        let ids: BTreeSet<_> = array(&d["source_atoms"])
            .iter()
            .filter_map(|a| a["atom_id"].as_str())
            .filter(|s| !s.is_empty())
            .collect();
        slots.insert("ids".into(), spaced_json(&json!(ids)));
        let indices: Vec<_> = array(&d["failed_line_indices"])
            .iter()
            .filter(|v| integer(v))
            .cloned()
            .collect();
        slots.insert("indices".into(), spaced_json(&json!(indices)));
        result.push(ChatMessage {
            role: Role::User,
            content: render(&ASSETS["retry"][kind], &slots)?,
        });
    }
    if kind == "haiku_workshop_revision" {
        result
            .last_mut()
            .unwrap()
            .content
            .push_str(asset("revision_suffix"));
    }
    if matches!(kind, "haiku_draft" | "haiku_line_regeneration")
        && truth(&d["background_companions"])
    {
        result.last_mut().unwrap().content.push_str(&format!(
            "\n同行ペットの補助材料: {}\n主題は今回選んだ情景を優先。この個体は必要なら添景に使える。名前と種名は同じ個体の別表現。\n",
            spaced_json(&d["background_companions"])));
    }
    Ok(result)
}
