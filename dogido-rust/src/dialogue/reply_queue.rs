//! 警告中の質問と緊急警告で未回答になった通常入力を、有界FIFOへ保持する。
//! 原文・分類・元turnを保持し、警告終了後に新しいepochと現在観測で同じ受付へ戻す。
//! 操作・保存・workshopの再実行は所有者へ任せ、配送確認済みの回答は保持しない。
use super::*;
/// 警告後に再送する元の入力。input_atはDialogue時計の経過ミリ秒。
/// 元turnと受理時の分類・記録制限を保持し、新しい依頼として二重実行しない。
#[derive(Clone, Debug)]
pub(super) struct Request {
    pub turn: String,
    pub text: String,
    pub source: String,
    pub input_at: u64,
    pub record_private: bool,
    pub classification: Option<crate::request_classifier::Classification>,
}

#[cfg(test)]
#[path = "combat_reply_tests.rs"]
mod combat_reply_tests;

pub(super) struct Pending {
    pub request: Request,
    ready: bool,
    dispatch_input: bool,
    cancel: watch::Sender<bool>,
    audit: super::workshop_record::Attempt,
}
pub(super) struct Checked {
    pub dispatch_input: bool,
    pub generation: u64,
    pub original: Option<Request>,
    pub audit: Option<super::workshop_record::Attempt>,
}

/// 取消所有者が残る実再生中の台詞だけを確認する。生成待ち・表示ACKは含めない。
pub(super) fn playback_started(d: &Data, sid: &str) -> bool {
    let Some(s) = d.sessions.get(sid) else {
        return false;
    };
    s.warning.as_ref().is_some_and(|w| w.started)
        || d.rows.iter().any(|r| {
            r["session_id"] == sid
                && r["playback_status"] == "started"
                && (s.cancel.is_some() && r["turn_id"] == s.current_turn
                    || s.haiku
                        .active
                        .as_ref()
                        .is_some_and(|a| r["haiku_job_id"] == a.id))
        })
}

/// 再送中・戦闘解除待ちも一件として数え、保持した入力の戻り先を予約する。
pub(super) fn occupied_slots(s: &Session) -> usize {
    s.reply_queue.len()
        + usize::from(
            s.reply_checked
                .as_ref()
                .is_some_and(|c| c.original.is_some()),
        )
        + usize::from(s.cancel.is_some())
        + usize::from(s.combat_input.is_some())
}

fn ready(s: &Session) -> bool {
    if super::workshop_focus::active(s) {
        (s.preview && fresh(s)) || super::workshop_combat_runtime::clear_for_resume(s)
    } else {
        observation_fresh(s)
    }
}

/// 再実行してよい通常会話だけを選ぶ。既存の操作・保存判定をここで拡張しない。
pub(super) fn ordinary(
    s: &Session,
    text: &str,
    classification: Option<&crate::request_classifier::Classification>,
) -> bool {
    !super::workshop_focus::active(s)
        && crate::poem_input::parse(text, false).is_none()
        && !crate::haiku_memory::clear_requested(text)
        && !crate::haiku_memory::memory_candidate(text)
        && !crate::combat::core::wants_quiet(text)
        && classification
            .is_none_or(|c| c.prediction.label == crate::request_classifier::Label::NotTarget)
}

fn combat_delivery(s: &Session) -> bool {
    s.warning.as_ref().is_some_and(|w| {
        w.actions
            .iter()
            .any(|a| a.delivery == crate::combat::model::Delivery::Combat)
    }) || s.pending_warning.as_ref().is_some_and(|actions| {
        actions
            .iter()
            .any(|a| a.delivery == crate::combat::model::Delivery::Combat)
    })
}

