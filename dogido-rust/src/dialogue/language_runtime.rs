use super::{Dialogue, Session};
use crate::{foreground::Route, language::State};
use serde_json::{Value, json};

pub(super) fn context(s: &Session, text: &str) -> (bool, State) {
    let active = s.foreground.route == Route::Learning
        || s.foreground
            .suspended
            .as_ref()
            .is_some_and(|t| t.route == Route::Learning)
            && crate::foreground::looks_like_resume(text);
    (
        active,
        if active {
            s.language.clone()
        } else {
            State::default()
        },
    )
}
pub(super) fn completed(s: &mut Session, result: &Value) {
    if let Ok(state) = serde_json::from_value(result["language_state"].clone()) {
        s.language = state;
    }
}
impl Dialogue {
    /// 同じepochの完成した学習結果だけを、既存の会話・音声経路へ渡す。
    pub(super) fn apply_language_result(&self, sid: &str, turn: &str, epoch: u64, result: &Value) {
        if result["language_status"].is_null() {
            return;
        }
        let mut d = self.data.lock().unwrap();
        if d.stopped || d.sessions.get(sid).is_none_or(|s| s.epoch != epoch) {
            return;
        }
        self.tick_foreground(&mut d, sid);
        let now = self.clock.elapsed().as_millis() as u64;
        let route = if result["language_status"] == "host_chat" {
            Route::Casual
        } else {
            Route::Learning
        };
        let s = d.sessions.get_mut(sid).unwrap();
        if !s.foreground.combat_active
            && matches!(s.foreground.route, Route::Casual | Route::Learning)
        {
            s.foreground.activate(route, now, None);
        }
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["conversation_route"] = json!(route);
            row["language_status"] = result["language_status"].clone();
            let sources: Vec<crate::knowledge::Source> = result["language_references"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|r| serde_json::from_value::<crate::knowledge::Source>(r.clone()).ok())
                .filter(|r| r.valid("language"))
                .collect();
            if route == Route::Learning {
                super::knowledge_display::attach(
                    row,
                    &json!({"knowledge_status":result["language_status"],"references":sources}),
                );
                row["category"] = "learning".into();
            }
        }
        d.revision += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dialogue::DialogueConfig;

    #[tokio::test]
    async fn late_language_reply_cannot_reactivate_an_expired_foreground() {
        let mut config = DialogueConfig::default();
        config
            .combat
            .0
            .insert("conversation_active_ttl_ms".into(), json!(0));
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "試験", true);
        {
            let mut d = dialogue.data.lock().unwrap();
            d.sessions
                .get_mut("s")
                .unwrap()
                .foreground
                .activate(Route::Learning, 0, Some(0));
            d.rows
                .push_back(json!({"session_id":"s", "turn_id":"late"}));
        }
        dialogue.apply_language_result("s", "late", 0, &json!({"language_status":"answer"}));
        {
            let d = dialogue.data.lock().unwrap();
            assert_eq!(d.sessions["s"].foreground.route, Route::None);
            assert_eq!(d.rows[0]["category"], "learning");
        }
        dialogue.shutdown().await;
    }
}
