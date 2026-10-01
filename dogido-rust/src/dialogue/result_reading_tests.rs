use super::*;
use std::{
    fs,
    path::{Path, PathBuf},
};

// A real, owned child with no imports of the application, SDK, model, or audio.
const HELPER: &str = r#"
import json,os,sys
from pathlib import Path
x=json.loads(sys.stdin.readline())
Path(x['pidfile']).write_text(str(os.getpid()))
mode=x['case']
frame={'op':'result','text':x['raw'],'repair':None,'debug_marker':'preserved'}
if mode=='payload': frame={'op':'result','unsupported':'原文の引継ぎ'}
if mode=='old_spoken': frame['spoken_text']='変換済み'
if mode=='references': frame['references']=['forged']
if mode=='memory': frame['memory_query']={}
if mode=='wrong_type': frame['text']=7
print(json.dumps(frame,ensure_ascii=False),flush=True)
if mode=='eof': sys.exit(0)
line=sys.stdin.readline()
if not line: sys.exit(0)
r=json.loads(line)
Path(x['tokenfile']).write_text(json.dumps(r,ensure_ascii=False))
if mode in ('ignore','cancel','drop'): sys.stdin.readline();sys.exit(0)
if mode=='bad_json': print('not-json',flush=True);sys.exit(0)
if mode=='legacy_token': print(json.dumps({'spoken_text':'legacy'}),flush=True);sys.exit(0)
if mode=='oversized': print('x'*1_000_001,flush=True);sys.exit(0)
tokens=[{'surface':'猫','goshu':'和','pos1':'名詞','kana':'ネコ','pron':'ネーコ'},
 {'surface':'と','goshu':'和','pos1':'助詞','kana':'ト','pron':'ト'},
 {'surface':'朝鮮','goshu':'漢','pos1':'名詞','kana':'チョウセン','pron':'チョーセン'}]
