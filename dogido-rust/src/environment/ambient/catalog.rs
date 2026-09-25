use serde::Serialize;
use serde_json::Value;
use std::sync::LazyLock;
static GENERAL: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../../data/fallbacks/general.json")).unwrap()
});
static REACTIONS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../../data/mobs/ambient_reactions.json")).unwrap()
});
static PASSIVE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../data/catalogs/entries/mobs/passive.json"
    ))
    .unwrap()
});
static NEUTRAL: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../data/catalogs/entries/mobs/neutral.json"
    ))
    .unwrap()
});
static BIOMES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../data/catalogs/entries/minecraft_biome.json"
    ))
    .unwrap()
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
pub fn reactions() -> &'static Value {
    &REACTIONS
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
pub fn tags(entry: &Value) -> Vec<String> {
    let mut tags = vec![];
    for key in [
        "visual_tags",
        "sound_tags",
        "motion_tags",
        "scene_tags",
        "reaction_tags",
        "comic_tags",
    ] {
        if let Some(values) = entry["poetic"][key].as_array() {
            for value in values {
                if let Some(s) = value.as_str().filter(|s| !s.is_empty())
                    && !tags.iter().any(|v| v == s)
                {
                    tags.push(s.to_owned());
                }
            }
        }
    }
    if let Some(role) = entry["poetic"]["role"].as_str().filter(|s| !s.is_empty())
        && !tags.iter().any(|s| s == role)
    {
        tags.push(role.to_owned());
    }
    tags.truncate(8);
    tags
}
static BIOME_REACTIONS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../../data/responses/ques/biome.json")).unwrap()
});
static STRUCTURES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../data/catalogs/entries/minecraft_structure.json"
    ))
    .unwrap()
});
pub fn biome_lines(biome: &str, phase: &str) -> Vec<String> {
    let r = &BIOME_REACTIONS["reactions"][biome];
    let payload = if r[phase].is_object() {
        &r[phase]
    } else {
        &r["default"]
    };
    let lines = if payload["lines"].is_array() {
        &payload["lines"]
    } else {
        &BIOME_REACTIONS["line_groups"][payload["line_group"].as_str().unwrap_or("")]
    };
    lines
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}
pub fn structure(key: &str) -> Option<Value> {
    STRUCTURES["groups"].as_object()?.iter().find_map(|(id,group)|{
        let entry=&group["structures"][key];if !entry.is_object(){return None;}
        Some(serde_json::json!({"label":entry["japanese"].as_str().unwrap_or(key),"note":entry["note"].as_str().unwrap_or(""),"group_id":id,"group_label":group["label"]}))
    })
}

pub fn dry_biome(kind: &str) -> bool {
    BIOMES["groups"]["dry"]["biomes"][norm(kind)].is_object()
}

static VEHICLES: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(include_str!("vehicle_labels.json")).unwrap());
pub fn vehicle_item_label(id: &str) -> Option<&'static str> {
    VEHICLES[id].as_str()
}
