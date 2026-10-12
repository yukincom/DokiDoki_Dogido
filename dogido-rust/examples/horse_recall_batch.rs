//! 馬呼び戻し二段階の検証境界。productionのprompt/decoder/validatorで検証し、世界操作は行わない。
use dogido_rust::{
    assist::horse_recall::{
        contracts::{Prepared, Snapshot},
        intent::{self, Decision, Stage},
    },
    chat_prompt,
};
use serde_json::{Value, json};
use std::io::{self, BufRead};
fn run(row: Value) -> anyhow::Result<Value> {
    let snapshot: Snapshot = serde_json::from_value(row["snapshot"].clone())?;
    let original = row["text"].as_str().unwrap_or("");
    let prepared = Prepared::new(snapshot, original)?;
    let stage: Stage = serde_json::from_value(row["stage"].clone())?;
    if row["op"] == "request" {
        let mut details = row["details"].as_object().cloned().unwrap_or_default();
        details.insert("user_text".into(), original.into());
        details.insert("dialogue_choice".into(), true.into());
        let base = chat_prompt::Input {
            schema_version: 1,
            kind: "player_chat".into(),
            model: row["model"].as_str().unwrap_or("fixture").into(),
            details: Value::Object(details),
            temperature: 0.65,
            max_tokens: 192,
            enable_thinking: false,
        }
        .into_request()?;
        let (request, tools) = intent::request(base, &prepared.original, Some(&prepared), stage)?;
        let mut wire = json!({"model":request.model,"messages":request.messages,"temperature":request.temperature,
            "max_tokens":request.max_tokens,"stream":false,
            "chat_template_kwargs":{"enable_thinking":false,"preserve_thinking":false}});
        if !tools.is_empty() {
            wire["tools"] = json!(tools);
            wire["tool_choice"] = "auto".into();
        }
        return Ok(wire);
    }
    let response = &row["response"]["choices"][0];
    let message = &response["message"];
    let outcome = match intent::decode(
        stage,
        message,
        response["finish_reason"].as_str().unwrap_or(""),
    ) {
        Ok(Decision::List(intent)) => match intent.validate(original) {
            Ok(()) => json!({"kind":"list","intent":intent,"valid":true}),
            Err(e) => json!({"kind":"rejected","error":e.to_string(),"valid":false}),
        },
        Ok(Decision::Select(choice)) => match prepared.resolve(&choice) {
            Ok(horse) => {
                json!({"kind":"recall","entity_id":horse.entity_id,"choice":choice,"valid":true})
            }
            Err(e) => json!({"kind":"rejected","error":e.to_string(),"valid":false}),
        },
        Ok(Decision::Conversation) => {
            let o = intent::conversation(message["content"].as_str().unwrap_or(""));
            json!({"kind":"conversation","action":o.action,"text":o.text,"status":o.status,"reason":o.reason,"valid":o.status!="rejected"})
        }
        Err(e) => json!({"kind":"rejected","error":e.to_string(),"valid":false}),
    };
    Ok(outcome)
}
fn main() {
    for line in io::stdin().lock().lines() {
        let result = line
            .map_err(anyhow::Error::from)
            .and_then(|s| Ok(serde_json::from_str::<Value>(&s)?))
            .and_then(run);
        println!(
            "{}",
            result.unwrap_or_else(|e| json!({"error":e.to_string()}))
        );
    }
}
