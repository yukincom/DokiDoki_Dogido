use dogido_rust::{
    chat_catalog, chat_world,
    environment::precipitation::Climate,
    events::{GameEvent, LookTarget},
    world_catalog::{self, WorldCatalog},
};
use serde_json::{Value, json};
use std::sync::LazyLock;
static FIXTURE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../src/world_catalog/fixtures.json")).unwrap()
});
fn value(row: &Value, index: usize) -> &Value {
    &FIXTURE["pool"][row[index].as_u64().unwrap() as usize]
}
fn climate(c: Climate) -> Value {
    json!({"biome_temperature":c.biome_temperature,"snow_start_y":c.snow_start_y,"biome_group_id":c.biome_group_id,"biome_downfall":c.biome_downfall})
}
#[test]
fn chat_world_catalogue_all_labels_and_raw_biome_metadata_match_canonical() {
    let c = world_catalog::catalog();
    assert_eq!(c.item_labels(), FIXTURE["items"].as_object().unwrap());
    assert!(
        c.item_labels()
            .keys()
            .eq(FIXTURE["items"].as_object().unwrap().keys())
    );
    assert!(
        c.block_labels()
            .keys()
            .eq(FIXTURE["blocks"].as_object().unwrap().keys())
    );
    assert!(
        c.biome_entries()
            .keys()
            .eq(FIXTURE["biomes"].as_object().unwrap().keys())
    );
    assert_eq!(c.block_labels(), FIXTURE["blocks"].as_object().unwrap());
    assert_eq!(c.biome_entries(), FIXTURE["biomes"].as_object().unwrap());
    for (i, row) in FIXTURE["labels"].as_array().unwrap().iter().enumerate() {
        let id = value(row, 0).as_str();
        let actual = json!({"item":c.item_label(id),"block":c.block_label(id),"biome":c.biome_label(id),"structure":world_catalog::structure_label(chat_catalog::catalog(),id)});
        assert_eq!(actual, *value(row, 1), "labels {i}: {id:?}");
    }
    for row in FIXTURE["materials"].as_array().unwrap() {
        assert_eq!(
            world_catalog::material_label(value(row, 0).as_str().unwrap()),
            value(row, 1).as_str().unwrap()
        );
    }
    for (i, row) in FIXTURE["climates"].as_array().unwrap().iter().enumerate() {
        let id = value(row, 0).as_str();
        assert_eq!(
            climate(c.climate(id).unwrap()),
            *value(row, 1),
            "climate {i}"
        );
        assert_eq!(
            c.biome_entry(id).unwrap_or(&Value::Null),
            value(row, 2),
            "entry {i}"
        );
    }
    for row in FIXTURE["metrics"].as_array().unwrap() {
        assert_eq!(
            serde_json::to_value(world_catalog::metric(value(row, 0))).unwrap(),
            *value(row, 1)
        );
    }
}
#[test]
fn chat_world_synthetic_recursive_pointers_cycles_variants_and_precedence_match_canonical() {
    let s = &FIXTURE["synthetic"];
    let c =
        WorldCatalog::from_documents(s["documents"].as_object().unwrap(), &s["biomes_document"]);
    assert_eq!(c.item_labels(), s["items"].as_object().unwrap());
    assert_eq!(c.block_labels(), s["blocks"].as_object().unwrap());
    assert_eq!(c.biome_entries(), s["biomes"].as_object().unwrap());
    for row in s["climates"].as_array().unwrap() {
        assert_eq!(climate(c.climate(row[0].as_str()).unwrap()), row[1]);
    }
    // Legacy flat biome source and invalid snow values retain their original boundaries.
    let c = WorldCatalog::from_documents(&Default::default(), &json!({"x":"場所","number":3}));
    assert_eq!(c.biome_label(Some("x")), "場所");
    assert_eq!(c.biome_label(Some("number")), "3");
    let c = WorldCatalog::from_documents(
        &Default::default(),
        &json!({"groups":{"x":{"biomes":{"bad":{"snow_starts_at_y":"not an integer"}}}}}),
    );
    assert!(c.climate(Some("bad")).is_err());
}
#[test]
fn chat_world_current_inventory_and_look_material_match_actual_narration() {
    let c = world_catalog::catalog();
    for (i, row) in FIXTURE["inventories"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        let inv = serde_json::from_value(value(row, 0).clone()).unwrap();
        let max = row[1].as_i64().unwrap() as isize;
        assert_eq!(
            chat_world::inventory_summary(c, &inv, max),
            value(row, 2).as_str().unwrap(),
            "inventory {i}"
        );
    }
    for (i, row) in FIXTURE["looks"].as_array().unwrap().iter().enumerate() {
        let look: Option<LookTarget> = serde_json::from_value(value(row, 0).clone()).unwrap();
        assert_eq!(
            c.look_target_label(chat_catalog::catalog(), look.as_ref()),
            value(row, 1).as_str().unwrap(),
            "look {i}"
        );
    }
    for row in FIXTURE["look_queries"].as_array().unwrap() {
        assert_eq!(
            chat_world::wants_look_answer(value(row, 0).as_str().unwrap()),
            value(row, 1).as_bool().unwrap()
        );
    }
}
#[test]
fn chat_world_place_and_weather_match_canonical_complete_event_projection() {
    let c = world_catalog::catalog();
    for (i, row) in FIXTURE["places"].as_array().unwrap().iter().enumerate() {
        let event: GameEvent = serde_json::from_value(value(row, 0).clone()).unwrap();
        let before = serde_json::to_value(&event).unwrap();
        let out = chat_world::place_context(
            c,
            chat_catalog::catalog(),
            &event,
            value(row, 1).as_str(),
            value(row, 2).as_f64().unwrap(),
        )
        .unwrap();
        let mut expected = value(row, 3).clone();
        // Intentional correction to the frozen Python baseline: a measured
        // surface canopy retains its biome even below low leaves.
        if event.world.overhead_cover_type.as_deref() == Some("foliage")
            && event.world.depth_below_surface == Some(0)
            && matches!(
                expected["space_kind"].as_str(),
                Some("canopy" | "underground_or_roofed")
            )
        {
            let biome = c.biome_label(event.world.biome.as_deref());
            expected["space_kind"] = json!("canopy");
            expected["biome_label"] = json!(biome);
            let line = expected["place_line"]
                .as_str()
                .unwrap()
                .replace("木陰っぽい空間", "地表の木陰（頭上は木の葉）")
                .replace(
                    "地下っぽい／屋根のある空間（空は見えない）",
                    "地表の木陰（頭上は木の葉）",
                );
            expected["place_line"] = json!(format!("地表バイオーム: {biome} / {line}"));
        }
        assert_eq!(
            serde_json::to_value(out).unwrap(),
            expected,
            "place {i} {:?}",
            value(row, 0)
        );
        assert_eq!(serde_json::to_value(event).unwrap(), before);
    }
    for (i, row) in FIXTURE["weather"].as_array().unwrap().iter().enumerate() {
        let event: GameEvent = serde_json::from_value(value(row, 0).clone()).unwrap();
        let cl = c.climate(event.world.biome.as_deref()).unwrap();
        assert_eq!(
            chat_world::weather_label(&event, &cl).unwrap(),
            value(row, 1).as_str().unwrap(),
            "weather {i}"
        );
        assert_eq!(
            chat_world::weather_fact(&event),
            value(row, 2).as_str().unwrap(),
            "weather fact {i}"
        );
    }
}

