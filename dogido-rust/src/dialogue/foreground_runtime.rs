use super::{Data, Dialogue};
use crate::foreground::Route;
use anyhow::{Result, ensure};

impl Dialogue {
    pub(super) fn tick_foreground(&self, d: &mut Data, sid: &str) {
        if let Some(s) = d.sessions.get_mut(sid) {
            let now = self.clock.elapsed().as_millis() as u64;
            let ttl = self.config.combat.ms("conversation_active_ttl_ms");
            s.foreground.tick(now, ttl);
            if matches!(
                s.foreground.route,
                Route::HaikuPreparation | Route::HaikuWorkshop
            ) && !s.haiku.foreground()
            {
                if s.foreground.route == Route::HaikuWorkshop {
                    // 一時的な採取ペースはworkshopの終了時から数える。作句中の経過時間で即発句しない。
                    s.foreground.clock.reset(now);
                }
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
    use crate::haiku_record::{PreparedEmission, Workshop};
    use crate::playback::Status as PlaybackStatus;
    use serde_json::json;
    use std::time::Instant;
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
        dialogue.update("s", "heard", 0, PlaybackStatus::Queued, Some(&result));
        dialogue.update("s", "heard", 0, PlaybackStatus::Completed, Some(&result));
        {
            let mut d = dialogue.data.lock().unwrap();
            d.rows
                .push_back(json!({"session_id":"s", "turn_id":"cancelled",
                "input_at_ms":0, "player_input_text":"新しい話"}));
            d.sessions.get_mut("s").unwrap().cancel = Some(watch::channel(false).0);
            Dialogue::cancel_chat(&mut d, "s", "new_player_input");
            d.sessions.get_mut("s").unwrap().foreground.start_combat();
        }
        assert!(
            dialogue
                .select_foreground("s", "cancelled", 0, Route::Knowledge)
                .is_err()
        );
        assert!(!dialogue.update(
            "s",
            "cancelled",
            0,
            PlaybackStatus::Completed,
            Some(&result)
        ));
        {
            let d = dialogue.data.lock().unwrap();
            let foreground = &d.sessions["s"].foreground;
            assert_eq!(foreground.route, Route::Knowledge);
            assert!(foreground.suspended.is_none());
        }
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn closing_workshop_starts_a_fresh_automatic_haiku_interval() {
        let mut dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        // 実時間を待たず起動後2分を再現し、相談に入る前の経過時間を確実に持たせる。
        std::sync::Arc::get_mut(&mut dialogue).unwrap().clock =
            Instant::now() - std::time::Duration::from_secs(120);
        dialogue.register("s", "試験", true);
        let now = dialogue.clock.elapsed().as_millis() as u64;
        let lines: Vec<serde_json::Value> = [
            ("upper", "上五", "くさちのひ"),
            ("middle", "中七", "くろきつるぎの"),
            ("lower", "下五", "かげのさむさ"),
        ]
        .iter()
        .enumerate()
        .map(|(i, (position, name, text))| json!({"line_id":format!("line_{}",i+1),
            "line_index":i,"position":position,"canonical_name":name,"surface_text":text,"reading_text":text,
            "source_atom_ids":[],"source_atoms":[],"provenance":"fixture"}))
        .collect();
        let prepared: PreparedEmission = serde_json::from_value(json!({
            "text":"くさちのひ くろきつるぎの かげのさむさ", "lines":lines, "materials":{}
        }))
        .unwrap();
        let mut workshop = Workshop::open(
            prepared.complete(chrono::Utc::now()).unwrap(),
            None,
            Instant::now(),
        );
        workshop.close("explicit_close");
        {
            let mut d = dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            s.foreground.clock.reset(now.saturating_sub(100_000));
            s.foreground.activate(Route::HaikuWorkshop, now, None);
            s.haiku.workshop = Some(workshop);
        }

        let mut d = dialogue.data.lock().unwrap();
        let before_tick = dialogue.clock.elapsed().as_millis() as u64;
        dialogue.tick_foreground(&mut d, "s");
        let after_tick = dialogue.clock.elapsed().as_millis() as u64;
        let foreground = &d.sessions["s"].foreground;
        assert_eq!(foreground.route, Route::None);
        // 同一ms内の終了でも成立する。時計のresetが抜けた場合は前の100秒が残って失敗する。
        let elapsed = foreground.clock.elapsed(after_tick);
        assert!(elapsed <= after_tick - before_tick);
        assert_eq!(
            foreground.clock.elapsed(after_tick + 60_000),
            elapsed + 60_000
        );
        drop(d);
        dialogue.shutdown().await;
    }
}
