use super::*;
use crate::text_format::{self, ContainerFormat::QuotedRepr};
use std::collections::HashMap;

pub fn catalog_source_snapshot(
    catalog_type: &str,
    catalog_id: &str,
    entry: &Value,
    observation_role: &str,
    fallback_label: &str,
) -> Option<CatalogSourceSnapshot> {
    let id = strip(catalog_id.strip_prefix("minecraft:").unwrap_or(catalog_id));
    if id.is_empty() {
        return None;
    }
    let label = [&entry["japanese"], &entry["label"]]
        .into_iter()
        .find(|v| truth(v))
        .map(|value| text_format::value_text(value, QuotedRepr))
        .unwrap_or_else(|| {
            if !fallback_label.is_empty() {
                fallback_label.into()
            } else {
                id.into()
            }
        });
    let label = strip(&label);
    if label.is_empty() {
        return None;
    }
    let mut extra_fields = vec![];
    if let Some(poetic) = entry.get("poetic").filter(|v| v.is_object()) {
        let role = field(poetic, "role");
        if !role.is_empty() {
            extra_fields.push(("poetic.role".into(), role))
        }
        for key in [
            "visual_tags",
            "sound_tags",
            "motion_tags",
            "comic_tags",
            "scene_tags",
            "reaction_tags",
        ] {
            if let Some(values) = poetic[key].as_array() {
                for (i, value) in values.iter().enumerate() {
                    let value = text_format::value_text(value, QuotedRepr);
                    let value = strip(&value);
                    if !value.is_empty() {
                        extra_fields.push((format!("poetic.{key}[{i}]"), value.into()))
                    }
                }
            }
        }
    }
    Some(CatalogSourceSnapshot {
        catalog_type: strip(catalog_type).into(),
        catalog_id: id.into(),
        label: label.into(),
        note_raw: field(entry, "note"),
        reading: field(entry, "reading"),
        observation_role: strip(observation_role).into(),
        extra_fields,
    })
}

pub fn split_note_sentences(raw: &str) -> Vec<String> {
    let text = strip(raw);
    let mut out = vec![];
    let mut start = 0;
    for (i, c) in text.char_indices() {
        if "。！？!?".contains(c) {
            let end = i + c.len_utf8();
            let part = strip(&text[start..end]);
            if !part.is_empty() {
                out.push(part.into())
            }
            start = end;
        }
    }
    let tail = strip(&text[start..]);
    if !tail.is_empty() {
        out.push(tail.into())
    }
    out
}
fn safe_path(text: &str, separator: char) -> String {
    let mut s = String::new();
    let mut rejected = false;
    for c in text.chars() {
        if c.is_ascii_alphanumeric() || c == '_' {
            if rejected {
                s.push(separator);
                rejected = false;
            }
            s.push(c)
        } else {
            rejected = true
        }
    }
    if rejected {
        s.push(separator)
    }
    s.trim_matches(separator).to_ascii_lowercase()
}
fn atom(
    id: String,
    text: String,
    source: String,
    path: String,
    role: String,
    claim: (&str, &str, &str),
) -> SourceAtom {
    let (kind, class, scope) = claim;
    SourceAtom {
        atom_id: id,
        text,
        source_ref: source,
        field_path: path,
        observation_role: role,
        kind: kind.into(),
        claim_class: class.into(),
        claim_scopes: vec![scope.into()],
        basis_atom_ids: vec![],
    }
}

