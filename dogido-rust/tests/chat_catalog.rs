use dogido_rust::chat_catalog::{self, Catalog, Hit};
use serde_json::Value;
use std::sync::LazyLock;
static FIXTURE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../src/chat_catalog/fixtures.json")).unwrap()
});
fn rows_match(catalog: &Catalog, expected: &Value) {
    assert_eq!(
        serde_json::to_value(catalog.all_mob_entries()).unwrap(),
        expected["mobs"]
    );
    assert_eq!(
        catalog.all_mob_entries().keys().collect::<Vec<_>>(),
        expected["mobs"]
            .as_object()
            .unwrap()
            .keys()
            .collect::<Vec<_>>()
    );
    assert_eq!(
        serde_json::to_value(catalog.structure_entries()).unwrap(),
        expected["structures"]
    );
    assert_eq!(
        catalog.structure_entries().keys().collect::<Vec<_>>(),
        expected["structures"]
            .as_object()
            .unwrap()
            .keys()
            .collect::<Vec<_>>()
    );
    assert_eq!(
        serde_json::to_value(catalog.term_rows()).unwrap(),
        expected["term_rows"]
    );
    for row in expected["mob_reads"].as_array().unwrap() {
        assert_eq!(
            serde_json::to_value(catalog.mob_entry(row[0].as_str().unwrap())).unwrap(),
            row[1],
            "read {:?}",
            row[0]
        );
    }
}
fn cases_match(catalog: &Catalog, cases: &Value) {
    let pool = FIXTURE["pool"].as_array().unwrap();
    for (i, row) in cases.as_array().unwrap().iter().enumerate() {
        let get = |n: usize| &pool[row[n].as_u64().unwrap() as usize];
        let query = get(0).as_str().unwrap();
        let observed: Vec<String> = serde_json::from_value(get(1).clone()).unwrap();
        let hits = catalog.find_topics(query, row[2].as_i64().unwrap(), row[3].as_f64().unwrap());
        assert_eq!(
            serde_json::to_value(&hits).unwrap(),
            *get(4),
            "general case {i}: {query:?}"
        );
        assert_eq!(
            chat_catalog::topic_hints(&hits),
            get(5).as_str().unwrap(),
            "general hints {i}"
        );
        let chat = catalog.player_chat_topics(query, &observed);
        assert_eq!(
            serde_json::to_value(&chat).unwrap(),
            *get(6),
            "chat case {i}: {query:?}"
        );
        assert_eq!(
            chat_catalog::topic_hints(&chat),
            get(7).as_str().unwrap(),
            "chat hints {i}"
        );
    }
}
#[test]
fn chat_catalog_all_current_rows_and_terms_match_python_without_material_loss() {
    rows_match(chat_catalog::catalog(), &FIXTURE["real"]);
}
#[test]
fn chat_catalog_all_current_terms_queries_and_hints_match_python() {
    cases_match(chat_catalog::catalog(), &FIXTURE["real"]["cases"]);
    cases_match(chat_catalog::catalog(), &FIXTURE["real_edges"]);
}
#[test]
fn chat_catalog_source_variants_duplicates_ties_and_limits_match_python() {
    let synthetic = &FIXTURE["synthetic"];
    let docs = &synthetic["documents"];
    let c = Catalog::from_documents(
        [&docs["mobs"][0], &docs["mobs"][1], &docs["mobs"][2]],
        &docs["structures"],
    );
    rows_match(&c, synthetic);
    cases_match(&c, &synthetic["cases"]);
}
#[test]
fn chat_catalog_hints_keep_all_rows_only_limit_the_four_terms() {
    for row in FIXTURE["hints"].as_array().unwrap() {
        let hits: Vec<Hit> = serde_json::from_value(row[0].clone()).unwrap();
        assert_eq!(chat_catalog::topic_hints(&hits), row[1].as_str().unwrap());
    }
}
#[test]
fn chat_catalog_observation_cannot_replace_the_players_query_target() {
    let c = chat_catalog::catalog();
    for query in ["前哨基地", "旗のある前哨基地", "ヤギ ゾンビ ネコ"] {
        let base = c.player_chat_topics(query, &[]);
        let observed = c.player_chat_topics(query, &["pillager".into(), "minecraft:goat".into()]);
        for (a, b) in base.iter().zip(&observed) {
            assert_eq!(
                (&a.entry_id, a.score, &a.matched_terms),
                (&b.entry_id, b.score, &b.matched_terms)
            );
        }
        assert_eq!(base.len(), observed.len());
    }
}
#[test]
fn chat_catalog_kana_short_name_and_mob_read_normalization_boundaries() {
    let c = chat_catalog::catalog();
    assert!(
        !c.player_chat_topics("いいんじゃないかな", &[])
            .iter()
            .any(|h| h.entry_id == "squid")
    );
    assert!(
        c.player_chat_topics("イカ", &[])
            .iter()
            .any(|h| h.entry_id == "squid")
    );
    assert_eq!(
        chat_catalog::fold_kana("Ａa ｶﾀｶﾅ ヴヶヿ\u{3000}猫"),
        "Ａa ｶﾀｶﾅ ゔゖヿ\u{3000}猫"
    );
    assert_eq!(c.mob_entry(" MINECRAFT:GoAt "), c.mob_entry("goat"));
    assert!(c.mob_entry("minecraft:minecraft:goat").is_none());
    assert!(c.mob_entry("minecraft: goat").is_none());
    assert!(c.mob_entry("").is_none());
}
