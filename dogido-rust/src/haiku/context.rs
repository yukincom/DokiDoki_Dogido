//! Owned one-job context projections. No inference, state mutation, or persistence.
use super::{
    SourceAtom,
    source_atoms::{CatalogSourceSnapshot, PrefaceClause, preface_clauses_from_payload},
};
use crate::text_format::{self, ContainerFormat::QuotedRepr};
use crate::{
    chat_catalog::{strip, truth},
    environment::precipitation::PrecipitationContext,
};
use anyhow::{Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::{collections::HashMap, sync::LazyLock};
static DECIMAL_DIGITS: LazyLock<HashMap<char, char>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("context-decimal-digits.json"))
        .expect("canonical Python decimal digits")
});
static FLOAT: LazyLock<regex::Regex> = LazyLock::new(|| {
    regex::Regex::new(r"(?i)^[+-]?(?:inf(?:inity)?|nan|(?:[0-9](?:_?[0-9])*(?:\.(?:[0-9](?:_?[0-9])*)?)?|\.[0-9](?:_?[0-9])*)(?:e[+-]?[0-9](?:_?[0-9])*)?)$").unwrap()
});
fn float_string(raw: &str) -> Option<f64> {
    let normalized: String = raw
        .trim_matches(char::is_whitespace)
        .chars()
        .map(|c| DECIMAL_DIGITS.get(&c).copied().unwrap_or(c))
        .collect();
    FLOAT
        .is_match(&normalized)
        .then(|| normalized.replace('_', "").parse::<f64>().ok())
        .flatten()
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Feature {
    pub source: String,
    pub key: String,
    pub label: String,
    #[serde(default)]
    pub tags: Vec<String>,
}
impl Feature {
    pub fn prompt_label(&self) -> String {
        strip(&format!("{} {}", self.source, self.label)).into()
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Irony {
    pub found: bool,
    pub kind: String,
    pub description: String,
    pub elements: Vec<String>,
    pub focus: Vec<String>,
    pub confidence: f64,
}
impl Default for Irony {
    fn default() -> Self {
        Self {
            found: false,
            kind: "none".into(),
            description: String::new(),
            elements: vec![],
            focus: vec![],
            confidence: 0.0,
        }
    }
}
fn selected_text(value: &Value, fallback: &str) -> String {
    if truth(value) {
        text_format::value_text(value, QuotedRepr)
    } else {
        fallback.into()
    }
}
fn strings(value: &Value) -> Result<Vec<String>> {
    if !truth(value) {
        return Ok(vec![]);
    }
    Ok(match value {
        Value::Array(values) => values
            .iter()
            .filter(|v| truth(v))
            .map(|value| text_format::value_text(value, QuotedRepr))
            .collect(),
        Value::Object(values) => values.keys().filter(|v| !v.is_empty()).cloned().collect(),
        Value::String(value) => value.chars().map(|c| c.to_string()).collect(),
        _ => bail!("TypeError: context field is not iterable"),
    })
}
fn confidence(value: &Value) -> f64 {
    let number = match value {
        Value::Bool(b) => {
            if *b {
                1.0
            } else {
                0.0
            }
        }
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(s) => float_string(s).unwrap_or(0.0),
        _ => 0.0,
    };
    // Python max(0.0, min(1.0, value)) returns 1.0 for NaN.
    if number.is_nan() {
        1.0
    } else {
        number.clamp(0.0, 1.0)
    }
}
impl Irony {
    pub fn from_mapping(payload: &Value) -> Result<Self> {
        if !payload.is_object() {
            return Ok(Self::default());
        }
        let result = Self {
            found: truth(&payload["found"]),
            kind: selected_text(&payload["kind"], "none"),
            description: selected_text(&payload["description"], ""),
            elements: strings(&payload["elements"])?,
            focus: strings(&payload["focus"])?,
            confidence: confidence(&payload["confidence"]),
        };
        Ok(if result.found {
            result
        } else {
            Self::default()
        })
    }
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Scene {
    pub found: bool,
    pub clauses: Vec<PrefaceClause>,
    pub motifs: Vec<String>,
    pub focus: Vec<String>,
    pub confidence: f64,
}
impl Scene {
    pub fn from_mapping(payload: &Value, source_atoms: &[SourceAtom]) -> Result<Self> {
        if !payload.is_object() {
            return Ok(Self::default());
        }
        let mut raw = payload["clauses"].clone();
        let found = if raw.is_null() && payload["text"].is_string() {
            raw = json!([{"text":payload["text"],"basis_atom_ids":payload["basis_atom_ids"],"claim_class":payload["claim_class"]}]);
            true
        } else {
            truth(&payload["found"])
        };
        let clauses = preface_clauses_from_payload(&raw, source_atoms);
        let motifs = strings(&payload["motifs"])?;
        let focus = strings(&payload["focus"])?;
        let confidence = confidence(&payload["confidence"]);
        Ok(match clauses {
            Some(clauses) if found => Self {
                found,
                clauses,
                motifs,
                focus,
                confidence,
            },
            _ => Self::default(),
        })
    }
    pub fn spoken_text(&self) -> String {
        self.clauses
            .iter()
            .map(|c| c.text.as_str())
            .collect::<Vec<_>>()
            .join("。")
    }
}
/// Internal audit value: only the three details methods are LLM prompt projections.
/// Full serialization includes internal precipitation measurements.
#[derive(Clone, Debug, Serialize)]
pub struct Context {
    pub player_name: String,
    pub biome_id: String,
    pub biome_label: String,
    pub biome_group: String,
    pub biome_traits: Vec<String>,
    pub time_phase: String,
    pub time_label: String,
    pub weather: String,
    pub weather_label: String,
    pub precipitation_context: PrecipitationContext,
    pub poem_item_id: String,
    pub held_item: String,
    pub poem_item_source: String,
    pub inventory_items: Vec<String>,
    pub inventory_close_pair: Vec<String>,
    pub inventory_far_item: String,
    pub nearby_blocks: Vec<String>,
    pub dropped_items: Vec<String>,
    pub passive_mobs: Vec<String>,
    pub haiku_tags: Vec<String>,
    pub feature_candidates: Vec<Feature>,
    pub candidate_tensions: Vec<String>,
    pub catalog_notes: Vec<String>,
    pub catalog_sources: Vec<CatalogSourceSnapshot>,
    pub source_atoms: Vec<SourceAtom>,
    pub poetic_lines: Vec<String>,
    pub structure_id: String,
    pub structure_label: String,
    pub climate_hint: String,
    pub include_biome_context: bool,
    pub include_sky_context: bool,
}
impl Context {
    pub fn base_details(&self) -> Map<String, Value> {
        let mut details = json!({"player_name":self.player_name,"structure_id":self.structure_id,"structure_label":self.structure_label,
            "has_structure":!self.structure_label.is_empty()||!self.structure_id.is_empty(),
            "climate_hint":if self.include_biome_context {self.climate_hint.as_str()} else {""},
            "biome_context_visible":self.include_biome_context,"sky_context_visible":self.include_sky_context,
            "poem_item_id":self.poem_item_id,"held_item":self.held_item,"poem_item_source":self.poem_item_source,
            "inventory_items":self.inventory_items,"inventory_close_pair":self.inventory_close_pair,"inventory_far_item":self.inventory_far_item,
            "nearby_blocks":self.nearby_blocks,"dropped_items":self.dropped_items,"passive_mobs":self.passive_mobs,
            "haiku_tags":self.haiku_tags,"poetic_lines":self.poetic_lines,"feature_candidates":self.feature_candidates.iter().map(Feature::prompt_label).collect::<Vec<_>>(),
            "candidate_tensions":self.candidate_tensions,"catalog_notes":self.catalog_notes,
            "catalog_sources":self.catalog_sources.iter().map(CatalogSourceSnapshot::to_dict).collect::<Vec<_>>(),"source_atoms":self.source_atoms}).as_object().unwrap().clone();
        if self.include_biome_context {
            details.extend(json!({"biome":self.biome_label,"biome_id":self.biome_id,"biome_group":self.biome_group,"biome_traits":self.biome_traits}).as_object().unwrap().clone());
        }
        if self.include_sky_context {
            details.extend(json!({"time_phase":self.time_phase,"time_label":self.time_label,"weather":self.weather,"weather_label":self.weather_label}).as_object().unwrap().clone());
            details.extend(
                serde_json::to_value(self.precipitation_context.to_prompt_details())
                    .unwrap()
                    .as_object()
                    .unwrap()
                    .clone(),
            );
        }
        details
    }
    pub fn irony_details(&self) -> Map<String, Value> {
        self.base_details()
    }
    pub fn scene_details(&self, irony: Option<&Irony>) -> Map<String, Value> {
        let mut details = self.base_details();
        details.insert("irony".into(), irony.filter(|i| i.found).map_or(Value::Null, |i| json!({"kind":i.kind,"description":i.description,"elements":i.elements,"focus":i.focus,"confidence":i.confidence})));
        details
    }
    pub fn prompt_details(
        &self,
        irony: Option<&Irony>,
        scene: Option<&Scene>,
    ) -> Map<String, Value> {
        let mut details = self.scene_details(irony);
        details.insert("scene".into(), scene.filter(|s| s.found).map_or(Value::Null, |s| json!({"spoken_text":s.spoken_text(),"clauses":s.clauses,"motifs":s.motifs,"focus":s.focus,"confidence":s.confidence})));
        details
    }
}
pub fn material_match_score(label: &str, text: &str) -> usize {
    let compact = |s: &str| {
        s.chars()
            .filter(|c| {
                !crate::knowledge::query::space(*c)
                    && !"、。，．・…「」『』（）()！？!?".contains(*c)
            })
            .collect::<String>()
    };
    let needle = compact(label);
    let haystack = compact(text);
    let nc: Vec<_> = needle.chars().collect();
    let hn = haystack.chars().count();
    if nc.len() < 2 || hn < 2 {
        return 0;
    }
    if needle == haystack {
        return 400 + nc.len();
    }
    if needle.contains(&haystack) || haystack.contains(&needle) {
        return 300 + nc.len().min(hn);
    }
    let mut longest = 0;
    for left in 0..nc.len() {
        for right in left + 2..=nc.len() {
            if haystack.contains(&nc[left..right].iter().collect::<String>()) {
                longest = longest.max(right - left);
            }
        }
    }
    if longest < 2 || longest * 2 < nc.len().min(hn) {
        0
    } else {
        100 + longest
    }
}
pub fn irony_basis_atom_ids(irony: &Irony, atoms: &[SourceAtom]) -> Vec<String> {
    let primary: Vec<_> = atoms
        .iter()
        .filter(|a| {
            a.basis_atom_ids.is_empty()
                && matches!(a.kind.as_str(), "catalog_label" | "observation")
        })
        .collect();
    let mut selected = vec![];
    let mut cues = vec![];
    for cue in irony.elements.iter().chain(&irony.focus) {
        if !cues.contains(cue) {
            cues.push(cue.clone());
        }
    }
    for cue in cues {
        let scored: Vec<_> = primary
            .iter()
            .map(|a| (material_match_score(&a.text, &cue), a.atom_id.as_str()))
            .collect();
        let best = scored.iter().map(|r| r.0).max().unwrap_or(0);
        let best_ids: Vec<_> = scored
            .iter()
            .filter(|r| r.0 == best && best > 0)
            .map(|r| r.1)
            .collect();
        if best_ids.len() == 1 && !selected.iter().any(|s| s == best_ids[0]) {
            selected.push(best_ids[0].to_owned());
        }
        if selected.len() >= 4 {
            return selected;
        }
    }
    let mut scores: Vec<_> = primary
        .iter()
        .filter(|a| !selected.contains(&a.atom_id))
        .map(|a| {
            (
                material_match_score(&a.text, &irony.description),
                a.atom_id.clone(),
            )
        })
        .collect();
    scores.sort();
    for (score, id) in scores.into_iter().rev() {
        if score == 0 {
            break;
        }
        selected.push(id);
        if selected.len() >= 4 {
            break;
        }
    }
    selected
}
pub fn scene_for_spoken_irony(irony: &Irony, scene: &Scene, atoms: &[SourceAtom]) -> Scene {
    let core = if irony.found {
        strip(&irony.description)
    } else {
        ""
    };
    if core.is_empty() {
        return scene.clone();
    }
    let mut basis = irony_basis_atom_ids(irony, atoms);
    if scene.found {
        for clause in &scene.clauses {
            for id in &clause.basis_atom_ids {
                if !id.is_empty() && !basis.contains(id) {
                    basis.push(id.clone());
                }
            }
        }
    }
    basis.truncate(4);
    if basis.is_empty() {
        return scene.clone();
    }
    Scene {
        found: true,
        clauses: vec![PrefaceClause {
            text: core.into(),
            basis_atom_ids: basis,
            claim_class: "interpretive".into(),
            claim_scopes: vec!["poetic_interpretation".into()],
        }],
        motifs: if scene.motifs.is_empty() {
            irony.elements.clone()
        } else {
            scene.motifs.clone()
        },
        focus: if scene.focus.is_empty() {
            irony.focus.clone()
        } else {
            scene.focus.clone()
        },
        confidence: scene.confidence.max(irony.confidence),
    }
}

// Python domain names retained as public aliases for integration.
pub type HaikuContext = Context;
pub type HaikuFeature = Feature;
pub type IronyContext = Irony;
pub type SceneContext = Scene;
