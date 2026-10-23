//! BERT分類済みの支援配送をJSONLで検査する。接続や旧語句判定は行わない。
use chrono::{DateTime, Utc};
use dogido_rust::{
    assist::{self, AssistState, Submission},
    events::{AdapterCommandResult, GameEvent, HotbarSlot},
    request_classifier::Classification,
};
use serde_json::{Value, json};
use std::io::{self, BufRead};
fn run(row: Value) -> anyhow::Result<Value> {
    Ok(match row["op"].as_str().unwrap_or("") {
        "select" => {
            let slots: Vec<HotbarSlot> = serde_json::from_value(row["slots"].clone())?;
            json!(assist::select_weapon_slot(&slots))
        }
        "session" => {
            let mut state = AssistState::new(serde_json::from_value::<Vec<String>>(
                row["capabilities"].clone(),
            )?);
            let mut outputs = Vec::new();
            let mut ids = Vec::<String>::new();
            for step in row["steps"]
                .as_array()
                .ok_or_else(|| anyhow::anyhow!("steps"))?
            {
                let now: DateTime<Utc> = serde_json::from_value(step["now"].clone())?;
                let busy = step["busy"].as_bool().unwrap_or(false);
                if step["type"] == "input" {
                    let classification: Classification =
                        serde_json::from_value(step["classification"].clone())?;
                    let event =
                        GameEvent::parse(step["event"].clone()).map_err(anyhow::Error::msg)?;
                    // BERTへ変更済み。旧判定へ戻さない。呼出側の明示分類を使う。
                    let result = state.submit_classified(&classification, &event, now, busy);
                    match result {
                        Submission::Handled(d) => {
                            if let Some(c) = &d.command {
                                ids.push(c.command_id.clone());
                            }
                            outputs.push(json!({"handled":true,"command": d.command.map(|c| json!({"slot":c.slot,"expected_item_id":c.expected_item_id,"issued_at":c.issued_at,"expires_at":c.expires_at})),"feedback":d.feedback}));
                        }
                        Submission::NotHandled => outputs.push(json!({"handled":false})),
                    }
                } else if step["type"] == "results" {
                    let mut results = step["results"].clone();
                    for result in results
                        .as_array_mut()
                        .ok_or_else(|| anyhow::anyhow!("results"))?
                    {
                        if let Some(i) = result["command_id"]
                            .as_str()
                            .and_then(|s| s.strip_prefix('$'))
                            .and_then(|s| s.parse::<usize>().ok())
                        {
                            result["command_id"] = json!(ids[i]);
                        }
                    }
                    let results: Vec<AdapterCommandResult> = serde_json::from_value(results)?;
                    let out = state.observe_results(&results, now, busy);
                    let alias = |s: &str| {
                        ids.iter()
                            .position(|id| id == s)
                            .map(|i| format!("${i}"))
                            .unwrap_or_else(|| s.into())
                    };
                    outputs.push(json!({"acked":out.acknowledged_ids.iter().map(|id|alias(id)).collect::<Vec<_>>(),"observed":out.observed.iter().map(|o|json!({"id":alias(&o.result.command_id),"status":o.result.status,"detail":o.result.detail_code})).collect::<Vec<_>>(),"feedback":out.feedback}));
                } else {
                    outputs.push(json!(state.pending_commands(now).iter().map(|c| json!({"slot":c.slot,"expected_item_id":c.expected_item_id,"issued_at":c.issued_at,"expires_at":c.expires_at})).collect::<Vec<_>>()));
                }
            }
            json!(outputs)
        }
        _ => anyhow::bail!("unknown operation"),
    })
}
fn main() -> anyhow::Result<()> {
    for line in io::stdin().lock().lines() {
        let value = run(serde_json::from_str(&line?)?)?;
        println!("{}", serde_json::to_string(&value)?);
    }
    Ok(())
}
