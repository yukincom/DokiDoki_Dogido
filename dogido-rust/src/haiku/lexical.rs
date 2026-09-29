//! Narrow lexical transforms. Only a line's validated source atoms may correct
//! a one-character typo; other catalog names never become automatic edits.
use super::{LineForm, SourceAtom};
use crate::knowledge::query::{alphanumeric, fold, nfkc, space};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet},
    sync::LazyLock,
};

static KANA_RUN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[ぁ-ゖー]+|[ァ-ヺー]+").unwrap());
pub fn hiragana(text: &str) -> String {
    text.chars()
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .collect()
}
pub fn signature(text: &str) -> String {
    hiragana(&fold(&nfkc(text)))
        .chars()
        .filter(|c| alphanumeric(*c) || ('ぁ'..='ゖ').contains(c) || ('一'..='鿿').contains(c))
        .collect()
}
pub fn form(text: String) -> LineForm {
    LineForm {
        signature: signature(&text),
        text,
    }
}
#[derive(Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Correction {
    pub original: String,
    pub corrected: String,
    pub source_atom_id: String,
}
pub fn correct(text: &str, atom_ids: &[String], source_atoms: &[SourceAtom]) -> Option<Correction> {
    let compact: String = text.chars().filter(|c| !space(*c)).collect();
    let normalized: Vec<char> = hiragana(&compact).chars().collect();
    let by_id: HashMap<_, _> = source_atoms
        .iter()
        .map(|a| (a.atom_id.as_str(), a))
        .collect();
    let mut seen = HashSet::new();
    let mut terms = vec![];
    for id in atom_ids {
        let Some(atom) = by_id.get(id.as_str()).filter(|a| a.kind == "catalog_label") else {
            continue;
        };
        for raw in KANA_RUN.find_iter(&atom.text) {
            let term: Vec<char> = hiragana(raw.as_str()).chars().collect();
            if term.len() < 4
                || normalized.windows(term.len()).any(|w| w == term)
                || !seen.insert(term.clone())
            {
                continue;
            }
            terms.push((term, atom.atom_id.as_str()));
        }
    }
    let mut suggestions = HashSet::new();
    for (term, id) in terms {
        for (start, fragment) in normalized.windows(term.len()).enumerate() {
            if fragment.iter().zip(&term).filter(|(a, b)| a != b).count() == 1 {
                suggestions.insert((start, start + term.len(), term.clone(), id));
                if suggestions.len() > 1 {
                    return None;
                }
            }
        }
    }
    let (start, end, replacement, id) = suggestions.into_iter().next()?;
    let original_chars: Vec<char> = compact.chars().collect();
    let corrected = original_chars[..start]
        .iter()
        .chain(&replacement)
        .chain(&original_chars[end..])
        .collect();
    Some(Correction {
        original: text.into(),
        corrected,
        source_atom_id: id.into(),
    })
}
/// Exact old post-normalization rule: strip whitespace and outer quote chars,
/// but retain the original line if a dictionary produces empty or multiline text.
pub fn normalized_form(source: &str, dictionary: Option<&str>) -> LineForm {
    let normalized = dictionary
        .unwrap_or(source)
        .trim_matches(space)
        .trim_matches(['「', '」', '"', '\'', ' ']);
    form(
        if !normalized.is_empty() && !normalized.contains(['\n', '\r']) {
            normalized.into()
        } else {
            source.into()
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    #[test]
    fn canonical_grounded_typo_and_signature_cases_match() {
        let data: Value = serde_json::from_str(include_str!("lexical-fixtures.json")).unwrap();
        for case in data["corrections"].as_array().unwrap() {
            let ids: Vec<String> = serde_json::from_value(case["atom_ids"].clone()).unwrap();
            let atoms: Vec<SourceAtom> =
                serde_json::from_value(case["source_atoms"].clone()).unwrap();
            let expected: Option<Correction> =
                serde_json::from_value(case["expected"].clone()).unwrap();
            assert_eq!(
                correct(case["text"].as_str().unwrap(), &ids, &atoms),
                expected,
                "{case}"
            );
        }
        for case in data["signatures"].as_array().unwrap() {
            assert_eq!(
                signature(case[0].as_str().unwrap()),
                case[1].as_str().unwrap(),
                "{case}"
            );
        }
        for case in data["normalized"].as_array().unwrap() {
            assert_eq!(
                normalized_form(case[0].as_str().unwrap(), case[1].as_str()),
                serde_json::from_value(case[2].clone()).unwrap(),
                "{case}"
            );
        }
        let tokens: Value =
            serde_json::from_str(include_str!("../tts_reading/token-fixtures.json")).unwrap();
        for case in data["neutral"].as_array().unwrap() {
            let row = &tokens[case[0].as_u64().unwrap() as usize];
            let words: Vec<crate::tts_reading::tokens::Token> =
                serde_json::from_value(row["tokens"].clone()).unwrap();
            assert_eq!(
                crate::tts_reading::tokens::neutral(&words),
                case[1].as_str().unwrap(),
                "{row}"
            );
        }
    }
}
