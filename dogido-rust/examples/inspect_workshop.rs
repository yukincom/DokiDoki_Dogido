use dogido_rust::{haiku_record::HaikuLine, workshop};
use serde_json::{Value, json};
use std::io::{self, BufRead};

fn main() -> anyhow::Result<()> {
    for line in io::stdin().lock().lines() {
        let input: Value = serde_json::from_str(&line?)?;
        let lines: Vec<HaikuLine> = serde_json::from_value(input["lines"].clone())?;
        let checks: Vec<String> = serde_json::from_value(input["checks"].clone())?;
        let observation = workshop::inspect(&lines, &checks);
        println!(
            "{}",
            json!({"observation":observation,"fallback":workshop::fallback(Some(&observation))})
        );
    }
    Ok(())
}
