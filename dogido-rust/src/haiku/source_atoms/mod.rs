//! Pure snapshots of the sources used by one poem. Never re-query current
//! catalogs when loading saved material; callers own selection and observation.
use crate::text_format::{self, ContainerFormat::QuotedRepr};
mod generation;
mod stored;
#[cfg(test)]
mod tests;
use super::SourceAtom;
use crate::chat_catalog::{strip, truth};
pub use generation::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashSet;
pub use stored::{line_source_ids_from_materials, source_atoms_from_materials};

const FACTUAL_SCOPES: [&str; 4] = [
    "identity_only",
    "source_meaning",
    "observed_state",
    "player_reported_context",
];
use crate::compat::is_dogido_whitespace as space;
fn compact(s: &str) -> String {
    s.chars().filter(|c| !space(*c)).collect()
}
fn value_text(v: &Value) -> String {
    if truth(v) {
        text_format::value_text(v, QuotedRepr)
    } else {
        String::new()
    }
}
fn field(v: &Value, k: &str) -> String {
    strip(&value_text(&v[k])).into()
}
fn scopes(bases: &[&SourceAtom]) -> Vec<String> {
    FACTUAL_SCOPES
        .into_iter()
        .filter(|scope| {
            bases
                .iter()
                .any(|a| a.claim_scopes.iter().any(|v| v == scope))
        })
        .map(str::to_owned)
        .collect()
}
fn unique<T: Eq + std::hash::Hash>(values: &[T]) -> bool {
    values.iter().collect::<HashSet<_>>().len() == values.len()
}
fn primary_valid(atom: &SourceAtom) -> bool {
    let expected = match atom.kind.as_str() {
        "catalog_label" => ("factual", "identity_only"),
        "catalog_fact" => ("factual", "source_meaning"),
        "catalog_field" => ("interpretive", "source_meaning"),
        "observation" => ("factual", "observed_state"),
        "dialogue_material" => ("factual", "player_reported_context"),
        _ => return false,
    };
    atom.claim_class == expected.0 && atom.claim_scopes == [expected.1]
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CatalogSourceSnapshot {
    pub catalog_type: String,
    pub catalog_id: String,
    pub label: String,
    #[serde(default)]
    pub note_raw: String,
    #[serde(default)]
    pub reading: String,
    #[serde(default)]
    pub observation_role: String,
    #[serde(default)]
    pub extra_fields: Vec<(String, String)>,
}
impl CatalogSourceSnapshot {
    pub fn source_ref(&self) -> String {
        format!("{}:{}", self.catalog_type, self.catalog_id)
    }
    pub fn to_dict(&self) -> Value {
        json!({"source_ref":self.source_ref(),"catalog_type":self.catalog_type,"catalog_id":self.catalog_id,"label":self.label,
        "reading":if self.reading.is_empty(){None}else{Some(&self.reading)},"note_raw":self.note_raw,"observation_role":self.observation_role,
        "extra_fields":self.extra_fields.iter().map(|(path,text)|json!({"field_path":path,"text":text})).collect::<Vec<_>>()})
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PrefaceClause {
    pub text: String,
    pub basis_atom_ids: Vec<String>,
    pub claim_class: String,
    pub claim_scopes: Vec<String>,
}

impl PrefaceClause {
    pub fn to_dict(&self) -> Value {
        json!({"text": self.text, "basis_atom_ids": self.basis_atom_ids,
            "claim_class": self.claim_class, "claim_scopes": self.claim_scopes})
    }
}
