//! Player discussion ideas are not staged revisions. Keep their identity and
//! inspection failures across side conversations; revalidate before staging.
use crate::haiku_record::HaikuLine;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Proposal {
    pub line_index: Option<usize>,
    pub target_fragment: String,
    pub replacement_text: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Draft {
    pub proposal: Proposal,
    pub evidence: String,
    pub validation_codes: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct Candidate {
    pub draft: Draft,
    base: Vec<HaikuLine>,
    base_version: u64,
}
impl Candidate {
    pub fn from_player(
        draft: Draft,
        current: &[HaikuLine],
        version: u64,
        player_text: &str,
    ) -> Option<Self> {
        let p = &draft.proposal;
        if current.len() != 3
            || draft.evidence.is_empty()
            || !player_text.contains(&draft.evidence)
            || p.replacement_text.is_empty()
            || p.replacement_text.chars().count() > 80
            || !draft.evidence.contains(&p.replacement_text)
            || p.target_fragment.chars().count() > 80
            || p.line_index.is_some_and(|i| i > 2)
            || (p.line_index.is_none() && p.target_fragment.is_empty())
        {
            return None;
        }
        Some(Self {
            draft,
            base: current.to_vec(),
            base_version: version,
        })
    }
    pub fn is_current(&self, current: &[HaikuLine], version: u64, has_pending: bool) -> bool {
        !has_pending && self.base_version == version && self.base == current
    }
    pub fn view(&self) -> Value {
        json!({"proposal":self.draft.proposal,"validation_codes":self.draft.validation_codes})
    }
}

pub fn fixed_selection(text: &str) -> bool {
    matches!(
        text.trim().trim_end_matches(['。', '！', '!']),
        "それにして" | "その案にして" | "さっきの案にして" | "その案でお願い"
    )
}

/// A model may resolve a short assent only against the one current candidate
/// (or pending proposal). The whole original utterance must be affirmative.
pub fn contextual_assent(text: &str) -> bool {
    use std::sync::LazyLock;
    static ASSENT: LazyLock<regex::Regex> = LazyLock::new(|| {
        regex::Regex::new(
        r"\A(?:うん[、,\s]*)?(?:はい|そう(?:しよう|しましょう|して(?:ください)?)|それで(?:お願い(?:します)?|いい(?:です)?|ええ))[。！!\s]*\z"
    ).unwrap()
    });
    ASSENT.is_match(text.trim())
        && crate::workshop_input_guard::state_change_safe(
            "stage_conversation_candidate",
            text,
            text,
        )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn contextual_assent_requires_the_whole_original_utterance() {
        for text in [
            "そうしましょう",
            "そうしよう。",
            "うん、それでお願いします",
            "はい",
        ] {
            assert!(contextual_assent(text), "{text}");
        }
        for text in [
            "そうしましょう？",
            "そうしましょうとは言ってない",
            "『そうしましょう』",
            "そうしないで",
            "はい、でもまだ変えないで",
            "もしそうしましょうと言ったら",
            "そうしましょうと彼が言った",
        ] {
            assert!(!contextual_assent(text), "{text}");
        }
    }
    #[test]
    fn raw_words_are_required_even_when_an_idea_is_only_discussed() {
        let draft = Draft {
            proposal: Proposal {
                line_index: Some(0),
                target_fragment: "はる".into(),
                replacement_text: "なつ".into(),
            },
            evidence: "はるをなつにするのはどう？".into(),
            validation_codes: vec![],
        };
        assert!(Candidate::from_player(draft, &[], 0, "発話にない案").is_none());
        assert!(fixed_selection("それにして。"));
        for text in ["それにして？", "それにしないで", "『それにして』と言われた"]
        {
            assert!(!fixed_selection(text));
        }
    }
}
