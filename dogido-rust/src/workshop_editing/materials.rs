use super::*;
use crate::entry_catalog::BIOMES;
use crate::haiku::source_atoms;
use std::collections::{BTreeSet, HashMap};
fn len(s: &str) -> usize {
    s.chars().count()
}
fn overlap(line: &str, probe: &str, min: usize) -> usize {
    if !line.is_empty() && (probe.contains(line) || line.contains(probe)) {
        return len(line);
    }
    let chars = line.chars().collect::<Vec<_>>();
    for width in (min..=chars.len().min(len(probe))).rev() {
        if chars
            .windows(width)
            .any(|w| probe.contains(&w.iter().collect::<String>()))
        {
            return width;
        }
    }
    0
}
fn string(v: &Value) -> String {
    if truth(v) {
        crate::chat_catalog::text(v)
    } else {
        String::new()
    }
}
fn unique_best(rows: &[(usize, usize, String)]) -> Option<String> {
    let best = rows.iter().map(|(s, k, _)| (*s, *k)).max()?;
    let mut labels = vec![];
    for (s, k, t) in rows {
        if (*s, *k) == best && !labels.contains(t) {
            labels.push(t.clone())
        }
    }
    (labels.len() == 1).then(|| labels.remove(0))
}
impl Engine {
    fn source_score(&self, source: &str, question: &str) -> Result<usize> {
        let c = compact(source);
        let q = compact(question);
        if c.is_empty() || q.is_empty() {
            return Ok(0);
        }
        if len(&c) >= 2 && q.contains(&c) {
            return Ok(200 + len(&c));
        }
        let reading = compact(&self.read(question)?);
        let mut best = 0;
        for m in parse::pattern("source_term").find_iter(source) {
            let term = m.as_str();
            let t = compact(term);
            if !t.is_empty() && q.contains(&t) {
                best = best.max(100 + len(&t))
            }
            let tr = compact(&self.read(term)?);
            if len(&tr) >= 2 && reading.contains(&tr) {
                best = best.max(50 + len(&tr))
            }
        }
        Ok(best)
    }
    pub(super) fn material_for_question(
        &self,
        s: &Snapshot,
        question: &str,
    ) -> Result<Option<String>> {
        let records = editing_records(s);
        let probe = compact(&self.read(question)?);
        let mut matched = vec![];
        for line in records {
            let reading = compact(&line.reading_text);
            let surface = compact(&line.surface_text);
            let score = if !reading.is_empty()
                && (probe.contains(&reading) || reading.contains(&probe))
            {
                len(&reading)
            } else if !surface.is_empty() && (probe.contains(&surface) || surface.contains(&probe))
            {
                len(&surface)
            } else {
                overlap(&reading, &probe, 3)
            };
            if score >= 3 {
                matched.push((score, line.line_index))
            }
        }
        if let Some(best) = matched.iter().map(|r| r.0).max() {
            let indices = matched
                .iter()
                .filter_map(|(s, i)| (*s == best).then_some(*i))
                .collect::<BTreeSet<_>>();
            if indices.len() == 1 {
                let line = records
                    .get(*indices.first().unwrap())
                    .context("invalid stored source line index")?;
                let atoms = source_atoms::source_atoms_from_materials(&s.materials)
                    .into_iter()
                    .map(|a| (a.atom_id.clone(), a))
                    .collect::<HashMap<_, _>>();
                let mut basis = vec![];
                for source in &line.source_atoms {
                    for id in list(source.get("basis_atom_ids").unwrap_or(&Value::Null)) {
                        if let Some(a) = atoms.get(&crate::chat_catalog::text(id)) {
                            let score = self.source_score(&a.text, question)?;
                            if score > 0 {
                                basis.push((
                                    score,
                                    usize::from(a.kind == "catalog_label"),
                                    a.text.clone(),
                                ))
                            }
                        }
                    }
                }
                if let Some(label) = unique_best(&basis) {
                    return Ok(Some(label));
                }
                let mut sources = line.source_atoms.iter().collect::<Vec<_>>();
                sources.sort_by_key(|s| {
                    usize::from(s.get("kind").and_then(Value::as_str) != Some("catalog_label"))
                });
                let mut labels = vec![];
                for source in sources {
                    let label = string(source.get("text").unwrap_or(&Value::Null))
                        .trim_matches(space)
                        .to_owned();
                    if !label.is_empty() && !labels.contains(&label) {
                        labels.push(label)
                    }
                }
                if !labels.is_empty() {
                    return Ok(Some(labels[..labels.len().min(2)].join("、")));
                }
            }
        }
        let mut matches = vec![];
        for line in records {
            for source in &line.source_atoms {
                let label = string(source.get("text").unwrap_or(&Value::Null))
                    .trim_matches(space)
                    .to_owned();
                let score = self.source_score(&label, question)?;
                if !label.is_empty() && score > 0 {
                    matches.push((score, line.line_index, label))
                }
            }
        }
        if let Some(best) = matches.iter().map(|r| r.0).max() {
            let best = matches.iter().filter(|r| r.0 == best).collect::<Vec<_>>();
            if best.iter().map(|r| r.1).collect::<BTreeSet<_>>().len() == 1 {
                let mut labels = vec![];
                for row in best {
                    if !labels.contains(&row.2) {
                        labels.push(row.2.clone())
                    }
                }
                return Ok(Some(labels[..labels.len().min(2)].join("、")));
            }
        }
        let verse = editing_lines(s).join("\n");
        let fragment = quoted_fragment(question, &verse);
        if let Some(link) =
            resolve_links(question, fragment.as_deref(), &s.materials).filter(|v| !v.is_empty())
        {
            return Ok(Some(link));
        }
        let fragment = compact(fragment.as_deref().unwrap_or(""));
        if len(&fragment) < 2 {
            return Ok(None);
        }
        let mut materials = s.materials.clone();
        if truth(&s.interpretation) && materials.get("interpretation").is_none() {
            materials["interpretation"] = s.interpretation.clone()
        }
        let candidates = short_material_entries(&materials)
            .into_iter()
            .map(|(label, _)| label)
            .collect::<Vec<_>>();
        Ok(candidates
            .into_iter()
            .filter(|c| {
                let m = compact(c);
                len(&m) >= 2 && (m.contains(&fragment) || fragment.contains(&m))
            })
            .min_by_key(|s| (len(s), s.matches('の').count())))
    }
}
fn quoted_fragment(question: &str, verse: &str) -> Option<String> {
    let question = question.trim_matches(space);
    if question.is_empty() || verse.is_empty() {
        return None;
    }
    let probe = compact(question);
    if probe.is_empty() {
        return None;
    }
    let mut best = None;
    let mut best_score = 0;
    for part in verse.split(space).filter(|s| !s.is_empty()) {
        let ph = compact(part);
        if len(&ph) < 2 {
            continue;
        }
        let score = overlap(&ph, &probe, 2);
        if score >= 2 && score > best_score {
            best_score = score;
            best = Some(part.into())
        }
    }
    if best.is_some() {
        return best;
    }
    let full = compact(verse).chars().collect::<Vec<_>>();
    for width in (2..=full.len().min(6)).rev() {
        for chars in full.windows(width) {
            let sub = chars.iter().collect::<String>();
            if probe.contains(&sub) {
                return Some(sub);
            }
        }
    }
    None
}
fn material_compact(s: &str) -> String {
    compact(s).replace(['・', '…'], "")
}
fn resolve_links(question: &str, fragment: Option<&str>, materials: &Value) -> Option<String> {
    let mut best = None;
    let mut best_score = 0;
    for probe in fragment
        .into_iter()
        .chain(std::iter::once(question))
        .map(|s| s.trim_matches(space))
        .filter(|s| !s.is_empty())
    {
        let ph = material_compact(probe);
        if len(&ph) < 2 {
            continue;
        }
        for link in list(&materials["fragment_links"]) {
            if !link.is_object() {
                continue;
            }
            let material = string(&link["material"]);
            let sh = material_compact(&string(&link["surface"]));
            let mh = material_compact(&material);
            let score = if ph == sh || ph == mh {
                30
            } else if ph.contains(&sh) || sh.contains(&ph) {
                20 + len(&ph).min(len(&sh))
            } else if ph.contains(&mh) || mh.contains(&ph) {
                12 + len(&ph).min(len(&mh))
            } else {
                0
            };
            if score > best_score {
                best_score = score;
                best = Some(material)
            }
        }
    }
    if best_score >= 12 { best } else { None }
}
fn catalog_label(doc: &Value, id: &str) -> Option<String> {
    for group in doc["groups"].as_object()?.values() {
        for field in ["biomes", "structures"] {
            if let Some(entry) = group[field].get(id) {
                return entry["japanese"].as_str().map(str::to_owned);
            }
        }
    }
    None
}
pub fn short_material_entries(materials: &Value) -> Vec<(String, String)> {
    fn add(out: &mut Vec<(String, String)>, source: &str, kind: &str, max: usize) {
        let mut s = source
            .trim_matches(space)
            .trim_end_matches(['。', '．', '.'])
            .replace("プレイヤー", "あんた");
        if len(&s) < 2 {
            return;
        }
        if len(&s) > max {
            let mut shortened = None;
            for sep in ['の', '、', '，', ' '] {
                if s.contains(sep) {
                    let parts = s.split(sep).collect::<Vec<_>>();
                    if let Some(t) = parts
                        .iter()
                        .rev()
                        .map(|s| s.trim_matches(space))
                        .find(|t| (2..=max).contains(&len(t)))
                    {
                        shortened = Some(t.into());
                        break;
                    }
                }
            }
            s = shortened
                .unwrap_or_else(|| format!("{}…", s.chars().take(max - 1).collect::<String>()));
        }
        if out.iter().any(|(label, _)| label == &s)
            || ["いる", "ただ", "して", "ある", "する", "なる", "よう"].contains(&s.as_str())
        {
            return;
        }
        if !s.contains('\n')
            && ["ている", "でいる", "ていた", "である"]
                .iter()
                .any(|end| s.ends_with(end))
            && len(&s) <= 12
            && (!s.chars().any(|c| ('一'..='鿿').contains(&c)) || len(&s) <= 8)
        {
            return;
        }
        out.push((s, kind.into()))
    }
    let mut out = vec![];
    for key in [
        "motifs",
        "held_item",
        "inventory_items",
        "nearby_blocks",
        "dropped_items",
        "passive_mobs",
        "biome_ja",
        "structure_ja",
        "place_ja",
    ] {
        if matches!(key, "held_item" | "biome_ja" | "structure_ja" | "place_ja") {
            add(
                &mut out,
                &string(&materials[key]),
                match key {
                    "biome_ja" => "biome",
                    "structure_ja" => "structure",
                    "place_ja" => "place",
                    _ => key,
                },
                18,
            )
        } else {
            for v in list(&materials[key]) {
                add(
                    &mut out,
                    &crate::chat_catalog::text(v),
                    match key {
                        "motifs" => "motif",
                        "inventory_items" => "inventory_item",
                        "nearby_blocks" => "nearby_block",
                        "dropped_items" => "dropped_item",
                        _ => "passive_mob",
                    },
                    18,
                )
            }
        }
    }
    add(
        &mut out,
        match text(&materials["time_phase"]) {
            "morning" => "朝",
            "day" => "昼",
            "evening" => "夕方",
            "night" => "夜",
            _ => "",
        },
        "time_phase",
        18,
    );
    for (key, doc) in [
        ("biome", &*BIOMES),
        ("structure", &*crate::entry_catalog::STRUCTURES),
    ] {
        let id = string(&materials[key]);
        if !id.is_empty()
            && !truth(&materials[format!("{key}_ja")])
            && let Some(label) = catalog_label(doc, &id)
                .or_else(|| catalog_label(doc, id.strip_prefix("minecraft:").unwrap_or(&id)))
        {
            add(&mut out, &label, key, 18)
        }
    }
    let interpretation = string(&materials["interpretation"]);
    for part in interpretation.split(['、', '，', '。', '・', '/', '／', 'と']) {
        let part = part.trim_matches(space);
        if len(part) >= 2 {
            add(&mut out, part, "interpretation", 16)
        }
    }
    out
}

pub fn material_context_visible(materials: &Value, context: &str) -> bool {
    materials["material_visibility"][context] != false
}
