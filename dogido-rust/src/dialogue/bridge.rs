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
        }
    }
}

impl From<Value> for Input {
    fn from(frame: Value) -> Self {
        Self {
            snapshot: None,
            context: None,
            retained_history_count: 0,
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
    render_with_route(config, llm, input, cancel, |_| Ok(()), |_| Ok(false)).await
}

pub async fn render_with_route(
    config: &DialogueConfig,
    llm: &RigLlm,
    input: impl Into<Input>,
    cancel: &mut watch::Receiver<bool>,
    select_route: impl FnMut(crate::foreground::Route) -> Result<()>,
    hold_handoff: impl FnMut(&Value) -> Result<bool>,
) -> Result<Value> {
    render_with_budget(
        config,
        llm,
        input.into(),
        cancel,
        select_route,
        hold_handoff,
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
    hold_handoff: impl FnMut(&Value) -> Result<bool>,
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
            json!({"knowledge_query":crate::knowledge::query::from_normalized(&prepared.normalized_text).is_some()}),
        );
    }
    let overlay = super::reading_runtime::load_overlay(config).await?;
    input["reading_corrections"] = json!(overlay);
    if let Some(prepared) = prepared {
        let context = crate::input_context::Context::from_prepared(
            &prepared,
            &overlay,
            chrono::Local::now().fixed_offset(),
        );
        if input["op"] == "address_route" {
            ensure!(
                !*cancel.borrow() && cancel.has_changed().is_ok(),
                "cancelled"
            );
            return Ok(
                json!({"op":"result","general_conversation":context.general_conversation()}),
            );
        }
        input.context = Some(context);
        input["language_requested"] = (config.language_enabled
            && (input["language_active"] == true || prepared.explicit_language))
            .into();
    }
    ensure!(
        input["op"] != "address_route",
        "address routing needs current text"
    );
    if input["op"] == "combat_leaf" {
        return super::reaction_runtime::render(config, llm, &input, cancel).await;
    }
    super::chat_runtime::render(
        config,
        llm,
        &input,
        cancel,
        select_route,
        hold_handoff,
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
    async fn address_classification_needs_no_helper_or_model() {
        let mut config = DialogueConfig {
            python: "/missing/input-test-python".into(),
            helper: "/missing/input-test-helper.py".into(),
            ..Default::default()
        };
        config.haiku.memory_enabled = false;
        let llm = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_secs(1)).unwrap();
        let (cancel, mut rx) = watch::channel(false);
        for (text, expected) in [
            ("", false),
            ("\u{1c}", false),
            ("こんにちは", true),
            ("家を建てたいな", true),
            ("枕詞って何？", false),
            ("剣に持ち替えて", false),
            ("草地はくさち", true),
            ("そうちじゃなくてくさち", true),
            ("今日の句", false),
            ("松明ある？", false),
            ("/say こんにちは", false),
        ] {
            let result = render(
                &config,
                &llm,
                json!({"op":"address_route","text":text}),
                &mut rx,
            )
            .await
            .unwrap();
            assert_eq!(
                result,
                json!({"op":"result","general_conversation":expected}),
                "{text}"
            );
        }
        assert!(
            render(&config, &llm, json!({"op":"address_route"}), &mut rx)
                .await
                .is_err()
        );
        cancel.send(true).unwrap();
        assert!(
            render(
                &config,
                &llm,
                json!({"op":"address_route","text":"こんにちは"}),
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
                json!({"op":"address_route","text":"こんにちは"}),
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
