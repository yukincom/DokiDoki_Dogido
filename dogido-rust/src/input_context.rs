//! Pure routing projection. Native state, reading saves and CAS retain authority.
use crate::{input_policy, knowledge::query, player_text::Prepared, recall_query};
use chrono::{DateTime, FixedOffset};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Context {
    pub raw_text: String,
    pub normalized_text: String,
    pub interpreted_text: String,
    pub breaks_silence: bool,
    pub wants_quiet: bool,
    pub should_block_ambient: bool,
    pub asks_hostile_count: bool,
    pub asks_hostile_direction: bool,
    pub asks_dragon_direction: bool,
    pub asks_save_last_haiku: bool,
    pub asks_inventory: bool,
    pub requests_sword: bool,
    pub assist_intent_source: String,
    pub assist_intent_evidence: String,
    pub assist_intent_confidence: f64,
    pub asks_about_sound: bool,
    pub knowledge_query: Option<query::Query>,
    pub player_haiku_text: Option<String>,
    pub revised_haiku_text: Option<String>,
    pub reading_correction: Option<input_policy::ReadingCorrection>,
    pub asks_haiku_recall: bool,
    pub haiku_recall_biome_hint: Option<String>,
    pub haiku_recall_query: Option<recall_query::InputRecall>,
}
impl Context {
    pub fn from_prepared(
        prepared: &Prepared,
        overlay: &[Value],
        now: DateTime<FixedOffset>,
    ) -> Self {
        Self::from_surfaces(
            &prepared.raw_text,
            &prepared.normalized_text,
            "",
            overlay,
            now,
        )
    }
    /// The raw text is used only for poem/reading extraction; normalized and
    /// interpreted surfaces have already been prepared by their respective owner.
    pub fn from_surfaces(
        raw: &str,
        normalized: &str,
        interpreted: &str,
        overlay: &[Value],
        now: DateTime<FixedOffset>,
    ) -> Self {
        let spoken = if normalized.is_empty() {
            raw
        } else {
            normalized
        };
        let semantic = if interpreted.is_empty() {
            spoken
        } else {
            interpreted
        };
        let mut context = Self {
            raw_text: spoken.into(),
            normalized_text: normalized.into(),
            interpreted_text: semantic.into(),
            breaks_silence: false,
            wants_quiet: false,
            should_block_ambient: false,
            asks_hostile_count: false,
            asks_hostile_direction: false,
            asks_dragon_direction: false,
            asks_save_last_haiku: false,
            asks_inventory: false,
            requests_sword: false,
            assist_intent_source: "none".into(),
            assist_intent_evidence: String::new(),
            assist_intent_confidence: 0.0,
            asks_about_sound: false,
            knowledge_query: None,
            player_haiku_text: None,
            revised_haiku_text: None,
            reading_correction: None,
            asks_haiku_recall: false,
            haiku_recall_biome_hint: None,
            haiku_recall_query: None,
        };
        // A slash command cannot trigger silence, extraction, recall or operation routing.
        if normalized.starts_with('/') {
            return context;
        }
        let p = input_policy::classify(raw, normalized);
        context.requests_sword =
            crate::assist::intent::is_explicit_select_sword_request(normalized);
        if context.requests_sword {
            context.assist_intent_source = "code".into();
            context.assist_intent_evidence = normalized.into();
            context.assist_intent_confidence = 1.0;
        } else {
            context.knowledge_query = query::extract(semantic);
        }
        context.breaks_silence = p.should_block_ambient;
        context.wants_quiet = p.wants_quiet;
        context.should_block_ambient = p.should_block_ambient;
        context.asks_hostile_count = p.asks_hostile_count;
        context.asks_hostile_direction = p.asks_hostile_direction;
        context.asks_dragon_direction = p.asks_dragon_direction;
        context.asks_save_last_haiku = p.asks_save_last_haiku;
        context.asks_inventory = p.asks_inventory;
        context.asks_about_sound = p.asks_about_sound;
        context.player_haiku_text = p.player_haiku_text;
        context.revised_haiku_text = p.revised_haiku_text;
        // Pronunciation registration belongs to the typed catalogue form, never
        // to an utterance (including text chat). Keep the wire field for helpers.
        context.haiku_recall_query =
            recall_query::context_from_normalized(normalized, overlay, now);
        context.asks_haiku_recall = context.haiku_recall_query.is_some();
        context.haiku_recall_biome_hint = context
            .haiku_recall_query
            .as_ref()
            .and_then(|q| q.biome_id.clone());
        context
    }
    pub fn general_conversation(&self) -> bool {
        !self
            .interpreted_text
            .trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
            .is_empty()
            && !self.normalized_text.starts_with('/')
            && !self.wants_quiet
            && !self.asks_hostile_count
            && !self.asks_hostile_direction
            && !self.asks_dragon_direction
            && !self.asks_save_last_haiku
            && !self.asks_inventory
            && !self.requests_sword
            && self.knowledge_query.is_none()
            && self.player_haiku_text.is_none()
            && self.revised_haiku_text.is_none()
            && self.reading_correction.is_none()
            && !self.asks_haiku_recall
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn conversation_cannot_register_pronunciation_even_with_explicit_reading_words() {
        for raw in [
            "何か音はするね",
            "読み: 草地=くさち",
            "草地の読みはくさち",
            "そうちじゃなくてくさち",
        ] {
            let c = Context::from_surfaces(
                raw,
                raw,
                "",
                &[],
                DateTime::parse_from_rfc3339("2026-09-30T00:00:00Z").unwrap(),
            );
            assert!(c.reading_correction.is_none(), "{raw}");
        }
    }
    #[test]
    fn all_context_fields_and_address_ownership_match_fixture() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("../fixtures/input-context.json")).unwrap();
        for case in cases {
            let actual = Context::from_surfaces(
                case["raw"].as_str().unwrap(),
                case["normalized"].as_str().unwrap(),
                case["interpreted"].as_str().unwrap(),
                case["overlay"].as_array().unwrap(),
                DateTime::parse_from_rfc3339(case["now"].as_str().unwrap()).unwrap(),
            );
            let mut expected = case["expected"].clone();
            expected["reading_correction"] = Value::Null;
            assert_eq!(
                serde_json::to_value(&actual).unwrap(),
                expected,
                "raw={:?}, normalized={:?}",
                case["raw"],
                case["normalized"]
            );
            let mut legacy = actual.clone();
            legacy.reading_correction =
                serde_json::from_value(case["expected"]["reading_correction"].clone()).unwrap();
            assert_eq!(
                legacy.general_conversation(),
                case["general"].as_bool().unwrap(),
                "raw={:?}",
                case["raw"]
            );
        }
    }
}
