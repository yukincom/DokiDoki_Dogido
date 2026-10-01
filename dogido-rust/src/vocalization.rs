//! 叫声だけの音声を会話から分離する。感情・原因はモデルにも文字列にも推定させない。
use crate::events::{EventName, GameEvent, HostileOutcomeEvidence, HostileOutcomeOutcome};
use icu_normalizer::ComposingNormalizer;

pub const UNKNOWN: &str = "状況：驚いた声を検出。原因は不明。";

pub fn is_pure(text: &str) -> bool {
    let normalized = ComposingNormalizer::new_nfkc().normalize(text);
    let trimmed = normalized.trim_matches(|c| " \t\r\n、,。.!！?？〜~…()（）".contains(c));
    if trimmed.is_empty() || trimmed.contains(['「', '」', '『', '』']) {
        return false;
    }
    let chars: Vec<char> = trimmed
        .chars()
        .filter(|c| {
            !(c.is_whitespace()
                || ('\u{1c}'..='\u{1f}').contains(c)
                || "、,。.!！?？…".contains(*c))
        })
        .collect();
    let Some((&first, rest)) = chars.split_first() else {
        return false;
    };
    let tail = |allowed: &str, minimum: usize| {
        rest.len() >= minimum && rest.iter().all(|c| allowed.contains(*c))
    };
    (first == 'う' && tail("おぉォオー〜~", 2))
        || (first == 'う' && tail("わあぁァアー〜~", 2))
        || (first == 'わ' && tail("あぁァアー〜~", 2))
        || ("ぎひき".contains(first) && tail("ゃャあぁァアー〜~", 2))
        || ("あぁァアうぅゥウ".contains(first) && tail("あぁァアうぅゥウー〜~", 3))
}

/// 同時点のコード観測だけ。爆発/死亡は視認より優先する。
pub fn observed_note(event: &GameEvent) -> &'static str {
    if event.event.name == EventName::CreeperDetonated
        || event
            .combat
            .hostile_outcomes
            .as_ref()
            .is_some_and(|outcomes| {
                outcomes.iter().any(|o| {
                    o.outcome == HostileOutcomeOutcome::CreeperDetonation
                        && o.evidence == HostileOutcomeEvidence::ExplosionPacket
                })
            })
    {
        "状況：クリーパーの爆発をコードで観測。"
    } else if event.event.name == EventName::PlayerDied {
        "状況：プレイヤーの死亡をコードで観測。原因の詳細は未確定。"
    } else if !event.visual_threats.is_empty() {
        "状況：近くの敵対モブを視認。"
    } else if !event.auditory_threats.is_empty() {
        "状況：敵らしい音を観測。具体名は未確定。"
    } else {
        UNKNOWN
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    #[test]
    fn matches_python_closed_rules_and_observed_notes() {
        let fixtures: Value =
            serde_json::from_str(include_str!("../fixtures/vocalization.json")).unwrap();
        for case in fixtures["texts"].as_array().unwrap() {
            assert_eq!(
                is_pure(case["text"].as_str().unwrap()),
                case["pure"].as_bool().unwrap(),
                "{case}"
            );
        }
        for case in fixtures["observations"].as_array().unwrap() {
            let event = GameEvent::parse(case["event"].clone()).unwrap();
            assert_eq!(observed_note(&event), case["note"].as_str().unwrap());
        }
    }
}
