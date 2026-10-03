//! Candidate acceptance and at most one wording repair for ordinary player_chat.
//! Rustの会話入力から候補の検査・一度だけの言い直し・最終文を確定する。
//! 本文はdialogueへ返し、発話IDの実再生完了による履歴確定はdialogueが所有する。
//! reaction_leaf::sanitizeの共通整形を使うが、会話固有のgroundingはguardで検査する。
mod guard;
use crate::{chat_prompt, reaction_leaf::sanitize, types::GenerationRequest};
use anyhow::{Context, Result, ensure};
pub(crate) use guard::mentioned;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub prompt: chat_prompt::Input,
    pub validation: Value,
    pub fallback_text: String,
}
#[derive(Debug, Serialize)]
pub struct Validation {
    pub cleaned: String,
    pub reason: Option<&'static str>,
    pub issue: Option<&'static str>,
    pub corrections: Vec<(String, String)>,
}
pub fn validate(raw: &str, d: &Value) -> Validation {
    let cleaned = sanitize::clean(raw);
    if let Some(issue) = sanitize::usability_reason(&cleaned, d) {
        return Validation {
            cleaned,
            reason: Some("unusable_output"),
            issue: Some(issue),
            corrections: vec![],
        };
    }
    let (cleaned, corrections) = guard::rewrite(&cleaned, d);
    let issue = guard::style_issue(&cleaned, d).or_else(|| {
        d["required_identification_label"]
            .as_str()
            .filter(|label| !label.is_empty() && !cleaned.contains(label))
            .map(|_| "missing_identification_name")
    });
    Validation {
        cleaned,
        reason: issue.map(|_| "style_mismatch"),
        issue,
        corrections,
    }
}
fn valid_validation(d: &Value) -> Result<()> {
    for (key, value) in d.as_object().context("chat validation details")? {
        let valid = match key.as_str() {
            "player_name"
            | "mode"
            | "character_mode"
            | "threat_summary"
            | "hearing_summary"
            | "event_digest"
            | "user_text"
            | "player_turn_plan"
            | "safety_priority"
            | "required_identification_label" => value.is_null() || value.is_string(),
            "has_visual_threats" | "combat_active" | "speech_whitelist_enforce" => {
                value.is_null() || value.is_boolean()
            }
            "nearby_hostile_types" | "nearby_mob_ids" => {
                value.is_null()
                    || value.is_string()
                    || value
                        .as_array()
                        .is_some_and(|a| a.iter().all(Value::is_string))
            }
            "forbidden_advice" | "allowed_speech_labels" => {
                value.is_null()
                    || value
                        .as_array()
                        .is_some_and(|a| a.iter().all(Value::is_string))
            }
            "world_context" => {
                serde_json::from_value::<crate::conversation_observation::Context>(value.clone())
                    .is_ok()
            }
            "speech_name_corrections" => {
                value.is_null()
                    || value
                        .as_object()
                        .is_some_and(|a| a.values().all(Value::is_string))
            }
            _ => false,
        };
        ensure!(valid, "invalid chat validation detail: {key}");
    }
    Ok(())
}
#[derive(Debug, Serialize)]
pub struct Outcome {
    pub text: String,
    pub final_text: String,
    pub status: &'static str,
    pub attempts: usize,
    pub validations: Vec<Validation>,
}

