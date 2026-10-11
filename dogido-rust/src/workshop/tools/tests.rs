use super::*;

const RAW: &str = "くろいをしろいに変えて";

fn view() -> Value {
    let lines: Vec<_> = ["さくらのは", "くろいおのへと", "あさのいろ"]
        .iter().enumerate().map(|(i, text)| json!({
            "line_id":format!("line_{}",i+1),"line_index":i,
            "position":(["upper","middle","lower"][i]),"canonical_name":(["上五","中七","下五"][i]),
            "surface_text":text,"reading_text":text,"source_atom_ids":[],"source_atoms":[],"provenance":"generated"
        })).collect();
    json!({"current_lines":lines,"emission":{"lines":lines},"materials":{},"followup":"discussion"})
}

fn edit_message() -> Value {
    json!({"role":"assistant","content":null,"tool_calls":[{
        "id":"edit-1","type":"function","function":{"name":"stage_player_edit",
        "arguments":json!({"line_id":"line_2",
            "expected_text":"line_1=さくらのは, line_2=くろいおのへと, line_3=あさのいろ。",
            "target_fragment":"くろい","replacement_text":"しろい","evidence":RAW,"confidence":0.95}).to_string()}
    }]})
}

#[test]
fn native_edit_keeps_verified_target_and_returns_failure_to_same_call() {
    let current = view();
    let message = edit_message();
    let call = Call::decode(message.clone(), &current, RAW).unwrap();
    let payload = call.payload();
    let frame = json!({"op":"validate","workshop":current,"text":RAW,"phase":"decide",
        "allowed_actions":["stage_player_edit"],"payload":payload});
    let details = crate::workshop_projection::details_for(&frame).unwrap();
    let checked = crate::workshop_validation::validate(&frame, &details).unwrap();
    let mut step = checked["step"].clone();
    assert_eq!(step["action"], "stage_player_edit");
    call.retain_verified_target(&mut step);
    assert_eq!(step["analysis"]["line_proposal"]["line_index"], 1);
    assert_eq!(
        step["analysis"]["line_proposal"]["replacement_text"],
        "しろい"
    );
    let observation = json!({"kind":"player_edit_validation","status":"rejected",
        "validation_codes":["meter_not_exact"],"canonical_unchanged":true});
    let messages = call.result_messages(&observation);
    assert_eq!(messages[0], message);
    assert_eq!(messages[1]["tool_call_id"], "edit-1");
    assert_eq!(
        serde_json::from_str::<Value>(messages[1]["content"].as_str().unwrap()).unwrap(),
        observation
    );
    assert_eq!(current, view());
}

#[test]
fn batch_unknown_function_and_stale_raw_evidence_never_become_edits() {
    let current = view();
    let original = edit_message();
    let mut batch = original.clone();
    batch["tool_calls"]
        .as_array_mut()
        .unwrap()
        .push(original["tool_calls"][0].clone());
    assert!(Call::decode(batch, &current, RAW).is_err());
    let mut unknown = original.clone();
    unknown["tool_calls"][0]["function"]["name"] = "save_verse".into();
    assert!(Call::decode(unknown, &current, RAW).is_err());
    assert!(Call::decode(original, &current, "前の案はやめといて").is_err());
}

#[test]
fn inspection_and_edit_cannot_enter_through_content_json() {
    let current = view();
    let call = Call::decode(edit_message(), &current, RAW).unwrap();
    assert!(validate_transport(&call.payload(), Some(&call)).is_ok());
    assert_eq!(
        validate_transport(&call.payload(), None)
            .unwrap_err()
            .to_string(),
        "native_tool_required"
    );
    assert_eq!(
        validate_transport(&json!({"action":"inspect","checks":["meter"]}), None)
            .unwrap_err()
            .to_string(),
        "native_tool_required"
    );
    for action in [
        "respond",
        "explain",
        "ask",
        "accept_pending",
        "close_workshop",
    ] {
        assert!(validate_transport(&json!({"action":action}), None).is_ok());
    }
}

#[test]
fn native_inspection_preserves_candidate_text_and_target_line() {
    let message = json!({"role":"assistant","content":null,"tool_calls":[{
        "id":"inspect-word","type":"function","function":{"name":"inspect_verse",
        "arguments":json!({"checks":["reading","meter"],"inspect_text":"白い骨","inspect_line_index":0}).to_string()}
    }]});
    let call = Call::decode(message, &view(), "白い骨の読みは？").unwrap();
    assert_eq!(
        call.payload(),
        json!({"action":"inspect","checks":["reading","meter"],
        "inspect_text":"白い骨","inspect_line_index":0})
    );
    assert_eq!(
        call.trace(),
        json!({"name":"inspect_verse","id":"inspect-word"})
    );
}
