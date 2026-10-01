//! Pure TTS preparation/residual replacements. Original display text stays caller-owned.
//! UniDic is deliberately outside this module; a caller can bypass its adapter for Ready.
pub mod tokens;
use serde::{Deserialize, Serialize};
use std::sync::LazyLock;
static REPLACEMENTS: LazyLock<Vec<(String, String)>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("tts_reading/replacements.json"))
        .expect("TTS replacement asset")
});
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Engine {
    Auto,
    Unidic,
    Off,
}
use crate::compat::is_python_whitespace as space;
/// Explicit engine (including an empty/invalid string) wins over the environment.
/// The caller supplies the environment snapshot; this pure function reads no process state.
pub fn resolve_engine(explicit: Option<&str>, environment: Option<&str>) -> Engine {
    match explicit
        .or(environment)
        .unwrap_or("auto")
        .trim_matches(space)
        .to_lowercase()
        .as_str()
    {
        "off" => Engine::Off,
        "unidic" => Engine::Unidic,
        _ => Engine::Auto,
    }
}
pub fn has_kanji(text: &str) -> bool {
    text.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c))
}
/// Preserve the canonical table's sequence, including compounds before the final 朝.
pub fn apply_manual(text: &str) -> String {
    let mut result = text.to_owned();
    for (surface, reading) in REPLACEMENTS.iter() {
        result = result.replace(surface, reading);
    }
    result
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step {
    Ready(String),
    /// Already stripped exactly once. Pass this value to the existing UniDic reader.
    NeedsUnidic {
        source: String,
    },
}
pub fn prepare(text: &str, engine: Option<&str>, environment: Option<&str>) -> Step {
    let source = text.trim_matches(space);
    if source.is_empty() {
        return Step::Ready(String::new());
    }
    if resolve_engine(engine, environment) == Engine::Off || !has_kanji(source) {
        Step::Ready(apply_manual(source))
    } else {
        Step::NeedsUnidic {
            source: source.into(),
        }
    }
}
/// None means unavailable/failed UniDic and retains the prepared original.
/// Cancellation, timeout and broken IPC are caller errors, not a None fallback.
/// A successful empty result is not a failure: Python also preserves it as empty.
/// Do not trim/NFKC/fold here, and do not apply catalog overlays to free-form TTS.
pub fn finish(source: &str, unidic_output: Option<&str>) -> String {
    apply_manual(unidic_output.unwrap_or(source))
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    #[test]
    fn pure_tts_boundary_matches_python() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("tts_reading/fixtures.json")).unwrap();
        for c in cases {
            let raw = c["text"].as_str().unwrap();
            let explicit = c["engine"].as_str();
            let environment = c["environment"].as_str();
            assert_eq!(
                serde_json::to_value(resolve_engine(explicit, environment)).unwrap(),
                c["resolved"],
                "{c}"
            );
            assert_eq!(apply_manual(raw), c["manual"], "{c}");
            let (actual, calls) = match prepare(raw, explicit, environment) {
                Step::Ready(s) => (s, 0),
                Step::NeedsUnidic { source } => {
                    assert_eq!(source, c["source"], "{c}");
                    (finish(&source, c["unidic_output"].as_str()), 1)
                }
            };
            assert_eq!(actual, c["expected"], "{c}");
            assert_eq!(calls, c["calls"], "{c}");
        }
    }
    #[test]
    fn display_and_successful_empty_reading_are_not_overwritten() {
        let raw = "\u{1c} 朝鮮の朝 \n".to_owned();
        assert_eq!(
            prepare(&raw, Some("off"), None),
            Step::Ready("ちょうせんのあさ".into())
        );
        assert_eq!(raw, "\u{1c} 朝鮮の朝 \n");
        assert_eq!(finish("朝", Some("")), "");
        assert_eq!(finish("朝", None), "あさ");
        assert_eq!(finish("朝", Some("  朝\n")), "  あさ\n");
        assert!(!has_kanji("㍻𠮷"));
    }
}
