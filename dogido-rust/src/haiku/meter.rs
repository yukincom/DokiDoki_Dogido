//! 川柳生成の行候補について、文字種・音数（モーラ数）および明示的な禁止語を検査する。
//! `count_japanese_sounds` は読み文字列のモーラ数を計測し、`line_failure_reasons` は生成時の許容幅（5/7/5 各±1音）に基づいて不合格理由を判定する。
//! 川柳WSでは本モジュールを編集の可否判定に使わず、音数照会のための計数だけを利用する。
use serde_json::{Map, Value};
pub const TARGETS: [usize; 3] = [5, 7, 5];
// 通常のUnicode空白に加えてC0制御文字（区切り文字）も除外し、入力経路による音数計算のブレを防ぐ。
use crate::compat::is_dogido_whitespace as space;
/// 空白を除外した読みの音数（モーラ数）を計測する。
/// 「ゃゅょ」などの拗音・小書き文字は直前の1音に含めて数え、先頭に単独で現れた場合のみ単独の1音として扱う。
/// 促音「っ」、撥音「ん」、長音「ー」はそれぞれ1音として数える。
/// 漢字からの読み変換は行わず、空白以外の記号も通常の1文字と同様にカウントする（文字種の妥当性は呼び出し元で検査する）。
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
/// 行インデックスおよび空行のバリデーションを最初に行い、改行混入、不正文字種、五十音の羅列（プレースホルダ）、音数過不足、明示的禁止語に該当する場合に不合格理由コードを返す。
/// 禁止語の照合は、かな変換と空白除去を行った正規化文字列による部分一致で行う（`allowed_terms` の強制使用や soft lesson の順守は課さない）。
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
