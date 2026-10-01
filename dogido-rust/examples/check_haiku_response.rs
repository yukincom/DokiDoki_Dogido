//! Offline JSONL parser probe. No provider, sockets, audio, or state machine.
use anyhow::Context;
use dogido_rust::{haiku_response, types::GeneratedText};
use serde::Deserialize;
use serde_json::Value;
use std::io::{self, BufRead, Write};

#[derive(Deserialize)]
struct Request {
    kind: String,
    generated: GeneratedText,
    fallback: Value,
}

fn main() -> anyhow::Result<()> {
    let mut out = io::BufWriter::new(io::stdout().lock());
    for (index, line) in io::stdin().lock().lines().enumerate() {
        let request: Request = serde_json::from_str(&line?)
            .with_context(|| format!("invalid request on line {}", index + 1))?;
        let response = haiku_response::parse(&request.kind, &request.generated, &request.fallback)
            .with_context(|| format!("parser error on line {}", index + 1))?;
        writeln!(out, "{response}")?;
    }
    Ok(())
}
