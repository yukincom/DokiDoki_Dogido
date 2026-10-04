//! 発話から行・置換語を抽出し、未採用の編集案と検査結果を構成する。
//! 生入力はValue境界で受け、編集案・行差分・検査結果は型で渡す。句・pending・記憶は変更しない。
//! workshop_edit::Pendingが正本への差分とCASを検証し、dialogue/workshop_editsが採用・保存する。
//! 辞書の読みは明示的な依存であり、Pythonへ編集判断を渡さない。
mod edits;
pub mod materials;
mod parse;
#[cfg(test)]
mod tests;
use crate::{
    haiku::lexical, haiku_record::HaikuLine, python_worker::Helper, workshop_projection::Snapshot,
};
use anyhow::{Context, Result, ensure};
use parse::{compact, list, space, text, truth};
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;

/// Native outputs stay typed until a prompt/log serialization boundary. No state
/// mutation is authorized here; Pending::stage still validates the full lines.
#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum Output {
    WholeVerse {
        lines: Vec<HaikuLine>,
    },
    KnowledgeRoute {
        query: Option<crate::knowledge::query::Query>,
    },
    CombatFallback(crate::workshop_combat_input::Analysis),
    FragmentCandidate {
        fixed_payload: Option<FixedEditStep>,
    },
    DiscussionCandidate {
        candidate: Option<crate::workshop_candidate::Draft>,
    },
    PlayerEdit(PlayerEditResult),
}

#[derive(Debug, Serialize)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum FixedEditStep {
    StagePlayerEdit {
        purpose: EditPurpose,
        confidence: f64,
        evidence: String,
        speech: String,
        checks: Vec<String>,
        line_reference: LineReference,
        line_proposal: LineProposal,
    },
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EditPurpose {
    ImproveWording,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LineConcept {
    Unknown,
}
#[derive(Debug, Serialize)]
pub struct LineReference {
    pub found: bool,
    pub concept_id: LineConcept,
    pub evidence: String,
    pub confidence: f64,
}
#[derive(Debug, Serialize)]
pub struct LineProposal {
    pub found: bool,
    pub target_fragment: Option<String>,
    pub replacement_text: String,
    pub evidence: String,
    pub confidence: f64,
}
#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum PlayerEditResult {
    Rejected {
        text: Option<String>,
        failure_reasons: Vec<String>,
    },
    Validated(EditResult),
}
impl PlayerEditResult {
    pub fn failure_reasons(&self) -> &[String] {
        match self {
            Self::Rejected {
                failure_reasons, ..
            } => failure_reasons,
            Self::Validated(result) => &result.failure_reasons,
        }
    }
}
#[derive(Debug, Serialize)]
pub struct EditResult {
    pub text: Option<String>,
    pub base_text: String,
    pub surface_text: Option<String>,
    pub lines: Vec<HaikuLine>,
    pub edits: Vec<crate::workshop_edit::LineEdit>,
    pub failure_reasons: Vec<String>,
    pub target_line_index: Option<usize>,
}

pub(crate) fn explicit_line_indices(text: &str) -> std::collections::BTreeSet<usize> {
    parse::explicit_lines(text)
}

