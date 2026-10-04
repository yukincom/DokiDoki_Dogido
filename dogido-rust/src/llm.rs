use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail, ensure};
use rig_core::{
    client::CompletionClient,
    completion::{CompletionRequest, Message},
    providers::openai,
};
use serde_json::{Value, json};

use crate::types::{GeneratedText, GenerationReport, GenerationRequest, Role};

/// この段階では一要求を一回だけ生成する。会話の状態・記憶は所有しない。
pub struct RigLlm {
    client: openai::CompletionsClient,
    timeout: Duration,
    enabled: bool,
}

impl RigLlm {
    pub fn new(base_url: &str, api_key: Option<&str>, timeout: Duration) -> Result<Self> {
        let url = reqwest::Url::parse(base_url).context("invalid LLM base URL")?;
        ensure!(
            matches!(url.scheme(), "http" | "https"),
            "LLM URL must be HTTP(S)"
        );
        ensure!(
            url.query().is_none() && url.fragment().is_none(),
            "LLM URL must not contain query or fragment"
        );
        ensure!(!timeout.is_zero(), "timeout must be positive");
        let http = reqwest::Client::builder()
            .timeout(timeout)
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .build()?;
        // RigのOpenAI clientはキーを必須にする。ローカルの無認証APIには公開の仮値を送る。
        let client = openai::Client::builder()
            .api_key(api_key.unwrap_or("dogido-local-no-key"))
            .base_url(base_url.trim_end_matches('/'))
            .http_client(http)
            .build()?
            .completions_api();
        Ok(Self {
            client,
            timeout,
            enabled: true,
        })
    }

    pub fn with_enabled(mut self, enabled: bool) -> Self {
        self.enabled = enabled;
        self
    }

    pub fn set_enabled(&mut self, enabled: bool) {
        self.enabled = enabled;
    }

    pub async fn generate(&self, input: &GenerationRequest) -> Result<GenerationReport> {
        ensure!(self.enabled, "LLM disabled by configuration");
        input.validate()?;
        let request = to_rig_request(input);
        let model = self.client.completion_model(&input.model);
        let start = Instant::now();
        let response = tokio::time::timeout(self.timeout, model.raw_completion(request))
            .await
            .context("LLM request timed out; no retry was sent")?
            .context("Rig Chat Completions request failed")?;
        let generated = decode_response(&serde_json::to_value(&response)?)?;
        Ok(GenerationReport {
            schema_version: 1,
            provider: "rig-core/openai-chat-completions".into(),
            kind: input.kind.clone(),
            requested_model: input.model.clone(),
            response_model: response.model,
            response_id: response.id,
            elapsed_ms: start.elapsed().as_millis(),
            generated,
        })
    }
}

fn to_rig_request(input: &GenerationRequest) -> CompletionRequest {
    let chat_history = input
        .messages
        .iter()
        .map(|message| match message.role {
            Role::System | Role::Developer => Message::system(&message.content),
            Role::User => Message::user(&message.content),
            Role::Assistant => Message::assistant(&message.content),
        })
        .collect();
    CompletionRequest {
        model: Some(input.model.clone()),
        preamble: None,
        chat_history,
        documents: Vec::new(),
        tools: Vec::new(),
        temperature: Some(input.temperature),
        max_tokens: Some(input.max_tokens),
        tool_choice: None,
        // Rig標準のsystem/assistant配列化を避け、既存MLXと同じroleと文字列を送る。
        additional_params: Some(json!({
            "messages": input.messages,
            "stream": false,
            "chat_template_kwargs": {"enable_thinking": input.enable_thinking}
        })),
        output_schema: None,
        record_telemetry_content: false,
    }
}

/// 応答本文を選択し、終了理由・使用量を保持する。JSON本文のドメイン検査は呼出側が行う。
pub fn decode_response(body: &Value) -> Result<GeneratedText> {
    let choice = body
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|v| v.first())
        .context("chat_completions response has no choices")?;
    let message = &choice["message"];
    let mut text = match &message["content"] {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter(|part| part["type"] == "text")
            .filter_map(|part| part["text"].as_str())
            .filter(|text| !text.is_empty())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    };
    for alternative in [
        &message["reasoning"],
        &message["reasoning_content"],
        &choice["text"],
    ] {
        if text.trim().is_empty()
            && let Some(value) = alternative.as_str()
        {
            text = value.to_owned();
        }
    }
    if text.trim().is_empty() {
        bail!("chat_completions response content is empty");
    }
    let finish_reason = choice["finish_reason"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_owned);
    Ok(GeneratedText {
        text,
        finish_reason,
        completion_tokens: body["usage"]["completion_tokens"].as_u64(),
        prompt_tokens: body["usage"]["prompt_tokens"].as_u64(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn disabled_provider_returns_before_network_access() {
        let client = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_secs(1))
            .unwrap()
            .with_enabled(false);
        let request = GenerationRequest {
            schema_version: 1,
            kind: "disabled-check".into(),
            model: "fixture".into(),
            messages: vec![crate::types::ChatMessage {
                role: Role::User,
                content: "こんにちは".into(),
            }],
            temperature: 0.0,
            max_tokens: 8,
            enable_thinking: false,
        };
        assert_eq!(
            client.generate(&request).await.unwrap_err().to_string(),
            "LLM disabled by configuration"
        );
    }

    #[test]
    fn truncated_json_keeps_finish_reason_and_token_count() {
        let output = decode_response(&json!({
            "choices": [{"message": {"content": "{\"verdicts\":"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 1234, "completion_tokens": 512}
        }))
        .unwrap();
        assert_eq!(output.text, "{\"verdicts\":");
        assert_eq!(output.finish_reason.as_deref(), Some("length"));
        assert_eq!(output.completion_tokens, Some(512));
    }

    #[test]
    fn absent_usage_remains_unknown_and_text_parts_keep_their_separation() {
        let output = decode_response(&json!({"choices": [{"message": {"content": [
            {"type": "text", "text": "そうやな。"}, {"type": "text", "text": "帰ろか。"}
        ]}}]}))
        .unwrap();
        assert_eq!(output.text, "そうやな。\n帰ろか。");
        assert_eq!(output.prompt_tokens, None);
        assert_eq!(output.completion_tokens, None);
        assert_eq!(output.finish_reason, None);
    }

    #[test]
    fn empty_or_missing_choices_are_errors() {
        assert!(decode_response(&json!({"choices": []})).is_err());
        assert!(decode_response(&json!({"choices": [{"message": {"content": " "}}]})).is_err());
    }

    #[test]
    fn reasoning_fallback_matches_the_current_python_provider() {
        let output = decode_response(&json!({"choices": [{"message": {
            "content": "", "reasoning_content": "確認できたで。"
        }}]}))
        .unwrap();
        assert_eq!(output.text, "確認できたで。");
    }
}
