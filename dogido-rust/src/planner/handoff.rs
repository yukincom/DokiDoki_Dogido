//! One ordinary turn's accepted plan and current-observation grounding boundary.
//! Catalog lookup/observation construction remain in the trusted Python projection.
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub topic_policy: Option<crate::chat_topics::Input>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Output {
    pub grounding: Grounding,
    pub fixed_reply: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub topics: Option<crate::chat_topics::Projection>,
}

#[derive(Default)]
pub struct Handoff {
    plan: Option<Plan>,
    prompt_observations: Vec<Observation>,
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
            input.plan.requests_catalog() || input.topic_hits.is_empty(),
            "nonentity plan cannot read catalog"
        );
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
        let grounding = ground(&input.plan, &hits, &observed);
        let result = Output {
            fixed_reply: fixed_reply(&input.plan, &grounding),
            topics: input
                .topic_policy
                .as_ref()
                .map(|policy| crate::chat_topics::finish(policy, &input.plan, &grounding)),
            grounding,
        };
        self.output = Some(result.clone());
        Ok(result)
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
fn normalize_prompt_observations(rows: &[Observation]) -> Vec<Observation> {
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
