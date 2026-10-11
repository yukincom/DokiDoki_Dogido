use dogido_rust::{
    chat_catalog, chat_world, conversation_observation,
    environment::projection::{MiningState, project_environment},
    events::GameEvent,
    haiku::{
        context::Context,
        materials::{self, ReadingSnapshot, RuntimeRead},
    },
    world_catalog,
};
use serde_json::json;

fn frame(cover: &str, y: f64, depth: Option<i64>) -> GameEvent {
    GameEvent::parse(json!({
        "schema_version":"2026-05-24", "adapter":"fixture",
        "observed_at":"2026-10-11T02:27:43Z",
        "event":{"name":"status_snapshot","source_kind":"system",
            "priority_hint":"background","certainty":"high"},
        "player":{"position":{"y":y},"held_item":"egg"},
        "world":{"biome":"plains","sky_visible":false,"surface_y":119,
            "depth_below_surface":depth,"overhead_cover_type":cover,
            "ceiling_observed":true,"ceiling_height":2,
            "nearby_door_count":0,"nearby_bed_count":0,"safe_zone_with_door":false}
    }))
    .unwrap()
}

fn materials(event: &GameEvent) -> Context {
    let world = world_catalog::catalog();
    materials::capture(
        event,
        RuntimeRead {
            current_structure: event.world.structure.as_deref(),
            player_name: "プレイヤー",
            inventory_order: &[],
        },
        world,
        chat_catalog::catalog(),
        world,
        &ReadingSnapshot::default(),
    )
    .unwrap()
}

#[test]
fn terrain_cover_is_an_observation_in_conversation_and_poem_sources() {
    for (cover, label) in [("earth", "土"), ("stone", "石"), ("ore", "鉱石")] {
        for (y, depth) in [(112.0, Some(7)), (24.0, Some(12)), (24.0, None)] {
            let event = frame(cover, y, depth);
            let before = serde_json::to_value(&event).unwrap();
            let projection = project_environment(&event);
            assert_eq!(projection.mining_state, MiningState::None);
            assert!(projection.mining_label.is_empty());

            let shared = conversation_observation::project(&event, None, 32., 7., 5000).unwrap();
            assert_eq!(shared.observations["place"], "空が見えない空間");
            assert_eq!(shared.observations["overhead_cover"], cover);
            assert_eq!(shared.observations["sky_visible"], false);

            let context = materials(&event);
            let feature = context
                .feature_candidates
                .iter()
                .find(|f| f.key == "overhead_cover")
                .unwrap();
            assert_eq!(feature.label, format!("空は見えず、頭上に{label}がある"));
            assert_eq!(feature.tags, vec![label]);
            assert!(
                !context
                    .feature_candidates
                    .iter()
                    .any(|f| f.key == "mining_context")
            );
            let atom = context
                .source_atoms
                .iter()
                .find(|a| a.text == feature.label)
                .unwrap();
            assert_eq!(atom.kind, "observation");
            assert_eq!(atom.claim_class, "factual");
            assert_eq!(atom.claim_scopes, ["observed_state"]);
            assert_eq!(serde_json::to_value(&event).unwrap(), before);
        }
    }
}

#[test]
fn missing_ceiling_and_unknown_material_do_not_create_terrain_facts() {
    let mut raw = serde_json::to_value(frame("earth", 112., Some(7))).unwrap();
    raw["world"]["ceiling_observed"] = json!(false);
    let not_detected = GameEvent::parse(raw).unwrap();
    for event in [
        not_detected,
        frame("unknown", 112., Some(7)),
        frame("none", 112., Some(7)),
    ] {
        assert!(
            !materials(&event)
                .feature_candidates
                .iter()
                .any(|f| f.key == "overhead_cover")
        );
        assert_eq!(project_environment(&event).mining_state, MiningState::None);
    }
}

#[test]
fn mining_requires_actual_breaks_and_does_not_assert_an_underground_location() {
    let mut raw = serde_json::to_value(frame("earth", 112., Some(7))).unwrap();
    raw["player"]["held_item"] = json!("iron_pickaxe");
    let event = GameEvent::parse(raw.clone()).unwrap();
    assert_eq!(project_environment(&event).mining_state, MiningState::None);
    raw["recent_block_breaks"] = json!([
        {"name":"stone","material":"stone","age_ms":100},
        {"name":"dirt","material":"earth","age_ms":800}
    ]);
    let event = GameEvent::parse(raw).unwrap();
    let projection = project_environment(&event);
    assert_eq!(projection.mining_state, MiningState::Active);
    assert_eq!(projection.mining_label, "プレイヤーは採掘している");
    let context = materials(&event);
    let feature = context
        .feature_candidates
        .iter()
        .find(|f| f.key == "mining_context")
        .unwrap();
    assert_eq!(feature.source, "行動");
    assert_eq!(feature.tags, ["採掘"]);
    let shared = conversation_observation::project(&event, None, 32., 7., 5000).unwrap();
    assert_eq!(shared.observations["place"], "採掘中");
}

#[test]
fn observed_mineshaft_structure_keeps_its_own_identity() {
    let mut raw = serde_json::to_value(frame("stone", 112., Some(7))).unwrap();
    raw["world"]["structure"] = json!("mineshaft");
    let event = GameEvent::parse(raw).unwrap();
    assert_eq!(project_environment(&event).mining_state, MiningState::None);
    let place = chat_world::place_context(
        world_catalog::catalog(),
        chat_catalog::catalog(),
        &event,
        Some("mineshaft"),
        32.,
    )
    .unwrap();
    assert_eq!(place.space_kind, "sky_hidden");
    assert!(place.place_line.contains("構造物: 廃坑"));
    assert!(
        materials(&event)
            .feature_candidates
            .iter()
            .any(|f| f.key == "structure" && f.label == "廃坑")
    );
}
