//! 既存の会話モデルで対話と出来事のあらすじを作る。通常turnの直列枠内から呼ぶ。
//! 生成中はdataを解放し、取消・接続の寿命・入力世代・元行を照合してから履歴を圧縮する。
//! あらすじは発話・世界操作へ流さず、保存用の「旅の記録」は同じ確定結果を使う。
use super::{
    Dialogue, Session, bridge,
    history::{Compaction, History},
};
use crate::types::{ChatMessage, GenerationRequest, Role};
use anyhow::{Result, ensure};
use tokio::sync::watch;

const PROMPT: &str = include_str!("../../../dogido_server/llm/history_summary.txt");
const MERGE_PROMPT: &str = include_str!("../../../dogido_server/llm/adventure_summary.txt");

impl Session {
    /// モデル入力だけを制限する共通投影。保存・復元・終了後の圧縮は全履歴を使う。
    pub(super) fn model_history(&self) -> Vec<serde_json::Value> {
        if self.haiku.foreground() {
            self.history.workshop_rows()
        } else {
            self.history.rows()
        }
    }
    pub(super) fn model_history_lines(&self) -> String {
        History::lines_for(&self.model_history())
    }
    pub(super) fn refresh_model_history(
        &self,
        turn: &str,
        captured: &[serde_json::Value],
        retained: usize,
    ) -> Vec<serde_json::Value> {
        if self.haiku.foreground() {
            // 制限前の原文を末尾へ足し戻さず、現在の正本の順番から窓を取り直す。
            self.model_history()
                .into_iter()
                .filter(|r| r["turn_id"] != turn)
                .collect()
        } else {
            self.history.refresh_before_input(turn, captured, retained)
        }
    }
    pub(super) fn workshop_context_rows(&self) -> Vec<serde_json::Value> {
        let all = self.history.rows();
        // private・未発話の専用履歴も20往復の入力枠を使うが、保存用対話には昇格させない。
        let extra = self.haiku.workshop.as_ref().map_or(0, |w| {
            w.dialogue
                .iter()
                .filter(|row| {
                    let id = format!("workshop:{}", row["turn_id"].as_str().unwrap_or(""));
                    !all.iter().any(|r| r["turn_id"] == id)
                })
                .count()
        });
        History::recent_turn_rows(&all, 20usize.saturating_sub(extra))
    }
}

