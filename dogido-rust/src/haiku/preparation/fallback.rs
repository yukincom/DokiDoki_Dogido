use super::*;
use crate::text_format::{self, ContainerFormat::QuotedRepr};
use std::sync::LazyLock;
static CATALOG: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../../data/fallbacks/haiku.json"))
        .expect("haiku fallback catalogue")
});
pub fn failed_text() -> String {
    CATALOG["llm_failed_line"]
        .as_str()
        .unwrap_or("まとまらんかった。。。")
        .into()
}
pub fn strip_preface(text: &str) -> &str {
    let s = strip(text);
    strip(
        s.strip_prefix("ここで一句。")
            .or_else(|| s.strip_prefix("ここで一句"))
            .unwrap_or(s),
    )
}
pub fn failed(text: &str) -> bool {
    let s = strip_preface(text);
    s.is_empty() || s == strip(&failed_text()) || s.contains("まとまらん")
}
pub fn fixed_text(event: &GameEvent) -> String {
    let sky = event.world.sky_visible == Some(true);
    let raw = strip(event.world.biome.as_deref().unwrap_or("")).to_lowercase();
    let biome = if sky || raw == "deep_dark" || raw.ends_with("_caves") {
        if raw.is_empty() { "unknown" } else { &raw }
    } else {
        "unknown"
    };
    let phase = if sky {
        serde_json::to_value(event.world.time_phase).unwrap()
    } else {
        Value::Null
    };
    let weather = if sky {
        serde_json::to_value(event.world.weather).unwrap()
    } else {
        Value::Null
    };
    let matches = |rule: &Value| {
        if let Some(biomes) = rule["biomes"].as_array().filter(|a| !a.is_empty())
            && !biomes.iter().any(|v| v == biome)
        {
            return false;
        }
        if let Some(groups) = rule["biome_groups"].as_array().filter(|a| !a.is_empty())
            && !groups.iter().any(|g| {
                CATALOG["biome_groups"][g.as_str().unwrap_or("")]
                    .as_array()
                    .is_some_and(|bs| bs.iter().any(|b| b == biome))
            })
        {
            return false;
        }
        for (key, actual) in [("weather", &weather), ("time_phase", &phase)] {
            if !rule[key].is_null() && rule[key] != *actual {
                return false;
            }
        }
        for (key, actual) in [
            ("player_y", event.player.position.y),
            ("danger_darkness_score", event.world.danger_darkness_score),
        ] {
            for (suffix, min) in [("min", true), ("max", false)] {
                if let Some(n) = rule[format!("{key}_{suffix}")].as_f64()
                    && actual.is_none_or(|a| if min { a < n } else { a > n })
                {
                    return false;
                }
            }
        }
        for (key, actual) in [
            (
                "visual_threat_types_any",
                event
                    .visual_threats
                    .iter()
                    .map(|v| v.r#type.as_str())
                    .collect::<Vec<_>>(),
            ),
            (
                "passive_mob_types_any",
                event
                    .passive_mobs
                    .iter()
                    .map(|v| v.r#type.as_str())
                    .collect(),
            ),
        ] {
            if let Some(values) = rule[key].as_array().filter(|a| !a.is_empty())
                && !values
                    .iter()
                    .any(|v| actual.contains(&v.as_str().unwrap_or("")))
            {
                return false;
            }
        }
        let names = rule["nearby_resource_names_any"].as_array();
        let suffixes = rule["nearby_resource_suffixes_any"].as_array();
        if names.is_some_and(|a| !a.is_empty()) || suffixes.is_some_and(|a| !a.is_empty()) {
            return event.nearby_resources.iter().any(|r| {
                if r.name.is_empty() {
                    return false;
                }
                if let Some(max) = rule["nearby_resource_distance_max"].as_f64()
                    && r.distance.is_none_or(|d| d > max)
                {
                    return false;
                }
                let name = strip(r.name.rsplit(':').next().unwrap_or("")).to_lowercase();
                names.is_some_and(|a| {
                    a.iter().any(|v| {
                        strip(
                            text_format::value_text(v, QuotedRepr)
                                .rsplit(':')
                                .next()
                                .unwrap_or(""),
                        )
                        .to_lowercase()
                            == name
                    })
                }) || suffixes.is_some_and(|a| {
                    a.iter()
                        .any(|v| name.ends_with(&text_format::value_text(v, QuotedRepr)))
                })
            });
        }
        true
    };
    for rule in CATALOG["rules"].as_array().unwrap() {
        if matches(rule) {
            return text_format::value_text(&rule["line"], QuotedRepr);
        }
    }
    if let Some(line) = CATALOG["defaults"].get(biome) {
        return text_format::value_text(line, QuotedRepr);
    }
    for rule in CATALOG["group_defaults"].as_array().unwrap() {
        if matches(rule) {
            return text_format::value_text(&rule["line"], QuotedRepr);
        }
    }
    CATALOG["under_construction_line"]
        .as_str()
        .unwrap_or("今、考え中やねん…")
        .into()
}
