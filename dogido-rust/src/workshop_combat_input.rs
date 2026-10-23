//! BERTで確定した川柳の行・変更・終了だけをコードへ渡す。状態・保存はコード所有。
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    WorkshopInput,
    CloseWorkshop,
    #[default]
    DeferredInput,
}

impl Action {
    /// 行3種・具体変更3種・終了はBERTへ変更済み。旧判定へ戻さない。
    pub fn from_classification(
        classification: Option<&crate::request_classifier::Classification>,
        text: &str,
        source: &str,
    ) -> Option<Self> {
        use crate::request_classifier::Label;
        let result = classification.filter(|result| result.matches(text, source))?;
        match result.prediction.label {
            Label::SenryuEnd => Some(Self::CloseWorkshop),
            Label::SenryuLineUpper
            | Label::SenryuLineMiddle
            | Label::SenryuLineLower
            | Label::SenryuChangeUpper
            | Label::SenryuChangeMiddle
            | Label::SenryuChangeLower => Some(Self::WorkshopInput),
            // not_targetを再開意思や安全の証拠へ読み替えず、その入力を保持する。
            _ => None,
        }
    }
}

#[cfg(test)]
mod bert_tests {
    use super::*;
    use crate::request_classifier::{Classification, LABEL_COUNT, Label, Prediction};

    fn classified(label: Label) -> Classification {
        Classification {
            text: "原文".into(),
            source: "typed".into(),
            manifest_sha256: "fixture".into(),
            prediction: Prediction {
                label,
                probabilities: [0.0; LABEL_COUNT],
                token_count: 3,
                inference_ms: 1.0,
            },
        }
    }

    #[test]
    fn known_workshop_labels_use_classifier_without_confidence_gate() {
        for label in [
            Label::SenryuLineUpper,
            Label::SenryuLineMiddle,
            Label::SenryuLineLower,
            Label::SenryuChangeUpper,
            Label::SenryuChangeMiddle,
            Label::SenryuChangeLower,
        ] {
            assert_eq!(
                Action::from_classification(Some(&classified(label)), "原文", "typed"),
                Some(Action::WorkshopInput)
            );
        }
        assert_eq!(
            Action::from_classification(Some(&classified(Label::SenryuEnd)), "原文", "typed"),
            Some(Action::CloseWorkshop)
        );
    }

    #[test]
    fn unrecorded_labels_stale_text_and_source_do_not_invent_resume_or_close() {
        for label in [
            Label::NotTarget,
            Label::EnemyInformation,
            Label::RecallMount,
        ] {
            assert_eq!(
                Action::from_classification(Some(&classified(label)), "原文", "typed"),
                None
            );
        }
        let result = classified(Label::SenryuEnd);
        assert_eq!(
            Action::from_classification(Some(&result), "別原文", "typed"),
            None
        );
        assert_eq!(
            Action::from_classification(Some(&result), "原文", "voice"),
            None
        );
        assert_eq!(Action::from_classification(None, "終了", "typed"), None);
    }
}
