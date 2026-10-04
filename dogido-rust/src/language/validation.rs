//! 国語対話の厳密な外形・発話引用・文脈対象・確定かなの検査。
//! モデルの再試行、未知語の読み推定、世界観測や記憶への昇格はしない。
use icu_normalizer::ComposingNormalizer;
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::State;

const GRADE_CONFIRMATION: &str = "それって、漢字を習う学年のこと？";

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Evidence {
    turn_id: String,
    quote: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Interpretation {
    dialogue_act: String,
    question: String,
    target: String,
    facet: String,
    topic: String,
    relation: String,
    target_status: String,
    alternatives: Vec<String>,
    evidence: Vec<Evidence>,
    search_terms: Vec<String>,
    clarification: String,
    #[serde(default)]
    lookup_requested: bool,
    #[serde(default)]
    web_query: String,
}

impl Interpretation {
    fn valid(&self) -> bool {
        ["information_request", "casual", "other"].contains(&self.dialogue_act.as_str())
            && [
                "grade",
                "mora_count",
                "reading",
                "meaning",
                "spelling",
                "grammar",
                "usage",
                "etymology",
                "translation",
                "comparison",
                "classification",
                "other",
            ]
            .contains(&self.facet.as_str())
            && ["language", "minecraft", "general", "unclear"].contains(&self.topic.as_str())
            && ["new", "continue", "correct", "switch", "end", "resume"]
                .contains(&self.relation.as_str())
            && ["explicit", "contextual", "ambiguous"].contains(&self.target_status.as_str())
            && length(&self.question, 0, 500)
            && length(&self.target, 0, 100)
            && length(&self.clarification, 0, 160)
            && length(&self.web_query, 0, 140)
            && self.alternatives.len() <= 3
            && self.search_terms.len() <= 4
            && self.evidence.len() <= 4
            && self.evidence.iter().all(|e| length(&e.quote, 1, 500))
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct GroundedReply {
    status: String,
    text: String,
    fact_ids: Vec<String>,
    application: String,
    missing: String,
    #[serde(default = "no_missing")]
    missing_kind: String,
    #[serde(default)]
    clarification: String,
}
fn no_missing() -> String {
    "none".into()
}
impl GroundedReply {
    fn valid(&self) -> bool {
        ["answer", "partial", "unsupported"].contains(&self.status.as_str())
            && ["none", "evidence", "context"].contains(&self.missing_kind.as_str())
            && length(&self.text, 1, 420)
            && self.fact_ids.len() <= 6
            && length(&self.application, 0, 300)
            && length(&self.missing, 0, 200)
            && length(&self.clarification, 0, 160)
    }
}

fn length(text: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&text.chars().count())
}
fn nfkc(text: &str) -> String {
    ComposingNormalizer::new_nfkc().normalize(text).into_owned()
}
fn string<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or("")
}
fn parse<T: DeserializeOwned>(generated: &Value) -> Option<T> {
    if matches!(
        generated["finish_reason"].as_str(),
        Some("length" | "max_tokens" | "MAX_TOKENS")
    ) {
        return None;
    }
    let mut object = crate::haiku_response::extract_object(string(generated, "text"), true)?;
    object.remove("__dogido_status");
    serde_json::from_value(Value::Object(object)).ok()
}

fn information_request(text: &str) -> bool {
    let text = nfkc(text);
    [
        "?",
        "？",
        "何",
        "なに",
        "いつ",
        "どこ",
        "誰",
        "だれ",
        "なぜ",
        "なんで",
        "どういう",
        "教えて",
        "ってどんな",
        "ってどう",
        "って何",
        "漢字",
        "短歌",
        "俳句",
        "川柳",
        "音数",
        "学年",
        "読み",
        "意味",
    ]
    .iter()
    .any(|t| text.contains(t))
}
fn mentions_writing(text: &str) -> bool {
    if ["漢字", "文字", "かんじ"].iter().any(|t| text.contains(t)) {
        return true;
    }
    let mut previous = None;
    for c in text.chars() {
        if c == '字' && previous != Some('数') {
            return true;
        }
        previous = Some(c);
    }
    false
}

/// Returns the former helper's checked frame, now exclusively built in Rust.
pub(super) fn interpretation(generated: &Value, details: &Value, state: &State) -> Value {
    let current = &details["current"];
    let mut output = json!({"payload":null, "information_request":information_request(string(current,"text")),
        "invalid_normal_chat":false, "computed_fact":null});
    let Some(mut i) = parse::<Interpretation>(generated).filter(Interpretation::valid) else {
        return output;
    };
    let mut turns: Vec<_> = details["history"]
        .as_array()
        .into_iter()
        .flatten()
        .collect();
    turns.push(current);
    let current_id = string(current, "turn_id");
    let mut valid = !i.evidence.is_empty()
        && i.evidence.iter().any(|e| e.turn_id == current_id)
        && i.evidence.iter().all(|e| {
            turns
                .iter()
                .rev()
                .find(|t| string(t, "turn_id") == e.turn_id)
                .is_some_and(|t| nfkc(string(t, "text")).contains(&nfkc(&e.quote)))
        });
    let historical = i.evidence.iter().any(|e| e.turn_id != current_id);
    if i.dialogue_act == "information_request" && i.target_status == "contextual" {
        let known = !i.target.is_empty()
            && turns
                .iter()
                .any(|t| nfkc(string(t, "text")).contains(&nfkc(&i.target)));
        let switching = i.facet == "other" && ["switch", "end"].contains(&i.relation.as_str());
        valid &= known || switching || historical;
    }
    if !valid {
        output["invalid_normal_chat"] = ["casual", "other"]
            .contains(&i.dialogue_act.as_str())
            .into();
        return output;
    }
    if i.facet != "other" {
        i.topic = "language".into();
    }
    if i.facet == "grade" {
        let continued = i.target_status == "contextual"
            && ["continue", "resume", "correct"].contains(&i.relation.as_str())
            && (state.kanji_scope_confirmed
                || details["focus"]["clarification"] == GRADE_CONFIRMATION
                || i.evidence
                    .iter()
                    .any(|e| e.turn_id != current_id && mentions_writing(&e.quote)));
        if !mentions_writing(string(current, "text")) && !continued {
            i.target_status = "ambiguous".into();
            i.clarification = GRADE_CONFIRMATION.into();
            i.alternatives = vec!["漢字の配当学年".into(), "漢字以外の学習".into()];
        }
    }
    if i.facet == "mora_count" && i.target_status != "ambiguous" {
        output["computed_fact"] =
            count_explicit_kana(&i.target, &i.evidence).unwrap_or(Value::Null);
        if output["computed_fact"].is_null() {
            i.target_status = "ambiguous".into();
            i.clarification = "数えたい言葉の読みを、ひらがなかカタカナで教えてくれる？".into();
        }
    }
    output["payload"] = serde_json::to_value(i).expect("interpretation is serializable");
    output
}

fn count_explicit_kana(target: &str, evidence: &[Evidence]) -> Option<Value> {
    let surface = nfkc(target);
    let surface =
        surface.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c));
    if surface.is_empty() || !evidence.iter().any(|e| nfkc(&e.quote).contains(surface)) {
        return None;
    }
    let reading: String = surface
        .chars()
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .collect();
    if !reading
        .chars()
        .all(|c| ('ぁ'..='ゔ').contains(&c) || c == 'ー')
        || reading.contains(['ゐ', 'ゑ', 'ゎ'])
        || reading.starts_with(|c| "ぁぃぅぇぉゃゅょゎー".contains(c))
    {
        return None;
    }
    let value = crate::haiku::meter::count_japanese_sounds(&reading);
    let hash = format!("{:x}", Sha256::digest(reading.as_bytes()));
    Some(
        json!({"id":format!("calculation:mora:{}", &hash[..16]), "title_ja":"確定かなの音数",
        "text_ja":format!("『{surface}』は{value}音。確定読み『{reading}』を既存の拍計数で計算。"),
        "claim_status":"verified_calculation", "sources":[],
        "calculation":{"operation":"mora_count","surface":surface,"reading":reading,"value":value}}),
    )
}

