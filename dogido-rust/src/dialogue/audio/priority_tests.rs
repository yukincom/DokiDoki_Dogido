//! 一時ディレクトリの模擬playerだけで最優先発話・取消・終了順を検証する。TTSは合成済みキャッシュを使い、スピーカーやサービスには接続しない。
use super::{Audio, DialogueConfig, cache_key};
use std::{
    fs,
    future::Future,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use tokio::sync::watch;

struct Fixture {
    root: PathBuf,
    audio: Arc<Audio>,
    config: DialogueConfig,
}

impl Fixture {
    fn new() -> Self {
        let root =
            std::env::temp_dir().join(format!("dogido-family-player-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        let player = root.join("player");
        fs::write(
            &player,
            r#"#!/bin/sh
cd "${1%/*}" || exit 1
# 先読みしたWAVの識別子を模擬プレイヤーが記録する。端末の音声APIは呼ばない。
name=$(tail -c 1 "$1") || exit 1
printf '%s\n' "$name" >> starts
while [ ! -f "$name.release" ]; do sleep 0.01; done
"#,
        )
        .unwrap();
        fs::set_permissions(&player, fs::Permissions::from_mode(0o700)).unwrap();
        let config = DialogueConfig {
            player,
            audio_dir: root.clone(),
            voicevox_url: "fixture://no-service".into(),
            ..DialogueConfig::default()
        };
        Self {
            root,
            config,
            audio: Arc::new(Audio::new().unwrap()),
        }
    }

    fn cache(&self, text: &str, tag: u8) {
        let mut bytes = b"RIFF\0\0\0\0WAVE".to_vec();
        bytes.push(tag);
        self.audio
            .cache
            .lock()
            .unwrap()
            .push_back((cache_key(&self.config, text), Arc::new(bytes)));
    }

    fn wav(&self, name: &str) -> PathBuf {
        let path = self.root.join(format!("{name}.wav"));
        fs::write(&path, name).unwrap();
        path
    }

    async fn started(&self, tag: &str) {
        bounded(async {
            while !self.starts().lines().any(|s| s == tag) {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
    }

    fn starts(&self) -> String {
        fs::read_to_string(self.root.join("starts")).unwrap_or_default()
    }

    fn release(&self, tag: &str) {
        fs::write(self.root.join(format!("{tag}.release")), b"").unwrap();
    }

    fn start_cue(
        &self,
        path: &Path,
        mut cancel: watch::Receiver<bool>,
    ) -> tokio::task::JoinHandle<anyhow::Result<()>> {
        let (audio, config, path) = (self.audio.clone(), self.config.clone(), path.to_owned());
        tokio::spawn(async move {
            audio
                .play_file_at_rate(&config, &path, &mut cancel, 0, 1.2, || {})
                .await
        })
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

async fn bounded<T>(future: impl Future<Output = T>) -> T {
    tokio::time::timeout(Duration::from_secs(5), future)
        .await
        .expect("模擬playerが時間内に応答")
}

// 倍率付き警告を再生中に二文の伝言が届く。従来のplayerを回収し、二文の間も次のcueを保留、最後にcueが再生される。
#[tokio::test]
async fn interrupts_current_cue_and_reserves_both_family_sentences() {
    let f = Fixture::new();
    f.cache("一。", b'A');
    f.cache("二。", b'B');
    let (_keep, cancel) = watch::channel(false);
    let first = f.start_cue(&f.wav("N"), cancel.clone());
    f.started("N").await;
    let (audio, config, mut family_cancel) = (f.audio.clone(), f.config.clone(), cancel.clone());
    let started = Arc::new(AtomicUsize::new(0));
    let count = started.clone();
    let family = tokio::spawn(async move {
        audio
            .speak_family(&config, "一。二。", &mut family_cancel, || {
                count.fetch_add(1, Ordering::SeqCst);
            })
            .await
    });
    f.started("A").await;
    assert_eq!(
        bounded(first).await.unwrap().unwrap_err().to_string(),
        "cancelled"
    );
    let warning = f.start_cue(&f.wav("W"), cancel);
    f.release("W");
    f.release("A");
    f.started("B").await;
    assert_eq!(f.starts(), "N\nA\nB\n");
    f.release("B");
    bounded(family).await.unwrap().unwrap();
    bounded(warning).await.unwrap().unwrap();
    assert_eq!(started.load(Ordering::SeqCst), 1);
    assert_eq!(f.starts(), "N\nA\nB\nW\n");
}

// 伝言中の古い警告と伝言を別々に取り消すと双方とも失敗で戻り、待機中の有効なcueだけが続く。取消時のspeaker所有権を検査する。
#[tokio::test]
async fn cancel_waiting_warning_and_family_releases_speaker() {
    let f = Fixture::new();
    f.cache("伝言", b'F');
    let (stop, mut family_cancel) = watch::channel(false);
    let (audio, config) = (f.audio.clone(), f.config.clone());
    let family = tokio::spawn(async move {
        audio
            .speak_family(&config, "伝言", &mut family_cancel, || {})
            .await
    });
    f.started("F").await;
    let (warning_stop, mut warning_cancel) = watch::channel(false);
    let obsolete_path = f.wav("X");
    let obsolete = f.audio.play_file_at_rate(
        &f.config,
        &obsolete_path,
        &mut warning_cancel,
        0,
        1.2,
        || panic!("伝言中の警告は起動しない"),
    );
    tokio::pin!(obsolete);
    assert!(futures_util::poll!(obsolete.as_mut()).is_pending());
    warning_stop.send(true).unwrap();
    assert_eq!(
        bounded(obsolete).await.unwrap_err().to_string(),
        "cancelled"
    );

    let (_keep, cancel) = watch::channel(false);
    let live = f.start_cue(&f.wav("L"), cancel);
    stop.send(true).unwrap();
    assert_eq!(
        bounded(family).await.unwrap().unwrap_err().to_string(),
        "cancelled"
    );
    f.release("L");
    bounded(live).await.unwrap().unwrap();
    assert_eq!(f.starts(), "F\nL\n");
}

// 合成エラーで原文を含むURLが返っても発話済みにならず、次の音声は再生できる。到達しないfixtureスキームで検査する。
#[tokio::test]
async fn synthesis_error_keeps_private_text_out_of_result_and_releases_speaker() {
    let f = Fixture::new();
    let (_keep, mut cancel) = watch::channel(false);
    let error = f
        .audio
        .speak_family(&f.config, "秘密の伝言", &mut cancel, || {
            panic!("合成失敗で開始しない")
        })
        .await
        .unwrap_err();
    assert_eq!(format!("{error:#}"), "family_audio_failed");
    f.release("W");
    bounded(f.start_cue(&f.wav("W"), cancel))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(f.starts(), "W\n");
}
