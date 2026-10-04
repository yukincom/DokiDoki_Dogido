//! One ordinary turn's accepted plan and current-observation grounding boundary.
//! Catalog lookup uses only the accepted plan. Rust chat_observation supplies
//! current observations; player reports and model text never become observations.
use super::{Action, Grounding, Plan, PreparedPlan, clean, fixed_reply, ground};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashSet;

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Candidate {
    pub entry_id: String,
    pub label: String,
    pub score: Option<f64>,
}
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Observation {
    pub entity_id: String,
    pub label: String,
}
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub schema_version: u32,
    pub source: String,
    pub plan: Plan,
    pub topic_hits: Vec<Candidate>,
    pub observed_entities: Vec<Observation>,
    /// Remembered name mappings are candidates only, never current presence.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub individual_names: Vec<Observation>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub look_target: Option<Observation>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub topic_policy: Option<crate::chat_topics::Input>,
    #[serde(default)]
    pub native_catalog: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name_context: Option<crate::chat_names::Input>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Output {
    pub grounding: Grounding,
    pub fixed_reply: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub topics: Option<crate::chat_topics::Projection>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub catalog: Option<Vec<crate::chat_catalog::Hit>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub catalog_topic_hints: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub names: Option<crate::chat_names::Names>,
}

#[derive(Default)]
pub struct Handoff {
    plan: Option<Plan>,
    prompt_observations: Vec<Observation>,
    prompt_look_target: Option<Observation>,
    output: Option<Output>,
}
impl Handoff {
    pub fn record_plan(&mut self, request: &PreparedPlan, plan: &Plan) -> Result<()> {
        ensure!(self.plan.is_none(), "grounding plan already recorded");
        // These are precisely the first 16 current rows used in the model prompt;
        // do not substitute history, user reports, or catalog candidates here.
        let rows: Vec<Observation> =
            serde_json::from_value(request.details.observations["observed_entities"].clone())?;
        ensure!(rows.len() <= 16, "invalid planner observation projection");
        self.prompt_observations = rows;
        self.prompt_look_target =
            serde_json::from_value(request.details.observations["look_target"].clone())?;
        self.plan = Some(plan.clone());
        Ok(())
    }
    pub fn resolve(&mut self, input: Input, active: bool) -> Result<Output> {
        ensure!(active, "cancelled");
        ensure!(self.output.is_none(), "grounding already resolved");
        ensure!(
            self.plan.as_ref() == Some(&input.plan),
            "grounding plan mismatch"
        );
        ensure!(
            input.schema_version == 1 && input.source == "current_observation",
            "invalid grounding projection"
        );
        ensure!(
            input.topic_hits.len() <= 8,
            "grounding candidate limit exceeded"
        );
        ensure!(
            normalize_prompt_observations(&input.observed_entities) == self.prompt_observations,
            "grounding current observations changed after planner"
        );
        ensure!(
            input.look_target == self.prompt_look_target,
            "grounding crosshair observation changed after planner"
        );
        ensure!(
            input.plan.requests_catalog() || input.topic_hits.is_empty(),
            "nonentity plan cannot read catalog"
        );
        let result = project(input)?;
        self.output = Some(result.clone());
        Ok(result)
    }
    pub fn validate_materials(
        &self,
        details: &Value,
        validation: &Value,
        workshop_open: bool,
    ) -> Result<()> {
        let Some(output) = &self.output else {
            return Ok(());
        };
        let (Some(_rows), Some(topics)) = (&output.catalog, &output.topics) else {
            return Ok(());
        };
        let stance = if workshop_open {
            "none"
        } else {
            topics.policy.reply_stance.as_str()
        };
        for (key, expected) in [
            ("reply_stance", json!(stance)),
            (
                "reply_policy",
                json!(crate::chat_prompt::reply_policy_line(stance)),
            ),
            (
                "catalog_topic_hints",
                json!(if workshop_open {
                    ""
                } else {
                    output.catalog_topic_hints.as_deref().unwrap_or("")
                }),
            ),
        ] {
            ensure!(
                details[key] == expected,
                "native chat material mismatch: {key}"
            );
        }
        if let Some(names) = &output.names {
            ensure!(
                validation["allowed_speech_labels"]
                    == if workshop_open {
                        json!([])
                    } else {
                        json!(names.allowed_speech_labels)
                    },
                "native allowed names mismatch"
            );
            ensure!(
                validation["speech_name_corrections"] == json!(names.speech_name_corrections),
                "native name corrections mismatch"
            );
            ensure!(
                validation["speech_whitelist_enforce"] == json!(!workshop_open),
                "native name whitelist mismatch"
            );
        }
        Ok(())
    }
    pub fn validate_leaf(&self, details: &Value) -> Result<()> {
        let Some(plan) = &self.plan else {
            return Ok(());
        };
        let output = self
            .output
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("chat leaf requires grounded plan"))?;
        ensure!(
            output.fixed_reply.is_empty(),
            "fixed grounding reply cannot generate"
        );
        let g = &output.grounding;
        for (key, expected) in [
            (
                "player_chat_plan_action",
                serde_json::to_value(plan.action)?,
            ),
            ("entity_query", json!(g.query)),
            ("entity_grounding_status", json!(g.status)),
            ("entity_candidate_labels", json!(g.candidate_labels)),
            ("entity_observed_labels", json!(g.observed_labels)),
        ] {
            ensure!(
                details[key] == expected,
                "grounding leaf field mismatch: {key}"
            );
        }
        Ok(())
    }
    pub fn validate_result(&self, text: &Value) -> Result<()> {
        let Some(plan) = &self.plan else {
            return Ok(());
        };
        // Conversation-repair clarification returns before entity grounding in the
        // canonical narration; its separate evidence/repair contract stays intact.
        if plan.action == Action::ClarifyRepair {
            return Ok(());
        }
        let output = self
            .output
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("chat result requires grounded plan"))?;
        if !output.fixed_reply.is_empty() {
            ensure!(
                text == &Value::String(output.fixed_reply.clone()),
                "helper changed fixed grounding reply"
            );
        }
        Ok(())
    }
}
pub(super) fn normalize_prompt_observations(rows: &[Observation]) -> Vec<Observation> {
    let mut seen = HashSet::new();
    rows.iter()
        .take(16)
        .filter_map(|row| {
            let raw = clean(&row.entity_id, 80);
            let id = raw
                .strip_prefix("minecraft:")
                .unwrap_or(&raw)
                .to_lowercase();
            if id.is_empty() || !seen.insert(id.clone()) {
                return None;
            }
            let label = clean(&row.label, 80);
            Some(Observation {
                entity_id: id.clone(),
                label: if label.is_empty() { id } else { label },
            })
        })
        .collect()
}

