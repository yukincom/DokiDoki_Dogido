//! Scripted payloadsと既存辞書で生成器の回帰確認を行う。モデルHTTPは使わない。
use anyhow::{Context, Result};
use dogido_rust::{
    haiku::{self, Backend, Input, LineForm, StructuredRequest, TransformRequest},
    python_worker::Helper,
    haiku_response,
    types::GeneratedText,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    io::{self, BufRead},
    path::PathBuf,
};

#[derive(Deserialize)]
struct Case {
    input: Input,
    responses: Vec<Value>,
}
struct Replay<'a> {
    helper: &'a mut Helper,
    responses: VecDeque<Value>,
    requests: Vec<Value>,
    prompts: Vec<Value>,
}

impl Backend for Replay<'_> {
    async fn generate(&mut self, request: StructuredRequest) -> Result<Value> {
        self.requests.push(serde_json::to_value(&request)?);
        self.prompts
            .push(serde_json::to_value(self.helper.prepare(&request).await?)?);
        let response = self
            .responses
            .pop_front()
            .context("scripted responses exhausted")?;
        if response.get("raw").is_some() {
            let raw: GeneratedText = serde_json::from_value(response["raw"].clone())?;
            return haiku_response::parse(&request.kind, &raw, &request.fallback_value);
        }
        if response.get("error").is_some() {
            anyhow::bail!("scripted generation error");
        }
        Ok(response)
    }

    async fn transform(&mut self, request: TransformRequest) -> Result<LineForm> {
        self.helper.transform(request).await
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let python = std::env::args().nth(1).context("pass Python executable")?;
    let script = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("scripts/haiku_tokens.py");
    let mut helper = Helper::start(&PathBuf::from(python), &script)?;
    let result: Result<()> = async {
        for line in io::stdin().lock().lines() {
            let case: Case = serde_json::from_str(&line?)?;
            let mut backend = Replay {
                helper: &mut helper,
                responses: case.responses.into(),
                requests: vec![],
                prompts: vec![],
            };
            let result = haiku::generate(&mut backend, case.input).await?;
            println!(
                "{}",
                json!({"result":result,"requests":backend.requests,"prompts":backend.prompts,
                "remaining_responses":backend.responses.len()})
            );
        }
        Ok(())
    }
    .await;
    let cleanup = helper.finish(result.is_err()).await;
    result?;
    cleanup
}
