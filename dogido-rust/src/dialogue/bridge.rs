use super::DialogueConfig;
use crate::llm::RigLlm;
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::time::Duration;
use tokio::sync::watch;

/// Frozen Rust-owned input. Only the flexible routing projections use JSON;
/// observation, conversation context and parsed input stay typed in-process.
#[derive(Clone)]
pub(super) struct Input {
    frame: Value,
    pub snapshot: Option<std::sync::Arc<(crate::events::GameEvent, super::chat_context::Native)>>,
    pub context: Option<crate::input_context::Context>,
    pub retained_history_count: usize,
    pub horse_recall: Option<super::horse_recall_runtime::Request>,
    pub request_classification: Option<crate::request_classifier::Classification>,
    pub visual: Option<super::visual_runtime::Source>,
    pub after_look: Option<super::visual_runtime::LookCapture>,
}

impl Input {
    pub fn native(
        frame: Value,
        event: crate::events::GameEvent,
        context: super::chat_context::Native,
    ) -> Self {
        Self {
            frame,
            snapshot: Some(std::sync::Arc::new((event, context))),
            context: None,
            retained_history_count: 0,
            horse_recall: None,
            request_classification: None,
            visual: None,
            after_look: None,
        }
    }
}

impl From<Value> for Input {
    fn from(frame: Value) -> Self {
        Self {
            snapshot: None,
            context: None,
            retained_history_count: 0,
            horse_recall: None,
            request_classification: None,
            visual: None,
            after_look: None,
            frame,
        }
    }
}

impl std::ops::Deref for Input {
    type Target = Value;
    fn deref(&self) -> &Self::Target {
        &self.frame
    }
}
impl std::ops::DerefMut for Input {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.frame
    }
}

pub async fn cancelled(cancel: &mut watch::Receiver<bool>) {
    loop {
        if *cancel.borrow_and_update() {
            return;
        }
        if cancel.changed().await.is_err() {
            return;
        }
    }
}

pub async fn render(
    config: &DialogueConfig,
    llm: &RigLlm,
    input: impl Into<Input>,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Value> {
    render_with_route(config, llm, input, cancel, |_| Ok(())).await
}

pub async fn render_with_route(
    config: &DialogueConfig,
    llm: &RigLlm,
    input: impl Into<Input>,
    cancel: &mut watch::Receiver<bool>,
    select_route: impl FnMut(crate::foreground::Route) -> Result<()>,
) -> Result<Value> {
    render_with_budget(
        config,
        llm,
        input.into(),
        cancel,
        select_route,
        Duration::from_secs(95),
    )
    .await
}

async fn render_with_budget(
    config: &DialogueConfig,
    llm: &RigLlm,
    mut input: Input,
    cancel: &mut watch::Receiver<bool>,
    select_route: impl FnMut(crate::foreground::Route) -> Result<()>,
    whole_turn_timeout: Duration,
) -> Result<Value> {
    if input["op"] == "light_plan" {
        return crate::light_plan::run(
            llm,
            &config.model,
            &input["details"],
            &input["fallback_payload"],
            cancel,
        )
        .await;
    }
    let prepared = input["text"].as_str().map(crate::player_text::prepare);
    // 既存FIFOの知識抽出。分類済み入力は三つの依頼性をここで再判定しない。
    if input["op"] == "assist_route" {
        let prepared = prepared
            .as_ref()
            .context("knowledge routing needs current text")?;
        ensure!(
            !*cancel.borrow() && cancel.has_changed().is_ok(),
            "cancelled"
        );
        // The assist fast path needs neither a helper nor a reading dictionary/DB.
        return Ok(
            json!({"knowledge_query":match &input.request_classification {
                Some(result) if result.prediction.label == crate::request_classifier::Label::NotTarget
                    => crate::knowledge::query::extract(&prepared.normalized_text).is_some(),
                Some(_) => false,
                None => crate::knowledge::query::from_normalized(&prepared.normalized_text).is_some(),
            }}),
        );
    }
    // 環境leafは入力の読み辞書を使わない。直前に固定した観測で判断を始める。
    if input["op"] == "combat_leaf" {
        return super::reaction_runtime::render(config, llm, &input, cancel).await;
    }
    let overlay = super::reading_runtime::load_overlay(config).await?;
    input["reading_corrections"] = json!(overlay);
    if let Some(prepared) = prepared {
        let mut context = crate::input_context::Context::from_prepared(
            &prepared,
            &overlay,
            chrono::Local::now().fixed_offset(),
        );
        if let Some(classification) = &input.request_classification {
            ensure!(
                classification.text == prepared.raw_text,
                "request classification input mismatch"
            );
            context.apply_request_classification(classification);
        }
        input.context = Some(context);
    }
    super::chat_runtime::render(
        config,
        llm,
        &input,
        cancel,
        select_route,
        whole_turn_timeout,
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn knowledge_classification_needs_no_python_reader_or_model() {
        let config = DialogueConfig {
            python: "/missing/knowledge-test-python".into(),
            helper: "/missing/knowledge-test-helper.py".into(),
            ..Default::default()
        };
        let llm = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_secs(1)).unwrap();
        let (cancel, mut rx) = watch::channel(false);
        for (text, expected) in [
            ("枕詞って何？", true),
            ("剣の耐久値を教えて", true),
            ("マイクラで関圧番って何？", true),
            ("剣に持ち替えて", false),
            ("こんにちは", false),
            ("/say 枕詞って何？", false),
        ] {
            let result = render(
                &config,
                &llm,
                json!({"op":"assist_route","text":text}),
                &mut rx,
            )
            .await
            .unwrap();
            assert_eq!(result, json!({"knowledge_query":expected}));
        }
        assert!(
            render(&config, &llm, json!({"op":"assist_route"}), &mut rx)
                .await
                .is_err()
        );
        cancel.send(true).unwrap();
        assert!(
            render(
                &config,
                &llm,
                json!({"op":"assist_route","text":"枕詞って何？"}),
                &mut rx
            )
            .await
            .is_err()
        );
        let (owner, mut orphaned) = watch::channel(false);
        drop(owner);
        assert!(
            render(
                &config,
                &llm,
                json!({"op":"assist_route","text":"枕詞って何？"}),
                &mut orphaned
            )
            .await
            .is_err()
        );
    }
    #[tokio::test]
    async fn light_plan_cancellation_needs_no_python_or_overlay() {
        let config = DialogueConfig {
            python: "/missing/light-test-python".into(),
            helper: "/missing/light-test-helper.py".into(),
            ..Default::default()
        };
        let llm = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_secs(1)).unwrap();
        let (_owner, mut cancel) = watch::channel(true);
        let result = render(&config, &llm, json!({
            "op":"light_plan", "details":{},
            "fallback_payload":{"action":"stay_silent","basis_ids":["light_source_gain_observed"],"confidence":0.0}
        }), &mut cancel).await;
        assert_eq!(result.unwrap_err().to_string(), "cancelled");
    }
}
