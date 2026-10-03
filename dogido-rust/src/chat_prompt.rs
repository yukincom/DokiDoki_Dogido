//! Pure player_chat message assembly. Observation, acceptance and retry remain caller-owned.
use crate::types::{ChatMessage, GenerationRequest, Role};
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::Value;
use std::{collections::HashMap, sync::LazyLock};
static DATA: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("chat_prompt/data.json")).expect("chat prompt assets")
});
const TEXT_FIELDS: &[&str] = &[
    "user_text",
    "player_name",
    "character_mode",
    "mode",
    "reply_stance",
    "reply_policy",
    "threat_summary",
    "biome",
    "place_context",
    "structure_label",
    "time_phase",
    "weather",
    "weather_label",
    "weather_fact",
    "weather_context",
    "player_turn_plan",
    "player_turn_plan_evidence",
    "safety_priority",
    "home_progress",
    "inventory_summary",
    "held_item_label",
    "hearing_summary",
    "observation_summary",
    "look_target_label",
    "catalog_topic_hints",
    "named_entity_description_hints",
    "named_mob_context",
    "plausibility_hints",
    "conversation_history",
    "event_digest",
    "player_chat_plan_action",
    "player_chat_plan_focus",
    "entity_query",
    "entity_grounding_status",
    "required_identification_label",
    "haiku_workshop_open",
    "haiku_workshop_text",
    "haiku_workshop_materials",
];
const BOOL_FIELDS: &[&str] = &[
    "dialogue_choice",
    "combat_active",
    "has_visual_threats",
    "danger_darkness_high",
    "asks_inventory",
    "include_sky_context",
    "world_observation_available",
];
const LIST_FIELDS: &[&str] = &[
    "hearing_named_mobs",
    "hearing_source_labels",
    "entity_candidate_labels",
    "entity_observed_labels",
    "nearby_hostile_types",
    "mob_tactics_notes",
    "safe_hints",
];
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub schema_version: u32,
    pub kind: String,
    pub model: String,
    pub details: Value,
    pub temperature: f64,
    pub max_tokens: u64,
    pub enable_thinking: bool,
}
impl Input {
    pub fn into_request(self) -> Result<GenerationRequest> {
        ensure!(
            self.schema_version == 1 && self.kind == "player_chat" && !self.enable_thinking,
            "invalid chat prompt route"
        );
        let request = GenerationRequest {
            schema_version: self.schema_version,
            kind: self.kind,
            model: self.model,
            messages: messages(&self.details)?,
            temperature: self.temperature,
            max_tokens: self.max_tokens,
            enable_thinking: self.enable_thinking,
        };
        request.validate()?;
        Ok(request)
    }
}
fn valid_details(d: &Value) -> Result<()> {
    for (key, value) in d
        .as_object()
        .context("chat prompt details must be an object")?
    {
        let nullable_text = |v: &Value| v.is_null() || v.is_string();
        let valid = if TEXT_FIELDS.contains(&key.as_str()) {
            nullable_text(value)
        } else if BOOL_FIELDS.contains(&key.as_str()) {
            value.is_null() || value.is_boolean()
        } else if LIST_FIELDS.contains(&key.as_str()) {
            nullable_text(value)
                || value
                    .as_array()
                    .is_some_and(|rows| rows.iter().all(Value::is_string))
        } else if key == "world_context" {
            serde_json::from_value::<crate::conversation_observation::Context>(value.clone())
                .is_ok()
        } else if matches!(key.as_str(), "conversation_repair" | "player_chat_repair") {
            value.is_null()
                || value.as_object().is_some_and(|obj| {
                    obj.iter().all(|(k, v)| {
                        (if key == "conversation_repair" {
                            ["target_turn_id", "target_quote", "replacement_quote"]
                                .contains(&k.as_str())
                        } else {
                            ["candidate", "reason"].contains(&k.as_str())
                        }) && nullable_text(v)
                    })
                })
        } else {
            false
        };
        ensure!(valid, "invalid chat prompt detail: {key}");
    }
    Ok(())
}
/// Canonical policy wording, shared with pure topic selection.
pub fn reply_policy_line(stance: &str) -> &'static str {
    let key = strip(stance).to_lowercase();
    asset(
        DATA["policies"]
            .get(&key)
            .unwrap_or(&DATA["policies"]["none"]),
    )
}

