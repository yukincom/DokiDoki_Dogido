use super::*;
use sha2::{Digest, Sha256};
fn clean(text: &str, limit: usize) -> String {
    let s = text
        .split(crate::knowledge::query::space)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    if s.chars().count() <= limit {
        s
    } else {
        format!("{}…", take(&s, limit.saturating_sub(1)))
    }
}
pub fn dialogue_material(turns: &[Value]) -> Result<Value> {
    let mut completed: Vec<(String, String)> = vec![];
    for turn in turns {
        ensure!(turn.is_object(), "completed turn must be an object");
        let fields =
            ["turn_id", "player_text", "dogido_text"].map(|k| turn.get(k).unwrap_or(&Value::Null));
        let mut strings = vec![];
        for (key, value) in ["turn_id", "player_text", "dogido_text"].iter().zip(fields) {
            let s = if !turn.as_object().unwrap().contains_key(*key) {
                ""
            } else {
                value
                    .as_str()
                    .context("completed turn field must be a string")?
            };
            strings.push(clean(s, 160));
        }
        if strings.iter().any(|s| s.is_empty()) {
            continue;
        }
        completed.retain(|(id, _)| id != &strings[0]);
        completed.push((strings[0].clone(), strings[1].clone()));
        if completed.len() > 5 {
            completed.remove(0);
        }
    }
    let turns = &completed[completed.len().saturating_sub(3)..];
    let phrases: Vec<_> = turns
        .iter()
        .map(|(_, s)| clean(s, 12))
        .filter(|s| !s.is_empty())
        .collect();
    if phrases.is_empty() {
        return Ok(json!({}));
    }
    let summary = clean(
        &format!(
            "プレイヤーと{}について話していた",
            phrases
                .iter()
                .map(|s| format!("『{s}』"))
                .collect::<Vec<_>>()
                .join("、")
        ),
        80,
    );
    let mut motifs = vec![];
    for phrase in phrases.iter().rev() {
        let mut candidates: Vec<_> = phrase
            .split(|c| {
                ['、', '。', '！', '？', '!', '?'].contains(&c) || crate::knowledge::query::space(c)
            })
            .map(|s| {
                clean(
                    s.trim_matches(['『', '』', '「', '」', '（', '）', '(', ')']),
                    16,
                )
            })
            .filter(|s| s.chars().count() >= 2)
            .collect();
        if candidates.is_empty() {
            candidates.push(clean(phrase, 16));
        }
        for candidate in candidates {
            if !motifs.contains(&candidate) {
                motifs.push(candidate);
            }
            if motifs.len() == 3 {
                break;
            }
        }
        if motifs.len() == 3 {
            break;
        }
    }
    Ok(
        json!({"summary":summary,"motifs":motifs,"source_turn_ids":turns.iter().map(|(id,_)|id).collect::<Vec<_>>(),"attribution":"player_dialogue_soft_material"}),
    )
}
fn list(material: &Value, key: &str) -> Vec<String> {
    material[key]
        .as_array()
        .map(|a| a.iter().map(crate::chat_catalog::text).collect())
        .unwrap_or_default()
}
pub(super) fn conversation_atoms(material: &Value) -> Vec<SourceAtom> {
    let summary = take(strip(&value_text(material.get("summary"))), 80);
    if summary.is_empty() {
        return vec![];
    }
    let motifs = list(material, "motifs")
        .into_iter()
        .take(3)
        .filter(|s| !strip(s).is_empty())
        .map(|s| take(strip(&s), 16));
    let ids = list(material, "source_turn_ids");
    let ids: Vec<_> = ids[ids.len().saturating_sub(5)..]
        .iter()
        .map(|s| take(s, 160))
        .collect();
    let source_ref = format!("dialogue:{}", ids.join(","));
    let mut fields = vec![("summary".into(), summary)];
    fields.extend(motifs.enumerate().map(|(i, s)| (format!("motifs[{i}]"), s)));
    fields
        .into_iter()
        .map(|(field_path, text)| {
            let digest = format!(
                "{:x}",
                Sha256::digest(format!("{source_ref}|{field_path}|{text}"))
            );
            SourceAtom {
                atom_id: format!("dialogue:{}", &digest[..12]),
                text,
                source_ref: source_ref.clone(),
                field_path,
                observation_role: "player_dialogue_attributed".into(),
                kind: "dialogue_material".into(),
                claim_class: "factual".into(),
                claim_scopes: vec!["player_reported_context".into()],
                basis_atom_ids: vec![],
            }
        })
        .collect()
}
pub(super) fn attach_conversation(materials: &mut Map<String, Value>, dialogue: &Value) {
    if !crate::chat_catalog::truth(dialogue) {
        return;
    }
    let ids = list(dialogue, "source_turn_ids");
    materials.insert("player_dialogue_material".into(),json!({"summary":take(&value_text(dialogue.get("summary")),80),"motifs":list(dialogue,"motifs").iter().take(3).map(|s|take(s,16)).collect::<Vec<_>>(),"source_turn_ids":ids[ids.len().saturating_sub(5)..].iter().map(|s|take(s,160)).collect::<Vec<_>>(),"attribution":"player_dialogue_soft_material","constraint":"soft"}));
}
fn label(s: &str) -> String {
    strip(s)
        .trim_end_matches(['。', '．', '.'])
        .replace("プレイヤー", "あんた")
}
pub(super) fn seed(
    event: &GameEvent,
    context: &HaikuContext,
    irony: &Irony,
    scene: &Scene,
    interpretation: Option<&str>,
    atoms: &[SourceAtom],
    spoken: Option<&str>,
) -> Map<String, Value> {
    let mut out = Map::new();
    let interpretation = interpretation.map(str::to_owned).or_else(|| {
        if scene.found && !strip(&scene.spoken_text()).is_empty() {
            Some(scene.spoken_text())
        } else if irony.found && !strip(&irony.description).is_empty() {
            Some(irony.description.clone())
        } else {
            None
        }
    });
    if let Some(s) = interpretation.filter(|s| !strip(s).is_empty()) {
        out.insert("interpretation".into(), strip(&s).into());
    }
    if context.include_biome_context {
        let biome = normalized(event.world.biome.as_deref()).unwrap_or(context.biome_id.clone());
        if !biome.is_empty() {
            out.insert(
                "biome".into(),
                biome.strip_prefix("minecraft:").unwrap_or(&biome).into(),
            );
        }
    }
    let structure =
        normalized(event.world.structure.as_deref()).unwrap_or(context.structure_id.clone());
    if !structure.is_empty() {
        out.insert(
            "structure".into(),
            structure
                .strip_prefix("minecraft:")
                .unwrap_or(&structure)
                .into(),
        );
    }
    if context.include_sky_context && !context.time_phase.is_empty() {
        out.insert("time_phase".into(), context.time_phase.clone().into());
    }
    let mut motifs = vec![];
    for item in scene
        .motifs
        .iter()
        .filter(|_| scene.found)
        .chain(scene.focus.iter().filter(|_| scene.found))
        .chain(irony.focus.iter().filter(|_| irony.found))
        .chain(irony.elements.iter().filter(|_| irony.found))
    {
        let s = label(item);
        if !s.is_empty() && !motifs.contains(&s) {
            motifs.push(s);
        }
    }
    if !motifs.is_empty() {
        out.insert("motifs".into(), json!(motifs));
    }
    let held = label(&context.held_item);
    if !held.is_empty() && !matches!(held.as_str(), "なし" | "無し") {
        out.insert("held_item".into(), held.clone().into());
    }
    for (key, values) in [
        ("inventory_items", &context.inventory_items),
        ("nearby_blocks", &context.nearby_blocks),
        ("dropped_items", &context.dropped_items),
        ("passive_mobs", &context.passive_mobs),
    ] {
        let mut labels = vec![];
        for s in values {
            let s = label(s);
            if !s.is_empty() && !labels.contains(&s) && (key != "inventory_items" || s != held) {
                labels.push(s);
            }
        }
        if !labels.is_empty() {
            out.insert(key.into(), json!(labels));
        }
    }
    let world = crate::world_catalog::catalog();
    let catalog = crate::chat_catalog::catalog();
    for (key, ja, entries) in [
        ("biome", "biome_ja", world.biome_entries()),
        ("structure", "structure_ja", catalog.structure_entries()),
    ] {
        if let Some(id) = out.get(key).and_then(Value::as_str)
            && let Some(entry) = entries.get(id)
            && let Some(value) = entry.get("label").filter(|v| crate::chat_catalog::truth(v))
        {
            out.insert(ja.into(), crate::chat_catalog::text(value).into());
        }
    }
    out.insert(
        "material_visibility".into(),
        json!({"biome":context.include_biome_context,"sky":context.include_sky_context}),
    );
    for (key, value, visible) in [
        (
            "biome_ja",
            &context.biome_label,
            context.include_biome_context,
        ),
        ("structure_ja", &context.structure_label, true),
    ] {
        if visible && !value.is_empty() && !out.get(key).is_some_and(crate::chat_catalog::truth) {
            out.insert(key.into(), value.clone().into());
        }
    }
    out.insert(
        "catalog_sources".into(),
        json!(
            context
                .catalog_sources
                .iter()
                .map(|s| s.to_dict())
                .collect::<Vec<_>>()
        ),
    );
    out.insert("source_atoms".into(), json!(atoms));
    if let Some(s) = spoken.filter(|s| !s.is_empty()) {
        out.insert("preface_spoken".into(), s.into());
    }
    out
}
fn compact(s: &str) -> String {
    super::super::lexical::hiragana(s)
        .chars()
        .filter(|c| {
            ![
                '\n', ' ', '　', '、', '。', '？', '?', '！', '!', '「', '」', '『', '』', '・',
                '…',
            ]
            .contains(c)
        })
        .collect()
}
fn shorten(s: &str, limit: usize) -> String {
    let s = strip(s);
    if s.chars().count() <= limit {
        return s.into();
    }
    for sep in ['の', '、', '，', ' '] {
        if s.contains(sep) {
            let parts: Vec<_> = s.split(sep).collect();
            for p in parts.iter().rev() {
                let p = strip(p);
                if (2..=limit).contains(&p.chars().count()) {
                    return p.into();
                }
            }
        }
    }
    format!("{}…", take(s, limit - 1))
}
fn noun(s: &str) -> bool {
    let n = s.chars().count();
    if n < 2 {
        return false;
    }
    if !s.contains('\n')
        && ["ている", "でいる", "ていた", "である"]
            .iter()
            .any(|end| s.ends_with(end))
        && n <= 12
        && (!s.chars().any(|c| ('一'..='鿿').contains(&c)) || n <= 8)
    {
        return false;
    }
    !matches!(
        s,
        "いる" | "ただ" | "して" | "ある" | "する" | "なる" | "よう"
    )
}
fn short_entries(m: &Map<String, Value>) -> Vec<(String, String)> {
    let mut out = vec![];
    let mut add = |s: &str, source: &str, max| {
        let s = shorten(&label(s), max);
        if noun(&s) && !out.iter().any(|(v, _)| v == &s) {
            out.push((s, source.into()));
        }
    };
    for (key, source) in [
        ("motifs", "motif"),
        ("held_item", "held_item"),
        ("inventory_items", "inventory_item"),
        ("nearby_blocks", "nearby_block"),
        ("dropped_items", "dropped_item"),
        ("passive_mobs", "passive_mob"),
        ("biome_ja", "biome"),
        ("structure_ja", "structure"),
        ("place_ja", "place"),
    ] {
        if let Some(v) = m.get(key) {
            if let Some(rows) = v.as_array() {
                for row in rows {
                    add(&crate::chat_catalog::text(row), source, 18);
                }
            } else {
                add(&crate::chat_catalog::text(v), source, 18);
            }
        }
    }
    let phase = match m.get("time_phase").and_then(Value::as_str) {
        Some("morning") => "朝",
        Some("day") => "昼",
        Some("evening") => "夕方",
        Some("night") => "夜",
        _ => "",
    };
    add(phase, "time_phase", 18);
    if let Some(s) = m.get("interpretation").and_then(Value::as_str) {
        for chunk in s.split(['、', '，', '。', '・', '/', '／', 'と']) {
            if strip(chunk).chars().count() >= 2 {
                add(strip(chunk), "interpretation", 16);
            }
        }
    }
    out
}
pub(super) fn attach_links(materials: &mut Map<String, Value>, verse: &str) {
    let verse_h = compact(strip(verse));
    if verse_h.chars().count() < 2 {
        return;
    }
    let phrases: Vec<_> = verse
        .split(crate::knowledge::query::space)
        .filter(|s| !s.is_empty())
        .collect();
    let mut links = vec![];
    let mut seen = std::collections::HashSet::new();
    for (label, source) in short_entries(materials) {
        let mh = compact(&label);
        let chars: Vec<_> = mh.chars().collect();
        if chars.len() < 2 {
            continue;
        }
        let mut needle = None;
        if verse_h.contains(&mh) {
            needle = Some(mh.clone());
        } else {
            'outer: for n in (2..=chars.len().min(6)).rev() {
                for slice in chars.windows(n) {
                    let bit: String = slice.iter().collect();
                    if verse_h.contains(&bit) {
                        needle = Some(bit);
                        break 'outer;
                    }
                }
            }
        }
        let Some(needle) = needle else {
            continue;
        };
        let mut best = None;
        let mut score = 0;
        for phrase in &phrases {
            let ph = compact(phrase);
            if !ph.is_empty()
                && (ph.contains(&needle) || needle.contains(&ph))
                && ph.chars().count() > score
            {
                score = ph.chars().count();
                best = Some(*phrase);
            }
        }
        let surface = best
            .map(str::to_owned)
            .unwrap_or_else(|| if needle == mh { label.clone() } else { needle });
        if seen.insert((compact(&surface), mh)) {
            links.push(json!({"surface":surface,"material":label,"source":source}));
        }
    }
    if !links.is_empty() {
        materials.insert("fragment_links".into(), links.into());
    }
}
