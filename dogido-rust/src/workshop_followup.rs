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
}

impl Stage {
    pub fn actions(self, pending: bool) -> &'static [&'static str] {
        if pending {
            return &[];
        }
        match self {
            Self::Discussion => &[],
            Self::MeaningExplained => &["acknowledge_meaning"],
            Self::CloseConfirmation => &["confirm_close", "continue_workshop"],
        }
    }

    /// 再生完了時に適用する候補。計算だけでは状態を変えず、同じ未変更の句へだけ反映する。
    pub fn after_completed(action: &str, purpose: &str, pending: bool) -> Self {
        if pending {
            return Self::Discussion;
        }
        match (action, purpose) {
            ("explain", "understand_meaning") => Self::MeaningExplained,
            // Understanding a meaning does not ask permission to end the conversation.
            ("acknowledge_meaning", _) => Self::Discussion,
            _ => Self::Discussion,
        }
    }
}

pub fn is_action(action: &str) -> bool {
    matches!(
        action,
        "acknowledge_meaning" | "confirm_close" | "continue_workshop"
    )
}

#[derive(Deserialize)]
struct Step {
    action: String,
    #[serde(default, rename = "purpose")]
    _purpose: String,
    #[serde(default, rename = "confidence")]
    _confidence: f64,
    evidence: String,
    #[serde(rename = "speech")]
    _speech: String,
    checks: Vec<String>,
}

/// LLMが選んだ継続操作を原文と現在段階へ接続する。意味は再判定しない。
pub fn validate(payload: &Value, text: &str, stage: Stage, pending: bool) -> Result<()> {
    // 納得や会話続行は保存・終了・戦闘復帰を実行しない。本文の受理は
    // conversation側で済ませ、ここでは実際の直前段階との接続だけを見る。
    if matches!(
        payload["action"].as_str(),
        Some("acknowledge_meaning" | "continue_workshop")
    ) {
        ensure!(
            stage
                .actions(pending)
                .contains(&payload["action"].as_str().unwrap()),
            "followup_stage_mismatch"
        );
        return Ok(());
    }
    let step: Step = serde_json::from_value(payload.clone())?;
    ensure!(
        stage.actions(pending).contains(&step.action.as_str()),
        "followup_stage_mismatch"
    );
    ensure!(
        step.evidence.trim().chars().count() >= 2 && text.contains(&step.evidence),
        "followup_ungrounded_evidence"
    );
    if step.action == "confirm_close" {
        ensure!(
            crate::workshop_input_guard::state_change_safe("close_workshop", text, &step.evidence),
            "unsafe_close_evidence"
        );
    }
    ensure!(step.checks.is_empty(), "followup_cannot_inspect");
    Ok(())
}

pub fn speech(action: &str) -> Option<&'static str> {
    match action {
        "acknowledge_meaning" => Some("うん。"),
        "confirm_close" => Some("おけ、この句の話はここまでや。"),
        "continue_workshop" => Some("おけ、まだ続けよか。気になるとこ教えてな。"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn acknowledgement_keeps_stage_and_model_close_is_not_reclassified() {
        let ack = json!({"action":"acknowledge_meaning","purpose":"understand_meaning","confidence":0.95,
            "evidence":"おーいループしてるぞい","speech":"","checks":[]});
        assert!(
            validate(
                &ack,
                "おーいループしてるぞい",
                Stage::MeaningExplained,
                false
            )
            .is_ok()
        );
        assert_eq!(
            Stage::after_completed("acknowledge_meaning", "understand_meaning", false),
            Stage::Discussion
        );
        let close = json!({"action":"confirm_close","purpose":"finish_workshop","confidence":0.95,
            "evidence":"おわらんわ。","speech":"","checks":[]});
        assert!(validate(&close, "おわらんわ。", Stage::CloseConfirmation, false).is_ok());
    }
    #[test]
    fn contextual_yes_requires_completed_confirmation_and_no_pending() {
        let p = json!({"action":"confirm_close","purpose":"finish_workshop","confidence":0.95,
            "evidence":"ここで区切ってよいよ","speech":"","checks":[]});
        assert!(validate(&p, "ここで区切ってよいよ", Stage::CloseConfirmation, false).is_ok());
        for stage in [Stage::Discussion, Stage::MeaningExplained] {
            assert!(validate(&p, "ここで区切ってよいよ", stage, false).is_err());
        }
        assert!(validate(&p, "ここで区切ってよいよ", Stage::CloseConfirmation, true).is_err());
        for (key, value) in [
            ("confidence", json!(true)),
            ("evidence", json!("別の発話")),
            ("checks", json!(["source"])),
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
            Stage::Discussion
        );
        assert_eq!(
            Stage::after_completed("acknowledge_meaning", "understand_meaning", true),
            Stage::Discussion
        );
    }

    #[test]
    fn automatic_resume_has_no_model_action_or_confirmation_stage() {
        // BERTと警戒状態のコード判定へ変更済み。旧再開判定へ戻さない。
        for action in ["resume_workshop", "decline_resume"] {
            assert!(!is_action(action));
            assert!(speech(action).is_none());
            let p = json!({"action":action,"purpose":"continue_discussion","confidence":0.95,
                "evidence":"返事の原文","speech":"","checks":[]});
            for stage in [
                Stage::Discussion,
                Stage::MeaningExplained,
                Stage::CloseConfirmation,
            ] {
                for pending in [false, true] {
                    assert!(!stage.actions(pending).contains(&action));
                    assert!(validate(&p, "返事の原文", stage, pending).is_err());
                }
            }
        }
        assert!(serde_json::from_value::<Stage>(json!("combat_resume_confirmation")).is_err());
    }
}
