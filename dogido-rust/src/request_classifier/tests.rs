//! 原文整形と、模擬workerによる通信失敗・取消後の回収を検査する。
//! モデルの分類精度やゲームでの実行を証明するテストではない。
use super::*;
use std::{fs, path::PathBuf};

fn input() -> Input {
    Input {
        text: "どっち？".into(),
        context: vec![],
    }
}

#[test]
fn renders_training_format_without_shortening_or_normalizing() {
    let mut value = input();
    assert_eq!(
        value.render(10).unwrap(),
        "履歴:\n（なし）\n現在のユーザー発話:\nどっち？"
    );
    let long = format!("  {}\n続き\t", "原文".repeat(100));
    value.context = vec![
        Utterance {
            role: Role::User,
            text: long.clone(),
        },
        Utterance {
            role: Role::Assistant,
            text: "敵がおる！".into(),
        },
    ];
    assert_eq!(
        value.render(10).unwrap(),
        format!("履歴:\nユーザー: {long}\nドギド: 敵がおる！\n現在のユーザー発話:\nどっち？")
    );
    assert!(value.render(1).is_err());
    assert!(
        serde_json::from_value::<Input>(
            json!({"text":"今", "context":[{"role":"event", "text":"未発話"}]})
        )
        .is_err()
    );
}

#[test]
fn rejects_invalid_predictions_instead_of_mapping_to_not_request() {
    let valid = || Prediction {
        label: Label::EnemyInformation,
        probabilities: [0.0, 1.0, 0.0, 0.0, 0.0],
        token_count: 20,
        inference_ms: 1.0,
    };
    assert!(valid().validate(256).is_ok());
    let mut p = valid();
    p.label = Label::NotRequest;
    assert!(p.validate(256).is_err());
    let mut p = valid();
    p.probabilities[0] = f64::NAN;
    assert!(p.validate(256).is_err());
    let mut p = valid();
    p.probabilities[1] = 0.4;
    assert!(p.validate(256).is_err());
    let mut p = valid();
    p.token_count = 257;
    assert!(p.validate(256).is_err());
    let mut p = valid();
    p.inference_ms = -1.0;
    assert!(p.validate(256).is_err());
}

struct Fixture {
    dir: PathBuf,
}
impl Fixture {
    fn new(mode: &str) -> Self {
        let dir =
            std::env::temp_dir().join(format!("dogido-classifier-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&dir).unwrap();
        fs::write(dir.join("manifest.json"), r#"{"schema_version":1,"path_base":"repository_root","evaluated_input":{"max_history_utterances":10,"max_length":256,"truncation":false}}"#).unwrap();
        let script = r#"
import json, os, sys, time
from pathlib import Path
base = Path(__file__).parent
base.joinpath('pid').write_text(str(os.getpid()))
for line in sys.stdin:
    f = json.loads(line)
    r = {k:f[k] for k in ('schema_version','request_id','manifest_sha256')}
    if f['op'] == 'load':
        if MODE == 'load_hang': time.sleep(60)
        r.update(ready=True, labels=['not_request','enemy_information','equip_sword','recall_mount','other_request'], limits={'max_history_utterances':10,'max_length':256,'truncation':False})
        if MODE == 'bad_load': r['limits']['max_length'] = 512
    else:
        base.joinpath('requested').write_text('yes')
        if MODE == 'hang': time.sleep(60)
        r['prediction'] = {'label':'enemy_information','probabilities':[0,1,0,0,0],'token_count':20,'inference_ms':1}
        if MODE == 'stale_id': r['request_id'] = 'old'
        if MODE == 'wrong_model': r['manifest_sha256'] = 'other'
        if MODE == 'bad_probabilities': r['prediction']['probabilities'] = [0,0,0,0,0]
        if MODE == 'unknown_label': r['prediction']['label'] = 'unknown'
        if MODE == 'error': r['error'] = 'inference failure'
    print(json.dumps(r), flush=True)
"#;
        fs::write(dir.join("worker.py"), format!("MODE = {mode:?}\n{script}")).unwrap();
        Self { dir }
    }
    async fn start(
        &self,
        cancel: &mut watch::Receiver<bool>,
        timeout: Duration,
    ) -> Result<Classifier> {
        Classifier::start(
            Path::new("/usr/bin/python3"),
            &self.dir.join("worker.py"),
            &self.dir.join("manifest.json"),
            &self.dir,
            timeout,
            cancel,
        )
        .await
    }
    fn assert_reaped(&self) {
        if self.dir.join("pid").exists() {
            let pid: i32 = fs::read_to_string(self.dir.join("pid"))
                .unwrap()
                .parse()
                .unwrap();
            // signal=0は送信せず存在だけ確認する。finishがwaitを終えていることを検査する。
            assert_eq!(unsafe { libc::kill(pid, 0) }, -1, "worker still exists");
            assert_eq!(
                std::io::Error::last_os_error().raw_os_error(),
                Some(libc::ESRCH)
            );
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

#[tokio::test]
async fn round_trip_and_clean_close() {
    let fixture = Fixture::new("valid");
    let (_tx, mut cancel) = watch::channel(false);
    let mut model = fixture
        .start(&mut cancel, Duration::from_secs(3))
        .await
        .unwrap();
    for _ in 0..2 {
        assert_eq!(
            model
                .classify(&input(), Duration::from_secs(3), &mut cancel)
                .await
                .unwrap()
                .label,
            Label::EnemyInformation
        );
    }
    model.close().await.unwrap();
    fixture.assert_reaped();
}

#[tokio::test]
async fn invalid_replies_and_timeouts_close_and_reap_worker() {
    for mode in [
        "stale_id",
        "wrong_model",
        "bad_probabilities",
        "unknown_label",
        "error",
        "hang",
    ] {
        let fixture = Fixture::new(mode);
        let (_tx, mut cancel) = watch::channel(false);
        let mut model = fixture
            .start(&mut cancel, Duration::from_secs(3))
            .await
            .unwrap();
        assert!(
            model
                .classify(&input(), Duration::from_millis(100), &mut cancel)
                .await
                .is_err(),
            "{mode}"
        );
        assert!(model.helper.is_none());
        fixture.assert_reaped();
        assert!(
            model
                .classify(&input(), Duration::from_secs(1), &mut cancel)
                .await
                .is_err()
        );
    }
}

#[tokio::test]
async fn load_contract_failure_and_load_timeout_reap_worker() {
    for mode in ["bad_load", "load_hang"] {
        let fixture = Fixture::new(mode);
        let (_tx, mut cancel) = watch::channel(false);
        assert!(
            fixture
                .start(&mut cancel, Duration::from_secs(1))
                .await
                .is_err()
        );
        fixture.assert_reaped();
    }
}

#[tokio::test]
async fn cancellation_during_inference_reaps_worker() {
    let fixture = Fixture::new("hang");
    let (tx, mut cancel) = watch::channel(false);
    let mut model = fixture
        .start(&mut cancel, Duration::from_secs(3))
        .await
        .unwrap();
    let marker = fixture.dir.join("requested");
    let task = tokio::spawn(async move {
        tokio::time::timeout(Duration::from_secs(3), async {
            while !marker.exists() {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
        tx.send(true).unwrap();
    });
    assert!(
        model
            .classify(&input(), Duration::from_secs(5), &mut cancel)
            .await
            .is_err()
    );
    task.await.unwrap();
    assert!(model.helper.is_none());
    fixture.assert_reaped();
}
