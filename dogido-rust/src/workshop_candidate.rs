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

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Candidate {
    pub draft: Draft,
    base: Vec<HaikuLine>,
    base_version: u64,
}
impl Candidate {
    /// Keep a model-extracted player idea even if this turn only discusses it.
    /// This grants no execution authority; a later request must select it again.
    pub fn from_model(
        payload: &Value,
        current: &[HaikuLine],
        version: u64,
        player_text: &str,
        retained: Option<&crate::workshop_target::Target>,
    ) -> Option<Self> {
        if !matches!(
            payload["action"].as_str(),
            Some("respond" | "ask" | "explain" | "stage_player_edit")
        ) {
            return None;
        }
        if !crate::workshop_input_guard::discussion_idea_safe(player_text) {
            return None;
        }
        let raw = &payload["line_proposal"];
        if raw["found"] != true
            || !raw["confidence"]
                .as_f64()
                .is_some_and(|c| (0.85..=1.0).contains(&c))
        {
            return None;
        }
        let fragment = raw["target_fragment"].as_str().unwrap_or("");
        let matches: Vec<_> = current
            .iter()
            .enumerate()
            .filter_map(|(i, l)| {
                (!fragment.is_empty()
                    && (l.reading_text.contains(fragment) || l.surface_text.contains(fragment)))
                .then_some(i)
            })
            .collect();
        let explicit = crate::workshop_target::Target::explicit(player_text, current);
        let explicit_indices = crate::workshop_editing::explicit_line_indices(player_text);
        let raw_reference = &payload["line_reference"];
        let reference = if raw_reference["found"] == true {
            if !raw_reference["confidence"]
                .as_f64()
                .is_some_and(|c| (0.85..=1.0).contains(&c))
            {
                return None;
            }
            let index = match raw_reference["concept_id"].as_str()? {
                "line_1" => 0,
                "line_2" => 1,
                "line_3" => 2,
                _ => return None,
            };
            Some(crate::workshop_target::Target::from_reference(
                &json!({"line_index":index,"evidence":raw_reference["evidence"],"confidence":raw_reference["confidence"]}),
                player_text,
                current,
            )?)
        } else {
            None
        };
        let index = if matches.len() == 1 {
            Some(matches[0])
        } else if fragment.is_empty() {
            explicit
                .as_ref()
                .or(reference.as_ref())
                .or(retained)
                .map(|t| t.line_index)
        } else {
            None
        }?;
        if explicit.as_ref().is_some_and(|t| t.line_index != index)
            || reference.as_ref().is_some_and(|t| t.line_index != index)
            || explicit_indices.len() > 1
            || explicit_indices.iter().any(|i| *i != index)
        {
            return None;
        }
        if explicit.is_none()
            && reference.is_none()
            && retained.is_some_and(|t| t.line_index != index)
        {
            return None;
        }
        Self::from_player(
            Draft {
                proposal: Proposal {
                    line_index: Some(index),
                    target_fragment: fragment.into(),
                    replacement_text: raw["replacement_text"].as_str()?.into(),
                },
                evidence: raw["evidence"].as_str()?.into(),
                validation_codes: vec![],
            },
            current,
            version,
            player_text,
        )
    }
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
    fn lines() -> Vec<HaikuLine> {
        ["よるのまど", "てつじんみまもる", "のくさふゆむ"]
            .iter()
            .enumerate()
            .map(|(i, text)| HaikuLine {
                line_id: format!("line_{}", i + 1),
                line_index: i,
                position: ["upper", "middle", "lower"][i].into(),
                canonical_name: ["上五", "中七", "下五"][i].into(),
                surface_text: (*text).into(),
                reading_text: (*text).into(),
                source_atom_ids: vec![],
                source_atoms: vec![],
                provenance: "player".into(),
            })
            .collect()
    }
    #[test]
    fn model_discussion_keeps_only_player_words_and_respects_new_line_reference() {
        let current = lines();
        let player = "じゃあ、のくさふゆむをくささむしに変更しよう！どう？";
        let mut payload = json!({"action":"stage_player_edit","line_proposal":{"found":true,
            "target_fragment":"のくさふゆむ","replacement_text":"くささむし","evidence":player,"confidence":0.95}});
        let c = Candidate::from_model(&payload, &current, 0, player, None).unwrap();
        assert_eq!(c.draft.proposal.line_index, Some(2));
        assert!(c.is_current(&current, 0, false));
        assert!(!c.is_current(&current, 1, false));
        let conflicting = "上五の「のくさふゆむ」を「くささむし」にするのはどう？";
        payload["line_proposal"]["evidence"] = conflicting.into();
        assert!(Candidate::from_model(&payload, &current, 0, conflicting, None).is_none());
        for question in [
            "のくさふゆむをくささむしに変えない？",
            "のくさふゆむをくささむしにしない？",
        ] {
            payload["line_proposal"]["evidence"] = question.into();
            assert!(Candidate::from_model(&payload, &current, 0, question, None).is_some());
        }
        for invalid in [
            "のくさふゆむをくささむしには変えないで",
            "『のくさふゆむをくささむしに変更』って言われた",
            "原文にない案",
        ] {
            payload["line_proposal"]["evidence"] = invalid.into();
            assert!(
                Candidate::from_model(&payload, &current, 0, invalid, None).is_none(),
                "{invalid}"
            );
        }
        let player = "冒頭の五音をさくらいろに変えよう！どう？";
        payload["line_proposal"] = json!({"found":true,"target_fragment":"","replacement_text":"さくらいろ","evidence":player,"confidence":0.95});
        payload["line_reference"] =
            json!({"found":true,"concept_id":"line_1","evidence":"冒頭の五音","confidence":0.95});
        let retained = crate::workshop_target::Target::explicit("下五", &current).unwrap();
        let c = Candidate::from_model(&payload, &current, 0, player, Some(&retained)).unwrap();
        assert_eq!(c.draft.proposal.line_index, Some(0));
        payload["line_proposal"]["target_fragment"] = "のくさふゆむ".into();
        assert!(Candidate::from_model(&payload, &current, 0, player, Some(&retained)).is_none());
    }
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
