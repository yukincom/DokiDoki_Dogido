//! プレイヤーが明示した句の想起条件。記憶の読込み・更新や世界の位置推定はしない。
use crate::haiku_memory::RecallQuery;
use chrono::{DateTime, Datelike, Duration, FixedOffset, Local, NaiveDate, TimeZone, Utc};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeSet, HashMap},
    sync::LazyLock,
};

#[derive(Deserialize)]
struct Entry {
    id: String,
    label: String,
    reading: String,
    group: String,
}
#[derive(Deserialize)]
struct Policy {
    entries: Vec<Entry>,
    groups: Vec<(Vec<String>, Vec<String>, String)>,
    recall_keywords: Vec<String>,
    digits: HashMap<char, u32>,
}
static POLICY: LazyLock<Policy> = LazyLock::new(|| {
    serde_json::from_str(include_str!("recall-query-policy.json")).expect("checked recall policy")
});
static MONTH: LazyLock<Regex> = LazyLock::new(|| date_pattern(false));
static DAY: LazyLock<Regex> = LazyLock::new(|| date_pattern(true));
fn date_pattern(day: bool) -> Regex {
    let digits: String = POLICY.digits.keys().collect();
    let month = format!(r"([{digits}]{{1,2}})[\s\u001c-\u001f]*月");
    let pattern = if day {
        format!(r"{month}[\s\u001c-\u001f]*([{digits}]{{1,2}})[\s\u001c-\u001f]*日")
    } else {
        month
    };
    Regex::new(&pattern).expect("checked recall date pattern")
}
fn number(s: &str) -> u32 {
    s.chars().fold(0, |v, c| v * 10 + POLICY.digits[&c])
}
use crate::compat::fold_kana as fold;
use crate::compat::is_python_whitespace as space;
fn contains_any(text: &str, values: &[&str]) -> bool {
    values.iter().any(|v| text.contains(v))
}

