//! 詩の基礎資料の検索と出典表示。入力は照会対象、出力は読取済みrecord。
//! ファイル読取と取消だけを担当し、対話状態・保存・モデル呼出は行わない。
use super::{
    catalog::{self, Catalog, Stop},
    query::space,
};
use anyhow::{Context, Result};
use serde_json::{Map, Value, json};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    sync::LazyLock,
};

#[derive(Clone)]
pub struct Paths {
    pub reference: PathBuf,
}
impl Paths {
    /// Python補助と同じcheckoutの資料。起動時cwdやモデル出力から選ばない。
    pub fn from_helper(helper: &Path) -> Result<Self> {
        let helper = std::fs::canonicalize(helper)?;
        let root = helper
            .ancestors()
            .nth(3)
            .context("missing helper repository root")?;
        Ok(Self {
            reference: root.join("reference/language_education_and_poetry"),
        })
    }
}
pub(super) fn shorten(text: &str, max: usize) -> String {
    let text = text
        .split(space)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    if text.chars().count() <= max {
        return text;
    }
    let mut kept = String::new();
    let mut part = String::new();
    let mut parts = Vec::new();
    for c in text.chars() {
        if "。！？!?".contains(c) {
            if !part.is_empty() {
                part.push(c);
                parts.push(std::mem::take(&mut part));
            }
        } else {
            part.push(c);
        }
    }
    if !part.is_empty() {
        parts.push(part);
    }
    for sentence in parts {
        let sentence = sentence.trim_matches(space);
        if kept.chars().count() + sentence.chars().count() > max {
            break;
        }
        kept.push_str(sentence);
    }
    if !kept.is_empty() {
        return kept.trim_end_matches("、， ").into();
    }
    format!(
        "{}…",
        text.chars()
            .take(max - 1)
            .collect::<String>()
            .trim_end_matches("、， ")
    )
}
static LABELS: LazyLock<Map<String, Value>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("organization-labels.json"))
        .expect("checked organization labels")
});

pub(super) struct Reader<'a> {
    paths: &'a Paths,
    stop: Stop,
    catalog: Option<Catalog>,
}
impl<'a> Reader<'a> {
    pub(super) fn new(paths: &'a Paths, stop: Stop) -> Self {
        Self {
            paths,
            stop,
            catalog: None,
        }
    }
    pub(super) fn catalog(&mut self) -> Result<&Catalog> {
        if self.catalog.is_none() {
            self.catalog = Some(Catalog::open(&self.paths.reference, self.stop.clone())?);
        }
        Ok(self.catalog.as_ref().unwrap())
    }
    pub(super) fn sources(&mut self, record: &Value) -> Result<Vec<Value>> {
        let mut refs = Vec::new();
        if let Some(rows) = record["source_refs"].as_array() {
            for row in rows {
                if let Some(id) = row["source_id"].as_str().filter(|s| !s.is_empty()) {
                    refs.push((id, row["locator"].as_str().unwrap_or("")));
                }
            }
        }
        if let Some(id) = record["source_id"].as_str().filter(|s| !s.is_empty()) {
            refs.push((id, record["source_locator"].as_str().unwrap_or("")));
        }
        let mut sources = Vec::new();
        let mut seen = HashSet::new();
        for (id, locator) in refs {
            if seen.contains(id) {
                continue;
            }
            if let Some(source) = self.catalog()?.get(id)? {
                let title = shorten(
                    source["title_ja"]
                        .as_str()
                        .filter(|s| !s.is_empty())
                        .unwrap_or(id),
                    100,
                );
                let organization = source["organization_id"].as_str().unwrap_or("");
                let label = LABELS
                    .get(organization)
                    .and_then(Value::as_str)
                    .unwrap_or(&title);
                sources.push(json!({"source_id":id,"title_ja":title,"citation_label_ja":label,
                    "locator":shorten(locator,120), "url":source["canonical_url"].as_str().unwrap_or(""),
                    "source_kind":source["publication_role"].as_str().filter(|s| !s.is_empty())
                        .or_else(||source["kind"].as_str()).unwrap_or("")}));
                seen.insert(id);
            }
        }
        sources.truncate(3);
        Ok(sources)
    }
    /// 詩資料だけを検索し、索引の一致順位と上限を保持する。
    pub(super) fn poetry(&mut self, query: &str, limit: usize) -> Result<Vec<Value>> {
        self.catalog()?
            .search_records(query, catalog::CORE, &[], limit)
    }
}

pub(super) async fn lookup_worker<T: Send + 'static>(
    work: impl FnOnce(Stop) -> T + Send + 'static,
) -> Result<T> {
    struct Guard(Stop);
    impl Drop for Guard {
        fn drop(&mut self) {
            self.0.cancel();
        }
    }
    let guard = Guard(Stop::default());
    let stop = guard.0.clone();
    Ok(tokio::task::spawn_blocking(move || work(stop)).await?)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn dropping_lookup_future_stops_its_blocking_worker() {
        let (started_tx, started_rx) = tokio::sync::oneshot::channel();
        let (ended_tx, ended_rx) = tokio::sync::oneshot::channel();
        let job = tokio::spawn(lookup_worker(move |stop| {
            started_tx.send(()).unwrap();
            while stop.check().is_ok() {
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
            ended_tx.send(()).unwrap();
        }));
        started_rx.await.unwrap();
        job.abort();
        assert!(job.await.unwrap_err().is_cancelled());
        tokio::time::timeout(std::time::Duration::from_secs(2), ended_rx)
            .await
            .unwrap()
            .unwrap();
    }
}
