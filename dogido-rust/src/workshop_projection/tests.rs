use super::*;
#[test]
fn canonical_context_details_and_revision_goldens() {
    let data: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let rows = data["projection"].as_array().unwrap();
    assert!(rows.len() > 400);
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
fn combat_resume_preserves_whole_utterance_conditions() {
    let data: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let rows = data["combat_resume"].as_array().unwrap();
    assert_eq!(rows.len(), 714);
    for row in rows {
        assert_eq!(
            fixed_combat_resume(row["text"].as_str().unwrap()),
            row["expected"].as_bool().unwrap(),
            "{row}"
        );
    }
}
