use super::*;
use crate::conversation_observation::{self, State};

fn frame(extra: Value) -> GameEvent {
    let mut value = json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":1,
        "observed_at":"2026-10-03T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld"},"world":{"sky_visible":true,"time_phase":"day","weather":"rain","biome":"plains"}});
    value
        .as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    GameEvent::parse(value).unwrap()
}

fn fish(subject: &str, water: bool) -> Value {
    json!({"type":"salmon","identity":{"entity_id":subject},"environment":{
        "touching_water":water,"submerged_in_water":water,"on_ground":!water,
        "touching_water_or_rain":water,"on_fire":false}})
}

fn context(event: &GameEvent) -> conversation_observation::Context {
    conversation_observation::project(event, None, 24.0, 7.0, 10000).unwrap()
}

#[test]
fn individuals_of_the_same_species_keep_different_medium_and_rain_measurements() {
    let event = frame(
        json!({"passive_mobs":[fish("water-fish",true),fish("land-fish",false),
        {"type":"axolotl","identity":{"entity_id":"rain-exposed"},"environment":{
            "touching_water":false,"on_ground":true,"touching_water_or_rain":true}},
        {"type":"axolotl","identity":{"entity_id":"under-roof"},"environment":{
            "touching_water":false,"on_ground":true,"touching_water_or_rain":false}}]}),
    );
    let rows = project(&event);
    assert_eq!(rows.as_array().unwrap().len(), 4);
    assert_eq!(rows[0]["medium"], "水に触れている");
    assert_eq!(rows[1]["medium"], "水に触れず地面にいる");
    assert_eq!(rows[2]["environment"]["touching_water_or_rain"], true);
    assert_eq!(rows[3]["environment"]["touching_water_or_rain"], false);
    assert!(rows[0].get("suffocating").is_none());
    assert!(rows[1].get("dead").is_none());
}

#[test]
fn missing_legacy_and_heard_only_states_never_invent_land_or_dryness() {
    let event = frame(
        json!({"visual_threats":[{"type":"zombie"},{"type":"skeleton","in_water":true},
            {"type":"zombie","in_water":true,"on_fire":true,"environment":{}}],
        "passive_mobs":[{"type":"salmon"},{"type":"axolotl","environment":{}}],
        "ambient_sounds":[{"type":"dolphin"}],
        "look_target":{"kind":"block","name":"salmon","environment":{"touching_water":false}}}),
    );
    let rows = project(&event);
    assert_eq!(rows.as_array().unwrap().len(), 1);
    assert_eq!(rows[0]["catalog_id"], "skeleton");
    assert_eq!(rows[0]["environment"], json!({"touching_water":true}));
    let event =
        frame(json!({"passive_mobs":[{"type":"salmon","environment":{"touching_water":false}}]}));
    assert_eq!(
        project(&event)[0]["medium"],
        "水の外にいる（地面にいるかは別の観測）"
    );
}

#[test]
fn crosshair_updates_only_the_matching_individual_and_unknown_ids_are_not_relabelled() {
    let event = frame(json!({"passive_mobs":[fish("one",true),fish("two",true)],
        "look_target":{"kind":"entity","name":"salmon","identity":{"entity_id":"one"},
            "environment":{"touching_water":false,"on_ground":true}}}));
    let rows = project(&event);
    assert_eq!(rows.as_array().unwrap().len(), 2);
    assert_eq!(rows[0]["environment"]["touching_water"], false);
    assert_eq!(rows[1]["environment"]["touching_water"], true);
    let foreign = frame(
        json!({"passive_mobs":[{"type":"mod:salmon","environment":{"touching_water":false}}]}),
    );
    assert_eq!(project(&foreign), json!([]));
}

#[test]
fn only_observed_environment_transitions_remain_in_current_dialogue_context() {
    let water = context(&frame(json!({"passive_mobs":[fish("one",true)]})));
    let land = context(&frame(json!({"passive_mobs":[fish("one",false)]})));
    let mut state = State::default();
    state.observe(water);
    state.observe(land.clone());
    let result = state.context().unwrap();
    let change = result
        .changes
        .iter()
        .find(|c| c.kind == "mob_environment")
        .unwrap();
    assert_eq!(change.before["environment"]["touching_water"], true);
    assert_eq!(change.now["environment"]["touching_water"], false);
    state.observe(land);
    assert_eq!(
        state
            .context()
            .unwrap()
            .changes
            .iter()
            .filter(|c| c.kind == "mob_environment")
            .count(),
        1
    );
    state.observe(context(&frame(json!({}))));
    let result = state.context().unwrap();
    assert_eq!(result.observations["mob_states"], json!([]));
    assert!(!result.changes.iter().any(|c| c.kind == "mob_environment"));
}

