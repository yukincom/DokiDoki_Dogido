//! Read-only workshop snapshots and request materials. No edits, state, saving,
//! dictionary lookup, or model calls. Snapshot data stays tied to its saved poem.
mod followup;
#[cfg(test)]
mod tests;
use crate::{
    chat_catalog::{strip, text, truth},
    haiku::source_atoms,
    haiku_record::HaikuLine,
};
use anyhow::{Context, Result};
pub use followup::fixed_followup;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeSet, HashSet, VecDeque},
    sync::LazyLock,
};
static RULES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../workshop_validation/assets.json"))
        .expect("workshop shared constants")
});
fn value_text(v: &Value) -> String {
    if truth(v) { text(v) } else { String::new() }
}
fn list(v: &Value) -> &[Value] {
    v.as_array().map_or(&[], Vec::as_slice)
}
fn cut(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}
use crate::compat::is_python_whitespace as space;
fn clean(s: &str) -> String {
    s.split(space)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}
use crate::haiku_record::verse::{reading as join_reading, surface as join_surface};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Snapshot {
    pub surface_text: String,
    pub current_lines: Vec<HaikuLine>,
    pub pending_revision: Option<String>,
    pub pending_revision_surface_text: Option<String>,
    pub pending_revision_lines: Vec<HaikuLine>,
    pub interpretation: Value,
    pub materials: Value,
    pub recent_dialogue: String,
    pub agent_steps: Vec<Value>,
    pub last_findings: Vec<Value>,
    pub last_repair_feedback: Value,
    pub awaiting_meaning_ack: bool,
    pub awaiting_close_confirmation: bool,
}
impl Snapshot {
    /// Matches workshop_helper.snapshot_for over the already-validated runtime
    /// view. It does not add last_findings/repair_feedback omitted by that bridge.
    pub fn from_view(view: &Value) -> Result<Self> {
        let emission = view
            .get("emission")
            .filter(|v| v.is_object())
            .context("missing workshop emission")?;
        let current = view.get("current_lines").unwrap_or(&emission["lines"]);
        let current_lines: Vec<HaikuLine> = serde_json::from_value(current.clone())?;
        let pending = view.get("pending").filter(|v| truth(v));
        let pending_lines: Vec<HaikuLine> = match pending {
            Some(p) => serde_json::from_value(p["lines"].clone())?,
            None => vec![],
        };
        let dialogue = dialogue_from_view(list(&view["dialogue"]))?;
        let steps = list(&view["agent_steps"]);
        let steps = steps[steps.len().saturating_sub(12)..].to_vec();
        Ok(Self {
            surface_text: join_reading(&current_lines),
            current_lines,
            pending_revision: pending.map(|_| join_reading(&pending_lines)),
            pending_revision_surface_text: pending.map(|_| join_surface(&pending_lines)),
            pending_revision_lines: pending_lines,
            interpretation: emission
                .get("interpretation")
                .cloned()
                .unwrap_or(Value::Null),
            materials: view
                .get("materials")
                .filter(|v| v.is_object())
                .context("missing workshop materials")?
                .clone(),
            recent_dialogue: dialogue,
            agent_steps: steps,
            last_findings: vec![],
            last_repair_feedback: json!({}),
            awaiting_meaning_ack: view["followup"] == "meaning_explained",
            awaiting_close_confirmation: view["followup"] == "close_confirmation",
        })
    }
    fn has_pending(&self) -> bool {
        self.pending_revision
            .as_ref()
            .is_some_and(|s| !s.is_empty())
    }
    fn display_line(&self) -> String {
        strip(&self.surface_text).into()
    }
    fn display_surface(&self) -> String {
        if self.current_lines.len() == 3
            && join_reading(&self.current_lines) == strip(&self.surface_text)
        {
            join_surface(&self.current_lines)
        } else {
            self.display_line()
        }
    }
    fn editing_line(&self) -> String {
        strip(
            self.pending_revision
                .as_deref()
                .filter(|s| !s.is_empty())
                .unwrap_or(&self.surface_text),
        )
        .into()
    }
    fn editing_surface(&self) -> String {
        if self.pending_revision_lines.len() == 3 {
            return join_surface(&self.pending_revision_lines);
        }
        if let Some(s) = self
            .pending_revision_surface_text
            .as_deref()
            .filter(|s| !s.is_empty())
        {
            return strip(s).into();
        }
        if self.has_pending() {
            return self.editing_line();
        }
        self.display_surface()
    }
}
fn dialogue_from_view(pairs: &[Value]) -> Result<String> {
    let mut utterances: VecDeque<(&str, String, String)> = VecDeque::new();
    for pair in &pairs[pairs.len().saturating_sub(4)..] {
        let id = cut(
            &clean(
                pair["turn_id"]
                    .as_str()
                    .context("workshop dialogue turn ID")?,
            ),
            180,
        );
        for (role, key) in [("プレイヤー", "player_text"), ("ドギド", "dogido_text")] {
            let value = clean(pair[key].as_str().context("workshop dialogue text")?);
            let value = if value.chars().count() > 320 {
                format!("{}…", cut(&value, 319))
            } else {
                value
            };
            if value.is_empty()
                || role == "ドギド" && matches!(value.as_str(), "ハッ" | "ハァハァ……" | "ハァハァ")
            {
                continue;
            }
            if !id.is_empty() && utterances.iter().any(|(r, i, _)| *r == role && i == &id) {
                continue;
            }
            if utterances.len() == 8 {
                utterances.pop_front();
            }
            utterances.push_back((role, id.clone(), value));
        }
    }
    Ok(utterances
        .iter()
        .map(|(role, _, value)| format!("{role}: {value}"))
        .collect::<Vec<_>>()
        .join("\n"))
}

