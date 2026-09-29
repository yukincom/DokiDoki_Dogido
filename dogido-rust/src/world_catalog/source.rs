//! Existing grouped-entry rules, evaluated over immutable bundled documents.
use super::*;
use std::collections::HashSet;
const GROUP: &[&str] = &["items", "groups", "refs"];
const NON_ENTRY: &[&str] = &[
    "label",
    "english",
    "japanese",
    "note",
    "items",
    "groups",
    "source",
    "refs",
    "meta",
    "variants",
    "role",
    "recommended_fields",
    "schema",
    "notes",
    "direct_labels",
    "description",
    "priority",
    "poetic",
    "parent",
];
const ITEM_FILES: &[&str] = &[
    "minecraft_tools_and_utilities",
    "minecraft_combat_items",
    "minecraft_food_and_drinks",
    "minecraft_materials",
    "minecraft_spawn_egg",
    "minecraft_command_only_items",
];
fn pointer(v: &Value) -> bool {
    v.as_str()
        .is_some_and(|s| strip(s).to_lowercase().ends_with(".json"))
}
fn group(v: &Value) -> bool {
    v.as_object()
        .is_some_and(|m| GROUP.iter().any(|k| m.contains_key(*k)))
}
fn any_key(v: &Map<String, Value>, keys: &[&str]) -> bool {
    keys.iter().any(|k| v.contains_key(*k))
}
fn label(v: &Value) -> Option<String> {
    if let Some(s) = v.as_str() {
        return Some(s.into());
    }
    v.as_object()?;
    ["japanese", "label"]
        .iter()
        .find_map(|k| v.get(*k).filter(|x| truth(x)).map(text))
}
struct Reader<'a> {
    docs: &'a Map<String, Value>,
}
impl Reader<'_> {
    fn path(&self, spec: &Value) -> Option<String> {
        let raw = text(spec);
        let name = strip(&raw);
        if name.is_empty() {
            return None;
        }
        let (parent, file) = name.rsplit_once('/').unwrap_or(("", name));
        let prefixed = if parent.is_empty() {
            format!("minecraft_{file}")
        } else {
            format!("{parent}/minecraft_{file}")
        };
        [
            name.into(),
            prefixed,
            format!("block/{file}"),
            format!("block/minecraft_{file}"),
        ]
        .into_iter()
        .find(|k| self.docs.contains_key(k))
    }
    fn canonical(&self, path: &str, id: &str, seen: &HashSet<(String, String)>) -> Option<Value> {
        let key = (path.into(), id.into());
        if seen.contains(&key) {
            return None;
        }
        let doc = self.docs.get(path)?.as_object()?;
        let mut found = vec![];
        for (section, payload) in doc {
            if let Some(node) = payload.as_object() {
                if section == "direct_labels" {
                    for (k, v) in node {
                        if v.is_string() {
                            found.push((k.clone(), v.clone()));
                        }
                    }
                } else if section != "meta" {
                    self.node(node, Some(section), false, &mut found)
                }
            }
        }
        let mut pending = None;
        for (k, v) in found {
            if k != id {
                continue;
            }
            if v.is_object() && pointer(&v["source"]) {
                if pending.is_none() {
                    pending = Some(v)
                }
                continue;
            }
            return Some(v);
        }
        let pending = pending?;
        let next = self.path(&pending["source"])?;
        let mut seen = seen.clone();
        seen.insert(key);
        self.canonical(&next, id, &seen)
    }
    fn resolve(&self, v: &Value, id: &str) -> Value {
        let Some(map) = v.as_object() else {
            return v.clone();
        };
        if !pointer(&v["source"]) {
            return v.clone();
        }
        let mut local: Map<String, Value> = map
            .iter()
            .filter(|(k, _)| !matches!(k.as_str(), "source" | "refs"))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let canonical = self
            .path(&v["source"])
            .and_then(|p| self.canonical(&p, id, &HashSet::new()));
        match canonical {
            Some(Value::String(s)) => {
                local.entry("japanese").or_insert(Value::String(s));
            }
            Some(Value::Object(m)) => {
                for (k, v) in m {
                    if !matches!(k.as_str(), "source" | "refs") {
                        local.insert(k, v);
                    }
                }
            }
            _ => {}
        }
        if local.is_empty() {
            Value::Null
        } else {
            Value::Object(local)
        }
    }
    fn maybe(&self, v: &Value, id: &str, resolve: bool) -> Value {
        if resolve {
            self.resolve(v, id)
        } else {
            v.clone()
        }
    }
    fn entry(&self, id: &str, v: &Value, resolve: bool, out: &mut Vec<(String, Value)>) {
        out.push((id.into(), self.maybe(v, id, resolve)));
        if let Some(labels) = v.get("label").and_then(Value::as_object)
            && !any_key(labels, &["japanese", "label", "note"])
        {
            for (k, v) in labels {
                if v.is_string() {
                    out.push((k.clone(), v.clone()));
                }
            }
        }
    }
    fn node(
        &self,
        node: &Map<String, Value>,
        node_id: Option<&str>,
        resolve: bool,
        out: &mut Vec<(String, Value)>,
    ) {
        if let Some(id) = node_id
            && !node.contains_key("refs")
            && any_key(node, &["japanese", "note", "source"])
        {
            let payload: Map<String, Value> = node
                .iter()
                .filter(|(k, _)| !GROUP.contains(&k.as_str()) && k.as_str() != "label")
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            if !payload.is_empty() {
                out.push((id.into(), self.maybe(&Value::Object(payload), id, resolve)));
            }
        }
        if let Some(labels) = node.get("label").and_then(Value::as_object) {
            if any_key(labels, &["japanese", "label", "note"]) {
                if let Some(id) = node_id {
                    out.push((id.into(), Value::Object(labels.clone())))
                }
            } else {
                for (k, v) in labels {
                    if v.is_string() {
                        out.push((k.clone(), v.clone()));
                    }
                }
            }
        }
        if resolve
            && let (Some(source), Some(refs)) = (
                node.get("source").filter(|v| pointer(v)),
                node.get("refs").and_then(Value::as_array),
            )
            && let Some(path) = self.path(source)
        {
            for id in refs {
                let id = text(id);
                if let Some(value) = self.canonical(&path, &id, &HashSet::new()) {
                    out.push((id, value));
                }
            }
        }
        if let Some(items) = node.get("items").and_then(Value::as_object) {
            for (id, v) in items {
                if group(v) {
                    self.node(v.as_object().unwrap(), Some(id), resolve, out)
                } else {
                    self.entry(id, v, resolve, out)
                }
            }
        }
        if let Some(groups) = node.get("groups").and_then(Value::as_object) {
            for (id, v) in groups {
                if let Some(v) = v.as_object() {
                    self.node(v, Some(id), resolve, out)
                }
            }
        }
        for (k, v) in node {
            if NON_ENTRY.contains(&k.as_str()) {
                continue;
            }
            if group(v) {
                self.node(v.as_object().unwrap(), Some(k), resolve, out)
            } else if v.is_string() {
                out.push((k.clone(), v.clone()))
            } else if v
                .as_object()
                .is_some_and(|m| any_key(m, &["japanese", "label", "note", "source"]))
            {
                self.entry(k, v, resolve, out)
            }
        }
    }
}
fn merge(labels: &mut Map<String, Value>, id: String, candidate: String) {
    let replace = labels
        .get(&id)
        .and_then(Value::as_str)
        .is_none_or(|s| candidate.chars().count() > s.chars().count());
    if replace {
        labels.insert(id, Value::String(candidate));
    }
}
pub(super) fn labels(docs: &Map<String, Value>) -> (Map<String, Value>, Map<String, Value>) {
    let reader = Reader { docs };
    let mut entries = Map::new();
    for name in ITEM_FILES {
        if let Some(catalog) = docs.get(&format!("{name}.json")).and_then(Value::as_object) {
            for payload in catalog.values() {
                if let Some(node) = payload.as_object() {
                    let mut rows = vec![];
                    reader.node(node, None, true, &mut rows);
                    for (id, payload) in rows {
                        let Some(label) = label(&payload).filter(|s| !s.is_empty()) else {
                            continue;
                        };
                        let note = payload
                            .get("note")
                            .cloned()
                            .unwrap_or(Value::String(String::new()));
                        if !truth(&note)
                            && entries.get(&id).is_some_and(|v: &Value| truth(&v["note"]))
                        {
                            continue;
                        }
                        entries.insert(id.clone(), serde_json::json!({"label":label,"note":note}));
                        if let Some(variants) = payload.get("variants").and_then(Value::as_object) {
                            for (k, v) in variants {
                                entries
                                    .insert(k.clone(), serde_json::json!({"label":v,"note":note}));
                            }
                        }
                    }
                }
            }
        }
    }
    let items = entries
        .into_iter()
        .map(|(k, v)| (k, Value::String(text(&v["label"]))))
        .collect();
    let mut blocks = Map::new();
    let mut paths: Vec<_> = docs
        .keys()
        .filter(|p| p.starts_with("block/") && p.ends_with(".json") && !p[6..].contains('/'))
        .cloned()
        .collect();
    paths.sort();
    if paths.is_empty() {
        if docs.contains_key("block.json") {
            paths.push("block.json".to_owned());
        } else if docs.contains_key("minecraft_block.json") {
            paths.push("minecraft_block.json".to_owned());
        }
    }
    // The real catalogue is a directory. Legacy single-file callers use the same collector below.
    for path in paths {
        let Some(doc) = docs[&path].as_object() else {
            continue;
        };
        if let Some(labels) = doc.get("direct_labels").and_then(Value::as_object) {
            for (k, v) in labels {
                merge(&mut blocks, k.clone(), text(v));
            }
        }
        for payload in doc.values() {
            if let Some(node) = payload.as_object() {
                let mut rows = vec![];
                reader.node(node, None, true, &mut rows);
                for (id, v) in rows {
                    if let Some(label) = label(&v).filter(|s| !s.is_empty()) {
                        merge(&mut blocks, id, label)
                    }
                }
            }
        }
    }
    (items, blocks)
}

