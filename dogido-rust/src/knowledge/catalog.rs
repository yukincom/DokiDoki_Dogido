//! 公式資料の読み取り専用検索。索引・本文を同じ世代のSHA-256で照合する。
use super::query::{fold, normalize, space};
use anyhow::{Context, Result, ensure};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::{BufRead, BufReader, Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

pub const CORE: &[&str] = &[
    "japanese_grammar",
    "historical_kana_and_scripts",
    "makurakotoba",
    "japanese_poetry_forms",
];

#[derive(Debug)]
struct Invalid;
impl std::fmt::Display for Invalid {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("invalid local reference data")
    }
}
impl std::error::Error for Invalid {}
#[derive(Debug)]
struct MissingKey;
impl std::fmt::Display for MissingKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("missing local reference field")
    }
}
impl std::error::Error for MissingKey {}
#[derive(Debug)]
struct TypeError;
impl std::fmt::Display for TypeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("invalid local reference field type")
    }
}
impl std::error::Error for TypeError {}
pub(super) fn valid(condition: bool) -> Result<()> {
    ensure!(condition, Invalid);
    Ok(())
}
pub(super) fn array(value: &Value) -> Result<&Vec<Value>> {
    value.as_array().context(Invalid)
}
pub(super) fn object(value: &Value) -> Result<&Map<String, Value>> {
    value.as_object().context(Invalid)
}
pub(super) fn string(value: &Value) -> Result<&str> {
    value.as_str().context(Invalid)
}
pub(super) fn strings(value: &Value) -> Result<Vec<&str>> {
    if value.is_null() {
        return Ok(vec![]);
    }
    array(value)?.iter().map(string).collect()
}
pub(super) fn normalized(text: &str) -> String {
    fold(&normalize(text))
}
fn compact(text: &str) -> String {
    normalized(text).chars().filter(|c| !space(*c)).collect()
}

/// Workerのfutureを取り消したときも、次の読込chunk/索引行で止まる。
#[derive(Clone, Default)]
pub struct Stop(pub Arc<AtomicBool>);
impl Stop {
    pub fn check(&self) -> Result<()> {
        ensure!(!self.0.load(Ordering::Relaxed), "lookup cancelled");
        Ok(())
    }
    pub fn cancel(&self) {
        self.0.store(true, Ordering::Relaxed);
    }
}

pub(super) fn error_class(error: &anyhow::Error) -> &'static str {
    if error.downcast_ref::<TypeError>().is_some() {
        return "TypeError";
    }
    if error.downcast_ref::<MissingKey>().is_some() {
        return "KeyError";
    }
    if let Some(io) = error.downcast_ref::<std::io::Error>() {
        return match io.kind() {
            std::io::ErrorKind::NotFound => "FileNotFoundError",
            std::io::ErrorKind::PermissionDenied => "PermissionError",
            _ => "OSError",
        };
    }
    if error.downcast_ref::<serde_json::Error>().is_some() {
        "JSONDecodeError"
    } else {
        "ValueError"
    }
}

pub(super) fn read_json(path: &Path, stop: &Stop) -> Result<Value> {
    stop.check()?;
    let mut file = File::open(path)?;
    let mut bytes = Vec::new();
    let mut chunk = [0; 65536];
    loop {
        stop.check()?;
        let size = file.read(&mut chunk)?;
        if size == 0 {
            break;
        }
        bytes.extend_from_slice(&chunk[..size]);
    }
    Ok(serde_json::from_slice(&bytes)?)
}

