use super::{Plan, PreparedPlan, contract_errors, extract_object, messages, parse_model_plan};
use crate::{
    llm::RigLlm,
    types::{GenerationReport, GenerationRequest},
};
use serde::Serialize;
use serde_json::Value;

#[derive(Debug, Serialize)]
pub struct PlannerReport {
    pub schema_version: u32,
    pub plan: Plan,
    pub result: String,
    pub calls: usize,
    pub attempts: Vec<GenerationReport>,
    pub validation_errors: Vec<Vec<String>>,
    pub transport_error: Option<String>,
}

/// 一手だけのread planner。JSON契約違反時だけ一度再試行。
/// 低信頼・JSON途中切れ・通信失敗は再判定を重ねずコードfallbackへ戻す。
pub async fn run(client: &RigLlm, input: &PreparedPlan) -> anyhow::Result<PlannerReport> {
    input.validate()?;
    let mut report = PlannerReport {
        schema_version: 1,
        plan: input.fallback.clone(),
        result: "fallback".into(),
        calls: 0,
        attempts: vec![],
        validation_errors: vec![],
        transport_error: None,
    };
    let mut retry: Option<(Vec<String>, Value)> = None;
    if super::clean(super::string(&input.details.current, "text"), 160).is_empty() {
        report.result = "empty_input".into();
        return Ok(report);
    }
    for attempt in 0..2 {
        let request = GenerationRequest {
            schema_version: 1,
            kind: "player_chat_plan".into(),
            model: input.model.clone(),
            messages: messages(
                &input.details,
                retry.as_ref().map(|(e, p)| (e.as_slice(), p)),
            ),
            temperature: 0.0,
            max_tokens: 640,
            enable_thinking: input.enable_thinking,
        };
        report.calls += 1;
        let generation = match client.generate(&request).await {
            Ok(value) => value,
            Err(error) => {
                report.result = if attempt == 0 {
                    "generation_error"
                } else {
                    "schema_contract_error"
                }
                .into();
                report.transport_error = Some(error.to_string());
                break;
            }
        };
        let payload = extract_object(&generation.generated.text);
        let truncated = matches!(
            generation.generated.finish_reason.as_deref(),
            Some("length" | "max_tokens" | "MAX_TOKENS")
        );
        report.attempts.push(generation);
        let Some(payload) = payload else {
            report.result = if attempt > 0 {
                "schema_contract_error"
            } else if truncated {
                "output_truncated"
            } else {
                "invalid_json"
            }
            .into();
            break;
        };
        let errors = contract_errors(&payload, &input.details);
        report.validation_errors.push(errors.clone());
        if !errors.is_empty() {
            report.result = "schema_contract_error".into();
            retry = Some((errors, payload));
            continue;
        }
        if let Some(mut plan) = parse_model_plan(&payload, &input.details) {
            plan.presence_challenged = input.fallback.presence_challenged;
            report.plan = plan;
            report.result = if attempt == 0 {
                "accepted"
            } else {
                "contract_retry_accepted"
            }
            .into();
        } else {
            report.result = "consumer_rejected".into();
        }
        break;
    }
    Ok(report)
}