pub(super) fn generated_reply(generated: &Value) -> Value {
    parse::<GroundedReply>(generated)
        .filter(GroundedReply::valid)
        .map(|r| serde_json::to_value(r).expect("reply is serializable"))
        .unwrap_or(Value::Null)
}
/// Fixed table/calculation replies also cross the same strict shape boundary.
pub(super) fn reply_shape(reply: &Value) -> bool {
    serde_json::from_value::<GroundedReply>(reply.clone()).is_ok_and(|r| r.valid())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn checkpoint_interpretation_and_reply_match_fixture() {
        let fixtures: Value =
            serde_json::from_str(include_str!("../../fixtures/language-validation.json")).unwrap();
        for case in fixtures["interpretations"].as_array().unwrap() {
            let c = &case["command"];
            let state = serde_json::from_value(c["state"].clone()).unwrap();
            assert_eq!(
                interpretation(&c["generated"], &c["details"], &state),
                case["expected"],
                "{}",
                case["name"]
            );
        }
        for case in fixtures["replies"].as_array().unwrap() {
            let reply = generated_reply(&case["generated"]);
            assert_eq!(reply, case["expected"], "{}", case["name"]);
            assert_eq!(reply_shape(&reply), !reply.is_null(), "{}", case["name"]);
        }
    }
}
