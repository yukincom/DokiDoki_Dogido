use super::*;

fn directory() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("dogido-shared-tts-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}
fn stopped(pid: u32) {
    assert_eq!(unsafe { libc::kill(pid as i32, 0) }, -1, "child remains");
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::ESRCH)
    );
}
fn script(dir: &Path, body: &str) -> std::path::PathBuf {
    let path = dir.join("helper.sh");
    std::fs::write(&path, body).unwrap();
    path
}
#[tokio::test]
async fn shared_ready_never_consumes_ipc_or_reformats_a_dictionary_reply() {
    let dir = directory();
    let path = script(
        &dir,
        &format!(
            r#"read -r line
printf '%s\n' "$line" > '{}/requests'
request_id=${{line#*\"request_id\":\"}}
request_id=${{request_id%%\"*}}
printf '{{"schema_version":1,"request_id":"%s","status":"ok","tokens":[{{"surface":"仮名","goshu":"漢","pos1":"名詞","kana":"カナ","pron":"カナ"}}]}}\n' "$request_id"
"#,
            dir.display()
        ),
    );
    let mut helper = Helper::start(Path::new("/bin/sh"), &path).unwrap();
    let pid = helper.child.id().unwrap();
    for (engine, text, expected) in [
        ("off", " 朝鮮の朝 ", "ちょうせんのあさ"),
        ("auto", "かな カナ𠮷㍻", "かな カナ𠮷㍻"),
        ("unidic", "\u{1c} \n", ""),
        // Preserve the canonical replacement order instead of silently fixing it.
        ("off", "今朝は元気や。", "今あさは元気や。"),
    ] {
        let frame = json!({"op":"reading","text":text,"reading_engine":engine});
        assert_eq!(
            helper.exchange(frame.clone()).await.unwrap(),
            json!({"spoken_text":expected})
        );
        assert_eq!(frame["text"], text);
        assert_eq!(helper.child.id(), Some(pid));
        assert!(!dir.join("requests").exists());
    }
    let transformed = helper
        .transform(TransformRequest {
            text: "仮名".into(),
            mode: crate::haiku::TransformMode::Normalize,
            line_index: 0,
            atom_ids: vec![],
            source_atoms: vec![],
        })
        .await
        .unwrap();
    assert_eq!(transformed.text, "かな");
    let frame: Value =
        serde_json::from_str(&std::fs::read_to_string(dir.join("requests")).unwrap()).unwrap();
    assert_eq!(frame["op"], "tts_tokens");
    helper.finish(false).await.unwrap();
    stopped(pid);
    std::fs::remove_dir_all(dir).unwrap();
}
#[tokio::test]
async fn shared_sdk_results_keep_the_same_child_for_subsequent_operations() {
    for status in ["ok", "empty", "unavailable", "parse_error"] {
        let dir = directory();
        let tokens = if status == "ok" {
            r#"[{"surface":"朝鮮","goshu":"漢","pos1":"名詞","kana":"チョウセン","pron":"チョーセン"},{"surface":"の","goshu":"和","pos1":"助詞","kana":"ノ","pron":"ノ"},{"surface":"猫","goshu":"和","pos1":"名詞","kana":"ネコ","pron":"ネコ"}]"#
        } else {
            "[]"
        };
        let wire_status = if status == "empty" { "ok" } else { status };
        let path = script(
            &dir,
            &format!(
                "count=0\nwhile read -r line; do\ncount=$((count+1))\nprintf '%s\\n' \"$line\" >> '{}/requests'\nrequest_id=${{line#*\\\"request_id\\\":\\\"}}\nrequest_id=${{request_id%%\\\"*}}\nif [ \"$count\" -le 2 ]; then\nprintf '{{\"schema_version\":1,\"request_id\":\"%s\",\"status\":\"{wire_status}\",\"tokens\":{tokens}}}\\n' \"$request_id\"\nelse\nprintf '{{\"applied\":true}}\\n'\nfi\ndone\n",
                dir.display()
            ),
        );
        let mut helper = Helper::start(Path::new("/bin/sh"), &path).unwrap();
        let pid = helper.child.id().unwrap();
        for _ in 0..2 {
            let frame = helper
                .exchange(json!({"op":"reading","text":" 朝鮮の猫 ","reading_engine":"auto"}))
                .await
                .unwrap();
            let expected = match status {
                "ok" => "ちょうせんのねこ",
                "empty" => "",
                _ => "ちょうせんの猫",
            };
            assert_eq!(frame["spoken_text"], expected);
            assert_eq!(helper.child.id(), Some(pid));
        }
        let overlay =
            json!({"op":"reading_overlay","rows":[{"surface":"猫","reading":"catalog-only"}]});
        assert_eq!(
            helper.exchange(overlay.clone()).await.unwrap(),
            json!({"applied":true})
        );
        helper.finish(false).await.unwrap();
        stopped(pid);
        let requests = std::fs::read_to_string(dir.join("requests")).unwrap();
        let rows: Vec<Value> = requests
            .lines()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect();
        assert_eq!(rows.len(), 3);
        for r in &rows[..2] {
            assert_eq!(r.as_object().unwrap().len(), 4);
            assert_eq!(r["op"], "tts_tokens");
            assert_eq!(r["text"], "朝鮮の猫");
            assert_eq!(r["schema_version"], 1);
        }
        assert_ne!(rows[0]["request_id"], rows[1]["request_id"]);
        assert_eq!(rows[2], overlay);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
#[tokio::test]
async fn shared_bad_token_protocol_is_not_a_successful_fallback_and_is_reaped() {
    for mode in [
        "timeout",
        "bad_json",
        "wrong_id",
        "legacy_spoken",
        "partial_tokens",
        "eof",
    ] {
        let dir = directory();
        let response = match mode {
            "timeout" => "read -r blocked",
            "bad_json" => "printf 'not-json\\n'",
            "legacy_spoken" => "printf '{\"spoken_text\":\"manual-already-applied\"}\\n'",
            "wrong_id" => {
                "printf '{\"schema_version\":1,\"request_id\":\"old\",\"status\":\"ok\",\"tokens\":[]}\\n'"
            }
            "partial_tokens" => {
                "printf '{\"schema_version\":1,\"request_id\":\"%s\",\"status\":\"ok\",\"tokens\":[{\"surface\":\"猫\"}]}\\n' \"$request_id\""
            }
            _ => "exit 0",
        };
        let path = script(
            &dir,
            &format!(
                "read -r line\nrequest_id=${{line#*\\\"request_id\\\":\\\"}}\nrequest_id=${{request_id%%\\\"*}}\n{response}\n"
            ),
        );
        let mut helper = Helper::start(Path::new("/bin/sh"), &path).unwrap();
        let pid = helper.child.id().unwrap();
        let result = helper
            .exchange_with_timeout(
                json!({"op":"reading","text":"猫"}),
                Duration::from_millis(150),
            )
            .await;
        assert!(result.is_err(), "{mode}");
        assert!(helper.poisoned);
        assert!(helper.child.try_wait().unwrap().is_some());
        assert!(
            helper
                .exchange(json!({"op":"reading","text":"かな"}))
                .await
                .is_err()
        );
        helper.finish(true).await.unwrap();
        stopped(pid);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
#[tokio::test]
async fn shared_cancelled_exchange_remains_an_error_and_owner_reaps_child() {
    let dir = directory();
    let path = script(&dir, "read -r line\nread -r blocked\n");
    let mut helper = Helper::start(Path::new("/bin/sh"), &path).unwrap();
    let pid = helper.child.id().unwrap();
    let (owner, mut cancel) = watch::channel(false);
    let error = {
        let exchange = helper.exchange(json!({"op":"reading","text":"猫"}));
        tokio::pin!(exchange);
        tokio::select! {biased;
            _=cancelled(&mut cancel)=>Err(anyhow::anyhow!("cancelled")),
            result=&mut exchange=>result,
            _=tokio::time::sleep(Duration::from_millis(50))=>{owner.send(true).unwrap();Err(anyhow::anyhow!("cancelled"))},
        }
    };
    assert!(error.is_err());
    // End the cancelled borrow before exercising the existing owner cleanup.
    helper.finish(true).await.unwrap();
    stopped(pid);
    std::fs::remove_dir_all(dir).unwrap();
}
#[tokio::test]
async fn shared_native_reading_preserves_deadline_and_output_bound() {
    for (text, timeout) in [
        ("かな".into(), Duration::ZERO),
        ("朝鮮".repeat(120_000), Duration::from_secs(2)),
    ] {
        let dir = directory();
        let path = script(&dir, "read -r never\n");
        let mut helper = Helper::start(Path::new("/bin/sh"), &path).unwrap();
        let pid = helper.child.id().unwrap();
        assert!(
            helper
                .exchange_with_timeout(
                    json!({"op":"reading","text":text,"reading_engine":"off"}),
                    timeout
                )
                .await
                .is_err()
        );
        helper.finish(true).await.unwrap();
        stopped(pid);
        std::fs::remove_dir_all(dir).unwrap();
    }
}

fn normalize(text: &str) -> TransformRequest {
    TransformRequest {
        text: text.into(),
        mode: crate::haiku::TransformMode::Normalize,
        line_index: 0,
        atom_ids: vec![],
        source_atoms: vec![],
    }
}
#[tokio::test]
async fn lexical_transforms_use_only_one_neutral_dictionary_query_when_needed() {
    for status in ["ok", "unavailable", "parse_error"] {
        let dir = directory();
        let tokens = if status == "ok" {
            r#"[{"surface":"情報","goshu":"漢","pos1":"記号","kana":"ジョウホウ","pron":"ジョーホー"}]"#
        } else {
            "[]"
        };
        let path = script(
            &dir,
            &format!(
                r#"count=0
while read -r line; do
count=$((count+1))
printf '%s\n' "$line" >> '{}/requests'
request_id=${{line#*\"request_id\":\"}}
request_id=${{request_id%%\"*}}
if [ "$count" -eq 1 ]; then
printf '{{"schema_version":1,"request_id":"%s","status":"{status}","tokens":{tokens}}}\n' "$request_id"
else
printf '{{"applied":true}}\n'
fi
done
"#,
                dir.display()
            ),
        );
        let mut helper = Helper::start(Path::new("/bin/sh"), &path).unwrap();
        let pid = helper.child.id().unwrap();
        assert_eq!(
            helper.transform(normalize("「カナ」")).await.unwrap().text,
            "カナ"
        );
        let mut correction = normalize("「カナ」");
        correction.mode = crate::haiku::TransformMode::Correct;
        assert_eq!(helper.transform(correction).await.unwrap().text, "「カナ」");
        assert!(!dir.join("requests").exists());
        let result = helper.transform(normalize("情報")).await.unwrap();
        // Neutral reading includes Sino-Japanese and symbol tokens; no TTS manual replacement.
        assert_eq!(
            result.text,
            if status == "ok" {
                "じょうほう"
            } else {
                "情報"
            }
        );
        assert_eq!(
            helper
                .exchange(json!({"op":"reading_overlay","rows":[]}))
                .await
                .unwrap(),
            json!({"applied":true})
        );
        helper.finish(false).await.unwrap();
        stopped(pid);
        let rows: Vec<Value> = std::fs::read_to_string(dir.join("requests"))
            .unwrap()
            .lines()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["op"], "tts_tokens");
        assert_eq!(rows[0]["text"], "情報");
        std::fs::remove_dir_all(dir).unwrap();
    }
}
#[tokio::test]
async fn lexical_invalid_sdk_reply_cannot_be_reused_as_a_later_line() {
    for response in [
        "not-json",
        r#"{"schema_version":1,"request_id":"old","status":"ok","tokens":[]}"#,
        r#"{"text":"かな","signature":"かな"}"#,
    ] {
        let dir = directory();
        let path = script(
            &dir,
            &format!("read -r line\nprintf '%s\\n' '{response}'\nread -r blocked\n"),
        );
        let mut helper = Helper::start(Path::new("/bin/sh"), &path).unwrap();
        let pid = helper.child.id().unwrap();
        assert!(helper.transform(normalize("情報")).await.is_err());
        assert!(helper.poisoned);
        assert!(helper.child.try_wait().unwrap().is_some());
        assert!(helper.transform(normalize("かな")).await.is_err());
        helper.finish(true).await.unwrap();
        stopped(pid);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
