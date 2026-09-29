//! 版固定Minecraft技術DBの読み取り専用検索。通信・モデル・世界操作は行わない。
use super::{
    catalog::{Stop, array, object, string, valid},
    query::{fold, nfkc, space},
};
use anyhow::{Context, Result};
use serde::{
    Deserialize, Deserializer,
    de::{self, MapAccess, SeqAccess, Visitor},
};
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};
use std::{
    cmp::Ordering,
    collections::{BinaryHeap, HashMap, HashSet},
    fmt,
    fs::{self, File},
    io::{BufRead, BufReader, Read, Seek, SeekFrom},
    path::{Path, PathBuf},
};

const DATASETS: &[&str] = &[
    "version_information",
    "official_changes",
    "registry_entries",
    "datapack_entries",
    "tag_definitions",
];

// Valueの標準Deserializeは重複キーを上書きするため、全階層で明示的に拒否する。
struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        struct JsonVisitor;
        impl<'de> Visitor<'de> for JsonVisitor {
            type Value = Strict;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("duplicate-free finite JSON")
            }
            fn visit_bool<E: de::Error>(self, value: bool) -> std::result::Result<Strict, E> {
                Ok(Strict(value.into()))
            }
            fn visit_i64<E: de::Error>(self, value: i64) -> std::result::Result<Strict, E> {
                Ok(Strict(value.into()))
            }
            fn visit_u64<E: de::Error>(self, value: u64) -> std::result::Result<Strict, E> {
                Ok(Strict(value.into()))
            }
            fn visit_f64<E: de::Error>(self, value: f64) -> std::result::Result<Strict, E> {
                Number::from_f64(value)
                    .map(|n| Strict(Value::Number(n)))
                    .ok_or_else(|| E::custom("nonfinite JSON number"))
            }
            fn visit_str<E: de::Error>(self, value: &str) -> std::result::Result<Strict, E> {
                Ok(Strict(value.into()))
            }
            fn visit_string<E: de::Error>(self, value: String) -> std::result::Result<Strict, E> {
                Ok(Strict(value.into()))
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_none<E: de::Error>(self) -> std::result::Result<Strict, E> {
                self.visit_unit()
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut values = Vec::new();
                while let Some(Strict(value)) = seq.next_element()? {
                    values.push(value);
                }
                Ok(Strict(Value::Array(values)))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut map: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut values = Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if values.contains_key(&key) {
                        return Err(de::Error::custom(format!("duplicate JSON key: {key}")));
                    }
                    let Strict(value) = map.next_value()?;
                    values.insert(key, value);
                }
                Ok(Strict(Value::Object(values)))
            }
        }
        deserializer.deserialize_any(JsonVisitor)
    }
}
fn parse(bytes: &[u8]) -> Result<Value> {
    // Python json.loads(bytes)もUTF-8 BOMを受け付ける。
    let bytes = bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(bytes);
    serde_json::from_slice::<Strict>(bytes)
        .map(|s| s.0)
        .map_err(|e| anyhow::anyhow!("invalid Minecraft JSON: {e}"))
}
fn regular(path: &Path) -> Result<()> {
    valid(fs::symlink_metadata(path).is_ok_and(|m| m.is_file() && !m.file_type().is_symlink()))
}
fn read_json(path: &Path, stop: &Stop) -> Result<(Value, String)> {
    stop.check()?;
    regular(path)?;
    let mut file = File::open(path)?;
    let mut bytes = Vec::new();
    let mut hash = Sha256::new();
    let mut chunk = [0; 65536];
    loop {
        stop.check()?;
        let length = file.read(&mut chunk)?;
        if length == 0 {
            break;
        }
        hash.update(&chunk[..length]);
        bytes.extend_from_slice(&chunk[..length]);
    }
    Ok((parse(&bytes)?, format!("{:x}", hash.finalize())))
}
fn digest(file: &mut File, expected: &Value, stop: &Stop) -> Result<u64> {
    file.seek(SeekFrom::Start(0))?;
    let mut hash = Sha256::new();
    let mut chunk = [0; 65536];
    loop {
        stop.check()?;
        let length = file.read(&mut chunk)?;
        if length == 0 {
            break;
        }
        hash.update(&chunk[..length]);
    }
    valid(expected.as_str() == Some(format!("{:x}", hash.finalize()).as_str()))?;
    Ok(file.stream_position()?)
}
fn normalize(text: &str) -> String {
    fold(&nfkc(text))
        .split(space)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}
