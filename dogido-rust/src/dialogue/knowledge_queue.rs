//! 危険中・警告中の知識質問を一度だけ分類し、安全な観測で先着順に戻す。
use super::*;
use crate::address::Request;

pub(super) struct Pending {
    pub request: Request,
    ready: bool,
    cancel: watch::Sender<bool>,
    audit: super::workshop_record::Attempt,
}
pub(super) struct Checked {
    pub generation: u64,
    pub original: Option<Request>,
    pub audit: Option<super::workshop_record::Attempt>,
}

fn safe(s: &Session) -> bool {
    (s.preview && fresh(s)) || super::workshop_combat_runtime::clear_for_resume(s)
}

impl Dialogue {
    pub(super) fn cancel_knowledge_queue(&self, d: &mut Data, sid: &str, reason: &str) {
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        s.input_generation = s.input_generation.wrapping_add(1);
        let after = super::workshop_record::state(s.haiku.workshop.as_ref());
        let mut cancelled = Vec::new();
        if let Some(checked) = s.knowledge_checked.take()
            && let Some(original) = checked.original
        {
            cancelled.push((original.turn, checked.audit));
        }
        for pending in s.knowledge_queue.drain(..) {
            let _ = pending.cancel.send(true);
            cancelled.push((pending.request.turn, Some(pending.audit)));
        }
        for (turn, audit) in cancelled {
            let result = if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["playback_status"] = "cancelled".into();
                row["resolution"] = reason.into();
                row.clone()
            } else {
                json!({"turn_id":turn,"epoch":audit.as_ref().and_then(|a|a.input.epoch),
                "playback_status":"cancelled","resolution":reason})
            };
            if let Some(audit) = audit {
                self.record_workshop_result(sid, &audit, &after, &result);
            }
        }
        d.revision += 1;
    }

    pub(super) fn queue_knowledge_input(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<tokio::task::JoinHandle<()>>,
        sid: &str,
        text: &str,
        source: &str,
    ) -> Option<Value> {
        let s = d.sessions.get_mut(sid)?;
        if super::workshop_focus::quiet(s)
            || s.knowledge_checked
                .as_ref()
                .is_some_and(|c| c.generation == s.input_generation)
            || (safe(s) && s.warning.is_none() && s.knowledge_queue.is_empty())
        {
            return None;
        }
        // 分類済みの予約枠と待機中の質問を合わせて上限9件。満杯なら新規受付を断り、先の質問を守る。
        let reserved = usize::from(
            s.knowledge_checked
                .as_ref()
                .is_some_and(|c| c.original.is_some()),
        );
        if s.knowledge_queue.len() + reserved >= 9 {
            return Some(json!({"accepted":false,"reason":"knowledge_queue_full"}));
        }
        let private = s.web.state.research.is_some()
            || s.record_private_generation
                .is_some_and(|(g, private)| g == s.input_generation && private);
        let audit = super::workshop_record::Attempt::new(
            super::workshop_record::state(s.haiku.workshop.as_ref()),
            super::workshop_record::Input {
                raw: text.into(),
                semantic: None,
                private,
                epoch: Some(s.epoch),
            },
        );
        let request = Request {
            turn: id("knowledge"),
            text: text.into(),
            source: source.into(),
            input_at: self.clock.elapsed().as_millis() as u64,
            record_private: private,
        };
        let generation = s.input_generation;
        let (cancel, rx) = watch::channel(false);
        s.knowledge_queue.push_back(Pending {
            request: request.clone(),
            audit: audit.clone(),
            ready: false,
            cancel,
        });
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(json!({"utterance_id":id("utt"),"turn_id":request.turn,"session_id":sid,
            "player_input_text":text,"source":source,"text":"","category":"routing",
            "input_at_ms":request.input_at,"epoch":audit.input.epoch,"workshop_record_private":private,"created_at":chrono::Utc::now(),"playback_status":"routing"}));
        d.revision += 1;
        let this = self.clone();
        let session = sid.to_owned();
        let turn = request.turn.clone();
        jobs.push(tokio::spawn(async move {
            this.classify_knowledge_input(session, request, generation, rx, audit)
                .await;
        }));
        Some(
            json!({"accepted":true,"queued":true,"session_id":sid,"turn_id":turn,"reason":"knowledge_input_routing"}),
        )
    }

    async fn classify_knowledge_input(
        self: Arc<Self>,
        sid: String,
        request: Request,
        generation: u64,
        mut cancel: watch::Receiver<bool>,
        audit: super::workshop_record::Attempt,
    ) {
        // Parserのみ。DB検索・モデル・音声は安全な観測まで実行しない。
        // bridge内のRust parserだけで完了する。取消・世代検査は従来どおり維持。
        let result = {
            let work = bridge::render(
                &self.config,
                &self.llm,
                json!({"op":"assist_route","text":request.text}),
                &mut cancel,
            );
            tokio::pin!(work);
            tokio::select! {
                result=&mut work=>result,
                _=tokio::time::sleep(Duration::from_secs(3))=>{
                    if let Some(p) = self.data.lock().unwrap().sessions.get(&sid)
                        .and_then(|s| s.knowledge_queue.iter().find(|p| p.request.turn == request.turn)) {
                        let _ = p.cancel.send(true);
                    }
                    work.await
                }
            }
        };
        let (forward, retained) = {
            let mut d = self.data.lock().unwrap();
            if d.stopped {
                return;
            }
            let Some(s) = d.sessions.get_mut(&sid) else {
                return;
            };
            let Some(index) = s
                .knowledge_queue
                .iter()
                .position(|p| p.request.turn == request.turn)
            else {
                return;
            };
            let knowledge = result
                .as_ref()
                .ok()
                .and_then(|r| r["knowledge_query"].as_bool());
            tracing::info!(event="knowledge_input_routed",session_id=sid,turn_id=request.turn,
                knowledge=?knowledge,queue_size=s.knowledge_queue.len());
            let forward = knowledge == Some(false) && generation == s.input_generation;
            if knowledge == Some(true) {
                s.knowledge_queue[index].ready = true;
            } else {
                s.knowledge_queue.remove(index);
                if forward {
                    s.knowledge_checked = Some(Checked {
                        generation,
                        original: None,
                        audit: None,
                    });
                }
            }
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == request.turn) {
                row["playback_status"] = match knowledge {
                    Some(true) => "waiting_for_safety",
                    Some(false) => "not_selected",
                    None => "failed",
                }
                .into();
                row["resolution"] = match knowledge {
                    Some(true) => "knowledge_queued",
                    Some(false) if forward => "other_input",
                    Some(false) => "superseded_input",
                    None => "knowledge_route_failed",
                }
                .into();
                if let Err(error) = &result {
                    row["error"] = error.to_string().into();
                }
            }
            d.revision += 1;
            (forward, knowledge == Some(true))
        };
        if forward {
            let response = self.submit_inner(
                Some(&sid),
                &request.text,
                &request.source,
                false,
                Some((generation, None)),
                false,
            );
            let mut d = self.data.lock().unwrap();
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == request.turn) {
                row["forwarded_input"] = response;
            }
            d.revision += 1;
        }
        if !retained {
            self.record_workshop_turn(&sid, &request.turn, &audit);
        }
    }

    pub(super) fn resume_knowledge_input(self: &Arc<Self>, sid: &str) {
        let (pending, generation) = {
            let mut d = self.data.lock().unwrap();
            if d.stopped {
                return;
            }
            let Some(s) = d.sessions.get_mut(sid) else {
                return;
            };
            if !safe(s)
                || s.warning.is_some()
                || s.pending_warning.is_some()
                || s.cancel.is_some()
                || s.assist_pending.is_some()
                || s.combat_input.is_some()
                || s.deferred_input.is_some()
                || s.haiku.active.is_some()
                || s.knowledge_checked
                    .as_ref()
                    .is_some_and(|c| c.original.is_some())
                || s.haiku
                    .workshop
                    .as_ref()
                    .is_some_and(|w| w.open && w.combat_paused())
                || s.knowledge_queue.front().is_none_or(|p| !p.ready)
            {
                return;
            }
            let pending = s.knowledge_queue.pop_front().unwrap();
            s.input_generation = s.input_generation.wrapping_add(1);
            let generation = s.input_generation;
            s.knowledge_checked = Some(Checked {
                generation,
                original: Some(pending.request.clone()),
                audit: Some(pending.audit.clone()),
            });
            (pending, generation)
        };
        let response = self.submit_recorded(
            Some(sid),
            &pending.request.text,
            &pending.request.source,
            true,
            Some((generation, None)),
            true,
            super::workshop_record::Admission {
                forwarded: true,
                private: pending.audit.input.private,
            },
        );
        let mut d = self.data.lock().unwrap();
        if let Some(s) = d.sessions.get_mut(sid)
            && let Some(c) = s
                .knowledge_checked
                .as_mut()
                .filter(|c| c.generation == generation)
        {
            c.original = None;
            c.audit = None;
        }
        if response["accepted"] != true {
            // ロックを離した間の危険化/満杯だけ戻す。手動停止・新入力・終了は復活させない。
            let retry = !d.stopped
                && matches!(
                    response["reason"].as_str(),
                    Some("fresh_safe_snapshot_required" | "input_queue_full")
                );
            if let Some(s) = d.sessions.get_mut(sid)
                && s.input_generation == generation
                && retry
            {
                s.knowledge_checked = None;
                s.knowledge_queue.push_front(pending);
                d.revision += 1;
                return;
            } else if let Some(row) = d
                .rows
                .iter_mut()
                .find(|r| r["turn_id"] == pending.request.turn)
            {
                row["playback_status"] = "cancelled".into();
                row["resolution"] = response["reason"].clone();
            }
            d.revision += 1;
            let after = super::workshop_record::state(
                d.sessions.get(sid).and_then(|s| s.haiku.workshop.as_ref()),
            );
            let row = d
                .rows
                .iter()
                .find(|r| r["turn_id"] == pending.request.turn)
                .cloned()
                .unwrap_or_else(|| {
                    json!({"turn_id":pending.request.turn,"epoch":pending.audit.input.epoch,
                    "playback_status":"cancelled","resolution":response["reason"]})
                });
            self.record_workshop_result(sid, &pending.audit, &after, &row);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pending(turn: &str, ready: bool) -> Pending {
        Pending {
            audit: super::super::workshop_record::Attempt::new(Value::Null, Default::default()),
            request: Request {
                record_private: false,
                turn: turn.into(),
                text: "枕詞って何？".into(),
                source: "voice".into(),
                input_at: 7,
            },
            ready,
            cancel: watch::channel(false).0,
        }
    }

    #[tokio::test]
    async fn unfinished_head_and_reserved_dispatch_keep_fifo_order() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("s", "試験", true);
        {
            let mut d = dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            s.knowledge_queue
                .extend([pending("first", false), pending("second", true)]);
        }
        dialogue.resume_knowledge_input("s");
        {
            let mut d = dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            assert_eq!(s.knowledge_queue.len(), 2);
            s.knowledge_queue[0].ready = true;
            s.knowledge_checked = Some(Checked {
                generation: 0,
                audit: None,
                original: Some(pending("reserved", true).request),
            });
        }
        dialogue.resume_knowledge_input("s");
        assert_eq!(
            dialogue.data.lock().unwrap().sessions["s"]
                .knowledge_queue
                .len(),
            2
        );
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn world_reset_invalidates_reserved_dispatch_even_before_it_starts() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("s", "試験", true);
        {
            let mut d = dialogue.data.lock().unwrap();
            d.sessions.get_mut("s").unwrap().knowledge_checked = Some(Checked {
                generation: 0,
                audit: None,
                original: Some(pending("reserved", true).request),
            });
            d.rows
                .push_back(json!({"turn_id":"reserved","playback_status":"waiting_for_safety"}));
            dialogue.cancel_knowledge_queue(&mut d, "s", "world_context_changed");
        }
        let result = dialogue.submit_inner(
            Some("s"),
            "枕詞って何？",
            "voice",
            true,
            Some((0, None)),
            true,
        );
        assert_eq!(result["reason"], "superseded_input");
        assert_eq!(
            dialogue.data.lock().unwrap().rows.back().unwrap()["resolution"],
            "world_context_changed"
        );
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn row_eviction_does_not_lose_a_queued_turn_and_shutdown_reaps_reading_adapter() {
        let folder =
            std::env::temp_dir().join(format!("dogido-knowledge-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&folder).unwrap();
        let helper = folder.join("helper.py");
        std::fs::write(
            &helper,
            "raise AssertionError('ordinary helper must not start')\n",
        )
        .unwrap();
        let adapter = folder.join("tts_unidic_adapter.py");
        std::fs::write(&adapter,"import os,sys,time\nfrom pathlib import Path\nsys.stdin.readline()\nPath(__file__).with_suffix('.pid').write_text(str(os.getpid()))\ntime.sleep(60)\n").unwrap();
        let mut config = DialogueConfig {
            helper: helper.clone(),
            ..DialogueConfig::default()
        };
        config.haiku.memory_enabled = false;
        config.web.enabled = false; // Only the deliberately stalled dictionary adapter is owned.
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "試験", true);
        {
            let mut d = dialogue.data.lock().unwrap();
            d.sessions
                .get_mut("s")
                .unwrap()
                .knowledge_queue
                .push_back(pending("evicted", true));
            for i in 0..200 {
                d.rows.push_back(json!({"turn_id":format!("old-{i}")}));
            }
        }
        dialogue.resume_knowledge_input("s");
        {
            let d = dialogue.data.lock().unwrap();
            assert_eq!(d.rows.len(), 200);
            let row = d.rows.iter().find(|r| r["turn_id"] == "evicted").unwrap();
            assert_eq!(row["input_at_ms"], 7);
            assert_eq!(row["source"], "voice");
            assert_eq!(row["player_input_text"], "枕詞って何？");
        }
        let pid_file = adapter.with_extension("pid");
        let pid: i32 = tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if let Ok(pid) = std::fs::read_to_string(&pid_file)
                    .unwrap_or_default()
                    .trim()
                    .parse::<i32>()
                    && pid > 0
                {
                    break pid;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        tokio::time::timeout(Duration::from_secs(2), dialogue.shutdown())
            .await
            .unwrap();
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
        assert_eq!(
            std::io::Error::last_os_error().raw_os_error(),
            Some(libc::ESRCH)
        );
        std::fs::remove_file(helper).unwrap();
        std::fs::remove_file(adapter).unwrap();
        std::fs::remove_file(pid_file).unwrap();
        std::fs::remove_dir(folder).unwrap();
    }

    #[tokio::test]
    async fn manual_stop_cancels_pending_parser_and_late_result_cannot_restore_it() {
        let mut config = DialogueConfig::default();
        config.haiku.memory_enabled = false;
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "試験", true);
        {
            let mut d = dialogue.data.lock().unwrap();
            d.sessions.get_mut("s").unwrap().chat_allowed = false;
        }
        let response = dialogue.submit(Some("s"), "枕詞って何？", "voice");
        assert_eq!(response["reason"], "knowledge_input_routing");
        dialogue.interrupt("s");
        dialogue.shutdown().await;
        let d = dialogue.data.lock().unwrap();
        assert!(d.sessions["s"].knowledge_queue.is_empty());
        assert_eq!(d.rows.back().unwrap()["playback_status"], "cancelled");
        assert_eq!(d.rows.back().unwrap()["resolution"], "manual_interrupt");
    }
    #[tokio::test]
    async fn queued_cancel_and_late_classifier_share_one_terminal_record() {
        let f = super::super::workshop_record::tests::fixture();
        f.d.data
            .lock()
            .unwrap()
            .sessions
            .get_mut("s")
            .unwrap()
            .chat_allowed = false;
        let result = f.d.submit(Some("s"), "枕詞って何？", "voice");
        assert_eq!(result["reason"], "knowledge_input_routing");
        f.d.interrupt("s");
        f.d.interrupt("s");
        let jobs = std::mem::take(&mut *f.d.jobs.lock().unwrap());
        for job in jobs {
            job.await.unwrap();
        }
        let rows = f.records().await;
        let results: Vec<_> = rows
            .iter()
            .filter(|r| r["event_kind"] == "turn_result")
            .collect();
        assert_eq!(results.len(), 1, "{rows:?}");
        assert_eq!(results[0]["turn_id"], result["turn_id"]);
        assert_eq!(results[0]["result"]["resolution"], "manual_interrupt");
        assert_eq!(results[0]["result"]["playback_status"], "cancelled");
        assert!(results[0]["semantic_player_text"].is_null());
        f.finish().await;
    }

    #[tokio::test]
    async fn unsuccessful_queue_routing_has_one_terminal_without_a_normal_turn() {
        for cancelled in [false, true] {
            let f = super::super::workshop_record::tests::fixture();
            let before = f.d.workshop_record_state("s");
            let audit = super::super::workshop_record::Attempt::new(
                before,
                super::super::workshop_record::Input {
                    raw: "こんにちは".into(),
                    epoch: Some(0),
                    ..Default::default()
                },
            );
            let (tx, rx) = watch::channel(cancelled);
            let request = Request {
                turn: "route-only".into(),
                text: "こんにちは".into(),
                source: "text".into(),
                input_at: 0,
                record_private: false,
            };
            {
                let mut data = f.d.data.lock().unwrap();
                let s = data.sessions.get_mut("s").unwrap();
                s.input_generation = 2; // The non-knowledge input was superseded, so it cannot forward.
                s.knowledge_queue.push_back(Pending {
                    request: request.clone(),
                    ready: false,
                    cancel: tx,
                    audit: audit.clone(),
                });
                data.rows.push_back(json!({"session_id":"s","turn_id":request.turn,"epoch":0,"playback_status":"routing"}));
            }
            f.d.clone()
                .classify_knowledge_input("s".into(), request, 1, rx, audit)
                .await;
            let rows = f.records().await;
            assert_eq!(rows.len(), 1, "{rows:?}");
            assert_eq!(rows[0]["event_kind"], "turn_result");
            assert_eq!(
                rows[0]["result"]["playback_status"],
                if cancelled { "failed" } else { "not_selected" }
            );
            assert_eq!(
                rows[0]["result"]["resolution"],
                if cancelled {
                    "knowledge_route_failed"
                } else {
                    "superseded_input"
                }
            );
            assert!(
                f.d.data.lock().unwrap().sessions["s"]
                    .knowledge_queue
                    .is_empty()
            );
            f.finish().await;
        }
    }

    #[tokio::test]
    async fn queue_handoff_records_only_the_new_epoch_and_keeps_private_on_eviction() {
        for private in [false, true] {
            let f = super::super::workshop_record::tests::fixture();
            let before = f.d.workshop_record_state("s");
            let audit = super::super::workshop_record::Attempt::new(
                before,
                super::super::workshop_record::Input {
                    raw: "気にせんで".into(),
                    private,
                    epoch: Some(0),
                    ..Default::default()
                },
            );
            // Ready, code-only request isolates dispatch/recording from the language worker.
            // Its original display row has already left the bounded UI ring.
            {
                let mut data = f.d.data.lock().unwrap();
                data.sessions
                    .get_mut("s")
                    .unwrap()
                    .knowledge_queue
                    .push_back(Pending {
                        request: Request {
                            turn: "reused-id".into(),
                            text: "気にせんで".into(),
                            source: "text".into(),
                            input_at: 7,
                            record_private: private,
                        },
                        ready: true,
                        cancel: watch::channel(false).0,
                        audit: audit.clone(),
                    });
            }
            f.d.resume_knowledge_input("s");
            let jobs = std::mem::take(&mut *f.d.jobs.lock().unwrap());
            for job in jobs {
                job.await.unwrap();
            }
            f.d.record_workshop_turn("s", "reused-id", &audit); // A delayed queue owner cannot finish again.
            let rows = f.records().await;
            if private {
                assert!(rows.is_empty(), "{rows:?}");
            } else {
                let results: Vec<_> = rows
                    .iter()
                    .filter(|r| r["event_kind"] == "turn_result")
                    .collect();
                assert_eq!(results.len(), 1, "{rows:?}");
                assert_eq!(results[0]["turn_id"], "reused-id");
                assert_eq!(results[0]["epoch"], 1);
                assert!(
                    rows.iter()
                        .any(|r| r["event_kind"] == "forwarded_admission")
                );
            }
            f.finish().await;
        }
    }
}
