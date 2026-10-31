//! パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
//! 通常会話・環境反応・想起の音声用読みを作る。表示原文は変更しない。
//! 共通の明示訂正を毎回読み、UniDicの前に反映する。保存・再生は呼出側の担当。
use super::{DialogueConfig, bridge};
use crate::{
    python_worker::Helper,
    tts_reading::{self, Step, tokens},
};
use anyhow::{Context, Result, ensure};
use serde_json::json;
use tokio::{sync::watch, time::Instant};

fn live(cancel: &watch::Receiver<bool>, deadline: Instant) -> Result<()> {
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    ensure!(Instant::now() < deadline, "TTS reading timed out");
    Ok(())
}
/// Dialogueが所有する辞書。返答の取消では処理中の応答を読み切って破棄し、
/// 次の返答も同じPython/UniDicを使う。通信失敗時の回収は既存Helperに任せる。
#[derive(Default)]
pub(super) struct Dictionary {
    worker: std::sync::Mutex<Option<DictionaryWorker>>,
}
struct DictionaryRequest {
    frame: serde_json::Value,
    request_id: String,
    reply: tokio::sync::oneshot::Sender<Result<Option<String>>>,
}
struct DictionaryWorker {
    requests: Option<tokio::sync::mpsc::UnboundedSender<DictionaryRequest>>,
    task: Option<tokio::task::JoinHandle<()>>,
    shutdown: Option<tokio::sync::oneshot::Sender<()>>,
}
impl Drop for DictionaryWorker {
    fn drop(&mut self) {
        // 明示shutdown以外で所有者が落ちても、Helperのkill_on_dropまで進める。
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}
impl Dictionary {
    async fn exchange(&self, config: &DialogueConfig, source: &str) -> Result<Option<String>> {
        let request_id = uuid::Uuid::new_v4().to_string();
        tracing::info!(event = "tts_unidic_requested", request_id);
        let (reply, response) = tokio::sync::oneshot::channel();
        let request = DictionaryRequest {
            frame: json!({"schema_version":1,"request_id":request_id,"text":source}),
            request_id,
            reply,
        };
        {
            let mut worker = self.worker.lock().unwrap();
            if worker.is_none() {
                let python = config.python.clone();
                let script = config
                    .helper
                    .parent()
                    .context("dictionary helper directory")?
                    .join("tts_unidic_adapter.py");
                let (requests, mut incoming) =
                    tokio::sync::mpsc::unbounded_channel::<DictionaryRequest>();
                let (shutdown, mut stopped) = tokio::sync::oneshot::channel();
                let task = tokio::spawn(async move {
                    let mut helper = None;
                    let mut abort = false;
                    loop {
                        let request = tokio::select! { biased;
                            _ = &mut stopped => { abort = true; break; },
                            request = incoming.recv() => match request { Some(request) => request, None => break },
                        };
                        if request.reply.is_closed() {
                            continue;
                        }
                        let operation = async {
                            if helper.is_none() {
                                helper = Some(Helper::start(&python, &script)?);
                            }
                            let response = helper.as_mut().unwrap().exchange(request.frame).await?;
                            tokens::decode(response, &request.request_id)
                        };
                        let result = tokio::select! { biased;
                            _ = &mut stopped => { abort = true; break; },
                            result = operation => result,
                        };
                        if result.is_err() {
                            if let Some(child) = helper.take() {
                                let _ = child.finish(true).await;
                            }
                        }
                        let _ = request.reply.send(result);
                    }
                    if let Some(child) = helper {
                        let _ = child.finish(abort).await;
                    }
                });
                *worker = Some(DictionaryWorker {
                    requests: Some(requests),
                    task: Some(task),
                    shutdown: Some(shutdown),
                });
            }
            worker
                .as_ref()
                .unwrap()
                .requests
                .as_ref()
                .unwrap()
                .send(request)
                .map_err(|_| anyhow::anyhow!("dictionary worker ended"))?;
        }
        response.await.context("dictionary worker ended")?
    }

