//! 通常会話の短期履歴と、戦闘前の会話の一時退避を管理する。
//! 永続保存や再生判定は行わず、assistantを加える時機は呼出側が配送結果から決める。
//! 沈黙とコード由来の出来事はeventとして残し、発話済みのassistantや川柳用の会話対には数えない。
use serde_json::{Value, json};
use std::collections::{HashSet, VecDeque};

const MAX_TURNS: usize = 20;
const COMPACT_TURNS: usize = 10;
const AUXILIARY_ROWS: usize = 40;
const ACTIVE_SUMMARIES: usize = 5;

/// 配送確定済みの古い10ターン。生成中の追加・取消・訂正と照合する元行を保持する。
pub(super) struct Compaction {
    pub rows: Vec<Value>,
    pub turns: usize,
}

#[derive(Clone, Default)]
pub struct History {
    rows: VecDeque<Value>,
    summaries: Vec<Value>,
    compacted_ids: HashSet<String>,
    summary_sequence: usize,
    deferred_for_haiku: bool,
    danger_retained: Vec<Value>,
    danger_active: bool,
    post_danger_turns: u64,
    counted_player_turns: Vec<String>,
    situations: VecDeque<&'static str>,
}
fn normalize(text: &str) -> String {
    text.split(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}
impl History {
    /// 通常は20ターン揃った次の入力境界で古い10ターンを渡す。あらすじ自体は再要約しない。
    /// 未回答入力は往復に数えず、確定した沈黙は発話なしの応答として扱う。
    pub(super) fn compaction(&self) -> Option<Compaction> {
        let rows = self.rows_with_summaries(&[]);
        let turns = terminal_turns(&rows);
        // 開始条件は通常・川柳終了後とも20ターン。川柳による保留で緩めない。
        if turns.len() < MAX_TURNS {
            return None;
        }
        // 川柳中に溜めた分は終了後に一括し、直近10往復だけを原文で残す。
        let count = if self.deferred_for_haiku {
            turns.len() - COMPACT_TURNS
        } else {
            COMPACT_TURNS
        };
        let all: HashSet<String> = turns
            .iter()
            .flat_map(|id| [id.to_string(), format!("{id}:reply")])
            .collect();
        let ids: HashSet<String> = turns
            .into_iter()
            .take(count)
            .flat_map(|id| [id.to_owned(), format!("{id}:reply")])
            .collect();
        let end = rows
            .iter()
            .rposition(|r| ids.contains(r["turn_id"].as_str().unwrap_or("")))?;
        // 同じ期間の確定した警告・出来事も材料へ含める。後のターンや未回答入力は残す。
        let rows = rows
            .into_iter()
            .take(end + 1)
            .filter(|r| {
                let id = r["turn_id"].as_str().unwrap_or("");
                ids.contains(id)
                    || (r["role"] != "user" && r["reaction"] != "summary" && !all.contains(id))
            })
            .collect();
        Some(Compaction { rows, turns: count })
    }
    pub(super) fn defer_for_haiku(&mut self) {
        self.deferred_for_haiku = true;
    }
    /// 読込範囲だけを絞る。終了後の要約材料となる原文は削除しない。
    pub(super) fn recent_turn_rows(rows: &[Value], limit: usize) -> Vec<Value> {
        let turns = terminal_turns(rows);
        if turns.len() <= limit {
            return rows.to_vec();
        }
        if limit == 0 {
            return Vec::new();
        }
        let first = turns[turns.len() - limit];
        let start = rows.iter().position(|r| r["turn_id"] == first).unwrap();
        rows[start..].to_vec()
    }
    pub(super) fn workshop_rows(&self) -> Vec<Value> {
        Self::recent_turn_rows(&self.model_rows(), MAX_TURNS)
    }
    /// 停止時の未要約分。聞いた入力と実際に配送された返答・出来事だけを渡す。
    pub(super) fn final_compaction(&self) -> Option<Compaction> {
        let rows = self.rows_with_summaries(&[]);
        let turns = terminal_turns(&rows).len();
        (!rows.is_empty()).then_some(Compaction { rows, turns })
    }
    /// 非空のあらすじと元行の一致を確認してから置換する。失敗時は元行を一切削除しない。
    pub(super) fn commit_compaction(&mut self, batch: &Compaction, text: &str) -> bool {
        let text = normalize(text);
        let current = self.rows_with_summaries(&[]);
        if text.is_empty()
            || batch.rows.is_empty()
            || batch.rows.iter().any(|row| !current.contains(row))
        {
            return false;
        }
        let ids: HashSet<String> = batch
            .rows
            .iter()
            .filter_map(|r| r["turn_id"].as_str().map(str::to_owned))
            .collect();
        self.rows
            .retain(|r| !ids.contains(r["turn_id"].as_str().unwrap_or("")));
        self.danger_retained
            .retain(|r| !ids.contains(r["turn_id"].as_str().unwrap_or("")));
        self.compacted_ids.extend(ids);
        self.summary_sequence += 1;
        let row = json!({"turn_id":format!("summary:{}", self.summary_sequence),
            "role":"event", "reaction":"summary", "text":text, "turn_count":batch.turns});
        self.summaries.push(row);
        self.deferred_for_haiku = false;
        true
    }
    pub(super) fn synopses(&self) -> Vec<crate::journey::Synopsis> {
        self.summaries
            .iter()
            .map(|row| crate::journey::Synopsis {
                text: row["text"].as_str().unwrap_or("").into(),
                turns: row["turn_count"].as_u64().unwrap_or(0) as usize,
            })
            .collect()
    }
    /// テキスト相談室のsnapshot引継ぎで、沈黙・相談のターン属性とあらすじを保つ。
    pub(super) fn restore_row(&mut self, row: &Value) {
        let (Some(id), Some(role), Some(text)) = (
            row["turn_id"].as_str(),
            row["role"].as_str(),
            row["text"].as_str(),
        ) else {
            return;
        };
        if text.trim().is_empty() {
            return;
        }
        match (role, row["reaction"].as_str()) {
            ("event", Some("summary")) => {
                self.summary_sequence += 1;
                let mut row = row.clone();
                row["turn_id"] = format!("summary:{}", self.summary_sequence).into();
                self.summaries.push(row);
            }
            ("event", Some("story")) => {
                self.story_record(id, text, row["dialogue_turn"] == true);
            }
            ("event", Some("silent")) => self.silent(id.strip_suffix(":reply").unwrap_or(id)),
            ("user", _) => self.push(id, role, text),
            ("assistant", _) => self.push(id.strip_suffix(":reply").unwrap_or(id), role, text),
            _ => {}
        }
    }
    fn trim_auxiliary(&mut self) {
        let mut rows = self.rows.drain(..).collect();
        trim_auxiliary(&mut rows, &HashSet::new());
        self.rows = rows.into();
    }
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
        }
    }
    /// 呼出側が配送を確定したuser/assistantの対を返す。未回答・取消入力や沈黙は対にしない。
    pub fn completed_pairs(&self) -> Vec<Value> {
        let mut pairs: Vec<Value> =
            self.rows
                .iter()
                .filter(|r| r["role"] == "user")
                .filter_map(|r| {
                    let id = r["turn_id"].as_str()?;
                    let reply = self.rows.iter().find(|p| {
                        p["turn_id"] == format!("{id}:reply") && p["role"] == "assistant"
                    })?;
                    Some(json!({"turn_id":id,"player_text":r["text"],"dogido_text":reply["text"]}))
                })
                .collect();
        if pairs.len() > 3 {
            pairs.drain(..pairs.len() - 3);
        }
        pairs
    }
    /// 保存・復元用の全要約と未要約本文。会話へ渡すときはmodel_rowsを使う。
    pub fn rows(&self) -> Vec<Value> {
        self.rows_with_summaries(&self.summaries)
    }
    /// 会話用は末尾5件だけを最初から複写する。古い要約は保存用に原文のまま残す。
    pub(super) fn model_rows(&self) -> Vec<Value> {
        self.rows_with_summaries(self.active_summaries())
    }
    fn active_summaries(&self) -> &[Value] {
        &self.summaries[self.summaries.len().saturating_sub(ACTIVE_SUMMARIES)..]
    }
    fn rows_with_summaries(&self, summaries: &[Value]) -> Vec<Value> {
        let mut rows = summaries.to_vec();
        rows.extend(self.danger_retained.clone());
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
    pub fn retained_count(&self) -> usize {
        self.danger_retained.len()
    }
    /// Refresh completed reactions without consuming this input's frozen bookmark.
    pub fn refresh_before_input(
        &self,
        turn: &str,
        captured: &[Value],
        retained: usize,
    ) -> Vec<Value> {
        // 受付後に圧縮した元行を凍結済みsnapshotから復活させず、最新の要約を先頭へ置く。
        let mut rows = self.active_summaries().to_vec();
        rows.extend(
            captured
                .iter()
                .filter(|row| {
                    row["reaction"] != "summary"
                        && !self
                            .compacted_ids
                            .contains(row["turn_id"].as_str().unwrap_or(""))
                })
                .cloned(),
        );
        for row in self.model_rows() {
            if row["turn_id"] == turn {
                continue;
            }
            if let Some(saved) = rows
                .iter_mut()
                .find(|saved| saved["turn_id"] == row["turn_id"])
            {
                *saved = row;
            } else if row["role"] == "assistant" || row["role"] == "event" {
                rows.push(row);
            }
        }
        // 第3入力の受付時に退避期限が切れても、その入力のbookmarkは維持する。
        let protected: HashSet<String> = captured
            .iter()
            .filter(|r| r["reaction"] != "summary")
            .take(retained)
            .filter_map(|row| row["turn_id"].as_str().map(str::to_owned))
            .collect();
        trim_auxiliary(&mut rows, &protected);
        rows
    }
    #[cfg(test)]
    pub fn lines(&self) -> String {
        Self::lines_for(&self.rows())
    }
    pub fn lines_for(rows: &[Value]) -> String {
        let mut lines = Vec::new();
        for row in rows {
            if row["reaction"] == "summary" {
                lines.push(format!(
                    "過去の冒険のあらすじ（現在の観測ではない）: {}",
                    row["text"].as_str().unwrap_or("")
                ));
                continue;
            }
            if row["role"] == "event" && row["reaction"] == "silent" {
                lines.push("ドギドの反応: 黙って受け止めた（沈黙を選択。発話なし）".into());
                continue;
            }
            if row["role"] == "event" && row["reaction"] == "story" {
                lines.push(format!("出来事: {}", row["text"].as_str().unwrap_or("")));
                continue;
            }
            lines.push(format!(
                "{}: {}",
                if row["role"] == "user" {
                    "プレイヤー"
                } else {
                    "ドギド"
                },
                row["text"].as_str().unwrap_or("")
            ));
        }
        lines.join("\n")
    }
    pub fn push(&mut self, turn: &str, role: &str, text: &str) {
        let text = normalize(text);
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
        if self.compacted_ids.contains(&id) || self.rows().iter().any(|r| r["turn_id"] == id) {
            return;
        }
        self.rows
            .push_back(json!({"turn_id":id,"role":role,"text":text}));
        self.trim_auxiliary();
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
    /// コードが確定した出来事を原文のまま保持し、同じIDの補完を退避側にも反映する。
    /// 呼出側が本文の範囲とIDを決める。出来事は発話や通常会話の完了対へ昇格しない。
    pub fn story(&mut self, event_id: &str, text: &str) -> bool {
        self.story_record(event_id, text, false)
    }
    /// 相談・記憶操作の配送済み一往復。句の材料にはせず、対話の保持数には一ターンと数える。
    pub fn story_turn(&mut self, event_id: &str, text: &str) -> bool {
        self.story_record(event_id, text, true)
    }
    fn story_record(&mut self, event_id: &str, text: &str, dialogue_turn: bool) -> bool {
        if event_id.trim().is_empty()
            || text.trim().is_empty()
            || self.compacted_ids.contains(event_id)
        {
            return false;
        }
        // 別種の発話・反応とIDが衝突しても、その記録を書き換えない。
        if self.rows.iter().chain(&self.danger_retained).any(|row| {
            row["turn_id"] == event_id && !(row["role"] == "event" && row["reaction"] == "story")
        }) {
            return false;
        }
        let mut replacement =
            json!({"turn_id":event_id,"role":"event","reaction":"story","text":text});
        if dialogue_turn
            || self
                .rows
                .iter()
                .chain(&self.danger_retained)
                .any(|r| r["turn_id"] == event_id && r["dialogue_turn"] == true)
        {
            replacement["dialogue_turn"] = true.into();
        }
        let mut found = false;
        let mut changed = false;
        for row in self.rows.iter_mut().chain(&mut self.danger_retained) {
            if row["turn_id"] == event_id {
                found = true;
                if *row != replacement {
                    *row = replacement.clone();
                    changed = true;
                }
            }
        }
        if found {
            return changed;
        }
        self.rows.push_back(replacement);
        self.trim_auxiliary();
        true
    }
    /// A chosen response, never a fabricated spoken assistant message.
    pub fn silent(&mut self, turn: &str) {
        let id = format!("{turn}:reply");
        if self.compacted_ids.contains(&id) || self.rows().iter().any(|r| r["turn_id"] == id) {
            return;
        }
        self.rows
            .push_back(json!({"turn_id":id,"role":"event","reaction":"silent",
            "text":"ドギドは黙って受け止めた（沈黙を選択。発話なし）"}));
        self.trim_auxiliary();
    }
}

/// 一往復のIDを発言順で返す。沈黙は対話文脈には含め、completed_pairsの発話対とは分ける。
fn terminal_turns(rows: &[Value]) -> Vec<&str> {
    rows.iter()
        .filter(|r| r["role"] == "user" || r["dialogue_turn"] == true)
        .filter_map(|row| {
            let id = row["turn_id"].as_str()?;
            if row["dialogue_turn"] == true {
                return Some(id);
            }
            rows.iter()
                .any(|r| {
                    r["turn_id"] == format!("{id}:reply")
                        && (r["role"] == "assistant" || r["reaction"] == "silent")
                })
                .then_some(id)
        })
        .collect()
}
/// 環境発話・出来事・未回答行の補助枠。完成した対話は警告の連発で押し出さない。
fn trim_auxiliary(rows: &mut Vec<Value>, protected: &HashSet<String>) {
    let paired: HashSet<String> = terminal_turns(rows)
        .into_iter()
        .flat_map(|id| [id.to_owned(), format!("{id}:reply")])
        .collect();
    let auxiliary = |r: &Value| {
        r["reaction"] != "summary"
            && !paired.contains(r["turn_id"].as_str().unwrap_or(""))
            && !protected.contains(r["turn_id"].as_str().unwrap_or(""))
    };
    let mut remove = rows
        .iter()
        .filter(|r| auxiliary(r))
        .count()
        .saturating_sub(AUXILIARY_ROWS);
    rows.retain(|r| {
        if remove > 0 && auxiliary(r) {
            remove -= 1;
            false
        } else {
            true
        }
    });
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
    fn precombat_bookmark_releases_after_three_inputs_without_evicting_recent_dialogue() {
        let mut h = seeded();
        h.begin_danger();
        for i in 0..12 {
            pair(&mut h, &format!("during-{i}"));
            h.begin_danger(); // repeated threat snapshots must not overwrite the bookmark
        }
        assert_eq!(h.rows().len(), 34);
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
        assert_eq!(h.rows().len(), 40);
        assert_eq!(h.retained_count(), 0);
        assert!(contains(&h, "old-0"));
    }

    #[test]
    fn refresh_preserves_third_input_bookmark_and_adds_delivered_reactions() {
        let mut h = seeded();
        h.begin_danger();
        h.end_danger(3);
        pair(&mut h, "after-0");
        pair(&mut h, "after-1");
        let before = h.rows();
        let retained = h.retained_count();
        h.push("third", "user", "さっきの続き");
        assert_eq!(h.retained_count(), 0);
        h.push("rain", "assistant", "雨やな。");
        h.silent("smell");
        let projected = h.refresh_before_input("third", &before, retained);
        assert!(projected.iter().any(|r| r["turn_id"] == "old-0"));
        assert!(!projected.iter().any(|r| r["turn_id"] == "third"));
        assert_eq!(projected[projected.len() - 2]["turn_id"], "rain:reply");
        assert_eq!(projected.last().unwrap()["reaction"], "silent");
        assert!(History::lines_for(&projected).contains("雨やな。"));
        assert!(History::lines_for(&projected).contains("沈黙を選択"));
        assert_eq!(h.retained_count(), 0);
    }

    #[test]
    fn refresh_keeps_normal_and_retained_history_bounded_without_restarting_lifetime() {
        for retain in [false, true] {
            let mut h = seeded();
            if retain {
                h.begin_danger();
                h.end_danger(1);
            }
            let before = h.rows();
            let retained = h.retained_count();
            h.push("current", "user", "今の話");
            for i in 0..45 {
                h.push(&format!("reaction-{i}"), "assistant", "雨やな。");
            }
            let projected = h.refresh_before_input("current", &before, retained);
            assert_eq!(projected.len(), 10 + AUXILIARY_ROWS);
            assert!(projected.iter().any(|r| r["turn_id"] == "old-0"));
            assert_eq!(projected.last().unwrap()["turn_id"], "reaction-44:reply");
            assert_eq!(h.retained_count(), 0);
            assert!(
                h.refresh_before_input("next", &h.rows(), 0)
                    .iter()
                    .any(|r| r["turn_id"] == "old-0")
            );
        }
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
        assert_eq!(h.danger_retained[0]["turn_id"], "old-0");
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
    fn chosen_silence_is_a_reaction_visible_next_turn_but_never_a_spoken_pair() {
        let mut h = History::default();
        h.push("a", "user", "今日は静かやな");
        h.silent("a");
        h.silent("a");
        h.replace_unanswered("a");
        assert_eq!(h.rows().len(), 2);
        assert_eq!(h.rows()[1]["role"], "event");
        assert!(h.lines().contains("黙って受け止めた"));
        assert!(h.completed_pairs().is_empty());
        assert_eq!(h.rows()[1]["reaction"], "silent");
        h.push("rain", "assistant", "雨が降ってきたな。");
        assert!(h.lines().contains("雨が降ってきたな"));
        assert!(h.completed_pairs().is_empty());
        pair(&mut h, "b");
        assert_eq!(h.completed_pairs().len(), 1);
    }

    #[test]
    fn story_keeps_full_text_and_updates_both_current_and_retained_copies() {
        let mut h = History::default();
        h.push("a", "user", "川柳の話を続けよう");
        let original = format!(
            "プレイヤーの言葉: {}\n返答は戦闘のため未発話。",
            "句の話。".repeat(50)
        );
        assert!(original.chars().count() > 160);
        assert!(h.story("story:a", &original));
        assert!(!h.story("story:a", &original));
        assert_eq!(h.rows()[1]["text"], original);
        assert!(h.lines().contains(&format!("出来事: {original}")));
        assert!(!h.lines().contains(&format!("ドギド: {original}")));
        assert!(h.completed_pairs().is_empty());

        h.begin_danger();
        let completed = format!("{original}\n予定していた返答: 続きも一緒に考えよか。");
        assert!(h.story("story:a", &completed));
        assert!(!h.story("story:a", &completed));
        for rows in [
            h.rows.iter().collect::<Vec<_>>(),
            h.danger_retained.iter().collect(),
        ] {
            let matching: Vec<_> = rows.iter().filter(|r| r["turn_id"] == "story:a").collect();
            assert_eq!(matching.len(), 1);
            assert_eq!(matching[0]["text"], completed);
            assert_eq!(matching[0]["role"], "event");
            assert_eq!(matching[0]["reaction"], "story");
        }
        assert_eq!(h.rows().len(), 2);
        assert!(h.completed_pairs().is_empty());
        h.push("a", "assistant", "続きも一緒に考えよか。");
        assert_eq!(h.completed_pairs().len(), 1);
    }

    #[test]
    fn story_preserves_retention_limits_and_never_replaces_a_spoken_row() {
        let mut h = History::default();
        assert!(h.story("story:before", "戦闘前に川柳の話をした。"));
        h.begin_danger();
        for i in 0..41 {
            assert!(h.story(&format!("story:{i}"), "戦闘の出来事。"));
        }
        assert_eq!(h.rows.len(), AUXILIARY_ROWS);
        assert_eq!(h.rows.front().unwrap()["turn_id"], "story:1");
        h.end_danger(3);
        assert!(!h.story("story:before", "戦闘前に川柳の話をした。"));
        assert!(h.story("story:before", "戦闘前の川柳相談。返答は未発話。"));
        assert!(!h.rows.iter().any(|r| r["turn_id"] == "story:before"));
        assert_eq!(
            h.danger_retained[0]["text"],
            "戦闘前の川柳相談。返答は未発話。"
        );
        assert_eq!(h.post_danger_turns, 3);
        assert!(h.completed_pairs().is_empty());

        h.push("spoken", "assistant", "実際に届けた返答。");
        assert!(!h.story("spoken:reply", "この出来事では上書きしない。"));
        assert_eq!(h.rows.back().unwrap()["role"], "assistant");
        assert_eq!(h.rows.back().unwrap()["text"], "実際に届けた返答。");
        assert!(!h.story("", "出来事。"));
        assert!(!h.story("story:empty", "\n "));
    }
    #[test]
    fn twenty_turns_compact_ten_at_a_time_and_keep_full_recent_text() {
        let mut h = History::default();
        let long = "長い入力を最後まで覚える。".repeat(20);
        h.push("t0", "user", &long);
        h.push("t0", "assistant", "そやな。");
        for i in 1..19 {
            pair(&mut h, &format!("t{i}"));
        }
        assert!(h.compaction().is_none());
        assert_eq!(h.rows()[0]["text"], long);
        pair(&mut h, "t19");
        let captured = h.rows();
        h.begin_danger();
        let batch = h.compaction().unwrap();
        assert_eq!(batch.rows.len(), 20);
        assert_eq!(batch.rows[0]["turn_id"], "t0");
        assert_eq!(batch.rows[19]["turn_id"], "t9:reply");
        h.push("current", "user", "新しい話");
        assert!(!h.commit_compaction(&batch, " "));
        assert_eq!(h.rows()[0]["text"], long);
        assert!(h.commit_compaction(&batch, "最初の十往復の話題。"));
        assert!(!h.commit_compaction(&batch, "同じ要約を重ねない。"));
        assert!(!contains(&h, "t0"));
        assert!(contains(&h, "t10"));
        let projected = h.refresh_before_input("current", &captured, 40);
        assert_eq!(projected[0]["reaction"], "summary");
        assert!(
            !projected
                .iter()
                .any(|r| r["turn_id"] == "t0" || r["turn_id"] == "current")
        );
        assert!(History::lines_for(&projected).contains("現在の観測ではない"));
        assert!(h.compaction().is_none());
        for i in 20..30 {
            pair(&mut h, &format!("t{i}"));
        }
        let batch = h.compaction().unwrap();
        assert_eq!(batch.rows[0]["turn_id"], "t10");
        assert!(h.commit_compaction(&batch, "次の十往復の話題。"));
        assert_eq!(h.summaries.len(), 2);
        assert_eq!(
            h.completed_pairs()
                .iter()
                .map(|r| r["turn_id"].as_str().unwrap())
                .collect::<Vec<_>>(),
            ["t27", "t28", "t29"]
        );
        assert!(h.danger_retained.is_empty());
        h.push("t0", "user", "遅れた重複");
        h.silent("t0");
        assert!(!contains(&h, "t0"));
    }

    #[test]
    fn compaction_ignores_unanswered_inputs_and_standalone_reactions() {
        let mut h = History::default();
        for i in 0..50 {
            h.push(&format!("pending{i}"), "user", "未回答");
            h.push(&format!("warning{i}"), "assistant", "警告");
        }
        assert!(h.compaction().is_none());
        assert_eq!(h.rows.len(), AUXILIARY_ROWS);
        for i in 0..20 {
            h.push(&format!("silent{i}"), "user", "話しかける");
            h.silent(&format!("silent{i}"));
        }
        let batch = h.compaction().unwrap();
        assert_eq!(terminal_turns(&batch.rows).len(), 10);
        assert!(h.completed_pairs().is_empty());
        // 生成中の訂正を古い要約で上書きしない。
        let row = h
            .rows
            .iter_mut()
            .find(|r| r["turn_id"] == "silent0")
            .unwrap();
        row["text"] = json!("訂正済みの話");
        assert!(!h.commit_compaction(&batch, "古い要約"));
        assert!(h.summaries.is_empty());
        assert!(contains(&h, "silent0"));
    }

    #[test]
    fn delivered_workshop_turns_count_toward_twenty_but_not_poem_material_pairs() {
        let mut h = History::default();
        for i in 0..20 {
            assert!(h.story_turn(
                &format!("workshop:{i}"),
                &format!("プレイヤーの相談{i}。ドギドの返答。")
            ));
            assert!(!h.story_turn(
                &format!("workshop:{i}"),
                &format!("プレイヤーの相談{i}。ドギドの返答。")
            ));
        }
        let batch = h.compaction().unwrap();
        assert_eq!(batch.turns, 10);
        assert_eq!(batch.rows.len(), 10);
        assert!(h.completed_pairs().is_empty());
        assert!(h.commit_compaction(&batch, "川柳の言葉選びを相談した。"));
        assert!(h.compaction().is_none());
        assert_eq!(h.final_compaction().unwrap().turns, 10);
    }

    #[test]
    fn old_synopses_remain_in_archive_but_only_last_five_enter_model_context() {
        let mut h = History::default();
        let mut captured = Vec::new();
        for i in 0..120 {
            pair(&mut h, &format!("t{i}"));
            if let Some(batch) = h.compaction() {
                assert!(h.commit_compaction(&batch, &format!("冒険の記録{i}")));
                if i == 69 {
                    captured = h.model_rows();
                }
            }
        }
        assert_eq!(h.synopses().len(), 11);
        assert!(h.synopses().iter().all(|p| p.turns == 10));
        let projected = h.model_rows();
        let summary_ids = |rows: &[Value]| {
            rows.iter()
                .filter(|r| r["reaction"] == "summary")
                .map(|r| r["turn_id"].as_str().unwrap().to_owned())
                .collect::<Vec<_>>()
        };
        let expected = (7..=11).map(|i| format!("summary:{i}")).collect::<Vec<_>>();
        assert_eq!(summary_ids(&projected), expected);
        assert_eq!(
            summary_ids(&h.refresh_before_input("new", &captured, 0)),
            expected
        );
        assert!(contains(&h, "t110"));
        assert!(!contains(&h, "t109"));
        assert_eq!(h.final_compaction().unwrap().turns, 10);
        assert!(
            h.compaction().is_none(),
            "stored summaries never create another model call"
        );
    }
    #[test]
    fn final_turn_count_excludes_unanswered_input_and_keeps_its_text() {
        let mut h = History::default();
        for i in 0..10 {
            pair(&mut h, &format!("t{i}"));
        }
        h.push("unanswered", "user", "返答のない入力も補作せず残す");
        let batch = h.final_compaction().unwrap();
        assert_eq!(batch.turns, 10);
        assert!(batch.rows.iter().any(|r| r["turn_id"] == "unanswered"));
    }
}