#[test]
fn authored_environment_conditions_and_individual_observations_reach_both_model_inputs() {
    let event = frame(json!({"passive_mobs":[fish("one",false)],
        "visual_threats":[{"type":"zombie","entity_id":"z","in_water":true,"environment":{
            "touching_water":true,"submerged_in_water":false,"on_fire":false}}]}));
    let context = context(&event).for_reaction("daylight_water", &json!({"hostiles":["ゾンビ"]}));
    let entry = context
        .catalog_knowledge
        .as_ref()
        .unwrap()
        .entries
        .iter()
        .find(|r| r.id == "salmon")
        .unwrap();
    assert!(
        entry.general["environment_notes"][0]
            .as_str()
            .unwrap()
            .contains("雨だけでは防げない")
    );
    let leaf = crate::reaction_leaf::Leaf::prepare(
        &json!({"kind":"daylight_water","model":"test","max_tokens":100,
        "temperature":0.6,"fallback_text":"水の中におるな。","details":{
            "hostiles":["ゾンビ"],"count":1,"conversation_context":{"world_context":context}}}),
        "test",
        100,
    )
    .unwrap();
    let prompt: Value = serde_json::from_str(&leaf.request.messages[1].content).unwrap();
    assert_eq!(prompt["event"], "daylight_water");
    assert_eq!(
        prompt["world_context"]["observations"]["mob_states"][0]["environment"]["submerged_in_water"],
        false
    );
    assert_eq!(
        prompt["world_context"]["observations"]["mob_states"][1]["medium"],
        "水に触れず地面にいる"
    );
    assert!(prompt["world_context"].get("catalog_knowledge").is_none());
    let entries = prompt["catalog_knowledge"]["entries"].as_array().unwrap();
    assert_eq!(
        entries.iter().find(|row| row["id"] == "salmon").unwrap()["general"],
        entry.general
    );
    let chat = crate::chat_prompt::messages(
        &json!({"user_text":"この魚、大丈夫かな。","world_context":context,"dialogue_choice":true}),
    )
    .unwrap();
    assert!(chat[1].content.contains("水に触れず地面にいる"));
    assert!(chat[1].content.contains("雨だけでは防げない"));
    assert!(
        chat[1]
            .content
            .contains("現在水中にいることだけでは変身開始や完了は分からない")
    );
}

#[test]
fn daylight_and_land_survival_flags_use_the_corrected_species_catalogue() {
    for species in [
        "drowned",
        "stray",
        "bogged",
        "zombie_horse",
        "zombie_nautilus",
        "minecraft:zombie",
    ] {
        assert!(burns_in_daylight(species), "{species}");
    }
    for species in [
        "husk",
        "zombified_piglin",
        "wither_skeleton",
        "skeleton_horse",
        "mod:zombie",
    ] {
        assert!(!burns_in_daylight(species), "{species}");
    }
    let entries = crate::chat_catalog::catalog().all_mob_entries();
    assert_eq!(entries["nautilus"]["dies_on_land"], true);
    assert_eq!(entries["zombie_nautilus"]["dies_on_land"], false);
}

#[test]
fn tamed_companion_outside_routine_slots_keeps_identity_without_environment_events() {
    let event = frame(json!({"passive_mobs":[
        {"type":"cow"},{"type":"cow"},{"type":"chicken"},{"type":"chicken"}],
        "companion_candidates":[{"type":"wolf","identity":{"entity_id":"pet","tamed":true},
            "environment":{"touching_water":false,"on_ground":true}}]}));
    let rows = project(&event);
    assert_eq!(rows.as_array().unwrap().len(), 1);
    assert_eq!(rows[0]["catalog_id"], "wolf");
    assert_eq!(rows[0]["subject"], "pet");
    assert_eq!(rows[0]["tamed"], true);
    assert_eq!(rows[0]["environment"], json!({}));
    assert!(rows[0].get("owner").is_none());
    let wet = frame(json!({"companion_candidates":[{"type":"wolf",
        "identity":{"entity_id":"pet","tamed":true},
        "environment":{"touching_water":true,"on_ground":false}}]}));
    assert!(changes(&rows, &project(&wet)).is_empty());
    let before = context(&frame(json!({})));
    let after = context(&event);
    assert!(
        crate::environment::notice::changes(&crate::environment::notice::snapshot(&before), &after)
            .is_empty()
    );
}

