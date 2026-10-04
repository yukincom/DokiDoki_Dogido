//! Optional reactions use shared dialogue and observed situations.
//! Code still owns event detection, immediate warnings, priorities and fallback.
use crate::text_format::{self, ContainerFormat::CompactJson};
mod prompts;
pub(crate) mod sanitize;
use crate::types::{GeneratedText, GenerationRequest};
use anyhow::{Context, Result, ensure};
use serde_json::Value;

pub const KINDS: [&str; 20] = [
    "death",
    "aftermath",
    "daylight_water",
    "daylight_water_skeleton",
    "newly_burning_visual",
    "deep_dark_ominous_sound",
    "occluded_hostile_presence",
    "ambient",
    "weather_transition",
    "thunder_reaction",
    "ender_eye_throw",
    "structure_entry",
    "light_source_gain",
    "darkness_escape",
    "occluded_entry_with_light",
    "occluded_entry_no_light",
    "dark_push_no_light",
    "dark_push_after_breath",
    "emergency_shelter_relief",
    "portal_appearance",
];

/// The environment renderer shares the same editable situation source.
pub(crate) fn context(kind: &str, details: &Value) -> Result<Value> {
    prompts::context(kind, details)
}

/// Model speech may intentionally be silent; fixed cues remain separate actions.
pub(crate) fn is_model_reaction(action: &crate::combat::model::Speech) -> bool {
    action.leaf.as_ref().is_some_and(|leaf| {
        KINDS.contains(&leaf.kind.as_str()) || leaf.kind == crate::environment::reaction::KIND
    })
}

