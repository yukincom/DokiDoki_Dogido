//! BERT受理後の一回の個体選択を、本体のprompt/decoder/validatorで検査する。世界操作は行わない。
use dogido_rust::{
    assist::horse_recall::{
        contracts::{Prepared, Snapshot},
        intent::{self, Decision},
    },
    chat_prompt,
    request_classifier::Classification,
};
use serde_json::{Value, json};
use std::io::{self, BufRead};
fn run(row: Value) -> anyhow::Result<Value> {
    // 保存済みの共通観測を本体と同じ差分台帳へ流す、実測比較用の読み取り操作。
    if row["op"] == "observation_changes" {
        let mut state = dogido_rust::conversation_observation::State::default();
        let mut contexts = Vec::new();
        for frame in row["contexts"]
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("contexts required"))?
        {
            state.observe(serde_json::from_value(frame.clone())?);
            contexts.push(state.context().unwrap());
        }
        return Ok(json!(contexts));
    }
    let snapshot: Snapshot = serde_json::from_value(row["snapshot"].clone())?;
    // BERTへ変更済み。旧判定へ戻さない。入力には受理済み分類を明示する。
    let classification: Classification = serde_json::from_value(row["classification"].clone())?;
    let original = classification.text.as_str();
    let prepared = Prepared::from_classified(snapshot, &classification)?;
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
        let (request, tools) = intent::request(base, &prepared)?;
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
    let outcome = match intent::decode(message, response["finish_reason"].as_str().unwrap_or("")) {
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
