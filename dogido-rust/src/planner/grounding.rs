use super::{Action, Plan, clean, string};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Grounding {
    pub status: String,
    pub query: String,
    pub candidate_ids: Vec<String>,
    pub candidate_labels: Vec<String>,
    pub observed_ids: Vec<String>,
    pub observed_labels: Vec<String>,
}
fn entity_id(text: &str) -> String {
    let text = clean(text, 80);
    text.strip_prefix("minecraft:")
        .unwrap_or(&text)
        .to_lowercase()
}

/// 候補検索結果は存在の証拠にしない。現在観測のIDとの一致だけがobservedになる。
pub fn ground(plan: &Plan, topic_hits: &[Value], observed: &[Value]) -> Grounding {
    let mut result = Grounding {
        status: "not_applicable".into(),
        query: String::new(),
        candidate_ids: vec![],
        candidate_labels: vec![],
        observed_ids: vec![],
        observed_labels: vec![],
    };
    if !plan.requests_catalog() {
        return result;
    }
    result.query.clone_from(&plan.entity_query);
    let mut candidates = Vec::new();
    let mut seen = HashSet::new();
    for hit in topic_hits.iter().take(8) {
        let id = entity_id(string(hit, "entry_id"));
        if id.is_empty() || !seen.insert(id.clone()) {
            continue;
        }
        let label = clean(
            [string(hit, "label_ja"), string(hit, "label"), &id]
                .into_iter()
                .find(|s| !s.is_empty())
                .unwrap(),
            80,
        );
        let score = hit["score"]
            .as_f64()
            .or_else(|| hit["score"].as_str().and_then(|s| s.parse::<f64>().ok()))
            .unwrap_or(0.0);
        let label = if label.is_empty() { id.clone() } else { label };
        candidates.push((id, label, score));
    }
    if candidates.is_empty() {
        result.status = "unknown".into();
        return result;
    }
    let mut observed_by_id = HashMap::new();
    for row in observed {
        let id = entity_id(string(row, "entity_id"));
        if id.is_empty() {
            continue;
        }
        let label = clean(string(row, "label"), 80);
        observed_by_id.insert(id.clone(), if label.is_empty() { id } else { label });
    }
    let limit = if matches!(
        plan.action,
        Action::CheckEntityPresence | Action::CorrectPreviousReply
    ) {
        1
    } else {
        candidates.len()
    };
    for (i, (id, label, _)) in candidates.iter().take(limit).enumerate() {
        if i < 4 {
            result.candidate_ids.push(id.clone());
            result.candidate_labels.push(label.clone());
        }
        if let Some(label) = observed_by_id.get(id) {
            result.observed_ids.push(id.clone());
            result.observed_labels.push(label.clone());
        }
    }
    result.status = if !result.observed_ids.is_empty() {
        "observed"
    } else if candidates.len() > 1 && (candidates[0].2 - candidates[1].2).abs() < 0.01 {
        "ambiguous"
    } else {
        "not_observed"
    }
    .into();
    result
}

pub fn fixed_reply(plan: &Plan, grounding: &Grounding) -> String {
    let label = grounding
        .candidate_labels
        .first()
        .map(String::as_str)
        .unwrap_or("その対象");
    match (plan.action, grounding.status.as_str()) {
        (Action::CorrectPreviousReply, "observed") => {
            format!("ごめん、さっきの言い方は断言しすぎたわ。今の観測では{label}を確認できとる。")
        }
        (Action::CorrectPreviousReply, "not_observed") => {
            format!("ごめん、今の観測では{label}は確認できてへん。さっきの言い方は断言しすぎたわ。")
        }
        (Action::CorrectPreviousReply, _) => {
            "ごめん、今の材料では確かめられへん。さっきは断言しすぎたわ。".into()
        }
        (Action::CheckEntityPresence, "observed") if plan.presence_challenged => {
            format!("今の観測には{label}が入っとるけど、プレイヤーから見えるとは限らへんわ。")
        }
        (Action::CheckEntityPresence, "not_observed") => {
            format!("今の観測では、{label}は確認できてへんわ。")
        }
        (Action::CheckEntityPresence, "unknown" | "ambiguous") => {
            "どれのことか、今の材料だけやと分からへんわ。".into()
        }
        (Action::IdentifyEntity, "unknown" | "ambiguous") => {
            "オレにはまだ分からへんわ。もうちょい見た目を教えてくれる？".into()
        }
        _ => String::new(),
    }
}
