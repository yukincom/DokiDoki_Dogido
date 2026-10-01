//! Pure environment replay; no network, model, audio, or server.
use dogido_rust::{
    combat::model::{Mode, Settings},
    environment::danger::Danger,
    events::GameEvent,
};
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};
fn main() -> anyhow::Result<()> {
    let mut out = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let case: Value = serde_json::from_str(&line?)?;
        let mut s = Settings::default();
        s.0.extend(dogido_rust::environment::danger::defaults().clone());
        if let Some(overrides) = case["settings"].as_object() {
            s.0.extend(overrides.clone());
        }
        let mut engine = Danger::default();
        let mut rows = vec![];
        for step in case["steps"]
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("steps"))?
        {
            let event = GameEvent::parse(step["event"].clone()).map_err(anyhow::Error::msg)?;
            let now = step["ms"].as_u64().ok_or_else(|| anyhow::anyhow!("ms"))?;
            let mode = match step["mode"].as_str().unwrap_or("normal") {
                "alert" => Mode::Alert,
                "panic" => Mode::Panic,
                "suppressed_panic" => Mode::SuppressedPanic,
                "aftermath" => Mode::Aftermath,
                _ => Mode::Normal,
            };
            engine.set_presence(
                step["boss"].as_bool().unwrap_or(false),
                step["ominous"].as_bool().unwrap_or(false),
            );
            engine.update(&event, now, step["complete"].as_bool().unwrap_or(true), &s);
            let focus = step["focus"].as_bool().unwrap_or(false);
            let mut actions = engine.urgent(&event, now, mode, focus, &s);
            if actions.is_empty() {
                actions = engine.actions(
                    &event,
                    now,
                    mode,
                    step["busy"].as_bool().unwrap_or(false),
                    focus,
                    &s,
                );
            }
            engine.finish_frame(mode);
            rows.push(json!({"actions":actions,"active":engine.dark_push_active(),"light":engine.light_context(&event,&s)}));
        }
        writeln!(out, "{}", json!(rows))?;
    }
    Ok(())
}
