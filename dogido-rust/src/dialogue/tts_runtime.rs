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
/// The caller retains its existing whole-turn deadline. A dictionary exchange also
/// retains Helper's 15-second IPC bound; cleanup is bounded separately as before.
pub(super) async fn read(
    config: &DialogueConfig,
    text: &str,
    cancel: &mut watch::Receiver<bool>,
    deadline: Instant,
) -> Result<String> {
    let corrections = super::reading_runtime::load_overlay(config).await?;
    let mut reader = Reader::new(corrections);
    let result = reader.read(config, text, cancel, deadline).await;
    let cleanup = reader.finish(result.is_err()).await;
    let spoken = result?;
    cleanup?;
    Ok(spoken)
}

/// 一つの返答の読み訂正と辞書プロセスを共有する。文ごとにPythonを起動せず、
/// 呼出側が成功・失敗・取消のいずれでもfinishを待って所有プロセスを回収する。
pub(super) struct Reader {
    corrections: Vec<serde_json::Value>,
    helper: Option<Helper>,
}
impl Reader {
    pub(super) fn new(corrections: Vec<serde_json::Value>) -> Self {
        Self {
            corrections,
            helper: None,
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
        if self.helper.is_none() {
            let script = config
                .helper
                .parent()
                .context("dictionary helper directory")?
                .join("tts_unidic_adapter.py");
            self.helper = Some(Helper::start(&config.python, &script)?);
        }
        let request_id = uuid::Uuid::new_v4().to_string();
        tracing::info!(event = "tts_unidic_requested", request_id);
        let helper = self.helper.as_mut().unwrap();
        let body = async {
            let response = helper
                .exchange(json!({"schema_version":1,"request_id":request_id,"text":source}))
                .await?;
            tokens::decode(response, &request_id)
        };
        let dictionary = tokio::select! {biased;
            _=bridge::cancelled(cancel)=>Err(anyhow::anyhow!("cancelled")),
            result=tokio::time::timeout_at(deadline,body)=>result.unwrap_or_else(|_|Err(anyhow::anyhow!("TTS reading timed out"))),
        }?;
        live(cancel, deadline)?;
        Ok(tts_reading::finish(&source, dictionary.as_deref()))
    }
    pub(super) async fn finish(mut self, abort: bool) -> Result<()> {
        if let Some(helper) = self.helper.take() {
            helper.finish(abort).await?;
        }
        Ok(())
    }
}

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
                }.validate().unwrap(),
                chrono::Utc::now(),
            )
            .unwrap();
        let before = read(
            &config,
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
                }.validate().unwrap(),
                chrono::Utc::now(),
            )
            .unwrap();
        let after = read(
            &config,
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

    #[cfg(unix)]
    #[tokio::test]
    async fn sentence_reader_reuses_one_dictionary_child_and_reaps_it() {
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
        let config = DialogueConfig {
            python: "/bin/sh".into(),
            helper: dir.join("unused.py"),
            ..Default::default()
        };
        let (_owner, mut cancel) = watch::channel(false);
        let mut reader = Reader::new(vec![]);
        for _ in 0..3 {
            assert_eq!(
                reader
                    .read(
                        &config,
                        "猫",
                        &mut cancel,
                        Instant::now() + Duration::from_secs(3)
                    )
                    .await
                    .unwrap(),
                "ねこ"
            );
        }
        reader.finish(false).await.unwrap();
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
                }.validate().unwrap(),
                chrono::Utc::now(),
            )
            .unwrap();
        let display = "草地で、ひと休みしよう。";
        let (_tx, mut cancel) = watch::channel(false);
        let spoken = read(
            &config,
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
            read(&config, "かな", &mut rx, Instant::now())
                .await
                .is_err()
        );
        tx.send(true).unwrap();
        assert_eq!(
            read(
                &config,
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
                "かな",
                &mut rx,
                Instant::now() + Duration::from_secs(1)
            )
            .await
            .is_err()
        );
    }
    #[cfg(unix)]
    #[tokio::test]
    async fn dedicated_child_is_reaped_on_success_sdk_failure_and_transport_errors() {
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
            let task = tokio::spawn(async move {
                read(&config, " 朝鮮の猫 ", &mut rx, Instant::now() + timeout).await
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
                "ok" => assert_eq!(result.unwrap(), "ねこ"),
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
