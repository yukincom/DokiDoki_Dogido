use super::*;
use std::sync::LazyLock;
static DARKNESS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../data/responses/ques/darkness.json"
    ))
    .expect("darkness catalog")
});
static EXPLORATION: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../data/responses/ques/exploration.json"
    ))
    .expect("exploration catalog")
});
static FALLBACK: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../../data/fallbacks/general.json"))
        .expect("fallback catalog")
});
static BIOMES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../../data/catalogs/entries/minecraft_biome.json"
    ))
    .expect("biome catalog")
});
static THREATS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../threat_catalog.json")).expect("threat catalog")
});
pub(super) fn ranged(k: &str) -> bool {
    THREATS["ranged"]
        .as_array()
        .expect("ranged")
        .iter()
        .any(|v| v.as_str() == Some(k))
}
pub(super) fn range(k: &str) -> f64 {
    THREATS["effective_range"][k].as_f64().unwrap_or(6.0)
}
pub(super) fn node(topic: &str, path: &[&str]) -> &'static Value {
    let mut v = match topic {
        "darkness" => &*DARKNESS,
        "exploration" => &*EXPLORATION,
        "fallback" => &*FALLBACK,
        _ => panic!("unknown environment catalog"),
    };
    for k in path {
        v = &v[*k];
    }
    v
}
pub(super) fn text(topic: &str, path: &[&str]) -> String {
    node(topic, path).as_str().unwrap_or("").to_owned()
}
pub(super) fn biome_entry(e: &GameEvent) -> Option<&'static Value> {
    BIOMES["groups"]
        .as_object()?
        .values()
        .find_map(|g| g["biomes"].get(biome(e)))
}
pub(super) fn biome_label(e: &GameEvent) -> String {
    biome_entry(e)
        .and_then(|b| b["japanese"].as_str())
        .unwrap_or(e.world.biome.as_deref().unwrap_or("いまの場所"))
        .to_owned()
}
pub(super) fn cold(e: &GameEvent) -> bool {
    matches!(
        biome(e),
        "snowy_plains"
            | "ice_spikes"
            | "snowy_taiga"
            | "snowy_slopes"
            | "frozen_river"
            | "snowy_beach"
            | "frozen_ocean"
            | "deep_frozen_ocean"
            | "frozen_peaks"
            | "jagged_peaks"
            | "grove"
    )
}
pub(super) fn dry(e: &GameEvent) -> bool {
    BIOMES["groups"]["dry"]["biomes"].get(biome(e)).is_some()
}
pub(super) fn context(e: &GameEvent, s: &Settings) -> Value {
    json!({"player_name":crate::combat::core::call_name(e,s),"biome":biome_label(e),"time_phase":e.world.time_phase.map(|p|serde_json::to_value(p).expect("phase")).unwrap_or(json!("unknown"))})
}
pub(super) fn leaf(kind: &'static str, text: String, details: Value, temperature: f64) -> Speech {
    let mut a = Speech::new(kind, text);
    a.leaf = Some(LeafRequest {
        kind: kind.to_owned(),
        details,
        temperature,
    });
    a
}
pub(super) fn dark_leaf(kind: &'static str, e: &GameEvent, s: &Settings) -> Speech {
    let mut details = context(e, s);
    let mut hostiles = e
        .visual_threats
        .iter()
        .map(|t| crate::combat::model::label(&t.r#type))
        .collect::<Vec<_>>();
    if hostiles.is_empty() && !e.auditory_threats.is_empty() {
        hostiles.push("気配あり".to_owned());
    }
    let (key, temp) = match kind {
        "occluded_entry_with_light" => (kind, 0.5),
        "occluded_entry_no_light" => {
            details["craftable"] = json!(craftable(e));
            (kind, 0.42)
        }
        "dark_push_no_light" => {
            details["hostiles"] = json!(hostiles);
            (kind, 0.58)
        }
        "darkness_escape" => {
            details["hostiles"] = json!(hostiles);
            (kind, 0.62)
        }
        "emergency_shelter_relief" => {
            details["ceiling_height"] = json!(e.world.ceiling_height);
            details["enclosure_score"] = json!(e.world.enclosure_score);
            (kind, 0.5)
        }
        "dark_push_after_breath" => {
            details["hostiles"] = json!(hostiles);
            details["time_phase"] = phase(e)
                .map(|p| serde_json::to_value(p).expect("phase"))
                .unwrap_or(json!("unknown"));
            (
                match phase(e) {
                    Some(TimePhase::Evening) => "dark_push_after_breath_evening",
                    Some(TimePhase::Night) => "dark_push_after_breath_night",
                    _ => "dark_push_after_breath_default",
                },
                0.5,
            )
        }
        _ => panic!("unknown dark leaf"),
    };
    if !matches!(kind, "emergency_shelter_relief" | "darkness_escape") {
        details["local_light"] = json!(e.world.local_light);
    }
    let name = crate::combat::core::call_name(e, s);
    let prefix = if name.is_empty() || name == "プレイヤー" {
        String::new()
    } else {
        format!("{name}、")
    };
    leaf(
        kind,
        text("fallback", &["darkness", key]).replace("{prefix}", &prefix),
        details,
        temp,
    )
}