pub fn workshop_context_details(workshop: &Snapshot) -> Value {
    let pending = workshop.has_pending() && workshop.pending_revision_lines.len() == 3;
    let lines = if pending {
        &workshop.pending_revision_lines
    } else {
        &workshop.current_lines
    };
    let mut atoms = source_atoms::source_atoms_from_materials(&workshop.materials);
    let line_ids = lines
        .iter()
        .flat_map(|l| l.source_atom_ids.iter().cloned())
        .collect::<HashSet<_>>();
    let basis_ids = atoms
        .iter()
        .filter(|a| line_ids.contains(&a.atom_id))
        .flat_map(|a| a.basis_atom_ids.iter().cloned())
        .collect::<HashSet<_>>();
    atoms.sort_by_key(|a| !line_ids.contains(&a.atom_id) && !basis_ids.contains(&a.atom_id));
    let sources=atoms.iter().take(24).map(|a|json!({"atom_id":a.atom_id,"text":cut(&a.text,240),"source_ref":a.source_ref,"observation_role":a.observation_role,"claim_class":a.claim_class,"basis_atom_ids":a.basis_atom_ids})).collect::<Vec<_>>();
    let interpretation = if truth(&workshop.interpretation) {
        &workshop.interpretation
    } else {
        &workshop.materials["interpretation"]
    };
    let mut context = json!({"current_verse":workshop.display_surface(),"pending_verse":if workshop.has_pending(){Some(workshop.editing_surface())}else{None},
        "interpretation":cut(&value_text(interpretation),1200),"source_atoms":sources,"line_sources_for":if pending{"pending_verse"}else{"current_verse"},
        "saved_line_sources":lines.iter().map(|l|json!({"line_index":l.line_index,"text":l.surface_text,"atom_ids":l.source_atom_ids,
            "sources":l.source_atoms.iter().take(3).map(|s|json!({"text":cut(&value_text(s.get("text").unwrap_or(&Value::Null)),240),"atom_id":s.get("atom_id").unwrap_or(&Value::Null)})).collect::<Vec<_>>(),"provenance":l.provenance})).collect::<Vec<_>>(),
        "recent_dialogue":workshop.recent_dialogue,"last_findings":workshop.last_findings.iter().take(3).collect::<Vec<_>>(),
        "recent_agent_steps":workshop.agent_steps[workshop.agent_steps.len().saturating_sub(6)..]});
    if workshop.last_repair_feedback.get("base_text")
        == Some(&Value::String(workshop.display_line()))
    {
        context["last_repair_result"] = workshop.last_repair_feedback.clone();
    }
    context
}
fn allowed_actions(workshop: &Snapshot, phase: &str) -> Vec<String> {
    let actions = match phase {
        "after_validation" => vec!["respond", "explain", "ask", "compare", "show_current"],
        "after_inspection" => vec![
            "respond",
            "explain",
            "ask",
            "propose_revision",
            "compare",
            "show_current",
            "stage_player_edit",
        ],
        _ => vec![
            "respond",
            "explain",
            "ask",
            "inspect",
            "propose_revision",
            "compare",
            "show_current",
            "stage_player_edit",
            "close_workshop",
            "unrelated",
        ],
    };
    let mut actions = actions.into_iter().collect::<BTreeSet<_>>();
    if workshop.has_pending() {
        if !matches!(phase, "after_validation" | "after_inspection") {
            actions.extend(["accept_pending", "reject_pending"]);
        }
        actions.remove("close_workshop");
    } else {
        for a in ["compare", "accept_pending", "reject_pending"] {
            actions.remove(a);
        }
    }
    actions.into_iter().map(str::to_owned).collect()
}
/// The complete canonical read projection before the helper-specific restrictions.
pub fn build_workshop_agent_details(
    workshop: &Snapshot,
    player: &str,
    original: Option<&str>,
    phase: &str,
    observation: &Value,
    turn_steps: &[Value],
) -> Value {
    let stage = if workshop.awaiting_close_confirmation {
        "close_confirmation"
    } else if workshop.awaiting_meaning_ack {
        "meaning_explained"
    } else if workshop.has_pending() {
        "pending_review"
    } else {
        "discussion"
    };
    let steps = &turn_steps[turn_steps.len().saturating_sub(4)..];
    let mut context = workshop_context_details(workshop);
    let recent = list(&context["recent_agent_steps"]);
    if !steps.is_empty() && recent.ends_with(steps) {
        context["recent_agent_steps"] = json!(recent[..recent.len() - steps.len()]);
    }
    json!({"phase":phase,"conversation_stage":stage,"canonical_verse":workshop.display_surface(),"canonical_reading":workshop.display_line(),
        "working_verse":workshop.editing_surface(),"working_reading":workshop.editing_line(),"pending_verse":if workshop.has_pending(){Some(workshop.editing_surface())}else{None},
        "player_text":strip(player),"original_player_text":strip(original.unwrap_or(player)),"workshop_context":context,"tool_observation":observation,
        "turn_steps":steps,"allowed_actions":allowed_actions(workshop,phase),"allowed_purposes":RULES["purposes"],"allowed_checks":["meter","reading","source"],"allowed_problem_types":RULES["problems"],"line_concepts":RULES["concepts"]})
}
/// Only the details portion of prepare_details. Fixed edit extraction remains a
/// separate operation until its neutral-reading and local-edit port is complete.
pub fn details_for(frame: &Value) -> Result<Value> {
    let view = &frame["workshop"];
    let workshop = Snapshot::from_view(view)?;
    let original = frame["text"].as_str().context("workshop original text")?;
    let player = frame
        .get("interpreted_text")
        .filter(|v| truth(v))
        .map(|v| v.as_str().context("workshop interpreted text"))
        .transpose()?
        .unwrap_or(original);
    let phase = frame["phase"].as_str().context("workshop phase")?;
    let frame_actions = frame["allowed_actions"]
        .as_array()
        .context("workshop allowed actions")?;
    let mut details = build_workshop_agent_details(
        &workshop,
        player,
        Some(original),
        phase,
        &frame["observation"],
        list(&frame["turn_steps"]),
    );
    let mut actions = list(&details["allowed_actions"])
        .iter()
        .filter(|a| frame_actions.contains(a))
        .cloned()
        .collect::<Vec<_>>();
    actions.extend(
        frame_actions
            .iter()
            .filter(|a| RULES["followups"].as_array().unwrap().contains(a))
            .cloned(),
    );
    let candidate = &view["conversation_candidate"];
    if frame_actions
        .iter()
        .any(|a| a == "stage_conversation_candidate")
        && candidate.is_object()
    {
        actions.push(json!("stage_conversation_candidate"));
        details["workshop_context"]["conversation_candidate"] = candidate.clone();
    }
    if truth(&view["current_player_idea"]) {
        details["workshop_context"]["current_player_idea"] = view["current_player_idea"].clone();
    }
    let lines = if workshop.pending_revision_lines.len() == 3 {
        &workshop.pending_revision_lines
    } else {
        &workshop.current_lines
    };
    if let Some(target) = crate::workshop_target::Target::from_view(view, lines) {
        details["workshop_context"]["discussion_target"] = target.context(lines);
    }
    details["allowed_actions"] = actions.into();
    if view["followup"] == "combat_resume_confirmation" {
        details["conversation_stage"] = json!("combat_resume_confirmation");
    }
    Ok(details)
}
pub fn revision_input(frame: &Value) -> Result<Value> {
    let workshop = Snapshot::from_view(&frame["workshop"])?;
    let lines = workshop
        .current_lines
        .iter()
        .map(|l| l.reading_text.clone())
        .collect::<Vec<_>>();
    let atoms = source_atoms::source_atoms_from_materials(&workshop.materials);
    let findings = frame["findings"].as_array().context("workshop findings")?;
    let targets = findings
        .iter()
        .filter_map(|f| f["line_index"].as_u64().filter(|i| *i < 3))
        .collect::<BTreeSet<_>>();
    let allowed = atoms
        .iter()
        .map(|a| a.atom_id.clone())
        .collect::<HashSet<_>>();
    let sources =
        source_atoms::line_source_ids_from_materials(&workshop.materials, &lines, &allowed);
    let mut details = workshop.materials.clone();
    details["workshop_context"] = workshop_context_details(&workshop);
    Ok(
        json!({"lines":lines,"line_sources":sources,"findings":findings,"basis":{"target_indices":targets,"source_atoms":atoms,"details":details},
        "max_tokens":frame["max_tokens"],"grounding_max_tokens":frame["grounding_max_tokens"]}),
    )
}
