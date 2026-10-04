//! 現在句と保存済み材料だけを候補にする音声の音近傍補正。原文は変更しない。
use icu_normalizer::ComposingNormalizer;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Candidate {
    pub surface: String,
    pub readings: Vec<String>,
    pub source: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Correction {
    pub original: String,
    pub replacement: String,
    pub candidate_surface: String,
    pub candidate_source: String,
    pub distance: usize,
}

use crate::compat::is_python_whitespace as space;
fn stripped(s: &str) -> &str {
    s.trim_matches(space)
}
pub fn normalize_kana(text: &str) -> String {
    let normalized = ComposingNormalizer::new_nfkc().normalize(text);
    let mut chars = Vec::new();
    for c in normalized.chars() {
        let c = if ('ァ'..='ヶ').contains(&c) {
            char::from_u32(c as u32 - 0x60).unwrap()
        } else {
            c
        };
        let c = match c {
            'ぁ' => 'あ',
            'ぃ' => 'い',
            'ぅ' => 'う',
            'ぇ' => 'え',
            'ぉ' => 'お',
            'ゎ' => 'わ',
            other => other,
        };
        if c == 'ー' {
            // 先頭の長音には既定母音「あ」を補い、それ以外は直前のかなから決める。
            if let Some(c) = chars.last().copied().map_or(Some('あ'), last_vowel) {
                chars.push(c);
            }
        } else if ('ぁ'..='ゖ').contains(&c) {
            chars.push(c);
        }
    }
    chars.into_iter().collect()
}
fn last_vowel(c: char) -> Option<char> {
    [
        ('あ', "あかがさざただなはばぱまゃやらゎわ"),
        ('い', "いきぎしじちぢにひびぴみりゐ"),
        ('う', "うくぐすずつづぬふぶぷむゅゆるゔ"),
        ('え', "えけげせぜてでねへべぺめれゑ"),
        ('お', "おこごそぞとどのほぼぽもょよろを"),
    ]
    .into_iter()
    .find_map(|(v, group)| group.contains(c).then_some(v))
}

/// 入力／候補ごとの2種類のかな列を、左優先・非重複のbyte範囲で取り出す。
fn runs(text: &str, for_input: bool) -> Vec<(usize, usize)> {
    let chars: Vec<_> = text.char_indices().collect();
    let mut result = Vec::new();
    let mut start = 0;
    while start < chars.len() {
        let mut matched = None;
        let patterns = if for_input {
            [(true, 3), (false, 4)]
        } else {
            [(false, 1), (true, 1)]
        };
        for (katakana, minimum) in patterns {
            let mut end = start;
            while end < chars.len()
                && (chars[end].1 == 'ー'
                    || if katakana {
                        ('ァ'..='ヺ').contains(&chars[end].1)
                    } else {
                        ('ぁ'..='ゖ').contains(&chars[end].1)
                    })
            {
                end += 1;
            }
            if end - start >= minimum {
                matched = Some(end);
                break;
            }
        }
        if let Some(end) = matched {
            result.push((chars[start].0, chars.get(end).map_or(text.len(), |p| p.0)));
            start = end;
        } else {
            start += 1;
        }
    }
    result
}
fn distance(left: &[char], right: &[char]) -> usize {
    let mut previous: Vec<_> = (0..=right.len()).collect();
    for (i, l) in left.iter().enumerate() {
        let mut current = vec![i + 1];
        for (j, r) in right.iter().enumerate() {
            current.push(
                (current[j] + 1)
                    .min(previous[j + 1] + 1)
                    .min(previous[j] + usize::from(l != r)),
            );
        }
        previous = current;
    }
    previous[right.len()]
}
pub fn apply(text: &str, candidates: &[Candidate], maximum: usize) -> (String, Vec<Correction>) {
    let mut seen = HashSet::new();
    let mut prepared = Vec::new();
    for candidate in candidates {
        let surface = stripped(&candidate.surface);
        if surface.is_empty() {
            continue;
        }
        for raw in &candidate.readings {
            let reading = normalize_kana(raw);
            if reading.chars().count() >= 3 && seen.insert((surface, reading.clone())) {
                prepared.push((candidate, reading));
            }
        }
    }
    let mut selected = Vec::new();
    for (start, end) in runs(text, true) {
        if selected.len() >= maximum {
            break;
        }
        let heard = &text[start..end];
        let kana: Vec<_> = normalize_kana(heard).chars().collect();
        if kana.is_empty() {
            continue;
        }
        let mut best_distance = usize::MAX;
        let mut best = HashSet::new();
        for (candidate, reading) in &prepared {
            for suffix in [
                "", "や", "かな", "の", "が", "を", "に", "は", "も", "で", "と",
            ] {
                let target: Vec<_> = normalize_kana(&format!("{reading}{suffix}"))
                    .chars()
                    .collect();
                if target.len().abs_diff(kana.len()) > 2 {
                    continue;
                }
                let d = distance(&kana, &target);
                let limit = if target.len() < 4 {
                    0
                } else if target.len() >= 8 {
                    2
                } else {
                    1
                };
                let replacement = format!("{}{suffix}", candidate.surface);
                if d > limit || heard == replacement {
                    continue;
                }
                if d < best_distance {
                    best_distance = d;
                    best.clear();
                }
                if d == best_distance {
                    best.insert((
                        replacement,
                        candidate.surface.clone(),
                        candidate.source.clone(),
                    ));
                }
            }
        }
        if best.len() == 1 {
            let (replacement, candidate_surface, candidate_source) =
                best.into_iter().next().unwrap();
            selected.push((
                start,
                end,
                Correction {
                    original: heard.into(),
                    replacement,
                    candidate_surface,
                    candidate_source,
                    distance: best_distance,
                },
            ));
        }
    }
    let mut result = text.to_owned();
    for (start, end, correction) in selected.iter().rev() {
        result.replace_range(start..end, &correction.replacement);
    }
    (result, selected.into_iter().map(|(_, _, c)| c).collect())
}

fn field<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key].as_str().unwrap_or("")
}
fn add(rows: &mut Vec<Candidate>, surface: &str, readings: &[&str], source: &str) {
    let surface = stripped(surface);
    let mut unique = Vec::new();
    for reading in readings
        .iter()
        .map(|r| stripped(r))
        .filter(|r| !r.is_empty())
    {
        if !unique.contains(&reading.to_owned()) {
            unique.push(reading.to_owned());
        }
    }
    if !surface.is_empty() && !unique.is_empty() {
        rows.push(Candidate {
            surface: surface.into(),
            readings: unique,
            source: source.into(),
        });
    }
}
fn add_terms(rows: &mut Vec<Candidate>, surface: &str, source: &str) {
    for (start, end) in runs(surface, false) {
        let term = &surface[start..end];
        if normalize_kana(term).chars().count() >= 3 {
            add(rows, term, &[term], source);
        }
    }
}
fn verse_lines(text: &str) -> Vec<&str> {
    // str.splitlines と同じ行番号。CRLFは一つ、末尾の改行は空行を足さない。
    let mut result = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if "\n\r\u{b}\u{c}\u{1c}\u{1d}\u{1e}\u{85}\u{2028}\u{2029}".contains(c) {
            result.push(&text[start..i]);
            start = i + c.len_utf8();
            if c == '\r' && chars.peek().is_some_and(|(_, c)| *c == '\n') {
                start = chars.next().unwrap().0 + 1;
            }
        }
    }
    if start < text.len() {
        result.push(&text[start..]);
    }
    result
}
pub fn candidates(verse: &str, materials: &Value) -> Vec<Candidate> {
    let mut rows = Vec::new();
    for (i, line) in verse_lines(verse).iter().enumerate() {
        let line = stripped(line);
        let compact: Vec<_> = line
            .chars()
            .filter(|c| !(space(*c) || "／/|".contains(*c)))
            .collect();
        if !compact.is_empty()
            && compact
                .iter()
                .all(|c| ('ぁ'..='ヿ').contains(c) || *c == 'ー')
        {
            add(&mut rows, line, &[line], &format!("verse:{i}"));
        }
    }
    let mut catalog_readings = std::collections::HashMap::new();
    for (i, item) in materials["catalog_sources"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
    {
        let label = stripped(field(item, "label"));
        let reading = stripped(field(item, "reading"));
        let source = if field(item, "source_ref").is_empty() {
            format!("catalog:{i}")
        } else {
            field(item, "source_ref").to_owned()
        };
        if !label.is_empty() && !reading.is_empty() {
            catalog_readings.insert(source.clone(), reading);
            add(&mut rows, label, &[reading], &source);
        }
        add_terms(&mut rows, label, &source);
    }
    for (i, item) in materials["source_atoms"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
    {
        let text = stripped(field(item, "text"));
        let source = if field(item, "source_ref").is_empty() {
            format!("atom:{i}")
        } else {
            field(item, "source_ref").to_owned()
        };
        if field(item, "kind") == "catalog_label"
            && let Some(reading) = catalog_readings.get(&source)
        {
            add(&mut rows, text, &[reading], &source);
        }
        add_terms(&mut rows, text, &source);
    }
    let phase = stripped(field(materials, "time_phase")).to_lowercase();
    let phase_terms: &[(&str, &str)] = match phase.as_str() {
        "morning" => &[("朝", "あさ")],
        "day" => &[("昼", "ひる")],
        "evening" => &[("夕方", "ゆうがた"), ("夕暮れ", "ゆうぐれ")],
        "night" => &[("夜", "よる")],
        _ => &[],
    };
    for (surface, reading) in phase_terms {
        add(
            &mut rows,
            surface,
            &[reading],
            &format!("time_phase:{phase}"),
        );
    }
    for (i, item) in materials["asr_context_terms"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
    {
        let readings = if let Some(values) = item["readings"].as_array() {
            values.iter().filter_map(Value::as_str).collect()
        } else {
            vec![field(item, "reading")]
        };
        let source = if field(item, "source").is_empty() {
            format!("explicit:{i}")
        } else {
            field(item, "source").to_owned()
        };
        add(&mut rows, field(item, "surface"), &readings, &source);
    }
    let mut unique = HashSet::new();
    rows.retain(|c| unique.insert((c.surface.clone(), c.readings.clone())));
    rows
}

/// 現在の編集対象は未採用案があればその三行。候補をsession外へcacheしない。
pub fn for_workshop(text: &str, source: &str, workshop: &Value) -> (String, Vec<Correction>) {
    if !source.trim().eq_ignore_ascii_case("voice") || !workshop.is_object() {
        return (text.into(), vec![]);
    }
    let lines = if workshop["pending"]["lines"].is_array() {
        &workshop["pending"]["lines"]
    } else {
        &workshop["current_lines"]
    };
    let verse = lines
        .as_array()
        .into_iter()
        .flatten()
        .map(|l| field(l, "reading_text"))
        .collect::<Vec<_>>()
        .join("\n");
    apply(text, &candidates(&verse, &workshop["materials"]), 2)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn matches_python_candidates_normalization_and_corrections() {
        let fixture: Value =
            serde_json::from_str(include_str!("../fixtures/contextual-asr.json")).unwrap();
        for c in fixture["builders"].as_array().unwrap() {
            assert_eq!(
                json!(candidates(c["verse"].as_str().unwrap(), &c["materials"])),
                c["expected"],
                "{c}"
            );
        }
        for c in fixture["normalizations"].as_array().unwrap() {
            assert_eq!(
                normalize_kana(c["text"].as_str().unwrap()),
                c["expected"].as_str().unwrap(),
                "{c}"
            );
        }
        for c in fixture["corrections"].as_array().unwrap() {
            let candidates: Vec<Candidate> = serde_json::from_value(
                fixture["candidate_sets"][c["candidate_set"].as_u64().unwrap() as usize].clone(),
            )
            .unwrap();
            let (text, applied) = apply(
                c["text"].as_str().unwrap(),
                &candidates,
                c["maximum"].as_u64().unwrap() as usize,
            );
            assert_eq!(text, c["expected"].as_str().unwrap(), "{c}");
            assert_eq!(json!(applied), c["corrections"], "{c}");
        }
    }

    #[test]
    fn only_voice_and_current_editing_target_supply_candidates() {
        let mut workshop = json!({"current_lines":[{"reading_text":"さくらのは"}], "materials":{}});
        assert_eq!(
            for_workshop("サクラノハって何？", "voice", &workshop).0,
            "さくらのはって何？"
        );
        for source in ["typed", "text", "unknown", ""] {
            assert_eq!(
                for_workshop("サクラノハ", source, &workshop),
                ("サクラノハ".into(), vec![])
            );
        }
        workshop["pending"] = json!({"lines":[{"reading_text":"くろいおのへと"}]});
        assert_eq!(
            for_workshop("サクラノハ", "voice", &workshop),
            ("サクラノハ".into(), vec![])
        );
        assert_eq!(
            for_workshop("クロイオノヘト", "voice", &workshop).0,
            "くろいおのへと"
        );
        assert_eq!(
            for_workshop("クロイオノヘト", "voice", &Value::Null),
            ("クロイオノヘト".into(), vec![])
        );
        workshop["pending"] = Value::Null;
        assert_eq!(
            for_workshop("クロイオノヘト", "voice", &workshop),
            ("クロイオノヘト".into(), vec![])
        );
    }
}
