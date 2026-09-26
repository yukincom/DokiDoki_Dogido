//! Offline record/HUD probe. The comparison harness supplies temporary roots.
use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use dogido_rust::haiku_record::{
    DEFAULT_IDLE_TIMEOUT, DEFAULT_OPEN_TIMEOUT, MemoryStore, PreparedEmission, Workshop,
    project_workshop,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    io::{self, BufRead, Write},
    path::PathBuf,
    time::{Duration, Instant},
};

#[derive(Deserialize)]
struct Request {
    emission: PreparedEmission,
    created_at: DateTime<Utc>,
    memory_root: PathBuf,
    session_id: String,
    player_name: String,
    #[serde(default)]
    steps: Vec<Value>,
}

fn run(request: Request) -> Result<Value> {
    let emission = request.emission.complete(request.created_at)?;
    let store = MemoryStore::new(request.memory_root);
    let first = store.save_agent_haiku(&request.session_id, &request.player_name, &emission)?;
    let second = store.save_agent_haiku(&request.session_id, &request.player_name, &emission)?;
    store.append_haiku_emission(&request.session_id, &emission)?;
    let completed = Instant::now();
    let mut workshop = Workshop::open(emission.clone(), Some(first.entry_id.clone()), completed);
    let mut trace = Vec::new();
    for step in request.steps {
        let now = completed + Duration::from_millis(step["ms"].as_u64().unwrap_or(0));
        let changed = match step["op"].as_str().context("missing step op")? {
            "pause" => json!(workshop.pause(now)),
            "resume" => json!(workshop.resume(now)),
            "activity" => {
                workshop.record_activity(now);
                Value::Null
            }
            "expire" => json!(
                workshop.expire(
                    now,
                    step["t_open_ms"]
                        .as_u64()
                        .map(Duration::from_millis)
                        .unwrap_or(DEFAULT_OPEN_TIMEOUT),
                    step["t_idle_ms"]
                        .as_u64()
                        .map(Duration::from_millis)
                        .unwrap_or(DEFAULT_IDLE_TIMEOUT)
                )
            ),
            "close" => {
                workshop.close(step["reason"].as_str().unwrap_or("test"));
                Value::Null
            }
            "project" => Value::Null,
            _ => anyhow::bail!("unknown step"),
        };
        trace.push(json!({"changed":changed,"close_reason":workshop.close_reason,
            "snapshot":project_workshop(Some(&workshop),&request.session_id,step["sequence"].as_i64(),
                step["mode"].as_str().unwrap_or("normal"),step["thinking"].as_bool().unwrap_or(false))}));
    }
    Ok(
        json!({"entry":first.entry,"inserted":first.inserted,"duplicate_inserted":second.inserted,
        "short_entry":emission.short_term_entry(&request.session_id),"materials":workshop.materials,"trace":trace}),
    )
}

fn main() -> Result<()> {
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        writeln!(stdout, "{}", run(serde_json::from_str(&line?)?)?)?;
    }
    Ok(())
}
