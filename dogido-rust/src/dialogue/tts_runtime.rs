//! Free-text reading for native reaction/recall paths only. No catalog overlay.
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
    live(cancel, deadline)?;
    let source = match tts_reading::prepare(text, Some(&config.reading_engine), None) {
        Step::Ready(spoken) => {
            live(cancel, deadline)?;
            return Ok(spoken);
        }
        Step::NeedsUnidic { source } => source,
    };
    let script = config
        .helper
        .parent()
        .context("dictionary helper directory")?
        .join("tts_unidic_adapter.py");
    live(cancel, deadline)?;
    let mut helper = Helper::start(&config.python, &script)?;
    let request_id = uuid::Uuid::new_v4().to_string();
    tracing::info!(event = "tts_unidic_requested", request_id);
    let body = async {
        let response = helper
            .exchange(json!({"schema_version":1,"request_id":request_id,"text":source}))
            .await?;
        tokens::decode(response, &request_id)
    };
    let result = tokio::select! {biased;
        _=bridge::cancelled(cancel)=>Err(anyhow::anyhow!("cancelled")),
        result=tokio::time::timeout_at(deadline,body)=>result.unwrap_or_else(|_|Err(anyhow::anyhow!("TTS reading timed out"))),
    };
    // IPC/cancel/timeout errors stay errors. Only a well-formed SDK failure is None.
    let cleanup = helper.finish(result.is_err()).await;
    let dictionary = result?;
    cleanup?;
    live(cancel, deadline)?;
    Ok(tts_reading::finish(&source, dictionary.as_deref()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use std::time::Duration;
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