pub struct Leaf {
    pub request: GenerationRequest,
    details: Value,
    fallback: String,
    suffix: String,
}
impl Leaf {
    pub fn prepare(input: &Value, model: &str, max_tokens: u64) -> Result<Self> {
        let kind = input["kind"].as_str().context("reaction leaf kind")?;
        ensure!(KINDS.contains(&kind), "unexpected reaction leaf");
        ensure!(
            input["model"] == model && input["max_tokens"] == max_tokens,
            "reaction route mismatch"
        );
        let mut details = input["details"]
            .as_object()
            .context("reaction details")?
            .clone();
        details.remove("__ambient_guard");
        let suffix = details
            .remove("__speech_suffix")
            .map_or_else(String::new, |v| text_format::value_text(&v, CompactJson));
        let mut fallback = input["fallback_text"]
            .as_str()
            .context("reaction fallback")?
            .to_owned();
        if !suffix.is_empty()
            && let Some(base) = fallback.strip_suffix(&suffix)
        {
            fallback = base.trim_end().to_owned();
        }
        let details = Value::Object(details);
        let request = GenerationRequest {
            schema_version: 1,
            kind: kind.into(),
            model: model.into(),
            messages: prompts::messages(kind, &details)?,
            temperature: input["temperature"]
                .as_f64()
                .context("reaction temperature")?,
            max_tokens,
            enable_thinking: false,
        };
        request.validate()?;
        Ok(Self {
            request,
            details,
            fallback,
            suffix,
        })
    }
    /// One generation only. Explicit silence is a successful choice, not fallback.
    pub fn finish(&self, generated: Option<&GeneratedText>) -> (String, &'static str) {
        let fallback = |reason| (self.fallback.clone() + &self.suffix, reason);
        let Some(generated) = generated else {
            return fallback("generation_error");
        };
        if generated.finish_reason.as_deref() == Some("length") {
            return fallback("truncated_output");
        }
        match crate::speech_choice::parse(&generated.text) {
            Ok(crate::speech_choice::Choice::Silent) => (String::new(), "silent"),
            Ok(crate::speech_choice::Choice::Speak(text)) => {
                if sanitize::forbidden(&text, &self.details) {
                    fallback("unsafe_advice")
                } else if sanitize::final_guard(&self.request.kind, &text, &self.details) {
                    fallback("claim_conflict")
                } else {
                    (text + &self.suffix, "speak")
                }
            }
            Err(reason) => fallback(reason),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn fixture() -> Value {
        let v: Value = serde_json::from_str(include_str!("reaction_leaf/fixtures.json")).unwrap();
        let get = |i: &Value| v["pool"][i.as_u64().unwrap() as usize].clone();
        json!({"prompts":v["prompts"].as_array().unwrap().iter().map(|r|json!({"kind":r[0],"details":get(&r[1]),"messages":get(&r[2])})).collect::<Vec<_>>(),
            "sanitizers":v["sanitizers"].as_array().unwrap().iter().map(|r|json!({"kind":r[0],"details":get(&r[1]),"raw":get(&r[2]),"cleaned":get(&r[3]),"usable":r[4],"style":r[5],"guard":r[6],"selected":get(&r[7])})).collect::<Vec<_>>()})
    }
    #[test]
    fn prompts_match_python_for_all_kinds_and_branches() {
        for row in fixture()["prompts"].as_array().unwrap() {
            let kind = row["kind"].as_str().unwrap();
            let actual =
                serde_json::to_value(prompts::messages(kind, &row["details"]).unwrap()).unwrap();
            assert_eq!(actual, row["messages"], "{kind} {}", row["details"]);
        }
    }
    #[test]
    fn cleaner_usable_style_and_final_guards_match_python() {
        for row in fixture()["sanitizers"].as_array().unwrap() {
            let kind = row["kind"].as_str().unwrap();
            let raw = row["raw"].as_str().unwrap();
            let d = &row["details"];
            let c = sanitize::clean(raw);
            assert_eq!(c, row["cleaned"], "clean {row}");
            assert_eq!(sanitize::usable(&c, d), row["usable"], "usable {row}");
            assert_eq!(sanitize::style(kind, &c, d), row["style"], "style {row}");
            assert_eq!(
                sanitize::final_guard(kind, &c, d),
                row["guard"],
                "guard {row}"
            );
        }
    }
    fn leaf(kind: &str, details: Value) -> Leaf {
        Leaf::prepare(&json!({"kind":kind,"details":details,"temperature":0.65,
            "model":"fixture-model","max_tokens":72,"fallback_text":"ひとまず落ち着いたな。 後ろも気になるわ。"}), "fixture-model",72).unwrap()
    }
    fn generated(raw: &str) -> GeneratedText {
        serde_json::from_value(json!({"text":raw,"finish_reason":"stop"})).unwrap()
    }
    #[test]
    fn explicit_silence_bypasses_suffix_and_invalid_contracts_keep_fallback() {
        let leaf = leaf(
            "aftermath",
            json!({"combat_outcome":"disengaged","__speech_suffix":" 後ろも気になるわ。"}),
        );
        let silent = generated(r#"{"action":"silent","speech":""}"#);
        assert_eq!(leaf.finish(Some(&silent)), (String::new(), "silent"));
        for raw in [
            "",
            "助かったわ。",
            r#"{"action":"silent","speech":"話すで"}"#,
            r#"{"action":"silent","speech":"","extra":1}"#,
            r#"{"action":"silent","speech":"""#,
        ] {
            let result = leaf.finish(Some(&generated(raw)));
            assert_eq!(result.0, "ひとまず落ち着いたな。 後ろも気になるわ。");
            assert_ne!(result.1, "silent");
        }
        let mut truncated = silent;
        truncated.finish_reason = Some("length".into());
        assert_eq!(leaf.finish(Some(&truncated)).1, "truncated_output");
        assert_eq!(leaf.finish(None).1, "generation_error");
        let spoken = generated(r#"{"action":"speak","speech":"ひと息つこか。"}"#);
        assert_eq!(
            leaf.finish(Some(&spoken)),
            ("ひと息つこか。 後ろも気になるわ。".into(), "speak")
        );
        for (body, expected) in [
            (
                r#"{"action":"silent","speech":""}"#,
                (String::new(), "silent"),
            ),
            (
                spoken.text.as_str(),
                ("ひと息つこか。 後ろも気になるわ。".into(), "speak"),
            ),
        ] {
            let fenced = generated(&format!("```json\n{body}\n```"));
            assert_eq!(leaf.finish(Some(&fenced)), expected);
        }
    }
    #[test]
    fn reactions_do_not_impose_old_emotions_or_vocabulary_but_keep_observed_outcomes() {
        for (kind, text) in [
            ("newly_burning_visual", "めっちゃ燃えてるな。"),
            ("darkness_escape", "明るい場所へ戻ろか。"),
            (
                "daylight_water_skeleton",
                "水辺は涼しそうやけど、こっちは落ち着かんな。",
            ),
            ("aftermath", "ちょっと回復しよか。"),
        ] {
            let leaf = leaf(kind, json!({"combat_outcome":"disengaged"}));
            let reply = generated(&json!({"action":"speak","speech":text}).to_string());
            assert_eq!(leaf.finish(Some(&reply)), (text.into(), "speak"), "{kind}");
        }
        let leaf = leaf("aftermath", json!({"combat_outcome":"disengaged"}));
        let reply = generated(r#"{"action":"speak","speech":"敵を倒したで！"}"#);
        assert_eq!(leaf.finish(Some(&reply)).1, "claim_conflict");
    }
}
