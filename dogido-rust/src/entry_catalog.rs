//! Shared immutable source documents. No technical Minecraft-cache search policy here.
use serde_json::Value;
use std::sync::LazyLock;

pub(crate) static HOSTILE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../data/catalogs/entries/mobs/hostile.json"
    ))
    .expect("hostile mob catalogue")
});
pub(crate) static NEUTRAL: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../data/catalogs/entries/mobs/neutral.json"
    ))
    .expect("neutral mob catalogue")
});
pub(crate) static PASSIVE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../data/catalogs/entries/mobs/passive.json"
    ))
    .expect("passive mob catalogue")
});
pub(crate) static STRUCTURES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../data/catalogs/entries/minecraft_structure.json"
    ))
    .expect("structure catalogue")
});

pub(crate) static BIOMES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../data/catalogs/entries/minecraft_biome.json"
    ))
    .expect("biome catalogue")
});
