use crate::planner::repair::{self, Repair};
use serde_json::{Value, json};
use std::collections::VecDeque;

#[derive(Default)]
pub struct History {
    rows: VecDeque<Value>,
}
fn clip(text: &str) -> String {
    let text = text
        .split(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    if text.chars().count() <= 80 {
        text
    } else {
        text.chars().take(79).chain(['…']).collect()
    }
}
impl History {
    pub fn rows(&self) -> Vec<Value> {
        self.rows.iter().cloned().collect()
    }
    pub fn lines(&self) -> String {
        let mut lines = Vec::new();
        for row in &self.rows {
            lines.push(format!(
                "{}: {}",
                if row["role"] == "user" {
                    "プレイヤー"
                } else {
                    "ドギド"
                },
                row["text"].as_str().unwrap_or("")
            ));
            if row.get("repair_action").is_some() {
                lines.push(format!("  [{}]", repair::note(row)));
            }
        }
        lines.join("\n")
    }
    pub fn push(&mut self, turn: &str, role: &str, text: &str) {
        let text = clip(text);
        if text.is_empty()
            || (role == "assistant" && ["ハッ", "ハァハァ……", "ハァハァ"].contains(&text.as_str()))
        {
            return;
        }
        let id = if role == "assistant" {
            format!("{turn}:reply")
        } else {
            turn.into()
        };
        if self.rows.iter().any(|r| r["turn_id"] == id) {
            return;
        }
        if self.rows.len() == 10 {
            self.rows.pop_front();
        }
        self.rows
            .push_back(json!({"turn_id":id,"role":role,"text":text}));
    }
    pub fn annotate(&mut self, turn: &str, repair: &Repair) {
        if !self.rows.iter().any(|r| {
            r["turn_id"] == repair.target_turn_id
                && r["text"]
                    .as_str()
                    .is_some_and(|t| t.contains(&repair.target_quote))
        }) {
            return;
        }
        if let Some(row) = self.rows.iter_mut().find(|r| {
            r["turn_id"] == turn && r["role"] == "user" && r["text"] == clip(&repair.current_text)
        }) && row.get("repair_action").is_none()
        {
            row.as_object_mut()
                .unwrap()
                .extend(repair.prompt_fields().as_object().unwrap().clone());
        }
    }
}
