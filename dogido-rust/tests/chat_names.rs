use dogido_rust::{
    chat_catalog::{self, Catalog},
    chat_names::{self, Input, Names},
};
use serde_json::{Value, json};
use std::sync::LazyLock;
static FIXTURE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../src/chat_names/fixtures.json")).unwrap()
});
fn check(catalog: &Catalog, cases: &Value) {
    let pool = FIXTURE["pool"].as_array().unwrap();
    for (i, row) in cases.as_array().unwrap().iter().enumerate() {
        let get = |n: usize| &pool[row[n].as_u64().unwrap() as usize];
        let input: Input = serde_json::from_value(get(0).clone()).unwrap();
        let base: Vec<String> = serde_json::from_value(get(1).clone()).unwrap();
        let expected: Names = serde_json::from_value(get(2).clone()).unwrap();
        assert_eq!(
            chat_names::allowed_labels(catalog, &input),
            base,
            "base {i}: {:?}",
            get(0)
        );
        assert_eq!(
            chat_names::project(catalog, &input),
            expected,
            "project {i}: {:?}",
            get(0)
        );
    }
}
#[test]
fn chat_names_all_shipped_names_and_narration_additions_match_python() {
    check(chat_catalog::catalog(), &FIXTURE["cases"]);
}
#[test]
fn chat_names_aliases_unknown_ids_and_ambiguous_corrections_match_python() {
    let docs = &FIXTURE["synthetic"]["documents"];
    let catalog = Catalog::from_documents(
        [&docs["mobs"][0], &docs["mobs"][1], &docs["mobs"][2]],
        &docs["structures"],
    );
    check(&catalog, &FIXTURE["synthetic"]["cases"]);
}
fn input(v: Value) -> Input {
    serde_json::from_value(v).unwrap()
}
#[test]
fn chat_names_history_only_user_names_and_current_labels_keep_their_scope() {
    let catalog = chat_catalog::catalog();
    let one = chat_names::project(
        catalog,
        &input(
            json!({"history":[{"role":"assistant","text":"ゾンビとネコがおる"},{"role":"user","text":"ヤギかもしれない"}],"current_entity_labels":["猫","  独自名  ","","猫"]}),
        ),
    );
    assert_eq!(one.allowed_speech_labels, vec!["猫", "  独自名  ", "ヤギ"]);
    assert!(one.speech_name_corrections.is_empty());
    let reported = chat_names::project(
        catalog,
        &input(json!({"user_text":"村人ゾンビかもしれない"})),
    );
    assert_eq!(reported.allowed_speech_labels, vec!["村人ゾンビ"]);
    assert!(reported.speech_name_corrections.is_empty());
}
#[test]
fn chat_names_topic_does_not_expand_observed_aliases_or_allow_world_claims() {
    let docs = &FIXTURE["synthetic"]["documents"];
    let catalog = Catalog::from_documents(
        [&docs["mobs"][0], &docs["mobs"][1], &docs["mobs"][2]],
        &docs["structures"],
    );
    let topic = chat_names::project(
        &catalog,
        &input(json!({"topics":[{"entry_id":"first","label_ja":"正式名A","kind":"mob"}]})),
    );
    assert_eq!(topic.allowed_speech_labels, vec!["正式名A"]);
    assert!(topic.speech_name_corrections.is_empty());
    let observed = chat_names::project(&catalog, &input(json!({"recent_mob_types":["first"]})));
    assert_eq!(
        observed.allowed_speech_labels,
        vec!["正式名A", "一般種", "略称", "別名"]
    );
    assert_eq!(
        observed
            .speech_name_corrections
            .get("一般種")
            .map(String::as_str),
        Some("正式名A")
    );
    let general = chat_names::observed_name_corrections(&catalog, &["first".into(), "base".into()]);
    assert!(general.is_empty());
    let ambiguous =
        chat_names::observed_name_corrections(&catalog, &["first".into(), "second".into()]);
    assert!(ambiguous.is_empty());
}
#[test]
fn chat_names_projection_rejects_unrecognized_world_evidence_and_wrong_history_types() {
    assert!(serde_json::from_value::<Input>(json!({"observed":true})).is_err());
    assert!(
        serde_json::from_value::<Input>(
            json!({"history":[{"role":"user","text":[],"completed":true}]})
        )
        .is_err()
    );
    assert!(
        serde_json::from_value::<Input>(
            json!({"topics":[{"entry_id":"goat","label_ja":"ヤギ","observed":true}]})
        )
        .is_err()
    );
}
