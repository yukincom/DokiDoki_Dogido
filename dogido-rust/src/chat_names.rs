//! Pure construction of speech-name permissions and unique observed-name rewrites.
//! A permission is not world evidence. The caller owns current/recent observations
//! and must supply only completed dialogue history from the existing runtime.
use crate::text_format::{self, ContainerFormat::QuotedRepr};
use crate::{
    chat_catalog::{self, Catalog, normalized_observation_id, strip, truth},
    chat_validation::mentioned,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Topic {
    #[serde(default)]
    pub entry_id: String,
    #[serde(default)]
    pub label_ja: String,
    #[serde(default = "mob_kind")]
    pub kind: String,
}
fn mob_kind() -> String {
    "mob".into()
}
impl From<&chat_catalog::Hit> for Topic {
    fn from(hit: &chat_catalog::Hit) -> Self {
        Self {
            entry_id: hit.entry_id.clone(),
            label_ja: hit.label_ja.clone(),
            kind: match hit.kind {
                chat_catalog::Kind::Mob => "mob",
                chat_catalog::Kind::Structure => "structure",
            }
            .into(),
        }
    }
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryRow {
    pub role: String,
    pub text: String,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    /// Already selected topic_for_identify, not raw unfiltered catalog candidates.
    #[serde(default)]
    pub topics: Vec<Topic>,
    #[serde(default)]
    pub visual_types: Vec<String>,
    /// Canonical caller-merged passive types and recent passive sighting IDs.
    #[serde(default)]
    pub passive_types: Vec<String>,
    /// Hearing named mobs followed by hearing source labels, in canonical order.
    #[serde(default)]
    pub hearing_named_mobs: Vec<String>,
    /// Existing recent_name_context_types only; do not infer this from history.
    #[serde(default)]
    pub recent_mob_types: Vec<String>,
    #[serde(default)]
    pub current_entity_labels: Vec<String>,
    #[serde(default)]
    pub user_text: String,
    #[serde(default)]
    pub history: Vec<HistoryRow>,
    /// Existing look_for_observation, after the caller's look-answer gate.
    #[serde(default)]
    pub look_label: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Names {
    pub allowed_speech_labels: Vec<String>,
    pub speech_name_corrections: BTreeMap<String, String>,
    /// This projection is ordinary narration. Workshop stripping remains caller-owned.
    pub speech_whitelist_enforce: bool,
}
fn value_text(v: &Value) -> String {
    if truth(v) {
        text_format::value_text(v, QuotedRepr)
    } else {
        String::new()
    }
}
fn add(labels: &mut Vec<String>, value: &str) {
    let value = strip(value);
    if value.chars().count() >= 2 && !labels.iter().any(|v| v == value) {
        labels.push(value.into());
    }
}
fn observed_entry(labels: &mut Vec<String>, entry: &Value) {
    add(labels, &value_text(&entry["label"]));
    if let Some(aliases) = entry["observed_speech_aliases"].as_array() {
        for alias in aliases {
            add(labels, &value_text(alias));
        }
    }
}
fn observed_type(catalog: &Catalog, labels: &mut Vec<String>, raw: &str) {
    let id = normalized_observation_id(raw);
    if id.is_empty() {
        return;
    }
    if let Some(entry) = catalog.mob_entry(&id).filter(|v| truth(v)) {
        observed_entry(labels, entry);
    } else {
        add(labels, &id);
    }
}
/// Equivalent to build_allowed_speech_labels, before narration adds user labels.
pub fn allowed_labels(catalog: &Catalog, input: &Input) -> Vec<String> {
    let mut labels = vec![];
    for topic in &input.topics {
        add(&mut labels, &topic.label_ja);
        let id = strip(&topic.entry_id);
        let kind = if topic.kind.is_empty() {
            "mob"
        } else {
            &topic.kind
        };
        if !id.is_empty() {
            let entry = if kind == "mob" {
                catalog.mob_entry(id)
            } else if kind == "structure" {
                catalog.structure_entries().get(id)
            } else {
                None
            };
            if let Some(entry) = entry {
                add(&mut labels, &value_text(&entry["label"]));
            }
        }
        // Candidate names do not permit observed-only aliases.
    }
    for id in &input.visual_types {
        observed_type(catalog, &mut labels, id);
    }
    for id in &input.passive_types {
        observed_type(catalog, &mut labels, id);
    }
    for raw in &input.hearing_named_mobs {
        let name = strip(raw);
        add(&mut labels, name);
        if !name.is_empty() {
            for entry in catalog.all_mob_entries().values() {
                if strip(&value_text(&entry["label"])) == name {
                    observed_entry(&mut labels, entry);
                }
            }
        }
    }
    for id in &input.recent_mob_types {
        observed_type(catalog, &mut labels, id);
    }
    labels
}
/// Equivalent to build_observed_speech_name_corrections. Python iterates sets,
/// so dictionary key order is unspecified there; native output is deterministic.
pub fn observed_name_corrections(
    catalog: &Catalog,
    observed_ids: &[String],
) -> BTreeMap<String, String> {
    let observed: BTreeSet<_> = observed_ids
        .iter()
        .filter(|s| !strip(s).is_empty())
        .map(|s| normalized_observation_id(s))
        .collect();
    let mut candidates: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for target_id in &observed {
        let Some(target) = catalog.mob_entry(target_id).filter(|v| truth(v)) else {
            continue;
        };
        let target_label = value_text(&target["label"]);
        let target_label = strip(&target_label);
        let Some(sources) = target["observed_speech_rewrite_from_ids"].as_array() else {
            continue;
        };
        if target_label.is_empty() {
            continue;
        }
        for source in sources {
            let source_id = normalized_observation_id(&value_text(source));
            if source_id.is_empty() || observed.contains(&source_id) {
                continue;
            }
            let Some(source) = catalog.mob_entry(&source_id) else {
                continue;
            };
            let source_label = value_text(&source["label"]);
            let source_label = strip(&source_label);
            if source_label.is_empty() || source_label == target_label {
                continue;
            }
            candidates
                .entry(source_label.into())
                .or_default()
                .insert(target_label.into());
        }
    }
    candidates
        .into_iter()
        .filter_map(|(source, targets)| {
            if targets.len() == 1 {
                Some((source, targets.into_iter().next().unwrap()))
            } else {
                None
            }
        })
        .collect()
}
/// Full ordinary narration projection. Current entity labels are kept exactly,
/// including one-character labels; matching user/look names uses the existing
/// longest-match guard shared with final candidate validation.
pub fn project(catalog: &Catalog, input: &Input) -> Names {
    let mut labels = allowed_labels(catalog, input);
    let mut add_exact = |label: String| {
        if !label.is_empty() && !labels.contains(&label) {
            labels.push(label);
        }
    };
    for label in &input.current_entity_labels {
        add_exact(label.clone());
    }
    for label in mentioned(&input.user_text) {
        add_exact(label);
    }
    for row in &input.history {
        if row.role == "user" {
            for label in mentioned(&row.text) {
                add_exact(label);
            }
        }
    }
    for label in mentioned(&input.look_label) {
        add_exact(label);
    }
    Names {
        allowed_speech_labels: labels,
        speech_name_corrections: observed_name_corrections(catalog, &input.recent_mob_types),
        speech_whitelist_enforce: true,
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn chat_names_shared_longest_match_keeps_canonical_order_and_repetitions() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("chat_names/fixtures.json")).unwrap();
        for row in fixture["mentions"].as_array().unwrap() {
            assert_eq!(
                serde_json::to_value(super::mentioned(row[0].as_str().unwrap())).unwrap(),
                row[1]
            );
        }
    }
}
