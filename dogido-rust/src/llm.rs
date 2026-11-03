//! 既存Chat Completionsへ一要求を一回送信し、本文と終了情報だけを返す。
//! 任意画像は通常会話・既存の環境反応の最後のuser入力にだけ添付し、履歴・保存・取消の判断は呼出側が所有する。
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail, ensure};
use rig_core::{
    client::CompletionClient,
    completion::{CompletionRequest, Message},
    providers::openai,
};
use serde_json::{Value, json};
use tracing::instrument::WithSubscriber;

use crate::types::{GeneratedText, GenerationReport, GenerationRequest, Role};

/// 採取あり・送信前の帰属記録なしは、旧形式の採取だけを行う。
pub(crate) enum CaptureMode<'a> {
    Off,
    Record { request_id: Option<&'a str> },
}

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
        Ok(self.complete(input, &[], &[], false, None, CaptureMode::Off).await?.0)
    }

    /// 照合済みの時系列画像を同じ会話・環境反応の要求へ添え、一回だけ送信する。
    /// 不正画像・通信失敗でも画像なしの再送はせず、画像本文を結果やログへ戻さない。
    pub async fn generate_with_image(
        &self,
        input: &GenerationRequest,
        image: &crate::chat_image::Attachment,
    ) -> Result<GenerationReport> {
        Ok(self
            .complete(input, &[], &[], false, Some(image), CaptureMode::Off)
            .await?
            .0)
    }

    /// 許可された道具定義と、直前の呼出し・実行結果を既存Chat Completionsへ載せる。
    /// 道具の実行判断は呼び手の担当。確認後はallow_call=falseで追加呼出しを止める。
    pub(crate) async fn generate_with_tools(
        &self,
        input: &GenerationRequest,
        tools: &[Value],
        tool_messages: &[Value],
        allow_call: bool,
    ) -> Result<(GenerationReport, Option<Value>)> {
        self.complete(input, tools, tool_messages, allow_call, None, CaptureMode::Off)
            .await
    }

    /// 自動発句だけに任意の採取フラグを付け、同じ要求を一回だけ送る。
    pub(crate) async fn generate_with_capture(
        &self,
        input: &GenerationRequest,
        capture: CaptureMode<'_>,
    ) -> Result<GenerationReport> {
        ensure!(
            matches!(capture, CaptureMode::Off) || crate::haiku_capture::supports(&input.kind),
            "unsupported_capture_kind"
        );
        Ok(self
            .complete(input, &[], &[], false, None, capture)
            .await?
            .0)
    }

    /// 通常会話の可視本文だけを上限付きチャネルへ渡す。同じ要求を一回だけ送り、
    /// reasoning/toolは発声へ混ぜず、終端通知の欠落は途中切断として返す。
    pub(crate) async fn generate_stream(
        &self,
        input: &GenerationRequest,
        image: Option<&crate::chat_image::Attachment>,
        deltas: Option<tokio::sync::mpsc::Sender<String>>,
        received: impl FnMut(&str) -> Result<()>,
    ) -> Result<GenerationReport> {
        // Rig受信futureは大きい。音声・文字の両経路で再生workerのstackへ積み重ねない。
        Ok(Box::pin(self.stream(input, image, &[], &[], false, deltas, received)).await?.0)
    }

    /// 相談の同じ一要求をstreamで受け取る。道具の差分は発話へ送らず、完成した呼出しだけ返す。
    pub(crate) async fn generate_stream_with_tools(
        &self,
        input: &GenerationRequest,
        tools: &[Value],
        tool_messages: &[Value],
        allow_call: bool,
        deltas: tokio::sync::mpsc::Sender<String>,
    ) -> Result<(GenerationReport, Option<Value>)> {
        Box::pin(self.stream(input, None, tools, tool_messages, allow_call, Some(deltas), |_| Ok(())))
            .await
    }

    async fn stream(
        &self,
        input: &GenerationRequest,
        image: Option<&crate::chat_image::Attachment>,
        tools: &[Value],
        tool_messages: &[Value],
        allow_call: bool,
        deltas: Option<tokio::sync::mpsc::Sender<String>>,
        mut received: impl FnMut(&str) -> Result<()>,
    ) -> Result<(GenerationReport, Option<Value>)> {
        use futures_util::StreamExt;
        use rig_core::{completion::CompletionModel, streaming::StreamedAssistantContent};
        ensure!(self.enabled, "LLM disabled by configuration");
        input.validate()?;
        let mut request = request_with_image(input, image)?;
        add_tools(&mut request, tools, tool_messages, allow_call);
        let model = self.client.completion_model(&input.model);
        let start = Instant::now();
        let run = async {
            // Rigが道具の引数を組み立てる。本文は差分のまま、呼出しは完成時に一度だけ受け取る。
            let mut stream = model.stream(request).await?;
            let mut text = String::new();
            let mut terminal = None;
            let mut calls = Vec::new();
            while let Some(event) = stream.next().await {
                match event? {
                    StreamedAssistantContent::Text(part) => {
                        let delta = part.text;
                        ensure!(
                            text.len().saturating_add(delta.len())
                                <= stream_text_limit(input.max_tokens),
                            "LLM speech stream exceeds response limit"
                        );
                        text.push_str(&delta);
                        if !delta.is_empty() {
                            received(&text)?;
                            if let Some(deltas) = &deltas {
                                deltas.send(delta).await.context("speech stream closed")?;
                            }
                        }
                    }
                    StreamedAssistantContent::Final(response) => terminal = Some(response.raw),
                    StreamedAssistantContent::ToolCall { tool_call, .. } => {
                        ensure!(!tools.is_empty(), "unexpected tool call in ordinary speech");
                        calls.push(json!({
                            "id": tool_call.provider.map(|id| id.call_id),
                            "type": "function",
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": serde_json::to_string(&tool_call.function.arguments)?,
                            }
                        }));
                    }
                    StreamedAssistantContent::ToolCallDelta { .. } => {
                        ensure!(!tools.is_empty(), "unexpected tool call in ordinary speech");
                    }
                    _ => {}
                }
            }
            let terminal = terminal.context("LLM stream ended before completion")?;
            let tool_message = (!calls.is_empty()).then(|| {
                json!({
                "role":"assistant","content":text,"tool_calls":calls})
            });
            if let Some(message) = &tool_message {
                text = serde_json::to_string(message)?;
            }
            ensure!(
                !text.trim().is_empty(),
                "chat_completions response content is empty"
            );
            Ok((
                GenerationReport {
                    schema_version: 1,
                    provider: "rig-core/openai-chat-completions".into(),
                    kind: input.kind.clone(),
                    requested_model: input.model.clone(),
                    response_model: terminal["model"].as_str().unwrap_or(&input.model).into(),
                    response_id: terminal["response_id"].as_str().unwrap_or("").into(),
                    elapsed_ms: start.elapsed().as_millis(),
                    generated: GeneratedText {
                        text,
                        finish_reason: terminal["finish_reason"].as_str().map(str::to_owned),
                        completion_tokens: terminal["usage"]["completion_tokens"].as_u64(),
                        prompt_tokens: terminal["usage"]["prompt_tokens"].as_u64(),
                    },
                },
                tool_message,
            ))
        };
        // 画像要求だけはproviderのTRACEから画像本文を除く。streamの読取り中も同じ購読を使う。
        let result = if image.is_some() {
            tokio::time::timeout(
                self.timeout,
                run.with_subscriber(tracing::subscriber::NoSubscriber::default()),
            )
            .await
        } else {
            tokio::time::timeout(self.timeout, run).await
        };
        result.context("LLM stream timed out; no retry was sent")?
    }

    async fn complete(
        &self,
        input: &GenerationRequest,
        tools: &[Value],
        tool_messages: &[Value],
        allow_call: bool,
        image: Option<&crate::chat_image::Attachment>,
        capture: CaptureMode<'_>,
    ) -> Result<(GenerationReport, Option<Value>)> {
        ensure!(self.enabled, "LLM disabled by configuration");
        input.validate()?;
        let mut request = request_with_image(input, image)?;
        if let CaptureMode::Record { request_id } = capture {
            if let Some(token) = request_id {
                request.additional_params.as_mut().unwrap()["dogido_capture_request_id"] = token.into();
            }
            request.additional_params.as_mut().unwrap()["dogido_capture"] = true.into();
            if input.kind == "haiku_irony" {
                request.additional_params.as_mut().unwrap()["dogido_capture_full"] = true.into();
            }
        }
        add_tools(&mut request, tools, tool_messages, allow_call);
        let model = self.client.completion_model(&input.model);
        let start = Instant::now();
        let completion = model.raw_completion(request);
        // Rigはtelemetry本文を無効にしてもTRACEで送信JSONを出す。
        // 画像を含む要求だけprovider内部の購読を外し、base64を診断ログへ残さない。
        let response = if image.is_some() {
            tokio::time::timeout(
                self.timeout,
                completion.with_subscriber(tracing::subscriber::NoSubscriber::default()),
            )
            .await
        } else {
            tokio::time::timeout(self.timeout, completion).await
        }
        .context("LLM request timed out; no retry was sent")?
        .context("Rig Chat Completions request failed")?;
        let mut body = serde_json::to_value(&response)?;
        let message = &body["choices"][0]["message"];
        let tool_message = (!tools.is_empty()
            && message["tool_calls"]
                .as_array()
                .is_some_and(|calls| !calls.is_empty()))
        .then(|| message.clone());
        if let Some(message) = &tool_message {
            // 本文を持たないtool応答も終了理由・使用量つきで記録する。
            // 呼出し内容は別値で渡し、本文のJSONを実行命令として解釈しない。
            body["choices"][0]["message"]["content"] = serde_json::to_string(message)?.into();
        }
        let generated = decode_response(&body)?;
        Ok((
            GenerationReport {
                schema_version: 1,
                provider: "rig-core/openai-chat-completions".into(),
                kind: input.kind.clone(),
                requested_model: input.model.clone(),
                response_model: response.model,
                response_id: response.id,
                elapsed_ms: start.elapsed().as_millis(),
                generated,
            },
            tool_message,
        ))
    }
}

