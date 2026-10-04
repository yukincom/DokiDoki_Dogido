//! プロンプト・カタログ用の値の文字列化、JSON整形、モデル返答のコード囲み除去。
//! Scalar text keeps None/True/False and unquoted strings. Containers retain
//! their caller's established format; changing that also changes search terms.
use serde::Serialize;
use serde_json::{
    Value,
    ser::{CompactFormatter, Formatter},
};
use std::io;

#[derive(Clone, Copy)]
pub(crate) enum ContainerFormat {
    CompactJson,
    SpacedJson,
    QuotedRepr,
}

/// Convert scalar values to None/True/False or unquoted text.
/// The caller explicitly chooses the established container format.
pub(crate) fn value_text(value: &Value, containers: ContainerFormat) -> String {
    match (value, containers) {
        (Value::Null, _) => "None".into(),
        (Value::Bool(true), _) => "True".into(),
        (Value::Bool(false), _) => "False".into(),
        (Value::String(s), _) => s.clone(),
        (Value::Number(n), _) => n.to_string(),
        (value, ContainerFormat::CompactJson) => value.to_string(),
        (value, ContainerFormat::SpacedJson) => spaced_json(value),
        (Value::Array(a), ContainerFormat::QuotedRepr) => {
            format!(
                "[{}]",
                a.iter().map(quoted_repr).collect::<Vec<_>>().join(", ")
            )
        }
        (Value::Object(o), ContainerFormat::QuotedRepr) => format!(
            "{{{}}}",
            o.iter()
                .map(|(k, v)| format!("{}: {}", quote_string(k), quoted_repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn quoted_repr(value: &Value) -> String {
    match value {
        Value::String(s) => quote_string(s),
        _ => value_text(value, ContainerFormat::QuotedRepr),
    }
}

fn quote_string(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if c.is_control() => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

// Only separators differ from serde_json's compact formatter. Numeric spelling,
// Unicode and insertion order must remain unchanged, including at size limits.
struct SpacedJsonFormatter;
impl Formatter for SpacedJsonFormatter {
    fn begin_array_value<W: ?Sized + io::Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }
    fn begin_object_key<W: ?Sized + io::Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }
    fn begin_object_value<W: ?Sized + io::Write>(&mut self, writer: &mut W) -> io::Result<()> {
        writer.write_all(b": ")
    }
    fn write_f64<W: ?Sized + io::Write>(&mut self, writer: &mut W, value: f64) -> io::Result<()> {
        CompactFormatter.write_f64(writer, value)
    }
}

/// JSON with a space after each comma/colon, also used by payload size checks.
/// Unlike value text, scalar null/bools stay JSON and strings are quoted.
pub(crate) fn spaced_json<T: Serialize + ?Sized>(value: &T) -> String {
    let mut output = Vec::new();
    value
        .serialize(&mut serde_json::Serializer::with_formatter(
            &mut output,
            SpacedJsonFormatter,
        ))
        .expect("JSON value serializes");
    String::from_utf8(output).expect("JSON is UTF-8")
}

/// 前後の空白と、先頭・末尾が揃ったコード囲みだけを除く。
/// 閉じていない囲みや本文の壊れは修復せず、呼出側のJSON検査へ渡す。
pub(crate) fn strip_code_fence(text: &str) -> String {
    use crate::compat::is_dogido_whitespace as is_space;

    let text = text.trim_matches(is_space);
    if !text.starts_with("```") {
        return text.into();
    }
    // モデル返答のコード囲みは、LF/CR以外のUnicode改行・C0改行でも行境界として外す。
    let lines: Vec<_> = text
        .split([
            '\n', '\r', '\u{000b}', '\u{000c}', '\u{001c}', '\u{001d}', '\u{001e}', '\u{0085}',
            '\u{2028}', '\u{2029}',
        ])
        .collect();
    if lines.len() >= 2
        && lines
            .last()
            .is_some_and(|line| line.trim_matches(is_space) == "```")
    {
        return lines[1..lines.len() - 1]
            .join("\n")
            .trim_matches(is_space)
            .into();
    }
    text.into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const FORMATS: [ContainerFormat; 3] = [
        ContainerFormat::CompactJson,
        ContainerFormat::SpacedJson,
        ContainerFormat::QuotedRepr,
    ];

    #[test]
    fn scalar_text_is_shared_but_json_remains_json() {
        for (value, text, json_text) in [
            (Value::Null, "None", "null"),
            (json!(true), "True", "true"),
            (json!(false), "False", "false"),
            (json!("猫\n'\"\\"), "猫\n'\"\\", "\"猫\\n'\\\"\\\\\""),
        ] {
            for format in FORMATS {
                assert_eq!(value_text(&value, format), text);
            }
            assert_eq!(spaced_json(&value), json_text);
        }
    }

    #[test]
    fn container_formats_preserve_nested_values_and_insertion_order() {
        let value: Value =
            serde_json::from_str(r#"{"z":[true,null,"猫"],"a":{"q":false}}"#).unwrap();
        assert_eq!(
            value_text(&value, ContainerFormat::CompactJson),
            r#"{"z":[true,null,"猫"],"a":{"q":false}}"#
        );
        assert_eq!(
            value_text(&value, ContainerFormat::SpacedJson),
            r#"{"z": [true, null, "猫"], "a": {"q": false}}"#
        );
        assert_eq!(
            spaced_json(&value),
            r#"{"z": [true, null, "猫"], "a": {"q": false}}"#
        );
        assert_eq!(
            value_text(&value, ContainerFormat::QuotedRepr),
            "{'z': [True, None, '猫'], 'a': {'q': False}}"
        );
        for (value, expected) in [(json!([]), "[]"), (json!({}), "{}")] {
            for format in FORMATS {
                assert_eq!(value_text(&value, format), expected);
            }
            assert_eq!(spaced_json(&value), expected);
        }
    }

    #[test]
    fn repr_quotes_keys_and_nested_strings_but_not_scalar_strings() {
        for (text, expected) in [
            ("plain", "'plain'"),
            ("can't", "\"can't\""),
            ("a\"b", "'a\"b'"),
            ("a'\"b", "'a\\'\"b'"),
            ("\\\n\r\t", "'\\\\\\n\\r\\t'"),
            ("\0\u{1b}\u{7f}\u{85}", "'\\x00\\x1b\\x7f\\x85'"),
            ("猫🐕\u{2028}", "'猫🐕\u{2028}'"),
        ] {
            assert_eq!(value_text(&json!(text), ContainerFormat::QuotedRepr), text);
            assert_eq!(
                value_text(&json!([text]), ContainerFormat::QuotedRepr),
                format!("[{expected}]")
            );
            let mut object = serde_json::Map::new();
            object.insert(text.into(), json!([text]));
            assert_eq!(
                value_text(&Value::Object(object), ContainerFormat::QuotedRepr),
                format!("{{{expected}: [{expected}]}}")
            );
        }
    }

    #[test]
    fn numbers_preserve_integer_precision_and_float_spelling() {
        for (value, expected) in [
            (json!(i64::MIN), "-9223372036854775808"),
            (json!(u64::MAX), "18446744073709551615"),
            (json!(0), "0"),
            (json!(0.0), "0.0"),
            (json!(-0.0), "-0.0"),
            (json!(1.25), "1.25"),
            (json!(1.0e100), "1e+100"),
            (json!(1.0e-100), "1e-100"),
        ] {
            for format in FORMATS {
                assert_eq!(value_text(&value, format), expected);
                assert_eq!(
                    value_text(&json!([&value]), format),
                    format!("[{expected}]")
                );
            }
            assert_eq!(spaced_json(&value), expected);
            assert_eq!(spaced_json(&json!([&value])), format!("[{expected}]"));
        }
    }
}
