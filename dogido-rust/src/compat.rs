//! Narrow compatibility primitives shared by the migrated Python contracts.
//! Callers still own normalization order, punctuation, lookup and coercion policy.
use serde_json::Value;

/// Python str.isspace()/re \s includes these four C0 separators in addition to
/// Rust's Unicode whitespace. Do not substitute str::trim for this predicate.
pub(crate) fn is_python_whitespace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Fold only U+30A1..=U+30F6. No NFKC, case, whitespace or long-vowel changes.
pub(crate) fn fold_kana(text: &str) -> String {
    text.chars()
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).expect("hiragana code point")
            } else {
                c
            }
        })
        .collect()
}

/// Python truthiness for the finite JSON value domain. This does not coerce text.
pub(crate) fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn whitespace_preserves_python_c0_and_unicode_boundaries() {
        for c in [
            '\t', '\n', '\r', ' ', '\u{1c}', '\u{1d}', '\u{1e}', '\u{1f}', '\u{85}', '\u{a0}', '　',
        ] {
            assert!(is_python_whitespace(c), "{c:?}");
        }
        for c in ['\u{1b}', '\u{7f}', '\u{200b}', '\u{feff}', 'あ'] {
            assert!(!is_python_whitespace(c), "{c:?}");
        }
    }

    #[test]
    fn kana_fold_does_not_apply_other_normalization() {
        assert_eq!(
            fold_kana("ァヶヷｶﾞ A ー カ\u{3099}"),
            "ぁゖヷｶﾞ A ー か\u{3099}"
        );
        assert_eq!(fold_kana("\u{30a0}\u{30f7}"), "\u{30a0}\u{30f7}");
    }

    #[test]
    fn truthiness_keeps_empty_and_nonempty_json_distinct() {
        for value in [
            json!(null),
            json!(false),
            json!(0),
            json!(-0.0),
            json!(""),
            json!([]),
            json!({}),
        ] {
            assert!(!json_truthy(&value), "{value}");
        }
        for value in [
            json!(true),
            json!(-1),
            json!(0.5),
            json!("false"),
            json!(" "),
            json!([null]),
            json!({"key":false}),
        ] {
            assert!(json_truthy(&value), "{value}");
        }
    }
}
