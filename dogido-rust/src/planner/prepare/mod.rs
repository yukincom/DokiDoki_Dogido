//! Code-owned request preparation before the existing bounded planner runner.
//! Inputs are read snapshots: history must already be playback-completed and
//! world observations must not contain player reports or remembered names.
//! No event state, clocks, model calls, or dictionary SDK are used here.
use super::{Action, Details, Evidence, Plan, PreparedPlan, clean, handoff, repair};
use crate::text_format::{self, ContainerFormat::QuotedRepr};
use crate::{chat_catalog, chat_topics, chat_validation};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::{collections::HashSet, sync::LazyLock};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub user_text: String,
    #[serde(default)]
    pub conversation_turns: Value,
    #[serde(default)]
    pub observation_summary: String,
    /// The caller supplies current rows only. Canonical truncation occurs before
    /// de-duplication; non-object rows are rejected by deserialization.
    #[serde(default)]
    pub observed_entities: Vec<Map<String, Value>>,
    #[serde(default)]
    pub look_target_label: String,
    /// Frozen crosshair observation, supplied by code only for a look question.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub look_target: Option<handoff::Observation>,
    #[serde(default)]
    pub hearing_summary: String,
    #[serde(default)]
    pub inventory_question: bool,
    #[serde(default)]
    pub sound_question: bool,
    #[serde(default)]
    pub raw_user_text: Option<String>,
    #[serde(default = "enabled")]
    pub repair_enabled: bool,
}
fn enabled() -> bool {
    true
}

#[derive(Debug, Serialize)]
pub struct PreparedInput {
    pub fallback: Plan,
    /// None for empty input or absent model. Do not turn this into a model call.
    pub request: Option<PreparedPlan>,
}

/// `model=None` is the canonical unavailable/non-callable generator path.
/// The caller owns model configuration and passes the prepared request unchanged
/// to the existing `planner::run`; this function does not add retries or calls.
pub fn prepare(model: Option<&str>, input: &Input) -> PreparedInput {
    let current = clean(&input.user_text, 160);
    let history = normalize_history(&input.conversation_turns);
    let fallback = fallback_plan(
        &current,
        input.inventory_question,
        input.sound_question,
        &history,
    );
    let plain_report =
        plain_presence_report(&current) && fallback.action != Action::CheckEntityPresence;
    let Some(model) = model.filter(|_| !current.is_empty()) else {
        return PreparedInput {
            fallback,
            request: None,
        };
    };
    let raw = input
        .raw_user_text
        .as_deref()
        .unwrap_or(&current)
        .chars()
        .take(1000)
        .collect::<String>();
    let pending = if input.repair_enabled {
        repair::pending(&history)
    } else {
        json!({})
    };
    let may_repair = input.repair_enabled
        && (repair::has_signal(&raw) || pending.as_object().is_some_and(|v| !v.is_empty()));
    let allowed_actions = [
        Action::ContinueConversation,
        Action::CheckEntityPresence,
        Action::IdentifyEntity,
        Action::AnswerObservation,
        Action::ClarifyReference,
        Action::CorrectPreviousReply,
        Action::RepairConversation,
        Action::ClarifyRepair,
    ]
    .into_iter()
    .filter(|action| may_repair || !action.is_repair())
    .collect();
    let mut details = Details {
        allowed_actions,
        history,
        pending_repair: pending,
        observations: json!({
            "summary": clean(&input.observation_summary,600),
            "observed_entities": normalize_observed_entities(&input.observed_entities),
            "look_target_label":clean(&input.look_target_label,80),
            "hearing_summary":clean(&input.hearing_summary,240),
        }),
        routing_hints: json!({
            "inventory_question":input.inventory_question,
            "sound_question":input.sound_question,
            "presence_question":fallback.action==Action::CheckEntityPresence,
            "plain_presence_report":plain_report,
        }),
        current: json!({"turn_id":"current", "role":"user", "text":current, "raw_text":raw}),
    };
    if let Some(target) = &input.look_target {
        details.observations["look_target"] = json!(target);
    }
    let request = PreparedPlan {
        schema_version: 1,
        model: model.into(),
        enable_thinking: false,
        details,
        fallback: fallback.clone(),
    };
    PreparedInput {
        fallback,
        request: Some(request),
    }
}

