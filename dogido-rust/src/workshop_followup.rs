//! 実再生済みの意味説明に対する納得だけを、一句の現在段階へ結び付ける。
//! 終了は共通BERTと相談runtime、戦闘後の再開は現在観測が所有する。
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    #[default]
    Discussion,
    MeaningExplained,
}

impl Stage {
    pub fn actions(self, pending: bool) -> &'static [&'static str] {
        if !pending && self == Self::MeaningExplained {
            &["acknowledge_meaning"]
        } else {
            &[]
        }
    }

    /// 同じ句の説明が配送完了した後だけ納得待ちにする。計算だけでは状態を変更しない。
    pub fn after_completed(action: &str, purpose: &str, pending: bool) -> Self {
        if !pending && (action, purpose) == ("explain", "understand_meaning") {
            Self::MeaningExplained
        } else {
            Self::Discussion
        }
    }
}

pub fn is_action(action: &str) -> bool {
    action == "acknowledge_meaning"
}

/// 納得の返事は保存・終了・戦闘復帰を実行しない。本文受理後、実際の直前段階だけを照合する。
pub fn validate(payload: &Value, stage: Stage, pending: bool) -> Result<()> {
    ensure!(
        payload["action"].as_str().is_some_and(|action| stage.actions(pending).contains(&action)),
        "followup_stage_mismatch"
    );
    Ok(())
}

pub fn speech(action: &str) -> Option<&'static str> {
    (action == "acknowledge_meaning").then_some("うん。")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_completed_meaning_explanation_accepts_ack_without_pending() {
        let ack = json!({"action":"acknowledge_meaning", "speech":"なるほどな。"});
        assert!(validate(&ack, Stage::MeaningExplained, false).is_ok());
        assert!(validate(&ack, Stage::Discussion, false).is_err());
        assert!(validate(&ack, Stage::MeaningExplained, true).is_err());
        assert_eq!(Stage::after_completed("explain", "understand_meaning", false), Stage::MeaningExplained);
        for (action, purpose, pending) in [
            ("explain", "evaluate_verse", false),
            ("ask", "understand_meaning", false),
            ("explain", "understand_meaning", true),
            ("acknowledge_meaning", "understand_meaning", false),
        ] {
            assert_eq!(Stage::after_completed(action, purpose, pending), Stage::Discussion);
        }
    }

    #[test]
    fn retired_close_and_resume_actions_and_stages_are_absent() {
        for action in ["confirm_close", "continue_workshop", "resume_workshop", "decline_resume"] {
            assert!(!is_action(action));
            assert!(speech(action).is_none());
            for stage in [Stage::Discussion, Stage::MeaningExplained] {
                for pending in [false, true] {
                    assert!(validate(&json!({"action":action}), stage, pending).is_err());
                }
            }
        }
        for stage in ["close_confirmation", "resume_confirmation"] {
            assert!(serde_json::from_value::<Stage>(json!(stage)).is_err());
        }
    }
}
