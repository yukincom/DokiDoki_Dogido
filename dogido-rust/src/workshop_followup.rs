//! 再生済みの意味説明と終了確認にだけ結び付く短い応答。
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    #[default]
    Discussion,
    MeaningExplained,
    CloseConfirmation,
    CombatResumeConfirmation,
}

impl Stage {
    pub fn actions(self, pending: bool) -> &'static [&'static str] {
        if self == Self::CombatResumeConfirmation {
            return &["resume_workshop", "decline_resume"];
        }
        if pending {
            return &[];
        }
        match self {
            Self::Discussion => &[],
            Self::MeaningExplained => &["acknowledge_meaning"],
            Self::CloseConfirmation => &["confirm_close", "continue_workshop"],
            Self::CombatResumeConfirmation => unreachable!(),
        }
    }

    /// 再生完了時に適用する候補。計算だけでは状態を変えず、同じ未変更の句へだけ反映する。
    pub fn after_completed(action: &str, purpose: &str, pending: bool) -> Self {
        if pending {
            return Self::Discussion;
        }
        match (action, purpose) {
            ("explain", "understand_meaning") => Self::MeaningExplained,
            ("acknowledge_meaning", _) => Self::CloseConfirmation,
            _ => Self::Discussion,
        }
    }
}

pub fn is_action(action: &str) -> bool {
    matches!(
        action,
        "acknowledge_meaning"
            | "confirm_close"
            | "continue_workshop"
            | "resume_workshop"
            | "decline_resume"
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Step {
    action: String,
    purpose: String,
    confidence: f64,
    evidence: String,
    speech: String,
    checks: Vec<String>,
}

/// 原文の否定・引用等は既存の純粋helperで照合し、外形と段階の権限をRustで再検査する。
pub fn validate(payload: &Value, text: &str, stage: Stage, pending: bool) -> Result<()> {
    let step: Step = serde_json::from_value(payload.clone())?;
    ensure!(
        stage.actions(pending).contains(&step.action.as_str()),
        "followup_stage_mismatch"
    );
    let purpose = match step.action.as_str() {
        "acknowledge_meaning" => "understand_meaning",
        "confirm_close" | "decline_resume" => "finish_workshop",
        _ => "continue_discussion",
    };
    ensure!(step.purpose == purpose, "followup_purpose_mismatch");
    ensure!(
        (0.85..=1.0).contains(&step.confidence),
        "followup_low_confidence"
    );
    ensure!(
        step.evidence.trim().chars().count() >= 2 && text.contains(&step.evidence),
        "followup_ungrounded_evidence"
    );
    ensure!(
        step.speech.is_empty() && step.checks.is_empty(),
        "followup_requires_fixed_speech"
    );
    Ok(())
}

pub fn speech(action: &str) -> Option<&'static str> {
    match action {
        "acknowledge_meaning" => Some("うん。この句の話はここまででええ？"),
        "confirm_close" => Some("おけ、この句の話はここまでや。"),
        "continue_workshop" => Some("おけ、まだ続けよか。気になるとこ教えてな。"),
        "resume_workshop" => Some("おけ、続けよか。気になるとこ教えてな。"),
        "decline_resume" => Some("おけ、句はここまでにしよか。"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn contextual_yes_requires_completed_confirmation_and_no_pending() {
        let p = json!({"action":"confirm_close","purpose":"finish_workshop","confidence":0.95,
            "evidence":"ここで区切ってよいよ","speech":"","checks":[]});
        assert!(validate(&p, "ここで区切ってよいよ", Stage::CloseConfirmation, false).is_ok());
        for stage in [
            Stage::Discussion,
            Stage::MeaningExplained,
            Stage::CombatResumeConfirmation,
        ] {
            assert!(validate(&p, "ここで区切ってよいよ", stage, false).is_err());
        }
        assert!(validate(&p, "ここで区切ってよいよ", Stage::CloseConfirmation, true).is_err());
        for (key, value) in [
            ("confidence", json!(true)),
            ("confidence", json!(0.84)),
            ("evidence", json!("別の発話")),
            ("speech", json!("採用したで。")),
            ("checks", json!(["source"])),
            ("purpose", json!("adopt_pending")),
            ("close_after_action", json!(true)),
        ] {
            let mut bad = p.clone();
            bad[key] = value;
            assert!(
                validate(
                    &bad,
                    "ここで区切ってよいよ",
                    Stage::CloseConfirmation,
                    false
                )
                .is_err()
            );
        }
    }
    #[test]
    fn only_meaning_explanation_arms_ack_and_pending_cannot_be_closed_by_ack() {
        assert_eq!(
            Stage::after_completed("explain", "understand_meaning", false),
            Stage::MeaningExplained
        );
        assert_eq!(
            Stage::after_completed("explain", "evaluate_verse", false),
            Stage::Discussion
        );
        assert_eq!(
            Stage::after_completed("ask", "understand_meaning", false),
            Stage::Discussion
        );
        assert_eq!(
            Stage::after_completed("acknowledge_meaning", "understand_meaning", false),
            Stage::CloseConfirmation
        );
        assert_eq!(
            Stage::after_completed("acknowledge_meaning", "understand_meaning", true),
            Stage::Discussion
        );
    }

    #[test]
    fn resume_and_decline_are_distinct_from_close_assent_and_pending_adoption() {
        for (action, purpose) in [
            ("resume_workshop", "continue_discussion"),
            ("decline_resume", "finish_workshop"),
        ] {
            let p = json!({"action":action,"purpose":purpose,"confidence":0.95,"evidence":"返事の原文","speech":"","checks":[]});
            assert!(validate(&p, "返事の原文", Stage::CombatResumeConfirmation, true).is_ok());
            for stage in [
                Stage::Discussion,
                Stage::MeaningExplained,
                Stage::CloseConfirmation,
            ] {
                assert!(validate(&p, "返事の原文", stage, false).is_err());
            }
        }
        assert!(
            !Stage::CombatResumeConfirmation
                .actions(true)
                .contains(&"accept_pending")
        );
        assert_eq!(
            Stage::after_completed("resume_workshop", "continue_discussion", true),
            Stage::Discussion
        );
    }
}