pub struct Turn {
    pub base: GenerationRequest,
    prompt_details: Value,
    validation: Value,
    fallback: String,
    attempts: usize,
    processed: usize,
    checks: Vec<Validation>,
    outcome: Option<Outcome>,
    choice_enabled: bool,
}
impl Turn {
    pub fn new(input: Input, model: &str, max_tokens: u64) -> Result<Self> {
        valid_validation(&input.validation)?;
        ensure!(
            input.prompt.model == model && input.prompt.max_tokens == max_tokens,
            "chat leaf route mismatch"
        );
        ensure!(
            input.prompt.details.get("player_chat_repair").is_none(),
            "chat repair is Rust-owned"
        );
        for (key, value) in input.validation.as_object().unwrap() {
            if let Some(prompt_value) = input.prompt.details.get(key) {
                ensure!(
                    value == prompt_value,
                    "chat prompt/validation mismatch: {key}"
                );
            }
        }
        let prompt_details = input.prompt.details.clone();
        let choice_enabled = prompt_details["dialogue_choice"] == true;
        let base = input.prompt.into_request()?;
        Ok(Self {
            base,
            prompt_details,
            validation: input.validation,
            fallback: input.fallback_text,
            attempts: 0,
            processed: 0,
            checks: vec![],
            outcome: None,
            choice_enabled,
        })
    }
    pub fn request(&mut self) -> Result<GenerationRequest> {
        ensure!(
            self.outcome.is_none() && self.attempts == self.processed && self.attempts < 2,
            "chat attempt limit"
        );
        let mut request = self.base.clone();
        if self.attempts == 1 {
            let previous = self.checks.first().context("missing first rejection")?;
            ensure!(previous.reason.is_some(), "accepted chat cannot retry");
            let mut details = self.prompt_details.clone();
            details["player_chat_repair"] = json!({"candidate":previous.cleaned.chars().take(600).collect::<String>(),"reason":previous.issue});
            request.messages = chat_prompt::messages(&details)?;
        }
        self.attempts += 1;
        Ok(request)
    }
    pub fn complete(&mut self, raw: Option<&str>) -> Result<bool> {
        ensure!(
            self.outcome.is_none() && self.attempts == self.processed + 1,
            "unexpected chat result"
        );
        self.processed += 1;
        let Some(raw) = raw else {
            self.finish(
                self.fallback.clone(),
                if self.attempts == 1 {
                    "generation_error"
                } else {
                    "repair_failed"
                },
            );
            return Ok(true);
        };
        let decoded;
        let v = if self.choice_enabled {
            match crate::speech_choice::parse(raw) {
                Ok(crate::speech_choice::Choice::Silent) => {
                    self.outcome = Some(Outcome {
                        text: String::new(),
                        final_text: String::new(),
                        status: "silent",
                        attempts: self.attempts,
                        validations: std::mem::take(&mut self.checks),
                    });
                    return Ok(true);
                }
                Ok(crate::speech_choice::Choice::Speak(text)) => {
                    decoded = text;
                    validate(&decoded, &self.validation)
                }
                Err(reason) => Validation {
                    cleaned: raw.into(),
                    reason: Some("unusable_output"),
                    issue: Some(reason),
                    corrections: vec![],
                },
            }
        } else {
            validate(raw, &self.validation)
        };
        let accepted = v.reason.is_none();
        let text = v.cleaned.clone();
        self.checks.push(v);
        if accepted {
            self.finish(
                text,
                if self.attempts == 1 {
                    "accepted"
                } else {
                    "repair_accepted"
                },
            );
            return Ok(true);
        }
        if self.attempts == 2 {
            self.finish(self.fallback.clone(), "repair_rejected");
            return Ok(true);
        }
        Ok(false)
    }
    fn finish(&mut self, text: String, status: &'static str) {
        let (corrected, _) = guard::rewrite(&text, &self.validation);
        let final_text = if guard::style_issue(&corrected, &self.validation).is_none() {
            corrected
        } else {
            self.fallback.clone()
        };
        self.outcome = Some(Outcome {
            text,
            final_text,
            status,
            attempts: self.attempts,
            validations: std::mem::take(&mut self.checks),
        });
    }
    pub fn take_outcome(&mut self) -> Result<Outcome> {
        self.outcome.take().context("unfinished chat leaf")
    }
}

