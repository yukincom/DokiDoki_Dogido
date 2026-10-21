//! 抽出済みの明示知識質問を、詩・Minecraftの正本資料へ照合して回答事実を組み立てる。
//! 正常経路はQueryの事前確認→分野と意図に合う資料検索→対象の一致確認→出典付き事実の抽出。
//! 対象語を生成で増やさず、登録済みの題名・別名・読み・検索語と正規化して照合する。
//! 未収録はnot_found、読込・検証・取消の失敗はunavailableへ分け、一般知識の生成で穴埋めしない。
//! ファイル読取りを所有し、発話生成・会話状態・保存・世界操作は呼出側へ残す。
use super::{
    catalog::{Stop, error_class, valid},
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
/// 検索に使う正本資料の所在。詩資料とMinecraftの収録データ・出典固定情報をまとめる。
/// パスの基準は起動時に選んだ補助スクリプトと同じcheckoutで、質問文から組み立てない。
pub struct Paths {
    pub poetry: retrieval::Paths,
    pub minecraft: PathBuf,
    pub source_lock: PathBuf,
}
impl Paths {
    /// 起動に使用するhelperの実パスからcheckoutを確定し、各資料の既定配置を求める。
    /// この段階はパス解決だけで、資料の読込み・検証は検索時に行う。
    pub fn from_helper(helper: &Path) -> Result<Self> {
        let poetry = retrieval::Paths::from_helper(helper)?;
        let root = poetry.reference.parent().unwrap().parent().unwrap();
        Ok(Self {
            minecraft: root.join(".dogido_reference/minecraft_technical"),
            source_lock: root.join("reference/minecraft_technical/source_lock.json"),
            poetry,
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
/// 対象の完全一致照合用に表記を正規化し、case foldと空白除去を行う。
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
/// 本文と出典が両方そろうときだけ、共通の回答事実を一件作る。
/// 本文・会話用説明を220文字以内へ整え、収録recordとclaim_statusの対応を保持する。
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
/// 正規化後の完全一致を、題名/entry_id→別名/読み→検索語→record IDの順に順位付けする。
/// 小さい値ほど優先。Noneは対象一致なしで、検索indexの部分一致候補を回答へ昇格させない。
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
/// 出典を取得できたrecordから、質問意図に合う事実を上限まで抽出する。
/// rulesは規則文を先に扱い、その後に定義または要約を補う。
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
/// 世界の詩形recordに収録された分類軸を日本語ラベルへ写し、編集上の分類根拠と出典を付ける。
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
/// 詩形・詩の用語を検索し、完全一致したrecordから出典付き事実を返す。
fn poetry(reader: &mut Reader<'_>, query: &Query, limit: usize) -> Result<Vec<Value>> {
    if has(&POLICY["ambiguous"], &compact(&query.subject)) {
        return Ok(vec![]);
    }
    let mut records = reader.poetry(&query.subject, limit)?;
    // 下位readerの検索は部分一致も拾うため、回答へ使う段階で対象の完全一致を必須にする。
    records.retain(|r| match_rank(r, &query.subject).is_some());
    // 一致順位が先。同順位の分類質問だけworld_poetryを優先し、最後はrecord IDで順序を固定する。
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
        // 定義・分類・読みは一つのrecordから事実を得たら確定し、別recordの説明を混ぜない。
        if has_candidates
            && ["definition", "classification", "reading"].contains(&query.intent.as_str())
        {
            return Ok(facts);
        }
    }
    Ok(facts)
}

/// 収録された公式Webページ・公式配布物だけを回答の出典表示へ変換する。
/// 同じ種類/URL/パスを重複除去し、版と所在を含む参照を最大3件残す。
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
/// Minecraft recordを質問意図へ対応させ、改名・公式ID・item属性などの回答事実を作る。
/// 専用情報がない場合は収録済み変更要約または登録IDへ狭め、出典のない事実は返さない。
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
/// 質問意図からMinecraftの検索datasetを選び、対象が完全一致した収録recordを回答へ写す。
/// 作り方だけは名称から公式IDを確定し、そのIDのレシピ定義を二段階で探す。
/// レシピの縮約資料から返せるのは方式と素材参照で、配置手順を補作しない。
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
        // 名称から登録IDを特定できないときは、似た名前のレシピを代わりに返さない。
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
            // 縮約資料に含まれる参照IDを素材候補として示す。個数や格子配置はこの資料にはない。
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
/// 発話evidence中に版があれば、収録対象の1.21.11一種類だけかを確認する。
/// 版指定なしは通し、別版または複数版の質問は対応版の回答で代用しない。
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
/// 取消を確認し、Minecraft用readerまたは詩用readerへ検索を渡す。
/// 取得不能と未収録を区別するため、readerのエラーは空配列へ変換せず呼出側へ返す。
fn lookup_inner(paths: &Paths, query: &Query, limit: usize, stop: Stop) -> Result<Vec<Value>> {
    stop.check()?;
    if query.domain == "minecraft" {
        let reader = super::minecraft::Minecraft::open(&paths.minecraft, &paths.source_lock, stop)?;
        minecraft(&reader, query, limit)
    } else if query.domain == "poetry" {
        poetry(&mut Reader::new(&paths.poetry, stop), query, limit)
    } else {
        Ok(vec![])
    }
}
/// Queryから回答事実1〜3件と状態を返す同期入口。
/// 未対応Minecraft版はnot_found、読込等の失敗はunavailable。
/// 成功時でも事実0件ならnot_foundとなり、検索成功だけではfoundにしない。
pub fn lookup(paths: &Paths, query: &Query, limit: usize, stop: Stop) -> Value {
    let limit = limit.clamp(1, 3);
    let error = if query.domain == "minecraft" && version_error(query) {
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
/// ファイル検索を取消対応の専用workerで実行し、返却形と出典の契約を再検査する。
/// 検査済みのJSONを返すまでが担当で、ここから発話生成や会話保存は行わない。
pub async fn lookup_async(paths: Paths, query: Query) -> Result<Value> {
    let result = retrieval::lookup_worker(move |stop| lookup(&paths, &query, 3, stop)).await?;
    // 検索経路にかかわらず、発話へ渡す最終形をLookupの閉じた型と出典規則へ揃える。
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
            poetry: retrieval::Paths {
                reference: root.join("reference/language_education_and_poetry"),
            },
            minecraft: root.join(".dogido_reference/minecraft_technical"),
            source_lock: root.join("reference/minecraft_technical/source_lock.json"),
        }
    }
    #[test]
    // 移植時に確定したfixtureと事実・出典・順序を照合する回帰試験。旧Python本体は起動しない。
    fn full_facts_sources_and_order_match_fixture() {
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
        paths.poetry.reference = PathBuf::from("/missing-dogido-reference-data");
        let query = Query {
            domain: "poetry".into(),
            subject: "枕詞".into(),
            intent: "definition".into(),
            evidence: "枕詞って何？".into(),
        };
        let result = lookup(&paths, &query, 3, Stop::default());
        assert_eq!(result["status"], "unavailable");
        assert_eq!(result["error_code"], "FileNotFoundError");
    }
}
