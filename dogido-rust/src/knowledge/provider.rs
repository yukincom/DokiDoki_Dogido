//! 明示知識質問を正本資料へ結び、検証可能な事実だけを返す。生成・保存・世界操作なし。
use super::{
    catalog::{CORE, Stop, error_class, valid},
    query::{Query, fold, normalize, space},
    retrieval::{self, Reader, shorten},
};
use anyhow::Result;
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    sync::LazyLock,
};

static POLICY: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("provider-policy.json")).expect("checked knowledge policy")
});
#[derive(Clone)]
pub struct Paths {
    pub language: retrieval::Paths,
    pub minecraft: PathBuf,
    pub source_lock: PathBuf,
}
impl Paths {
    pub fn from_helper(helper: &Path) -> Result<Self> {
        let language = retrieval::Paths::from_helper(helper)?;
        let root = language.reference.parent().unwrap().parent().unwrap();
        Ok(Self {
            minecraft: root.join(".dogido_reference/minecraft_technical"),
            source_lock: root.join("reference/minecraft_technical/source_lock.json"),
            language,
        })
    }
}
fn text<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key].as_str().unwrap_or("")
}
fn choose<'a>(v: &'a Value, keys: &[&str], default: &'a str) -> &'a str {
    keys.iter()
        .map(|k| text(v, k))
        .find(|s| !s.is_empty())
        .unwrap_or(default)
}
fn rows<'a>(v: &'a Value, key: &str) -> &'a [Value] {
    v[key].as_array().map(Vec::as_slice).unwrap_or(&[])
}
fn compact(s: &str) -> String {
    fold(&normalize(s)).chars().filter(|c| !space(*c)).collect()
}
fn has(values: &Value, s: &str) -> bool {
    values.as_array().is_some_and(|v| v.iter().any(|v| v == s))
}
fn push_unique(out: &mut Vec<String>, value: &str) {
    if !value.is_empty() && !out.iter().any(|s| s == value) {
        out.push(value.into());
    }
}
fn fact(
    record: &Value,
    body: &str,
    status: &str,
    sources: Vec<Value>,
    dialogue: &str,
) -> Option<Value> {
    let body = shorten(body, 220);
    if body.is_empty() || sources.is_empty() {
        return None;
    }
    Some(
        json!({"record_id":text(record,"id"),"dataset_id":text(record,"dataset_id"),
        "title_ja":choose(record,&["title_ja","reading"],""),"text_ja":body,"claim_status":status,
        "sources":sources,"dialogue_text_ja":shorten(dialogue,220)}),
    )
}
fn match_rank(record: &Value, subject: &str) -> Option<u8> {
    let q = compact(subject);
    if ["title_ja", "entry_id"]
        .iter()
        .any(|k| !text(record, k).is_empty() && compact(text(record, k)) == q)
    {
        return Some(0);
    }
    if rows(record, "aliases")
        .iter()
        .any(|v| v.as_str().is_some_and(|s| !s.is_empty() && compact(s) == q))
        || compact(text(record, "reading")) == q
    {
        return Some(1);
    }
    if rows(record, "search_terms")
        .iter()
        .any(|v| v.as_str().is_some_and(|s| !s.is_empty() && compact(s) == q))
    {
        return Some(2);
    }
    (compact(text(record, "id")) == q).then_some(3)
}
fn cjk(subject: &str) -> Option<char> {
    let mut chars = subject.chars();
    let c = chars.next()?;
    (chars.next().is_none()
        && ((0x3400..=0x4dbf).contains(&(c as u32))
            || (0x4e00..=0x9fff).contains(&(c as u32))
            || (0xf900..=0xfaff).contains(&(c as u32))))
    .then_some(c)
}
fn grammar_fact(record: &Value, intent: &str, sources: Vec<Value>) -> Option<Value> {
    let title = choose(record, &["title_ja", "reading"], "");
    let reading = text(record, "reading");
    let (mut categories, mut connections, mut explanations) = (vec![], vec![], vec![]);
    push_unique(
        &mut explanations,
        text(&record["general_explanation"], "text").trim_matches(space),
    );
    for sense in rows(record, "senses") {
        for category in rows(sense, "categories") {
            if let Some(v) = category.as_str() {
                push_unique(&mut categories, v);
            }
        }
        for conn in rows(sense, "connections") {
            push_unique(&mut connections, text(&conn["connection_type"], "text"));
        }
        push_unique(
            &mut explanations,
            text(&sense["usage"], "text").trim_matches(space),
        );
    }
    let mut parts = vec![format!("文型「{title}」")];
    if !reading.is_empty() && reading != text(record, "title_ja") {
        parts.push(format!("読みは「{reading}」"));
    }
    if intent == "definition" && !explanations.is_empty() {
        let shown = shorten(&explanations[0], 135);
        let mut notes = vec![];
        if shown
            != explanations[0]
                .split(space)
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join(" ")
        {
            notes.push("一部省略".into());
        }
        if explanations.len() > 1 {
            notes.push(format!("ほか{}件", explanations.len() - 1));
        }
        parts.push(format!("意味・用法は「{shown}」{}", suffix(&notes)));
    }
    if intent != "definition" && !connections.is_empty() {
        let shown: Vec<_> = connections.iter().take(2).map(|s| shorten(s, 48)).collect();
        let mut notes = vec![];
        if connections
            .iter()
            .zip(&shown)
            .any(|(raw, short)| raw != short)
        {
            notes.push("一部省略".into());
        }
        if connections.len() > shown.len() {
            notes.push(format!("ほか{}件", connections.len() - shown.len()));
        }
        parts.push(format!(
            "{}は「{}」{}",
            if notes.is_empty() {
                "接続"
            } else {
                "代表的な接続"
            },
            shown.join("／"),
            suffix(&notes)
        ));
    }
    if !categories.is_empty() {
        let shown: Vec<_> = categories.iter().take(3).map(|s| shorten(s, 24)).collect();
        let notes = if categories.len() > shown.len() {
            vec![format!("ほか{}件", categories.len() - shown.len())]
        } else {
            vec![]
        };
        parts.push(format!(
            "{}は「{}」{}",
            if notes.is_empty() {
                "分類"
            } else {
                "代表的な分類"
            },
            shown.join("・"),
            suffix(&notes)
        ));
    }
    fact(
        record,
        &(parts.join("。") + "。"),
        "official_normalized_extract",
        sources,
        "",
    )
}
fn suffix(notes: &[String]) -> String {
    if notes.is_empty() {
        String::new()
    } else {
        format!("（{}）", notes.join("、"))
    }
}
fn reference_facts(
    reader: &mut Reader<'_>,
    record: &Value,
    query: &Query,
    limit: usize,
) -> Result<Vec<Value>> {
    let sources = reader.sources(record)?;
    if sources.is_empty() {
        return Ok(vec![]);
    }
    if text(record, "kind") == "grammar_pattern" {
        return Ok(grammar_fact(record, &query.intent, sources)
            .into_iter()
            .collect());
    }
    let mut facts = vec![];
    if query.intent == "rules" {
        for rule in rows(record, "rules") {
            facts.extend(fact(
                record,
                text(rule, "statement_ja"),
                choose(rule, &["claim_status"], "source_stated"),
                sources.clone(),
                "",
            ));
            if facts.len() >= limit {
                return Ok(facts);
            }
        }
    }
    let summary = choose(record, &["definition_ja", "summary_ja"], "");
    if !summary.is_empty() {
        facts.extend(fact(
            record,
            summary,
            choose(
                record,
                &["definition_status", "classification_status"],
                "source_stated",
            ),
            sources,
            if query.intent == "definition" {
                text(record, "dialogue_text_ja")
            } else {
                ""
            },
        ));
    }
    facts.truncate(limit);
    Ok(facts)
}
fn classification_fact(record: &Value, sources: Vec<Value>) -> Option<Value> {
    let mut labels = vec![];
    if let Some(label) = POLICY["_ENTITY_KIND_LABELS"][text(record, "entity_kind")].as_str() {
        labels.push(format!("分類単位は{label}"));
    }
    for (key, policy, label) in [
        ("expression_modes", "_EXPRESSION_MODE_LABELS", "表現様式"),
        (
            "formal_constraints",
            "_FORMAL_CONSTRAINT_LABELS",
            "形式上の区分",
        ),
    ] {
        let values: Vec<_> = rows(&record["classification"], key)
            .iter()
            .filter_map(|v| v.as_str().and_then(|s| POLICY[policy][s].as_str()))
            .collect();
        if !values.is_empty() {
            labels.push(format!("{label}は{}", values.join("・")));
        }
    }
    if labels.is_empty() {
        return None;
    }
    fact(
        record,
        &format!(
            "{}の{}です。",
            choose(record, &["title_ja"], "この詩"),
            labels.join("、")
        ),
        choose(record, &["classification_status"], "editorial_synthesis"),
        sources,
        "",
    )
}
fn kanji_facts(reader: &mut Reader<'_>, query: &Query, c: char) -> Result<Vec<Value>> {
    let joyo = reader.bulk()?.get(&format!("kanji.joyo.u{:x}", c as u32))?;
    let grade = reader
        .bulk()?
        .get(&format!("kanji.grade-allocation.u{:x}", c as u32))?;
    if query.intent == "grade" {
        let Some(record) = grade else {
            return Ok(vec![]);
        };
        let sources = reader.sources(&record)?;
        return Ok(fact(
            &record,
            &format!(
                "「{}」は{}{}の配当漢字です。",
                query.subject,
                text(&record, "school_stage_ja"),
                text(&record, "school_grade_ja")
            ),
            "official_normalized_extract",
            sources,
            "",
        )
        .into_iter()
        .collect());
    }
    let Some(record) = joyo else {
        return Ok(vec![]);
    };
    let mut by_type: Vec<(String, Vec<String>)> = vec![];
    for item in rows(&record, "readings") {
        let reading = text(item, "reading");
        if reading.is_empty() {
            continue;
        }
        let kind = choose(item, &["reading_type_ja"], "読み");
        if let Some((_, v)) = by_type.iter_mut().find(|(k, _)| k == kind) {
            v.push(reading.into());
        } else {
            by_type.push((kind.into(), vec![reading.into()]));
        }
    }
    if by_type.is_empty() {
        return Ok(vec![]);
    }
    let parts: Vec<_> = by_type
        .iter()
        .map(|(k, v)| format!("{k}は「{}」", v.join("・")))
        .collect();
    let sources = reader.sources(&record)?;
    Ok(fact(
        &record,
        &format!(
            "常用漢字表で「{}」の{}です。",
            query.subject,
            parts.join("、")
        ),
        "official_normalized_extract",
        sources,
        "",
    )
    .into_iter()
    .collect())
}
fn japanese(reader: &mut Reader<'_>, query: &Query, limit: usize) -> Result<Vec<Value>> {
    let subject = compact(&query.subject);
    if has(&POLICY["ambiguous"], &subject) {
        return Ok(vec![]);
    }
    if query.domain == "japanese_language"
        && let Some(id) = POLICY["_OFFICIAL_KANJI_TABLE_RECORDS"][&subject].as_str()
    {
        let Some(mut record) = reader.catalog()?.get(id)? else {
            return Ok(vec![]);
        };
        if record.get("dataset_id").is_none() {
            record["dataset_id"] = "japanese_language_education".into();
        }
        let sources = reader.sources(&record)?;
        let body = if id == "jp.bunka.joyo-kanji" {
            "「常用漢字表」は、平成22年内閣告示第2号の2,136字を収めた漢字表です。本データベースでは字種と音訓を検索できます。"
        } else {
            "「学年別漢字配当表」は、小学校第1学年から第6学年までの配当漢字1,026字を示す別表です。本データベースでは文字ごとの配当学年を検索できます。"
        };
        return Ok(
            fact(&record, body, "official_normalized_extract", sources, "")
                .into_iter()
                .collect(),
        );
    }
    if ["grade", "reading"].contains(&query.intent.as_str())
        && let Some(c) = cjk(&query.subject)
    {
        return kanji_facts(reader, query, c);
    }
    let mut records = if query.domain == "poetry" {
        let mut records = reader.japanese(&query.subject, &["japanese_poetry_forms"], limit)?;
        for hit in reader.catalog()?.search(
            &query.subject,
            &[("by_dataset".into(), vec!["world_poetry".into()])],
            limit,
        )? {
            if let Some(mut record) = reader.catalog()?.get(text(&hit, "id"))? {
                if record.get("dataset_id").is_none() {
                    record["dataset_id"] = "world_poetry".into();
                }
                records.push(record);
            }
        }
        records
    } else {
        let mut datasets = CORE.to_vec();
        if query.subject.starts_with(['～', '〜', '~']) || has(&POLICY["grammar_exact"], &subject)
        {
            datasets.push("grammar_patterns");
        }
        reader.japanese(&query.subject, &datasets, limit)?
    };
    records.retain(|r| match_rank(r, &query.subject).is_some());
    let exact: HashSet<_> = records
        .iter()
        .filter(|r| {
            text(r, "dataset_id") == "grammar_patterns" && compact(text(r, "title_ja")) == subject
        })
        .map(|r| compact(text(r, "title_ja")))
        .collect();
    if !exact.is_empty() {
        records.retain(|r| {
            text(r, "dataset_id") != "grammar_patterns"
                || exact.contains(&compact(text(r, "title_ja")))
        });
    }
    records.sort_by_key(|r| {
        (
            match_rank(r, &query.subject).unwrap_or(0),
            if query.intent == "classification" && text(r, "dataset_id") == "world_poetry" {
                0
            } else {
                1
            },
            text(r, "id").to_owned(),
        )
    });
    let (mut facts, mut seen) = (vec![], HashSet::new());
    for record in records {
        let candidates =
            if query.intent == "classification" && text(&record, "dataset_id") == "world_poetry" {
                classification_fact(&record, reader.sources(&record)?)
                    .into_iter()
                    .collect()
            } else {
                reference_facts(reader, &record, query, limit - facts.len())?
            };
        let has_candidates = !candidates.is_empty();
        for candidate in candidates {
            if seen.insert(compact(text(&candidate, "text_ja"))) {
                facts.push(candidate);
                if facts.len() >= limit {
                    return Ok(facts);
                }
            }
        }
        if has_candidates
            && ["definition", "classification", "reading"].contains(&query.intent.as_str())
            && text(&record, "dataset_id") != "grammar_patterns"
        {
            return Ok(facts);
        }
    }
    Ok(facts)
}