/// Pure projection for native material assembly; lifecycle and accepted-plan checks remain in Handoff.
pub(crate) fn project(mut input: Input) -> Result<Output> {
    let mut individuals = input
        .individual_names
        .iter()
        .filter(|row| {
            input.plan.requests_catalog()
                && row.entity_id.starts_with("individual:")
                && crate::mob_identity::named_in(&input.plan.entity_query, &row.label)
        })
        .collect::<Vec<_>>();
    individuals.retain(|row| {
        !input.individual_names.iter().any(|other| {
            crate::mob_identity::longer_name(&other.label, &row.label)
                && crate::mob_identity::named_in(&input.plan.entity_query, &other.label)
        })
    });
    let catalog = if input.native_catalog {
        let policy = input
            .topic_policy
            .as_mut()
            .ok_or_else(|| anyhow::anyhow!("native catalog needs topic context"))?;
        ensure!(
            input.topic_hits.is_empty() && policy.topic_hits.is_empty(),
            "native catalog cannot accept projected hits"
        );
        let rows = if input.plan.requests_catalog()
            && individuals.is_empty()
            && !(input.look_target.is_some() && is_look_reference(&input.plan.entity_query))
        {
            crate::chat_catalog::catalog()
                .player_chat_topics(&input.plan.entity_query, &policy.observed_ids)
        } else {
            vec![]
        };
        input.topic_hits = rows
            .iter()
            .map(|row| Candidate {
                entry_id: row.entry_id.clone(),
                label: row.label_ja.clone(),
                score: Some(row.score),
            })
            .collect();
        policy.topic_hits = rows
            .iter()
            .map(|row| crate::chat_topics::Topic {
                entry_id: row.entry_id.clone(),
                label_ja: row.label_ja.clone(),
                score: row.score,
                matched_terms: row.matched_terms.clone(),
            })
            .collect();
        Some(rows)
    } else {
        None
    };
    let selected = if let Some(policy) = &input.topic_policy {
        ensure!(
            policy.topic_hits.len() == input.topic_hits.len(),
            "topic projection length mismatch"
        );
        for (topic, hit) in policy.topic_hits.iter().zip(&input.topic_hits) {
            ensure!(
                topic.entry_id == hit.entry_id && hit.score == Some(topic.score),
                "topic projection mismatch"
            );
        }
        crate::chat_topics::prepare(policy).usable_indices
    } else {
        (0..input.topic_hits.len()).collect()
    };
    let hits: Vec<Value> = input
        .topic_hits
        .iter()
        .enumerate()
        .filter(|(i, _)| selected.contains(i))
        .map(|(_, row)| row)
        .map(|row| {
            json!({
                "entry_id": row.entry_id, "label": row.label,
                "score": row.score.map_or_else(|| json!("NaN"), |n| json!(n)),
            })
        })
        .collect();
    let observed: Vec<Value> = input
        .observed_entities
        .iter()
        .map(|row| {
            json!({
                "entity_id": row.entity_id, "label": row.label,
            })
        })
        .collect();
    let grounding = if input.plan.action == Action::IdentifyEntity
        && is_look_reference(&input.plan.entity_query)
        && let Some(target) = input
            .look_target
            .as_ref()
            .filter(|target| !target.entity_id.is_empty() && !target.label.is_empty())
    {
        Grounding {
            status: "observed".into(),
            query: input.plan.entity_query.clone(),
            candidate_ids: vec![target.entity_id.clone()],
            candidate_labels: vec![target.label.clone()],
            observed_ids: vec![target.entity_id.clone()],
            observed_labels: vec![target.label.clone()],
        }
    } else if !individuals.is_empty() {
        let unique = individuals.len() == 1;
        let present = individuals
            .iter()
            .filter(|candidate| {
                unique
                    && input
                        .observed_entities
                        .iter()
                        .any(|row| row.entity_id == candidate.entity_id)
            })
            .collect::<Vec<_>>();
        Grounding {
            status: if !unique {
                "ambiguous"
            } else if present.is_empty() {
                "not_observed"
            } else {
                "observed"
            }
            .into(),
            query: input.plan.entity_query.clone(),
            candidate_ids: individuals.iter().map(|r| r.entity_id.clone()).collect(),
            candidate_labels: individuals.iter().map(|r| r.label.clone()).collect(),
            observed_ids: present.iter().map(|r| r.entity_id.clone()).collect(),
            observed_labels: present.iter().map(|r| r.label.clone()).collect(),
        }
    } else {
        ground(&input.plan, &hits, &observed)
    };
    let topics = input
        .topic_policy
        .as_ref()
        .map(|policy| crate::chat_topics::finish(policy, &input.plan, &grounding));
    let catalog_topic_hints = catalog.as_ref().map(|rows| {
        crate::chat_catalog::topic_hints(
            &topics
                .as_ref()
                .unwrap()
                .topic_for_identify_indices
                .iter()
                .map(|i| rows[*i].clone())
                .collect::<Vec<_>>(),
        )
    });
    let names = if let Some(mut context) = input.name_context {
        ensure!(
            context.topics.is_empty(),
            "native name context cannot select topics"
        );
        ensure!(
            context.current_entity_labels
                == input
                    .observed_entities
                    .iter()
                    .map(|r| r.label.clone())
                    .collect::<Vec<_>>(),
            "name context observations mismatch"
        );
        let rows = catalog
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("name context needs native catalog"))?;
        context.topics = topics
            .as_ref()
            .unwrap()
            .topic_for_identify_indices
            .iter()
            .map(|i| (&rows[*i]).into())
            .collect();
        Some(crate::chat_names::project(
            crate::chat_catalog::catalog(),
            &context,
        ))
    } else {
        None
    };
    Ok(Output {
        names,
        fixed_reply: fixed_reply(&input.plan, &grounding),
        topics,
        catalog,
        catalog_topic_hints,
        grounding,
    })
}

/// A named query must never be replaced by whatever happens to be under the crosshair.
fn is_look_reference(query: &str) -> bool {
    let query = query.trim().trim_end_matches(['?', '？', '。']);
    ["これ", "それ", "あれ"].iter().any(|pronoun| {
        query.strip_prefix(pronoun).is_some_and(|tail| {
            matches!(
                tail,
                "" | "何"
                    | "なに"
                    | "は何"
                    | "はなに"
                    | "何かな"
                    | "なにかな"
                    | "は何かな"
                    | "はなにかな"
                    | "何ですか"
                    | "なにですか"
                    | "は何ですか"
                    | "はなにですか"
            )
        })
    }) || ["この", "その", "あの"].iter().any(|prefix| {
        query.trim().strip_prefix(prefix).is_some_and(|noun| {
            matches!(
                noun,
                "ブロック"
                    | "花"
                    | "石"
                    | "木"
                    | "モブ"
                    | "動物"
                    | "敵"
                    | "生き物"
                    | "もの"
                    | "物"
            )
        })
    })
}