fn compact(text: &str) -> String {
    normalize(text).chars().filter(|c| !space(*c)).collect()
}
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    }
}
fn python_text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        _ => value.to_string(),
    }
}
fn one(value: &Value) -> bool {
    value == &Value::Bool(true) || value.as_f64() == Some(1.0)
}
fn integer(value: &Value) -> Result<u64> {
    value
        .as_u64()
        .or_else(|| value.as_bool().map(u64::from))
        .context("invalid nonnegative Minecraft integer")
}
fn root(path: &Path) -> Result<PathBuf> {
    valid(!fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink()))?;
    let resolved = fs::canonicalize(path)?;
    if !resolved.is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "missing Minecraft cache root",
        )
        .into());
    }
    Ok(resolved)
}
fn snapshot(root: &Path, name: &str) -> Result<PathBuf> {
    valid(name.strip_prefix("mcjava-").is_some_and(|tail| {
        !tail.is_empty()
            && tail
                .bytes()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || b".-".contains(&c))
    }))?;
    let candidate = root.join(name);
    valid(
        fs::symlink_metadata(&candidate).is_ok_and(|m| m.is_dir() && !m.file_type().is_symlink()),
    )?;
    let resolved = fs::canonicalize(candidate)?;
    valid(resolved.parent() == Some(root))?;
    Ok(resolved)
}
fn artifact(base: &Path, name: &str, suffix: &str) -> Result<PathBuf> {
    // PurePosixPathと同じく単独の ./ と末尾 / は正規化する。.. は許可しない。
    let parts: Vec<_> = name
        .split('/')
        .filter(|p| !p.is_empty() && *p != ".")
        .collect();
    valid(
        !name.is_empty()
            && !name.starts_with('/')
            && !name.contains('\\')
            && parts.len() == 1
            && parts[0] != ".."
            && parts[0].ends_with(suffix)
            && (suffix != ".jsonl" || !parts[0].ends_with(".index.jsonl")),
    )?;
    let path = base.join(parts[0]);
    regular(&path)?;
    let resolved = fs::canonicalize(path)?;
    valid(resolved.parent() == Some(base))?;
    Ok(resolved)
}