fn minecraft_sources(record: &Value) -> Vec<Value> {
    let version = text(record, "minecraft_version").trim_matches(space);
    let mut seen = HashSet::new();
    let mut out = vec![];
    for (index, source) in rows(record, "sources").iter().enumerate() {
        let kind = text(source, "source_kind");
        let url = text(source, "url");
        let path = text(source, "relative_path");
        let locator = choose(source, &["section", "json_pointer", "relative_path"], "");
        let (title, label) = match kind {
            "official_web_page" => (
                format!("Minecraft Java Edition {version} 公式リリースノート"),
                "Minecraft公式リリースノート",
            ),
            "official_artifact" => (
                format!("Minecraft Java Edition {version} 公式配布物"),
                "Minecraft公式配布物",
            ),
            _ => continue,
        };
        if !seen.insert((kind, url, path)) {
            continue;
        }
        out.push(json!({"source_id":format!("minecraft:{kind}:{index}"),"title_ja":title.trim_matches(space),"citation_label_ja":label,
            "locator":shorten(locator,120),"url":url,"source_kind":kind}));
    }
    out.truncate(3);
    out
}
fn minecraft_record(record: &Value, query: &Query, limit: usize) -> Vec<Value> {
    let sources = minecraft_sources(record);
    if sources.is_empty() {
        return vec![];
    }
    let version = text(record, "minecraft_version");
    let mut facts = vec![];
    if text(record, "record_type") == "official_change" {
        if ["change", "definition", "identifier"].contains(&query.intent.as_str()) {
            for mapping in rows(record, "identifier_mappings") {
                let old = text(mapping, "from");
                let new = text(mapping, "to");
                if ![compact(old), compact(new)].contains(&compact(&query.subject)) {
                    continue;
                }
                let inversion = if mapping["value_inverted"] == true {
                    "。値の意味も反転します"
                } else {
                    ""
                };
                facts.extend(fact(record,&format!("Minecraft Java Edition {version}では、旧名「{old}」は「{new}」に改名されました{inversion}。"),"source_stated",sources.clone(),""));
                break;
            }
        }
        if facts.is_empty() {
            let note = if text(record, "coverage") == "representative_selection" {
                "これは収録した主要変更の抜粋です。"
            } else {
                ""
            };
            facts.extend(fact(
                record,
                &format!(
                    "Minecraft Java Edition {version}では、{}{note}",
                    text(record, "summary_ja")
                ),
                choose(record, &["summary_method"], "editorial_paraphrase"),
                sources,
                "",
            ));
        }
        facts.truncate(limit);
        return facts;
    }
    let title = choose(record, &["title_ja"], &query.subject);
    let id = text(record, "entry_id");
    if query.intent == "identifier" && !id.is_empty() {
        return fact(
            record,
            &format!("Minecraft Java Edition {version}では、{title}の公式IDは「{id}」です。"),
            "official_artifact",
            sources,
            "",
        )
        .into_iter()
        .collect();
    }
    let item = &record["item_summary"];
    if query.intent == "properties" && item.is_object() {
        if (query.evidence.contains("耐久値") || query.subject.contains("耐久値"))
            && let Some(value) = item["max_damage"].as_i64()
        {
            facts.extend(fact(
                record,
                &format!(
                    "Minecraft Java Edition {version}では、{title}の最大耐久値は{value}です。"
                ),
                "official_artifact",
                sources.clone(),
                "",
            ));
        }
        if facts.is_empty() {
            let values: Vec<_> = [
                ("max_damage", "最大耐久値"),
                ("max_stack_size", "最大スタック数"),
            ]
            .iter()
            .filter_map(|(key, label)| item[*key].as_i64().map(|v| format!("{label}は{v}")))
            .collect();
            if !values.is_empty() {
                facts.extend(fact(
                    record,
                    &format!(
                        "Minecraft Java Edition {version}では、{title}は、{}です。",
                        values.join("、")
                    ),
                    "official_artifact",
                    sources.clone(),
                    "",
                ));
            }
        }
    }
    if facts.is_empty() && !id.is_empty() {
        facts.extend(fact(
            record,
            &format!(
                "Minecraft Java Edition {version}では、{title}は「{id}」として登録されています。"
            ),
            "official_artifact",
            sources,
            "",
        ));
    }
    facts.truncate(limit);
    facts
}
fn minecraft(
    reader: &super::minecraft::Minecraft,
    query: &Query,
    limit: usize,
) -> Result<Vec<Value>> {
    if query.intent == "rules" && query.evidence.contains("作り方") {
        static RESOURCE: LazyLock<regex::Regex> =
            LazyLock::new(|| regex::Regex::new(r"\A[a-z0-9_.-]+:[a-z0-9_./-]+\z").unwrap());
        let mut target = if RESOURCE.is_match(&query.subject) {
            query.subject.clone()
        } else {
            String::new()
        };
        if target.is_empty() {
            let records = reader.search(&query.subject, &["registry_entries"], &[], &[], 5)?;
            if let Some(r) = records
                .iter()
                .find(|r| match_rank(r, &query.subject).is_some())
            {
                target = text(r, "entry_id").into();
            }
        }
        if target.is_empty() {
            return Ok(vec![]);
        }
        for record in reader.search(&target, &["datapack_entries"], &["datapack_entry"], &[], 20)? {
            let is_recipe = text(&record, "id").contains(".recipe.")
                || rows(&record, "sources")
                    .iter()
                    .any(|r| text(r, "relative_path").contains("/recipe/"));
            if !is_recipe
                || text(&record, "registry_id") != "minecraft:recipe"
                || text(&record, "entry_id") != target
            {
                continue;
            }
            let summary = &record["document_summary"];
            let Some(references) = summary["referenced_resource_ids"].as_array() else {
                continue;
            };
            let declared = text(summary, "declared_type");
            let mut ingredients = vec![];
            for v in references {
                if let Some(s) = v.as_str()
                    && s != target
                    && s != declared
                {
                    push_unique(&mut ingredients, s);
                }
            }
            let label = POLICY["_RECIPE_TYPE_LABELS"][declared].as_str().unwrap_or(
                if declared.is_empty() {
                    "方式不明"
                } else {
                    declared
                },
            );
            let shown = if ingredients.is_empty() {
                "縮約データでは確認できません".into()
            } else {
                ingredients
                    .iter()
                    .take(4)
                    .map(String::as_str)
                    .collect::<Vec<_>>()
                    .join("・")
            };
            let omitted = if ingredients.len() > 4 {
                format!("（ほか{}件）", ingredients.len() - 4)
            } else {
                String::new()
            };
            let body = format!(
                "Minecraft Java Edition {}の公式レシピ定義では、{}は{label}で、素材参照は「{shown}」{omitted}です。配置の詳細は縮約データにないため推測しません。",
                text(&record, "minecraft_version"),
                query.subject
            );
            return Ok(fact(
                &record,
                &body,
                "official_artifact",
                minecraft_sources(&record),
                "",
            )
            .into_iter()
            .collect());
        }
        return Ok(vec![]);
    }
    let datasets: &[&str] = match query.intent.as_str() {
        "change" => &["official_changes"],
        "identifier" | "properties" => &["registry_entries"],
        "definition" => &["official_changes", "registry_entries"],
        _ => &[
            "official_changes",
            "registry_entries",
            "datapack_entries",
            "tag_definitions",
        ],
    };
    let mut facts = vec![];
    for record in reader.search(&query.subject, datasets, &[], &[], limit)? {
        if match_rank(&record, &query.subject).is_none() {
            continue;
        }
        facts.extend(minecraft_record(&record, query, limit - facts.len()));
        if facts.len() >= limit {
            break;
        }
    }
    Ok(facts)
}
static VERSIONS: LazyLock<Vec<regex::Regex>> = LazyLock::new(|| {
    POLICY["version_patterns"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| super::query::compile(v.as_str().unwrap(), false))
        .collect()
});
fn version_error(query: &Query) -> bool {
    let versions: HashSet<_> = VERSIONS
        .iter()
        .flat_map(|r| {
            r.captures_iter(&query.evidence)
                .map(|c| c.name("version").unwrap().as_str().to_owned())
        })
        .collect();
    !versions.is_empty() && (versions.len() != 1 || !versions.contains("1.21.11"))
}
fn lookup_inner(paths: &Paths, query: &Query, limit: usize, stop: Stop) -> Result<Vec<Value>> {
    stop.check()?;
    if query.domain == "minecraft" {
        let reader = super::minecraft::Minecraft::open(&paths.minecraft, &paths.source_lock, stop)?;
        minecraft(&reader, query, limit)
    } else {
        japanese(&mut Reader::new(&paths.language, stop), query, limit)
    }
}
pub fn lookup(paths: &Paths, query: &Query, limit: usize, stop: Stop) -> Value {
    let limit = limit.clamp(1, 3);
    let error = if query.domain == "japanese_language"
        && ["grade", "definition"].contains(&query.intent.as_str())
        && query.subject.len() == 1
        && query.subject.as_bytes()[0].is_ascii_digit()
    {
        "ambiguous_kanji_numeric_notation"
    } else if query.domain == "minecraft" && version_error(query) {
        "unsupported_minecraft_version"
    } else {
        ""
    };
    if !error.is_empty() {
        return json!({"query":query,"status":"not_found","facts":[],"error_code":error});
    }
    match lookup_inner(paths, query, limit, stop) {
        Ok(facts) => {
            json!({"query":query,"status":if facts.is_empty(){"not_found"}else{"found"},"facts":facts,"error_code":""})
        }
        Err(error) => {
            tracing::warn!(event="knowledge_lookup_failed",domain=query.domain,intent=query.intent,error=%error);
            json!({"query":query,"status":"unavailable","facts":[],"error_code":error_class(&error)})
        }
    }
}
pub async fn lookup_async(paths: Paths, query: Query) -> Result<Value> {
    let result = retrieval::lookup_worker(move |stop| lookup(&paths, &query, 3, stop)).await?;
    // 正本readerでも発話へ渡す前の閉じた型・出典の再検証は省略しない。
    let checked: super::Lookup = serde_json::from_value(result.clone())?;
    valid(checked.valid())?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn paths() -> Paths {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        Paths {
            language: retrieval::Paths {
                reference: root.join("reference/language_education_and_poetry"),
                cards: PathBuf::new(),
            },
            minecraft: root.join(".dogido_reference/minecraft_technical"),
            source_lock: root.join("reference/minecraft_technical/source_lock.json"),
        }
    }
    #[test]
    fn full_facts_sources_and_order_match_canonical_python() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("../../fixtures/knowledge-provider.json")).unwrap();
        for case in cases {
            let query: Query = serde_json::from_value(case["query"].clone()).unwrap();
            let actual = lookup(&paths(), &query, 3, Stop::default());
            assert_eq!(actual, case["expected"], "{:?}", query);
            let checked: super::super::Lookup = serde_json::from_value(actual).unwrap();
            assert!(checked.valid());
        }
    }
    #[test]
    fn missing_data_cannot_become_an_empty_successful_search() {
        let mut paths = paths();
        paths.language.reference = PathBuf::from("/missing-dogido-reference-data");
        let query = Query {
            domain: "japanese_language".into(),
            subject: "枕詞".into(),
            intent: "definition".into(),
            evidence: "枕詞って何？".into(),
        };
        let result = lookup(&paths, &query, 3, Stop::default());
        assert_eq!(result["status"], "unavailable");
        assert_eq!(result["error_code"], "FileNotFoundError");
    }
}