status='ok'
if mode in ('unavailable','parse_error'): status=mode;tokens=[]
if mode=='empty': tokens=[]
if mode=='partial': tokens=[{'surface':'猫'}]
if mode=='failed_tokens': status='parse_error'
if mode=='unknown_status': status='maybe'
reply={'schema_version':1,'request_id': 'old' if mode=='wrong_id' else r['request_id'],'status':status,'tokens':tokens}
print(json.dumps(reply,ensure_ascii=False),end='' if mode=='no_newline' else '\n',flush=True)
sys.exit(7 if mode=='exit_error' else 0)
"#;
fn setup(case: &str, raw: &str, engine: &str) -> (PathBuf, DialogueConfig, Value) {
    let dir = std::env::temp_dir().join(format!("dogido-chat-reading-{}", uuid::Uuid::new_v4()));
    fs::create_dir_all(&dir).unwrap();
    let helper = dir.join("helper.py");
    fs::write(&helper, HELPER).unwrap();
    let mut config = DialogueConfig {
        python: "/usr/bin/python3".into(),
        helper,
        reading_engine: engine.into(),
        ..Default::default()
    };
    config.haiku.memory_enabled = false;
    let input = json!({"text":"こんにちは","case":case,"raw":raw,"pidfile":dir.join("pid"),"tokenfile":dir.join("token")});
    (dir, config, input)
}
fn reaped(dir: &Path) {
    let pid: i32 = fs::read_to_string(dir.join("pid"))
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(unsafe { libc::kill(pid, 0) }, -1, "helper still alive");
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::ESRCH)
    );
}
fn llm() -> RigLlm {
    RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_millis(1)).unwrap()
}
async fn bounded(
    config: &DialogueConfig,
    input: Value,
    cancel: &mut watch::Receiver<bool>,
    budget: Duration,
) -> Result<Value> {
    render_with_budget(
        config,
        &llm(),
        input.into(),
        cancel,
        |_| Ok(()),
        |_| Ok(false),
        budget,
    )
    .await
}
#[tokio::test]
async fn chat_reading_ready_and_payload_results_have_no_token_exchange() {
    for (case, raw, engine, expected) in [
        ("ready", " 朝鮮の朝 ", "off", Some("ちょうせんのあさ")),
        ("ready", " かな カナ𠮷㍻ ", "auto", Some("かな カナ𠮷㍻")),
        ("ready", "\u{1c} \n", "unidic", Some("")),
        ("payload", "猫", "auto", None),
    ] {
        let (dir, config, input) = setup(case, raw, engine);
        let (_owner, mut rx) = watch::channel(false);
        let result = bounded(&config, input, &mut rx, Duration::from_secs(3))
            .await
            .unwrap();
        if let Some(expected) = expected {
            assert_eq!(result["text"], raw);
            assert_eq!(result["spoken_text"], expected);
            assert_eq!(result["debug_marker"], "preserved");
            assert!(result["repair"].is_null());
        } else {
            assert!(result.get("spoken_text").is_none());
            assert!(result.get("text").is_none());
        }
        assert_eq!(result["llm_reports"], json!([]));
        assert!(!dir.join("token").exists());
        reaped(&dir);
        fs::remove_dir_all(dir).unwrap();
    }
}
#[tokio::test]
async fn chat_reading_one_token_exchange_uses_raw_text_and_retains_metadata() {
    for (case, spoken) in [
        ("ok", "ねことちょうせん"),
        ("empty", ""),
        ("unavailable", "猫とちょうせん"),
        ("parse_error", "猫とちょうせん"),
    ] {
        let (dir, config, input) = setup(case, " 猫と朝鮮 ", "auto");
        let (_owner, mut rx) = watch::channel(false);
        let result = bounded(&config, input, &mut rx, Duration::from_secs(3))
            .await
            .unwrap();
        assert_eq!(result["text"], " 猫と朝鮮 ");
        assert_eq!(result["spoken_text"], spoken);
        assert_eq!(result["debug_marker"], "preserved");
        assert_eq!(result["llm_reports"], json!([]));
        let request: Value =
            serde_json::from_str(&fs::read_to_string(dir.join("token")).unwrap()).unwrap();
        assert_eq!(request.as_object().unwrap().len(), 4);
        assert_eq!(request["op"], "tts_tokens");
        assert_eq!(request["schema_version"], 1);
        assert_eq!(request["text"], "猫と朝鮮");
        assert!(
            request["request_id"]
                .as_str()
                .is_some_and(|s| !s.is_empty())
        );
        reaped(&dir);
        fs::remove_dir_all(dir).unwrap();
    }
}
#[tokio::test]
async fn chat_raw_result_checks_precede_dictionary_and_reject_preformatted_speech() {
    for case in [
        "references",
        "memory",
        "old_spoken",
        "wrong_type",
        "address_mismatch",
    ] {
        let (dir, config, mut input) = setup(case, "猫と朝鮮", "auto");
        if case == "address_mismatch" {
            input["address_reply"] = "別の本文".into();
        }
        let (_owner, mut rx) = watch::channel(false);
        let result = bounded(&config, input, &mut rx, Duration::from_secs(3)).await;
        assert!(result.is_err(), "{case}");
        assert!(
            !dir.join("token").exists(),
            "SDK request before raw validation: {case}"
        );
        reaped(&dir);
        fs::remove_dir_all(dir).unwrap();
    }
}
#[tokio::test]
async fn chat_old_or_broken_token_helpers_fail_and_are_reaped() {
    for case in [
        "eof",
        "ignore",
        "bad_json",
        "wrong_id",
        "legacy_token",
        "partial",
        "failed_tokens",
        "unknown_status",
        "no_newline",
        "oversized",
        "exit_error",
    ] {
        let (dir, config, input) = setup(case, "猫と朝鮮", "auto");
        let (_owner, mut rx) = watch::channel(false);
        let budget = if case == "ignore" {
            Duration::from_secs(2)
        } else {
            Duration::from_secs(3)
        };
        let result = bounded(&config, input, &mut rx, budget).await;
        let message = result.unwrap_err().to_string();
        if case == "ignore" {
            assert!(message.contains("timed out"), "{message}");
        }
        reaped(&dir);
        fs::remove_dir_all(dir).unwrap();
    }
}
#[tokio::test]
async fn chat_token_cancellation_and_sender_drop_reap_the_existing_child() {
    for mode in ["cancel", "drop"] {
        let (dir, config, input) = setup(mode, "猫と朝鮮", "auto");
        let (owner, mut rx) = watch::channel(false);
        let task =
            tokio::spawn(
                async move { bounded(&config, input, &mut rx, Duration::from_secs(5)).await },
            );
        tokio::time::timeout(Duration::from_secs(3), async {
            while !dir.join("token").exists() {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
        if mode == "cancel" {
            owner.send(true).unwrap();
        } else {
            drop(owner);
        }
        let result = tokio::time::timeout(Duration::from_secs(3), task)
            .await
            .unwrap()
            .unwrap();
        assert!(result.unwrap_err().to_string().contains("cancelled"));
        reaped(&dir);
        fs::remove_dir_all(dir).unwrap();
    }
}