use crate::compat::is_python_whitespace as space;
fn strip(s: &str) -> &str {
    s.trim_matches(space)
}
fn text<'a>(d: &'a Value, key: &str, default: &'a str) -> &'a str {
    d[key]
        .as_str()
        .map(strip)
        .filter(|s| !s.is_empty())
        .unwrap_or(default)
}
fn strings<'a>(d: &'a Value, key: &str) -> Vec<&'a str> {
    match &d[key] {
        Value::String(s) => {
            if strip(s).is_empty() {
                vec![]
            } else {
                vec![strip(s)]
            }
        }
        Value::Array(rows) => rows
            .iter()
            .filter_map(Value::as_str)
            .map(strip)
            .filter(|s| !s.is_empty())
            .collect(),
        _ => vec![],
    }
}
fn flag(d: &Value, key: &str) -> bool {
    d[key] == true
}
fn asset(v: &Value) -> &str {
    v.as_str().expect("chat prompt string asset")
}
// One pass: inserted user text is never scanned again for template markers.
fn fill(template: &str, fields: &[(&str, &str)]) -> String {
    let mut out = String::new();
    let mut tail = template;
    while let Some(start) = tail.find("@@DOGIDO_") {
        out.push_str(&tail[..start]);
        let rest = &tail[start + 9..];
        let Some(end) = rest.find("@@") else {
            out.push_str(&tail[start..]);
            return out;
        };
        let key = &rest[..end];
        let value = fields
            .iter()
            .find(|(k, _)| *k == key)
            .expect("known chat template marker")
            .1;
        out.push_str(value);
        tail = &rest[end + 2..];
    }
    out.push_str(tail);
    out
}
fn section(key: &str, values: &[(&str, &str)]) -> String {
    fill(asset(&DATA[key]), values)
}
fn pair(key: &str, value: &str, marker: &str) -> (String, String) {
    if value.is_empty() {
        return (String::new(), String::new());
    }
    (
        asset(&DATA[key][0]).into(),
        fill(asset(&DATA[key][1]), &[(marker, value)]),
    )
}
pub(crate) fn mode(d: &Value) -> &'static str {
    match text(d, "character_mode", "").to_lowercase().as_str() {
        "normal" => return "normal",
        "base" => return "base",
        "tension" => return "tension",
        "workshop" => {
            return "workshop";
        }
        _ => {}
    }
    let state = text(d, "mode", "normal").to_lowercase();
    if matches!(state.as_str(), "panic" | "suppressed_panic" | "aftermath")
        || flag(d, "combat_active")
        || flag(d, "has_visual_threats")
    {
        "base"
    } else if state == "alert" || flag(d, "danger_darkness_high") {
        "tension"
    } else {
        "normal"
    }
}
/// The input is a closed projection of already selected prompt material, not a world snapshot.
pub fn messages(d: &Value) -> Result<Vec<ChatMessage>> {
    valid_details(d)?;
    let character = mode(d);
    let mut f: HashMap<&str, String> = HashMap::new();
    f.insert(
        "user_text",
        text(d, "user_text", "（聞き取れなかった）").into(),
    );
    f.insert("player_name", text(d, "player_name", "プレイヤー").into());
    let stance = text(d, "reply_stance", "none");
    f.insert("stance", stance.into());
    let policy = text(d, "reply_policy", "");
    f.insert(
        "policy",
        if policy.is_empty() {
            reply_policy_line(stance).into()
        } else {
            policy.into()
        },
    );
    let place = text(
        d,
        "place_context",
        text(d, "structure_label", text(d, "biome", "そのへん")),
    );
    f.insert("place", place.into());
    let inventory = text(d, "inventory_summary", "");
    let (rules, block) = if flag(d, "asks_inventory") && !inventory.is_empty() {
        (
            asset(&DATA["inventory"][0]).into(),
            fill(
                asset(&DATA["inventory"][1]),
                &[
                    ("inventory", inventory),
                    ("held", text(d, "held_item_label", "なし")),
                ],
            ),
        )
    } else {
        (String::new(), String::new())
    };
    f.insert("inventory_rules", rules);
    f.insert("inventory_block", block);
    let (rules, block) = pair("history", text(d, "conversation_history", ""), "history");
    f.insert("history_rules", rules);
    f.insert("history_block", block);
    let (rules, block) = pair("digest", text(d, "event_digest", ""), "digest");
    f.insert("digest_rules", rules);
    f.insert("digest_block", block);
    let named = strings(d, "hearing_named_mobs").join("、");
    let sources = strings(d, "hearing_source_labels").join("、");
    let summary = text(d, "hearing_summary", "");
    f.insert(
        "hearing_block",
        if named.is_empty() && sources.is_empty() && summary.is_empty() {
            String::new()
        } else {
            section(
                "hearing",
                &[
                    (
                        "named",
                        if named.is_empty() {
                            "（なし）"
                        } else {
                            &named
                        },
                    ),
                    (
                        "source",
                        if sources.is_empty() {
                            "（なし）"
                        } else {
                            &sources
                        },
                    ),
                    (
                        "summary",
                        if summary.is_empty() {
                            "（なし）"
                        } else {
                            summary
                        },
                    ),
                ],
            )
        },
    );
    for (field, key, marker) in [
        ("topic_block", "catalog_topic_hints", "topic"),
        ("plausibility_block", "plausibility_hints", "plausibility"),
    ] {
        let v = text(d, key, "");
        f.insert(
            field,
            if v.is_empty() {
                String::new()
            } else {
                section(marker, &[(marker, v)])
            },
        );
    }
    let observation = text(d, "observation_summary", "");
    let look = text(d, "look_target_label", "");
    let mut block = if !observation.is_empty() {
        section("observation", &[("summary", observation)])
    } else {
        String::new()
    };
    if !look.is_empty() && (observation.is_empty() || !observation.contains("視線先")) {
        block.push_str(&section("look", &[("look", look)]));
    }
    f.insert("observation_block", block);
    f.insert(
        "world_observation_rules",
        if d["world_observation_available"] == false {
            asset(&DATA["world"]).into()
        } else {
            String::new()
        },
    );
    let action = text(d, "player_chat_plan_action", "");
    let (rules, block) = if action.is_empty() {
        (String::new(), String::new())
    } else {
        let status = text(d, "entity_grounding_status", "not_applicable");
        let rules = asset(
            DATA["grounding_rules"]
                .get(status)
                .unwrap_or(&DATA["grounding_rules"]["not_applicable"]),
        )
        .into();
        let mut block = format!(
            "{}action: {action}\nentity_status: {status}\n",
            asset(&DATA["grounding_header"])
        );
        let query = text(d, "entity_query", "");
        if !query.is_empty() {
            block.push_str(&format!("entity_query: {query}\n"));
        }
        for (key, label) in [
            ("entity_candidate_labels", "catalog_candidates"),
            ("entity_observed_labels", "observed_entities"),
        ] {
            let rows = strings(d, key);
            if !rows.is_empty() {
                block.push_str(&format!("{label}: {}\n", rows.join("、")));
            }
        }
        (rules, block)
    };
    f.insert("grounding_rules", rules);
    f.insert("grounding_block", block);
    f.insert(
        "repair_block",
        if d["conversation_repair"].is_object() {
            section(
                "conversation_repair",
                &[(
                    "repair",
                    &crate::planner::python_json(&d["conversation_repair"]),
                )],
            )
        } else {
            String::new()
        },
    );
    let home = text(d, "player_turn_plan", "none") == "return_home";
    let safe = text(d, "safety_priority", "none") == "seek_safe_place";
    let evidence = text(d, "player_turn_plan_evidence", "");
    let progress = text(d, "home_progress", "unknown");
    let progress = if ["approaching", "leaving", "at_home"].contains(&progress) {
        progress
    } else {
        "unknown"
    };
    let key = format!(
        "{}{}{}:{progress}",
        u8::from(home),
        u8::from(safe),
        u8::from(!evidence.is_empty())
    );
    f.insert("priority_rules", asset(&DATA["priority"][&key][0]).into());
    f.insert(
        "priority_block",
        fill(asset(&DATA["priority"][&key][1]), &[("evidence", evidence)]),
    );
    let (weather, time) = if d["include_sky_context"] == false {
        (String::new(), String::new())
    } else {
        let fact = text(d, "weather_fact", "");
        let context = text(d, "weather_context", "");
        let key = format!(
            "{}{}",
            u8::from(!fact.is_empty()),
            u8::from(!context.is_empty())
        );
        (
            fill(
                asset(&DATA["weather"][&key]),
                &[
                    (
                        "weather",
                        text(d, "weather_label", text(d, "weather", "不明")),
                    ),
                    ("fact", fact),
                    ("context", context),
                ],
            ),
            section("time", &[("time", text(d, "time_phase", "unknown"))]),
        )
    };
    f.insert("weather_block", weather);
    f.insert("time_block", time);
    let world_block = if d["world_context"].is_object() {
        let mut world = d["world_context"].clone();
        let knowledge = world
            .as_object_mut()
            .unwrap()
            .shift_remove("catalog_knowledge");
        let routines = world
            .as_object_mut()
            .unwrap()
            .shift_remove("villager_routines");
        let mut block = format!(
            "【現在の環境観測と変化（会話履歴とは別）】\n{}\n",
            crate::planner::python_json(&world)
        );
        if let Some(knowledge) = knowledge.filter(|value| !value.is_null()) {
            block.push_str(&format!(
                "【対象のカタログ情報（一般的特徴・表現材料）】\n{}\n",
                crate::planner::python_json(&knowledge)
            ));
        }
        if let Some(routines) = routines.filter(|value| !value.is_null()) {
            block.push_str(&format!(
                "【観測範囲の村人の日課（時刻からの予定）】\n{}\n",
                crate::planner::python_json(&routines)
            ));
        }
        block
    } else {
        format!("場所: {place}\n{}{}", f["time_block"], f["weather_block"])
    };
    f.insert("world_context_block", world_block);
    let block = if text(d, "haiku_workshop_open", "").is_empty() {
        String::new()
    } else {
        let verse = text(d, "haiku_workshop_text", "");
        let materials = text(d, "haiku_workshop_materials", "");
        let key = format!(
            "{}{}",
            u8::from(!verse.is_empty()),
            u8::from(!materials.is_empty())
        );
        fill(
            asset(&DATA["workshop"][&key]),
            &[("verse", verse), ("materials", materials)],
        )
    };
    f.insert("haiku_workshop_block", block);
    let nearby = strings(d, "nearby_hostile_types");
    let in_hostile = flag(d, "has_visual_threats")
        || flag(d, "combat_active")
        || !nearby.is_empty()
        || stance == "saw";
    let block = if !in_hostile {
        String::new()
    } else if nearby.is_empty() {
        asset(&DATA["combat_without_nearby"]).into()
    } else {
        let notes = strings(d, "mob_tactics_notes");
        let hints = strings(d, "safe_hints");
        let key = format!(
            "{}{}",
            u8::from(!notes.is_empty()),
            u8::from(!hints.is_empty())
        );
        fill(
            asset(&DATA["combat"][&key]),
            &[
                (
                    "notes",
                    &notes.into_iter().take(3).collect::<Vec<_>>().join(" / "),
                ),
                (
                    "hints",
                    &hints.into_iter().take(5).collect::<Vec<_>>().join(" / "),
                ),
            ],
        )
    };
    f.insert("combat_safety_rules", block);
    let mut user = String::new();
    for part in DATA["template"].as_array().expect("chat template") {
        if let Some(s) = part["text"].as_str() {
            user.push_str(s);
        } else {
            user.push_str(
                f.get(asset(&part["field"]))
                    .context("chat template field")?,
            );
        }
    }
    let descriptions = text(d, "named_entity_description_hints", "");
    let individuals = text(d, "named_mob_context", "");
    if !individuals.is_empty() {
        user.push_str(&format!("\n【話題の個体の名前と種類（JSONの値は観測データ）】\n{individuals}\n名前で呼び、種類の特徴にも沿って会話する。過去の対応は現在の在否の根拠にしない。プレイヤーから聞いた出来事には自然に応じ、自分が目撃したことにはしない。\n"));
    }
    if !descriptions.is_empty() {
        user.push_str(&format!("\n【今回名前が出た種類の辞書描写ヒント】\n{descriptions}\n種類の描写の参考。現在の存在・視認・行動の証拠にはしない。会話履歴の別の対象の描写を引き継がず、この対象に沿って返す。\n"));
    }
    let identification = text(d, "required_identification_label", "");
    if !identification.is_empty() {
        user.push_str(&format!(
            "\n名前を尋ねられた照準先の対象は「{identification}」。まずこの名前を答える。\n"
        ));
    }
    let focus = text(d, "player_chat_plan_focus", "");
    if !focus.is_empty() {
        user.push_str(&format!(
            "\n今回の発言の焦点: {focus}\n最新のプレイヤー発言:「{}」。周囲の観測も会話につなげてええで。\n",
            text(d, "user_text", "")
        ));
    }
    let system = if flag(d, "dialogue_choice") {
        crate::companion_prompt::dialogue(character)
    } else {
        crate::companion_prompt::speech(character)
    };
    let mut out = vec![
        ChatMessage {
            role: Role::System,
            content: if descriptions.is_empty() {
                system
            } else {
                format!(
                    "{}\n今回の話題の種類と辞書描写: {descriptions}。特徴を話す場合はこの描写に沿うこと。前の返答にあった別対象の描写を繰り返さない。これは種類の一般的な描写の参考であり、現在の視認・存在・行動は観測欄だけで判断する。",
                    system
                )
            },
        },
        ChatMessage {
            role: Role::User,
            content: user,
        },
    ];
    if let Some(repair) = d["player_chat_repair"].as_object() {
        let candidate = repair
            .get("candidate")
            .and_then(Value::as_str)
            .map(strip)
            .filter(|s| !s.is_empty())
            .unwrap_or(asset(&DATA["empty_candidate"]));
        // The Python reason keeps surrounding spaces; only an empty/null value defaults.
        let reason = repair
            .get("reason")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .unwrap_or("surface_style_mismatch");
        let feedback = if reason == "missing_identification_name" {
            "対象の名前を答えていません。材料にある照準先の名前を先に答えてください。"
        } else {
            asset(
                DATA["repair_feedback"]
                    .get(reason)
                    .unwrap_or(&DATA["repair_feedback"]["surface_style_mismatch"]),
            )
        };
        out.push(ChatMessage {
            role: Role::Assistant,
            content: candidate.into(),
        });
        out.push(ChatMessage {
            role: Role::User,
            content: if flag(d, "dialogue_choice") {
                format!("いまの出力にコードの検査結果が返ったで: {reason}。{feedback}。元の会話・観測を踏まえて一度だけ考え直してな。話すか黙るかを、最初と同じ action/speech のJSON一個で返す。")
            } else { section("repair_user", &[("feedback", feedback)]) },
        });
    }
    Ok(out)
}