#[derive(Debug)]
struct NeedsReading(String);
impl std::fmt::Display for NeedsReading {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "neutral reading required")
    }
}
impl std::error::Error for NeedsReading {}
/// A turn owns one cache and the existing helper. Failed optional dictionary
/// lookup caches the original; IPC/cancellation errors never enter this cache.
#[derive(Default)]
pub struct Engine {
    readings: HashMap<String, String>,
}
impl Engine {
    fn read(&self, s: &str) -> Result<String> {
        if !crate::tts_reading::has_kanji(s) {
            return Ok(s.to_owned());
        }
        self.readings
            .get(s)
            .cloned()
            .ok_or_else(|| NeedsReading(s.into()).into())
    }
    fn normalized(&self, s: &str, player: bool) -> Result<Option<String>> {
        let mut source = s.trim_matches(space);
        if player {
            source = source.trim_matches(|c| "「」『』\"' 。．.!！?？…".contains(c));
        }
        if source.is_empty() || source.contains(['\n', '\r']) {
            return Ok(None);
        }
        let result = lexical::hiragana(&self.read(source)?)
            .chars()
            .filter(|c| !space(*c))
            .collect::<String>();
        Ok((!result.is_empty()
            && result
                .chars()
                .all(|c| ('ぁ'..='ゖ').contains(&c) || c == 'ー'))
        .then_some(result))
    }
    pub async fn run(&mut self, helper: &mut Helper, frame: &Value) -> Result<Output> {
        ensure!(
            serde_json::to_vec(frame)?.len() < 1_000_000,
            "workshop edit request too large"
        );
        loop {
            match self.project(frame) {
                Ok(result) => {
                    ensure!(
                        crate::text_format::spaced_json(&result).len() < 1_000_000,
                        "workshop edit response too large"
                    );
                    return Ok(result);
                }
                Err(error) => match error.downcast_ref::<NeedsReading>() {
                    Some(NeedsReading(source)) => {
                        let source = source.clone();
                        let reading = helper.neutral_reading(&source).await?;
                        self.readings.insert(source, reading);
                    }
                    None => return Err(error),
                },
            }
        }
    }
    fn project(&self, frame: &Value) -> Result<Output> {
        let input = text(&frame["text"]);
        if frame["op"] == "whole_verse" {
            return Ok(Output::WholeVerse {
                lines: self.whole_verse(input, text(&frame["source"]))?,
            });
        }
        if frame["op"] == "knowledge_route" {
            return self.knowledge_route(frame);
        }
        let view = &frame["workshop"];
        let snapshot = Snapshot::from_view(view)?;
        match text(&frame["op"]) {
            "combat_fallback" => {
                let source = input.trim_matches(space);
                let compact = source.split(space).collect::<String>();
                let resume = crate::workshop_projection::fixed_followup(
                    source,
                    crate::workshop_followup::Stage::CombatResumeConfirmation,
                    false,
                ) == Some("resume_workshop")
                    || [
                        "句を続け",
                        "句の続き",
                        "句に戻",
                        "川柳を続け",
                        "川柳の続き",
                        "ワークショップを続け",
                        "ワークショップに戻",
                        "推敲を続け",
                        "添削を続け",
                        "さっきの句",
                    ]
                    .iter()
                    .any(|s| compact.contains(s));
                use crate::workshop_combat_input::{Action, Analysis};
                let mut action = if source.is_empty() {
                    Action::Uncertain
                } else if resume {
                    Action::ResumeWorkshop
                } else if self.mentioned_line(&snapshot, source)?.is_some() {
                    Action::WorkshopInput
                } else {
                    Action::Uncertain
                };
                let confidence = if action == Action::Uncertain {
                    0.0
                } else {
                    1.0
                };
                let evidence = if action == Action::Uncertain {
                    ""
                } else {
                    source
                };
                if !crate::workshop_input_guard::combat_safe(action.name(), input, evidence) {
                    action = Action::Uncertain;
                }
                Ok(Output::CombatFallback(Analysis {
                    action,
                    confidence,
                    evidence: evidence.into(),
                }))
            }
            "fragment_candidate" => Ok(Output::FragmentCandidate {
                fixed_payload: self.fixed_fragment(frame, &snapshot)?,
            }),
            "explicit_discussion" => {
                let candidate = if parse::explicit_discussion(input) {
                    self.discussion(frame, &snapshot, &Value::Null)?
                } else {
                    None
                };
                Ok(Output::DiscussionCandidate { candidate })
            }
            "discussion_candidate" => Ok(Output::DiscussionCandidate {
                candidate: self.discussion(frame, &snapshot, &frame["proposal"])?,
            }),
            "player_edit" => self.player_edit(frame, &snapshot).map(Output::PlayerEdit),
            _ => anyhow::bail!("unsupported native workshop edit operation"),
        }
    }
    fn knowledge_route(&self, frame: &Value) -> Result<Output> {
        let input = text(&frame["text"]);
        let prepared = crate::player_text::prepare(input);
        let context = crate::input_context::Context::from_prepared(
            &prepared,
            &[],
            chrono::Utc::now().fixed_offset(),
        );
        let query = context.knowledge_query;
        let Some(query) = query else {
            return Ok(Output::KnowledgeRoute { query: None });
        };
        if context.wants_quiet || prepared.normalized_text.starts_with('/') {
            return Ok(Output::KnowledgeRoute { query: None });
        }
        let snapshot = Snapshot::from_view(&frame["workshop"])?;
        let question = frame["interpreted_text"]
            .as_str()
            .filter(|s| !s.is_empty())
            .unwrap_or(input);
        let subject = query.subject.split(space).collect::<String>();
        let whole = [
            "この",
            "今の",
            "いまの",
            "さっきの",
            "先ほどの",
            "今詠んだ",
            "いま詠んだ",
        ]
        .iter()
        .any(|s| subject.starts_with(s))
            && ["句", "川柳", "俳句", "三行"]
                .iter()
                .any(|s| subject.contains(s));
        let related = whole
            || self.mentioned_line(&snapshot, question)?.is_some()
            || self.material_for_question(&snapshot, question)?.is_some();
        Ok(Output::KnowledgeRoute {
            query: (!related).then_some(query),
        })
    }
    fn whole_verse(&self, input: &str, provenance: &str) -> Result<Vec<HaikuLine>> {
        let surfaces = parse::verse_lines(input);
        if surfaces.len() != 3 {
            return Ok(vec![]);
        }
        let mut readings = Vec::new();
        for s in &surfaces {
            readings.push(self.normalized(s, false)?)
        }
        if readings.iter().any(Option::is_none) {
            return Ok(vec![]);
        }
        Ok(surfaces
            .into_iter()
            .zip(readings)
            .enumerate()
            .map(|(i, (surface, reading))| {
                let (id, position, name) = crate::haiku_record::LinePosition::ALL[i].metadata();
                HaikuLine {
                    line_id: id.into(),
                    line_index: i,
                    position: position.into(),
                    canonical_name: name.into(),
                    surface_text: surface,
                    reading_text: reading.unwrap(),
                    source_atom_ids: vec![],
                    source_atoms: vec![],
                    provenance: provenance.into(),
                }
            })
            .collect())
    }
    fn mentioned_line(&self, s: &Snapshot, input: &str) -> Result<Option<String>> {
        let source = input.trim_matches(space);
        if source.is_empty() {
            return Ok(None);
        }
        let spoken = compact(&self.read(source)?);
        if spoken.is_empty() {
            return Ok(None);
        }
        let lines = editing_lines(s);
        if lines.len() != 3 {
            return Ok(None);
        }
        let matched = lines
            .into_iter()
            .filter(|line| {
                let c = compact(line);
                c.chars().count() >= 2 && spoken.contains(&c)
            })
            .collect::<Vec<_>>();
        Ok(if matched.len() == 1 {
            matched.into_iter().next()
        } else {
            None
        })
    }
    fn mentioned_fragment(
        &self,
        s: &Snapshot,
        input: &str,
        replacement: &str,
    ) -> Result<Option<String>> {
        if let Some(line) = self.mentioned_line(s, input)? {
            return Ok(Some(line));
        }
        let Some(at) = input.find(replacement) else {
            return Ok(None);
        };
        let before = input[..at].trim_end_matches(|c| "「『\"' 、， ".contains(c));
        let Some(separator) = ["より", "から", "を"]
            .iter()
            .find(|s| before.ends_with(**s))
        else {
            return Ok(None);
        };
        let before =
            before[..before.len() - separator.len()].trim_matches(|c| "「『\"' 、， ".contains(c));
        let before = parse::fragment_prefix(before)
            .trim_matches(|c| "「『\"' 、， ".contains(c))
            .to_owned();
        let Some(reading) = self.normalized(&before, true)? else {
            return Ok(None);
        };
        let lines = editing_lines(s);
        Ok((reading.chars().count() >= 2
            && lines.len() == 3
            && lines
                .iter()
                .map(|l| l.matches(&reading).count())
                .sum::<usize>()
                == 1)
            .then_some(before))
    }
}
fn editing_records(s: &Snapshot) -> &[HaikuLine] {
    if s.pending_revision_lines.len() == 3 {
        &s.pending_revision_lines
    } else {
        &s.current_lines
    }
}
fn editing_lines(s: &Snapshot) -> Vec<String> {
    parse::verse_lines(
        s.pending_revision
            .as_deref()
            .filter(|v| !v.is_empty())
            .unwrap_or(&s.surface_text),
    )
}