struct Dataset {
    row: Value,
    data: PathBuf,
    index: PathBuf,
}
pub struct Minecraft {
    datasets: Vec<Dataset>,
    stop: Stop,
}
impl Minecraft {
    /// current・manifest・source_lockと全資料のSHAを同じ読取境界で照合する。
    pub fn open(cache_root: &Path, source_lock_path: &Path, stop: Stop) -> Result<Self> {
        stop.check()?;
        let root = root(cache_root)?;
        let (current, _) = read_json(&root.join("current.json"), &stop)?;
        valid(current.is_object() && one(&current["schema_version"]))?;
        let snapshot = snapshot(&root, string(&current["snapshot_id"])?)?;
        let (manifest, manifest_hash) = read_json(&snapshot.join("manifest.json"), &stop)?;
        valid(current["manifest_sha256"].as_str() == Some(&manifest_hash))?;
        valid(
            manifest.is_object()
                && one(&manifest["schema_version"])
                && manifest["snapshot_id"] == current["snapshot_id"]
                && manifest["storage_scope"] == "local_cache_only_gitignored",
        )?;
        let (lock, lock_hash) = read_json(source_lock_path, &stop)?;
        valid(
            lock.is_object()
                && one(&lock["schema_version"])
                && lock["snapshot_id"] == manifest["snapshot_id"]
                && lock["minecraft_version"] == manifest["minecraft_version"]
                && manifest["source_lock_sha256"].as_str() == Some(&lock_hash),
        )?;
        let rows = array(&manifest["datasets"])?;
        let mut datasets = Vec::new();
        let mut ids = HashSet::new();
        for row in rows {
            object(row)?;
            let id = string(&row["id"])?;
            valid(DATASETS.contains(&id) && ids.insert(id))?;
            integer(&row["record_count"])?;
            let data = artifact(&snapshot, string(&row["path"])?, ".jsonl")?;
            let index = artifact(&snapshot, string(&row["index_path"])?, ".index.jsonl")?;
            digest(&mut File::open(&data)?, &row["sha256"], &stop)?;
            digest(&mut File::open(&index)?, &row["index_sha256"], &stop)?;
            datasets.push(Dataset {
                row: row.clone(),
                data,
                index,
            });
        }
        valid(
            rows.len() == DATASETS.len()
                && rows.iter().zip(DATASETS).all(|(row, id)| row["id"] == *id),
        )?;
        Ok(Self { datasets, stop })
    }
    pub fn search(
        &self,
        query: &str,
        dataset_ids: &[&str],
        record_types: &[&str],
        registry_ids: &[&str],
        limit: usize,
    ) -> Result<Vec<Value>> {
        if limit == 0 {
            return Ok(vec![]);
        }
        self.stop.check()?;
        let requested = normalized_unique(dataset_ids);
        let selected: Vec<_> = if requested.is_empty() {
            self.datasets.iter().collect()
        } else {
            requested
                .iter()
                .filter_map(|id| self.datasets.iter().find(|d| d.row["id"] == *id))
                .collect()
        };
        let types = normalized_unique(record_types);
        let registries = normalized_unique(registry_ids);
        let query = normalize(query);
        let mut seen = HashSet::new();
        let mut candidates = BinaryHeap::new();
        for dataset in selected {
            regular(&dataset.index)?;
            let mut file = File::open(&dataset.index)?;
            digest(&mut file, &dataset.row["index_sha256"], &self.stop)?;
            file.seek(SeekFrom::Start(0))?;
            let mut reader = BufReader::new(file);
            let mut line = Vec::new();
            let mut count = 0u64;
            loop {
                self.stop.check()?;
                line.clear();
                if reader.read_until(b'\n', &mut line)? == 0 {
                    break;
                }
                let entry = parse(&line)?;
                valid(
                    entry.is_object()
                        && entry["dataset_id"] == dataset.row["id"]
                        && entry["dataset_path"] == dataset.row["path"]
                        && entry["id"].is_string()
                        && entry["record_type"].is_string()
                        && entry["title_ja"].is_string()
                        && entry["search_terms"].is_array(),
                )?;
                count += 1;
                if !types.is_empty() && !types.contains(&normalize(string(&entry["record_type"])?))
                {
                    continue;
                }
                if !registries.is_empty()
                    && !registries.contains(&normalize(
                        &entry
                            .get("registry_id")
                            .map(python_text)
                            .unwrap_or_default(),
                    ))
                {
                    continue;
                }
                let id = string(&entry["id"])?;
                valid(seen.insert(id.to_owned()))?;
                let Some(score) = score(&entry, &query)? else {
                    continue;
                };
                let candidate = Candidate {
                    score,
                    id: id.into(),
                    entry,
                };
                if candidates.len() < limit {
                    candidates.push(candidate);
                } else if candidates.peek().is_some_and(|worst| &candidate < worst) {
                    candidates.pop();
                    candidates.push(candidate);
                }
            }
            valid(count == integer(&dataset.row["record_count"])?)?;
        }
        let ranked = candidates.into_sorted_vec();
        let mut grouped: HashMap<&str, Vec<(usize, &Value)>> = HashMap::new();
        for (position, candidate) in ranked.iter().enumerate() {
            grouped
                .entry(string(&candidate.entry["dataset_id"])?)
                .or_default()
                .push((position, &candidate.entry));
        }
        let mut loaded = vec![Value::Null; ranked.len()];
        for (id, entries) in grouped {
            self.stop.check()?;
            let dataset = self
                .datasets
                .iter()
                .find(|d| d.row["id"] == id)
                .context("missing Minecraft dataset")?;
            regular(&dataset.data)?;
            let mut file = File::open(&dataset.data)?;
            let size = digest(&mut file, &dataset.row["sha256"], &self.stop)?;
            for (position, entry) in entries {
                self.stop.check()?;
                let offset = integer(&entry["byte_offset"])?;
                let length = integer(&entry["byte_length"])?;
                valid(length >= 2 && offset.checked_add(length).is_some_and(|end| end <= size))?;
                file.seek(SeekFrom::Start(offset))?;
                let mut bytes = vec![0; usize::try_from(length)?];
                file.read_exact(&mut bytes)?;
                self.stop.check()?;
                let mut record = parse(&bytes)?;
                valid(record.is_object() && record["id"] == entry["id"])?;
                if record.get("dataset_id").is_none() {
                    record["dataset_id"] = entry["dataset_id"].clone();
                }
                loaded[position] = record;
            }
        }
        valid(loaded.iter().all(Value::is_object))?;
        Ok(loaded)
    }
}
fn normalized_unique(values: &[&str]) -> Vec<String> {
    let mut seen = HashSet::new();
    values
        .iter()
        .map(|s| normalize(s))
        .filter(|s| !s.is_empty() && seen.insert(s.clone()))
        .collect()
}
fn score(entry: &Value, query: &str) -> Result<Option<u8>> {
    if query.is_empty() {
        return Ok(Some(4));
    }
    let id = normalize(string(&entry["id"])?);
    let title = normalize(string(&entry["title_ja"])?);
    let terms: Vec<_> = array(&entry["search_terms"])?
        .iter()
        .filter(|v| truthy(v))
        .map(|v| normalize(&python_text(v)))
        .collect();
    if query == id || query == title {
        return Ok(Some(0));
    }
    if terms.iter().any(|s| s == query) {
        return Ok(Some(1));
    }
    let query_compact = compact(query);
    let compact_terms: Vec<_> = [id, title]
        .into_iter()
        .chain(terms)
        .map(|s| compact(&s))
        .collect();
    if !query_compact.is_empty() && compact_terms.iter().any(|s| s.contains(&query_compact)) {
        return Ok(Some(2));
    }
    let tokens: Vec<_> = query
        .split(space)
        .map(compact)
        .filter(|s| !s.is_empty())
        .collect();
    if !tokens.is_empty()
        && tokens
            .iter()
            .all(|t| compact_terms.iter().any(|s| s.contains(t)))
    {
        return Ok(Some(3));
    }
    Ok(None)
}
struct Candidate {
    score: u8,
    id: String,
    entry: Value,
}
impl PartialEq for Candidate {
    fn eq(&self, other: &Self) -> bool {
        (self.score, &self.id) == (other.score, &other.id)
    }
}
impl Eq for Candidate {}
impl PartialOrd for Candidate {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Candidate {
    fn cmp(&self, other: &Self) -> Ordering {
        (self.score, &self.id).cmp(&(other.score, &other.id))
    }
}

#[cfg(test)]
#[path = "minecraft_tests.rs"]
mod tests;
