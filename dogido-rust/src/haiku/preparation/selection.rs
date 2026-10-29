//! 前置きで取り上げた元材料と、その中から句へ渡す材料を受け取る。
//! 同じ出典の名称・説明は一材料。モデルが返した参照の存在と部分集合だけを確認し、
//! 意味の一致は判定しない。前置きの材料が1〜3個なら全件を句へ渡す。
use super::*;
use std::collections::HashSet;

#[derive(Clone, Debug, Serialize)]
pub(super) struct Material {
    pub material_id: String,
    pub source_ref: String,
    pub atoms: Vec<SourceAtom>,
}

/// 観測・カタログの一次資料だけを、元の順序のまま出典ごとにまとめる。
pub(super) fn candidates(atoms: &[SourceAtom]) -> Vec<Material> {
    let mut groups: Vec<Material> = vec![];
    for atom in atoms.iter().filter(|a| {
        a.basis_atom_ids.is_empty()
            && !matches!(a.kind.as_str(), "preface_clause" | "poetic_interpretation")
    }) {
        let reference = if atom.source_ref.is_empty() {
            &atom.atom_id
        } else {
            &atom.source_ref
        };
        if let Some(group) = groups.iter_mut().find(|g| &g.source_ref == reference) {
            group.atoms.push(atom.clone());
        } else {
            groups.push(Material {
                material_id: format!("m{:02}", groups.len() + 1),
                source_ref: reference.clone(),
                atoms: vec![atom.clone()],
            });
        }
    }
    groups
}

pub(super) fn prompt_rows(groups: &[Material]) -> Value {
    json!(
        groups
            .iter()
            .map(|g| json!({
                "material_id":g.material_id,
                "facts":g.atoms.iter().map(|a| json!({"text":a.text,
                    "claim_class":a.claim_class,"claim_scopes":a.claim_scopes})).collect::<Vec<_>>()
            }))
            .collect::<Vec<_>>()
    )
}

#[derive(Clone, Debug, Serialize)]
pub(super) struct Selection {
    pub preface_materials: Vec<Material>,
    pub poem_materials: Vec<Material>,
}

fn resolve(value: &Value, groups: &[Material]) -> Result<Vec<Material>> {
    let ids = value.as_array().context("material IDs must be an array")?;
    let mut seen = HashSet::new();
    let mut selected = vec![];
    for id in ids {
        let id = id.as_str().context("material ID must be a string")?;
        let material = groups
            .iter()
            .find(|g| g.material_id == id)
            .context("material ID is not in the offered candidates")?;
        if seen.insert(id) {
            selected.push(material.clone());
        }
    }
    ensure!(!selected.is_empty(), "no preface materials selected");
    Ok(selected)
}

impl Selection {
    /// 1〜3材料はモデルの絞込み項目を使わず全件を保持。4個以上のときだけ部分集合を読む。
    pub(super) fn from_payload(payload: &Value, atoms: &[SourceAtom]) -> Result<Self> {
        let offered = candidates(atoms);
        let preface_materials = resolve(&payload["preface_material_ids"], &offered)?;
        let poem_materials = if preface_materials.len() < 4 {
            preface_materials.clone()
        } else {
            resolve(&payload["poem_material_ids"], &preface_materials)?
        };
        Ok(Self {
            preface_materials,
            poem_materials,
        })
    }

    pub(super) fn atoms(&self) -> Vec<SourceAtom> {
        self.poem_materials
            .iter()
            .flat_map(|g| g.atoms.clone())
            .collect()
    }
}
