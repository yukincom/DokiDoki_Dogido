//! The editable situation text is shared with Python, not generated from it.
use crate::types::{ChatMessage, Role};
use anyhow::{Context, Result};
use serde_json::{Value, json};
use std::sync::LazyLock;

static SITUATIONS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../dogido_server/llm/reaction_situations.json"
    ))
    .expect("reaction situation source")
});

pub(super) use crate::compat::json_truthy as truth;
pub(super) fn pystr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(v) => v.clone(),
        _ => v.to_string(),
    }
}

fn situation(kind: &str, d: &Value) -> Result<&'static str> {
    let kind = if kind == "daylight_water_skeleton" {
        "daylight_water"
    } else {
        kind
    };
    SITUATIONS
        .as_object()
        .unwrap()
        .values()
        .flat_map(|group| group.as_array().unwrap())
        .find(|row| row["event"] == kind)
        .and_then(|row| {
            row["situation_key"]
                .as_str()
                .and_then(|key| d[key].as_str())
                .and_then(|encounter| row["situations"][encounter].as_str())
                .or_else(|| row["situation"].as_str())
        })
        .context("unsupported reaction situation")
}

fn observation_fields(kind: &str) -> &[&str] {
    match kind {
        "death" => &["cause", "hostile"],
        "aftermath" => &[
            "hostiles",
            "health_state",
            "combat_outcome",
            "hostile_clear_confirmed",
        ],
        "daylight_water" | "daylight_water_skeleton" => &["hostiles", "count", "mob_states"],
        "newly_burning_visual" => &["hostile", "distance"],
        "deep_dark_ominous_sound" => &["ominous_kind", "ominous_stage"],
        "occluded_hostile_presence" => &["direction", "hostile", "distance_band", "sound_event"],
        "ambient" => &[
            "mob",
            "direction",
            "distance",
            "mob_profession",
            "mob_is_baby",
        ],
        "weather_transition" => &["weather_from", "weather_to", "scene"],
        "thunder_reaction" => &["scene", "nearby_lightning"],
        "ender_eye_throw" => &[],
        "structure_entry" => &["structure_label"],
        "light_source_gain" => &["surroundings_light"],
        "darkness_escape" => &["hostiles"],
        "occluded_entry_with_light" => &["local_light"],
        "occluded_entry_no_light" => &["local_light", "craftable"],
        "dark_push_no_light" | "dark_push_after_breath" => &["local_light", "hostiles"],
        "emergency_shelter_relief" => &["ceiling_height", "enclosure_score"],
        "portal_appearance" => &[
            "portal_type",
            "portal_label",
            "portal_distance",
            "dimension",
            "portal_encounter",
        ],
        _ => &[],
    }
}

fn selected(d: &Value, fields: &[&str]) -> serde_json::Map<String, Value> {
    fields
        .iter()
        .filter_map(|key| {
            d.get(*key)
                .filter(|v| !v.is_null())
                .map(|value| ((*key).into(), value.clone()))
        })
        .collect()
}

