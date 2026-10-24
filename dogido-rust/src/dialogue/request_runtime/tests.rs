//! 模擬分類workerで共通受付・取消・既存FIFOへの接続を検査する。モデル精度の試験ではない。
use super::*;
use std::{fs, path::Path};

struct Fixture {
    dir: PathBuf,
    d: Arc<Dialogue>,
}
impl Fixture {
    fn new(label: usize, delay: f64, fail: bool) -> Self {
        Self::with_modes(label, delay, fail, &[Mode::Normal])
    }
    fn with_modes(label: usize, delay: f64, fail: bool, modes: &[Mode]) -> Self {
        let dir =
            std::env::temp_dir().join(format!("dogido-request-runtime-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&dir).unwrap();
        for mode in modes {
            let root = dir.join(mode.name());
            fs::create_dir(&root).unwrap();
            fs::write(root.join("adopted-model.json"), serde_json::to_vec(&json!({"schema_version":1,"path_base":"repository_root","classifier_mode":mode,"label_to_id": mode.labels().iter().enumerate().map(|(i,l)| (serde_json::to_value(l).unwrap().as_str().unwrap().to_owned(),i)).collect::<std::collections::BTreeMap<_,_>>(),"evaluated_input":{"max_history_utterances":0,"max_length":128,"truncation":false}})).unwrap()).unwrap();
        }
        fs::write(
            dir.join("helper.py"),
            "raise AssertionError('no generic helper')\n",
        )
        .unwrap();
        let script = r#"
import fcntl, json, sys, time
from pathlib import Path
base = Path(__file__).parent
for line in sys.stdin:
    f = json.loads(line)
    r = {k:f[k] for k in ('schema_version','request_id','manifest_sha256','mode')}
    labels = domains[f['mode']]
    if f['op'] == 'load':
        r.update(ready=True, labels=labels, limits={'max_history_utterances':0,'max_length':128,'truncation':False})
    else:
        lock = base.joinpath('inference.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = base.joinpath('block-inference').exists()
        with base.joinpath('inputs.jsonl').open('a') as out: out.write(json.dumps(f,ensure_ascii=False)+'\n')
        if blocked: time.sleep(60)
        time.sleep(DELAY)
        if FAIL: r['error'] = 'deliberate test failure'
        else: r['prediction'] = {'label':labels[LABEL], 'probabilities':[int(i==LABEL) for i in range(len(labels))],'token_count':30,'inference_ms':1}
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    print(json.dumps(r),flush=True)
"#;
        let labels = serde_json::to_string(
            &json!({"normal":Mode::Normal.labels(),"senryu":Mode::Senryu.labels()}),
        )
        .unwrap();
        fs::write(
            dir.join("request_classifier_worker.py"),
            format!(
                "domains={labels}\nLABEL={label}\nDELAY={delay}\nFAIL={}\n{script}",
                if fail { "True" } else { "False" }
            ),
        )
        .unwrap();
        let mut config = DialogueConfig {
            helper: dir.join("helper.py"),
            python: "/usr/bin/python3".into(),
            audio_enabled: false,
            llm_enabled: false,

            request_classifier: Settings {
                normal_model_root: modes.contains(&Mode::Normal).then(|| dir.join("normal")),
                senryu_model_root: modes.contains(&Mode::Senryu).then(|| dir.join("senryu")),
                python: Some("/usr/bin/python3".into()),
                ..Settings::default()
            },
            ..DialogueConfig::default()
        };
        config.haiku.memory_enabled = false;
        let d = Dialogue::new(config).unwrap();
        d.register("s", "試験", true);
        d.set_execution_capabilities("s", &[crate::assist::SELECT_HOTBAR_CAPABILITY.into()]);
        d.data.lock().unwrap().sessions.get_mut("s").unwrap().latest = Some(crate::events::GameEvent::parse(json!({
            "schema_version":"2026-05-24","adapter":"test","sequence":1,"observed_at":chrono::Utc::now(),
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"dimension":"minecraft:overworld","hotbar":{"selected_slot":8,"slots":[{"slot":0,"item_id":"minecraft:stone_sword","count":1,"damage":0,"max_damage":131,"attack_damage":5,"weapon_kind":"sword"}]}},
            "world":{"time_phase":"day","weather":"clear","biome":"plains","local_light":15}})).unwrap());
        Self { dir, d }
    }
    fn inputs(&self) -> Vec<Value> {
        fs::read_to_string(self.dir.join("inputs.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }
    fn row(&self, turn: &Value) -> Value {
        self.d
            .data
            .lock()
            .unwrap()
            .rows
            .iter()
            .find(|r| r["turn_id"] == *turn)
            .unwrap()
            .clone()
    }
    async fn idle(&self) {
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                let jobs = std::mem::take(&mut *self.d.jobs.lock().unwrap());
                if jobs.is_empty() {
                    break;
                }
                for job in jobs {
                    job.await.unwrap();
                }
            }
        })
        .await
        .unwrap();
    }
    async fn finish(self) {
        self.d.shutdown().await;
        fs::remove_dir_all(self.dir).unwrap();
    }
}
async fn wait_for_file(path: &Path) {
    tokio::time::timeout(Duration::from_secs(3), async {
        while !fs::read_to_string(path).is_ok_and(|value| value.ends_with('\n')) {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn classified_labels_use_existing_routes_without_legacy_sword_override() {
    for label in 0..crate::request_classifier::NORMAL_LABELS.len() {
        let f = Fixture::new(label, 0.0, false);
        f.d.prepare_request_classifier().await.unwrap();
        let text = if label == 2 {
            "それに替えて"
        } else {
            "剣に持ち替えて"
        };
        let accepted = f.d.submit(Some("s"), text, "text");
        assert_eq!(accepted["reason"], "request_classification");
        f.idle().await;
        let row = f.row(&accepted["turn_id"]);
        assert_eq!(
            row["request_classification"]["prediction"]["probabilities"][label],
            1.0
        );
        let response = &row["forwarded_input"];
        if label == 2 {
            assert_eq!(response["reason"], "assist_input");
            assert_eq!(response["assist"]["intent"]["source"], "modernbert");
            assert!(!response["assist"]["command"].is_null(), "{response}");
        } else {
            assert!(
                f.d.data.lock().unwrap().sessions["s"]
                    .assist
                    .commands(chrono::Utc::now())
                    .is_empty()
            );
            if label == 3 {
                assert_eq!(response["reason"], "horse_selection_unavailable");
            }
        }
        assert_eq!(f.inputs().len(), 1);
        assert_eq!(
            f.inputs()[0]["text"],
            format!("履歴:\n（なし）\n現在のユーザー発話:\n{text}")
        );
        f.finish().await;
    }
}

#[tokio::test]
async fn newer_input_cancels_pending_work_without_inheriting_preceding_request() {
    let f = Fixture::new(2, 0.2, false);
    fs::write(f.dir.join("block-inference"), "").unwrap();
    let first = format!("  {}\n持ち替えて\t", "全文".repeat(90));
    let a = f.d.submit(Some("s"), &first, "text");
    wait_for_file(&f.dir.join("inputs.jsonl")).await;
    fs::remove_file(f.dir.join("block-inference")).unwrap();
    let b = f.d.submit(Some("s"), "それに替えて", "text");
    f.idle().await;
    assert_eq!(f.row(&a["turn_id"])["playback_status"], "cancelled");
    assert_eq!(
        f.row(&b["turn_id"])["forwarded_input"]["reason"],
        "assist_input"
    );
    let inputs = f.inputs();
    assert_eq!(inputs.len(), 2);
    assert_eq!(
        inputs[0]["text"],
        format!("履歴:\n（なし）\n現在のユーザー発話:\n{first}")
    );
    assert_eq!(
        inputs[1]["text"],
        "履歴:\n（なし）\n現在のユーザー発話:\nそれに替えて"
    );
    f.finish().await;
}

#[tokio::test]
async fn danger_queue_keeps_both_questions_and_replays_without_reclassification() {
    let f = Fixture::new(0, 0.1, false);
    f.d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .pending_warning = Some(vec![crate::combat::model::Speech::new(
        "creeper_fuse",
        "危ない！",
    )]);
    let a = f.d.submit(Some("s"), "剣の耐久値は？", "text");
    let b = f.d.submit(Some("s"), "枕詞って何？", "text");
    assert_eq!(a["reason"], "reply_input_routing");
    assert_eq!(b["reason"], "reply_input_routing");
    f.d.resume_reply_input("s");
    assert_eq!(f.d.data.lock().unwrap().sessions["s"].reply_queue.len(), 2);
    f.idle().await;
    assert_eq!(f.row(&a["turn_id"])["resolution"], "reply_queued");
    assert_eq!(f.row(&b["turn_id"])["resolution"], "reply_queued");
    assert_eq!(
        f.inputs()[1]["text"],
        "履歴:\n（なし）\n現在のユーザー発話:\n枕詞って何？"
    );
    f.d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .pending_warning = None;
    f.d.resume_reply_input("s");
    f.idle().await;
    let remaining: Vec<_> = f.d.data.lock().unwrap().sessions["s"]
        .reply_queue
        .iter()
        .map(|p| p.request.turn.clone())
        .collect();
    assert!(remaining.is_empty());
    assert_eq!(f.row(&a["turn_id"])["resolution"], "reply_resumed");
    assert_eq!(f.row(&b["turn_id"])["resolution"], "reply_resumed");
    assert_eq!(f.inputs().len(), 2);
    f.finish().await;
}

#[tokio::test]
async fn obsolete_action_from_danger_queue_never_dispatches() {
    let f = Fixture::new(2, 0.1, false);
    f.d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .pending_warning = Some(vec![crate::combat::model::Speech::new(
        "creeper_fuse",
        "危ない！",
    )]);
    let a = f.d.submit(Some("s"), "先の依頼", "text");
    let b = f.d.submit(Some("s"), "今の依頼", "text");
    f.idle().await;
    assert_eq!(f.row(&a["turn_id"])["resolution"], "superseded_input");
    assert!(f.row(&a["turn_id"])["forwarded_input"].is_null());
    assert_eq!(
        f.row(&b["turn_id"])["forwarded_input"]["reason"],
        "assist_input"
    );
    assert!(
        f.d.data.lock().unwrap().sessions["s"]
            .reply_queue
            .is_empty()
    );
    f.finish().await;
}

#[tokio::test]
async fn failure_and_interrupt_release_reservations_without_legacy_dispatch() {
    for interrupt in [false, true] {
        let f = Fixture::new(2, 0.2, true);
        if interrupt {
            fs::write(f.dir.join("block-inference"), "").unwrap();
        }
        f.d.data
            .lock()
            .unwrap()
            .sessions
            .get_mut("s")
            .unwrap()
            .chat_allowed = false;
        let a = f.d.submit(Some("s"), "剣にして", "text");
        wait_for_file(&f.dir.join("inputs.jsonl")).await;
        if interrupt {
            f.d.interrupt("s");
        }
        f.idle().await;
        assert!(
            f.d.data.lock().unwrap().sessions["s"]
                .reply_queue
                .is_empty()
        );
        assert!(
            f.d.data.lock().unwrap().sessions["s"]
                .assist
                .commands(chrono::Utc::now())
                .is_empty()
        );
        assert_eq!(
            f.row(&a["turn_id"])["playback_status"],
            if interrupt { "cancelled" } else { "failed" }
        );
        f.finish().await;
    }
}

#[tokio::test]
async fn reconnect_cannot_replay_a_result_even_when_generation_matches() {
    let f = Fixture::new(2, 0.0, false);
    let instance = f.d.data.lock().unwrap().sessions["s"].instance_id.clone();
    f.d.register("s", "再接続", true);
    let result = f.d.submit_recorded_guarded(
        Some("s"),
        "剣にして",
        "text",
        false,
        Some((0, None)),
        false,
        super::super::workshop_record::Admission {
            forwarded: true,
            private: false,
        },
        Some(&instance),
        None,
    );
    assert_eq!(result["reason"], "superseded_session");
    assert!(f.inputs().is_empty());
    for _ in 0..16 {
        f.d.jobs
            .lock()
            .unwrap()
            .push(tokio::spawn(std::future::pending::<()>()));
    }
    let result = f.d.submit_recorded_guarded(
        Some("s"),
        "うん",
        "text",
        false,
        Some((0, None)),
        false,
        super::super::workshop_record::Admission {
            forwarded: true,
            private: false,
        },
        Some(&instance),
        None,
    );
    assert_eq!(result["reason"], "superseded_session");
    assert!(f.inputs().is_empty());
    for job in f.d.jobs.lock().unwrap().iter() {
        job.abort();
    }
    f.finish().await;
}

#[tokio::test]
async fn current_input_excludes_completed_requests_without_erasing_conversation() {
    for danger in [false, true] {
        let f = Fixture::new(0, 0.0, false);
        {
            let mut d = f.d.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            s.history.push("past", "user", "馬呼んで");
            s.history.push("past", "assistant", "呼んだで！");
            s.chat_allowed = !danger;
            if danger {
                s.pending_warning = Some(vec![crate::combat::model::Speech::new(
                    "creeper_fuse",
                    "危ない！",
                )]);
            }
        }
        let current = "  ありがとう！\n";
        let accepted = f.d.submit(Some("s"), current, "text");
        assert_eq!(
            accepted["reason"],
            if danger {
                "reply_input_routing"
            } else {
                "request_classification"
            }
        );
        f.idle().await;
        assert_eq!(f.inputs().len(), 1);
        assert_eq!(
            f.inputs()[0]["text"],
            format!("履歴:\n（なし）\n現在のユーザー発話:\n{current}")
        );
        {
            let d = f.d.data.lock().unwrap();
            let s = &d.sessions["s"];
            assert!(s.history.rows().iter().any(|r| r["text"] == "馬呼んで"));
            assert!(s.history.rows().iter().any(|r| r["text"] == "呼んだで！"));
            assert!(s.assist.commands(chrono::Utc::now()).is_empty());
        }
        f.finish().await;
    }
}

#[tokio::test]
async fn playing_reply_queues_one_classification_then_dispatches_each_operation_once() {
    for label in [2, 3] {
        let f = Fixture::new(label, 0.0, false);
        f.d.prepare_request_classifier().await.unwrap();
        let (tx, rx) = watch::channel(false);
        {
            let mut d = f.d.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            s.current_turn = "old".into();
            s.cancel = Some(tx);
            d.rows
                .push_back(json!({"session_id":"s","turn_id":"old","playback_status":"started"}));
        }
        let accepted = f.d.submit(Some("s"), "それをお願い", "voice");
        assert_eq!(accepted["queued"], true);
        f.idle().await;
        assert!(!*rx.borrow());
        assert_eq!(f.inputs().len(), 1);
        assert!(
            f.d.data.lock().unwrap().sessions["s"]
                .assist
                .commands(chrono::Utc::now())
                .is_empty()
        );
        {
            let mut d = f.d.data.lock().unwrap();
            d.sessions.get_mut("s").unwrap().cancel = None;
            d.rows[0]["playback_status"] = "completed".into();
        }
        f.d.resume_reply_input("s");
        f.idle().await;
        let row = f.row(&accepted["turn_id"]);
        let response = &row["forwarded_input"];
        if label == 2 {
            assert_eq!(response["reason"], "assist_input", "{row}");
        } else {
            // 模擬sessionでは馬の候補取得は使えない。既存経路での拒否を終端として保持する。
            assert_eq!(row["resolution"], "horse_selection_unavailable", "{row}");
            assert_eq!(row["playback_status"], "cancelled");
        }
        assert_eq!(f.inputs().len(), 1);
        assert!(
            f.d.data.lock().unwrap().sessions["s"]
                .reply_queue
                .is_empty()
        );
        f.finish().await;
    }
}

#[tokio::test]
async fn explicit_quiet_does_not_wait_for_the_request_classifier() {
    let f = Fixture::new(0, 60.0, false);
    let (tx, rx) = watch::channel(false);
    {
        let mut d = f.d.data.lock().unwrap();
        let s = d.sessions.get_mut("s").unwrap();
        s.current_turn = "old".into();
        s.cancel = Some(tx);
        d.rows
            .push_back(json!({"session_id":"s","turn_id":"old","playback_status":"started"}));
    }
    assert_eq!(
        f.d.submit(Some("s"), "静かにして", "voice")["accepted"],
        true
    );
    assert!(*rx.borrow());
    assert!(f.inputs().is_empty());
    assert!(
        f.d.data.lock().unwrap().sessions["s"]
            .reply_queue
            .is_empty()
    );
    f.finish().await;
}

fn open_workshop(d: &Arc<Dialogue>, sid: &str) {
    use crate::haiku_record::{PreparedEmission, Workshop};
    let lines: Vec<Value> = [("upper","上五","くさちのひ"),("middle","中七","くろきつるぎの"),("lower","下五","かげのさむさ")].iter().enumerate().map(|(i,(position,name,text))| json!({"line_id":format!("line_{}",i+1),"line_index":i,"position":position,"canonical_name":name,"surface_text":text,"reading_text":text,"source_atom_ids":[],"source_atoms":[],"provenance":"fixture"})).collect();
    let prepared: PreparedEmission = serde_json::from_value(
        json!({"text":"くさちのひ くろきつるぎの かげのさむさ","lines":lines,"materials":{}}),
    )
    .unwrap();
    d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut(sid)
        .unwrap()
        .haiku
        .workshop = Some(Workshop::open(
        prepared.complete(chrono::Utc::now()).unwrap(),
        None,
        std::time::Instant::now(),
    ));
}

#[tokio::test]
async fn each_mode_calls_only_its_model_even_for_not_target_and_failures() {
    for mode in [Mode::Normal, Mode::Senryu] {
        for fail in [false, true] {
            let f = Fixture::with_modes(0, 0.0, fail, &[Mode::Normal, Mode::Senryu]);
            if mode == Mode::Senryu {
                open_workshop(&f.d, "s");
            }
            f.d.prepare_request_classifier().await.unwrap();
            let a = f.d.submit(Some("s"), "その話を聞かせて", "text");
            f.idle().await;
            assert_eq!(f.inputs().len(), 1);
            assert_eq!(f.inputs()[0]["mode"], json!(mode));
            let row = f.row(&a["turn_id"]);
            assert_eq!(row["classifier_mode"], json!(mode));
            if fail {
                assert_eq!(row["resolution"], "request_classification_failed");
            }
            f.finish().await;
        }
    }
}

#[tokio::test]
async fn missing_normal_model_refuses_without_using_senryu() {
    let f = Fixture::with_modes(0, 0.0, false, &[Mode::Senryu]);
    f.d.prepare_request_classifier().await.unwrap();
    let a = f.d.submit(Some("s"), "普通の話", "text");
    assert_eq!(a["accepted"], false);
    assert_eq!(a["reason"], "classifier_model_unavailable");
    assert_eq!(a["classifier_mode"], "normal");
    assert!(f.inputs().is_empty());
    open_workshop(&f.d, "s");
    f.d.submit(Some("s"), "句の意味は？", "text");
    f.idle().await;
    assert_eq!(f.inputs().len(), 1);
    assert_eq!(f.inputs()[0]["mode"], "senryu");
    f.finish().await;
}

#[tokio::test]
async fn two_sessions_and_two_models_share_one_inference_lock() {
    let f = Fixture::with_modes(0, 0.15, false, &[Mode::Normal, Mode::Senryu]);
    f.d.register("verse", "川柳", true);
    open_workshop(&f.d, "verse");
    f.d.prepare_request_classifier().await.unwrap();
    let a = f.d.submit(Some("s"), "通常の話", "text");
    let b = f.d.submit(Some("verse"), "句の意味は？", "text");
    f.idle().await;
    assert_eq!(f.inputs().len(), 2);
    for turn in [&a["turn_id"], &b["turn_id"]] {
        assert!(f.row(turn)["request_classification"].is_object());
    }
    f.finish().await;
}

#[tokio::test]
async fn delayed_results_never_cross_workshop_start_end_pause_aba_or_revision() {
    for boundary in ["start", "end", "pause_aba", "revision", "replace"] {
        let f = Fixture::with_modes(0, 0.0, false, &[Mode::Normal, Mode::Senryu]);
        if boundary != "start" {
            open_workshop(&f.d, "s");
        }
        fs::write(f.dir.join("block-inference"), "").unwrap();
        let a = f.d.submit(Some("s"), "同じ原文", "text");
        wait_for_file(&f.dir.join("inputs.jsonl")).await;
        if boundary == "start" || boundary == "replace" {
            open_workshop(&f.d, "s");
        } else {
            let mut data = f.d.data.lock().unwrap();
            let w = data
                .sessions
                .get_mut("s")
                .unwrap()
                .haiku
                .workshop
                .as_mut()
                .unwrap();
            match boundary {
                "end" => w.close("fixture"),
                "pause_aba" => {
                    w.pause(std::time::Instant::now());
                    w.resume(std::time::Instant::now());
                }
                "revision" => w.version += 1,
                _ => unreachable!(),
            }
        }
        f.idle().await;
        assert!(f.row(&a["turn_id"])["forwarded_input"].is_null());
        assert_eq!(f.inputs().len(), 1);
        f.finish().await;
    }
}

#[tokio::test]
async fn voice_generation_rejects_old_recordings_after_pause_resume_and_replacement() {
    let f = Fixture::with_modes(0, 0.0, false, &[Mode::Normal, Mode::Senryu]);
    f.d.enable_text_delivery("s", Some(true));
    let old = voice_generation(&f.d.data.lock().unwrap().sessions["s"]);
    open_workshop(&f.d, "s");
    assert_eq!(
        f.d.submit_voice(Some("s"), "句の話", Some(&old))["reason"],
        "stale_voice_generation"
    );
    assert!(
        f.d.data.lock().unwrap().sessions["s"]
            .delivery
            .voice_inputs
            .is_empty()
    );
    let before_pause = voice_generation(&f.d.data.lock().unwrap().sessions["s"]);
    {
        let mut data = f.d.data.lock().unwrap();
        let w = data
            .sessions
            .get_mut("s")
            .unwrap()
            .haiku
            .workshop
            .as_mut()
            .unwrap();
        w.pause(std::time::Instant::now());
        w.resume(std::time::Instant::now());
    }
    assert_eq!(
        f.d.submit_voice(Some("s"), "句の話", Some(&before_pause))["reason"],
        "stale_voice_generation"
    );
    assert!(f.inputs().is_empty());
    let current = voice_generation(&f.d.data.lock().unwrap().sessions["s"]);
    assert_eq!(
        f.d.submit_voice(Some("s"), "句の話", Some(&current))["accepted"],
        true
    );
    f.idle().await;
    assert_eq!(f.inputs().len(), 1);
    assert_eq!(f.inputs()[0]["mode"], "senryu");
    f.finish().await;
}

fn playing(f: &Fixture) {
    let mut d = f.d.data.lock().unwrap();
    let s = d.sessions.get_mut("s").unwrap();
    s.current_turn = "old".into();
    s.cancel = Some(watch::channel(false).0);
    d.rows
        .push_back(json!({"session_id":"s","turn_id":"old","playback_status":"started"}));
}
fn finish_playing(f: &Fixture) {
    let mut d = f.d.data.lock().unwrap();
    d.sessions.get_mut("s").unwrap().cancel = None;
    d.rows.iter_mut().find(|r| r["turn_id"] == "old").unwrap()["playback_status"] =
        "completed".into();
}
#[tokio::test]
async fn completed_senryu_queue_waits_for_same_verse_without_another_inference() {
    let f = Fixture::with_modes(1, 0.0, false, &[Mode::Normal, Mode::Senryu]);
    open_workshop(&f.d, "s");
    playing(&f);
    let a = f.d.submit(Some("s"), "上の意味は", "text");
    f.idle().await;
    {
        let mut d = f.d.data.lock().unwrap();
        d.sessions
            .get_mut("s")
            .unwrap()
            .haiku
            .workshop
            .as_mut()
            .unwrap()
            .pause(std::time::Instant::now());
    }
    finish_playing(&f);
    f.d.resume_reply_input("s");
    assert_eq!(f.d.data.lock().unwrap().sessions["s"].reply_queue.len(), 1);
    assert_eq!(f.inputs().len(), 1);
    {
        let mut d = f.d.data.lock().unwrap();
        d.sessions
            .get_mut("s")
            .unwrap()
            .haiku
            .workshop
            .as_mut()
            .unwrap()
            .resume(std::time::Instant::now());
    }
    f.d.resume_reply_input("s");
    f.idle().await;
    assert!(
        f.d.data.lock().unwrap().sessions["s"]
            .reply_queue
            .is_empty()
    );
    assert_eq!(f.inputs().len(), 1);
    assert_ne!(
        f.row(&a["turn_id"])["resolution"],
        "superseded_classifier_mode"
    );
    f.finish().await;
}
#[tokio::test]
async fn queued_normal_input_is_cancelled_when_workshop_opens() {
    let f = Fixture::with_modes(0, 0.0, false, &[Mode::Normal, Mode::Senryu]);
    playing(&f);
    let a = f.d.submit(Some("s"), "同じ話", "text");
    f.idle().await;
    open_workshop(&f.d, "s");
    finish_playing(&f);
    f.d.resume_reply_input("s");
    f.idle().await;
    assert!(
        f.d.data.lock().unwrap().sessions["s"]
            .reply_queue
            .is_empty()
    );
    assert_eq!(
        f.row(&a["turn_id"])["resolution"],
        "superseded_classifier_mode"
    );
    assert_eq!(f.inputs().len(), 1);
    assert_eq!(f.inputs()[0]["mode"], "normal");
    f.finish().await;
}

#[tokio::test]
async fn same_text_during_combat_does_not_deduplicate_into_old_senryu_input() {
    let f = Fixture::with_modes(1, 0.0, false, &[Mode::Senryu]);
    open_workshop(&f.d, "s");
    playing(&f);
    let before = f.d.submit(Some("s"), "上の意味は", "text");
    f.idle().await;
    {
        let mut d = f.d.data.lock().unwrap();
        d.sessions
            .get_mut("s")
            .unwrap()
            .haiku
            .workshop
            .as_mut()
            .unwrap()
            .pause(std::time::Instant::now());
    }
    let during = f.d.submit(Some("s"), "上の意味は", "text");
    assert_eq!(during["reason"], "classifier_model_unavailable");
    assert_ne!(during["deduplicated"], true);
    assert_eq!(during["classifier_mode"], "normal");
    assert_eq!(
        f.d.data.lock().unwrap().sessions["s"].reply_queue[0]
            .request
            .turn,
        before["turn_id"].as_str().unwrap()
    );
    assert_eq!(f.inputs().len(), 1);
    f.finish().await;
}