pub fn atoms_from_catalog_sources(
    sources: &[CatalogSourceSnapshot],
    max_note_atoms: i64,
    max_extra_atoms_per_source: i64,
) -> Vec<SourceAtom> {
    let mut atoms = vec![];
    let mut seen = HashSet::new();
    let mut note_count = 0;
    for source in sources {
        let reference = source.source_ref();
        let label_id = format!("{reference}:japanese");
        if seen.insert(label_id.clone()) {
            atoms.push(atom(
                label_id,
                source.label.clone(),
                reference.clone(),
                "japanese".into(),
                source.observation_role.clone(),
                ("catalog_label", "factual", "identity_only"),
            ))
        }
        for (i, sentence) in split_note_sentences(&source.note_raw)
            .into_iter()
            .enumerate()
        {
            if note_count >= max_note_atoms {
                break;
            }
            let id = format!("{reference}:note:{i}");
            if !seen.insert(id.clone()) {
                continue;
            }
            note_count += 1;
            atoms.push(atom(
                id,
                sentence,
                reference.clone(),
                format!("note[{i}]"),
                source.observation_role.clone(),
                ("catalog_fact", "factual", "source_meaning"),
            ));
        }
        let len = source.extra_fields.len();
        let end = if max_extra_atoms_per_source < 0 {
            len.saturating_sub(max_extra_atoms_per_source.unsigned_abs() as usize)
        } else {
            len.min(max_extra_atoms_per_source as usize)
        };
        for (path, text) in &source.extra_fields[..end] {
            let id = format!("{reference}:{}", safe_path(path, ':'));
            if !seen.insert(id.clone()) {
                continue;
            }
            atoms.push(atom(
                id,
                text.clone(),
                reference.clone(),
                path.clone(),
                source.observation_role.clone(),
                ("catalog_field", "interpretive", "source_meaning"),
            ));
        }
    }
    atoms
}
/// featureのsource・key・labelを、観測ラベルを表す出典atomへ投影する。
/// sourceが未指定/空ならobservation、key/labelが空またはlabelが不明なら材料から除外する。
pub fn atoms_from_observations(features: &[Value]) -> Vec<SourceAtom> {
    features
        .iter()
        .filter_map(|f| {
            let source = f
                .get("source")
                .filter(|v| truth(v))
                .map(|value| text_format::value_text(value, QuotedRepr))
                .unwrap_or_else(|| "observation".into());
            let key = field(f, "key");
            let label = field(f, "label");
            if key.is_empty() || label.is_empty() || label == "不明" {
                return None;
            }
            let safe = safe_path(strip(&source), '_');
            let safe = if safe.is_empty() { "observed" } else { &safe };
            Some(atom(
                format!("observation:{safe}:{key}"),
                label,
                format!("observation:{key}"),
                "observed_label".into(),
                key,
                ("observation", "factual", "observed_state"),
            ))
        })
        .collect()
}
fn single_clause(value: &Value) -> String {
    let Some(s) = value.as_str() else {
        return String::new();
    };
    let s = strip(s).trim_matches(|c| "「」\"' 。．.!！?？…".contains(c));
    let n = s.chars().count();
    if !(2..=36).contains(&n)
        || s.contains(['\n', '\r'])
        || ["プレイヤー", "Y座標", "ｙ座標", "%", "％", "確率"]
            .iter()
            .any(|t| s.contains(t))
    {
        String::new()
    } else {
        s.into()
    }
}
pub fn preface_clauses_from_payload(
    raw: &Value,
    source_atoms: &[SourceAtom],
) -> Option<Vec<PrefaceClause>> {
    let rows = raw.as_array().filter(|v| (1..=3).contains(&v.len()))?;
    let by_id = source_atoms
        .iter()
        .filter(|a| a.basis_atom_ids.is_empty())
        .map(|a| (a.atom_id.as_str(), a))
        .collect::<HashMap<_, _>>();
    if by_id.is_empty() {
        return None;
    }
    let mut clauses = vec![];
    let mut seen = HashSet::new();
    let mut total = 0;
    for row in rows {
        if !row.is_object() {
            return None;
        }
        let text = single_clause(&row["text"]);
        let ids = row["basis_atom_ids"]
            .as_array()
            .filter(|v| (1..=4).contains(&v.len()))?;
        let class = field(row, "claim_class");
        if text.is_empty() || !matches!(class.as_str(), "factual" | "interpretive") {
            return None;
        }
        let ids = ids
            .iter()
            .map(|v| strip(&text_format::value_text(v, QuotedRepr)).to_owned())
            .collect::<Vec<_>>();
        if ids.iter().any(|id| id.is_empty()) || !unique(&ids) {
            return None;
        }
        let bases = ids
            .iter()
            .map(|id| by_id.get(id.as_str()).copied())
            .collect::<Option<Vec<_>>>()?;
        if !seen.insert(compact(&text)) {
            return None;
        }
        total += text.chars().count();
        if total > 72 {
            return None;
        }
        let claim_scopes = if class == "factual" {
            if bases.iter().any(|a| a.claim_class != "factual") {
                return None;
            }
            scopes(&bases)
        } else {
            vec!["poetic_interpretation".into()]
        };
        if claim_scopes.is_empty() {
            return None;
        }
        clauses.push(PrefaceClause {
            text,
            basis_atom_ids: ids,
            claim_class: class,
            claim_scopes,
        });
    }
    Some(clauses)
}
pub fn atoms_from_preface_clauses(clauses: &[PrefaceClause]) -> Vec<SourceAtom> {
    clauses
        .iter()
        .enumerate()
        .map(|(i, c)| SourceAtom {
            atom_id: format!("preface:spoken:clause:{i}"),
            text: c.text.clone(),
            source_ref: "preface:spoken".into(),
            field_path: format!("clause[{i}]"),
            observation_role: "spoken_preface".into(),
            kind: "preface_clause".into(),
            claim_class: c.claim_class.clone(),
            claim_scopes: c.claim_scopes.clone(),
            basis_atom_ids: c.basis_atom_ids.clone(),
        })
        .collect()
}
pub fn atom_from_poetic_interpretation(clauses: &[PrefaceClause]) -> Option<SourceAtom> {
    let text = clauses
        .iter()
        .filter(|c| !strip(&c.text).is_empty())
        .map(|c| c.text.as_str())
        .collect::<Vec<_>>()
        .join("。");
    let text = strip(&text);
    let mut seen = HashSet::new();
    let ids = clauses
        .iter()
        .flat_map(|c| &c.basis_atom_ids)
        .filter(|id| !id.is_empty() && seen.insert((*id).clone()))
        .cloned()
        .collect::<Vec<_>>();
    if text.is_empty() || ids.is_empty() {
        return None;
    }
    Some(SourceAtom {
        atom_id: "preface:spoken:interpretation".into(),
        text: text.into(),
        source_ref: "preface:spoken".into(),
        field_path: "interpretation".into(),
        observation_role: "poetic_interpretation".into(),
        kind: "poetic_interpretation".into(),
        claim_class: "interpretive".into(),
        claim_scopes: vec!["poetic_interpretation".into()],
        basis_atom_ids: ids,
    })
}
pub fn merge_source_atoms(groups: &[Vec<SourceAtom>]) -> Vec<SourceAtom> {
    let mut ids = HashSet::new();
    let mut texts = HashSet::new();
    let mut merged = vec![];
    for atom in groups.iter().flatten() {
        let normalized = compact(&atom.text);
        if ids.contains(&atom.atom_id)
            || (atom.basis_atom_ids.is_empty() && texts.contains(&normalized))
        {
            continue;
        }
        ids.insert(atom.atom_id.clone());
        texts.insert(normalized);
        merged.push(atom.clone());
    }
    merged
}
pub fn catalog_notes_projection(sources: &[CatalogSourceSnapshot]) -> Vec<String> {
    sources
        .iter()
        .filter(|s| !s.note_raw.is_empty())
        .map(|source| {
            let note = if source.note_raw.chars().count() > 100 {
                format!(
                    "{}…",
                    source
                        .note_raw
                        .chars()
                        .take(99)
                        .collect::<String>()
                        .trim_end_matches(space)
                )
            } else {
                source.note_raw.clone()
            };
            format!("{}: {note}", source.label)
        })
        .collect()
}
