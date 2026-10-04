use dogido_rust::{
    chat_catalog::{self, Catalog, Hit},
    chat_hints::{self, ChatTactics, Plausibility, Tactics},
    events::VisualThreat,
};
use serde_json::{Value, json};
use std::sync::LazyLock;
static FIXTURE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../src/chat_hints/fixtures.json")).unwrap()
});
fn check(catalog: &Catalog, plau: &Value, tactics: &Value) {
    let pool = FIXTURE["pool"].as_array().unwrap();
    for (i, row) in plau.as_array().unwrap().iter().enumerate() {
        let get = |n: usize| &pool[row[n].as_u64().unwrap() as usize];
        let topics: Vec<Hit> = serde_json::from_value(get(0).clone()).unwrap();
        let ids: Vec<String> = serde_json::from_value(get(1).clone()).unwrap();
        let biome = get(2).as_str();
        let label = get(3).as_str();
        let stance = get(4).as_str().unwrap();
        assert_eq!(
            serde_json::to_value(chat_hints::structure_ids_for_plausibility(catalog, &topics))
                .unwrap(),
            *get(5),
            "ids {i}"
        );
        assert_eq!(
            serde_json::to_value(chat_hints::build_plausibility_hint_lines(
                catalog,
                &ids,
                Some(&topics),
                biome,
                label
            ))
            .unwrap(),
            *get(6),
            "lines {i}"
        );
        let expected: Plausibility = serde_json::from_value(get(7).clone()).unwrap();
        assert_eq!(
            chat_hints::player_chat_plausibility(catalog, stance, &topics, biome, label),
            expected,
            "projection {i}"
        );
    }
    for (i, row) in tactics.as_array().unwrap().iter().enumerate() {
        let get = |n: usize| &pool[row[n].as_u64().unwrap() as usize];
        let ids: Vec<String> = serde_json::from_value(get(0).clone()).unwrap();
        let visual: Vec<VisualThreat> = serde_json::from_value(get(1).clone()).unwrap();
        let expected: Tactics = serde_json::from_value(get(2).clone()).unwrap();
        assert_eq!(
            chat_hints::collect_tactics(catalog, &ids).unwrap(),
            expected,
            "tactics {i}"
        );
        let expected: ChatTactics = serde_json::from_value(get(3).clone()).unwrap();
        assert_eq!(
            chat_hints::player_chat_tactics(catalog, &visual, &ids).unwrap(),
            expected,
            "chat tactics {i}"
        );
    }
}
#[test]
fn chat_hints_current_catalog_and_actual_narration_match_fixture() {
    check(
        chat_catalog::catalog(),
        &FIXTURE["plausibility"],
        &FIXTURE["tactics"],
    );
}
#[test]
fn chat_hints_synthetic_related_mobs_order_notes_and_normalization_match_fixture() {
    let d = &FIXTURE["synthetic"]["documents"];
    let catalog = Catalog::from_documents(
        [&d["mobs"][0], &d["mobs"][1], &d["mobs"][2]],
        &d["structures"],
    );
    check(
        &catalog,
        &FIXTURE["synthetic"]["plausibility"],
        &FIXTURE["synthetic"]["tactics"],
    );
}
#[test]
fn chat_hints_recent_visual_can_supply_rules_but_cannot_create_current_safe_fallback() {
    let result =
        chat_hints::player_chat_tactics(chat_catalog::catalog(), &[], &["creeper".into()]).unwrap();
    assert!(!result.safe_hints.is_empty() || !result.forbidden_advice.is_empty());
    assert!(result.safe_fallback.is_none());
    let hits = chat_catalog::catalog().player_chat_topics("前哨基地", &["pillager".into()]);
    for stance in ["none", "saw", "clarify"] {
        assert_eq!(
            chat_hints::player_chat_plausibility(
                chat_catalog::catalog(),
                stance,
                &hits,
                Some("plains"),
                None
            ),
            Plausibility::default()
        );
    }
}
#[test]
fn chat_hints_invalid_non_iterable_tactics_remain_errors_instead_of_lost_advice() {
    let mob = json!({"items":{"bad":{"dogido_tactics":{"forbidden_advice":123}}}});
    let empty = json!({});
    let catalog = Catalog::from_documents([&mob, &empty, &empty], &empty);
    assert!(chat_hints::collect_tactics(&catalog, &["bad".into()]).is_err());
}
