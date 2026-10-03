// Generated source inventory only; do not replace with derived label tables.
fn bundled_documents() -> serde_json::Map<String, serde_json::Value> {
    let mut out = serde_json::Map::new();
    out.insert("block/minecraft_colored_blocks.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/block/minecraft_colored_blocks.json")).expect("entry source JSON"));
    out.insert("block/minecraft_functional_blocks.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/block/minecraft_functional_blocks.json")).expect("entry source JSON"));
    out.insert("block/minecraft_metal_blocks.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/block/minecraft_metal_blocks.json")).expect("entry source JSON"));
    out.insert("block/minecraft_natural_blocks.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/block/minecraft_natural_blocks.json")).expect("entry source JSON"));
    out.insert("block/minecraft_redstone_components.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/block/minecraft_redstone_components.json")).expect("entry source JSON"));
    out.insert("block/minecraft_stone_blocks.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/block/minecraft_stone_blocks.json")).expect("entry source JSON"));
    out.insert("block/minecraft_wood_and_bamboo.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/block/minecraft_wood_and_bamboo.json")).expect("entry source JSON"));
    out.insert("minecraft_biome.json".into(), crate::entry_catalog::BIOMES.clone());
    out.insert("minecraft_combat_items.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/minecraft_combat_items.json")).expect("entry source JSON"));
    out.insert("minecraft_command_only_items.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/minecraft_command_only_items.json")).expect("entry source JSON"));
    out.insert("minecraft_food_and_drinks.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/minecraft_food_and_drinks.json")).expect("entry source JSON"));
    out.insert("minecraft_materials.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/minecraft_materials.json")).expect("entry source JSON"));
    out.insert("minecraft_paintings.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/minecraft_paintings.json")).expect("entry source JSON"));
    out.insert("minecraft_spawn_egg.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/minecraft_spawn_egg.json")).expect("entry source JSON"));
    out.insert("minecraft_structure.json".into(), crate::entry_catalog::STRUCTURES.clone());
    out.insert("minecraft_tools_and_utilities.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/minecraft_tools_and_utilities.json")).expect("entry source JSON"));
    out.insert("mobs/hostile.json".into(), crate::entry_catalog::HOSTILE.clone());
    out.insert("mobs/neutral.json".into(), crate::entry_catalog::NEUTRAL.clone());
    out.insert("mobs/passive.json".into(), crate::entry_catalog::PASSIVE.clone());
    out.insert("mobs/schema.json".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/mobs/schema.json")).expect("entry source JSON"));
    out
}