impl Dialogue {
    /// 一入力につき最大二回。通常は10ターンの圧縮だけ、6件揃ったときだけ統合を加える。
    pub(super) async fn compact_history(
        &self,
        sid: &str,
        turn: &str,
        epoch: u64,
        cancel: &mut watch::Receiver<bool>,
    ) {
        if !self.config.llm_enabled {
            return;
        }
        for attempt in 0..2 {
            if *cancel.borrow() || cancel.has_changed().is_err() {
                return;
            }
            let Some((instance, batch)) = ({
                let mut d = self.data.lock().unwrap();
                if d.stopped {
                    return;
                }
                d.sessions
                    .get_mut(sid)
                    .filter(|s| s.epoch == epoch && s.current_turn == turn)
                    .and_then(|s| {
                        if s.haiku.foreground() {
                            s.history.defer_for_haiku();
                            return None;
                        }
                        s.history
                            .compaction()
                            .map(|batch| (s.instance_id.clone(), batch))
                    })
            }) else {
                return;
            };
            // 失敗後に原文が増えても、同じ入力でrawを何度も再要約しない。
            if attempt > 0 && !batch.merge {
                return;
            }
            let response = tokio::select! {
                biased;
                _ = bridge::cancelled(cancel) => return,
                response = self.summarize_history(&batch) => response,
            };
            if *cancel.borrow() || cancel.has_changed().is_err() {
                return;
            }
            let text = match response {
                Ok(text) => text,
                Err(error) => {
                    tracing::warn!(event="conversation_summary_failed", session_id=sid, %error);
                    return;
                }
            };
            let saved = {
                let mut d = self.data.lock().unwrap();
                if d.stopped {
                    return;
                }
                let Some(s) = d.sessions.get_mut(sid).filter(|s| {
                    s.instance_id == instance
                        && s.epoch == epoch
                        && s.current_turn == turn
                        && !s.haiku.foreground()
                }) else {
                    return;
                };
                if !s.history.commit_compaction(&batch, &text) {
                    return;
                }
                let saved = if self.config.haiku.memory_enabled && !s.text_workshop {
                    s.journey.as_mut().map(|record| {
                        record.synopses = s.history.synopses();
                        record.clone()
                    })
                } else {
                    None
                };
                d.revision += 1;
                saved
            };
            if let Some(record) = saved {
                self.persist_journey(record).await;
            }
        }
    }
    /// 対話中と停止時で同じ一回生成を使う。要約用の道具や再試行は追加しない。
    pub(super) async fn summarize_history(&self, batch: &Compaction) -> Result<String> {
        ensure!(self.config.llm_enabled, "LLM disabled by configuration");
        let prompt = if batch.merge {
            MERGE_PROMPT.trim().to_owned()
        } else {
            let scope = if batch.turns == 0 {
                "冒険中の対話と出来事".into()
            } else {
                format!("過去{}ターンの対話と出来事", batch.turns)
            };
            PROMPT.trim().replace("{record_scope}", &scope).replace(
                "{target_chars}",
                &(40 * batch.turns.div_ceil(10).max(1)).to_string(),
            )
        };
        let input = if batch.merge {
            batch
                .rows
                .iter()
                .enumerate()
                .map(|(i, row)| format!("{}: {}", i + 1, row["text"].as_str().unwrap_or("")))
                .collect::<Vec<_>>()
                .join("\n")
        } else {
            History::lines_for(&batch.rows)
        };
        let request = GenerationRequest {
            schema_version: 1,
            kind: if batch.merge {
                "adventure_summary"
            } else {
                "conversation_summary"
            }
            .into(),
            model: self.config.model.clone(),
            messages: vec![
                ChatMessage {
                    role: Role::System,
                    content: prompt,
                },
                ChatMessage {
                    role: Role::User,
                    content: input,
                },
            ],
            temperature: 0.2,
            // 字数は指示上の目安。原文の入力予算や文字数による切断は設けない。
            max_tokens: if batch.merge { 768 } else { 512 },
            enable_thinking: false,
        };
        let report = self.llm.generate(&request).await?;
        ensure!(
            report.generated.finish_reason.as_deref() != Some("length")
                && !report.generated.text.trim().is_empty(),
            "incomplete_summary"
        );
        tracing::info!(
            event = "conversation_summary_generated",
            turns = batch.turns,
            merge = batch.merge,
            chars = report.generated.text.trim().chars().count(),
            elapsed_ms = report.elapsed_ms as u64
        );
        Ok(report.generated.text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dialogue::DialogueConfig;
    use serde_json::{Value, json};
    use std::sync::{Arc, Mutex};
    use tokio::sync::Notify;

    struct Fixture {
        dialogue: Arc<Dialogue>,
        seen: Arc<Mutex<Vec<Value>>>,
        mode: Arc<Mutex<&'static str>>,
        entered: Arc<Notify>,
        release: Arc<Notify>,
        server: tokio::task::JoinHandle<()>,
        root: std::path::PathBuf,
    }
    impl Fixture {
        async fn new() -> Self {
            let seen = Arc::new(Mutex::new(vec![]));
            let mode = Arc::new(Mutex::new("ok"));
            let entered = Arc::new(Notify::new());
            let release = Arc::new(Notify::new());
            let (requests, setting, begun, resume) =
                (seen.clone(), mode.clone(), entered.clone(), release.clone());
            let app = axum::Router::new().route("/chat/completions", axum::routing::post(
                move |axum::Json(body): axum::Json<Value>| {
                    let (requests, setting, begun, resume) = (requests.clone(), setting.clone(), begun.clone(), resume.clone());
                    async move {
                        requests.lock().unwrap().push(body);
                        let mode = *setting.lock().unwrap();
                        if mode == "wait" { begun.notify_one(); resume.notified().await; }
                        let code = if mode == "error" { axum::http::StatusCode::INTERNAL_SERVER_ERROR } else { axum::http::StatusCode::OK };
                        (code, axum::Json(json!({"id":"summary-fixture", "object":"chat.completion", "created":0,
                            "model":"fixture", "choices":[{"index":0,"message":{"role":"assistant","content":if mode=="empty" {""} else {"川沿いを歩き、村のそばに拠点を作る話をした。"}},"finish_reason":if mode=="length" {"length"} else {"stop"}}],
                            "usage":{"prompt_tokens":10,"completion_tokens":10,"total_tokens":20}})))
                    }
                }));
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let server = tokio::spawn(async move {
                axum::serve(listener, app).await.unwrap();
            });
            let root = std::env::temp_dir()
                .join(format!("dogido-history-runtime-{}", uuid::Uuid::new_v4()));
            let mut config = DialogueConfig {
                base_url: format!("http://{address}"),
                model: "fixture".into(),
                audio_enabled: false,
                timeout_ms: 2000,
                ..Default::default()
            };
            config.haiku.enabled = false;
            config.haiku.memory_dir = root.clone();
            let dialogue = Dialogue::new(config).unwrap();
            dialogue.register("s", "試験プレイヤー", false);
            Self {
                dialogue,
                seen,
                mode,
                entered,
                release,
                server,
                root,
            }
        }
        fn seed(&self, start: usize, end: usize) {
            let mut d = self.dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            s.epoch = 1;
            s.current_turn = "current".into();
            for i in start..end {
                s.history
                    .push(&format!("t{i}"), "user", &format!("入力{i}の最後まで。"));
                s.history
                    .push(&format!("t{i}"), "assistant", &format!("返答{i}やで。"));
            }
        }
        async fn compact(&self) {
            let (_tx, mut cancel) = watch::channel(false);
            self.dialogue
                .compact_history("s", "current", 1, &mut cancel)
                .await;
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            self.server.abort();
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }
    #[tokio::test]
    async fn workshop_defers_all_summaries_reads_last_twenty_and_then_keeps_ten_raw_turns() {
        let f = Fixture::new().await;
        f.seed(0, 5);
        let fixture: Value = serde_json::from_str(include_str!(
            "../../scripts/fixtures/workshop_meaning_cases.json"
        ))
        .unwrap();
        let view = &fixture["contexts"]["grass"];
        let e = &view["emission"];
        let emission = serde_json::from_value::<crate::haiku_record::PreparedEmission>(json!({
            "text":e["reading_text"],"surface_text":null,"reading_text":e["reading_text"],
            "lines":e["lines"],"materials":view["materials"],"interpretation":null,"preface":null,
            "biome":null,"structure":null,"time_phase":null,"dimension":null,"event_sequence":null,"route":"haiku"
        })).unwrap().complete(chrono::Utc::now()).unwrap();
        {
            let mut d = f.dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            s.haiku.workshop = Some(crate::haiku_record::Workshop::open(
                emission,
                None,
                std::time::Instant::now(),
            ));
            for i in 5..40 {
                s.history
                    .story_turn(&format!("workshop:t{i}"), &format!("川柳相談{i}の往復"));
                s.haiku.workshop.as_mut().unwrap().dialogue.push_back(json!({"turn_id":format!("t{i}"),"player_text":format!("相談{i}"),"dogido_text":format!("返答{i}")}));
            }
        }
        f.compact().await;
        assert!(
            f.seen.lock().unwrap().is_empty(),
            "no summary model call during workshop"
        );
        {
            let mut d = f.dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            assert_eq!(s.model_history().len(), 20);
            let captured = s.model_history();
            let refreshed = s.refresh_model_history("current", &captured, 0);
            assert_eq!(
                refreshed, captured,
                "refresh cannot resurrect older rows or change their order"
            );
            assert!(
                s.history.lines().contains("川柳相談5の往復"),
                "old originals remain buffered"
            );
            let view = Dialogue::workshop_view(s, "続けよう").unwrap();
            assert_eq!(view["dialogue"].as_array().unwrap().len(), 20);
            assert_eq!(view["dialogue"][0]["turn_id"], "t20");
            assert!(!view["shared_context"].as_str().unwrap().contains("入力0"));
            s.haiku.workshop.as_mut().unwrap().close("explicit_close");
        }
        f.compact().await;
        {
            let seen = f.seen.lock().unwrap();
            assert_eq!(seen.len(), 1, "one batch after workshop ends");
            let source = seen[0]["messages"][1]["content"].as_str().unwrap();
            assert!(source.contains("入力0") && source.contains("川柳相談29の往復"));
            assert!(!source.contains("川柳相談30の往復"));
        }
        let d = f.dialogue.data.lock().unwrap();
        let h = &d.sessions["s"].history;
        assert_eq!(h.synopses()[0].turns, 30);
        assert_eq!(
            h.rows()
                .iter()
                .filter(|r| r["dialogue_turn"] == true)
                .count(),
            10
        );
        assert!(!h.lines().contains("川柳相談29の往復"));
        assert!(h.lines().contains("川柳相談30の往復"));
    }

    #[tokio::test]
    async fn twenty_turn_boundary_uses_configured_model_and_six_synopses_merge_once() {
        let f = Fixture::new().await;
        f.seed(0, 19);
        f.compact().await;
        assert!(f.seen.lock().unwrap().is_empty());
        f.seed(19, 20);
        f.compact().await;
        for start in [20, 30, 40, 50, 60] {
            f.seed(start, start + 10);
            f.compact().await;
        }
        {
            let seen = f.seen.lock().unwrap();
            assert_eq!(seen.len(), 7); // 6件の短いあらすじと、その6件をまとめる1回。
            assert_eq!(seen[0]["model"], "fixture");
            assert!(seen[0]["tools"].is_null());
            assert!(
                seen[0]["messages"][0]["content"]
                    .as_str()
                    .unwrap()
                    .contains("ドギドやで")
            );
            let input = seen[0]["messages"][1]["content"].as_str().unwrap();
            assert!(input.contains("入力0の最後まで。"));
            assert!(!input.contains("入力10の最後まで。"));
            assert!(
                seen[6]["messages"][0]["content"]
                    .as_str()
                    .unwrap()
                    .contains("200文字")
            );
            let d = f.dialogue.data.lock().unwrap();
            assert_eq!(d.sessions["s"].history.synopses().len(), 1);
            assert_eq!(d.sessions["s"].history.synopses()[0].turns, 60);
            assert_eq!(
                d.sessions["s"].history.completed_pairs()[0]["turn_id"],
                "t67"
            );
        }
        let saved = crate::journey::load(&f.root).unwrap();
        assert_eq!(saved.len(), 1);
        assert_eq!(saved[0].synopses[0].turns, 60);
        assert!(saved[0].ended_at.is_none());
        f.dialogue.shutdown().await;
    }
    #[tokio::test]
    async fn network_empty_and_truncated_failures_keep_raw_history_without_retry() {
        for mode in ["error", "empty", "length"] {
            let f = Fixture::new().await;
            f.seed(0, 20);
            *f.mode.lock().unwrap() = mode;
            let before = f.dialogue.data.lock().unwrap().sessions["s"].history.rows();
            f.compact().await;
            assert_eq!(f.seen.lock().unwrap().len(), 1);
            assert_eq!(
                f.dialogue.data.lock().unwrap().sessions["s"].history.rows(),
                before
            );
            assert!(crate::journey::load(&f.root).unwrap().is_empty());
            f.dialogue.shutdown().await;
        }
    }
    #[tokio::test]
    async fn cancellation_reconnection_and_epoch_change_discard_delayed_summary() {
        for change in ["cancel", "reconnect", "epoch"] {
            let f = Fixture::new().await;
            f.seed(0, 20);
            *f.mode.lock().unwrap() = "wait";
            let owner = f.dialogue.clone();
            let (tx, mut cancel) = watch::channel(false);
            let task = tokio::spawn(async move {
                owner.compact_history("s", "current", 1, &mut cancel).await;
            });
            f.entered.notified().await;
            match change {
                "cancel" => {
                    tx.send(true).unwrap();
                }
                "reconnect" => {
                    f.dialogue.register("s", "別接続", false);
                    f.seed(30, 31);
                }
                _ => {
                    f.dialogue
                        .data
                        .lock()
                        .unwrap()
                        .sessions
                        .get_mut("s")
                        .unwrap()
                        .epoch += 1;
                }
            }
            f.release.notify_one();
            task.await.unwrap();
            assert!(
                f.dialogue.data.lock().unwrap().sessions["s"]
                    .history
                    .synopses()
                    .is_empty()
            );
            assert!(crate::journey::load(&f.root).unwrap().is_empty());
            *f.mode.lock().unwrap() = "ok";
            f.dialogue.shutdown().await;
        }
    }
    #[tokio::test]
    async fn shutdown_summarizes_closed_and_current_sessions_and_writes_each_only_once() {
        let f = Fixture::new().await;
        f.seed(0, 3);
        f.dialogue
            .data
            .lock()
            .unwrap()
            .sessions
            .get_mut("s")
            .unwrap()
            .journey
            .as_mut()
            .unwrap()
            .poem("session:s:verse".into(), "旅で詠んだ句".into());
        f.dialogue.close("s");
        f.dialogue.register("s", "次の冒険", false);
        f.seed(3, 5);
        f.dialogue.shutdown().await;
        let records = crate::journey::load(&f.root).unwrap();
        assert_eq!(records.len(), 2);
        assert!(
            records
                .iter()
                .all(|r| r.ended_at.is_some() && !r.date.is_empty() && !r.unfinished)
        );
        assert_eq!(
            records.iter().map(|r| r.synopses[0].turns).sum::<usize>(),
            5
        );
        assert_eq!(records.iter().map(|r| r.poems.len()).sum::<usize>(), 1);
        assert_eq!(f.seen.lock().unwrap().len(), 2);
        f.dialogue.shutdown().await;
        assert_eq!(f.seen.lock().unwrap().len(), 2);
        assert_eq!(crate::journey::load(&f.root).unwrap().len(), 2);
    }
    #[tokio::test]
    async fn shutdown_failure_keeps_existing_synopsis_and_omits_remaining_raw_text() {
        let f = Fixture::new().await;
        f.seed(0, 20);
        f.compact().await;
        *f.mode.lock().unwrap() = "error";
        f.dialogue.shutdown().await;
        let record = crate::journey::load(&f.root).unwrap().remove(0);
        assert_eq!(record.synopses.len(), 1);
        assert_eq!(record.omitted_turns, 10);
        assert!(record.unfinished);
        let text = std::fs::read_to_string(
            f.root
                .join("long_term/journeys")
                .join(format!("{}.json", record.id)),
        )
        .unwrap();
        assert!(!text.contains("入力10の最後まで。"));
    }
}
