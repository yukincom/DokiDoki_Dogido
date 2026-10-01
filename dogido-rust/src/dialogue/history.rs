use crate::planner::repair::{self, Repair};
use serde_json::{Value, json};
use std::collections::VecDeque;

#[derive(Default)]
pub struct History {
    rows: VecDeque<Value>,
    danger_retained: Vec<Value>,
    danger_active: bool,
    post_danger_turns: u64,
    counted_player_turns: Vec<String>,
    // 入力前に生成器へ渡した履歴。今回の訂正対象照合だけに使い、次のpromptへ戻さない。
    repair_context: Option<(String, Vec<Value>)>,
    situations: VecDeque<&'static str>,
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
    /// 戦闘前の通常履歴だけを退避する。短い再戦で退避済みの会話を上書きしない。
    pub fn begin_danger(&mut self) {
        if !self.danger_active {
            self.counted_player_turns.clear();
            self.situations.clear();
        }
        if !self.danger_active && self.danger_retained.is_empty() {
            self.danger_retained = self.rows.iter().cloned().collect();
        }
        self.danger_active = true;
        self.post_danger_turns = 0;
    }
    pub fn end_danger(&mut self, player_turns: u64) {
        if !self.danger_active {
            return;
        }
        self.danger_active = false;
        self.post_danger_turns = player_turns;
        if player_turns == 0 {
            self.danger_retained.clear();
        }
    }
    pub fn retention_status(&self) -> Value {
        json!({"danger_active":self.danger_active,
            "retained_utterances":self.danger_retained.len(),
            "remaining_player_turns":self.post_danger_turns})
    }
    pub fn note_situation(&mut self, text: &'static str) {
        if self.situations.back() != Some(&text) {
            if self.situations.len() == 8 {
                self.situations.pop_front();
            }
            self.situations.push_back(text);
        }
    }
    pub fn situation_lines(&self) -> Vec<&'static str> {
        if self.danger_active || self.post_danger_turns > 0 {
            self.situations.iter().copied().collect()
        } else {
            vec![]
        }
    }
    /// 同じ実入力の再開時にだけ古い未回答turnを置き換える。診断行は保持する。
    pub fn replace_unanswered(&mut self, turn: &str) {
        if !self
            .rows()
            .iter()
            .any(|r| r["turn_id"] == format!("{turn}:reply"))
        {
            self.rows
                .retain(|r| !(r["role"] == "user" && r["turn_id"] == turn));
            self.danger_retained
                .retain(|r| !(r["role"] == "user" && r["turn_id"] == turn));
            if self
                .repair_context
                .as_ref()
                .is_some_and(|(id, _)| id == turn)
            {
                self.repair_context = None;
            }
        }
    }
    /// 実再生完了に対応するuser/assistantの対だけ。未回答・取消入力は除く。
    pub fn completed_pairs(&self) -> Vec<Value> {
        let mut pairs: Vec<Value> = self
            .rows
            .iter()
            .filter(|r| r["role"] == "user")
            .filter_map(|r| {
                let id = r["turn_id"].as_str()?;
                let reply = self
                    .rows
                    .iter()
                    .find(|p| p["turn_id"] == format!("{id}:reply"))?;
                Some(json!({"turn_id":id,"player_text":r["text"],"dogido_text":reply["text"]}))
            })
            .collect();
        if pairs.len() > 3 {
            pairs.drain(..pairs.len() - 3);
        }
        pairs
    }
    pub fn rows(&self) -> Vec<Value> {
        let mut rows = self.danger_retained.clone();
        for row in &self.rows {
            if let Some(saved) = rows.iter_mut().find(|r| r["turn_id"] == row["turn_id"]) {
                // 退避後に付いた訂正注記も同じ発話の一部として見せる。
                *saved = row.clone();
            } else {
                rows.push(row.clone());
            }
        }
        rows
    }
    pub fn lines(&self) -> String {
        let mut lines = Vec::new();
        for row in self.rows() {
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
                lines.push(format!("  [{}]", repair::note(&row)));
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
        if self.rows().iter().any(|r| r["turn_id"] == id) {
            return;
        }
        if role == "user" {
            self.repair_context = Some((turn.into(), self.rows()));
        } else if self
            .repair_context
            .as_ref()
            .is_some_and(|(id, _)| id == turn)
        {
            self.repair_context = None;
        }
        if self.rows.len() == 10 {
            self.rows.pop_front();
        }
        self.rows
            .push_back(json!({"turn_id":id,"role":role,"text":text}));
        if role == "user"
            && !self.danger_active
            && self.post_danger_turns > 0
            && !self.counted_player_turns.iter().any(|id| id == turn)
        {
            self.counted_player_turns.push(turn.into());
            self.post_danger_turns -= 1;
            if self.post_danger_turns == 0 {
                self.danger_retained.clear();
                self.counted_player_turns.clear();
            }
        }
    }
    pub fn annotate(&mut self, turn: &str, repair: &Repair) {
        let targets = self
            .repair_context
            .as_ref()
            .filter(|(id, _)| id == turn)
            .map_or_else(|| self.rows(), |(_, rows)| rows.clone());
        if !targets.iter().any(|r| {
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
            if let Some(saved) = self
                .danger_retained
                .iter_mut()
                .find(|r| r["turn_id"] == turn)
            {
                *saved = row.clone();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pair(h: &mut History, turn: &str) {
        h.push(turn, "user", &format!("話題{turn}"));
        h.push(turn, "assistant", &format!("返事{turn}"));
    }
    fn seeded() -> History {
        let mut h = History::default();
        for i in 0..5 {
            pair(&mut h, &format!("old-{i}"));
        }
        h
    }
    fn contains(h: &History, turn: &str) -> bool {
        h.rows().iter().any(|r| r["turn_id"] == turn)
    }

    #[test]
    fn situation_notes_expire_with_retention_and_do_not_cross_danger_episodes() {
        let mut h = seeded();
        h.begin_danger();
        h.note_situation("敵の観測");
        h.begin_danger();
        h.note_situation("敵の観測");
        assert_eq!(h.situation_lines(), ["敵の観測"]);
        h.end_danger(3);
        pair(&mut h, "after-0");
        assert_eq!(h.situation_lines(), ["敵の観測"]);
        h.begin_danger();
        assert!(h.situation_lines().is_empty());
        for note in ["1", "2", "3", "4", "5", "6", "7", "8", "9"] {
            h.note_situation(note);
        }
        assert_eq!(
            h.situation_lines(),
            ["2", "3", "4", "5", "6", "7", "8", "9"]
        );
        h.end_danger(1);
        pair(&mut h, "after-1");
        assert!(h.situation_lines().is_empty());
    }

    #[test]
    fn precombat_history_survives_rollover_and_expires_after_three_distinct_inputs() {
        let mut h = seeded();
        h.begin_danger();
        for i in 0..12 {
            pair(&mut h, &format!("during-{i}"));
            h.begin_danger(); // repeated threat snapshots must not overwrite the bookmark
        }
        assert_eq!(h.rows().len(), 20);
        assert!(contains(&h, "old-0:reply"));
        h.end_danger(3);
        for i in 0..3 {
            // Input projection precedes the push: the third response still sees the bookmark.
            assert!(contains(&h, "old-0"));
            let id = format!("after-{i}");
            pair(&mut h, &id);
            pair(&mut h, &id);
            h.end_danger(3); // duplicate combat_ended must not extend the lifetime
            assert_eq!(h.post_danger_turns, 2 - i);
        }
        assert_eq!(h.rows().len(), 10);
        assert!(!contains(&h, "old-0"));
    }

    #[test]
    fn short_reentry_keeps_first_bookmark_and_zero_releases_it() {
        let mut h = seeded();
        h.begin_danger();
        h.end_danger(3);
        pair(&mut h, "after-0");
        h.begin_danger();
        pair(&mut h, "during");
        assert_eq!(h.danger_retained[0]["turn_id"], "old-0");
        assert_eq!(h.post_danger_turns, 0);
        h.end_danger(0);
        assert!(h.danger_retained.is_empty());
        h.begin_danger();
        assert_ne!(h.danger_retained[0]["turn_id"], "old-0");
    }

    #[test]
    fn empty_bookmark_does_not_capture_incombat_dialogue_on_repeated_threat() {
        let mut h = History::default();
        h.begin_danger();
        pair(&mut h, "during");
        h.begin_danger();
        assert!(h.danger_retained.is_empty());
        h.end_danger(3);
        h.push("", "user", "   ");
        assert_eq!(h.post_danger_turns, 3);
    }

    #[test]
    fn replay_drops_unanswered_retained_row_and_counts_same_input_once() {
        let mut h = seeded();
        h.push("pending", "user", "未回答");
        h.begin_danger();
        for i in 0..6 {
            pair(&mut h, &format!("during-{i}"));
        }
        h.replace_unanswered("pending");
        assert!(!contains(&h, "pending"));
        h.replace_unanswered("old-4");
        assert!(contains(&h, "old-4"));
        h.end_danger(3);
        h.push("replay", "user", "再開");
        h.replace_unanswered("replay");
        h.push("replay", "user", "再開");
        assert_eq!(h.post_danger_turns, 2);
        assert!(!h.completed_pairs().iter().any(|p| p["turn_id"] == "old-4"));
    }

    #[test]
    fn correction_can_reference_retained_turn_without_rewriting_original() {
        let mut h = seeded();
        h.push("correction", "user", "違う、材料を集めたいだけ");
        h.begin_danger();
        let repair = Repair {
            action: crate::planner::Action::RepairConversation,
            target_turn_id: "old-1:reply".into(),
            target_quote: "返事old-1".into(),
            signal_quote: "違う".into(),
            replacement_quote: "材料を集めたいだけ".into(),
            current_text: "違う、材料を集めたいだけ".into(),
        };
        h.annotate("correction", &repair);
        for i in 0..6 {
            pair(&mut h, &format!("during-{i}"));
        }
        let rows = h.rows();
        let corrected = rows.iter().find(|r| r["turn_id"] == "correction").unwrap();
        assert_eq!(corrected["repair_target_turn_id"], "old-1:reply");
        assert_eq!(corrected["text"], repair.current_text);
        assert!(h.lines().contains(&repair::note(corrected)));
        h.push("new-correction", "user", &repair.current_text);
        h.annotate("new-correction", &repair);
        assert_eq!(
            h.rows.back().unwrap()["repair_target_turn_id"],
            "old-1:reply"
        );
        assert_eq!(h.rows()[1]["text"], "話題old-1");
    }

    #[test]
    fn last_retained_input_can_record_repair_but_next_input_cannot_reuse_expired_target() {
        let mut h = seeded();
        h.begin_danger();
        h.end_danger(3);
        pair(&mut h, "after-0");
        pair(&mut h, "after-1");
        let repair = Repair {
            action: crate::planner::Action::RepairConversation,
            target_turn_id: "old-0:reply".into(),
            target_quote: "返事old-0".into(),
            signal_quote: "違う".into(),
            replacement_quote: "材料の話".into(),
            current_text: "違う、材料の話".into(),
        };
        assert!(contains(&h, "old-0:reply"));
        h.push("third", "user", &repair.current_text);
        assert!(!contains(&h, "old-0:reply"));
        h.annotate("third", &repair);
        assert_eq!(
            h.rows.back().unwrap()["repair_target_turn_id"],
            "old-0:reply"
        );
        h.push("fourth", "user", &repair.current_text);
        h.annotate("fourth", &repair);
        assert!(h.rows.back().unwrap().get("repair_action").is_none());
    }
}