fn safe_path(dir: &Path, raw: &str, suffix: &str) -> Result<PathBuf> {
    valid(
        !raw.is_empty()
            && !raw.contains(['/', '\\'])
            && raw != ".."
            && !Path::new(raw).is_absolute()
            && if suffix == ".json" {
                raw.to_lowercase().ends_with(suffix)
            } else {
                raw.ends_with(suffix) && (suffix != ".jsonl" || !raw.ends_with(".index.jsonl"))
            },
    )?;
    let file = dir.join(raw);
    valid(
        !fs::symlink_metadata(dir)?.file_type().is_symlink()
            && !fs::symlink_metadata(&file)?.file_type().is_symlink(),
    )?;
    let base = fs::canonicalize(dir)?;
    let path = fs::canonicalize(file)?;
    valid(path.parent() == Some(base.as_path()))?;
    Ok(path)
}
fn digest(file: &mut File, expected: &str, stop: &Stop) -> Result<()> {
    file.seek(SeekFrom::Start(0))?;
    let mut hash = Sha256::new();
    let mut chunk = [0; 65536];
    loop {
        stop.check()?;
        let size = file.read(&mut chunk)?;
        if size == 0 {
            break;
        }
        hash.update(&chunk[..size]);
    }
    valid(format!("{:x}", hash.finalize()) == expected)
}

