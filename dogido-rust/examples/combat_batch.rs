//! 戦闘EngineのJSONL系列リプレイ。通信・LLM・音声・HTTPは起動しない。
use dogido_rust::{
    combat::{core::Engine, model::Settings},
    events::GameEvent,
    threats,
};
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};

fn main() -> anyhow::Result<()> {
    let mut output = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let case: Value = serde_json::from_str(&line?)?;
        let settings = Settings::merged(
            case.get("settings")
                .and_then(Value::as_object)
                .unwrap_or(&Default::default()),
        )?;
        let warning_settings: threats::Settings =
            serde_json::from_value(case.get("warning_settings").cloned().unwrap_or(json!({})))?;
        warning_settings.validate()?;
        let mut engine = Engine::default();
        let mut rows = Vec::new();
        for step in case["steps"]
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("steps must be an array"))?
        {
            let event = GameEvent::parse(step["event"].clone()).map_err(anyhow::Error::msg)?;
            let now = step["ms"]
                .as_u64()
                .ok_or_else(|| anyhow::anyhow!("ms must be an unsigned integer"))?;
            if let Some(active) = step["dark_push_active"].as_bool() {
                engine.set_dark_push_active(active);
            }
            let decision = if let Some(input) = step["input"].as_str() {
                engine.input(&event, input, now, &settings, &warning_settings)
            } else {
                Some(engine.observe(
                    &event,
                    now,
                    step["complete"].as_bool().unwrap_or(true),
                    step["busy"].as_bool().unwrap_or(false),
                    &settings,
                    &warning_settings,
                ))
            };
            let notes = engine.take_notes();
            rows.push(json!({"replay_schema":1,"decision":decision,"mode":engine.mode,"shut_up_count":engine.shut_up_count,"notes":notes}));
        }
        writeln!(output, "{}", json!(rows))?;
    }
    Ok(())
}
