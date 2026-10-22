//! 会話の所有権と発句準備等による一時的な話題。本文生成・世界観測・永続保存はしない。
//! 戦闘中も会話routeを保持する。警告で未回答になった入力はdialogueのreply_queueが管理する。
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Route {
    #[default]
    None,
    Casual,
    Knowledge,
    HaikuPreparation,
    HaikuWorkshop,
}
impl Route {
    pub fn blocks_haiku(self) -> bool {
        matches!(self, Self::Knowledge)
    }
}

/// 発句までの経過時間。休止中を足さず、解除時に残り時間から再開する。
#[derive(Debug, Default)]
pub struct IntervalClock {
    last: Option<u64>,
    elapsed: u64,
    paused: bool,
}
impl IntervalClock {
    pub fn sync(&mut self, now: u64, paused: bool) {
        if let Some(last) = self.last {
            if !self.paused {
                self.elapsed = self.elapsed.saturating_add(now.saturating_sub(last));
            }
            self.last = Some(now.max(last));
        }
        self.paused = paused;
    }
    pub fn start(&mut self, now: u64) {
        self.last.get_or_insert(now);
    }
    pub fn reset(&mut self, now: u64) {
        self.last = Some(now);
        self.elapsed = 0;
    }
    pub fn elapsed(&self, now: u64) -> u64 {
        self.elapsed.saturating_add(if self.paused {
            0
        } else {
            self.last.map_or(0, |last| now.saturating_sub(last))
        })
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct SuspendedTopic {
    pub route: Route,
    pub summary: String,
    pub source_turn_ids: Vec<String>,
    pub remaining_player_turns: u64,
    pub reason: String,
}
#[derive(Debug)]
struct Completed {
    id: String,
    player: String,
    route: Route,
}

#[derive(Debug, Default)]
pub struct State {
    pub route: Route,
    pub started_at: Option<u64>,
    pub last_player_at: Option<u64>,
    pub combat_active: bool,
    pub game_paused: bool,
    pub suspended: Option<SuspendedTopic>,
    pub clock: IntervalClock,
    completed: VecDeque<Completed>,
    selected_turn: String,
}

impl State {
    pub fn set_game_paused(&mut self, now: u64, paused: bool) {
        self.clock.sync(now, paused || self.route.blocks_haiku());
        self.game_paused = paused;
    }
    pub fn haiku_clock_paused(&self) -> bool {
        self.game_paused || self.route.blocks_haiku()
    }
    pub fn activate(&mut self, route: Route, now: u64, player_at: Option<u64>) {
        self.clock
            .sync(now, self.game_paused || route.blocks_haiku());
        if self.route != route {
            self.route = route;
            self.started_at = Some(now);
            if route == Route::HaikuWorkshop {
                self.completed.clear();
            }
        }
        if let Some(at) = player_at {
            self.last_player_at = Some(at);
        }
    }
    pub fn clear(&mut self, now: u64) {
        self.clock.sync(now, self.game_paused);
        self.route = Route::None;
        self.started_at = None;
        self.last_player_at = None;
        self.completed.clear();
    }
    pub fn tick(&mut self, now: u64, ttl: u64) {
        if matches!(self.route, Route::Casual | Route::Knowledge)
            && let Some(at) = self.last_player_at
            && now.saturating_sub(at) >= ttl
        {
            // 観測が途切れていても、期限後まで資料を確認していたことにしない。
            self.clear(at.saturating_add(ttl));
        }
        self.clock.sync(now, self.haiku_clock_paused());
    }
    /// 生成前のrouting成立時に一度だけ数える。再試行・再生通知では延長しない。
    pub fn select(
        &mut self,
        turn: &str,
        text: &str,
        route: Route,
        now: u64,
        player_at: u64,
    ) -> Route {
        if self.selected_turn == turn {
            return self.route;
        }
        self.selected_turn = turn.to_owned();
        let mut selected = route;
        if let Some(topic) = &mut self.suspended {
            if looks_like_resume(text) {
                selected = topic.route;
                self.suspended = None;
            } else {
                topic.remaining_player_turns = topic.remaining_player_turns.saturating_sub(1);
                if topic.remaining_player_turns == 0 {
                    self.suspended = None;
                }
            }
        }
        self.activate(selected, now, Some(player_at));
        selected
    }
    pub fn completed(&mut self, turn: &str, player: &str, reply: &str, route: Route) {
        if turn.trim().is_empty()
            || player.trim().is_empty()
            || reply.trim().is_empty()
            || !matches!(route, Route::Casual | Route::Knowledge)
        {
            return;
        }
        self.completed.retain(|r| r.id != turn);
        self.completed.push_back(Completed {
            id: clean(turn, 160),
            player: clean(player, 160),
            route,
        });
        while self.completed.len() > 5 {
            self.completed.pop_front();
        }
    }
    pub fn suspend(&mut self, now: u64, reason: &str, hold: u64) {
        if !matches!(self.route, Route::Casual | Route::Knowledge) {
            return;
        }
        let turns = self
            .completed
            .iter()
            .filter(|r| r.route == self.route)
            .rev()
            .take(3)
            .collect::<Vec<_>>();
        let phrases = turns
            .iter()
            .rev()
            .map(|r| format!("『{}』", clean(&r.player, 12)))
            .collect::<Vec<_>>();
        let summary = if phrases.is_empty() {
            String::new()
        } else {
            clean(
                &format!("プレイヤーと{}について話していた", phrases.join("、")),
                80,
            )
        };
        self.suspended = Some(SuspendedTopic {
            route: self.route,
            summary,
            source_turn_ids: turns.iter().rev().map(|r| r.id.clone()).collect(),
            remaining_player_turns: hold.max(1),
            reason: reason.to_owned(),
        });
        self.clock.sync(now, self.game_paused);
        self.route = Route::None;
        self.started_at = None;
    }
    /// 戦闘の事実だけを更新する。通常会話を続けるため、routeと学習文脈は保持する。
    pub fn start_combat(&mut self) {
        self.combat_active = true;
    }
    pub fn finish_combat(&mut self) {
        self.combat_active = false;
    }
    pub fn resume_prompt(&self, text: &str) -> String {
        self.suspended
            .as_ref()
            .filter(|t| !t.summary.is_empty() && looks_like_resume(text))
            .map_or_else(String::new, |t| {
                format!("プレイヤーが明示的に再開した保留話題: {}", t.summary)
            })
    }
    pub fn snapshot(&self, now: u64) -> serde_json::Value {
        serde_json::json!({"route":self.route,"combat_active":self.combat_active,
            "blocks_new_haiku":self.haiku_clock_paused(),"suspended":self.suspended,
            "last_player_at_ms":self.last_player_at,"haiku_elapsed_ms":self.clock.elapsed(now)})
    }
}

fn clean(text: &str, max: usize) -> String {
    let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if text.chars().count() <= max {
        text
    } else {
        format!("{}…", text.chars().take(max - 1).collect::<String>())
    }
}
pub fn looks_like_resume(text: &str) -> bool {
    [
        "さっきの話",
        "前の話",
        "話の続き",
        "続き話",
        "続きやけど",
        "続きを",
        "戻るけど",
        "戻ろ",
    ]
    .iter()
    .any(|marker| text.contains(marker))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn game_pause_freezes_remaining_time_across_route_changes_without_resume_burst() {
        let mut s = State::default();
        s.clock.start(0);
        s.set_game_paused(300_000, true);
        s.activate(Route::Knowledge, 400_000, Some(400_000));
        s.tick(800_000, 300_000); // 資料照会の期限が切れてもMinecraftのポーズは保持する。
        assert_eq!(s.route, Route::None);
        s.activate(Route::Casual, 900_000, Some(900_000));
        s.suspend(1_000_000, "combat", 10);
        s.clear(1_200_000);
        s.tick(1_400_000, 300_000);
        assert!(s.game_paused);
        assert_eq!(s.clock.elapsed(1_500_000), 300_000);
        assert_eq!(s.snapshot(1_500_000)["haiku_elapsed_ms"], 300_000);
        s.set_game_paused(1_500_000, false);
        assert_eq!(s.clock.elapsed(1_500_000), 300_000);
        assert_eq!(s.clock.elapsed(1_799_999), 599_999);
        assert_eq!(s.clock.elapsed(1_800_000), 600_000);
    }
    #[test]
    fn unpausing_game_does_not_unpause_knowledge() {
        let mut s = State::default();
        s.clock.start(0);
        s.activate(Route::Knowledge, 100_000, Some(100_000));
        s.set_game_paused(200_000, true);
        s.set_game_paused(900_000, false);
        assert_eq!(s.clock.elapsed(1_000_000), 100_000);
        s.clear(1_000_000);
        assert_eq!(s.clock.elapsed(1_500_000), 600_000);
    }
    #[test]
    fn knowledge_freezes_remaining_interval_and_expires_at_the_deadline() {
        let mut s = State::default();
        s.clock.start(0);
        s.activate(Route::Casual, 0, Some(0));
        s.activate(Route::Knowledge, 300_000, Some(300_000));
        s.tick(599_999, 300_000);
        assert_eq!(s.clock.elapsed(599_999), 300_000);
        // tick無しで期限を越えても、資料照会終了後の1秒だけを加算する。
        s.tick(601_000, 300_000);
        assert_eq!(s.route, Route::None);
        assert_eq!(s.clock.elapsed(601_000), 301_000);
        s.activate(Route::Knowledge, 602_000, Some(602_000));
        s.activate(Route::Casual, 900_000, Some(900_000));
        assert_eq!(s.clock.elapsed(901_000), 303_000);
    }
    #[test]
    fn combat_keeps_conversation_owner_and_accepts_new_turns() {
        let mut s = State::default();
        s.activate(Route::Knowledge, 0, Some(0));
        s.completed(
            "heard",
            "枕詞って何？",
            "定型的な言葉やで。",
            Route::Knowledge,
        );
        s.start_combat();
        for now in 1001..1200 {
            s.start_combat();
            s.tick(now, 300_000);
        }
        assert_eq!(s.route, Route::Knowledge);
        assert!(s.suspended.is_none());
        assert_eq!(
            s.select("battle", "ここは何バイオーム？", Route::Casual, 2000, 2000),
            Route::Casual
        );
        assert!(s.combat_active);
        s.finish_combat();
        assert_eq!(s.route, Route::Casual);
        assert!(s.suspended.is_none());
    }
    #[test]
    fn haiku_preparation_keeps_its_explicit_topic_resume() {
        let mut s = State::default();
        s.activate(Route::Knowledge, 0, Some(0));
        s.completed(
            "heard",
            "枕詞って何？",
            "定型的な言葉やで。",
            Route::Knowledge,
        );
        s.suspend(1, "haiku_preparation", 10);
        assert_eq!(s.suspended.as_ref().unwrap().source_turn_ids, ["heard"]);
        assert!(s.resume_prompt("さっきの話の続き").contains("枕詞"));
        assert_eq!(
            s.select("resume", "さっきの話の続き", Route::Casual, 3000, 3000),
            Route::Knowledge
        );
        assert!(s.suspended.is_none());
    }
    #[test]
    fn suspended_topic_expires_after_ten_other_routed_turns() {
        let mut s = State::default();
        s.activate(Route::Casual, 0, Some(0));
        s.suspend(1, "haiku_preparation", 10);
        for n in 0..10 {
            s.select(&format!("turn{n}"), "別の話", Route::Casual, n + 2, n + 2);
        }
        assert!(s.suspended.is_none());
    }
    #[test]
    fn clock_reset_during_pause_and_backward_time_never_double_count() {
        let mut c = IntervalClock::default();
        c.start(100);
        c.sync(200, true);
        c.reset(250);
        c.sync(300, false);
        c.sync(290, false);
        assert_eq!(c.elapsed(310), 10);
    }
}