// Preserve the canonical entry's metadata for source snapshots and inventory similarity.
impl Reader<'_> {
    fn raw_entry(
        &self,
        id: &str,
        value: &Value,
        path: &[String],
        out: &mut Vec<(String, Value, Vec<String>)>,
    ) {
        out.push((id.into(), self.resolve(value, id), path.to_vec()));
        if let Some(labels) = value.get("label").and_then(Value::as_object)
            && !any_key(labels, &["japanese", "label", "note"])
        {
            for (id, value) in labels {
                if value.is_string() {
                    out.push((id.clone(), value.clone(), path.to_vec()));
                }
            }
        }
    }
    fn raw_node(
        &self,
        node: &Map<String, Value>,
        id: Option<&str>,
        path: &[String],
        out: &mut Vec<(String, Value, Vec<String>)>,
    ) {
        if let Some(id) = id
            && !node.contains_key("refs")
            && any_key(node, &["japanese", "note", "source"])
        {
            let value: Map<_, _> = node
                .iter()
                .filter(|(k, _)| !GROUP.contains(&k.as_str()) && k.as_str() != "label")
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            if !value.is_empty() {
                out.push((
                    id.into(),
                    self.resolve(&Value::Object(value), id),
                    path.to_vec(),
                ));
            }
        }
        if let Some(labels) = node.get("label").and_then(Value::as_object) {
            if any_key(labels, &["japanese", "label", "note"]) {
                if let Some(id) = id {
                    out.push((id.into(), Value::Object(labels.clone()), path.to_vec()));
                }
            } else {
                for (id, value) in labels {
                    if value.is_string() {
                        out.push((id.clone(), value.clone(), path.to_vec()));
                    }
                }
            }
        }
        if let (Some(source), Some(refs)) = (
            node.get("source").filter(|v| pointer(v)),
            node.get("refs").and_then(Value::as_array),
        ) && let Some(source) = self.path(source)
        {
            for id in refs {
                let id = text(id);
                if let Some(value) = self.canonical(&source, &id, &HashSet::new()) {
                    out.push((id, value, path.to_vec()));
                }
            }
        }
        if let Some(items) = node.get("items").and_then(Value::as_object) {
            for (id, value) in items {
                if group(value) {
                    let mut next = path.to_vec();
                    next.push(id.clone());
                    self.raw_node(value.as_object().unwrap(), Some(id), &next, out);
                } else {
                    self.raw_entry(id, value, path, out);
                }
            }
        }
        if let Some(groups) = node.get("groups").and_then(Value::as_object) {
            for (id, value) in groups {
                if let Some(value) = value.as_object() {
                    let mut next = path.to_vec();
                    next.push(id.clone());
                    self.raw_node(value, Some(id), &next, out);
                }
            }
        }
        for (id, value) in node {
            if NON_ENTRY.contains(&id.as_str()) {
                continue;
            }
            if group(value) {
                let mut next = path.to_vec();
                next.push(id.clone());
                self.raw_node(value.as_object().unwrap(), Some(id), &next, out);
            } else if value.is_string() {
                out.push((id.clone(), value.clone(), path.to_vec()));
            } else if value
                .as_object()
                .is_some_and(|m| any_key(m, &["japanese", "label", "note", "source"]))
            {
                self.raw_entry(id, value, path, out);
            }
        }
    }
    fn collect_raw(&self, node: &Map<String, Value>, section: &str, out: &mut Map<String, Value>) {
        let mut rows = vec![];
        self.raw_node(node, None, &[], &mut rows);
        for (id, value, path) in rows {
            let Some(entry) = raw_payload(&value, section, &path) else {
                continue;
            };
            if !truth(&entry["note"]) && out.get(&id).is_some_and(|v| truth(&v["note"])) {
                continue;
            }
            out.insert(id.clone(), entry);
            if let Some(variants) = value.get("variants").and_then(Value::as_object) {
                for (variant, label) in variants {
                    out.insert(variant.clone(),serde_json::json!({"label":label,"japanese":label,"note":value.get("note").cloned().unwrap_or(serde_json::json!("")),"parent":id,"section":section,"group_path":path}));
                }
            }
        }
    }
}
fn raw_payload(value: &Value, section: &str, path: &[String]) -> Option<Value> {
    let label = label(value).filter(|v| !v.is_empty())?;
    let mut out = value.as_object().cloned().unwrap_or_default();
    out.insert("label".into(), label.clone().into());
    out.entry("japanese").or_insert(label.into());
    for key in ["role", "note"] {
        out.entry(key).or_insert(Value::String(String::new()));
    }
    out.entry("section").or_insert(section.into());
    out.entry("group_path").or_insert(serde_json::json!(path));
    Some(out.into())
}
pub(super) fn raw_entries(docs: &Map<String, Value>) -> (Map<String, Value>, Map<String, Value>) {
    let reader = Reader { docs };
    let mut items = Map::new();
    let mut blocks = Map::new();
    for name in ITEM_FILES {
        if let Some(doc) = docs.get(&format!("{name}.json")).and_then(Value::as_object) {
            for (section, value) in doc {
                if let Some(value) = value.as_object() {
                    reader.collect_raw(value, section, &mut items);
                }
            }
        }
    }
    let mut paths: Vec<_> = docs
        .keys()
        .filter(|p| p.starts_with("block/") && p.ends_with(".json") && !p[6..].contains('/'))
        .collect();
    paths.sort();
    if paths.is_empty() {
        for name in ["block.json", "minecraft_block.json"] {
            if let Some((k, _)) = docs.get_key_value(name) {
                paths.push(k);
                break;
            }
        }
    }
    for path in paths {
        if let Some(doc) = docs[path].as_object() {
            let name = path.rsplit('/').next().unwrap().trim_end_matches(".json");
            if let Some(labels) = doc.get("direct_labels").and_then(Value::as_object) {
                for (id, value) in labels {
                    blocks.insert(id.clone(),raw_payload(value,name,&["direct_labels".into()]).unwrap_or_else(||serde_json::json!({"label":text(value),"japanese":text(value),"section":name,"group_path":["direct_labels"]})));
                }
            }
            for (section, value) in doc {
                if !matches!(section.as_str(), "direct_labels" | "meta")
                    && let Some(value) = value.as_object()
                {
                    reader.collect_raw(value, section, &mut blocks);
                }
            }
        }
    }
    (items, blocks)
}