pub struct Catalog {
    index: Value,
    datasets: HashMap<String, Value>,
    stop: Stop,
}
impl Catalog {
    pub fn open(base: &Path, stop: Stop) -> Result<Self> {
        let index = read_json(&safe_path(base, "index.json", ".json")?, &stop)?;
        valid(index["schema_version"] == 1)?;
        let rows = array(&index["datasets"])?;
        valid(!rows.is_empty())?;
        let mut datasets = HashMap::new();
        for row in rows {
            let name = string(&row["path"])?;
            let expected = string(&row["sha256"])?;
            valid(
                expected.len() == 64
                    && expected
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            )?;
            // 検査した本文そのものを使い、検査後にpathを開き直さない。
            let path = safe_path(base, name, ".json")?;
            let mut file = File::open(path)?;
            let mut bytes = Vec::new();
            let mut hash = Sha256::new();
            let mut chunk = [0; 65536];
            loop {
                stop.check()?;
                let size = file.read(&mut chunk)?;
                if size == 0 {
                    break;
                }
                hash.update(&chunk[..size]);
                bytes.extend_from_slice(&chunk[..size]);
            }
            valid(format!("{:x}", hash.finalize()) == expected)?;
            datasets.insert(name.to_owned(), serde_json::from_slice(&bytes)?);
        }
        for entry in object(&index["entries"])?.values() {
            valid(datasets.contains_key(string(&entry["dataset_path"])?))?;
        }
        Ok(Self {
            index,
            datasets,
            stop,
        })
    }
    pub fn get(&self, id: &str) -> Result<Option<Value>> {
        self.stop.check()?;
        let Some(entry) = self.index["entries"].get(id) else {
            return Ok(None);
        };
        let dataset = &self.datasets[string(&entry["dataset_path"])?];
        let records = array(&dataset[string(&entry["record_key"])?])?;
        let position = entry["record_index"].as_u64().context(Invalid)? as usize;
        let record = records.get(position).context(Invalid)?;
        valid(record["id"] == entry["id"])?;
        Ok(Some(record.clone()))
    }
    fn ids(&self, facet: &str, key: &str) -> Result<HashSet<String>> {
        let table = object(self.index.get(facet).context(MissingKey)?)?;
        let Some(ids) = table.get(key) else {
            return Ok(HashSet::new());
        };
        // 未登録語は正常な空集合。存在するnull等は壊れた索引として区別する。
        ids.as_array()
            .context(TypeError)?
            .iter()
            .map(|id| Ok(string(id)?.to_owned()))
            .collect()
    }
    fn token_ids(&self, token: &str) -> Result<HashSet<String>> {
        let mut direct = self.ids("by_term", token)?;
        let token = compact(token);
        let chars: Vec<char> = token.chars().collect();
        if chars.is_empty() {
            return Ok(HashSet::new());
        }
        let mut matches: Option<HashSet<String>> = None;
        for window in chars.windows(chars.len().min(3)) {
            let ids = self.ids("by_term_ngram", &window.iter().collect::<String>())?;
            matches = Some(match matches {
                None => ids,
                Some(old) => old.intersection(&ids).cloned().collect(),
            });
            if matches.as_ref().is_some_and(HashSet::is_empty) {
                return Ok(direct);
            }
        }
        for id in matches.unwrap_or_default() {
            if strings(&self.index["entries"][&id]["explicit_terms"])?
                .iter()
                .any(|term| compact(term).contains(&token))
            {
                direct.insert(id);
            }
        }
        Ok(direct)
    }
    /// ファセット名は索引のby_*名。各分類内はOR、分類間はAND。
    pub fn search(
        &self,
        query: &str,
        filters: &[(String, Vec<String>)],
        limit: usize,
    ) -> Result<Vec<Value>> {
        if limit == 0 {
            return Ok(vec![]);
        }
        self.stop.check()?;
        let entries = object(&self.index["entries"])?;
        let mut candidates: HashSet<String> = entries.keys().cloned().collect();
        let query = normalized(query);
        if !query.is_empty() {
            let direct = self.ids("by_term", &query)?;
            let compact_ids = if direct.is_empty() {
                self.token_ids(&compact(&query))?
            } else {
                direct
            };
            if !compact_ids.is_empty() {
                candidates.retain(|id| compact_ids.contains(id));
            } else {
                for token in query.split(space).filter(|s| !s.is_empty()) {
                    let ids = self.token_ids(token)?;
                    candidates.retain(|id| ids.contains(id));
                }
            }
        }
        for (facet, values) in filters {
            let requested: Vec<_> = values
                .iter()
                .map(|s| normalized(s))
                .filter(|s| !s.is_empty())
                .collect();
            if requested.is_empty() {
                continue;
            }
            let mut ids = HashSet::new();
            for value in requested {
                ids.extend(self.ids(facet, &value)?);
            }
            candidates.retain(|id| ids.contains(id));
        }
        let mut ranked = Vec::new();
        for id in candidates {
            self.stop.check()?;
            let entry = &entries[&id];
            let score = if query.is_empty() {
                4
            } else if normalized(string(&entry["title_ja"])?) == query {
                0
            } else if strings(&entry["aliases"])?
                .iter()
                .any(|s| normalized(s) == query)
            {
                1
            } else if strings(&entry["explicit_terms"])?
                .iter()
                .any(|s| normalized(s) == query)
            {
                2
            } else {
                3
            };
            ranked.push((score, string(&entry["id"])?.to_owned(), entry.clone()));
        }
        ranked.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
        Ok(ranked.into_iter().take(limit).map(|(_, _, v)| v).collect())
    }
    pub fn search_core(&self, query: &str, limit: usize) -> Result<Vec<Value>> {
        self.search_records(query, CORE, &[], limit)
    }
    pub fn search_records(
        &self,
        query: &str,
        datasets: &[&str],
        kinds: &[&str],
        limit: usize,
    ) -> Result<Vec<Value>> {
        let filters = vec![
            (
                "by_dataset".into(),
                datasets.iter().map(|s| (*s).into()).collect(),
            ),
            (
                "by_kind".into(),
                kinds.iter().map(|s| (*s).into()).collect(),
            ),
        ];
        let mut hits = self.search(query, &filters, limit)?;
        let query = normalized(query);
        hits.sort_by_key(|e| {
            (
                normalized(e["title_ja"].as_str().unwrap_or("")) != query,
                e["id"].as_str().unwrap_or("").to_owned(),
            )
        });
        let mut records = HashMap::new();
        for hit in hits {
            if let Some(mut record) = self.get(string(&hit["id"])?)? {
                // Pythonの {dataset_id: ..., **record} と同じ優先関係。
                if record.get("dataset_id").is_none() {
                    record["dataset_id"] = hit["dataset_id"].clone();
                }
                records.insert(string(&record["id"])?.to_owned(), record);
            }
        }
        rank_records(records.into_values(), &query, limit)
    }
}

pub(super) fn rank_records(
    records: impl IntoIterator<Item = Value>,
    query: &str,
    limit: usize,
) -> Result<Vec<Value>> {
    let query = normalized(query);
    let mut by_id = HashMap::new();
    for record in records {
        by_id.insert(string(&record["id"])?.to_owned(), record);
    }
    let mut ranked = Vec::new();
    for (id, record) in by_id {
        let score = if query.is_empty() {
            4
        } else if normalized(string(&record["title_ja"])?) == query {
            0
        } else if record
            .get("aliases")
            .map(strings)
            .transpose()?
            .unwrap_or_default()
            .iter()
            .any(|s| normalized(s) == query)
        {
            1
        } else if record
            .get("search_terms")
            .map(strings)
            .transpose()?
            .unwrap_or_default()
            .iter()
            .any(|s| normalized(s) == query)
        {
            2
        } else {
            3
        };
        ranked.push((score, id, record));
    }
    ranked.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
    Ok(ranked.into_iter().take(limit).map(|(_, _, v)| v).collect())
}

