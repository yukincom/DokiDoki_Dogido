//! ローカル正本DBの回答だけを、出典境界を保って表示・音声へ投影する。
//! 一般知識の検索は移行中のPython reader。限定国語のreaderはcatalog/retrieval。
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::hash::{DefaultHasher, Hash, Hasher};

pub mod catalog;
pub mod query;
pub mod retrieval;

const UNAVAILABLE: &str = "手元の公式資料を今は読めへんわ。推測では答えんとくで。";

#[derive(Debug, Deserialize)]
struct Query {
    domain: String,
    subject: String,
    intent: String,
    evidence: String,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq, Hash)]
pub struct Source {
    pub source_id: String,
    pub title_ja: String,
    pub citation_label_ja: String,
    pub locator: String,
    pub url: String,
    pub source_kind: String,
}

impl Source {
    /// 表示履歴内の同一出典を束ねるID。正本の認証・改竄検査には使わない。
    pub fn display_id(&self) -> String {
        let mut hash = DefaultHasher::new();
        self.hash(&mut hash);
        format!("ref_{:016x}", hash.finish())
    }

    pub(crate) fn valid(&self, domain: &str) -> bool {
        if !(required(&self.source_id, 240)
            && required(&self.title_ja, 300)
            && required(&self.citation_label_ja, 120)
            && length(&self.locator) <= 500
            && length(&self.url) <= 2048
            && required(&self.source_kind, 100))
        {
            return false;
        }
        if domain == "minecraft" {
            return self.source_id.starts_with("minecraft:official_")
                && matches!(
                    self.citation_label_ja.as_str(),
                    "Minecraft公式リリースノート" | "Minecraft公式配布物"
                )
                && match self.source_kind.as_str() {
                    "official_web_page" => self.url.starts_with("https://www.minecraft.net/"),
                    "official_artifact" => !self.locator.is_empty(),
                    _ => false,
                };
        }
        self.source_id.starts_with("src.")
            && self.url.starts_with("https://")
            && matches!(
                self.source_kind.as_str(),
                "organization_authored_or_issued"
                    | "bibliographic_or_authority_record"
                    | "public_institution_content_on_official_platform"
                    | "institution_published_scholarly_work"
            )
            && INSTITUTIONS.contains(&self.citation_label_ja.as_str())
    }
}

const INSTITUTIONS: &[&str] = &[
    "文部科学省",
    "文化庁",
    "国立教育政策研究所",
    "国立国会図書館",
    "国文学研究資料館",
    "国立国語研究所",
    "東京学芸大学",
    "Academy of American Poets",
    "Poetry Foundation",
    "UNESCO",
    "Encyclopaedia Iranica Foundation",
    "Columbia University",
    "Smithsonian Institution",
    "Korea.net",
    "Web Japan",
    "Pennsylvania State University",
    "Te Ara - The Encyclopedia of New Zealand",
    "奈良県",
    "奈良市",
    "桜井市",
    "東京外国語大学",
    "鹿児島大学",
    "京都大学",
    "東京医科歯科大学",
    "岩手大学",
    "琉球大学",
    "Unicode Consortium",
];

#[derive(Debug, Deserialize)]
struct Fact {
    record_id: String,
    dataset_id: String,
    title_ja: String,
    text_ja: String,
    claim_status: String,
    sources: Vec<Source>,
    dialogue_text_ja: String,
}

#[derive(Debug, Deserialize)]
struct Lookup {
    query: Query,
    status: String,
    facts: Vec<Fact>,
    error_code: String,
}

impl Lookup {
    fn valid(&self) -> bool {
        matches!(
            self.query.domain.as_str(),
            "japanese_language" | "poetry" | "minecraft"
        ) && matches!(
            self.query.intent.as_str(),
            "definition"
                | "rules"
                | "reading"
                | "grade"
                | "identifier"
                | "change"
                | "properties"
                | "classification"
        ) && required(&self.query.subject, 240)
            && required(&self.query.evidence, 240)
            && matches!(self.status.as_str(), "found" | "not_found" | "unavailable")
            && length(&self.error_code) <= 160
            && self.facts.len() <= 3
            && ((self.status == "found") != self.facts.is_empty())
            && self.facts.iter().all(|fact| {
                required(&fact.record_id, 300)
                    && required(&fact.dataset_id, 160)
                    && required(&fact.title_ja, 300)
                    && required(&fact.text_ja, 1000)
                    && length(&fact.dialogue_text_ja) <= 1000
                    && matches!(
                        fact.claim_status.as_str(),
                        "source_stated"
                            | "official_normalized_extract"
                            | "official_artifact"
                            | "editorial_synthesis"
                            | "editorial_guardrail"
                            | "editorial_paraphrase"
                    )
                    && (1..=3).contains(&fact.sources.len())
                    && fact
                        .sources
                        .iter()
                        .all(|source| source.valid(&self.query.domain))
            })
    }
}

#[derive(Debug, Serialize)]
pub struct Reply {
    pub text: String,
    pub speech_segments: Vec<String>,
    pub references: Vec<Source>,
    pub lookup_status: String,
}

fn length(text: &str) -> usize {
    text.chars().count()
}
fn required(text: &str, max: usize) -> bool {
    !text.trim().is_empty() && length(text) <= max && !text.contains('\0')
}
fn punctuation(c: char) -> bool {
    "。！？!?".contains(c)
}

fn reply(text: String, references: Vec<Source>, status: &str) -> Reply {
    let speech_segments = text
        .split_inclusive(punctuation)
        .map(str::to_owned)
        .collect();
    Reply {
        text,
        speech_segments,
        references,
        lookup_status: status.to_owned(),
    }
}