#[test]
fn measured_surface_leaves_keep_taiga_instead_of_inventing_underground() {
    let base = value(&FIXTURE["places"][0], 0).clone();
    for (cover, depth, expected) in [
        ("foliage", 0, "canopy"),
        ("solid", 0, "underground_or_roofed"),
        ("stone", 12, "mine_like"),
    ] {
        let mut raw = base.clone();
        raw["player"]["position"]["y"] = json!(111.0);
        raw["player"]["held_item"] = json!("pink_petals");
        raw["world"] = json!({"biome":"taiga","sky_visible":false,"overhead_cover_type":cover,
            "depth_below_surface":depth,"ceiling_height":2.0,"enclosure_score":0.4,
            "local_light":13,"danger_darkness_score":0.3});
        raw["recent_block_breaks"] = json!([]);
        raw["dropped_items"] = json!([]);
        let e = GameEvent::parse(raw).unwrap();
        let p = chat_world::place_context(
            world_catalog::catalog(),
            chat_catalog::catalog(),
            &e,
            None,
            10.,
        )
        .unwrap();
        assert_eq!(p.space_kind, expected);
        if cover == "foliage" {
            assert_eq!(p.biome_label, "タイガ");
            assert!(p.place_line.contains("タイガ"));
            assert!(p.place_line.contains("木陰"));
            assert!(!p.place_line.contains("地下"));
            assert_eq!(p.sky_visible, Some(false));
        }
    }
}
