//! 自動発句の出力に混ざった空行・折返しを、本文を補作せず三行へ整える。
//! 明示された三要素を優先し、要素数が崩れた場合だけ改行区切りから三行を回収する。
//! 読み・音数・採用・保存は呼出元が扱い、ここでは語句の意味を判定しない。
use serde_json::Value;

/// 一行の外側の空白・引用符と途中の改行を除く。残る文字の順序は保つ。
pub(super) fn one(text: &str) -> Option<String> {
    let clean = |s: &str| {
        s.trim_matches(crate::compat::is_dogido_whitespace)
            .trim_matches(['「', '」', '\"', '\'', ' '])
            .to_owned()
    };
    let text = text.split(['\n', '\r']).map(clean).collect::<String>();
    (!text.is_empty()).then_some(text)
}

/// 余分な空要素を除き、三つの行が一意に得られる場合だけ返す。
/// 三要素内の改行は折返しとして結合する。一〜二要素なら改行を行境界として回収する。
/// 二行しかない本文から三行目の言葉を作ったり、四行の一部を捨てたりはしない。
pub(super) fn three(payload: &Value) -> Option<Vec<String>> {
    let value = payload.get("lines")?;
    let rows: Vec<&str> = if let Some(text) = value.as_str() {
        vec![text]
    } else {
        value
            .as_array()?
            .iter()
            .map(Value::as_str)
            .collect::<Option<Vec<_>>>()?
    };
    let rows: Vec<_> = rows
        .into_iter()
        .filter(|text| one(text).is_some())
        .collect();
    if rows.len() == 3 {
        return rows.into_iter().map(one).collect();
    }
    let lines: Vec<_> = rows
        .into_iter()
        .flat_map(|text| text.split(['\n', '\r']))
        .filter_map(one)
        .collect();
    (lines.len() == 3).then_some(lines)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn empty_rows_and_line_wraps_preserve_the_three_original_lines() {
        let expected = ["さくらのは", "くろいおのへと", "あさのいろ"];
        for lines in [
            json!(["", "さくらのは", " ", "くろいおのへと", "あさのいろ", ""]),
            json!(["さくら\nのは", "くろい\r\nおのへと", "\nあさのいろ\n"]),
            json!(["さくらのは\n\nくろいおのへと\r\nあさのいろ"]),
            json!(["さくらのは\nくろいおのへと", "あさのいろ"]),
            json!("\nさくらのは\n\nくろいおのへと\nあさのいろ\n"),
        ] {
            assert_eq!(
                three(&json!({"lines":lines})),
                Some(expected.iter().map(|s| s.to_string()).collect())
            );
        }
    }

    #[test]
    fn completion_never_invents_or_discards_a_nonempty_line() {
        for lines in [
            json!(["さくらのは", "", "あさのいろ"]),
            json!(["一", "二", "三", "四"]),
            json!([]),
            json!([1, 2, 3]),
        ] {
            assert!(three(&json!({"lines":lines})).is_none());
        }
        assert_eq!(one("「まとまらん」"), Some("まとまらん".into()));
    }
}