pub(super) fn context(kind: &str, d: &Value) -> Result<Value> {
    let mut context = json!({"event":kind,"situation":situation(kind, d)?});
    if kind == "thunder_reaction"
        && let Some(status) = d["scream_status"].as_str()
    {
        let state = SITUATIONS
            .as_object()
            .unwrap()
            .values()
            .flat_map(|group| group.as_array().unwrap())
            .find(|row| row["event"] == kind)
            .and_then(|row| row["self_states"][status].as_str());
        if let Some(state) = state {
            context["self_state"] = json!({"scream_status":status,"situation":state});
        }
    }
    let mut observations = selected(d, &["biome", "time_phase"]);
    observations.extend(selected(d, observation_fields(kind)));
    if kind == "structure_entry" && d["group_id"] == "village" {
        let count = d["visible_villager_count"].as_i64();
        let crowd = match count {
            None => "未確認",
            Some(n) if n >= 10 => "多い（10人以上）",
            Some(n) if n > 0 => "多くはない（10人未満）",
            _ => "観測範囲では未検出",
        };
        observations.insert(
            "villagers".into(),
            json!({
                "scope":"16ブロック以内で見通しが通る範囲。村全体の人口ではない。",
                "count":count,"crowd":crowd
            }),
        );
    }
    if kind == "portal_appearance" && d["portal_encounter"].is_null() {
        observations.insert(
            "appearance_fact".into(),
            "新しく出現したか、前からあったかは未確認。".into(),
        );
    }
    if kind == "aftermath" {
        let outcome = d
            .get("combat_outcome")
            .filter(|v| truth(v))
            .map(pystr)
            .unwrap_or("disengaged".into());
        let fact = match outcome.as_str() {
            "player_kill" => "追跡していた敵の死亡と、プレイヤーによる撃破根拠を確認済み。",
            "charged_creeper_detonated" => {
                "追跡していた帯電クリーパーが爆発して消えたことを確認済み。"
            }
            "creeper_detonated" => "追跡していたクリーパーが爆発して消えたことを確認済み。",
            "explosion_death" => "追跡していた敵が爆発による致死ダメージで死亡したことを確認済み。",
            "hostile_defeated" => "追跡していた敵の死亡は確認済み。誰が倒したかは未確認。",
            "disengaged" => "交戦が終了した。敵の死亡は確認されていない。",
            _ => "敵の死亡と撃破者は未確認。",
        };
        observations.insert("outcome_fact".into(), fact.into());
        observations.insert(
            "surroundings_fact".into(),
            if truth(&d["hostile_clear_confirmed"]) {
                "現在の観測範囲で視認敵・敵音・周辺敵数がすべて空と確認済み。"
            } else {
                "現在の観測範囲に敵が残っていないかは未確認。"
            }
            .into(),
        );
    }
    if kind == "light_source_gain" && d["comment_action"] == "relief_after_darkness" {
        observations.insert("darkness_recovered".into(), true.into());
    }
    if !observations.is_empty() {
        context["observations"] = observations.into();
    }
    let mut properties = selected(
        d,
        match kind {
            "ambient" => &["mob_temperament", "mob_caution_reason"],
            "structure_entry" => &["structure_note"],
            _ => &[],
        },
    );
    if kind == "portal_appearance" {
        let dimension = d["dimension"].as_str().unwrap_or("").trim().to_lowercase();
        let portal_type = d["portal_type"]
            .as_str()
            .unwrap_or("")
            .trim()
            .to_lowercase();
        let row = SITUATIONS
            .as_object()
            .unwrap()
            .values()
            .flat_map(|group| group.as_array().unwrap())
            .find(|row| row["event"] == kind)
            .context("portal role source")?;
        let role = row["roles"][dimension.trim_start_matches("minecraft:")]
            [portal_type.trim_start_matches("minecraft:")]
        .as_str()
        .or_else(|| row["unknown_role"].as_str())
        .context("portal role")?;
        properties.insert("portal_role".into(), role.into());
    }
    if !properties.is_empty() {
        context["properties"] = properties.into();
    }
    if truth(&d["player_name"]) {
        context["player_name"] = d["player_name"].clone();
    }
    if let Some(conversation) = d["conversation_context"].as_object() {
        for key in ["world_context", "recent"] {
            if let Some(value) = conversation.get(key).filter(|v| !v.is_null()) {
                context[key] = value.clone();
            }
        }
    }
    crate::catalog_knowledge::separate(context.as_object_mut().unwrap());
    crate::villager_routines::separate(context.as_object_mut().unwrap());
    Ok(context)
}

fn mode(kind: &str, d: &Value) -> &'static str {
    match pystr(&d["character_mode"]).trim().to_lowercase().as_str() {
        "normal" => return "normal",
        "base" => return "base",
        "tension" => return "tension",
        "workshop" => return "workshop",
        _ => {}
    }
    match kind {
        "aftermath" | "newly_burning_visual" | "occluded_hostile_presence" => "base",
        "daylight_water"
        | "daylight_water_skeleton"
        | "deep_dark_ominous_sound"
        | "darkness_escape"
        | "occluded_entry_with_light"
        | "occluded_entry_no_light"
        | "dark_push_no_light"
        | "dark_push_after_breath" => "tension",
        _ => "normal",
    }
}

pub(super) fn messages(kind: &str, d: &Value) -> Result<Vec<ChatMessage>> {
    Ok(vec![
        ChatMessage {
            role: Role::System,
            content: crate::companion_prompt::dialogue(mode(kind, d)),
        },
        ChatMessage {
            role: Role::User,
            content: serde_json::to_string(&context(kind, d)?)?,
        },
    ])
}