/// Same closed field projection used by the transitional Python helper.
pub(crate) fn project_details(details: &Value) -> Value {
    Value::Object(
        details
            .as_object()
            .expect("native chat details")
            .iter()
            .filter(|(k, _)| {
                TEXT_FIELDS.contains(&k.as_str())
                    || BOOL_FIELDS.contains(&k.as_str())
                    || LIST_FIELDS.contains(&k.as_str())
                    || matches!(
                        k.as_str(),
                        "conversation_repair" | "player_chat_repair" | "world_context"
                    )
            })
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn initial_and_repair_messages_match_python() {
        let fixture: Value =
            serde_json::from_str(include_str!("chat_prompt/fixtures.json")).unwrap();
        let pool = fixture["pool"].as_array().unwrap();
        for (index, row) in fixture["cases"].as_array().unwrap().iter().enumerate() {
            let details = &pool[row[0].as_u64().unwrap() as usize];
            let expected: Vec<Value> = row[1]
                .as_array()
                .unwrap()
                .iter()
                .map(|id| pool[id.as_u64().unwrap() as usize].clone())
                .collect();
            let actual = serde_json::to_value(messages(details).unwrap()).unwrap();
            assert_eq!(
                actual,
                serde_json::json!(expected),
                "case {index} details={details}"
            );
        }
    }
    #[test]
    fn closed_projection_rejects_unknown_fields_and_invalid_types() {
        for details in [
            json!({"messages":[]}),
            json!({"user_text":true}),
            json!({"combat_active":"false"}),
            json!({"nearby_hostile_types":[2]}),
            json!({"player_chat_repair":{"extra":1}}),
            json!({"conversation_repair":{"world_observed":true}}),
        ] {
            assert!(messages(&details).is_err(), "{details}");
        }
        let input = json!({"schema_version":1,"kind":"player_chat","model":"fixture","max_tokens":72,"temperature":0.65,"enable_thinking":false,"details":{}});
        let mut wrong = input.clone();
        wrong["messages"] = json!([]);
        assert!(serde_json::from_value::<Input>(wrong).is_err());
        for (key, value) in [
            ("kind", json!("ambient")),
            ("enable_thinking", json!(true)),
            ("schema_version", json!(2)),
        ] {
            let mut wrong = input.clone();
            wrong[key] = value;
            assert!(
                serde_json::from_value::<Input>(wrong)
                    .unwrap()
                    .into_request()
                    .is_err()
            );
        }
        let request = serde_json::from_value::<Input>(input)
            .unwrap()
            .into_request()
            .unwrap();
        assert_eq!(request.max_tokens, 72);
        assert_eq!(request.temperature, 0.65);
        assert_eq!(request.messages.len(), 2);
    }
}
