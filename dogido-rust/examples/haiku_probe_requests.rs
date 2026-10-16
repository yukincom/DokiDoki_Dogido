//! 内部表現の独立測定へ、本体と同じ発句準備・promptを渡す読み取り専用の入口。
//! stdinのStartと任意の実モデル応答を既存Preparationへ渡し、次段の要求をstdoutへ返す。
//! 通信・音声・記憶保存は実行しない。draft要求は既存生成器の最初のBackend呼出しから取得する。
use anyhow::{Context, Result, bail};
use dogido_rust::{
    haiku::{
        self, Backend, LineForm, StructuredRequest, TransformRequest,
        preparation::{Preparation, Start},
    },
    haiku_prompt, haiku_response,
    types::GeneratedText,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::io::{self, Read};

#[derive(Deserialize)]
struct ProbeInput {
    start: Start,
    irony: Option<Value>,
    scene: Option<Value>,
}

#[derive(Default)]
struct CaptureRequest(Option<StructuredRequest>);

impl Backend for CaptureRequest {
    async fn generate(&mut self, request: StructuredRequest) -> Result<Value> {
        if self.0.is_some() {
            bail!("expected exactly one draft request");
        }
        self.0 = Some(request);
        // 実モデル応答を捏造しない。空の候補で生成器を止め、要求だけを取得する。
        Ok(json!({"lines": []}))
    }
    async fn transform(&mut self, _: TransformRequest) -> Result<LineForm> {
        bail!("request capture must not reach text transformation")
    }
}

fn prepared(request: &StructuredRequest) -> Result<Value> {
    Ok(json!({"request": request, "messages": haiku_prompt::messages(request)?}))
}

#[tokio::main]
async fn main() -> Result<()> {
    let mut source = String::new();
    io::stdin().read_to_string(&mut source)?;
    let value: Value = serde_json::from_str(&source)?;
    if let Some(parse) = value.get("parse") {
        let request: StructuredRequest = serde_json::from_value(parse["request"].clone())?;
        let generated: GeneratedText = serde_json::from_value(parse["generated"].clone())?;
        let parsed = haiku_response::parse(&request.kind, &generated, &request.fallback_value)?;
        println!("{}", serde_json::to_string(&parsed)?);
        return Ok(());
    }
    let input: ProbeInput = serde_json::from_str(&source)?;
    let (mut preparation, context) = Preparation::capture(input.start)?;
    let mut output = json!({"context": context});
    output["next"] = prepared(context.request.as_ref().context("LLM-disabled input")?)?;
    if let Some(irony) = input.irony {
        let inspiration = preparation.inspiration(&irony)?;
        output["spoken_text"] = json!(inspiration.spoken_text);
        output["next"] = prepared(&inspiration.request)?;
        if let Some(scene) = input.scene {
            let materials = preparation.materials(&scene)?;
            let mut capture = CaptureRequest::default();
            let _ = haiku::generate(&mut capture, materials.input.clone()).await?;
            output["materials"] = serde_json::to_value(materials)?;
            output["next"] = prepared(capture.0.as_ref().context("no draft request")?)?;
        }
    }
    println!("{}", serde_json::to_string(&output)?);
    Ok(())
}