/// 通常の完了とstreamの双方で、同じnative tool契約を送る。
fn add_tools(
    request: &mut CompletionRequest,
    tools: &[Value],
    messages: &[Value],
    allow_call: bool,
) {
    if !tools.is_empty() {
        let params = request.additional_params.as_mut().unwrap();
        params["tools"] = json!(tools);
        params["tool_choice"] = if allow_call { "auto" } else { "none" }.into();
        params["messages"]
            .as_array_mut()
            .unwrap()
            .extend_from_slice(messages);
    }
}

mod image;
use image::request_with_image;

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

/// 音声待ちでLLM受信を止めないための、一返答全体のUTF-8 byte上限。
/// 設定のtoken数に十分な余裕を持たせ、絶対上限も置く。上限超過は切捨てず失敗にする。
pub(crate) fn stream_text_limit(max_tokens: u64) -> usize {
    usize::try_from(max_tokens)
        .unwrap_or(usize::MAX)
        .saturating_mul(64)
        .clamp(4096, 1_000_000)
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

    // 見込み: 取消前に生成したIDはHTTPの採取メタデータだけに属する。
    // 合格: 模擬窓口の各要求ペアで本文不変・旧フラグと同じ要求にIDが一度だけ付く。実モデルは使わない。
    #[tokio::test]
    async fn capture_flag_uses_one_existing_request_and_never_changes_messages() {
        use std::sync::{Arc, Mutex};
        let seen = Arc::new(Mutex::new(Vec::<Value>::new()));
        let state = seen.clone();
        let app = axum::Router::new().route("/chat/completions", axum::routing::post(
            move |axum::Json(body): axum::Json<Value>| {
                let state = state.clone();
                async move {
                    state.lock().unwrap().push(body);
                    axum::Json(json!({"id":"fixture-response","object":"chat.completion","created":0,
                        "model":"fixture","choices":[{"index":0,"message":{"role":"assistant","content":"返答"},"finish_reason":"stop"}],
                        "usage":{"prompt_tokens":4,"completion_tokens":1,"total_tokens":5}}))
                }
            }
        ));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        let llm = RigLlm::new(&format!("http://{address}"), None, Duration::from_secs(2)).unwrap();
        let token = uuid::Uuid::new_v4().to_string();
        for kind in [
            "haiku_irony",
            "haiku_scene",
            "haiku_draft",
            "haiku_line_regeneration",
        ] {
            let mut input = chat_request();
            input.kind = kind.into();
            llm.generate(&input).await.unwrap();
            llm.generate_with_capture(&input, CaptureMode::Record { request_id: Some(&token) }).await.unwrap();
            llm.generate_with_capture(&input, CaptureMode::Record { request_id: None }).await.unwrap();
        }
        for kind in ["player_chat", "haiku_workshop_revision"] {
            let mut input = chat_request();
            input.kind = kind.into();
            assert!(llm.generate_with_capture(&input, CaptureMode::Record { request_id: Some(&token) }).await.is_err());
        }
        let rows = seen.lock().unwrap();
        assert_eq!(rows.len(), 12);
        for (index, pair) in rows.as_chunks::<3>().0.iter().enumerate() {
            assert!(pair[0].get("dogido_capture").is_none());
            assert!(pair[0].get("dogido_capture_full").is_none());
            let mut captured = pair[1].clone();
            assert_eq!(captured.as_object_mut().unwrap().remove("dogido_capture_request_id"), Some(json!(token)));
            assert_eq!(pair[2], captured, "untracked requests keep the old capture flag");
            assert_eq!(
                captured.as_object_mut().unwrap().remove("dogido_capture"),
                Some(json!(true))
            );
            assert_eq!(
                captured
                    .as_object_mut()
                    .unwrap()
                    .remove("dogido_capture_full"),
                (index == 0).then(|| json!(true))
            );
            assert_eq!(pair[0], captured);
        }
        server.abort();
    }

    fn chat_request() -> GenerationRequest {
        GenerationRequest {
            schema_version: 1,
            kind: "player_chat".into(),
            model: "fixture".into(),
            messages: vec![
                crate::types::ChatMessage {
                    role: Role::System,
                    content: "system".into(),
                },
                crate::types::ChatMessage {
                    role: Role::User,
                    content: "先の質問".into(),
                },
                crate::types::ChatMessage {
                    role: Role::Assistant,
                    content: "先の返事".into(),
                },
                crate::types::ChatMessage {
                    role: Role::User,
                    content: "今回の質問".into(),
                },
                crate::types::ChatMessage {
                    role: Role::Assistant,
                    content: "後続は変更しない".into(),
                },
            ],
            temperature: 0.65,
            max_tokens: 72,
            enable_thinking: false,
        }
    }

    #[test]
    fn absent_image_preserves_text_payload_exactly() {
        let input = chat_request();
        let request = request_with_image(&input, None).unwrap();
        let parameters = request.additional_params.unwrap();
        assert_eq!(parameters["messages"], json!(input.messages));
        assert_eq!(
            parameters,
            to_rig_request(&input).additional_params.unwrap()
        );
        assert!(!request.record_telemetry_content);
    }

    // 見込み: 画像は最後のuserだけに付く。合格: 元の会話は不変で、今回はPNG一枚と実在する枠説明だけ。確認範囲: 送信用JSON、実モデルは対象外。
    #[test]
    fn image_changes_only_last_user_and_keeps_source_messages_unchanged() {
        let input = chat_request();
        let original = json!(input.messages);
        let image = crate::chat_image::tests::image();
        let request = request_with_image(&input, Some(&image)).unwrap();
        let parameters = request.additional_params.unwrap();
        let messages = parameters["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 5);
        for index in [0, 1, 2, 4] {
            assert_eq!(messages[index], original[index]);
        }
        assert_eq!(messages[3]["role"], "user");
        let content = messages[3]["content"].as_array().unwrap();
        assert_eq!(content.len(), 3);
        assert_eq!(content[0]["type"], "text");
        assert_eq!(
            content[0]["text"],
            "今回の質問"
        );
        assert_eq!(
            content[2],
            json!({"type":"image_url", "image_url":{"url":image.data_url()}})
        );
        assert_eq!(content[1]["text"], "緑のPLAYER枠はプレイヤーの位置やで");
        assert_eq!(json!(input.messages), original);
        assert_eq!(parameters["stream"], false);
        assert_eq!(parameters["chat_template_kwargs"]["enable_thinking"], false);
        assert!(!request.record_telemetry_content);
    }

    #[test]
    fn image_rejects_other_kind_missing_user_and_invalid_attachment() {
        let image = crate::chat_image::tests::image();
        let mut input = chat_request();
        input.kind = "haiku_irony".into();
        assert_eq!(
            request_with_image(&input, Some(&image))
                .unwrap_err()
                .to_string(),
            "chat_image_wrong_generation_kind"
        );
        input.kind = "player_chat".into();
        input.messages.retain(|message| message.role != Role::User);
        assert_eq!(
            request_with_image(&input, Some(&image))
                .unwrap_err()
                .to_string(),
            "chat_image_user_message_missing"
        );
        let mut image = image;
        image.sha256 = "0".repeat(64);
        assert_eq!(
            request_with_image(&chat_request(), Some(&image))
                .unwrap_err()
                .to_string(),
            "chat_image_sha256_mismatch"
        );
    }

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
    fn empty_content_uses_reasoning_content() {
        let output = decode_response(&json!({"choices": [{"message": {
            "content": "", "reasoning_content": "確認できたで。"
        }}]}))
        .unwrap();
        assert_eq!(output.text, "確認できたで。");
    }
}