impl Dialogue {
    /// 緊急警告で取り消す直前に、まだ届いていない通常返答の元入力を保持する。
    /// ACK確認と同じdataロック下で判定するため、表示済みの回答を再送へ戻さない。
    pub(super) fn retain_interrupted_reply(&self, d: &mut Data, sid: &str) {
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        if s.cancel.is_none() {
            return;
        }
        let Some(row) = d
            .rows
            .iter_mut()
            .find(|r| r["session_id"] == sid && r["turn_id"] == s.current_turn)
        else {
            return;
        };
        if row["combat_resumable"] != true
            || row["delivery_confirmed"] == true
            || !row["horse_command_id"].is_null()
            || s.reply_queue
                .iter()
                .any(|p| row["turn_id"] == p.request.turn)
        {
            return;
        }
        let request = Request {
            turn: s.current_turn.clone(),
            text: row["player_input_text"].as_str().unwrap_or("").into(),
            source: row["source"].as_str().unwrap_or("text").into(),
            input_at: row["input_at_ms"].as_u64().unwrap_or(0),
            record_private: row["workshop_record_private"] == true,
            classification: serde_json::from_value(row["request_classification"].clone()).ok(),
        };
        let audit = super::workshop_record::Attempt::new(
            row["workshop_state_before"].clone(),
            super::workshop_record::Input {
                raw: request.text.clone(),
                semantic: row["interpreted_player_input_text"]
                    .as_str()
                    .map(str::to_owned),
                private: request.record_private,
                epoch: row["epoch"].as_u64(),
            },
        );
        // 新しい待機入力の受付時に進行中の一件も上限へ数え、ここへ戻す枠を確保する。
        s.reply_queue.push_front(Pending {
            request,
            audit,
            ready: true,
            dispatch_input: false,
            cancel: watch::channel(false).0,
        });
        row["resolution"] = "reply_interrupted".into();
        s.delivery.forget_reply(&s.current_turn);
    }