#[test]
fn named_or_legacy_wolves_are_not_inferred_to_be_tamed_or_wild() {
    let event = frame(json!({"passive_mobs":[
        {"type":"wolf","identity":{"entity_id":"known-pet","tamed":true}},
        {"type":"wolf","identity":{"entity_id":"named","custom_name":"クロちゃん"}},
        {"type":"wolf","identity":{"entity_id":"legacy"},"environment":{"on_ground":true}},
        {"type":"wolf","identity":{"entity_id":"untamed","tamed":false},"environment":{"on_ground":true}}],
        "ambient_sounds":[{"type":"wolf","source_id":"heard","identity":{"entity_id":"heard","tamed":true}}]}));
    let rows = project(&event);
    assert_eq!(rows.as_array().unwrap().len(), 4);
    assert_eq!(rows[0]["tamed"], true);
    assert_eq!(rows[1]["custom_name"], "クロちゃん");
    for row in rows.as_array().unwrap().iter().skip(1) {
        assert!(row.get("tamed").is_none());
    }
    assert!(
        !rows
            .as_array()
            .unwrap()
            .iter()
            .any(|row| row["subject"] == "heard")
    );
}

#[test]
fn crosshair_keeps_taming_separate_for_two_wolves_and_refreshes_name() {
    let event = frame(json!({"passive_mobs":[
        {"type":"wolf","identity":{"entity_id":"pet","tamed":true,"custom_name":"前の名前"},"environment":{"touching_water":true}},
        {"type":"wolf","identity":{"entity_id":"other"},"environment":{"on_ground":true}}],
        "companion_candidates":[{"type":"wolf","identity":{"entity_id":"pet","tamed":true,"custom_name":"前の名前"}}],
        "look_target":{"kind":"entity","name":"wolf","identity":{"entity_id":"pet","tamed":true,"custom_name":"今の名前"},
            "environment":{"touching_water":false,"on_ground":true}}}));
    let rows = project(&event);
    assert_eq!(rows.as_array().unwrap().len(), 2);
    assert_eq!(rows[0]["subject"], "pet");
    assert_eq!(rows[0]["tamed"], true);
    assert_eq!(rows[0]["custom_name"], "今の名前");
    assert_eq!(rows[0]["environment"]["touching_water"], false);
    assert_eq!(rows[1]["subject"], "other");
    assert!(rows[1].get("tamed").is_none());
    assert!(rows[1].get("custom_name").is_none());
}

#[test]
fn companion_metadata_reaches_chat_and_surroundings_model_inputs_and_expires() {
    let event = frame(json!({"companion_candidates":[{"type":"wolf",
        "identity":{"entity_id":"pet","tamed":true,"custom_name":"クロちゃん"}}]}));
    let world = context(&event);
    let chat = crate::chat_prompt::messages(
        &json!({"user_text":"そばのオオカミは？","world_context":world,"dialogue_choice":true}),
    )
    .unwrap();
    assert!(chat[1].content.contains("クロちゃん"));
    assert!(chat[1].content.contains("\"tamed\": true"));
    let prepared = crate::environment::reaction::Prepared::new(
        &json!({"kind":"environment_reaction","model":"fixture","max_tokens":100,
            "fallback_text":"", "details":{"reaction_context":{
                "topic":"surroundings","world_context":world}}}),
        "fixture",
    )
    .unwrap();
    let prompt: Value = serde_json::from_str(&prepared.request.messages[1].content).unwrap();
    let pet = &prompt["world_context"]["observations"]["mob_states"][0];
    assert_eq!(pet["tamed"], true);
    assert_eq!(pet["custom_name"], "クロちゃん");
    assert_eq!(pet["subject"], "pet");
    let mut state = State::default();
    state.observe(world);
    state.observe(context(&frame(json!({}))));
    assert_eq!(
        state.context().unwrap().observations["mob_states"],
        json!([])
    );
}
