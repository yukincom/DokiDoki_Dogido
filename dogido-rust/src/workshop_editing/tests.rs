use super::*;
#[test]
fn canonical_python_extraction_reading_edits_and_materials() {
    let cases: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    assert_eq!(cases.as_array().unwrap().len(), 2666);
    for (index, case) in cases.as_array().unwrap().iter().enumerate() {
        let engine = Engine {
            readings: serde_json::from_value(case["readings"].clone()).unwrap(),
        };
        let f = &case["input"];
        let op = text(&case["op"]);
        let actual: Result<Value> = match op {
            "parse" => {
                let (status, replacement) = parse::replacement(text(&f["text"]));
                Ok(
                    json!({"status":status,"replacement":replacement.map(|r|json!({"text":r.text,"explicit_line_index":r.index,"target_fragment":r.fragment}))}),
                )
            }
            "normalize" => engine.normalized(text(&f["text"]), true).map(|v| json!(v)),
            "material" => engine
                .material_for_question(
                    &Snapshot::from_view(&f["workshop"]).unwrap(),
                    text(&f["text"]),
                )
                .map(|v| json!(v)),
            "fragment" => engine
                .mentioned_fragment(
                    &Snapshot::from_view(&f["workshop"]).unwrap(),
                    text(&f["text"]),
                    text(&f["replacement"]),
                )
                .map(|v| json!(v)),
            "short_materials" => Ok(json!(materials::short_material_entries(f))),
            _ => {
                let mut f = f.clone();
                f["op"] = op.into();
                engine.project(&f)
            }
        };
        assert_eq!(
            actual.unwrap_or_else(|e| panic!("case {index} {op} text={} error {e}", f["text"])),
            case["expected"],
            "case {index} {op} text={}",
            f["text"]
        );
    }
}

fn fake_helper(dir: &std::path::Path, mode: &str) -> Helper {
    let script = r#"import json,os,sys,time
from pathlib import Path
root=Path(__file__).parent
(root/'pid').write_text(str(os.getpid()))
for line in sys.stdin:
 f=json.loads(line)
 with (root/'requests').open('a') as out:out.write(json.dumps(f,ensure_ascii=False)+'\n')
 mode=(root/'mode').read_text()
 if mode=='blocked':time.sleep(60)
 if mode=='legacy':r={'fixed_payload':None}
 else:
  text=f['text'];reading=text.replace('桜','さくら').replace('夏','なつ')
  r={'schema_version':1,'request_id':'wrong' if mode=='wrong_id' else f['request_id'],
     'status':mode if mode in ('unavailable','parse_error') else 'ok',
     'tokens':[] if mode in ('unavailable','parse_error') else [{'surface':text,'kana':reading,'pron':None,'goshu':None,'pos1':None}]}
 print(json.dumps(r,ensure_ascii=False),flush=True)
"#;
    std::fs::write(dir.join("helper.py"), script).unwrap();
    std::fs::write(dir.join("mode"), mode).unwrap();
    Helper::start(
        std::path::Path::new(&std::env::var("DOGIDO_TEST_PYTHON").unwrap_or("python3".into())),
        &dir.join("helper.py"),
    )
    .unwrap()
}
fn dir() -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("dogido-native-edit-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn reaped(d: &std::path::Path) {
    let pid = std::fs::read_to_string(d.join("pid"))
        .unwrap()
        .parse::<i32>()
        .unwrap();
    assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::ESRCH)
    );
}
#[tokio::test]
async fn same_child_cache_and_optional_dictionary_failure() {
    for mode in ["ok", "unavailable", "parse_error"] {
        let dir = dir();
        let mut helper = fake_helper(&dir, mode);
        let mut engine = Engine::default();
        let frame = json!({"op":"whole_verse","text":"桜\n桜\n桜","source":"formal"});
        let first = engine.run(&mut helper, &frame).await.unwrap();
        let second = engine.run(&mut helper, &frame).await.unwrap();
        assert_eq!(first, second);
        assert_eq!(
            list(&first["lines"]).len(),
            if mode == "ok" { 3 } else { 0 }
        );
        helper.finish(false).await.unwrap();
        reaped(&dir);
        let requests = std::fs::read_to_string(dir.join("requests")).unwrap();
        assert_eq!(requests.lines().count(), 1);
        let f: Value = serde_json::from_str(requests.trim()).unwrap();
        assert_eq!(f["op"], "tts_tokens");
        assert_eq!(f["text"], "桜");
        std::fs::remove_dir_all(dir).unwrap();
    }
}
#[tokio::test]
async fn dictionary_protocol_errors_deadline_and_cancel_do_not_become_success() {
    for mode in ["legacy", "wrong_id", "timeout", "cancel"] {
        let dir = dir();
        let mut helper = fake_helper(
            &dir,
            if matches!(mode, "timeout" | "cancel") {
                "blocked"
            } else {
                mode
            },
        );
        let mut engine = Engine::default();
        let frame = json!({"op":"whole_verse","text":"桜\n桜\n桜","source":"formal"});
        let result = if mode == "cancel" {
            let (send, mut cancel) = tokio::sync::watch::channel(false);
            let trigger = async {
                tokio::time::sleep(std::time::Duration::from_millis(150)).await;
                send.send(true).unwrap();
            };
            let run = async {
                tokio::select! {_=cancel.changed()=>Err(anyhow::anyhow!("cancelled")),r=engine.run(&mut helper,&frame)=>r}
            };
            tokio::join!(run, trigger).0
        } else {
            tokio::time::timeout(
                std::time::Duration::from_millis(250),
                engine.run(&mut helper, &frame),
            )
            .await
            .unwrap_or_else(|_| Err(anyhow::anyhow!("test turn deadline")))
        };
        assert!(result.is_err(), "{mode}");
        assert!(engine.readings.is_empty());
        helper.finish(true).await.unwrap();
        reaped(&dir);
        assert_eq!(
            std::fs::read_to_string(dir.join("requests"))
                .unwrap()
                .lines()
                .count(),
            1
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
}
