//! stdin/stdoutだけの比較器。HTTP・LLM・音声・記憶storeは作らない。
use dogido_rust::{
    events::GameEvent,
    ingress::{InputQueue, PendingInput, PendingPolicy, SequenceLedger},
};
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};

fn trace(steps: &[Value]) -> Value {
    let mut ledger = SequenceLedger::default();
    let mut queue = InputQueue::default();
    let mut output = Vec::new();
    for step in steps {
        let result = match step["op"].as_str().unwrap() {
            "admit" => {
                json!({"admission":ledger.admit(step["sequence"].as_i64(), step["key"].as_str()),
                "last_sequence":ledger.last_sequence})
            }
            "heartbeat" => {
                if let Some(sequence) = step["sequence"].as_i64() {
                    ledger.last_sequence = Some(sequence);
                }
                json!({"last_sequence":ledger.last_sequence})
            }
            "enqueue" | "replace" => {
                let input: PendingInput = serde_json::from_value(step["input"].clone()).unwrap();
                let policy = if step["op"] == "replace" {
                    PendingPolicy::Replace
                } else {
                    PendingPolicy::Preserve
                };
                json!({"accepted":queue.push(input, policy)})
            }
            "direct" => {
                queue.remove_direct_duplicate(step["text"].as_str().unwrap());
                Value::Null
            }
            "take" => serde_json::to_value(queue.take_pending()).unwrap(),
            "promote" => {
                queue.promote();
                Value::Null
            }
            _ => panic!("unknown comparison operation"),
        };
        output.push(json!({"result":result,"queue":queue}));
    }
    json!({"trace":output})
}

fn main() -> anyhow::Result<()> {
    let mut out = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let request: Value = serde_json::from_str(&line?)?;
        let response = if let Some(steps) = request.get("steps") {
            trace(steps.as_array().unwrap())
        } else {
            match GameEvent::parse(request["event"].clone()) {
                Ok(event) => json!({"accepted":true,"event":event}),
                Err(reason) => json!({"accepted":false,"reason":reason}),
            }
        };
        writeln!(out, "{response}")?;
    }
    Ok(())
}