fn shorten(text: &str, max: usize) -> String {
    let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if length(&text) <= max {
        return text;
    }
    let mut kept = String::new();
    // Pythonの既存rendererと同じく、まず文の境界で短くする。
    for sentence in text.split_inclusive(punctuation) {
        let sentence = sentence.trim();
        if sentence.chars().all(punctuation) {
            continue;
        }
        if length(&kept) + length(sentence) > max {
            break;
        }
        kept.push_str(sentence);
    }
    if !kept.is_empty() {
        return kept.trim_end().to_owned();
    }
    let prefix: String = text.chars().take(max - 1).collect();
    format!("{}…", prefix.trim_end_matches(['、', '，', ' ']))
}

/// readerの外形不正・出典不正時も、LLMへ流さずコード固定の返事を返す。
pub fn render(value: &Value) -> Reply {
    let Ok(result) = serde_json::from_value::<Lookup>(value.clone()) else {
        return reply(UNAVAILABLE.into(), vec![], "invalid");
    };
    if !result.valid() {
        return reply(UNAVAILABLE.into(), vec![], "invalid");
    }
    let fixed = if result.status == "unavailable" {
        Some(UNAVAILABLE.to_owned())
    } else if result.error_code == "unsupported_minecraft_version" {
        Some("このMinecraft技術DBはJava Edition 1.21.11専用やで。別の版は推測せんとくで。".into())
    } else if result.error_code == "ambiguous_kanji_numeric_notation" {
        Some(format!(
            "「{}」だけやと、どの漢字か決められへんわ。「一二三の一」みたいに言うてみてな。",
            result.query.subject
        ))
    } else if result.status != "found" {
        Some("手元の公式資料では確認できへんかったわ。推測はせんとくで。".into())
    } else {
        None
    };
    if let Some(text) = fixed {
        return reply(text, vec![], &result.status);
    }

    let mut text = String::new();
    let mut references = Vec::new();
    for fact in result.facts {
        let mut body = shorten(
            if fact.dialogue_text_ja.is_empty() {
                &fact.text_ja
            } else {
                &fact.dialogue_text_ja
            },
            220,
        );
        if !body.is_empty() && !body.ends_with(punctuation) {
            body.push('。');
        }
        let prefix = if !fact.dialogue_text_ja.is_empty() {
            ""
        } else {
            match fact.claim_status.as_str() {
                "editorial_synthesis" => "資料を基に整理すると、",
                "editorial_guardrail" => "資料を基にした注意点として、",
                "editorial_paraphrase" => "公式リリースノートを要約すると、",
                _ => "",
            }
        };
        let part = format!("{prefix}{body}");
        if !text.is_empty() && length(&text) + length(&part) > 420 {
            let suffix = "ほかの項目は省略したで。";
            if length(&text) + length(suffix) <= 420 {
                text.push_str(suffix);
            }
            break;
        }
        text.push_str(&shorten(&part, 420));
        for source in fact.sources {
            if !references.contains(&source) {
                references.push(source);
            }
        }
    }
    reply(text, references, &result.status)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn lookup() -> Value {
        json!({"query":{"domain":"japanese_language","subject":"枕詞","intent":"definition","evidence":"枕詞って何？"},
            "status":"found","error_code":"","facts":[{"record_id":"r","dataset_id":"d","title_ja":"枕詞",
            "text_ja":"資料の説明。","dialogue_text_ja":"","claim_status":"editorial_synthesis",
            "sources":[{"source_id":"src.example","title_ja":"参考資料","citation_label_ja":"文部科学省",
            "locator":"1頁","url":"https://example.org/reference","source_kind":"organization_authored_or_issued"}]}]})
    }

    #[test]
    fn invalid_or_untrusted_facts_never_reach_speech() {
        for path in [
            "status",
            "facts.0.claim_status",
            "facts.0.sources.0.source_id",
            "facts.0.sources.0.url",
        ] {
            let mut value = lookup();
            let pointer = format!("/{}", path.replace('.', "/"));
            *value.pointer_mut(&pointer).unwrap() = "untrusted".into();
            let r = render(&value);
            assert_eq!(r.text, UNAVAILABLE);
            assert!(r.references.is_empty());
        }
        let mut value = lookup();
        value["facts"] = json!([]);
        assert_eq!(render(&value).lookup_status, "invalid");
    }

    #[test]
    fn sources_follow_only_spoken_facts_and_keep_locators_distinct() {
        let mut value = lookup();
        value["facts"][0]["dialogue_text_ja"] = "あ".repeat(190).into();
        let mut second = value["facts"][0].clone();
        second["sources"][0]["locator"] = "2頁".into();
        let mut third = second.clone();
        third["sources"][0]["locator"] = "3頁".into();
        value["facts"]
            .as_array_mut()
            .unwrap()
            .extend([second, third]);
        let r = render(&value);
        assert!(length(&r.text) <= 420);
        assert_eq!(r.references.len(), 2);
        assert_ne!(r.references[0].display_id(), r.references[1].display_id());
        assert_eq!(r.speech_segments.concat(), r.text);
        assert!(!r.text.contains("https"));
        assert!(!r.text.contains("資料を基に整理"));
        assert!(r.text.ends_with("ほかの項目は省略したで。"));
    }

    #[test]
    fn unicode_length_and_sentence_boundaries_match_existing_limits() {
        assert_eq!(shorten("短い。次の文は長すぎる。", 5), "短い。");
        assert_eq!(shorten("あいうえおかき", 5), "あいうえ…");
        assert_eq!(shorten("  あ\n  い  ", 5), "あ い");
    }
}
