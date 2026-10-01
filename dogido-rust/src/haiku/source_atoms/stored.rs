use super::*;
use std::collections::{BTreeMap, HashMap};

pub fn source_atoms_from_materials(materials: &Value) -> Vec<SourceAtom> {
    let Some(rows) = materials.get("source_atoms").and_then(Value::as_array) else {
        return vec![];
    };
    let mut atoms = vec![];
    let mut seen = HashSet::new();
    for row in rows {
        if !row.is_object() {
            continue;
        }
        let id = field(row, "atom_id");
        let atom_text = field(row, "text");
        let reference = field(row, "source_ref");
        let class = field(row, "claim_class");
        let Some(raw_scopes) = row["claim_scopes"].as_array() else {
            continue;
        };
        let Some(raw_basis) = row["basis_atom_ids"].as_array() else {
            continue;
        };
        let valid_scopes = raw_scopes.iter().all(|v| {
            v.as_str()
                .is_some_and(|s| FACTUAL_SCOPES.contains(&s) || s == "poetic_interpretation")
        });
        let valid_basis = raw_basis
            .iter()
            .all(|v| v.as_str().is_some_and(|s| !strip(s).is_empty()));
        if id.is_empty()
            || seen.contains(&id)
            || atom_text.is_empty()
            || reference.is_empty()
            || !matches!(class.as_str(), "factual" | "interpretive")
            || raw_scopes.is_empty()
            || !valid_scopes
            || !valid_basis
        {
            continue;
        }
        let claim_scopes = raw_scopes
            .iter()
            .map(|v| v.as_str().unwrap().to_owned())
            .collect::<Vec<_>>();
        let basis = raw_basis
            .iter()
            .map(|v| v.as_str().unwrap().to_owned())
            .collect::<Vec<_>>();
        if !unique(&claim_scopes) || !unique(&basis) {
            continue;
        }
        // The first structurally valid ID wins even if the second-pass claim
        // check drops that row. This matches the saved-data reader's ordering.
        seen.insert(id.clone());
        atoms.push(SourceAtom {
            atom_id: id,
            text: atom_text,
            source_ref: reference,
            field_path: field(row, "field_path"),
            observation_role: field(row, "observation_role"),
            kind: field(row, "kind"),
            claim_class: class,
            claim_scopes,
            basis_atom_ids: basis.iter().map(|s| strip(s).into()).collect(),
        });
    }
    let by_id = atoms
        .iter()
        .map(|a| (a.atom_id.as_str(), a))
        .collect::<HashMap<_, _>>();
    atoms
        .iter()
        .filter(|atom| {
            if matches!(
                atom.kind.as_str(),
                "preface_clause" | "poetic_interpretation"
            ) {
                let bases = atom
                    .basis_atom_ids
                    .iter()
                    .map(|id| by_id.get(id.as_str()).copied())
                    .collect::<Option<Vec<_>>>();
                let Some(bases) = bases.filter(|v| !v.is_empty()) else {
                    return false;
                };
                let expected_scopes = if atom.claim_class == "factual" {
                    scopes(&bases)
                } else {
                    vec!["poetic_interpretation".into()]
                };
                if bases
                    .iter()
                    .any(|base| !base.basis_atom_ids.is_empty() || !primary_valid(base))
                {
                    return false;
                }
                if atom.kind == "poetic_interpretation"
                    && (atom.atom_id != "preface:spoken:interpretation"
                        || atom.claim_class != "interpretive"
                        || atom.source_ref != "preface:spoken"
                        || atom.field_path != "interpretation"
                        || atom.observation_role != "poetic_interpretation")
                {
                    return false;
                }
                !(atom.claim_class == "factual"
                    && bases.iter().any(|base| base.claim_class != "factual"))
                    && atom.claim_scopes == expected_scopes
            } else {
                atom.basis_atom_ids.is_empty() && primary_valid(atom)
            }
        })
        .cloned()
        .collect()
}

pub fn line_source_ids_from_materials(
    materials: &Value,
    verse_lines: &[String],
    allowed_atom_ids: &HashSet<String>,
) -> BTreeMap<usize, Vec<String>> {
    let Some(rows) = materials.get("line_sources").and_then(Value::as_array) else {
        return BTreeMap::new();
    };
    let repeatable = source_atoms_from_materials(materials)
        .into_iter()
        .filter(|a| a.kind == "poetic_interpretation")
        .map(|a| a.atom_id)
        .collect::<HashSet<_>>();
    let mut result = BTreeMap::new();
    let mut used = HashSet::new();
    for row in rows {
        let Some(index) = row
            .get("line_index")
            .and_then(Value::as_u64)
            .and_then(|i| usize::try_from(i).ok())
        else {
            continue;
        };
        let Some(ids) = row
            .get("atom_ids")
            .and_then(Value::as_array)
            .filter(|v| !v.is_empty())
        else {
            continue;
        };
        if index >= verse_lines.len()
            || result.contains_key(&index)
            || field(row, "text") != verse_lines[index]
        {
            continue;
        }
        let ids = ids
            .iter()
            .map(|v| strip(&text(v)).to_owned())
            .collect::<Vec<_>>();
        let reserved = ids
            .iter()
            .filter(|id| !repeatable.contains(*id))
            .cloned()
            .collect::<HashSet<_>>();
        if ids
            .iter()
            .any(|id| id.is_empty() || !allowed_atom_ids.contains(id))
            || !unique(&ids)
            || !used.is_disjoint(&reserved)
        {
            continue;
        }
        used.extend(reserved);
        result.insert(index, ids);
    }
    result
}
