use super::{Data, Dialogue};
use crate::foreground::Route;
use anyhow::{Result, ensure};

impl Dialogue {
    pub(super) fn tick_foreground(&self, d: &mut Data, sid: &str) {
        self.tick_address(d, sid);
        if let Some(s) = d.sessions.get_mut(sid) {
            let now = self.clock.elapsed().as_millis() as u64;
            let ttl = self.config.combat.ms("conversation_active_ttl_ms");
            super::web_runtime::tick(s, now, ttl);
            if s.foreground.route == Route::Web && !s.web.state.active() {
                s.foreground.clear(now);
            }
            s.foreground.tick(now, s.web.state.ttl(ttl));
            if matches!(
                s.foreground.route,
                Route::HaikuPreparation | Route::HaikuWorkshop
            ) && !s.haiku.foreground()
            {
                s.foreground.clear(now);
            }
        }
    }

    /// モデル/DB結果の適用と同じsession epochで会話所有権を確定する。
    pub(super) fn select_foreground(
        &self,
        sid: &str,
        turn: &str,
        epoch: u64,
        route: Route,
    ) -> Result<()> {
        let mut d = self.data.lock().unwrap();
        ensure!(
            !d.stopped && d.sessions.get(sid).is_some_and(|s| s.epoch == epoch),
            "stale conversation route"
        );
        self.tick_foreground(&mut d, sid);
        let row = d
            .rows
            .iter()
            .find(|r| r["turn_id"] == turn)
            .ok_or_else(|| anyhow::anyhow!("missing conversation turn"))?;
        let text = row["player_input_text"].as_str().unwrap_or("").to_owned();
        let now = self.clock.elapsed().as_millis() as u64;
        let player_at = row["input_at_ms"].as_u64().unwrap_or(now);
        let selected = d
            .sessions
            .get_mut(sid)
            .unwrap()
            .foreground
            .select(turn, &text, route, now, player_at);
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["conversation_route"] = serde_json::to_value(selected)?;
        }
        d.revision += 1;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dialogue::DialogueConfig;
    use serde_json::json;
    use tokio::sync::watch;

    #[tokio::test]
    async fn cancelled_turn_cannot_reacquire_foreground_or_supply_a_topic() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("s", "試験", true);
        {
            let mut d = dialogue.data.lock().unwrap();
            d.rows.push_back(json!({"session_id":"s", "turn_id":"heard",
                "input_at_ms":0, "player_input_text":"枕詞って何？"}));
        }
        let result = json!({"text":"定型的な言葉やで。", "knowledge_status":"found"});
        dialogue.update("s", "heard", 0, "queued", Some(&result));
        dialogue.update("s", "heard", 0, "completed", Some(&result));
        {
            let mut d = dialogue.data.lock().unwrap();
            d.rows
                .push_back(json!({"session_id":"s", "turn_id":"cancelled",
                "input_at_ms":0, "player_input_text":"新しい話"}));
            d.sessions.get_mut("s").unwrap().cancel = Some(watch::channel(false).0);
            Dialogue::cancel_chat(&mut d, "s", "new_player_input");
            d.sessions
                .get_mut("s")
                .unwrap()
                .foreground
                .start_combat(1, 10);
        }
        assert!(
            dialogue
                .select_foreground("s", "cancelled", 0, Route::Learning)
                .is_err()
        );
        assert!(!dialogue.update("s", "cancelled", 0, "completed", Some(&result)));
        {
            let d = dialogue.data.lock().unwrap();
            let foreground = &d.sessions["s"].foreground;
            assert_eq!(foreground.route, Route::None);
            assert_eq!(
                foreground.suspended.as_ref().unwrap().source_turn_ids,
                ["heard"]
            );
        }
        dialogue.shutdown().await;
    }
}
