//! One player-selected discussion/editing target, independent of short dialogue history.
use crate::haiku_record::HaikuLine;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeSet;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Target {
    pub line_index: usize,
    pub line_id: String,
    pub fragment: String,
}

impl Target {
    /// Only player text can select a target. Multiple/conflicting locations do not move it.
    pub fn observe(current: &mut Option<Self>, text: &str, lines: &[HaikuLine]) {
        if let Some(target) = current {
            if !target.valid(lines) {
                *current = None;
            } else if !target.fragment.is_empty()
                && !contains(&lines[target.line_index], &target.fragment)
            {
                // An edit may replace the fragment, but its stable line remains selected.
                target.fragment.clear();
            }
        }
        if let Some(next) = Self::explicit(text, lines) {
            *current = Some(next);
        }
    }

    pub fn explicit(text: &str, lines: &[HaikuLine]) -> Option<Self> {
        if lines.len() != 3 {
            return None;
        }
        let explicit = crate::workshop_editing::explicit_line_indices(text);
        if explicit.len() > 1 {
            return None;
        }
        let mut mentions = vec![];
        for quoted in
            crate::reaction_leaf::sanitize::re(r"[「『]([^」』]+)[」』]").captures_iter(text)
        {
            let word = quoted[1].trim();
            if (2..=80).contains(&word.chars().count()) {
                for (i, line) in lines.iter().enumerate() {
                    if contains(line, word) {
                        mentions.push((i, word.to_owned()));
                    }
                }
            }
        }
        if mentions.is_empty() {
            for (i, line) in lines.iter().enumerate() {
                if [&line.surface_text, &line.reading_text]
                    .iter()
                    .any(|s| s.chars().count() >= 2 && text.contains(s.as_str()))
                {
                    mentions.push((i, line.surface_text.clone()));
                }
            }
        }
        let indices = mentions.iter().map(|(i, _)| *i).collect::<BTreeSet<_>>();
        if indices.len() > 1 {
            return None;
        }
        let index = explicit
            .first()
            .copied()
            .or_else(|| indices.first().copied())?;
        if indices.first().is_some_and(|i| *i != index) {
            return None;
        }
        let fragment = mentions
            .into_iter()
            .filter(|(i, _)| *i == index)
            .min_by_key(|(_, word)| word.chars().count())
            .map(|(_, word)| word)
            .unwrap_or_default();
        Some(Self {
            line_index: index,
            line_id: lines[index].line_id.clone(),
            fragment,
        })
    }

    pub fn valid(&self, lines: &[HaikuLine]) -> bool {
        lines.len() == 3
            && self.line_index < 3
            && lines[self.line_index].line_id == self.line_id
            && self.fragment.chars().count() <= 80
    }

    pub fn from_view(view: &Value, lines: &[HaikuLine]) -> Option<Self> {
        let mut target: Self = serde_json::from_value(view["discussion_target"].clone()).ok()?;
        if !target.valid(lines) {
            return None;
        }
        if !target.fragment.is_empty() && !contains(&lines[target.line_index], &target.fragment) {
            target.fragment.clear();
        }
        Some(target)
    }

    /// A model-resolved line reference has already passed the line-concept and
    /// verbatim-evidence checks. This permits new player wording beyond aliases.
    pub fn from_reference(reference: &Value, player: &str, lines: &[HaikuLine]) -> Option<Self> {
        let index = reference["line_index"].as_u64()? as usize;
        let evidence = reference["evidence"].as_str()?;
        if lines.len() != 3
            || index >= 3
            || evidence.chars().count() < 2
            || !player.contains(evidence)
            || reference["confidence"].as_f64()? < 0.75
        {
            return None;
        }
        Some(Self {
            line_index: index,
            line_id: lines[index].line_id.clone(),
            fragment: String::new(),
        })
    }

    pub fn context(&self, lines: &[HaikuLine]) -> Value {
        let line = &lines[self.line_index];
        json!({"line_index":self.line_index,"line_id":self.line_id,
            "canonical_name":line.canonical_name,"line_text":line.surface_text,
            "fragment":self.fragment,"retain_until_player_changes_target":true})
    }

    pub fn label(&self, lines: &[HaikuLine]) -> String {
        if self.fragment.is_empty() {
            format!(
                "{}の「{}」",
                lines[self.line_index].canonical_name, lines[self.line_index].surface_text
            )
        } else {
            format!("「{}」", self.fragment)
        }
    }
}

fn contains(line: &HaikuLine, fragment: &str) -> bool {
    line.surface_text.contains(fragment) || line.reading_text.contains(fragment)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn lines() -> Vec<HaikuLine> {
        let cases: Value = serde_json::from_str(include_str!(
            "../scripts/fixtures/workshop_meaning_cases.json"
        ))
        .unwrap();
        serde_json::from_value(cases["contexts"]["grass"]["current_lines"].clone()).unwrap()
    }

    #[test]
    fn model_resolves_new_player_line_wording_with_verbatim_evidence() {
        let lines = lines();
        let player = "今度は冒頭の五音を見よう";
        assert!(Target::explicit(player, &lines).is_none());
        let reference = json!({"line_index":0,"evidence":"冒頭の五音","confidence":0.95});
        let target = Target::from_reference(&reference, player, &lines).unwrap();
        assert_eq!(target.line_index, 0);
        assert!(Target::from_reference(&reference, "もう少し話そう", &lines).is_none());
    }

    #[test]
    fn explicit_player_target_survives_side_talk_and_only_explicit_new_target_moves_it() {
        let mut lines = lines();
        let mut target = None;
        Target::observe(
            &mut target,
            "のくさふゆむの「ふゆむ」って、どういう意味？",
            &lines,
        );
        let first = target.clone().unwrap();
        assert_eq!(first.line_index, 2);
        assert_eq!(first.fragment, "ふゆむ");
        for text in [
            "失敗を認めてもいいんだぞ？",
            "おわらんわ。",
            "だいぶ喋っとるで",
            "もう少し考えて",
            "やっぱりふわりにして",
        ] {
            Target::observe(&mut target, text, &lines);
            assert_eq!(target.as_ref(), Some(&first));
        }
        lines[2].surface_text = "くさゆれる".into();
        lines[2].reading_text = "くさゆれる".into();
        Target::observe(&mut target, "もう一度考えて", &lines);
        assert_eq!(target.as_ref().unwrap().line_index, 2);
        assert_eq!(target.as_ref().unwrap().fragment, "");
        Target::observe(&mut target, "次は上五を見よう", &lines);
        assert_eq!(target.as_ref().unwrap().line_index, 0);
        Target::observe(&mut target, "「てつじんみまもる」はどういう意味？", &lines);
        assert_eq!(target.as_ref().unwrap().line_index, 1);
        Target::observe(&mut target, "上五と下五はどう？", &lines);
        assert_eq!(target.as_ref().unwrap().line_index, 1);
        Target::observe(&mut target, "上五の「てつじんみまもる」", &lines);
        assert_eq!(target.as_ref().unwrap().line_index, 1);
    }
}
