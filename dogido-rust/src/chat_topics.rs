//! Pure topic policy; does not query catalogs, observe the world, or call a model.
//! Indices refer to the caller's unchanged catalog rows, so metadata is preserved.
use crate::planner::{Action, Grounding, Plan};
use serde::{Deserialize, Serialize};
use std::{collections::HashSet, sync::LazyLock};

#[derive(Deserialize)]
struct Rules {
    generic_terms: Vec<String>,
    specific_short_terms: Vec<String>,
    identify_markers: Vec<String>,
    identify_min_score: f64,
}
static RULES: LazyLock<Rules> = LazyLock::new(|| {
    serde_json::from_str(include_str!("chat_topics/rules.json")).expect("canonical topic rules")
});

/// Closed projection of a real catalog hit, before its policy filter.
/// Catalog scores are finite numbers; an absent score is canonically zero.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Topic {
    #[serde(default)]
    pub entry_id: String,
    #[serde(default)]
    pub label_ja: String,
    #[serde(default)]
    pub matched_terms: Vec<String>,
    #[serde(default)]
    pub score: f64,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub has_visual_threats: bool,
    pub topic_hits: Vec<Topic>,
    pub threat_summary: String,
    /// Use the accepted plan's entity_query when present; otherwise current text.
    pub user_text: String,
    /// Current visual/passive/hearing IDs, never conversational statements.
    pub observed_ids: Vec<String>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stance {
    Saw,
    Hypothesis,
    Clarify,
    None,
}
impl Stance {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Saw => "saw",
            Self::Hypothesis => "hypothesis",
            Self::Clarify => "clarify",
            Self::None => "none",
        }
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Policy {
    pub usable_indices: Vec<usize>,
    pub reply_stance: Stance,
    pub reply_policy: String,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Projection {
    #[serde(flatten)]
    pub policy: Policy,
    pub topic_for_identify_indices: Vec<usize>,
    pub identify_skeleton: Option<String>,
}
fn strip(text: &str) -> &str {
    text.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}
fn has(text: &str, needles: &[&str]) -> bool {
    needles.iter().any(|part| text.contains(part))
}
fn normalized_id(text: &str) -> String {
    // Preserve canonical order: namespace removal precedes stripping/lowercase.
    strip(text.strip_prefix("minecraft:").unwrap_or(text)).to_lowercase()
}
pub fn is_generic_topic_term(term: &str) -> bool {
    let term = strip(term);
    term.is_empty()
        || RULES.generic_terms.iter().any(|s| s == term)
        || (term.chars().count() < 2 && !RULES.specific_short_terms.iter().any(|s| s == term))
}
pub fn has_identify_intent(text: &str) -> bool {
    let text = strip(text);
    !text.is_empty()
        && (RULES
            .identify_markers
            .iter()
            .any(|marker| text.contains(marker))
            || (has(text, &["？", "?"]) && has(text, &["いる", "おる", "誰", "何", "なに"])))
}
pub fn has_threat_presence_query(text: &str) -> bool {
    let text = strip(text);
    if text.is_empty() {
        return false;
    }
    if has(
        text,
        &["いる", "おる", "居る", "いない", "おらん", "いなく"],
    ) && (has(
        text,
        &["？", "?", "か", "の", "まだ", "今", "いま", "まだい"],
    ) || text.chars().count() <= 12)
    {
        return true;
    }
    has(text, &["まだい", "気配", "ついてく", "追いかけ"])
        || (has(text, &["聞こ", "きこ", "声", "音"])
            && has(
                text,
                &["？", "?", "か", "する", "した", "してる", "ない", "へん"],
            ))
}
pub fn usable_topic_indices(hits: &[Topic]) -> Vec<usize> {
    hits.iter()
        .enumerate()
        .filter_map(|(i, hit)| {
            hit.matched_terms
                .iter()
                .any(|term| !is_generic_topic_term(term))
                .then_some(i)
        })
        .collect()
}
/// Pre-grounding policy. Feed usable_indices in their original order to ground.
pub fn prepare(input: &Input) -> Policy {
    let usable = usable_topic_indices(&input.topic_hits);
    let threat = strip(&input.threat_summary);
    let stance = if input.has_visual_threats || has(threat, &["視認", "音メモ", "気配"]) {
        Stance::Saw
    } else if !usable.is_empty() {
        let observed: HashSet<_> = input
            .observed_ids
            .iter()
            .filter(|s| !strip(s).is_empty())
            .map(|s| normalized_id(s))
            .collect();
        let matched = usable.iter().any(|i| {
            let id = normalized_id(&input.topic_hits[*i].entry_id);
            !id.is_empty() && observed.contains(&id)
        });
        if has_threat_presence_query(&input.user_text) && !matched && threat.is_empty() {
            Stance::None
        } else {
            Stance::Hypothesis
        }
    } else if has_identify_intent(&input.user_text) {
        Stance::Clarify
    } else {
        Stance::None
    };
    Policy {
        usable_indices: usable,
        reply_stance: stance,
        reply_policy: crate::chat_prompt::reply_policy_line(stance.as_str()).to_owned(),
    }
}
/// Post-grounding selection matches narration's identify-only gate. Merely being
/// a candidate or a past player report cannot add an observed ID here.
pub fn finish(input: &Input, plan: &Plan, grounding: &Grounding) -> Projection {
    let policy = prepare(input);
    let selected =
        if plan.action == Action::IdentifyEntity && policy.reply_stance == Stance::Hypothesis {
            policy
                .usable_indices
                .iter()
                .copied()
                .filter(|i| {
                    grounding.observed_ids.is_empty()
                        || grounding
                            .observed_ids
                            .contains(&input.topic_hits[*i].entry_id)
                })
                .collect::<Vec<_>>()
        } else {
            vec![]
        };
    let skeleton = if grounding.status == "observed" {
        None
    } else {
        identify_skeleton(
            policy.reply_stance,
            &selected
                .iter()
                .map(|i| &input.topic_hits[*i])
                .collect::<Vec<_>>(),
            RULES.identify_min_score,
        )
    };
    Projection {
        policy,
        topic_for_identify_indices: selected,
        identify_skeleton: skeleton,
    }
}
/// Existing fixed hypothesis wording; this is not a generated answer and the
/// final caller must suppress it when code grounding says observed.
pub fn identify_skeleton(stance: Stance, hits: &[&Topic], min_score: f64) -> Option<String> {
    if stance != Stance::Hypothesis {
        return None;
    }
    let hits: Vec<_> = hits
        .iter()
        .filter(|hit| {
            hit.matched_terms
                .iter()
                .any(|term| !is_generic_topic_term(term))
        })
        .collect();
    let first = hits.first()?;
    if first.score < min_score {
        return None;
    }
    if let Some(second) = hits.get(1)
        && (second.score - first.score).abs() < 0.01
        && !first.label_ja.is_empty()
        && !second.label_ja.is_empty()
    {
        return Some(format!(
            "俺には見えんけど、{}か{}あたりかもしれんな",
            first.label_ja, second.label_ja
        ));
    }
    let label = strip(&first.label_ja);
    (!label.is_empty()).then(|| format!("俺にははっきり見えんけど、{label}かもしれんな"))
}