fn value_text(value: &Value) -> String {
    if chat_catalog::truth(value) {
        text_format::value_text(value, QuotedRepr)
    } else {
        String::new()
    }
}
/// Slice the last ten raw rows before discarding malformed/empty history rows.
/// Repair fields retain scalar text and quoted containers without cleaning/truncation.
pub fn normalize_history(value: &Value) -> Vec<Value> {
    let Some(rows) = value.as_array() else {
        return vec![];
    };
    rows[rows.len().saturating_sub(10)..]
        .iter()
        .enumerate()
        .filter_map(|(index, raw)| {
            let raw = raw.as_object()?;
            let role = value_text(raw.get("role").unwrap_or(&Value::Null));
            let role = chat_catalog::strip(&role);
            let text = clean(&value_text(raw.get("text").unwrap_or(&Value::Null)), 160);
            let turn = raw
                .get("turn_id")
                .filter(|v| chat_catalog::truth(v))
                .map(|value| text_format::value_text(value, QuotedRepr))
                .unwrap_or_else(|| format!("history:{index}:{role}"));
            let turn = clean(&turn, 180);
            let silent = role == "event" && raw.get("reaction").is_some_and(|v| v == "silent");
            if !(matches!(role, "user" | "assistant") || silent)
                || text.is_empty()
                || turn.is_empty()
            {
                return None;
            }
            let mut row = json!({"turn_id":turn,"role":role,"text":text});
            if silent {
                row["reaction"] = "silent".into();
            }
            for key in repair::FIELDS {
                if let Some(value) = raw.get(key) {
                    row[key] = Value::String(text_format::value_text(value, QuotedRepr));
                }
            }
            Some(row)
        })
        .collect()
}
pub fn normalize_observed_entities(rows: &[Map<String, Value>]) -> Vec<handoff::Observation> {
    // Reuse the same operation that the post-planner handoff uses to guard the
    // observation projection. Namespace removal follows whitespace cleaning.
    let rows = rows
        .iter()
        .take(16)
        .map(|raw| handoff::Observation {
            entity_id: value_text(raw.get("entity_id").unwrap_or(&Value::Null)),
            label: value_text(raw.get("label").unwrap_or(&Value::Null)),
        })
        .collect::<Vec<_>>();
    handoff::normalize_prompt_observations(&rows)
}

fn fallback_plan(text: &str, inventory: bool, sound: bool, history: &[Value]) -> Plan {
    let challenge = presence_challenge(text, history);
    let mut evidence = vec![Evidence {
        turn_id: "current".into(),
        quote: if text.is_empty() {
            "（入力なし）"
        } else {
            text
        }
        .into(),
    }];
    let (action, focus, query) = if inventory {
        (Action::AnswerObservation, "現在の所持品", String::new())
    } else if sound {
        (
            Action::AnswerObservation,
            "現在または直近の音の観測",
            String::new(),
        )
    } else if let Some((label, previous)) = &challenge {
        if let Some(previous) = previous {
            evidence.push(Evidence {
                turn_id: previous["turn_id"].as_str().unwrap().into(),
                quote: previous["text"].as_str().unwrap().into(),
            });
        }
        (
            Action::CheckEntityPresence,
            "プレイヤーの見えないという指摘を現在観測と照合",
            label.clone(),
        )
    } else if explicit_presence_question(text) || exact_structure_presence_question(text) {
        (Action::CheckEntityPresence, "現在の対象の在否", text.into())
    } else if chat_topics::has_identify_intent(text) {
        (
            Action::IdentifyEntity,
            "プレイヤーが示した対象の同定",
            text.into(),
        )
    } else {
        (
            Action::ContinueConversation,
            "直近の会話への返答",
            String::new(),
        )
    };
    Plan {
        action,
        focus: focus.into(),
        entity_query: query,
        evidence,
        confidence: 0.0,
        source: "fallback".into(),
        status: "fallback".into(),
        repair: None,
        presence_challenged: challenge.is_some() && action == Action::CheckEntityPresence,
    }
}
const DENIAL: &str =
    r"(?:見え(?:ない|ん|へん)|見当た(?:らない|らん|らへん)|いない|おらん|おらへん)";
const ENDING: &str =
    r"(?:んじゃない|じゃない|ん|の|か|よ|ぞ|ね|けど|やん|じゃん|で|だ|[?？!！。\s\x1c-\x1f])*";