#[derive(Default)]
struct Place {
    biome_id: Option<String>,
    biome_ids: Vec<String>,
    group_ids: Vec<String>,
    label: Option<String>,
}
fn place(text: &str, overlay: &[Value]) -> Place {
    let raw = text.trim_matches(space);
    let folded = fold(raw);
    let mut labels: Vec<(String, &Entry)> = vec![];
    // Pythonのdictと同様、重複ラベルは最初の位置に残し、対応先だけ後勝ち。
    for entry in &POLICY.entries {
        let reading = overlay
            .iter()
            .rev()
            .find(|v| v["surface"].as_str().map(str::trim) == Some(entry.label.trim()))
            .and_then(|v| v["reading"].as_str())
            .filter(|s| !s.trim_matches(space).is_empty())
            .unwrap_or(&entry.reading);
        for hint in [
            entry.label.trim_matches(space).to_owned(),
            reading.trim_matches(space).to_owned(),
            entry.id.clone(),
            entry.id.replace('_', ""),
        ] {
            if hint.is_empty() {
                continue;
            }
            if let Some(row) = labels.iter_mut().find(|(label, _)| label == &hint) {
                row.1 = entry;
            } else {
                labels.push((hint, entry));
            }
        }
    }
    labels.sort_by_key(|(hint, _)| std::cmp::Reverse(hint.chars().count()));
    for (hint, entry) in labels {
        if hint.chars().count() >= 2 && (raw.contains(&hint) || folded.contains(&fold(&hint))) {
            return Place {
                biome_id: Some(entry.id.clone()),
                biome_ids: vec![entry.id.clone()],
                group_ids: vec![],
                label: Some(entry.label.clone()),
            };
        }
    }
    for (phrases, groups, label) in &POLICY.groups {
        if phrases
            .iter()
            .any(|p| raw.contains(p) || folded.contains(&fold(p)))
        {
            return Place {
                biome_id: None,
                biome_ids: POLICY
                    .entries
                    .iter()
                    .filter(|e| groups.contains(&e.group))
                    .map(|e| e.id.clone())
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect(),
                group_ids: groups
                    .iter()
                    .cloned()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect(),
                label: Some(label.clone()),
            };
        }
    }
    Place::default()
}
fn requested(folded: &str, place: &Place) -> bool {
    if POLICY.recall_keywords.iter().any(|k| folded.contains(k)) {
        return true;
    }
    if !folded.contains('句') && !folded.contains("川柳") {
        return false;
    }
    !place.biome_ids.is_empty()
        || MONTH.is_match(folded)
        || contains_any(
            folded,
            &[
                "思い出",
                "覚えて",
                "前に",
                "いつ",
                "どこで",
                "場所",
                "今月",
                "昨日",
                "今日",
                "ひと月",
                "一ヶ月",
                "1ヶ月",
                "ヶ月",
                "寒い",
                "暖かい",
                "あたたかい",
                "乾燥",
                "洞窟",
                "ネザー",
                "エンド",
                "海",
                "水辺",
                "氷雪",
                "冷帯",
                "温帯",
                "暖地",
            ],
        )
}
fn date(
    at: DateTime<FixedOffset>,
    year: i32,
    month: u32,
    day: u32,
) -> Option<DateTime<FixedOffset>> {
    let naive = NaiveDate::from_ymd_opt(year, month, day)?.and_hms_opt(0, 0, 0)?;
    at.offset().from_local_datetime(&naive).single()
}
fn range(
    text: &str,
    now: DateTime<FixedOffset>,
) -> Option<(DateTime<FixedOffset>, DateTime<FixedOffset>, String)> {
    let start = date(now, now.year(), now.month(), now.day())?;
    if contains_any(
        text,
        &[
            "ここひと月",
            "このひと月",
            "直近ひと月",
            "ここ一ヶ月",
            "ここ1ヶ月",
            "ここ一カ月",
        ],
    ) {
        return Some((now - Duration::days(30), now, "ここひと月".into()));
    }
    if text.contains("今月") {
        return Some((date(now, now.year(), now.month(), 1)?, now, "今月".into()));
    }
    if contains_any(text, &["今日", "きょう"]) {
        return Some((start, now, "今日".into()));
    }
    if contains_any(text, &["昨日", "きのう"]) {
        return Some((start - Duration::days(1), start, "昨日".into()));
    }
    if text.contains("先週") {
        return Some((now - Duration::days(7), now, "先週".into()));
    }
    if let Some(c) = DAY.captures(text) {
        let (month, day) = (number(&c[1]), number(&c[2]));
        let mut start = date(now, now.year(), month, day)?;
        if start > now {
            start = date(now, now.year() - 1, month, day)?;
        }
        return Some((
            start,
            start + Duration::days(1),
            format!("{month}月{day}日"),
        ));
    }
    if let Some(c) = MONTH.captures(text) {
        let month = number(&c[1]);
        let mut start = date(now, now.year(), month, 1)?;
        if start > now {
            start = date(now, now.year() - 1, month, 1)?;
        }
        let end = if month == 12 {
            date(now, start.year() + 1, 1, 1)?
        } else {
            date(now, start.year(), month + 1, 1)?
        };
        return Some((
            start,
            end.min(now + Duration::seconds(1)),
            format!("{month}月"),
        ));
    }
    None
}
/// 現在ホストの壁時計を使う。ゲーム内の時刻・バイオームは推測材料にしない。
pub fn parse(text: &str, overlay: &[Value], now: DateTime<Utc>) -> Option<RecallQuery> {
    parse_at(text, overlay, now.with_timezone(&Local).fixed_offset())
}
fn parse_at(text: &str, overlay: &[Value], now: DateTime<FixedOffset>) -> Option<RecallQuery> {
    let text = crate::player_text::normalize(text);
    context_from_normalized(&text, overlay, now).map(|query| RecallQuery {
        biome_id: query.biome_id,
        biome_ids: query.biome_ids,
        place_label: query.place_label,
        since: query.since.map(|t| t.with_timezone(&Utc)),
        until: query.until.map(|t| t.with_timezone(&Utc)),
        time_label: query.time_label,
    })
}

/// Input routing retains Python's group IDs and local date types. The storage
/// query above deliberately uses its existing UTC/expanded-biome contract.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InputRecall {
    pub biome_id: Option<String>,
    pub biome_ids: Vec<String>,
    pub group_ids: Vec<String>,
    pub place_label: Option<String>,
    pub since: Option<DateTime<FixedOffset>>,
    pub until: Option<DateTime<FixedOffset>>,
    pub time_label: Option<String>,
}
pub fn context_from_normalized(
    text: &str,
    overlay: &[Value],
    now: DateTime<FixedOffset>,
) -> Option<InputRecall> {
    let folded = fold(text);
    let place = place(text, overlay);
    if !requested(&folded, &place) {
        return None;
    }
    let (since, until, time_label) = match range(&folded, now) {
        Some((since, until, label)) => (Some(since), Some(until), Some(label)),
        None => (None, None, None),
    };
    Some(InputRecall {
        biome_id: place.biome_id,
        biome_ids: place.biome_ids,
        group_ids: place.group_ids,
        place_label: place.label,
        since,
        until,
        time_label,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn catalog_reading_overlay_and_calendar_match_python() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("../fixtures/recall-query.json")).unwrap();
        for case in cases {
            let now = DateTime::parse_from_rfc3339(case["now"].as_str().unwrap()).unwrap();
            let offset =
                FixedOffset::east_opt(case["offset_seconds"].as_i64().unwrap() as i32).unwrap();
            let actual = parse_at(
                case["text"].as_str().unwrap(),
                case["overlay"].as_array().unwrap(),
                now.with_timezone(&offset),
            );
            assert_eq!(
                serde_json::to_value(actual).unwrap(),
                case["expected"],
                "{}",
                case["text"]
            );
        }
    }
}
