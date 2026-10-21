//! 入力の表記正規化。保存・操作の原文は別に保持する。
use serde::{Deserialize, Serialize};

const REPLACEMENTS: &[(&str, &str)] = &[
    ("かんあつばん", "感圧板"),
    ("カンアツバン", "感圧板"),
    ("関圧番", "感圧板"),
    ("管轄版", "感圧板"),
    ("間月版", "感圧板"),
    ("貨物板", "感圧板"),
    ("感圧版", "感圧板"),
];

use crate::compat::is_dogido_whitespace as space;
fn whitespace(text: &str) -> String {
    text.split(space)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Prepared {
    pub raw_text: String,
    pub normalized_text: String,
    pub applied_fixes: Vec<(String, String)>,
}

pub fn prepare(text: &str) -> Prepared {
    let (normalized_text, applied_fixes) = normalize_with_fixes(text);
    Prepared {
        raw_text: text.into(),
        normalized_text,
        applied_fixes,
    }
}

fn normalize_with_fixes(text: &str) -> (String, Vec<(String, String)>) {
    let mut normalized_text = whitespace(text);
    let mut applied_fixes = Vec::new();
    for (from, to) in REPLACEMENTS {
        if normalized_text.contains(from) {
            normalized_text = normalized_text.replace(from, to);
            applied_fixes.push(((*from).into(), (*to).into()));
        }
    }
    (normalized_text, applied_fixes)
}

pub fn normalize(text: &str) -> String {
    normalize_with_fixes(text).0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn normalization_matches_fixture() {
        let cases: Vec<Prepared> =
            serde_json::from_str(include_str!("../fixtures/player-text.json")).unwrap();
        for expected in cases {
            assert_eq!(prepare(&expected.raw_text), expected);
        }
    }
    #[test]
    fn normalization_preserves_raw_poem_and_does_not_apply_contextual_candidates() {
        let raw = "直し: 桜の葉\n黒い斧へと\n朝の色";
        assert_eq!(prepare(raw).raw_text, raw);
        assert_eq!(normalize("　関圧番\x1c ある？\n"), "感圧板 ある？");
        assert_eq!(normalize("環圧板"), "環圧板");
        assert_eq!(normalize("県に持ち替えて"), "県に持ち替えて");
        assert_eq!(normalize("ｶﾝｱﾂﾊﾞﾝ"), "ｶﾝｱﾂﾊﾞﾝ");
    }
}
