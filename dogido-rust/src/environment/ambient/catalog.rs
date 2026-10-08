use crate::entry_catalog::{BIOMES, NEUTRAL, PASSIVE};
use serde::Serialize;
use serde_json::Value;
use std::sync::LazyLock;
static GENERAL: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../../data/fallbacks/general.json")).unwrap()
});
static EXPLORATION: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../data/responses/ques/exploration.json"
    ))
    .unwrap()
});
pub fn enum_text<T: Serialize>(v: &T) -> String {
    serde_json::to_value(v)
        .unwrap()
        .as_str()
        .unwrap_or("")
        .to_owned()
}
pub fn general(group: &str, key: &str) -> String {
    GENERAL[group][key]
        .as_str()
        .expect("general text")
        .to_owned()
}
pub fn exploration() -> &'static Value {
    &EXPLORATION
}
pub fn norm(text: &str) -> String {
    text.trim()
        .to_lowercase()
        .trim_start_matches("minecraft:")
        .to_owned()
}
pub fn mob(kind: &str, profession: Option<&str>, baby: bool) -> Value {
    let kind = norm(kind);
    let base = if PASSIVE["items"][&kind].is_object() {
        &PASSIVE["items"][&kind]
    } else {
        &NEUTRAL["items"][&kind]
    };
    let mut result = base.clone();
    if kind == "villager" {
        let overlay = if baby {
            &base["baby"]
        } else {
            &base["professions"][profession.unwrap_or("")]
        };
        if overlay.is_object() {
            for key in ["label", "job_site"] {
                if overlay[key].as_str().is_some_and(|s| !s.is_empty()) {
                    result[key] = overlay[key].clone();
                }
            }
            if let Some(fields) = overlay["poetic"].as_object() {
                for (k, v) in fields {
                    if !v.is_null()
                        && v.as_str() != Some("")
                        && v.as_array().is_none_or(|a| !a.is_empty())
                    {
                        result["poetic"][k] = v.clone();
                    }
                }
            }
        }
    }
    result
}
pub fn mob_label(kind: &str) -> String {
    mob(kind, None, false)["label"]
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| crate::combat::model::label(kind))
}
pub fn biome_label(kind: &str) -> String {
    let key = norm(kind);
    BIOMES["groups"]
        .as_object()
        .unwrap()
        .values()
        .find_map(|g| g["biomes"][&key]["japanese"].as_str())
        .unwrap_or(kind)
        .to_owned()
}
static VEHICLES: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(include_str!("vehicle_labels.json")).unwrap());
pub fn vehicle_item_label(id: &str) -> Option<&'static str> {
    VEHICLES[id].as_str()
}
