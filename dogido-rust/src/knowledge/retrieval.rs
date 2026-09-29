//! 限定国語対話の資料検索。対象・検索語は検証済みの解釈からだけ受け取る。
use super::{
    catalog::{self, Bulk, Catalog, Stop, array, string, strings, valid},
    query::{fold, nfkc, space},
};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    sync::LazyLock,
};

#[derive(Clone)]
pub struct Paths {
    pub reference: PathBuf,
    pub cards: PathBuf,
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
            cards: root.join("dogido_server/language_dialogue/source_cards.json"),
        })
    }
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub terms: Vec<String>,
    pub facet: String,
    pub target: String,
}
#[derive(Serialize)]
pub struct SearchResult {
    pub terms: Vec<String>,
    pub facts: Vec<Value>,
    pub status: &'static str,
    pub error: &'static str,
}

fn dedup(values: impl IntoIterator<Item = String>, limit: usize) -> Vec<String> {
    let mut seen = HashSet::new();
    values
        .into_iter()
        .filter(|v| seen.insert(v.clone()))
        .take(limit)
        .collect()
}
fn compact(text: &str) -> String {
    fold(&nfkc(text))
        .chars()
        .filter(|c| !space(*c))
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .collect()
}
fn kanji(c: char) -> bool {
    ('一'..='鿿').contains(&c)
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
    bulk: Option<Bulk>,
}
impl<'a> Reader<'a> {
    pub(super) fn new(paths: &'a Paths, stop: Stop) -> Self {
        Self {
            paths,
            stop,
            catalog: None,
            bulk: None,
        }
    }
    pub(super) fn catalog(&mut self) -> Result<&Catalog> {
        if self.catalog.is_none() {
            self.catalog = Some(Catalog::open(&self.paths.reference, self.stop.clone())?);
        }
        Ok(self.catalog.as_ref().unwrap())
    }
    pub(super) fn bulk(&mut self) -> Result<&Bulk> {
        if self.bulk.is_none() {
            self.bulk = Some(Bulk::open(&self.paths.reference, self.stop.clone())?);
        }
        Ok(self.bulk.as_ref().unwrap())
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
    pub(super) fn japanese(
        &mut self,
        query: &str,
        datasets: &[&str],
        limit: usize,
    ) -> Result<Vec<Value>> {
        let core: Vec<_> = if datasets.is_empty() {
            catalog::CORE.to_vec()
        } else {
            datasets
                .iter()
                .copied()
                .filter(|s| catalog::CORE.contains(s))
                .collect()
        };
        let mut records = if core.is_empty() {
            vec![]
        } else {
            self.catalog()?.search_records(query, &core, &[], limit)?
        };
        if !datasets.is_empty() && datasets.iter().all(|s| catalog::CORE.contains(s)) {
            return catalog::rank_records(records, query, limit);
        }
        let available = self
            .bulk()?
            .dataset_ids()?
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        let selected = if datasets.is_empty() {
            available.iter().map(String::as_str).collect::<Vec<_>>()
        } else {
            datasets
                .iter()
                .copied()
                .filter(|s| available.iter().any(|x| x == s))
                .collect()
        };
        if !selected.is_empty() {
            records.extend(self.bulk()?.search(query, &selected, &[], limit)?);
        }
        catalog::rank_records(records, query, limit)
    }
    fn fact(&mut self, record: &Value, text: &str, status: &str) -> Result<Value> {
        let mut fact = json!({"id":string(&record["id"])?,"title_ja":string(&record["title_ja"])?,
            "text_ja":text,"claim_status":status,"scope":record["structured_data"]["source_scope"].as_str().unwrap_or(""),
            "sources":self.sources(record)?});
        for key in ["rules", "machine_use"] {
            let value = &record[key];
            if !value.is_null()
                && value != false
                && value != ""
                && value != &json!([])
                && value != &json!({})
            {
                fact[key] = value.clone();
            }
        }
        Ok(fact)
    }
    fn search(
        &mut self,
        terms: &[String],
        facet: &str,
        target: &str,
        facts: &mut Map<String, Value>,
    ) -> Result<()> {
        let tokens = dedup(
            terms
                .iter()
                .flat_map(|s| s.split(space).filter(|s| !s.is_empty()).map(str::to_owned)),
            12,
        );
        let searches = dedup(terms.iter().chain(tokens.iter()).cloned(), usize::MAX);
        let cards = catalog::read_json(&self.paths.cards, &self.stop)?;
        for card in array(&cards["records"])? {
            let keys: HashSet<_> = strings(&card["search_terms"])?
                .iter()
                .map(|s| compact(s))
                .collect();
            if searches.iter().any(|s| keys.contains(&compact(s))) {
                facts.insert(string(&card["id"])?.into(), card.clone());
            }
        }
        if ["grade", "reading", "spelling", "comparison"].contains(&facet) {
            let mut candidates: Vec<String> = tokens
                .iter()
                .filter(|s| s.chars().count() == 1)
                .cloned()
                .collect();
            if !target.is_empty() && target.chars().all(kanji) && target.chars().count() <= 4 {
                candidates.extend(target.chars().map(|c| c.to_string()));
            }
            if facet == "grade" {
                candidates = candidates
                    .iter()
                    .map(|s| {
                        nfkc(s)
                            .chars()
                            .map(|c| {
                                if c.is_ascii_digit() {
                                    "零一二三四五六七八九"
                                        .chars()
                                        .nth(c as usize - '0' as usize)
                                        .unwrap()
                                } else {
                                    c
                                }
                            })
                            .collect()
                    })
                    .collect();
            }
            let characters = dedup(
                candidates
                    .into_iter()
                    .filter(|s| "一" <= s.as_str() && s.as_str() <= "鿿"),
                8,
            );
            for character in characters {
                self.stop.check()?;
                // ㍻→平成等はPythonのprofileと同じく一字検査で不成立にする。
                valid(character.chars().count() == 1)?;
                let c = character.chars().next().unwrap();
                let joyo = self.bulk()?.get(&format!("kanji.joyo.u{:x}", c as u32))?;
                let grade = self
                    .bulk()?
                    .get(&format!("kanji.grade-allocation.u{:x}", c as u32))?;
                if joyo.is_none() && grade.is_none() {
                    continue;
                }
                for (key, record) in [("grade", grade.as_ref()), ("joyo", joyo.as_ref())] {
                    let Some(record) = record else {
                        continue;
                    };
                    let text = if key == "grade" {
                        valid(record["school_grade"].is_u64())?;
                        format!(
                            "漢字『{character}』の小学校配当学年は第{}学年。字種の配当であり、全ての読みや熟語の学年ではない。",
                            record["school_grade"]
                        )
                    } else {
                        let readings: Result<Vec<_>> = array(&record["readings"])?
                            .iter()
                            .map(|r| string(&r["reading"]))
                            .collect();
                        format!(
                            "常用漢字表の『{character}』の音訓: {}。熟語の語源を示す情報ではない。",
                            readings?.join("、")
                        )
                    };
                    let mut fact = self.fact(record, &text, "official_table")?;
                    if key == "grade" {
                        fact["allocation"] = json!({"character":character,"school_grade":record["school_grade"],"scope":"character_only"});
                    }
                    facts.insert(string(&record["id"])?.into(), fact);
                }
                if facet == "grade" && grade.is_none() {
                    let id = format!("allocation-absence:{character}");
                    facts.insert(id.clone(), json!({"id":id,"title_ja":character,
                        "text_ja":"手元の小学校学年別漢字配当表にこの字の配当なし。中学校の特定学年や、習わないことは示さない。",
                        "claim_status":"local_table_lookup","sources":[]}));
                }
            }
        }
        for term in searches {
            for record in self.catalog()?.search_core(&term, 2)? {
                if let Some(text) = record["definition_ja"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .or_else(|| record["summary_ja"].as_str().filter(|s| !s.is_empty()))
                {
                    let fact = self.fact(
                        &record,
                        text,
                        record["definition_status"]
                            .as_str()
                            .unwrap_or("editorial_synthesis"),
                    )?;
                    facts.insert(string(&record["id"])?.into(), fact);
                }
            }
        }
        Ok(())
    }
}

pub fn search(paths: &Paths, request: &Request, stop: Stop) -> SearchResult {
    let terms = dedup(
        request
            .terms
            .iter()
            .map(|s| s.trim_matches(space).to_owned())
            .filter(|s| !s.is_empty()),
        4,
    );
    let mut facts = Map::new();
    let mut reader = Reader {
        paths,
        stop,
        catalog: None,
        bulk: None,
    };
    let outcome = reader.search(&terms, &request.facet, &request.target, &mut facts);
    SearchResult {
        terms,
        facts: facts.into_values().take(10).collect(),
        status: if outcome.is_ok() {
            "searched"
        } else {
            "unavailable"
        },
        error: outcome
            .as_ref()
            .err()
            .map(catalog::error_class)
            .unwrap_or(""),
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

pub async fn search_async(paths: Paths, request: Request) -> Result<Value> {
    let result = lookup_worker(move |stop| search(&paths, &request, stop)).await?;
    Ok(serde_json::to_value(result)?)
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
    fn paths() -> Paths {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        Paths {
            reference: root.join("reference/language_education_and_poetry"),
            cards: root.join("dogido_server/language_dialogue/source_cards.json"),
        }
    }
    #[test]
    fn full_lookup_terms_facts_sources_rules_and_order_match_python() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../fixtures/language-retrieval.json")).unwrap();
        for case in fixture["lookups"].as_array().unwrap() {
            let request = serde_json::from_value(case["request"].clone()).unwrap();
            let result = serde_json::to_value(search(&paths(), &request, Stop::default())).unwrap();
            assert_eq!(result, case["expected"], "request: {}", case["request"]);
        }
    }
    #[test]
    fn missing_cards_and_missing_catalog_keep_failure_distinct_from_no_match() {
        let request = Request {
            terms: vec!["たくみ".into()],
            facet: "meaning".into(),
            target: "匠".into(),
        };
        let mut paths = paths();
        paths.reference = paths.reference.join("not-present");
        let result = search(&paths, &request, Stop::default());
        assert_eq!(result.status, "unavailable");
        assert_eq!(result.error, "FileNotFoundError");
        assert_eq!(result.facts[0]["id"], "language.usage.takumi");
        paths.cards = paths.cards.join("not-present");
        let result = search(&paths, &request, Stop::default());
        assert!(result.facts.is_empty());
        assert_eq!(result.status, "unavailable");
    }
}
