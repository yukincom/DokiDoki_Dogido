//! 見込み: 入力取消とworker停止を分ければ、旧結果を使わずモデルを使い回せる。
//! 合格: 同じPID・一回のロードで次入力へ進み、全体終了時は子が残らない。
//! 範囲: 模擬workerとRust共通受付・既存FIFO。実BERT精度・Minecraft実操作は含まない。
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
import fcntl, json, os, sys, time
from pathlib import Path
base = Path(__file__).parent
for line in sys.stdin:
    f = json.loads(line)
    r = {k:f[k] for k in ('schema_version','request_id','manifest_sha256','mode')}
    labels = domains[f['mode']]
    if f['op'] == 'load':
        with base.joinpath('loads.jsonl').open('a') as out: out.write(json.dumps({'pid':os.getpid(),'mode':f['mode']})+'\n')
        while base.joinpath('block-load').exists(): time.sleep(0.005)
        r.update(ready=True, labels=labels, limits={'max_history_utterances':0,'max_length':128,'truncation':False})
    else:
        lock = base.joinpath('inference.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with base.joinpath('inputs.jsonl').open('a') as out: out.write(json.dumps({**f,'pid':os.getpid()},ensure_ascii=False)+'\n')
        while base.joinpath('block-inference').exists(): time.sleep(0.005)
        time.sleep(DELAY)
        if FAIL or base.joinpath('fail-inference').exists(): r['error'] = 'deliberate test failure'
        else: r['prediction'] = {'label':labels[LABEL], 'probabilities':[int(i==LABEL) for i in range(len(labels))],'token_count':30,'inference_ms':1}
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    encoded = json.dumps(r)
    if f['op'] == 'classify' and base.joinpath('block-response').exists():
        mid = len(encoded)//2
        print(encoded[:mid],end='',flush=True)
        base.joinpath('response-started').write_text('yes\n')
        while base.joinpath('block-response').exists(): time.sleep(0.005)
        print(encoded[mid:],flush=True)
    else: print(encoded,flush=True)
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
        self.records("inputs.jsonl")
    }
    fn records(&self, name: &str) -> Vec<Value> {
        fs::read_to_string(self.dir.join(name))
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
        for row in self.records("loads.jsonl") {
            let pid = row["pid"].as_i64().unwrap() as i32;
            assert_eq!(unsafe { libc::kill(pid, 0) }, -1, "worker remains: {pid}");
            assert_eq!(
                std::io::Error::last_os_error().raw_os_error(),
                Some(libc::ESRCH)
            );
        }
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

// 見込み: 新入力は受付済み旧入力だけを取り消し、開始済み推論の完了後に同じworkerを使う。
// 合格: 旧行は取消・転送なし、次の行だけ支援へ進み、両入力のPIDが同じでロードは1回。
// 範囲: 模擬推論と実共通受付・支援コマンド作成。実モデルやMinecraftは含まない。
#[tokio::test]
async fn newer_input_cancels_pending_work_without_inheriting_preceding_request() {
    let f = Fixture::new(2, 0.2, false);
    fs::write(f.dir.join("block-inference"), "").unwrap();
    let first = format!("  {}\n持ち替えて\t", "全文".repeat(90));
    let a = f.d.submit(Some("s"), &first, "text");
    wait_for_file(&f.dir.join("inputs.jsonl")).await;
    let b = f.d.submit(Some("s"), "それに替えて", "text");
    assert_eq!(b["accepted"], true);
    fs::remove_file(f.dir.join("block-inference")).unwrap();
    f.idle().await;
    assert_eq!(f.row(&a["turn_id"])["playback_status"], "cancelled");
    assert_eq!(
        f.row(&b["turn_id"])["forwarded_input"]["reason"],
        "assist_input"
    );
    let inputs = f.inputs();
    assert_eq!(inputs.len(), 2);
    assert_eq!(inputs[0]["pid"], inputs[1]["pid"]);
    assert_eq!(f.records("loads.jsonl").len(), 1);
    assert!(f.row(&a["turn_id"])["forwarded_input"].is_null());
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

// 見込み: 取消は応答の読取futureを破棄しないので、JSONの途中でもworkerを再利用できる。
// 合格: 前半受信後に取り消した旧入力は転送なし、次入力は同じPID・1ロードで分類成功。
// 範囲: 分割JSONを返す模擬workerと共通受付。実モデル推論は含まない。
#[tokio::test]
async fn cancellation_during_partial_response_keeps_worker_and_stream() {
    let f = Fixture::new(2, 0.0, false);
    fs::write(f.dir.join("block-response"), "").unwrap();
    let a = f.d.submit(Some("s"), "先の依頼", "text");
    wait_for_file(&f.dir.join("response-started")).await;
    let b = f.d.submit(Some("s"), "次の依頼", "text");
    assert_eq!(b["accepted"], true);
    fs::remove_file(f.dir.join("block-response")).unwrap();
    f.idle().await;
    assert_eq!(f.row(&a["turn_id"])["playback_status"], "cancelled");
    assert!(f.row(&a["turn_id"])["forwarded_input"].is_null());
    assert_eq!(
        f.row(&b["turn_id"])["forwarded_input"]["reason"],
        "assist_input"
    );
    let inputs = f.inputs();
    assert_eq!(inputs.len(), 2);
    assert_eq!(inputs[0]["pid"], inputs[1]["pid"]);
    assert_eq!(f.records("loads.jsonl").len(), 1);
    f.finish().await;
}

// 見込み: 再ロード中の入力取消はモデルを捨てず、完了後は最新入力だけを分類する。
// 合格: 旧入力は転送なし、ロードは1回で、workerへ届く分類入力は次の依頼の1件だけ。
// 範囲: 模擬ロード待ちと共通受付。実モデルのロード時間は含まない。
#[tokio::test]
async fn cancellation_during_load_retains_model_without_classifying_old_input() {
    let f = Fixture::new(2, 0.0, false);
    fs::write(f.dir.join("block-load"), "").unwrap();
    let a = f.d.submit(Some("s"), "先の依頼", "text");
    wait_for_file(&f.dir.join("loads.jsonl")).await;
    let b = f.d.submit(Some("s"), "次の依頼", "text");
    assert_eq!(b["accepted"], true);
    fs::remove_file(f.dir.join("block-load")).unwrap();
    f.idle().await;
    assert!(f.row(&a["turn_id"])["forwarded_input"].is_null());
    assert_eq!(
        f.row(&b["turn_id"])["forwarded_input"]["reason"],
        "assist_input"
    );
    assert_eq!(f.records("loads.jsonl").len(), 1);
    let inputs = f.inputs();
    assert_eq!(inputs.len(), 1);
    assert_eq!(
        inputs[0]["text"],
        "履歴:\n（なし）\n現在のユーザー発話:\n次の依頼"
    );
    f.finish().await;
}

// 見込み: 推論順番待ち中の取消はworkerへ送らず、進行中の推論と最新入力だけを完了する。
// 合格: 3入力中の前2件は転送なし、実分類は先頭と最新の2件、ロード1回で最後だけ支援へ。
// 範囲: 模擬worker・共通排他・入力世代。実モデルやゲームの実操作は含まない。
#[tokio::test]
async fn cancelled_lock_waiter_never_reaches_resident_worker() {
    let f = Fixture::new(2, 0.0, false);
    fs::write(f.dir.join("block-inference"), "").unwrap();
    let a = f.d.submit(Some("s"), "先の依頼", "text");
    wait_for_file(&f.dir.join("inputs.jsonl")).await;
    let b = f.d.submit(Some("s"), "順番待ちの依頼", "text");
    tokio::task::yield_now().await;
    let c = f.d.submit(Some("s"), "最新の依頼", "text");
    fs::remove_file(f.dir.join("block-inference")).unwrap();
    f.idle().await;
    for turn in [&a["turn_id"], &b["turn_id"]] {
        assert_eq!(f.row(turn)["playback_status"], "cancelled");
        assert!(f.row(turn)["forwarded_input"].is_null());
    }
    assert_eq!(
        f.row(&c["turn_id"])["forwarded_input"]["reason"],
        "assist_input"
    );
    let inputs = f.inputs();
    assert_eq!(inputs.len(), 2);
    assert_eq!(
        inputs[1]["text"],
        "履歴:\n（なし）\n現在のユーザー発話:\n最新の依頼"
    );
    assert_eq!(inputs[0]["pid"], inputs[1]["pid"]);
    assert_eq!(f.records("loads.jsonl").len(), 1);
    f.finish().await;
}

// 見込み: 入力取消後でも全体終了は別の停止信号としてworkerへ届く。
// 合格: ロード中/推論中の双方で2秒以内にshutdownが完了し、全PIDが消える。
// 範囲: 停止するまで待ち続ける模擬workerと本体shutdown。実BERT・OS終了は含まない。
#[tokio::test]
async fn shutdown_reaps_workers_even_after_input_cancellation() {
    for (block, started) in [
        ("block-load", "loads.jsonl"),
        ("block-inference", "inputs.jsonl"),
    ] {
        let f = Fixture::new(2, 0.0, false);
        fs::write(f.dir.join(block), "").unwrap();
        let a = f.d.submit(Some("s"), "先の依頼", "text");
        wait_for_file(&f.dir.join(started)).await;
        f.d.interrupt("s");
        tokio::time::timeout(Duration::from_secs(2), f.d.shutdown())
            .await
            .unwrap();
        assert!(f.row(&a["turn_id"])["forwarded_input"].is_null());
        f.finish().await;
    }
}

// 見込み: 通常取消と異常は分け、既存の推論失敗/timeoutではworkerを回収して次回再作成する。
// 合格: 失敗行は転送なし、原因解除後は別PID・2回目のロードで分類成功する。
// 範囲: 模擬異常・Rustの既存timeoutと復旧。実BERTの障害原因は含まない。
#[tokio::test]
async fn worker_failure_or_timeout_still_restarts_on_next_input() {
    for marker in ["fail-inference", "block-inference"] {
        let mut f = Fixture::new(2, 0.0, false);
        Arc::get_mut(&mut f.d)
            .unwrap()
            .config
            .request_classifier
            .timeout_ms = 200;
        f.d.prepare_request_classifier().await.unwrap();
        fs::write(f.dir.join(marker), "").unwrap();
        let a = f.d.submit(Some("s"), "失敗する依頼", "text");
        f.idle().await;
        assert_eq!(f.row(&a["turn_id"])["playback_status"], "failed");
        assert!(f.row(&a["turn_id"])["forwarded_input"].is_null());
        fs::remove_file(f.dir.join(marker)).unwrap();
        let b = f.d.submit(Some("s"), "次の依頼", "text");
        f.idle().await;
        assert_eq!(
            f.row(&b["turn_id"])["forwarded_input"]["reason"],
            "assist_input"
        );
        let inputs = f.inputs();
        assert_eq!(inputs.len(), 2);
        assert_ne!(inputs[0]["pid"], inputs[1]["pid"]);
        assert_eq!(f.records("loads.jsonl").len(), 2);
        f.finish().await;
    }
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

// 見込み: 取消後のworker失敗でも、既存の受付保留・支援へ古い入力を戻さない。
// 合格: 通常失敗はfailed、取消済みはcancelledで、FIFOと支援コマンドが空。
// 範囲: 模擬推論失敗と共通受付。モデル精度は対象外。
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
            fs::remove_file(f.dir.join("block-inference")).unwrap();
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

// 見込み: 通常の再生中も分類と操作受付は進み、元の音声は取り消されない。
// 合格: 旧行はstarted・取消なしのまま分類1回、剣命令1件または馬候補不可へ到達し、再開処理でも重複しない。
// 範囲: 模擬BERTと共通受付・操作作成。実音声の直列再生とMinecraft実操作は含まない。
#[tokio::test]
async fn playing_reply_classifies_and_dispatches_each_operation_once_without_waiting() {
    for label in [2, 3] {
        let f = Fixture::new(label, 0.0, false);
        f.d.prepare_request_classifier().await.unwrap();
        let rx = playing(&f);
        let accepted = f.d.submit(Some("s"), "それをお願い", "voice");
        assert_eq!(accepted["reason"], "request_classification");
        f.idle().await;
        assert!(!*rx.borrow());
        assert_eq!(f.row(&json!("old"))["playback_status"], "started");
        assert_eq!(f.inputs().len(), 1);
        let row = f.row(&accepted["turn_id"]);
        let response = &row["forwarded_input"];
        if label == 2 {
            assert_eq!(response["reason"], "assist_input", "{row}");
        } else {
            // 模擬sessionでは馬の候補取得は使えない。既存経路での拒否を終端として保持する。
            assert_eq!(response["reason"], "horse_selection_unavailable", "{row}");
        }
        let commands = f.d.data.lock().unwrap().sessions["s"]
            .assist
            .commands(chrono::Utc::now());
        assert_eq!(commands.len(), usize::from(label == 2));
        assert!(f.d.update("s", "old", 0, crate::playback::Status::Completed, None));
        assert_eq!(f.row(&json!("old"))["playback_status"], "completed");
        f.d.resume_reply_input("s");
        f.idle().await;
        assert!(!*rx.borrow());
        assert_eq!(f.row(&accepted["turn_id"])["forwarded_input"], *response);
        assert_eq!(
            f.d.data.lock().unwrap().sessions["s"]
                .assist
                .commands(chrono::Utc::now()),
            commands
        );
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

// 見込み: 受付境界が変わってもモデルは残し、旧結果を別の句・版へ移さない。
// 合格: 5種の境界で旧結果の転送なし、次入力は分類成功、通常/川柳のロードは各1回。
// 範囲: 模擬workerと共通受付・workshop境界。実モデル・発話は含まない。
#[tokio::test]
async fn delayed_results_never_cross_workshop_start_end_pause_aba_or_revision() {
    for boundary in ["start", "end", "pause_aba", "revision", "replace"] {
        let f = Fixture::with_modes(0, 0.0, false, &[Mode::Normal, Mode::Senryu]);
        if boundary != "start" {
            open_workshop(&f.d, "s");
        }
        f.d.prepare_request_classifier().await.unwrap();
        let loads = f.records("loads.jsonl");
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
        fs::remove_file(f.dir.join("block-inference")).unwrap();
        f.idle().await;
        assert!(f.row(&a["turn_id"])["forwarded_input"].is_null());
        assert_eq!(f.inputs().len(), 1);
        let b = f.d.submit(Some("s"), "次の話", "text");
        f.idle().await;
        assert!(f.row(&b["turn_id"])["request_classification"].is_object());
        assert_eq!(f.records("loads.jsonl"), loads);
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

fn playing(f: &Fixture) -> watch::Receiver<bool> {
    let (tx, rx) = watch::channel(false);
    let mut d = f.d.data.lock().unwrap();
    let s = d.sessions.get_mut("s").unwrap();
    s.current_turn = "old".into();
    s.cancel = Some(tx);
    d.rows
        .push_back(json!({"session_id":"s","turn_id":"old","epoch":0,"playback_status":"started"}));
    rx
}
// 通常再生では入力を保留しない。戦闘で保持した川柳workerの再開待ちだけを模擬する。
fn retained_workshop_reply(f: &Fixture) {
    let _rx = playing(f);
    let mut d = f.d.data.lock().unwrap();
    let s = d.sessions.get_mut("s").unwrap();
    s.workshop_reply = Some(super::super::workshop_reply::State::new(
        "old",
        s.epoch,
        &s.haiku.workshop.as_ref().unwrap().hud_id,
    ));
    assert!(f.d.pause_workshop_reply(&mut d, "s"));
}
fn finish_playing(f: &Fixture) {
    let mut d = f.d.data.lock().unwrap();
    let s = d.sessions.get_mut("s").unwrap();
    s.cancel = None;
    s.workshop_reply = None;
    d.rows.iter_mut().find(|r| r["turn_id"] == "old").unwrap()["playback_status"] =
        "completed".into();
}
// 見込み: 戦闘で保持した川柳返答への追加入力は、分類済みの同一句だけ再開へ持ち越せる。
// 合格: 再中断中は同じ入力が1件残り、同一句の再開後は排出され、川柳分類は計1回。
// 範囲: 模擬BERT・保留worker状態と既存FIFO。実戦闘判定・音声再開は含まない。
#[tokio::test]
async fn completed_senryu_queue_waits_for_same_verse_without_another_inference() {
    let f = Fixture::with_modes(1, 0.0, false, &[Mode::Normal, Mode::Senryu]);
    open_workshop(&f.d, "s");
    retained_workshop_reply(&f);
    let a = f.d.submit(Some("s"), "上の意味は", "text");
    assert_eq!(a["reason"], "reply_input_routing");
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
// 見込み: 戦闘警告中に分類した通常入力を、後から開いた川柳の受付へ付け替えない。
// 合格: 警告中は通常分類済み1件を保持し、句を開くと取消・転送なしとなり再分類しない。
// 範囲: 模擬BERT・警告状態と共通受付FIFO。実戦闘の開始・解除条件は含まない。
#[tokio::test]
async fn queued_normal_input_is_cancelled_when_workshop_opens() {
    let f = Fixture::with_modes(0, 0.0, false, &[Mode::Normal, Mode::Senryu]);
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
    let a = f.d.submit(Some("s"), "同じ話", "text");
    assert_eq!(a["reason"], "reply_input_routing");
    f.idle().await;
    assert_eq!(f.d.data.lock().unwrap().sessions["s"].reply_queue.len(), 1);
    open_workshop(&f.d, "s");
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
    assert!(f.row(&a["turn_id"])["forwarded_input"].is_null());
    assert_eq!(f.inputs().len(), 1);
    assert_eq!(f.inputs()[0]["mode"], "normal");
    f.finish().await;
}

// 見込み: 同じ原文でも、川柳の受付から戦闘中の通常受付へ変われば別入力として扱う。
// 合格: 旧川柳入力は保留1件を維持し、新入力は通常モデル未設定で拒否され、重複扱い・追加推論なし。
// 範囲: 模擬BERT・保留worker状態と受付境界。実戦闘の判定・モデル精度は含まない。
#[tokio::test]
async fn same_text_during_combat_does_not_deduplicate_into_old_senryu_input() {
    let f = Fixture::with_modes(1, 0.0, false, &[Mode::Senryu]);
    open_workshop(&f.d, "s");
    retained_workshop_reply(&f);
    let before = f.d.submit(Some("s"), "上の意味は", "text");
    assert_eq!(before["reason"], "reply_input_routing");
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