    pub(super) async fn close(&self) {
        let worker = self.worker.lock().unwrap().take();
        if let Some(mut worker) = worker {
            worker.requests.take();
            if let Some(shutdown) = worker.shutdown.take() {
                let _ = shutdown.send(());
            }
            if let Some(task) = worker.task.take() {
                let _ = task.await;
            }
        }
    }
}

/// 読み訂正は返答ごとに読み直す。辞書の寿命はDialogueの所有者が管理する。
pub(super) async fn read(
    config: &DialogueConfig,
    dictionary: &Dictionary,
    text: &str,
    cancel: &mut watch::Receiver<bool>,
    deadline: Instant,
) -> Result<String> {
    let corrections = super::reading_runtime::load_overlay(config).await?;
    Reader::new(dictionary, corrections)
        .read(config, text, cancel, deadline)
        .await
}

/// 一返答の文間では同じ読み訂正を使い、辞書は返答をまたいで共有する。
pub(super) struct Reader<'a> {
    corrections: Vec<serde_json::Value>,
    dictionary: &'a Dictionary,
}
impl<'a> Reader<'a> {
    pub(super) fn new(dictionary: &'a Dictionary, corrections: Vec<serde_json::Value>) -> Self {
        Self {
            corrections,
            dictionary,
        }
    }
    pub(super) async fn read(
        &mut self,
        config: &DialogueConfig,
        text: &str,
        cancel: &mut watch::Receiver<bool>,
        deadline: Instant,
    ) -> Result<String> {
        live(cancel, deadline)?;
        let corrected = tts_reading::apply_corrections(text, &self.corrections);
        let source = match tts_reading::prepare(&corrected, Some(&config.reading_engine), None) {
            Step::Ready(spoken) => return Ok(spoken),
            Step::NeedsUnidic { source } => source,
        };
        let body = self.dictionary.exchange(config, &source);
        let dictionary = tokio::select! {biased;
            _=bridge::cancelled(cancel)=>Err(anyhow::anyhow!("cancelled")),
            result=tokio::time::timeout_at(deadline,body)=>result.unwrap_or_else(|_|Err(anyhow::anyhow!("TTS reading timed out"))),
        }?;
        live(cancel, deadline)?;
        Ok(tts_reading::finish(&source, dictionary.as_deref()))
    }
}