/// Closed validation projection; acceptance remains in Turn and guard.
pub(crate) fn project_details(details: &Value) -> Value {
    Value::Object(
        details
            .as_object()
            .expect("native chat details")
            .iter()
            .filter(|(k, _)| {
                matches!(
                    k.as_str(),
                    "player_name"
                        | "mode"
                        | "character_mode"
                        | "threat_summary"
                        | "hearing_summary"
                        | "event_digest"
                        | "user_text"
                        | "player_turn_plan"
                        | "safety_priority"
                        | "required_identification_label"
                        | "has_visual_threats"
                        | "combat_active"
                        | "speech_whitelist_enforce"
                        | "nearby_hostile_types"
                        | "nearby_mob_ids"
                        | "forbidden_advice"
                        | "allowed_speech_labels"
                        | "speech_name_corrections"
                        | "world_context"
                )
            })
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixtures(key: &str) -> Vec<Value> {
        let v: Value = serde_json::from_str(include_str!("chat_validation/fixtures.json")).unwrap();
        v[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| {
                Value::Object(
                    r.as_object()
                        .unwrap()
                        .iter()
                        .map(|(k, i)| (k.clone(), v["pool"][i.as_u64().unwrap() as usize].clone()))
                        .collect(),
                )
            })
            .collect()
    }
    #[test]
    fn python_candidate_reasons_and_observed_name_rules_match() {
        for row in fixtures("candidates") {
            let raw = row["raw"].as_str().unwrap();
            let d = &row["details"];
            let actual = serde_json::to_value(validate(raw, d)).unwrap();
            for k in ["cleaned", "reason", "issue", "corrections"] {
                assert_eq!(actual[k], row[k], "{k}: {row}");
            }
            let clean = sanitize::clean(raw);
            let (corrected, _) = guard::rewrite(&clean, d);
            assert_eq!(json!(guard::mentioned(&clean)), row["mentioned"], "{row}");
            assert_eq!(corrected, row["corrected"], "{row}");
            assert_eq!(
                guard::style_issue(&corrected, d).is_none(),
                row["style"],
                "{row}"
            );
            assert_eq!(
                guard::style_issue(&corrected, d).unwrap_or("surface_style_mismatch"),
                row["style_reason"],
                "{row}"
            );
        }
    }
    #[test]
    fn python_full_turn_retries_and_narration_fallback_boundary_match() {
        for row in fixtures("turns") {
            let input: Input = serde_json::from_value(row["input"].clone()).unwrap();
            let d = input.validation.clone();
            let fallback = input.fallback_text.clone();
            let mut turn = Turn::new(input, "fixture", 72).unwrap();
            let mut responses = row["outputs"].as_array().unwrap().iter();
            let mut n = 0;
            loop {
                let request = serde_json::to_value(turn.request().unwrap()).unwrap();
                let expected = &row["calls"][n];
                for k in ["messages", "temperature", "max_tokens"] {
                    assert_eq!(request[k], expected[k], "{k}: {row}");
                }
                n += 1;
                let raw = responses.next().and_then(Value::as_str);
                if turn.complete(raw).unwrap() {
                    break;
                }
            }
            assert_eq!(n, row["calls"].as_array().unwrap().len(), "{row}");
            assert!(turn.request().is_err());
            assert!(turn.complete(Some("もう一回")).is_err());
            let out = turn.take_outcome().unwrap();
            assert_eq!(out.text, row["selected"], "{row}");
            // Narration currently retains this exact second-pass safety boundary.
            // Do not run it a third time in Rust: corrections may be non-idempotent.
            let (corrected, _) = guard::rewrite(&out.text, &d);
            let final_text = if guard::style_issue(&corrected, &d).is_none() {
                corrected
            } else {
                fallback
            };
            assert_eq!(final_text, row["final"], "{row}");
            assert_eq!(out.final_text, row["final"], "native final {row}");
        }
    }
    #[test]
    fn malformed_route_and_external_repair_are_rejected_without_generation() {
        let base = json!({"prompt":{"schema_version":1,"kind":"player_chat","model":"fixture","max_tokens":72,"temperature":0.65,"enable_thinking":false,"details":{}},"validation":{},"fallback_text":"そうなんやな。"});
        for (path, value) in [
            ("model", json!("other")),
            ("max_tokens", json!(73)),
            ("kind", json!("death")),
            ("enable_thinking", json!(true)),
        ] {
            let mut v = base.clone();
            v["prompt"][path] = value;
            assert!(Turn::new(serde_json::from_value(v).unwrap(), "fixture", 72).is_err());
        }
        let mut v = base.clone();
        v["prompt"]["details"]["player_chat_repair"] = json!({});
        assert!(Turn::new(serde_json::from_value(v).unwrap(), "fixture", 72).is_err());
        let mut v = base.clone();
        v["validation"]["extra"] = true.into();
        assert!(Turn::new(serde_json::from_value(v).unwrap(), "fixture", 72).is_err());
        let mut v = base;
        v["prompt"]["details"]["user_text"] = "帰る".into();
        v["validation"]["user_text"] = "遊ぶ".into();
        assert!(Turn::new(serde_json::from_value(v).unwrap(), "fixture", 72).is_err());
    }
    fn choice_turn(smell: Value) -> Turn {
        let d = json!({"dialogue_choice":true,"user_text":"一緒に帰ろう",
            "speech_whitelist_enforce":true,"allowed_speech_labels":[],
            "world_context":{"observations":{"weather":{"label":"雨"},"smell":smell},"changes":[],"revision":2}});
        let input = Input {
            prompt: chat_prompt::Input {
                schema_version: 1,
                kind: "player_chat".into(),
                model: "fixture".into(),
                details: chat_prompt::project_details(&d),
                temperature: 0.65,
                max_tokens: 192,
                enable_thinking: false,
            },
            validation: project_details(&d),
            fallback_text: "そばにおるで。".into(),
        };
        Turn::new(input, "fixture", 192).unwrap()
    }
    #[test]
    fn normal_reply_can_include_observed_smell_and_weather_or_choose_silence() {
        let mut turn = choice_turn(json!({"status":"present","description":"パンの匂いや。"}));
        let req = turn.request().unwrap();
        assert!(
            req.messages[0]
                .content
                .contains(crate::companion_prompt::base())
        );
        assert!(req.messages[1].content.contains("パンの匂いや"));
        let speech = "そやな、一緒に帰ろか。雨も降ってきたし、パンの匂いでお腹もすいたわ。";
        assert!(
            turn.complete(Some(&json!({"action":"speak","speech":speech}).to_string()))
                .unwrap()
        );
        assert_eq!(turn.take_outcome().unwrap().final_text, speech);
        let mut turn = choice_turn(json!({"status":"none"}));
        turn.request().unwrap();
        assert!(
            turn.complete(Some(r#"{"action":"silent","speech":""}"#))
                .unwrap()
        );
        let out = turn.take_outcome().unwrap();
        assert_eq!(out.status, "silent");
        assert_eq!(out.attempts, 1);
        assert!(out.final_text.is_empty());
    }
    #[test]
    fn no_observation_and_broken_contract_require_repair_not_silent_history() {
        for smell in [
            json!({"status":"none"}),
            json!({"status":"unknown"}),
            json!({"status":"suppressed"}),
        ] {
            let mut turn = choice_turn(smell);
            turn.request().unwrap();
            assert!(
                !turn
                    .complete(Some(r#"{"action":"speak","speech":"土の匂いがするわ。"}"#))
                    .unwrap()
            );
            let repair = turn.request().unwrap();
            assert!(
                repair
                    .messages
                    .last()
                    .unwrap()
                    .content
                    .contains("unsupported_olfactory_claim")
            );
            assert!(repair.messages.last().unwrap().content.contains("JSON"));
            assert!(turn.complete(Some("{}")).unwrap());
            let out = turn.take_outcome().unwrap();
            assert_ne!(out.status, "silent");
            assert!(!out.final_text.is_empty());
        }
        for raw in [
            "",
            r#"{"action":"silent","speech":"""#,
            r#"{"action":"silent","speech":"話した"}"#,
        ] {
            let mut turn = choice_turn(json!({"status":"none"}));
            turn.request().unwrap();
            assert!(!turn.complete(Some(raw)).unwrap());
            turn.request().unwrap();
            assert!(turn.complete(None).unwrap());
            assert_ne!(turn.take_outcome().unwrap().status, "silent");
        }
    }
}