    pub(super) fn cancel_reply_queue(&self, d: &mut Data, sid: &str, reason: &str) {
        Self::cancel_request_classification(d, sid);
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        s.input_generation = s.input_generation.wrapping_add(1);
        s.body_requests.cancel();
        let after = super::workshop_record::state(s.haiku.workshop.as_ref());
        let mut cancelled = Vec::new();
        if let Some(checked) = s.reply_checked.take()
            && let Some(original) = checked.original
        {
            cancelled.push((original.turn, checked.audit));
        }
        for pending in s.reply_queue.drain(..) {
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

    pub(super) fn queue_reply_input(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<tokio::task::JoinHandle<()>>,
        sid: &str,
        text: &str,
        source: &str,
        allow_conversation: bool,
    ) -> Option<Value> {
        let dispatch_input = playback_started(d, sid);
        let s = d.sessions.get_mut(sid)?;
        if !dispatch_input
            && (super::workshop_focus::quiet(s)
                || s.reply_checked
                    .as_ref()
                    .is_some_and(|c| c.generation == s.input_generation)
                || (ready(s)
                    && s.warning.is_none()
                    && s.pending_warning.is_none()
                    && s.reply_queue.is_empty()))
        {
            return None;
        }
        // 分類済み・戦闘解除待ちの予約枠も合わせて上限9件。先に受けた入力を守る。
        if occupied_slots(s) >= 9 {
            return Some(json!({"accepted":false,"reason":"reply_queue_full"}));
        }
        let private = s
            .record_private_generation
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
        let instance = s.instance_id.clone();
        let request = Request {
            classification: super::request_runtime::classification(s, text, source).cloned(),
            turn: id("reply"),
            text: text.into(),
            source: source.into(),
            input_at: self.clock.elapsed().as_millis() as u64,
            record_private: private,
        };
        let generation = s.input_generation;
        // 雷・夕方の専用保留は従来の所有者へ返す。戦闘警告中の通常会話を同じFIFOへ加える。
        let retain_conversation =
            allow_conversation && (combat_delivery(s) || !s.reply_queue.is_empty());
        let (cancel, rx) = watch::channel(false);
        s.reply_queue.push_back(Pending {
            request: request.clone(),
            audit: audit.clone(),
            ready: false,
            dispatch_input,
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
            this.classify_reply_input(
                session,
                request,
                generation,
                rx,
                audit,
                instance,
                retain_conversation,
                dispatch_input,
            )
            .await;
        }));
        Some(
            json!({"accepted":true,"queued":true,"session_id":sid,"turn_id":turn,"reason":"reply_input_routing"}),
        )
    }

    #[allow(clippy::too_many_arguments)]
    async fn classify_reply_input(
        self: Arc<Self>,
        sid: String,
        mut request: Request,
        generation: u64,
        mut cancel: watch::Receiver<bool>,
        audit: super::workshop_record::Attempt,
        instance: String,
        retain_conversation: bool,
        dispatch_input: bool,
    ) {
        // 危険中も依頼の種類だけはCPUで判定する。操作・知識回答・音声はここでは行わない。
        let classified =
            if self.config.request_classifier.enabled() && request.classification.is_none() {
                self.classify_request(&request.text, &request.source, &mut cancel)
                    .await
                    .map(|result| {
                        request.classification = Some(result);
                    })
            } else {
                Ok(())
            };
        let result = if let Err(error) = classified {
            Err(error)
        } else if dispatch_input {
            // 再生中の入力は行為も意味もまだ実行しない。分類だけ保持し、終了後に通常受付へ戻す。
            Ok(json!({"knowledge_query":true}))
        } else {
            let mut input = bridge::Input::from(json!({"op":"assist_route","text":request.text}));
            input.request_classification = request.classification.clone();
            let work = bridge::render(&self.config, &self.llm, input, &mut cancel);
            tokio::pin!(work);
            tokio::select! {
                result=&mut work=>result,
                _=tokio::time::sleep(Duration::from_secs(3))=>{
                    if let Some(p) = self.data.lock().unwrap().sessions.get(&sid)
                        .and_then(|s| s.reply_queue.iter().find(|p| p.request.turn == request.turn)) {
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
            if s.instance_id != instance {
                return;
            }
            let Some(index) = s
                .reply_queue
                .iter()
                .position(|p| p.request.turn == request.turn)
            else {
                return;
            };
            if s.reply_queue[index].request.text != request.text
                || s.reply_queue[index].request.source != request.source
            {
                return;
            }
            s.reply_queue[index].request.classification = request.classification.clone();
            let retained = if *cancel.borrow() {
                None
            } else {
                result
                    .as_ref()
                    .ok()
                    .and_then(|r| r["knowledge_query"].as_bool())
                    .map(|knowledge| {
                        knowledge
                            || retain_conversation
                                && ordinary(s, &request.text, request.classification.as_ref())
                    })
            };
            tracing::info!(event="reply_input_routed",session_id=sid,turn_id=request.turn,
                retained=?retained,queue_size=s.reply_queue.len());
            let forward = retained == Some(false) && generation == s.input_generation;
            if retained == Some(true) {
                s.reply_queue[index].ready = true;
            } else {
                s.reply_queue.remove(index);
                if forward {
                    s.request_classification
                        .restore(generation, request.classification.clone());
                    s.reply_checked = Some(Checked {
                        dispatch_input: false,
                        generation,
                        original: None,
                        audit: None,
                    });
                }
            }
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == request.turn) {
                row["playback_status"] = match retained {
                    Some(true) => "waiting_for_warning",
                    Some(false) => "not_selected",
                    None => "failed",
                }
                .into();
                row["resolution"] = match retained {
                    Some(true) => "reply_queued",
                    Some(false) if forward => "other_input",
                    Some(false) => "superseded_input",
                    None => "reply_route_failed",
                }
                .into();
                if let Err(error) = &result {
                    row["error"] = error.to_string().into();
                }
            }
            d.revision += 1;
            (forward, retained == Some(true))
        };
        if forward {
            let response = self.submit_recorded_guarded(
                Some(&sid),
                &request.text,
                &request.source,
                false,
                Some((generation, None)),
                false,
                super::workshop_record::Admission {
                    forwarded: true,
                    private: request.record_private,
                },
                Some(&instance),
            );
            let mut d = self.data.lock().unwrap();
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == request.turn) {
                row["forwarded_input"] = response;
            }
            d.revision += 1;
        }
        if !retained {
            self.record_workshop_turn(&sid, &request.turn, &audit);
        } else {
            self.resume_reply_input(&sid);
        }
    }

    /// 戦闘側が予約した一件を、同じdataロック下で既存FIFOへ移す。
    /// 受付時刻で挿入し、保留中に追加された入力に追い越されないようにする。
    pub(super) fn queue_combat_reply(
        d: &mut Data,
        sid: &str,
        request: Request,
        audit: super::workshop_record::Attempt,
    ) -> Value {
        let s = d.sessions.get_mut(sid).unwrap();
        let index = s
            .reply_queue
            .iter()
            .position(|p| p.request.input_at > request.input_at)
            .unwrap_or(s.reply_queue.len());
        s.reply_queue.insert(
            index,
            Pending {
                request: request.clone(),
                audit: audit.clone(),
                ready: true,
                dispatch_input: false,
                cancel: watch::channel(false).0,
            },
        );
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(
            json!({"utterance_id":id("utt"),"turn_id":request.turn,"session_id":sid,
            "player_input_text":request.text,"source":request.source,"text":"","category":"routing",
            "input_at_ms":request.input_at,"epoch":audit.input.epoch,
            "workshop_record_private":request.record_private,"created_at":chrono::Utc::now(),
            "playback_status":"waiting_for_warning","resolution":"reply_queued"}),
        );
        json!({"accepted":true,"queued":true,"session_id":sid,"turn_id":request.turn,
            "reason":"reply_input_routing"})
    }

    pub(super) fn resume_reply_input(self: &Arc<Self>, sid: &str) {
        let (pending, generation, instance) = {
            let mut d = self.data.lock().unwrap();
            if d.stopped || playback_started(&d, sid) {
                return;
            }
            let Some(s) = d.sessions.get_mut(sid) else {
                return;
            };
            let dispatch_input = s.reply_queue.front().is_some_and(|p| p.dispatch_input);
            if !(if dispatch_input {
                observation_fresh(s)
            } else {
                ready(s)
            }) || s.warning.is_some()
                || s.pending_warning.is_some()
                || s.cancel.is_some()
                || s.assist_pending.is_some()
                || s.request_classification.pending.is_some()
                || (self.config.request_classifier.enabled()
                    && s.reply_queue
                        .iter()
                        .any(|p| p.request.classification.is_none()))
                || s.combat_input.is_some()
                || s.deferred_input.is_some()
                || s.haiku.active.is_some()
                || s.reply_checked
                    .as_ref()
                    .is_some_and(|c| c.original.is_some())
                || !dispatch_input
                    && s.haiku
                        .workshop
                        .as_ref()
                        .is_some_and(|w| w.open && w.combat_paused())
                || s.reply_queue.front().is_none_or(|p| !p.ready)
            {
                return;
            }
            let pending = s.reply_queue.pop_front().unwrap();
            s.input_generation = s.input_generation.wrapping_add(1);
            let generation = s.input_generation;
            s.request_classification
                .restore(generation, pending.request.classification.clone());
            s.reply_checked = Some(Checked {
                dispatch_input: pending.dispatch_input,
                generation,
                original: Some(pending.request.clone()),
                audit: Some(pending.audit.clone()),
            });
            (pending, generation, s.instance_id.clone())
        };
        let response = self.submit_recorded_guarded(
            Some(sid),
            &pending.request.text,
            &pending.request.source,
            !pending.dispatch_input,
            Some((generation, Some(pending.request.turn.clone()))),
            !pending.dispatch_input,
            super::workshop_record::Admission {
                forwarded: true,
                private: pending.audit.input.private,
            },
            Some(&instance),
        );
        let mut d = self.data.lock().unwrap();
        if let Some(s) = d.sessions.get_mut(sid)
            && s.instance_id == instance
            && let Some(c) = s
                .reply_checked
                .as_mut()
                .filter(|c| c.generation == generation)
        {
            c.original = None;
            c.audit = None;
        }
        if response["accepted"] == true && response["turn_id"] != pending.request.turn {
            if let Some(row) = d
                .rows
                .iter_mut()
                .find(|r| r["turn_id"] == pending.request.turn)
            {
                row["playback_status"] = "not_selected".into();
                row["resolution"] = "input_dispatched".into();
                row["forwarded_input"] = response.clone();
            }
            d.revision += 1;
        }
        if response["accepted"] != true {
            // ロックを離した間の警告開始・観測失効・満杯だけ戻す。手動停止・新入力・終了は復活させない。
            let retry = !d.stopped
                && matches!(
                    response["reason"].as_str(),
                    Some(
                        "fresh_safe_snapshot_required" | "input_queue_full" | "warning_in_progress"
                    )
                );
            if let Some(s) = d.sessions.get_mut(sid)
                && s.instance_id == instance
                && s.input_generation == generation
                && retry
            {
                s.reply_checked = None;
                s.reply_queue.push_front(pending);
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
                classification: None,
                record_private: false,
                turn: turn.into(),
                text: "枕詞って何？".into(),
                source: "voice".into(),
                input_at: 7,
            },
            ready,
            dispatch_input: false,
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
            s.reply_queue
                .extend([pending("first", false), pending("second", true)]);
        }
        dialogue.resume_reply_input("s");
        {
            let mut d = dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            assert_eq!(s.reply_queue.len(), 2);
            s.reply_queue[0].ready = true;
            s.reply_checked = Some(Checked {
                dispatch_input: false,
                generation: 0,
                audit: None,
                original: Some(pending("reserved", true).request),
            });
        }
        dialogue.resume_reply_input("s");
        assert_eq!(
            dialogue.data.lock().unwrap().sessions["s"]
                .reply_queue
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
            d.sessions.get_mut("s").unwrap().reply_checked = Some(Checked {
                dispatch_input: false,
                generation: 0,
                audit: None,
                original: Some(pending("reserved", true).request),
            });
            d.rows
                .push_back(json!({"turn_id":"reserved","playback_status":"waiting_for_warning"}));
            dialogue.cancel_reply_queue(&mut d, "s", "world_context_changed");
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
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "試験", true);
        {
            let mut d = dialogue.data.lock().unwrap();
            d.sessions
                .get_mut("s")
                .unwrap()
                .reply_queue
                .push_back(pending("evicted", true));
            for i in 0..200 {
                d.rows.push_back(json!({"turn_id":format!("old-{i}")}));
            }
        }
        dialogue.resume_reply_input("s");
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
            d.sessions.get_mut("s").unwrap().pending_warning =
                Some(vec![crate::combat::model::Speech::new(
                    "creeper_fuse",
                    "危ない！",
                )]);
        }
        let response = dialogue.submit(Some("s"), "枕詞って何？", "voice");
        assert_eq!(response["reason"], "reply_input_routing");
        dialogue.interrupt("s");
        dialogue.shutdown().await;
        let d = dialogue.data.lock().unwrap();
        assert!(d.sessions["s"].reply_queue.is_empty());
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
        assert_eq!(result["reason"], "reply_input_routing");
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
                classification: None,
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
                s.reply_queue.push_back(Pending {
                    request: request.clone(),
                    ready: false,
                    dispatch_input: false,
                    cancel: tx,
                    audit: audit.clone(),
                });
                data.rows.push_back(json!({"session_id":"s","turn_id":request.turn,"epoch":0,"playback_status":"routing"}));
            }
            let instance = f.d.data.lock().unwrap().sessions["s"].instance_id.clone();
            f.d.clone()
                .classify_reply_input("s".into(), request, 1, rx, audit, instance, false, false)
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
                    "reply_route_failed"
                } else {
                    "superseded_input"
                }
            );
            assert!(
                f.d.data.lock().unwrap().sessions["s"]
                    .reply_queue
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
                    .reply_queue
                    .push_back(Pending {
                        request: Request {
                            classification: None,
                            turn: "reused-id".into(),
                            text: "気にせんで".into(),
                            source: "text".into(),
                            input_at: 7,
                            record_private: private,
                        },
                        ready: true,
                        dispatch_input: false,
                        cancel: watch::channel(false).0,
                        audit: audit.clone(),
                    });
            }
            f.d.resume_reply_input("s");
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

