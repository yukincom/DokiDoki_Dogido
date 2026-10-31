//! パート6: 本体がモデルへ示す完成JSON例を抽出し、応答受信と身体操作の実パーサへ通す。モデル品質は評価しない。
use super::acceptance_body_results::{observation, sample};
use super::*;

fn ready() -> (State, Prepared, Instant, DateTime<Utc>) {
    let now = Instant::now();
    let utc = Utc::now();
    let sample = sample(1);
    let mut state = State::default();
    state.observe(Some(&observation(&sample)), Some(&sample), now, Some(0));
    let prepared = state.prepare(now).unwrap();
    (state, prepared, now, utc)
}
fn response(prepared: &Prepared) -> crate::environment::reaction::Prepared {
    crate::environment::reaction::Prepared::new(&json!({"kind":"environment_reaction","model":"test-only",
        "max_tokens":256,"fallback_text":"","details":{"reaction_context":{"topic":"surroundings",
        "body_options":{"available_actions":prepared.model_actions(),"candidates":prepared.model_candidates}}}}), "test-only").unwrap()
}
fn examples(text: &str) -> Vec<Value> {
    // JSONの引用符と括弧だけを扱い、placeholderを書き換えて完成例にしない。
    let mut values = Vec::new();
    for (start, _) in text.match_indices('{') {
        let mut decoder = serde_json::Deserializer::from_str(&text[start..]).into_iter::<Value>();
        if let Some(Ok(value)) = decoder.next() {
            values.push(value);
        }
    }
    values
}
fn selected_example(text: &str, action: &str) -> Value {
    examples(text)
        .into_iter()
        .find(|v| v["body_action"]["action"] == action)
        .unwrap_or_else(|| {
            panic!("本体プロンプトに{action}の完成JSON例がない。試験側で補作しない。")
        })
}
fn decode(prepared: &crate::environment::reaction::Prepared, example: &Value) -> Value {
    let generated = crate::types::GeneratedText {
        text: example.to_string(),
        finish_reason: Some("stop".into()),
        prompt_tokens: None,
        completion_tokens: None,
    };
    let (_, outcome) = prepared.finish(Some(&generated));
    assert!(
        matches!(outcome, "speak" | "silent"),
        "外側の応答JSONも本体が受理する: {outcome}"
    );
    prepared
        .body_action(Some(&generated))
        .expect("同じ応答からbody_actionを受け取る")
}

// 実際に選べる各行動の完成例を通常環境プロンプトから読む。全例が応答パーサとState::chooseの双方を通る。
#[test]
fn acceptance_body_schema_normal_prompt_examples_pass_real_receivers() {
    let (_, prepared, _, _) = ready();
    for action in prepared.available_actions {
        let (mut state, prepared, now, utc) = ready();
        let response = response(&prepared);
        let system = &response.request.messages[0].content;
        let example = selected_example(system, &action);
        let choice = decode(&response, &example);
        let command = state
            .choose(&prepared, &choice.to_string(), now, utc)
            .expect("完成例を既存操作契約が受け取る");
        assert_eq!(command.is_some(), action != "stay");
    }
}

// 風景を眺める行動の本体プロンプトから完成例を読む。image_targetの詳細objectがそのまま実操作パーサを通る。
#[test]
fn acceptance_body_schema_image_prompt_example_passes_real_receivers() {
    let (mut state, mut prepared, now, utc) = ready();
    prepared.available_actions.push("inspect_image".into());
    prepared.image_capture = Some(("view_12345678-1234-1234-1234-123456789abc".into(), utc));
    let response = response(&prepared);
    let example = selected_example(
        crate::companion_prompt::image_body_contract(),
        "inspect_image",
    );
    assert!(example["body_action"]["image_target"].is_object());
    let choice = decode(&response, &example);
    let command = state
        .choose(&prepared, &choice.to_string(), now, utc)
        .unwrap()
        .unwrap();
    assert!(command["image_target"].is_object());
}

// 合成入力を対照に使う。詳細objectは受理されるが文字列image_targetは拒否され、欠けたプロンプト例とは区別できる。
#[test]
fn acceptance_body_schema_control_object_target_accepts_and_string_target_rejects() {
    for string in [false, true] {
        let (mut state, mut prepared, now, utc) = ready();
        prepared.available_actions.push("inspect_image".into());
        prepared.image_capture = Some(("view_12345678-1234-1234-1234-123456789abc".into(), utc));
        let target = json!({"description":"左の黄色い花","kind":"flower","color":"yellow","horizontal":"left","vertical":"middle"});
        let choice = json!({"action":"inspect_image","target_id":null,"image_target":if string {json!("左の黄色い花")} else {target}});
        let result = state.choose(&prepared, &choice.to_string(), now, utc);
        assert_eq!(result.is_ok(), !string);
    }
}