/// 大きなJSONLは索引を順に読み、必要な行だけをbyte位置から読む。
/// 全文検索・全レコード常駐・語彙選定レベルから学年への変換はしない。
pub struct Bulk {
    base: PathBuf,
    index: Value,
    stop: Stop,
}
impl Bulk {
    pub fn open(base: &Path, stop: Stop) -> Result<Self> {
        let base = fs::canonicalize(base)?.join("data/normalized");
        let index = read_json(&base.join("index.json"), &stop)?;
        valid(index["schema_version"] == 2)?;
        let mut ids = HashSet::new();
        for row in array(&index["datasets"])? {
            let id = string(&row["id"])?;
            valid(!id.is_empty() && ids.insert(id))?;
            for (path, hash, suffix) in [
                ("path", "sha256", ".jsonl"),
                ("index_path", "index_sha256", ".index.jsonl"),
            ] {
                let path = safe_path(&base, string(&row[path])?, suffix)?;
                digest(&mut File::open(path)?, string(&row[hash])?, &stop)?;
            }
        }
        Ok(Self { base, index, stop })
    }
    pub fn get(&self, id: &str) -> Result<Option<Value>> {
        for dataset in array(&self.index["datasets"])? {
            let path = safe_path(&self.base, string(&dataset["index_path"])?, ".index.jsonl")?;
            let mut file = File::open(path)?;
            digest(&mut file, string(&dataset["index_sha256"])?, &self.stop)?;
            file.seek(SeekFrom::Start(0))?;
            for line in BufReader::new(file).lines() {
                self.stop.check()?;
                let entry: Value = serde_json::from_str(&line?)?;
                valid(
                    entry["dataset_id"] == dataset["id"]
                        && entry["dataset_path"] == dataset["path"],
                )?;
                if entry["id"] != id {
                    continue;
                }
                let path = safe_path(&self.base, string(&dataset["path"])?, ".jsonl")?;
                let mut data = File::open(path)?;
                digest(&mut data, string(&dataset["sha256"])?, &self.stop)?;
                let size = data.stream_position()?;
                let offset = entry["byte_offset"].as_u64().context(Invalid)?;
                let length = entry["byte_length"].as_u64().context(Invalid)?;
                valid(length >= 2 && offset.checked_add(length).is_some_and(|end| end <= size))?;
                data.seek(SeekFrom::Start(offset))?;
                let mut bytes = vec![0; usize::try_from(length)?];
                data.read_exact(&mut bytes)?;
                self.stop.check()?;
                let mut record: Value = serde_json::from_slice(&bytes)?;
                valid(record["id"] == entry["id"])?;
                if record.get("dataset_id").is_none() {
                    record["dataset_id"] = entry["dataset_id"].clone();
                }
                return Ok(Some(record));
            }
        }
        Ok(None)
    }
}

