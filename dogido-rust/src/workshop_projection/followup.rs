use super::*;
use crate::workshop_followup::Stage;
use regex::Regex;
use std::collections::HashMap;
static PATTERNS: LazyLock<HashMap<String, Regex>> = LazyLock::new(|| {
    let data: HashMap<String, String> =
        serde_json::from_str(include_str!("followup_patterns.json"))
            .expect("canonical workshop short replies");
    data.into_iter()
        .map(|(k, v)| {
            (
                k,
                Regex::new(&format!(r"\A(?:{})\z", v.replace(r"\s", r"[\s\x1c-\x1f]")))
                    .expect("canonical followup regex"),
            )
        })
        .collect()
});
/// Selection only. Existing caller checks Stage::actions and owns completed
/// playback, pending edits, and all state transitions.
pub fn fixed_followup(text: &str, stage: Stage, pending: bool) -> Option<&'static str> {
    let text = strip(text);
    if text.is_empty() {
        return None;
    }
    let matches = |key: &str| PATTERNS[key].is_match(text);
    if stage == Stage::CombatResumeConfirmation {
        if text.ends_with(['?', '？']) {
            return None;
        }
        if matches("combat_accept") {
            Some("resume_workshop")
        } else if matches("combat_decline") {
            Some("decline_resume")
        } else {
            None
        }
    } else if !pending {
        match stage {
            Stage::MeaningExplained if matches("meaning_ack") => Some("acknowledge_meaning"),
            Stage::CloseConfirmation if !text.ends_with(['?', '？']) => {
                if matches("close_continue") {
                    Some("continue_workshop")
                } else if matches("close_accept") {
                    Some("confirm_close")
                } else {
                    None
                }
            }
            _ => None,
        }
    } else {
        None
    }
}