#[cfg(test)]
mod playback_completion_tests {
    use super::*;

    fn speaking(status: &str) -> (Arc<Dialogue>, watch::Receiver<bool>) {
        let mut config = DialogueConfig::default();
        config.haiku.memory_enabled = false;
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "試験", true);
        let (tx, rx) = watch::channel(false);
        {
            let mut d = dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            s.current_turn = "speaking".into();
            s.cancel = Some(tx);
            d.rows.push_back(json!({"session_id":"s","turn_id":"speaking",
                "playback_status":status,"text":"最後まで話すで。","player_input_text":"前の質問"}));
        }
        (dialogue, rx)
    }

    #[tokio::test]
    async fn acknowledgement_and_sneeze_queue_without_cancelling_started_reply() {
        let (dialogue, rx) = speaking("started");
        let first = dialogue.submit(Some("s"), "うん", "voice");
        let second = dialogue.submit(Some("s"), "へっくしょん", "voice");
        assert_eq!(first["queued"], true);
        assert_eq!(second["queued"], true);
        assert_eq!(
            dialogue.submit(Some("s"), "うん", "voice")["deduplicated"],
            true
        );
        for _ in 0..8 {
            tokio::task::yield_now().await;
        }
        {
            let mut d = dialogue.data.lock().unwrap();
            dialogue.accept_vocalization(&mut d, "s", "うおおお");
            assert!(!*rx.borrow());
            assert_eq!(d.rows[0]["playback_status"], "started");
            assert_eq!(d.sessions["s"].reply_queue.len(), 2);
            assert!(
                d.sessions["s"]
                    .reply_queue
                    .iter()
                    .all(|p| p.ready && p.dispatch_input)
            );
        }
        dialogue.resume_reply_input("s");
        assert_eq!(
            dialogue.data.lock().unwrap().sessions["s"]
                .reply_queue
                .len(),
            2
        );
        dialogue.interrupt("s");
        assert!(*rx.borrow());
        assert!(
            dialogue.data.lock().unwrap().sessions["s"]
                .reply_queue
                .is_empty()
        );
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn new_input_still_replaces_a_reply_before_playback_begins() {
        let (dialogue, rx) = speaking("queued");
        assert_eq!(
            dialogue.submit(Some("s"), "ドギド、別の話やで", "text")["accepted"],
            true
        );
        assert!(*rx.borrow());
        assert!(
            dialogue.data.lock().unwrap().sessions["s"]
                .reply_queue
                .is_empty()
        );
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn explicit_quiet_bypasses_stale_observation_and_keeps_manual_stop() {
        let (dialogue, rx) = speaking("started");
        dialogue
            .data
            .lock()
            .unwrap()
            .sessions
            .get_mut("s")
            .unwrap()
            .preview = false;
        let result = dialogue.submit(Some("s"), "静かにして", "voice");
        assert_eq!(result["accepted"], true);
        assert!(*rx.borrow());
        assert!(
            dialogue.data.lock().unwrap().sessions["s"]
                .reply_queue
                .is_empty()
        );
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn workshop_input_waits_without_changing_the_poem_or_pending() {
        let f = super::super::workshop_record::tests::fixture();
        let before = f.d.workshop_record_state("s");
        let (tx, rx) = watch::channel(false);
        {
            let mut d = f.d.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            s.current_turn = "explanation".into();
            s.cancel = Some(tx);
            d.rows.push_back(
                json!({"session_id":"s","turn_id":"explanation","playback_status":"started"}),
            );
        }
        let response = f.d.submit(Some("s"), "上五をあさひかるにして", "voice");
        assert_eq!(response["queued"], true);
        for _ in 0..8 {
            tokio::task::yield_now().await;
        }
        assert!(!*rx.borrow());
        assert_eq!(f.d.workshop_record_state("s"), before);
        assert_eq!(f.d.data.lock().unwrap().sessions["s"].reply_queue.len(), 1);
        f.finish().await;
    }
}