// 模擬辞書で返答間の再利用・取消後の応答分離・終了回収を確認する。実UniDicの性能と実音声は別確認。
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use std::time::Duration;

    #[tokio::test]
    async fn saved_reading_is_reloaded_without_restarting_and_display_stays_original() {
        let dir =
            std::env::temp_dir().join(format!("dogido-reading-reload-{}", uuid::Uuid::new_v4()));
        let mut config = DialogueConfig {
            reading_engine: "off".into(),
            ..Default::default()
        };
        config.haiku.memory_dir = dir.clone();
        config.haiku.memory_enabled = true;
        let store = crate::haiku_record::MemoryStore::new(&dir);
        let display = "草地で待とう。";
        let (tx, mut cancel) = watch::channel(false);
        let first = store
            .edit_catalog_reading(
                &crate::catalog_editor::Edit {
                    surface: "草地".into(),
                    reading: "そうち".into(),
                    entry_id: None,
                    wrong_reading: None,
                    expected_id: None,
                }
                .validate()
                .unwrap(),
                chrono::Utc::now(),
            )
            .unwrap();
        let before = read(
            &config,
            &Dictionary::default(),
            display,
            &mut cancel,
            Instant::now() + Duration::from_secs(5),
        )
        .await
        .unwrap();
        assert!(before.starts_with("そうち"));
        store
            .edit_catalog_reading(
                &crate::catalog_editor::Edit {
                    surface: "草地".into(),
                    reading: "くさち".into(),
                    entry_id: None,
                    wrong_reading: None,
                    expected_id: first["id"].as_str().map(str::to_owned),
                }
                .validate()
                .unwrap(),
                chrono::Utc::now(),
            )
            .unwrap();
        let after = read(
            &config,
            &Dictionary::default(),
            display,
            &mut cancel,
            Instant::now() + Duration::from_secs(5),
        )
        .await
        .unwrap();
        assert!(after.starts_with("くさち"));
        assert_eq!(display, "草地で待とう。");
        drop(tx);
        std::fs::remove_dir_all(dir).unwrap();
    }

    // 見込み: Dialogue所有の辞書なら別々の返答でも初期化は一回。
    // 合格: 三返答でPID一件・要求三件、shutdown後はPID消滅。辞書応答は模擬し、実UniDic速度・音声は対象外。
    #[cfg(unix)]
    #[tokio::test]
    async fn separate_replies_reuse_one_dictionary_until_dialogue_shutdown() {
        let dir = std::env::temp_dir().join(format!("dogido-reader-turn-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let script = format!(
            r#"echo $$ >> '{root}/pids'
while read -r line; do
  request_id=${{line#*\"request_id\":\"}}
  request_id=${{request_id%%\"*}}
  echo request >> '{root}/requests'
  printf '{{"schema_version":1,"request_id":"%s","status":"ok","tokens":[{{"surface":"猫","goshu":"和","pos1":"名詞","kana":"ネコ","pron":"ネコ"}}]}}\n' "$request_id"
done
"#,
            root = dir.display()
        );
        std::fs::write(dir.join("tts_unidic_adapter.py"), script).unwrap();
        std::fs::write(dir.join("unused.py"), "").unwrap();
        let mut config = DialogueConfig {
            python: "/bin/sh".into(),
            helper: dir.join("unused.py"),
            ..Default::default()
        };
        let (_owner, mut cancel) = watch::channel(false);
        config.haiku.memory_enabled = false;
        let dialogue = super::super::Dialogue::new(config).unwrap();
        for _ in 0..3 {
            let mut reader = Reader::new(&dialogue.reading_dictionary, vec![]);
            assert_eq!(
                reader
                    .read(
                        &dialogue.config,
                        "猫",
                        &mut cancel,
                        Instant::now() + Duration::from_secs(3)
                    )
                    .await
                    .unwrap(),
                "ねこ"
            );
        }
        dialogue.shutdown().await;
        assert_eq!(
            std::fs::read_to_string(dir.join("pids"))
                .unwrap()
                .lines()
                .count(),
            1
        );
        assert_eq!(
            std::fs::read_to_string(dir.join("requests"))
                .unwrap()
                .lines()
                .count(),
            3
        );
        let pid: i32 = std::fs::read_to_string(dir.join("pids"))
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
        std::fs::remove_dir_all(dir).unwrap();
    }

    // 見込み: 返答取消で受信待ちだけを離れ、辞書側が旧応答を読み切れば次の返答と混ざらない。
    // 合格: 取消は待たずに戻り、同じPIDの次要求は「いぬ」を返す。closeは子を回収。ゲート付き模擬辞書で確認。
    #[cfg(unix)]
    #[tokio::test]
    async fn cancellation_keeps_dictionary_and_discards_its_late_reply() {
        let dir =
            std::env::temp_dir().join(format!("dogido-dictionary-cancel-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("tts_unidic_adapter.py"), format!(r#"echo $$ >> '{root}/pids'
count=0
while read -r line; do
  count=$((count + 1))
  request_id=${{line#*\"request_id\":\"}}
  request_id=${{request_id%%\"*}}
  echo request >> '{root}/requests'
  if [ "$count" = 1 ]; then
    while [ ! -f '{root}/release' ]; do sleep 0.01; done
    surface=猫
    kana=ネコ
  else
    surface=犬
    kana=イヌ
  fi
  printf '{{"schema_version":1,"request_id":"%s","status":"ok","tokens":[{{"surface":"%s","goshu":"和","pos1":"名詞","kana":"%s","pron":"%s"}}]}}\n' "$request_id" "$surface" "$kana" "$kana"
done
"#, root=dir.display())).unwrap();
        let config = DialogueConfig {
            python: "/bin/sh".into(),
            helper: dir.join("unused.py"),
            ..Default::default()
        };
        let dictionary = std::sync::Arc::new(Dictionary::default());
        let first_dictionary = dictionary.clone();
        let first_config = config.clone();
        let (stop, mut cancel) = watch::channel(false);
        let first = tokio::spawn(async move {
            Reader::new(&first_dictionary, vec![])
                .read(
                    &first_config,
                    "猫",
                    &mut cancel,
                    Instant::now() + Duration::from_secs(5),
                )
                .await
        });
        for _ in 0..200 {
            if dir.join("requests").exists() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        assert!(dir.join("requests").exists());
        stop.send(true).unwrap();
        assert_eq!(
            tokio::time::timeout(Duration::from_secs(1), first)
                .await
                .unwrap()
                .unwrap()
                .unwrap_err()
                .to_string(),
            "cancelled"
        );
        std::fs::write(dir.join("release"), "").unwrap();
        let (_owner, mut cancel) = watch::channel(false);
        let next = Reader::new(&dictionary, vec![])
            .read(
                &config,
                "犬",
                &mut cancel,
                Instant::now() + Duration::from_secs(3),
            )
            .await
            .unwrap();
        assert_eq!(next, "いぬ");
        assert_eq!(
            std::fs::read_to_string(dir.join("pids"))
                .unwrap()
                .lines()
                .count(),
            1
        );
        assert_eq!(
            std::fs::read_to_string(dir.join("requests"))
                .unwrap()
                .lines()
                .count(),
            2
        );
        let pid: i32 = std::fs::read_to_string(dir.join("pids"))
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        dictionary.close().await;
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
        std::fs::remove_dir_all(dir).unwrap();
    }

    // 明示実行専用。通常テストで音声エンジンやスピーカーを起動・使用しない。
    #[tokio::test]
    #[ignore = "実VOICEVOXとスピーカーを使う明示的な確認"]
    async fn live_voicevox_bookshelf_reading() {
        let dir =
            std::env::temp_dir().join(format!("dogido-live-reading-{}", uuid::Uuid::new_v4()));
        let mut config = DialogueConfig {
            python: std::env::current_dir()
                .unwrap()
                .parent()
                .unwrap()
                .join("dogido-llm/bin/python"),
            audio_dir: dir.join("audio"),
            ..Default::default()
        };
        config.haiku.memory_dir = dir.clone();
        config.haiku.memory_enabled = true;
        let store = crate::haiku_record::MemoryStore::new(&dir);
        store
            .edit_catalog_reading(
                &crate::catalog_editor::Edit {
                    surface: "草地".into(),
                    reading: "くさっぱら".into(),
                    entry_id: None,
                    wrong_reading: None,
                    expected_id: None,
                }
                .validate()
                .unwrap(),
                chrono::Utc::now(),
            )
            .unwrap();
        let display = "草地で、ひと休みしよう。";
        let (_tx, mut cancel) = watch::channel(false);
        let spoken = read(
            &config,
            &Dictionary::default(),
            display,
            &mut cancel,
            Instant::now() + Duration::from_secs(20),
        )
        .await
        .unwrap();
        assert!(
            spoken.contains("くさっぱら") || spoken.contains("クサッパラ"),
            "{spoken}"
        );
        println!("display={display}\nspoken={spoken}");
        super::super::audio::Audio::new()
            .unwrap()
            .speak(&config, &spoken, &mut cancel, || {})
            .await
            .unwrap();
        println!("VOICEVOX synthesis and audio playback completed");
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[tokio::test]
    async fn ready_needs_no_helper_and_cancelled_ready_is_not_spoken() {
        let mut config = DialogueConfig {
            python: "/missing/tts-python".into(),
            helper: "/missing/haiku_tokens.py".into(),
            ..Default::default()
        };
        let (tx, mut rx) = watch::channel(false);
        for (engine, text, expected) in [
            ("off", " 朝鮮の朝 ", "ちょうせんのあさ"),
            ("auto", "\u{1c} \n", ""),
            ("unidic", "かな カナ𠮷㍻", "かな カナ𠮷㍻"),
        ] {
            config.reading_engine = engine.into();
            assert_eq!(
                read(
                    &config,
                    &Dictionary::default(),
                    text,
                    &mut rx,
                    Instant::now() + Duration::from_secs(1)
                )
                .await
                .unwrap(),
                expected
            );
        }
        assert!(
            read(
                &config,
                &Dictionary::default(),
                "かな",
                &mut rx,
                Instant::now()
            )
            .await
            .is_err()
        );
        tx.send(true).unwrap();
        assert_eq!(
            read(
                &config,
                &Dictionary::default(),
                "かな",
                &mut rx,
                Instant::now() + Duration::from_secs(1)
            )
            .await
            .unwrap_err()
            .to_string(),
            "cancelled"
        );
        let (owner, mut rx) = watch::channel(false);
        drop(owner);
        assert!(
            read(
                &config,
                &Dictionary::default(),
                "かな",
                &mut rx,
                Instant::now() + Duration::from_secs(1)
            )
            .await
            .is_err()
        );
    }
    // 見込み: 辞書の成功応答・任意SDK不在は従来どおり受け取り、既存IPC失敗は表へ返す。
    // 合格: 各結果・要求形式を保持し、明示close後に子PIDが消える。模擬辞書のみで実音声は対象外。
    #[cfg(unix)]
    #[tokio::test]
    async fn resident_child_preserves_results_and_closes_after_transport_errors() {
        for mode in [
            "ok",
            "empty",
            "unavailable",
            "parse_error",
            "cancel",
            "timeout",
            "bad_json",
            "wrong_id",
            "exit_error",
        ] {
            let dir = std::env::temp_dir().join(format!("dogido-unidic-{}", uuid::Uuid::new_v4()));
            std::fs::create_dir_all(&dir).unwrap();
            let response = match mode {
                "cancel" | "timeout" => "read -r blocked".into(),
                "bad_json" => "printf 'not json\\n'".into(),
                _ => {
                    let status = if matches!(mode, "unavailable" | "parse_error") {
                        mode
                    } else {
                        "ok"
                    };
                    let tokens = if matches!(mode, "ok" | "wrong_id" | "exit_error") {
                        r#"[{"surface":"猫","goshu":"和","pos1":"名詞","kana":"ネコ","pron":"ネコ"}]"#
                    } else {
                        "[]"
                    };
                    format!(
                        "{}\nprintf '{{\"schema_version\":1,\"request_id\":\"%s\",\"status\":\"{status}\",\"tokens\":{tokens}}}\\n' \"$request_id\"\n{}",
                        if mode == "wrong_id" {
                            "request_id=old"
                        } else {
                            ""
                        },
                        if mode == "exit_error" { "exit 7" } else { "" }
                    )
                }
            };
            let script = format!(
                "echo $$ > '{}/pid'\nread -r line\nprintf '%s\\n' \"$line\" > '{}/request.pending'\nmv '{}/request.pending' '{}/request'\nrequest_id=${{line#*\\\"request_id\\\":\\\"}}\nrequest_id=${{request_id%%\\\"*}}\n{response}\n",
                dir.display(),
                dir.display(),
                dir.display(),
                dir.display()
            );
            std::fs::write(dir.join("tts_unidic_adapter.py"), script).unwrap();
            let config = DialogueConfig {
                python: "/bin/sh".into(),
                helper: dir.join("haiku_tokens.py"),
                ..Default::default()
            };
            let (tx, mut rx) = watch::channel(false);
            let timeout = if mode == "timeout" {
                Duration::from_millis(150)
            } else {
                Duration::from_secs(3)
            };
            let dictionary = std::sync::Arc::new(Dictionary::default());
            let task_dictionary = dictionary.clone();
            let task = tokio::spawn(async move {
                read(
                    &config,
                    &task_dictionary,
                    " 朝鮮の猫 ",
                    &mut rx,
                    Instant::now() + timeout,
                )
                .await
            });
            if mode == "cancel" {
                for _ in 0..200 {
                    if dir.join("request").exists() {
                        break;
                    }
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }
                tx.send(true).unwrap();
            }
            let result = task.await.unwrap();
            match mode {
                // 応答後にプロセスが終了しても、受信済みの読みは有効。終了はcloseで回収する。
                "ok" | "exit_error" => assert_eq!(result.unwrap(), "ねこ"),
                "empty" => assert_eq!(result.unwrap(), ""),
                "unavailable" | "parse_error" => assert_eq!(result.unwrap(), "ちょうせんの猫"),
                _ => assert!(result.is_err(), "{mode}"),
            }
            let request: Value =
                serde_json::from_str(&std::fs::read_to_string(dir.join("request")).unwrap())
                    .unwrap();
            assert_eq!(request.as_object().unwrap().len(), 3);
            assert_eq!(request["text"], "朝鮮の猫");
            assert_eq!(request["schema_version"], 1);
            assert!(request["request_id"].is_string());
            dictionary.close().await;
            let pid: i32 = std::fs::read_to_string(dir.join("pid"))
                .unwrap()
                .trim()
                .parse()
                .unwrap();
            assert_eq!(unsafe { libc::kill(pid, 0) }, -1, "child remains: {mode}");
            assert_eq!(
                std::io::Error::last_os_error().raw_os_error(),
                Some(libc::ESRCH)
            );
            std::fs::remove_dir_all(&dir).unwrap();
        }
    }
}
