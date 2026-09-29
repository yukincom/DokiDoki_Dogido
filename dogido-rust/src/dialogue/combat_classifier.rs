//! OS SDKは休眠worker、chat fallbackはRig。一要求の所有者が取消・回収を待つ。
use super::*;
use crate::{
    haiku_bridge::Helper,
    types::{ChatMessage, GenerationRequest},
    workshop_combat_input::{Action, Analysis},
};
use anyhow::Context;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub provider: String,
    pub timeout_sec: f64,
    pub refresh_sec: f64,
    pub failure_cooldown_sec: f64,
    pub foundry_model_alias: String,
    pub allow_model_download: bool,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            provider: "auto".into(),
            timeout_sec: 8.0,
            refresh_sec: 300.0,
            failure_cooldown_sec: 60.0,
            foundry_model_alias: "qwen2.5-7b".into(),
            allow_model_download: false,
        }
    }
}
impl Settings {
    pub fn validate(&self) -> Result<()> {
        anyhow::ensure!(
            ["auto", "apple", "foundry", "chat"].contains(&self.provider.as_str()),
            "invalid platform provider"
        );
        anyhow::ensure!(
            [
                self.timeout_sec,
                self.refresh_sec,
                self.failure_cooldown_sec
            ]
            .iter()
            .all(|v| v.is_finite() && *v > 0.0),
            "invalid platform timeout"
        );
        Duration::try_from_secs_f64(self.timeout_sec * 2.0 + 5.0)
            .context("platform timeout is too large")?;
        Ok(())
    }
}
#[derive(Default)]
pub(super) struct Classifier {
    helper: tokio::sync::Mutex<Option<Helper>>,
}
impl Classifier {
    pub async fn run(
        &self,
        c: &DialogueConfig,
        llm: &RigLlm,
        input: Value,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Value> {
        let mut slot = tokio::select! { _=bridge::cancelled(cancel)=>anyhow::bail!("cancelled"), s=self.helper.lock()=>s };
        let work = async {
            let text = input["text"].as_str().context("combat input text")?;
            let response = if c.haiku.platform_ai.provider == "chat" {
                // Chat classification needs no OS SDK or Python preparation.
                json!({"provider":"chat", "needs_chat":true, "payload":null})
            } else {
                Self::helper(&mut slot, c)?
                    .exchange_with_timeout(
                        json!({"op":"classify", "text":text,
                "verse":input["verse"], "settings":c.haiku.platform_ai}),
                        Duration::from_secs_f64(c.haiku.platform_ai.timeout_sec * 2.0 + 5.0),
                    )
                    .await?
            };
            let mut provider = response["provider"].clone();
            let mut payload = response["payload"].clone();
            let mut reports = vec![];
            if response["needs_chat"] == true {
                let mut request = GenerationRequest {
                    schema_version: 1,
                    kind: "haiku_workshop_combat_input".into(),
                    model: c.model.clone(),
                    messages: crate::workshop_input_guard::combat_messages(
                        input["verse"].as_str().unwrap_or(""),
                        text,
                    ),
                    temperature: 0.0,
                    max_tokens: 120,
                    enable_thinking: false,
                };
                for attempt in 0..2 {
                    let result = llm.generate(&request).await;
                    match result {
                        Ok(report) => {
                            tracing::info!(event="combat_workshop_classified",attempt,elapsed_ms=report.elapsed_ms as u64,
                                finish_reason=?report.generated.finish_reason,completion_tokens=?report.generated.completion_tokens);
                            payload = crate::planner::extract_object(&report.generated.text)
                                .unwrap_or(Value::Null);
                            let truncated =
                                report.generated.finish_reason.as_deref() == Some("length");
                            reports.push(json!(report));
                            if truncated {
                                payload = Value::Null;
                            }
                            if truncated
                                || serde_json::from_value::<Analysis>(payload.clone()).is_ok()
                            {
                                break;
                            }
                            request.messages.push(ChatMessage {role:crate::types::Role::User,content:"外形が不正です。action, confidence（数値）, evidence（原文）の3キーだけで返してください。".into()});
                        }
                        Err(e) => {
                            reports.push(json!({"error":e.to_string()}));
                            break;
                        }
                    }
                }
            }
            let mut analysis = Analysis::parse(&payload, text);
            if !crate::workshop_input_guard::combat_safe(
                analysis.action.name(),
                text,
                &analysis.evidence,
            ) {
                analysis = Analysis::default();
            }
            if analysis.action == Action::Uncertain {
                // The remaining fallback resolves spoken verse fragments through the dictionary.
                let fallback = Self::helper(&mut slot, c)?
                    .exchange(json!({"op":"fallback","text":text,"workshop":input["workshop"]}))
                    .await?;
                analysis = Analysis::parse(&fallback, text);
                if analysis.action != Action::Uncertain {
                    provider = "rule_fallback".into();
                }
            }
            Ok(json!({"analysis":analysis,"provider":provider,"llm_reports":reports}))
        };
        let result = tokio::select! {
            _=bridge::cancelled(cancel)=>Err(anyhow::anyhow!("cancelled")),
            r=tokio::time::timeout(Duration::from_secs(95),work)=>r.unwrap_or_else(|_|Err(anyhow::anyhow!("combat classifier timed out"))),
        };
        if result.is_err()
            && let Some(helper) = slot.take()
        {
            let _ = helper.finish(true).await;
        }
        result
    }
    fn helper<'a>(slot: &'a mut Option<Helper>, c: &DialogueConfig) -> Result<&'a mut Helper> {
        if slot.is_none() {
            *slot = Some(Helper::start(
                &c.python,
                &PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("scripts/combat_input_helper.py"),
            )?);
        }
        Ok(slot.as_mut().unwrap())
    }
    pub async fn close(&self) {
        if let Some(helper) = self.helper.lock().await.take() {
            let _ = helper.finish(false).await;
        }
    }
}
