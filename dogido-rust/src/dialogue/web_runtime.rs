//! Web制御をgame-event workerから離し、実再生とsession寿命へ結ぶ。
use super::PlaybackStatus;
use super::{Data, Dialogue, Session, bridge, web_adapter};
use crate::{
    foreground::Route,
    language::web::{self, Proposal, SearchResult},
};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::VecDeque, path::PathBuf, sync::Arc, time::Duration};
use tokio::sync::watch;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub enabled: bool,
    pub available: Option<bool>,
    pub adapter: Option<PathBuf>,
    pub timeout_ms: u64,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            enabled: true,
            available: None,
            adapter: None,
            timeout_ms: 70_000,
        }
    }
}
#[derive(Default)]
pub(super) struct State {
    pub state: web::State,
    client: Option<web_adapter::Client>,
    available: Option<bool>,
    control_cancel: Option<watch::Sender<bool>>,
    history: VecDeque<Value>,
}
impl State {
    pub fn cancel(&mut self, reason: &str) {
        if let Some(c) = self.control_cancel.take() {
            let _ = c.send(true);
            self.state.interrupt();
        }
        if reason != "new_player_input" {
            self.state.interrupt();
            if matches!(
                reason,
                "session_closed" | "server_shutdown" | "manual_interrupt"
            ) {
                self.state.clear(false);
                self.history.clear();
                if let Some(client) = self.client.take() {
                    client.close();
                }
            }
        }
    }
}
pub(super) fn tick(s: &mut Session, now: u64, ttl: u64) {
    let deadline = s
        .web
        .state
        .last_activity
        .saturating_add(s.web.state.ttl(ttl));
    if s.web.state.expire(now, ttl) {
        if let Some(c) = s.web.control_cancel.take() {
            let _ = c.send(true);
        }
        if let Some(client) = s.web.client.take() {
            client.close();
        }
        s.web.history.clear();
        remember_topic(s);
        s.language = crate::language::State::default();
        // 生成中の読書返答も旧結果として無効にする。
        if let Some(c) = s.cancel.take() {
            let _ = c.send(true);
            s.epoch += 1;
        }
        s.foreground.clear(deadline.min(now));
    }
}
// 復帰話題は既存の8件の出来事欄へ一度だけ置く。本文・URL・読書中の履歴は渡さない。
fn remember_topic(s: &mut Session) {
    let topic = std::mem::take(&mut s.web.state.last_topic);
    let topic = topic.split_whitespace().collect::<Vec<_>>().join(" ");
    if topic.is_empty() {
        return;
    }
    let note = format!("直前にプレイヤーと『{topic}』をWebで調べた。")
        .chars()
        .take(60)
        .collect::<String>();
    if s.combat_digest.back() != Some(&note) {
        if s.combat_digest.len() == 8 {
            s.combat_digest.pop_front();
        }
        s.combat_digest.push_back(note);
    }
}
pub(super) fn completed(s: &mut Session, turn: &str, player: &str, result: &Value) {
    if result["web_private"] != true || s.web.state.research.is_none() {
        return;
    }
    s.web
        .history
        .push_back(json!({"turn_id":turn,"role":"user","text":player}));
    s.web.history.push_back(
        json!({"turn_id":format!("{turn}:reply"),"role":"assistant","text":result["text"]}),
    );
    while s.web.history.len() > 10 {
        s.web.history.pop_front();
    }
}
fn result(row: Value, private: bool) -> Value {
    json!({"text":row["text"],"spoken_text":row["text"],"language_status":row["status"],"web_turn":true,"web_private":private,"web_departure":row["web_departure"],"return_context":row["return_context"]})
}
impl Dialogue {
    fn web_client(self: &Arc<Self>, sid: &str, epoch: u64) -> Result<web_adapter::Client> {
        let mut jobs = self.jobs.lock().unwrap();
        let mut d = self.data.lock().unwrap();
        ensure!(!d.stopped, "server stopped");
        let s = d
            .sessions
            .get_mut(sid)
            .filter(|s| s.epoch == epoch)
            .ok_or_else(|| anyhow::anyhow!("stale web session"))?;
        if let Some(client) = &s.web.client {
            return Ok(client.clone());
        }
        let (client, job) = web_adapter::Client::new(web_adapter::Config {
            python: self.config.python.clone(),
            helper: self
                .config
                .web
                .adapter
                .clone()
                .unwrap_or_else(|| self.config.helper.with_file_name("web_adapter.py")),
            timeout: Duration::from_millis(self.config.web.timeout_ms),
        });
        s.web.client = Some(client.clone());
        jobs.push(job);
        Ok(client)
    }
    pub(super) async fn prepare_web(
        self: &Arc<Self>,
        sid: &str,
        epoch: u64,
        input: &mut Value,
        cancel: &watch::Receiver<bool>,
    ) {
        if !self.config.web.enabled
            || input["workshop"].is_object()
            || input["address_reply"].is_string()
            || !(input["language_active"] == true
                || crate::player_text::prepare(input["text"].as_str().unwrap_or(""))
                    .explicit_language)
        {
            return;
        }
        let cached = self
            .data
            .lock()
            .unwrap()
            .sessions
            .get(sid)
            .and_then(|s| s.web.available)
            .or(self.config.web.available);
        let available = if let Some(value) = cached {
            value
        } else {
            match self.web_client(sid, epoch) {
                Ok(client) => client.inspect(cancel).await.ok().is_some_and(|v| {
                    tracing::info!(event="main_language_web",status=?v["reason"]);
                    v["available"] == true
                }),
                Err(_) => false,
            }
        };
        if let Some(s) = self
            .data
            .lock()
            .unwrap()
            .sessions
            .get_mut(sid)
            .filter(|s| s.epoch == epoch)
        {
            s.web.available = Some(available);
            input["web_available"] = available.into();
        }
    }
    pub(super) async fn web_turn(
        self: &Arc<Self>,
        sid: &str,
        epoch: u64,
        input: &Value,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Option<Value>> {
        let text = input["text"].as_str().unwrap_or("");
        if input["workshop"].is_object()
            || input["poem_input"].is_object()
            || input["address_reply"].is_string()
            || crate::haiku_memory::clear_requested(text)
        {
            return Ok(None);
        }
        let (mut state, history) = {
            let d = self.data.lock().unwrap();
            let Some(s) = d.sessions.get(sid).filter(|s| s.epoch == epoch) else {
                return Ok(None);
            };
            (s.web.state.clone(), s.web.history.clone())
        };
        if state.proposal.is_none() && state.research.is_none() {
            return Ok(None);
        }
        let prepared = crate::player_text::prepare(text);
        let overlay = super::reading_runtime::load_overlay(&self.config).await?;
        let context = crate::input_context::Context::from_prepared(
            &prepared,
            &overlay,
            chrono::Local::now().fixed_offset(),
        );
        if !context.general_conversation() {
            return Ok(None);
        }
        let private = state.research.is_some();
        let details = json!({"current":{"turn_id":input["operation_id"],"text":input["text"],"source":input["source"]},"history":history});
        let now = self.clock.elapsed().as_millis() as u64;
        let response = tokio::select! {biased;_=bridge::cancelled(cancel)=>anyhow::bail!("web turn cancelled"),r=web::model::respond(&self.llm,&self.config.model,&mut state,&details,now)=>r?};
        let mut d = self.data.lock().unwrap();
        ensure!(!d.stopped, "stopped web turn");
        let s = d
            .sessions
            .get_mut(sid)
            .filter(|s| s.epoch == epoch)
            .ok_or_else(|| anyhow::anyhow!("stale web turn"))?;
        s.web.state = state;
        remember_topic(s);
        if !s.web.state.active() {
            s.web.history.clear();
            s.language = crate::language::State::default();
            s.foreground.clear(now);
            if let Some(client) = s.web.client.take() {
                client.close();
            }
        }
        // 終了を含め、読書中の原文を通常履歴へ戻さない。
        Ok(response.map(|r| result(r, private)))
    }
    /// 言語結果は発話前に同意待ちへ移すが、出発許可は実再生後しか消費しない。
    pub(super) fn apply_web_result(
        &self,
        sid: &str,
        turn: &str,
        epoch: u64,
        result: &Value,
    ) -> bool {
        if result["web_proposal"].is_null() && result["web_turn"] != true {
            return false;
        }
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return true;
        }
        let Some(s) = d.sessions.get_mut(sid).filter(|s| s.epoch == epoch) else {
            return true;
        };
        let now = self.clock.elapsed().as_millis() as u64;
        if let Ok(p) = serde_json::from_value::<Proposal>(result["web_proposal"].clone()) {
            s.web.state.propose(p, now);
        }
        let route = if s.web.state.active() {
            Route::Web
        } else if matches!(
            result["language_status"].as_str(),
            Some("handoff" | "web_declined")
        ) {
            Route::None
        } else {
            Route::Learning
        };
        if !s.foreground.combat_active {
            if route == Route::None {
                s.foreground.clear(now);
            } else {
                s.foreground.activate(route, now, Some(now));
            }
        }
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["conversation_route"] = json!(route);
            row["language_status"] = result["language_status"].clone();
            row["category"] = "learning".into();
            if let Some(id) = result["web_departure"].as_str() {
                row["utterance_id"] = id.into();
            }
        }
        d.revision += 1;
        true
    }
    /// completed/failed/cancelledの実際のplayer結果だけ。jobの登録とpermit消費を同じlock内で行う。
    pub(super) fn web_playback(
        self: &Arc<Self>,
        sid: &str,
        epoch: u64,
        result: &Value,
        status: PlaybackStatus,
    ) {
        let Some(id) = result["web_departure"].as_str() else {
            return;
        };
        let mut jobs = self.jobs.lock().unwrap();
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return;
        }
        let Some(s) = d.sessions.get_mut(sid).filter(|s| s.epoch == epoch) else {
            return;
        };
        let matched = s.web.state.departure.as_deref() == Some(id);
        let Some(proposal) = s.web.state.playback(id, status) else {
            if matched
                && matches!(
                    status,
                    PlaybackStatus::Failed
                        | PlaybackStatus::Cancelled
                        | PlaybackStatus::AudioDisabled
                )
                && !s.foreground.combat_active
            {
                s.foreground.activate(
                    Route::Learning,
                    self.clock.elapsed().as_millis() as u64,
                    None,
                );
            }
            return;
        };
        let (tx, rx) = watch::channel(false);
        s.web.control_cancel = Some(tx);
        let this = self.clone();
        let sid = sid.to_owned();
        let id = id.to_owned();
        jobs.push(tokio::spawn(async move {
            this.search_web(sid, epoch, id, proposal, rx).await;
        }));
    }
    async fn search_web(
        self: Arc<Self>,
        sid: String,
        epoch: u64,
        id: String,
        proposal: Proposal,
        mut cancel: watch::Receiver<bool>,
    ) {
        let found = match self.web_client(&sid, epoch) {
            Ok(client) => client.search(&proposal, &cancel).await,
            Err(e) => Err(e),
        };
        let outcome = {
            let mut d = self.data.lock().unwrap();
            if d.stopped || *cancel.borrow() {
                return;
            }
            let Some(s) = d.sessions.get_mut(&sid).filter(|s| s.epoch == epoch) else {
                return;
            };
            let now = self.clock.elapsed().as_millis() as u64;
            if s.web
                .state
                .expire(now, self.config.combat.ms("conversation_active_ttl_ms"))
            {
                return;
            }
            let found = found.unwrap_or_else(|e| {
                tracing::warn!(event="web_search_failed",error=%e);
                SearchResult {
                    status: "unavailable".into(),
                    ..Default::default()
                }
            });
            let outcome = s.web.state.searched(&id, &proposal, &found, now);
            if outcome["status"] == "stale_search" {
                return;
            }
            let route = if s.web.state.research.is_some() {
                Route::Web
            } else {
                Route::Learning
            };
            s.foreground.activate(route, now, Some(now));
            d.revision += 1;
            result(outcome, true)
        };
        // 可視ページを開いた場合は無音。失敗/非表示の案内だけ専用turnで実再生を記録。
        if outcome["text"].as_str().is_some_and(|s| !s.is_empty()) {
            let turn = super::id("web-control");
            {
                let mut d = self.data.lock().unwrap();
                if d.stopped || d.sessions.get(&sid).is_none_or(|s| s.epoch != epoch) {
                    return;
                }
                if d.rows.len() == 200 {
                    d.rows.pop_front();
                }
                d.rows.push_back(json!({"turn_id":turn,"session_id":sid,"utterance_id":super::id("web-reply"),"epoch":epoch,"category":"learning","player_input_text":"","conversation_route":"learning","playback_status":"generating"}));
            }
            let permit = tokio::select! {biased;_=bridge::cancelled(&mut cancel)=>return,p=self.serial.acquire()=>p.unwrap()};
            if self.update(&sid, &turn, epoch, PlaybackStatus::Queued, Some(&outcome)) {
                let played = self
                    .audio
                    .speak(
                        &self.config,
                        outcome["text"].as_str().unwrap(),
                        &mut cancel,
                        || {
                            self.update(&sid, &turn, epoch, PlaybackStatus::Started, None);
                        },
                    )
                    .await;
                self.update(
                    &sid,
                    &turn,
                    epoch,
                    if played.is_ok() {
                        PlaybackStatus::Completed
                    } else if *cancel.borrow() {
                        PlaybackStatus::Cancelled
                    } else {
                        PlaybackStatus::Failed
                    },
                    Some(&outcome),
                );
            }
            drop(permit);
        }
        let mut d = self.data.lock().unwrap();
        if let Some(s) = d.sessions.get_mut(&sid).filter(|s| s.epoch == epoch) {
            s.web.control_cancel = None;
        }
    }
    pub(super) fn cancel_web(d: &mut Data, sid: &str, reason: &str) {
        if let Some(s) = d.sessions.get_mut(sid) {
            // 新しい発話で出発音声自体を中断した場合、古いIDは再利用できない。
            // 問いの同意待ちは保持し、次の明示同意から新しい出発音声を作る。
            if reason == "new_player_input" && s.cancel.is_some() {
                s.web.state.departure = None;
            }
            s.web.cancel(reason);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::super::DialogueConfig;
    use super::*;
    use std::fs;
    struct Files(PathBuf);
    impl Drop for Files {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn fixture() -> (Arc<Dialogue>, Files, PathBuf) {
        let path = std::env::temp_dir().join(format!("dogido-web-host-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&path).unwrap();
        let helper = path.join("adapter.py");
        let log = path.join("calls");
        fs::write(&helper,r#"import json,os,sys
from pathlib import Path
log=Path(__file__).with_name('calls')
for line in sys.stdin:
    v=json.loads(line)
    if v['op']=='close': break
    if v['op']=='search':
        with log.open('a') as f: f.write(str(os.getpid())+'\n')
        print(json.dumps({'request_id':v['request_id'],'result':{'status':'page_opened','child_status':'opened','search_url':'https://www.google.com/search?q=fixture'}}),flush=True)
"#).unwrap();
        let d = Dialogue::new(DialogueConfig {
            python: "/usr/bin/python3".into(),
            web: Settings {
                available: Some(true),
                adapter: Some(helper),
                ..Default::default()
            },
            ..Default::default()
        })
        .unwrap();
        d.register("s", "試験", true);
        (d, Files(path), log)
    }
    fn proposal() -> Proposal {
        Proposal {
            question: "狐の語源".into(),
            target: "狐".into(),
            facet: "etymology".into(),
            search_terms: vec!["狐".into()],
            web_query: "狐の語源".into(),
            known_urls: vec![],
            trigger_reason: "explicit_request".into(),
        }
    }
    fn propose(d: &Dialogue) {
        d.data
            .lock()
            .unwrap()
            .rows
            .push_back(json!({"turn_id":"p","session_id":"s","epoch":0}));
        d.apply_web_result(
            "s",
            "p",
            0,
            &json!({"web_proposal":proposal(),"language_status":"web_consent_requested"}),
        );
    }
    async fn wait_reading(d: &Dialogue) {
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                if d.data.lock().unwrap().sessions["s"]
                    .web
                    .state
                    .research
                    .is_some()
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
    }
    #[tokio::test]
    async fn permission_and_consent_do_not_search_completion_opens_once_and_return_is_topic_only() {
        let (d, _files, log) = fixture();
        propose(&d);
        assert!(!log.exists());
        let (_tx, mut rx) = watch::channel(false);
        let response = d
            .web_turn(
                "s",
                0,
                &json!({"operation_id":"yes","text":"ええで","source":"text"}),
                &mut rx,
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(response["text"], web::DEPARTURE);
        assert!(!log.exists());
        d.web_playback("s", 0, &response, PlaybackStatus::Started);
        assert!(!log.exists());
        d.web_playback("s", 0, &response, PlaybackStatus::Completed);
        wait_reading(&d).await;
        d.web_playback("s", 0, &response, PlaybackStatus::Completed);
        assert_eq!(fs::read_to_string(&log).unwrap().lines().count(), 1);
        assert_eq!(
            d.data.lock().unwrap().sessions["s"].foreground.route,
            Route::Web
        );
        let response = d
            .web_turn(
                "s",
                0,
                &json!({"operation_id":"return","text":"このページ https://example.com/private は閉じて","source":"text"}),
                &mut rx,
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            response["return_context"],
            json!({"researched_topic":"狐の語源"})
        );
        assert!(!response.to_string().contains("google.com"));
        assert_eq!(response["web_private"], true);
        {
            let mut data = d.data.lock().unwrap();
            let session = data.sessions.get_mut("s").unwrap();
            assert_eq!(session.combat_digest.len(), 1);
            assert!(session.combat_digest[0].contains("狐の語源"));
            assert!(session.web.state.last_topic.is_empty());
            completed(
                session,
                "return",
                "このページ https://example.com/private は閉じて",
                &response,
            );
            assert!(session.web.history.is_empty());
            remember_topic(session);
            assert_eq!(session.combat_digest.len(), 1);
        }
        assert!(
            d.data.lock().unwrap().sessions["s"]
                .web
                .state
                .research
                .is_none()
        );
        d.shutdown().await;
        let pid = fs::read_to_string(log)
            .unwrap()
            .trim()
            .parse::<i32>()
            .unwrap();
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
    }
    #[tokio::test]
    async fn web_does_not_claim_inventory_knowledge_poem_or_world_inputs() {
        let (d, _files, log) = fixture();
        propose(&d);
        let (_tx, mut rx) = watch::channel(false);
        for text in [
            "持ち物を教えて",
            "枕詞って何？",
            "剣に持ち替えて",
            "敵は何体？",
            "静かにして",
            "今日の句を思い出して",
            "草地の読みはくさち",
            "川柳保存: 今日の空\n見上げて歩く\nいい天気",
            "/say はい",
        ] {
            let result = d
                .web_turn(
                    "s",
                    0,
                    &json!({"operation_id":"other","text":text,"source":"text"}),
                    &mut rx,
                )
                .await
                .unwrap();
            assert!(result.is_none(), "{text}: {result:?}");
            assert!(
                d.data.lock().unwrap().sessions["s"]
                    .web
                    .state
                    .proposal
                    .is_some()
            );
            assert!(!log.exists());
        }
        d.shutdown().await;
    }
    #[tokio::test]
    async fn failed_audio_and_idle_combat_revoke_grant_but_reading_survives() {
        let (d, _files, log) = fixture();
        propose(&d);
        let (_tx, mut rx) = watch::channel(false);
        let input = json!({"operation_id":"yes","text":"はい","source":"text"});
        let r = d.web_turn("s", 0, &input, &mut rx).await.unwrap().unwrap();
        d.web_playback("s", 0, &r, PlaybackStatus::Failed);
        d.web_playback("s", 0, &r, PlaybackStatus::Completed);
        assert!(!log.exists());
        propose(&d);
        let r = d.web_turn("s", 0, &input, &mut rx).await.unwrap().unwrap();
        {
            let mut data = d.data.lock().unwrap();
            Dialogue::cancel_chat(&mut data, "s", "combat_priority");
        }
        d.web_playback("s", 0, &r, PlaybackStatus::Completed);
        assert!(!log.exists());
        propose(&d);
        let r = d.web_turn("s", 0, &input, &mut rx).await.unwrap().unwrap();
        d.web_playback("s", 0, &r, PlaybackStatus::Completed);
        wait_reading(&d).await;
        {
            let mut data = d.data.lock().unwrap();
            Dialogue::cancel_chat(&mut data, "s", "combat_priority");
            assert!(data.sessions["s"].web.state.research.is_some());
        }
        d.shutdown().await;
    }
    #[tokio::test]
    async fn stale_epoch_cannot_open_or_restore_web_and_research_does_not_reach_regular_history() {
        let (d, _files, log) = fixture();
        propose(&d);
        let (_tx, mut rx) = watch::channel(false);
        let r = d
            .web_turn(
                "s",
                0,
                &json!({"operation_id":"yes","text":"はい","source":"text"}),
                &mut rx,
            )
            .await
            .unwrap()
            .unwrap();
        d.data.lock().unwrap().sessions.get_mut("s").unwrap().epoch = 1;
        d.web_playback("s", 0, &r, PlaybackStatus::Completed);
        assert!(!log.exists());
        {
            let mut data = d.data.lock().unwrap();
            data.sessions.get_mut("s").unwrap().web.state.research = Some(web::Research {
                question: "狐の語源".into(),
                target: "狐".into(),
                pages: vec![],
                phase: "discussing".into(),
                search_results: vec![],
                search_url: String::new(),
            });
            data.rows.push_back(json!({"turn_id":"private","session_id":"s","epoch":1,"player_input_text":"説明の質問","conversation_route":"web"}));
        }
        let reflection = json!({"text":"取得本文への反応","web_private":true,"web_turn":true,"language_status":"research_reflection"});
        d.update("s", "private", 1, PlaybackStatus::Queued, Some(&reflection));
        d.update(
            "s",
            "private",
            1,
            PlaybackStatus::Completed,
            Some(&reflection),
        );
        {
            let data = d.data.lock().unwrap();
            assert!(!data.sessions["s"].history.lines().contains("取得本文"));
            assert_eq!(data.sessions["s"].web.history.len(), 2);
        }
        d.shutdown().await;
    }
}
