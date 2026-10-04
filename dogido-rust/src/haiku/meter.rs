//! 川柳生成の行候補について、文字種・音数と明示的な禁止語を検査する。
//! count_japanese_soundsは読み文字列を数え、line_failure_reasonsは生成の許容幅5/7/5各±1で理由を返す。
//! 本人の局所編集が要求する厳密な5/7/5は、編集側が同じ計数関数を使って別に検証する。
use serde_json::{Map, Value};
pub const TARGETS: [usize; 3] = [5, 7, 5];
// 通常のUnicode空白に加えてC0の区切り文字も除き、入力経路による音数の差を避ける。
use crate::compat::is_python_whitespace as space;
/// 空白を除いた読みを数える。ゃゅょ等の指定小書き文字は前の一音に付き、先頭に単独で現れた場合は一音とする。
/// 促音っ・撥音ん・長音ーは各一音。漢字から読みを推測せず、空白以外の記号も通常文字と同様に数える。
/// 文字種を受け入れてよいかは、line_failure_reasonsなどの利用側が別に検査する。
pub fn count_japanese_sounds(text: &str) -> usize {
    text.chars()
        .filter(|c| !space(*c))
        .enumerate()
        .map(|(i, c)| usize::from(i == 0 || !"ゃゅょャュョぁぃぅぇぉァィゥェォゎヮ".contains(c)))
        .sum()
}
/// 語句照合のために空白を除き、カタカナをひらがなへ揃える。漢字の読み変換は行わない。
pub fn normalize_term(text: &str) -> String {
    text.chars()
        .filter(|c| !space(*c))
        .map(|c| {
            if ('\u{30a1}'..='\u{30f6}').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .collect()
}
/// 行番号・空行を先に確認し、改行、文字種、五十音列、音数、明示的な禁止語の不合格理由を返す。
/// 禁止語はかなと空白を正規化した部分一致で検査する。allowed_termsの必須使用やsoft lessonの遵守は要求しない。
pub fn line_failure_reasons(
    text: &str,
    line_index: usize,
    details: &Map<String, Value>,
) -> Vec<String> {
    if line_index > 2 {
        return vec!["invalid_line_index".into()];
    }
    if text.is_empty() {
        return vec!["empty_line".into()];
    }
    let mut reasons = Vec::new();
    if text.contains(['\n', '\r']) {
        reasons.push("multiline");
    }
    if !text.chars().all(|c| {
        ('\u{3041}'..='\u{309f}').contains(&c)
            || ('\u{30a1}'..='\u{30ff}').contains(&c)
            || space(c)
            || "ー／/|".contains(c)
    }) {
        reasons.push("invalid_script");
    }
    let compact: String = text.chars().filter(|c| !space(*c)).collect();
    if [
        "あいうえお",
        "かきくけこ",
        "さしすせそ",
        "たちつてと",
        "なにぬねの",
        "はひふへほ",
        "まみむめも",
        "やゆよ",
        "らりるれろ",
        "アイウエオ",
        "カキクケコ",
        "サシスセソ",
        "タチツテト",
        "ナニヌネノ",
        "ハヒフヘホ",
        "マミムメモ",
        "ヤユヨ",
        "ラリルレロ",
    ]
    .iter()
    .any(|s| compact.contains(s))
    {
        reasons.push("gibberish_sequence");
    }
    let count = count_japanese_sounds(text);
    let target = TARGETS[line_index];
    if count < target - 1 {
        reasons.push("meter_too_short");
    } else if count > target + 1 {
        reasons.push("meter_too_long");
    }
    if let Some(terms) = details
        .get("haiku_constraints")
        .and_then(|v| v.get("forbidden_terms"))
        .and_then(Value::as_array)
    {
        let normalized = normalize_term(text);
        if terms
            .iter()
            .filter(|v| super::generation::truthy(v))
            .map(|v| {
                normalize_term(
                    v.as_str()
                        .map(str::to_owned)
                        .unwrap_or_else(|| v.to_string())
                        .as_str(),
                )
            })
            .any(|t| !t.is_empty() && normalized.contains(&t))
        {
            reasons.push("hard_forbidden_term");
        }
    }
    reasons.into_iter().map(str::to_owned).collect()
}
