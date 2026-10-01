//! Immutable labels and climate from the existing source catalogues, without search.
use crate::chat_catalog::{Catalog, strip, text, truth};
use crate::environment::precipitation::Climate;
use anyhow::{Result, bail};
use serde_json::{Map, Value};
use std::sync::LazyLock;
mod source;
include!("world_catalog/documents.rs");
static BUILTIN: LazyLock<WorldCatalog> = LazyLock::new(|| {
    WorldCatalog::from_documents(&bundled_documents(), &crate::entry_catalog::BIOMES)
});
static RULES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("world_catalog/rules.json")).expect("world label rules")
});
pub fn catalog() -> &'static WorldCatalog {
    &BUILTIN
}
#[derive(Clone, Debug)]
pub struct WorldCatalog {
    items: Map<String, Value>,
    blocks: Map<String, Value>,
    biomes: Map<String, Value>,
    raw_items: Map<String, Value>,
    raw_blocks: Map<String, Value>,
}
impl WorldCatalog {
    pub fn from_documents(docs: &Map<String, Value>, biomes: &Value) -> Self {
        let (items, blocks) = source::labels(docs);
        let (raw_items, raw_blocks) = source::raw_entries(docs);
        Self {
            items,
            blocks,
            raw_items,
            raw_blocks,
            biomes: flatten_biomes(biomes),
        }
    }
    pub fn item_labels(&self) -> &Map<String, Value> {
        &self.items
    }
    pub fn block_labels(&self) -> &Map<String, Value> {
        &self.blocks
    }
    pub fn biome_entries(&self) -> &Map<String, Value> {
        &self.biomes
    }
    /// This is NarrationMixin's overriding method, deliberately NOT WorldAnalysisMixin's.
    pub fn item_label(&self, id: Option<&str>) -> String {
        let key = id.unwrap_or("");
        if key.is_empty() {
            return String::new();
        }
        let key = key.strip_prefix("minecraft:").unwrap_or(key);
        self.items
            .get(key)
            .filter(|v| truth(v))
            .or_else(|| self.blocks.get(key).filter(|v| truth(v)))
            .map(text)
            .unwrap_or_else(|| key.into())
    }
    pub fn block_label(&self, id: Option<&str>) -> String {
        let original = id.unwrap_or("");
        if original.is_empty() {
            return String::new();
        }
        let normalized = strip(original.rsplit(':').next().unwrap_or("")).to_lowercase();
        if normalized.is_empty() || normalized == "air" {
            return String::new();
        }
        if let Some(mapped) = self.blocks.get(&normalized) {
            return text(mapped);
        }
        for (suffix, ja) in [
            ("_log", "の原木"),
            ("_planks", "の板材"),
            ("_wool", "の羊毛"),
            ("_bed", "のベッド"),
            ("_leaves", "の葉"),
        ] {
            if let Some(token) = normalized.strip_suffix(suffix) {
                return format!("{}{ja}", material_label(token));
            }
        }
        if normalized.is_ascii() {
            normalized.replace('_', " ")
        } else {
            original.into()
        }
    }
    pub fn biome_entry(&self, id: Option<&str>) -> Option<&Value> {
        let key = biome_key(id);
        if key.is_empty() {
            None
        } else {
            self.biomes.get(&key)
        }
    }
    pub fn biome_label(&self, id: Option<&str>) -> String {
        let key = biome_key(id);
        if key.is_empty() {
            return "そのへん".into();
        }
        if let Some(entry) = self.biomes.get(&key) {
            return entry.get("label").map(text).unwrap_or(key);
        }
        if key.is_ascii() {
            "そのへん".into()
        } else {
            id.unwrap_or("").into()
        }
    }
    /// Climate is catalog-only. Current Y/weather/dimension enter precipitation separately.
    pub fn climate(&self, id: Option<&str>) -> Result<Climate> {
        let entry = self.biome_entry(id).unwrap_or(&Value::Null);
        Ok(Climate {
            biome_temperature: metric(&entry["temperature"]),
            biome_downfall: metric(&entry["downfall"]),
            snow_start_y: integer(&entry["snow_starts_at_y"])?,
            biome_group_id: entry
                .get("group_id")
                .filter(|v| truth(v))
                .map(text)
                .unwrap_or_default(),
        })
    }
    pub fn look_target_label(
        &self,
        catalog: &Catalog,
        target: Option<&crate::events::LookTarget>,
    ) -> String {
        let Some(target) = target.filter(|t| !t.name.is_empty()) else {
            return String::new();
        };
        let name = &target.name;
        let kind = if target.kind.is_empty() {
            "block".to_owned()
        } else {
            target.kind.to_lowercase()
        };
        if kind == "entity" {
            if let Some(label) = catalog
                .mob_entry(name)
                .and_then(|e| e.get("label"))
                .filter(|v| truth(v))
            {
                return text(label);
            }
            return crate::combat::catalog::labels()
                .get(name)
                .map(text)
                .unwrap_or_else(|| name.clone());
        }
        let label = self.block_label(Some(name));
        if label.is_empty() {
            name.clone()
        } else {
            label
        }
    }
}
pub fn structure_label(catalog: &Catalog, id: Option<&str>) -> String {
    let original = id.unwrap_or("");
    if original.is_empty() {
        return "なにかの建物".into();
    }
    let key = strip(original).to_lowercase();
    if let Some(entry) = catalog.structure_entries().get(&key) {
        return entry.get("label").map(text).unwrap_or(key);
    }
    if key.is_ascii() {
        "なにかの建物".into()
    } else {
        original.into()
    }
}
fn biome_key(id: Option<&str>) -> String {
    let lower = strip(id.unwrap_or("")).to_lowercase();
    lower.strip_prefix("minecraft:").unwrap_or(&lower).into()
}
pub fn material_label(token: &str) -> String {
    let normalized = strip(token).to_lowercase();
    if normalized.is_empty() {
        return "その".into();
    }
    RULES["material_labels"]
        .get(&normalized)
        .map(text)
        .unwrap_or_else(|| normalized.replace('_', " "))
}
pub(crate) fn foliage_biome(id: &str) -> bool {
    RULES["foliage_shade_biomes"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v.as_str() == Some(id))
}
pub fn metric(value: &Value) -> Option<f64> {
    fn numeric(v: &Value) -> Option<f64> {
        v.as_f64()
            .or_else(|| v.as_bool().map(|v| if v { 1.0 } else { 0.0 }))
    }
    if let Some(n) = numeric(value) {
        return Some(n);
    }
    let values = value.as_object()?;
    values
        .get("java")
        .and_then(numeric)
        .or_else(|| values.values().find_map(numeric))
}
fn integer(v: &Value) -> Result<Option<i64>> {
    if v.is_null() {
        return Ok(None);
    }
    if let Some(b) = v.as_bool() {
        return Ok(Some(i64::from(b)));
    }
    if let Some(n) = v.as_i64() {
        return Ok(Some(n));
    }
    if let Some(n) = v.as_str() {
        return Ok(Some(strip(n).parse()?));
    }
    if let Some(n) = v.as_f64() {
        let n = n.trunc();
        if n >= i64::MIN as f64 && n < (i64::MAX as f64) {
            return Ok(Some(n as i64));
        }
    }
    bail!("biome snow_starts_at_y cannot be represented as an integer")
}
fn flatten_biomes(doc: &Value) -> Map<String, Value> {
    let mut out = Map::new();
    let Some(doc) = doc.as_object() else {
        return out;
    };
    if let Some(groups) = doc.get("groups").and_then(Value::as_object) {
        for (group_id, group) in groups {
            let Some(group) = group.as_object() else {
                continue;
            };
            let Some(biomes) = group.get("biomes").and_then(Value::as_object) else {
                continue;
            };
            for (id, biome) in biomes {
                let Some(mut entry) = biome.as_object().cloned() else {
                    continue;
                };
                let label = entry
                    .shift_remove("japanese")
                    .unwrap_or(Value::String(id.clone()));
                entry.insert("label".into(), label);
                entry.insert("group_id".into(), Value::String(group_id.clone()));
                entry.insert(
                    "group_label".into(),
                    group.get("label").cloned().unwrap_or(Value::Null),
                );
                entry.insert(
                    "group_description".into(),
                    group.get("description").cloned().unwrap_or(Value::Null),
                );
                for (k, v) in group {
                    if !matches!(k.as_str(), "label" | "description" | "biomes") {
                        entry.insert(format!("group_{k}"), v.clone());
                    }
                }
                out.insert(id.clone(), Value::Object(entry));
            }
        }
    } else {
        for (k, v) in doc {
            out.insert(k.clone(), serde_json::json!({"label":text(v)}));
        }
    }
    out
}

impl crate::haiku::materials::Entries for WorldCatalog {
    fn item_entry(&self, id: &str) -> Option<&Value> {
        self.raw_items
            .get(&strip(id.rsplit(':').next().unwrap_or("")).to_lowercase())
    }
    fn block_entry(&self, id: &str) -> Option<&Value> {
        self.raw_blocks
            .get(&strip(id.rsplit(':').next().unwrap_or("")).to_lowercase())
    }
    fn mob_label(&self, id: &str) -> String {
        VOICE_CATALOG
            .all_mob_entries()
            .get(id)
            .map(|e| e.get("label").map(text).unwrap_or_else(|| id.into()))
            .unwrap_or_else(|| id.into())
    }
}

static VOICE_CATALOG: LazyLock<Catalog> = LazyLock::new(|| {
    Catalog::from_documents(
        [
            &crate::entry_catalog::PASSIVE,
            &crate::entry_catalog::HOSTILE,
            &crate::entry_catalog::NEUTRAL,
        ],
        &crate::entry_catalog::STRUCTURES,
    )
});
