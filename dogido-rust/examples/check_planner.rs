//! 比較器専用のJSONL入口。HTTP公開・モデル呼出・状態変更なし。
use dogido_rust::planner::{self, Details, Plan, repair};
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};

fn check(v: Value) -> anyhow::Result<Value> {
    Ok(match v["op"].as_str().unwrap() {
        "evaluate" => {
            let details: Details = serde_json::from_value(v["details"].clone())?;
            let errors = planner::contract_errors(&v["payload"], &details);
            json!({"accepted": errors.is_empty(), "errors": errors, "plan": planner::parse_model_plan(&v["payload"], &details)})
        }
        "messages" => {
            let details: Details = serde_json::from_value(v["details"].clone())?;
            let errors: Option<Vec<String>> = v
                .get("errors")
                .map(|e| serde_json::from_value(e.clone()).unwrap());
            json!(planner::messages(
                &details,
                errors.as_ref().map(|e| (e.as_slice(), &v["previous"]))
            ))
        }
        "repair" => {
            let details: Details = serde_json::from_value(v["details"].clone())?;
            let parsed = serde_json::from_value(v["payload"].clone())
                .ok()
                .and_then(|p| {
                    repair::parse(
                        serde_json::from_value(v["action"].clone()).unwrap(),
                        &p,
                        &details,
                    )
                });
            json!({"repair": parsed, "fallback": parsed.as_ref().map(|r| r.fallback()), "note": parsed.as_ref().map(|r| repair::note(&r.prompt_fields()))})
        }
        "signal" => json!(repair::has_signal(v["raw"].as_str().unwrap())),
        "pending" => repair::pending(v["history"].as_array().unwrap()),
        "ground" => {
            let plan: Plan = serde_json::from_value(v["plan"].clone())?;
            let grounding = planner::ground(
                &plan,
                v["hits"].as_array().unwrap(),
                v["observed"].as_array().unwrap(),
            );
            json!({"grounding": grounding, "fixed_reply": planner::fixed_reply(&plan, &grounding)})
        }
        "extract" => json!(planner::extract_object(v["text"].as_str().unwrap())),
        "prepared" => {
            let input: planner::PreparedPlan = serde_json::from_value(v["input"].clone())?;
            json!(input.validate().is_ok())
        }
        _ => anyhow::bail!("unknown comparison operation"),
    })
}
fn main() -> anyhow::Result<()> {
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let value = check(serde_json::from_str(&line?)?)?;
        writeln!(stdout, "{value}")?;
    }
    Ok(())
}