impl Bulk {
    pub(super) fn dataset_ids(&self) -> Result<Vec<&str>> {
        array(&self.index["datasets"])?
            .iter()
            .map(|r| string(&r["id"]))
            .collect()
    }
    pub fn search(
        &self,
        query: &str,
        datasets: &[&str],
        kinds: &[&str],
        limit: usize,
    ) -> Result<Vec<Value>> {
        if limit == 0 {
            return Ok(vec![]);
        }
        let query = normalized(query);
        let wanted: Vec<_> = datasets
            .iter()
            .filter(|s| !s.is_empty())
            .map(|s| normalized(s))
            .collect();
        let kinds: HashSet<_> = kinds
            .iter()
            .filter(|s| !s.is_empty())
            .map(|s| normalized(s))
            .collect();
        let mut candidates: Vec<(u8, String, Value)> = Vec::new();
        let mut seen = HashSet::new();
        let all = array(&self.index["datasets"])?;
        let mut visited = HashSet::new();
        let ordered: Vec<_> = if wanted.is_empty() {
            all.iter().collect()
        } else {
            wanted
                .iter()
                .filter(|id| visited.insert((*id).clone()))
                .filter_map(|id| all.iter().find(|row| row["id"] == *id))
                .collect()
        };
        for dataset in ordered {
            let mut file = File::open(safe_path(
                &self.base,
                string(&dataset["index_path"])?,
                ".index.jsonl",
            )?)?;
            digest(&mut file, string(&dataset["index_sha256"])?, &self.stop)?;
            file.seek(SeekFrom::Start(0))?;
            for line in BufReader::new(file).lines() {
                self.stop.check()?;
                let mut entry: Value = serde_json::from_str(&line?)?;
                valid(
                    entry["dataset_id"] == dataset["id"]
                        && entry["dataset_path"] == dataset["path"],
                )?;
                if !kinds.is_empty()
                    && !kinds.contains(&normalized(entry["kind"].as_str().unwrap_or("")))
                {
                    continue;
                }
                let Some(score) = bulk_score(&entry, &query)? else {
                    continue;
                };
                let id = string(&entry["id"])?.to_owned();
                if !seen.insert(id.clone()) {
                    continue;
                }
                entry["_dataset_sha256"] = dataset["sha256"].clone();
                candidates.push((score, id, entry));
                candidates.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
                candidates.truncate(limit);
            }
        }
        let mut files: HashMap<String, (File, u64)> = HashMap::new();
        let mut result = Vec::new();
        for (_, _, entry) in candidates {
            self.stop.check()?;
            let path = string(&entry["dataset_path"])?;
            if !files.contains_key(path) {
                let mut file = File::open(safe_path(&self.base, path, ".jsonl")?)?;
                digest(&mut file, string(&entry["_dataset_sha256"])?, &self.stop)?;
                let size = file.stream_position()?;
                files.insert(path.into(), (file, size));
            }
            let (file, size) = files.get_mut(path).unwrap();
            let offset = entry["byte_offset"].as_u64().context(Invalid)?;
            let length = entry["byte_length"].as_u64().context(Invalid)?;
            valid(length >= 2 && offset.checked_add(length).is_some_and(|end| end <= *size))?;
            file.seek(SeekFrom::Start(offset))?;
            let mut bytes = vec![0; usize::try_from(length)?];
            file.read_exact(&mut bytes)?;
            let mut record: Value = serde_json::from_slice(&bytes)?;
            valid(record["id"] == entry["id"])?;
            if record.get("dataset_id").is_none() {
                record["dataset_id"] = entry["dataset_id"].clone();
            }
            result.push(record);
        }
        Ok(result)
    }
}
fn bulk_score(entry: &Value, query: &str) -> Result<Option<u8>> {
    if query.is_empty() {
        return Ok(Some(3));
    }
    if normalized(entry["title_ja"].as_str().unwrap_or("")) == query {
        return Ok(Some(0));
    }
    let terms: Vec<_> = strings(&entry["search_terms"])?
        .iter()
        .filter(|s| !s.is_empty())
        .map(|s| normalized(s))
        .collect();
    if terms.iter().any(|s| s == query) {
        return Ok(Some(1));
    }
    let compact_query = compact(query);
    if !compact_query.is_empty() && terms.iter().any(|s| compact(s).contains(&compact_query)) {
        return Ok(Some(2));
    }
    let tokens: Vec<_> = query
        .split(space)
        .map(compact)
        .filter(|s| !s.is_empty())
        .collect();
    Ok((!tokens.is_empty()
        && tokens
            .iter()
            .all(|t| terms.iter().any(|s| compact(s).contains(t))))
    .then_some(3))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    pub(super) fn base() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../reference/language_education_and_poetry")
    }
    #[test]
    fn canonical_search_order_facets_and_records_match_python() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../fixtures/language-retrieval.json")).unwrap();
        let catalog = Catalog::open(&base(), Stop::default()).unwrap();
        for case in fixture["catalog"].as_array().unwrap() {
            let filters: Vec<(String, Vec<String>)> =
                serde_json::from_value(case["filters"].clone()).unwrap();
            let actual = catalog
                .search(
                    case["query"].as_str().unwrap(),
                    &filters,
                    case["limit"].as_u64().unwrap() as usize,
                )
                .unwrap();
            assert_eq!(json!(actual), case["expected"], "{case}");
        }
        for case in fixture["records"].as_array().unwrap() {
            assert_eq!(
                json!(catalog.get(case["id"].as_str().unwrap()).unwrap()),
                case["expected"]
            );
        }
    }
    struct Temporary(PathBuf);
    impl Temporary {
        fn new() -> Self {
            let path =
                std::env::temp_dir().join(format!("dogido-reference-{}", uuid::Uuid::new_v4()));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Temporary {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }
    fn write(path: &Path, value: &Value) -> String {
        let bytes = serde_json::to_vec(value).unwrap();
        fs::write(path, &bytes).unwrap();
        format!("{:x}", Sha256::digest(&bytes))
    }
    fn small_catalog(base: &Path) -> Value {
        let hash = write(
            &base.join("data.json"),
            &json!({"records":[{"id":"record","title_ja":"旧"}]}),
        );
        let index = json!({"schema_version":1,"datasets":[{"path":"data.json","sha256":hash}],
            "entries":{"record":{"id":"record","dataset_path":"data.json","record_key":"records","record_index":0}}});
        write(&base.join("index.json"), &index);
        index
    }
    fn small_bulk(base: &Path) -> Value {
        let normalized = base.join("data/normalized");
        fs::create_dir_all(&normalized).unwrap();
        let record = json!({"id":"record","title_ja":"旧"});
        let hash = write(&normalized.join("data.jsonl"), &record);
        let length = fs::metadata(normalized.join("data.jsonl")).unwrap().len();
        let index_hash = write(
            &normalized.join("data.index.jsonl"),
            &json!({"id":"record","dataset_id":"sample","dataset_path":"data.jsonl","byte_offset":0,"byte_length":length}),
        );
        let manifest = json!({"schema_version":2,"datasets":[{"id":"sample","path":"data.jsonl","sha256":hash,"index_path":"data.index.jsonl","index_sha256":index_hash}]});
        write(&normalized.join("index.json"), &manifest);
        manifest
    }
    #[test]
    fn core_reader_checks_paths_hashes_record_ids_and_snapshot_generation() {
        let tmp = Temporary::new();
        let original = small_catalog(&tmp.0);
        let snapshot = Catalog::open(&tmp.0, Stop::default()).unwrap();
        let new_hash = write(
            &tmp.0.join("next.json"),
            &json!({"records":[{"id":"record","title_ja":"新"}]}),
        );
        let old_metadata = fs::metadata(tmp.0.join("data.json")).unwrap();
        File::options()
            .write(true)
            .open(tmp.0.join("next.json"))
            .unwrap()
            .set_times(fs::FileTimes::new().set_modified(old_metadata.modified().unwrap()))
            .unwrap();
        fs::rename(tmp.0.join("next.json"), tmp.0.join("data.json")).unwrap();
        assert_eq!(snapshot.get("record").unwrap().unwrap()["title_ja"], "旧");
        assert!(Catalog::open(&tmp.0, Stop::default()).is_err());
        let mut current = original.clone();
        current["datasets"][0]["sha256"] = new_hash.into();
        write(&tmp.0.join("index.json"), &current);
        assert_eq!(
            Catalog::open(&tmp.0, Stop::default())
                .unwrap()
                .get("record")
                .unwrap()
                .unwrap()["title_ja"],
            "新"
        );
        current["entries"]["record"]["id"] = "different".into();
        write(&tmp.0.join("index.json"), &current);
        assert!(
            Catalog::open(&tmp.0, Stop::default())
                .unwrap()
                .get("record")
                .is_err()
        );
        for name in [
            "../data.json",
            "/tmp/data.json",
            "sub/data.json",
            "./data.json",
            "data\\x.json",
            "data.txt",
        ] {
            let mut bad = original.clone();
            bad["datasets"][0]["path"] = name.into();
            write(&tmp.0.join("index.json"), &bad);
            assert!(Catalog::open(&tmp.0, Stop::default()).is_err(), "{name}");
        }
    }
    #[test]
    fn bulk_reader_rechecks_open_generation_and_validates_byte_locators() {
        let tmp = Temporary::new();
        let original = small_bulk(&tmp.0);
        let bulk = Bulk::open(&tmp.0, Stop::default()).unwrap();
        assert_eq!(bulk.get("record").unwrap().unwrap()["title_ja"], "旧");
        assert!(bulk.get("missing").unwrap().is_none());
        let dir = tmp.0.join("data/normalized");
        let new_hash = write(
            &dir.join("next.jsonl"),
            &json!({"id":"record","title_ja":"新"}),
        );
        fs::rename(dir.join("next.jsonl"), dir.join("data.jsonl")).unwrap();
        assert!(bulk.get("record").is_err());
        let mut manifest = original;
        manifest["datasets"][0]["sha256"] = new_hash.into();
        write(&dir.join("index.json"), &manifest);
        assert_eq!(
            Bulk::open(&tmp.0, Stop::default())
                .unwrap()
                .get("record")
                .unwrap()
                .unwrap()["title_ja"],
            "新"
        );
        for (offset, length, id, dataset, path) in [
            (0, 1, "record", "sample", "data.jsonl"),
            (u64::MAX, 2, "record", "sample", "data.jsonl"),
            (1, 999999, "record", "sample", "data.jsonl"),
            (0, 38, "record", "wrong", "data.jsonl"),
            (0, 38, "record", "sample", "../data.jsonl"),
        ] {
            let hash = write(
                &dir.join("data.index.jsonl"),
                &json!({"id":id,"dataset_id":dataset,"dataset_path":path,"byte_offset":offset,"byte_length":length}),
            );
            manifest["datasets"][0]["index_sha256"] = hash.into();
            write(&dir.join("index.json"), &manifest);
            assert!(
                Bulk::open(&tmp.0, Stop::default())
                    .unwrap()
                    .get("record")
                    .is_err()
            );
        }
        manifest["datasets"][0]["path"] = "../data.jsonl".into();
        write(&dir.join("index.json"), &manifest);
        assert!(Bulk::open(&tmp.0, Stop::default()).is_err());
    }
    #[cfg(unix)]
    #[test]
    fn symlinked_catalog_index_dataset_and_normalized_directory_are_rejected() {
        use std::os::unix::fs::symlink;
        let tmp = Temporary::new();
        small_catalog(&tmp.0);
        fs::rename(tmp.0.join("index.json"), tmp.0.join("actual.json")).unwrap();
        symlink("actual.json", tmp.0.join("index.json")).unwrap();
        assert!(Catalog::open(&tmp.0, Stop::default()).is_err());
        fs::remove_file(tmp.0.join("index.json")).unwrap();
        fs::rename(tmp.0.join("actual.json"), tmp.0.join("index.json")).unwrap();
        fs::rename(tmp.0.join("data.json"), tmp.0.join("actual.json")).unwrap();
        symlink("actual.json", tmp.0.join("data.json")).unwrap();
        assert!(Catalog::open(&tmp.0, Stop::default()).is_err());
        small_bulk(&tmp.0);
        fs::rename(tmp.0.join("data/normalized"), tmp.0.join("actual")).unwrap();
        symlink("../actual", tmp.0.join("data/normalized")).unwrap();
        assert!(Bulk::open(&tmp.0, Stop::default()).is_err());
    }
    #[test]
    fn cancelled_reader_stops_before_open_or_next_read() {
        let stop = Stop::default();
        stop.cancel();
        assert!(read_json(&base().join("index.json"), &stop).is_err());
        assert!(Catalog::open(&base(), stop.clone()).is_err());
        let tmp = Temporary::new();
        small_bulk(&tmp.0);
        let bulk = Bulk::open(&tmp.0, Stop::default()).unwrap();
        bulk.stop.cancel();
        assert!(bulk.get("record").is_err());
    }
}
