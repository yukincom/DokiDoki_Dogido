use super::*;
#[test]
fn canonical_context_details_and_revision_goldens() {
    let data: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let rows = data["projection"].as_array().unwrap();
    assert_eq!(rows.len(), 216);
    for (i, row) in rows.iter().enumerate() {
        let before = row["input"].clone();
        let output = match row["op"].as_str().unwrap() {
            "details" => details_for(&row["input"]).unwrap(),
            "revision" => revision_input(&row["input"]).unwrap(),
            "context" => {
                workshop_context_details(&serde_json::from_value(row["input"].clone()).unwrap())
            }
            _ => panic!("unknown projection"),
        };
        assert_eq!(output, row["expected"], "case {i} op {}", row["op"]);
        assert_eq!(
            crate::text_format::spaced_json(&output),
            row["expected_json"].as_str().unwrap(),
            "serialized case {i}"
        );
        assert_eq!(row["input"], before);
        if row["op"] == "revision" {
            let _: crate::haiku::revision::Input = serde_json::from_value(output).unwrap();
        }
    }
}
#[test]
fn shared_context_joins_workshop_projection_without_repeating_the_existing_dialogue() {
    let fixture: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let case = fixture["projection"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["op"] == "details")
        .unwrap();
    let mut input = case["input"].clone();
    input["workshop"]["shared_context"] = "出来事: ゾンビを倒した".into();
    let details = details_for(&input).unwrap();
    let text = details["workshop_context"]["recent_dialogue"]
        .as_str()
        .unwrap();
    assert_eq!(text.matches("ゾンビを倒した").count(), 1);
    assert_eq!(text.matches("プレイヤー: 何の対比？").count(), 1);
    assert_eq!(text.matches("ドギド: 葉と斧。").count(), 1);
}

#[test]
fn current_verse_save_confirmation_comes_only_from_the_runtime_flag() {
    let fixture: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let case = fixture["projection"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["op"] == "details")
        .unwrap();
    let mut input = case["input"].clone();
    input["workshop"]["entry_id"] = "hk_saved".into();
    assert!(
        details_for(&input).unwrap()["workshop_context"]
            .get("current_verse_saved")
            .is_none()
    );
    for saved in [false, true] {
        input["workshop"]["current_verse_saved"] = saved.into();
        let before = input.clone();
        let details = details_for(&input).unwrap();
        assert_eq!(details["workshop_context"]["current_verse_saved"], saved);
        assert_eq!(input, before);
    }
}

#[test]
fn pending_allows_explicit_close_only_during_the_decision_phase() {
    let fixture: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let case = fixture["projection"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["op"] == "details" && row["input"]["workshop"]["pending"].is_object())
        .unwrap();
    let mut input = case["input"].clone();
    for phase in ["decide", "after_inspection"] {
        input["phase"] = phase.into();
        input["allowed_actions"] = json!(crate::workshop::allowed_actions(phase, true, false));
        let before = input.clone();
        let details = details_for(&input).unwrap();
        assert_eq!(
            details["allowed_actions"]
                .as_array()
                .unwrap()
                .iter()
                .any(|action| action == "close_workshop"),
            phase == "decide"
        );
        assert_eq!(input, before);
    }
}

#[test]
fn workshop_model_reads_the_latest_twenty_pairs() {
    let pairs: Vec<_> = (0..25).map(|i| json!({
        "turn_id": format!("turn-{i}"), "player_text": format!("入力{i:02}"),
        "dogido_text": format!("返答{i:02}"), "reply_delivery": "spoken"
    })).collect();
    let text = dialogue_from_view(&pairs).unwrap();
    assert_eq!(text.lines().count(), 40);
    assert!(!text.contains("入力04"));
    assert!(!text.contains("返答04"));
    assert!(text.starts_with("プレイヤー: 入力05\nドギド: 返答05"));
    assert!(text.ends_with("ドギド: 返答24"));
}
