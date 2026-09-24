//! 警告系列比較用。モデル・音声・HTTPなし。
use dogido_rust::{
    events::GameEvent,
    threats::{Policy, Settings},
};
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};
fn main() -> anyhow::Result<()> {
    let mut out = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let v: Value = serde_json::from_str(&line?)?;
        let settings: Settings =
            serde_json::from_value(v.get("settings").cloned().unwrap_or(json!({})))?;
        settings.validate()?;
        let mut policy = Policy::default();
        let mut rows = Vec::new();
        for step in v["steps"].as_array().unwrap() {
            let event: GameEvent = serde_json::from_value(step["event"].clone())?;
            let plan = policy.observe(
                &event,
                step["ms"].as_u64().unwrap(),
                step["busy"].as_bool().unwrap_or(false),
                &settings,
            );
            let mut actions = Vec::new();
            if let Some(plan) = plan {
                if let Some(c) = &plan.cue {
                    actions.push(json!({"text":c.text,"cue_id":c.id,"cue_sequence":[]}));
                }
                if !plan.text.is_empty() {
                    actions.push(json!({"text":plan.text,"cue_id":null,"cue_sequence":if plan.fragment_paths(&settings.cue_dir).is_some() {plan.cue_sequence.clone()} else {vec![]}}));
                }
            }
            rows.push(actions);
        }
        writeln!(out, "{}", json!(rows))?;
    }
    Ok(())
}