static QUOTED: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"「[^」]*」|『[^』]*』|"[^"]*"|“[^”]*”"#).unwrap());
static DENIAL_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(DENIAL).unwrap());
static HYPOTHETICAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"もし|なら|たら|場合|とき|時|って|と言|かもしれ|わけでは|わけじゃ").unwrap()
});
static PLAIN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"が(?:まだ|今も|いまも|もう)?(?:いる|居る|おる|いない|おらん|ある|ない)").unwrap()
});
static IMPLICIT_DENIAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(r"\A(?:いや|でも|それ|あれ|どこにも|今は|今も|もう|え|、|，|\s|[\x1c-\x1f])*{DENIAL}{ENDING}\z")).unwrap()
});
fn labels(text: &str) -> Vec<String> {
    let mut seen = HashSet::new();
    chat_validation::mentioned(text)
        .into_iter()
        .filter(|label| seen.insert(label.clone()))
        .collect()
}
fn presence_challenge<'a>(text: &str, history: &'a [Value]) -> Option<(String, Option<&'a Value>)> {
    let unquoted = QUOTED.replace_all(text, "");
    let denial = DENIAL_RE.find(&unquoted)?;
    if HYPOTHETICAL.is_match(&unquoted) {
        return None;
    }
    let labels = labels(&unquoted);
    let previous = history.last().filter(|row| row["role"] == "assistant");
    let previous_labels = previous
        .map(|row| self::labels(row["text"].as_str().unwrap()))
        .unwrap_or_default();
    if labels.len() == 1 {
        let suffix=Regex::new(&format!(r"{}ー?(?:が|は|も|なんぞ|なんて|なんか)?(?:今は|今も|もう|どこにも|全然|ぜんぜん|全く|まったく|\s|[\x1c-\x1f])*{DENIAL}{ENDING}$",regex::escape(&labels[0]))).unwrap();
        if !suffix.is_match(&unquoted) {
            return None;
        }
        if denial.as_str().starts_with("見え")
            || denial.as_str().starts_with("見当た")
            || previous_labels.contains(&labels[0])
        {
            return Some((labels[0].clone(), None));
        }
    } else if labels.is_empty() && previous_labels.len() == 1 && IMPLICIT_DENIAL.is_match(&unquoted)
    {
        return Some((previous_labels[0].clone(), previous));
    }
    None
}
fn has(text: &str, parts: &[&str]) -> bool {
    parts.iter().any(|part| text.contains(part))
}
fn explicit_presence_question(text: &str) -> bool {
    let text = chat_catalog::strip(text);
    if text.is_empty() || !has(text, &["いる", "おる", "居る", "いない", "おらん", "気配"])
    {
        return false;
    }
    if has(text, &["？", "?"])
        || [
            "いるの",
            "おるの",
            "いるか",
            "おるか",
            "いるん",
            "おるん",
            "いないの",
            "おらんの",
            "いないか",
            "おらんか",
            "いるかな",
            "おるかな",
            "いないかな",
            "おらんかな",
        ]
        .iter()
        .any(|ending| text.ends_with(ending))
    {
        return true;
    }
    !PLAIN.is_match(text) && has(text, &["まだ", "今も", "いまも"])
}
fn exact_structure_presence_question(text: &str) -> bool {
    let text = chat_catalog::strip(text);
    if text.is_empty()
        || !(has(text, &["？", "?"])
            || [
                "あるか",
                "あるの",
                "あるかな",
                "ないか",
                "ないの",
                "ないかな",
                "見えるか",
                "見えるの",
                "見えるかな",
            ]
            .iter()
            .any(|ending| text.ends_with(ending)))
        || !has(text, &["ある", "ない", "見える"])
    {
        return false;
    }
    // With no observation boost, this is find_catalog_topics' default query.
    // The shared policy filter excludes generic scenery terms before Kind checks.
    let hits = chat_catalog::catalog().player_chat_topics(text, &[]);
    let topics = hits
        .iter()
        .map(|hit| chat_topics::Topic {
            entry_id: hit.entry_id.clone(),
            label_ja: hit.label_ja.clone(),
            matched_terms: hit.matched_terms.clone(),
            score: hit.score,
        })
        .collect::<Vec<_>>();
    chat_topics::usable_topic_indices(&topics)
        .into_iter()
        .any(|index| hits[index].kind == chat_catalog::Kind::Structure)
}
fn plain_presence_report(text: &str) -> bool {
    let text = chat_catalog::strip(text);
    !text.is_empty()
        && !explicit_presence_question(text)
        && !exact_structure_presence_question(text)
        && PLAIN.is_match(text)
}

#[cfg(test)]
mod tests;
