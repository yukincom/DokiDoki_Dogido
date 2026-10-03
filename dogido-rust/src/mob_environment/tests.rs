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
