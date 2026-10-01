//! Pure catalogue plausibility and observed-Mob tactics. No observation memory,
//! entity inference, model calls, or world actions. Presence stays caller-owned.
use crate::{
    chat_catalog::{Catalog, Hit, Kind, normalized_observation_id, strip, text, truth},
    events::VisualThreat,
};
use anyhow::{Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;

fn value_text(v: &Value) -> String {
    if truth(v) { text(v) } else { String::new() }
}
pub fn normalize_biome_id(raw: Option<&str>) -> Option<String> {
    let normalized = normalized_observation_id(raw.unwrap_or("")).replace('-', "_");
    (!normalized.is_empty()).then_some(normalized)
}
fn structure_biomes(catalog: &Catalog, id: &str) -> HashSet<String> {
    catalog
        .structure_entries()
        .get(id)
        .and_then(|e| e["biomes"].as_array())
        .into_iter()
        .flatten()
        .filter_map(|raw| normalize_biome_id(Some(&text(raw))))
        .collect()
}
fn structures_for_mob<'a>(catalog: &'a Catalog, raw: &str) -> Vec<&'a str> {
    let id = normalized_observation_id(raw);
    catalog
        .structure_entries()
        .iter()
        .filter_map(|(structure_id, entry)| {
            let related = entry["related_mobs"].as_array()?;
            related
                .iter()
                .any(|raw| {
                    let key = normalized_observation_id(&value_text(raw));
                    !key.is_empty() && key == id
                })
                .then_some(structure_id.as_str())
        })
        .collect()
}
pub fn structure_ids_for_plausibility(catalog: &Catalog, topics: &[Hit]) -> Vec<String> {
    let mut ids = vec![];
    let mut add = |id: &str| {
        if !id.is_empty()
            && !ids.iter().any(|s| s == id)
            && catalog.structure_entries().contains_key(id)
        {
            ids.push(id.into());
        }
    };
    for hit in topics {
        let id = strip(&hit.entry_id);
        if id.is_empty() {
            continue;
        }
        if hit.kind == Kind::Structure {
            add(id);
            continue;
        }
        let terms: Vec<_> = hit
            .matched_terms
            .iter()
            .map(|s| strip(s))
            .filter(|s| !s.is_empty())
            .collect();
        if terms.is_empty() {
            continue;
        }
        let mob_label = strip(&hit.label_ja);
        for structure_id in structures_for_mob(catalog, id) {
            let label = value_text(&catalog.structure_entries()[structure_id]["label"]);
            if terms
                .iter()
                .any(|term| term.chars().count() >= 2 && label.contains(term) && *term != mob_label)
            {
                add(structure_id);
            }
        }
    }
    ids
}
pub fn build_plausibility_hint_lines(
    catalog: &Catalog,
    explicit_ids: &[String],
    topics: Option<&[Hit]>,
    biome_id: Option<&str>,
    biome_label: Option<&str>,
) -> Vec<String> {
    let mut ids = explicit_ids.to_vec();
    if let Some(topics) = topics {
        for id in structure_ids_for_plausibility(catalog, topics) {
            if !ids.contains(&id) {
                ids.push(id);
            }
        }
    }
    let biome = normalize_biome_id(biome_id);
    let place = strip(biome_label.unwrap_or(""));
    let place = if place.is_empty() {
        biome.as_deref().unwrap_or("いまの場所")
    } else {
        place
    };
    let mut lines = vec![];
    let mut seen = HashSet::new();
    for raw in ids {
        let id = strip(&raw);
        if id.is_empty() || seen.contains(id) {
            continue;
        }
        let Some(entry) = catalog.structure_entries().get(id) else {
            continue;
        };
        seen.insert(id.to_owned());
        let label = if truth(&entry["label"]) {
            text(&entry["label"])
        } else {
            id.into()
        };
        let biomes = structure_biomes(catalog, id);
        if biomes.is_empty() {
            continue;
        }
        let Some(biome) = biome.as_deref() else {
            lines.push(format!(
                "{label}: 生成バイオームの知識はあるが、いまの場所は不明"
            ));
            continue;
        };
        lines.push(if biomes.contains(biome) {
            format!("{label}: いまの場所（{place}）は生成されうるバイオームに含む → ありうる")
        } else {
            format!("{label}: いまの場所（{place}）には生成されにくいかも")
        });
        if id == "pillager_outpost" {
            lines.push("（ピリジャーがいること自体は襲撃でも起きる。前哨の有無は別）".into());
        }
    }
    lines
}
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct Plausibility {
    pub structure_ids: Vec<String>,
    pub lines: Vec<String>,
    pub hints: String,
}
/// Exact ordinary-chat gate. Catalog related_mobs never establishes a location.
pub fn player_chat_plausibility(
    catalog: &Catalog,
    stance: &str,
    topics: &[Hit],
    biome_id: Option<&str>,
    biome_label: Option<&str>,
) -> Plausibility {
    if stance != "hypothesis" || topics.is_empty() {
        return Plausibility::default();
    }
    let structure_ids = structure_ids_for_plausibility(catalog, topics);
    // Narration normalizes once before the canonical hint function normalizes again.
    let normalized = normalize_biome_id(biome_id);
    let lines = build_plausibility_hint_lines(
        catalog,
        &[],
        Some(topics),
        normalized.as_deref(),
        biome_label,
    );
    let hints = lines
        .iter()
        .map(|line| format!("- {line}"))
        .collect::<Vec<_>>()
        .join("\n");
    Plausibility {
        structure_ids,
        lines,
        hints,
    }
}
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct Tactics {
    pub labels: Vec<String>,
    pub notes: Vec<String>,
    pub forbidden_advice: Vec<String>,
    pub safe_hints: Vec<String>,
}
fn iterable(v: &Value) -> Result<Vec<String>> {
    if !truth(v) {
        return Ok(vec![]);
    }
    Ok(match v {
        Value::Array(a) => a.iter().map(text).collect(),
        Value::String(s) => s.chars().map(|c| c.to_string()).collect(),
        Value::Object(o) => o.keys().cloned().collect(),
        _ => bail!("catalog tactics list is not iterable"),
    })
}
pub fn collect_tactics(catalog: &Catalog, ids: &[String]) -> Result<Tactics> {
    let mut out = Tactics::default();
    let mut seen = HashSet::new();
    for raw in ids {
        let id = normalized_observation_id(raw);
        if id.is_empty() || !seen.insert(id.clone()) {
            continue;
        }
        let Some(entry) = catalog.mob_entry(&id) else {
            continue;
        };
        let label = if truth(&entry["label"]) {
            text(&entry["label"])
        } else {
            id
        };
        out.labels.push(label.clone());
        let Some(tactics) = entry["dogido_tactics"].as_object() else {
            continue;
        };
        if let Some(note) = tactics.get("notes").filter(|v| truth(v)) {
            out.notes.push(format!("{label}: {}", text(note)));
        }
        for (key, target) in [
            ("forbidden_advice", &mut out.forbidden_advice),
            ("safe_hints", &mut out.safe_hints),
        ] {
            for raw in iterable(tactics.get(key).unwrap_or(&Value::Null))? {
                let s = strip(&raw);
                if !s.is_empty() && !target.iter().any(|v| v == s) {
                    target.push(s.into());
                }
            }
        }
    }
    Ok(out)
}
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChatTactics {
    pub nearby_hostile_types: Vec<String>,
    pub notes: Vec<String>,
    pub forbidden_advice: Vec<String>,
    pub safe_hints: Vec<String>,
    pub safe_fallback: Option<String>,
}
/// `recent_visual_types` must come from the existing timed visual snapshot.
/// Topic hypotheses, user reports, hearing and assistant history are not inputs.
pub fn player_chat_tactics(
    catalog: &Catalog,
    visual: &[VisualThreat],
    recent_visual_types: &[String],
) -> Result<ChatTactics> {
    let mut ids = vec![];
    for raw in visual
        .iter()
        .map(|t| &t.r#type)
        .chain(recent_visual_types.iter())
    {
        let id = normalized_observation_id(raw);
        if !id.is_empty() && !ids.contains(&id) {
            ids.push(id);
        }
    }
    if ids.is_empty() {
        return Ok(ChatTactics::default());
    }
    let tactics = collect_tactics(catalog, &ids)?;
    let safe_fallback = if !tactics.forbidden_advice.is_empty() || !tactics.safe_hints.is_empty() {
        visual
            .iter()
            .reduce(|best, next| {
                if next.distance.unwrap_or(999.0) < best.distance.unwrap_or(999.0) {
                    next
                } else {
                    best
                }
            })
            .map(|nearest| {
                let direction = crate::threats::direction(nearest);
                // Canonical _hostile_label is an exact lookup; model::label strips
                // minecraft: and therefore has a different contract on raw inputs.
                let label = crate::combat::catalog::labels()[&nearest.r#type]
                    .as_str()
                    .unwrap_or(&nearest.r#type);
                let hint = tactics
                    .safe_hints
                    .first()
                    .map(String::as_str)
                    .unwrap_or("気いつけ");
                format!("{direction}に{label}や！{hint}や！")
            })
    } else {
        None
    };
    Ok(ChatTactics {
        nearby_hostile_types: ids,
        notes: tactics.notes,
        forbidden_advice: tactics.forbidden_advice,
        safe_hints: tactics.safe_hints,
        safe_fallback,
    })
}
