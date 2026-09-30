//! Bounded combat/environment wording. The caller owns observation and priorities.
//! Canonical Python prompt text is frozen as data; no Python prompt or judgement runs.
mod prompts;
pub(crate) mod sanitize;
use crate::types::GenerationRequest;
use anyhow::{Context, Result, ensure};
use serde_json::Value;

pub const KINDS: [&str; 18] = [
    "death",
    "aftermath",
    "daylight_water_skeleton",
    "newly_burning_visual",
    "deep_dark_ominous_sound",
    "occluded_hostile_presence",
    "ambient",
    "weather_transition",
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
            .map_or_else(String::new, |v| prompts::pystr(&v));
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
    /// No repair loop for these leaves: one failed/invalid candidate uses the fixed fallback.
    pub fn finish(&self, raw: Option<&str>) -> (String, &'static str) {
        let (text, reason) = match raw {
            None => (self.fallback.clone(), "generation_error"),
            Some(raw) => {
                let cleaned = sanitize::clean(raw);
                let reason = if !sanitize::usable(&cleaned, &self.details) {
                    "unusable_output"
                } else if !sanitize::style(&self.request.kind, &cleaned, &self.details) {
                    "style_mismatch"
                } else if sanitize::final_guard(&self.request.kind, &cleaned, &self.details) {
                    "claim_conflict"
                } else {
                    "accepted"
                };
                // The final guard also runs on the fallback, as in the Python facade;
                // a conflicting fallback remains the same authoritative fallback.
                (
                    if reason == "accepted" {
                        cleaned
                    } else {
                        self.fallback.clone()
                    },
                    reason,
                )
            }
        };
        (text + &self.suffix, reason)
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
            "sanitizers":v["sanitizers"].as_array().unwrap().iter().map(|r|json!({"kind":r[0],"details":get(&r[1]),"raw":get(&r[2]),"cleaned":get(&r[3]),"usable":r[4],"style":r[5],"guard":r[6],"selected":get(&r[7])})).collect::<Vec<_>>(),"attempts":v["attempts"]})
    }
    #[test]
    fn prompts_match_python_for_all_kinds_and_branches() {
        for row in fixture()["prompts"].as_array().unwrap() {
            let kind = row["kind"].as_str().unwrap();
            let mut actual =
                serde_json::to_value(prompts::messages(kind, &row["details"]).unwrap()).unwrap();
            // Native reactions now explicitly share the established first person.
            let system = actual[0]["content"].as_str().unwrap();
            assert!(system.ends_with("一人称は「オレ」。自分を名前の「ドギド」で呼ばない。"));
            actual[0]["content"] = json!(system.strip_suffix(prompts::FIRST_PERSON).unwrap());
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
            let input = json!({"kind":kind,"details":d,"temperature":0.65,"model":"fixture-model","max_tokens":72,"fallback_text":"ひとまず落ち着いたな。"});
            let leaf = Leaf::prepare(&input, "fixture-model", 72).unwrap();
            assert_eq!(leaf.finish(Some(raw)).0, row["selected"], "finish {row}");
        }
    }
    #[test]
    fn generation_contract_matches_python_single_attempt_and_suffix_is_once() {
        for row in fixture()["attempts"].as_array().unwrap() {
            let input = json!({"kind":row["kind"],"details":{"combat_outcome":"disengaged","__ambient_guard":{"do_not_prompt":true},"__speech_suffix":" 後ろも気になるわ。"},"temperature":row["calls"][0]["temperature"],"model":"fixture-model","max_tokens":row["calls"][0]["max_tokens"],"fallback_text":"ひとまず落ち着いたな。 後ろも気になるわ。"});
            let leaf = Leaf::prepare(
                &input,
                "fixture-model",
                input["max_tokens"].as_u64().unwrap(),
            )
            .unwrap();
            let calls = row["calls"].as_array().unwrap();
            assert_eq!(calls.len(), 1);
            let mut req = serde_json::to_value(&leaf.request).unwrap();
            req["messages"][0]["content"] = json!(
                req["messages"][0]["content"]
                    .as_str()
                    .unwrap()
                    .strip_suffix(prompts::FIRST_PERSON)
                    .unwrap()
            );
            for field in [
                "kind",
                "temperature",
                "max_tokens",
                "enable_thinking",
                "messages",
            ] {
                assert_eq!(req[field], calls[0][field], "{field} {row}");
            }
            let raw = if row["error"] == true {
                None
            } else {
                row["raw"].as_str()
            };
            assert_eq!(
                leaf.finish(raw).0,
                format!("{} 後ろも気になるわ。", row["expected"].as_str().unwrap())
            );
        }
    }
}
