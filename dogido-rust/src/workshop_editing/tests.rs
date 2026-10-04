use super::*;
use serde_json::json;
#[test]
fn extraction_reading_edits_and_materials_match_fixture() {
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
                engine
                    .project(&f)
                    .and_then(|result| Ok(serde_json::to_value(result)?))
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
        let Output::WholeVerse { lines: first } = first else {
            panic!("wrong output");
        };
        let Output::WholeVerse { lines: second } = second else {
            panic!("wrong output");
        };
        assert_eq!(first, second);
        assert_eq!(first.len(), if mode == "ok" { 3 } else { 0 });
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

#[test]
fn retained_target_applies_omitted_location_but_blocks_silent_redirection() {
    let cases: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let case = cases
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["op"] == "player_edit" && c["expected"]["text"].is_string())
        .unwrap();
    let mut frame = case["input"].clone();
    frame["op"] = "player_edit".into();
    let engine = Engine {
        readings: serde_json::from_value(case["readings"].clone()).unwrap(),
    };
    let snapshot = Snapshot::from_view(&frame["workshop"]).unwrap();
    let records = editing_records(&snapshot);
    let target = crate::workshop_target::Target {
        line_index: 0,
        line_id: records[0].line_id.clone(),
        fragment: String::new(),
    };
    frame["workshop"]["discussion_target"] = serde_json::to_value(target).unwrap();
    frame["text"] = "それをさくらいろにして".into();
    frame["proposal"] =
        json!({"replacement_text":"さくらいろ","line_index":null,"target_fragment":""});
    let Output::PlayerEdit(PlayerEditResult::Validated(applied)) = engine.project(&frame).unwrap()
    else {
        panic!("wrong output");
    };
    assert!(applied.text.is_some(), "{applied:?}");
    assert_eq!(applied.target_line_index, Some(0));
    frame["proposal"]["line_index"] = 2.into();
    let Output::PlayerEdit(result) = engine.project(&frame).unwrap() else {
        panic!("wrong output");
    };
    assert_eq!(result.failure_reasons(), ["retained_target_conflict"]);
}

#[test]
fn typed_proposal_preserves_model_evidence_boundary_and_rejects_invalid_indices() {
    let cases: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let case = cases
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["op"] == "player_edit" && c["expected"]["text"].is_string())
        .unwrap();
    let engine = Engine {
        readings: serde_json::from_value(case["readings"].clone()).unwrap(),
    };
    let mut frame = case["input"].clone();
    frame["op"] = "discussion_candidate".into();
    frame["text"] = "『さくらのは』を『さくらいろ』にするのはどう？".into();
    frame["proposal"] = json!({"found":true,"line_index":0,"target_fragment":"さくらのは","replacement_text":"さくらいろ","evidence":frame["text"],"confidence":0.95});
    let Output::DiscussionCandidate {
        candidate: Some(draft),
    } = engine.project(&frame).unwrap()
    else {
        panic!("missing draft");
    };
    assert_eq!(draft.proposal.line_index, Some(0));
    assert!(draft.validation_codes.is_empty());
    assert_eq!(draft.evidence, frame["text"].as_str().unwrap());
    assert!(
        crate::workshop_candidate::Candidate::from_player(
            draft,
            &Snapshot::from_view(&frame["workshop"])
                .unwrap()
                .current_lines,
            0,
            frame["text"].as_str().unwrap()
        )
        .is_some()
    );
    for index in [
        json!(-1),
        json!(u64::MAX),
        json!(true),
        json!(0.5),
        json!("0"),
    ] {
        frame["proposal"]["line_index"] = index;
        assert!(matches!(
            engine.project(&frame).unwrap(),
            Output::DiscussionCandidate { candidate: None }
        ));
    }
}

#[test]
fn typed_edit_passes_directly_to_pending_and_rechecks_stale_or_untargeted_lines() {
    let cases: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let case = cases
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["op"] == "player_edit" && c["expected"]["text"].is_string())
        .unwrap();
    let engine = Engine {
        readings: serde_json::from_value(case["readings"].clone()).unwrap(),
    };
    let mut frame = case["input"].clone();
    frame["op"] = "player_edit".into();
    frame["proposal"]["evidence"] = frame["text"].clone();
    frame["proposal"]["confidence"] = 0.95.into();
    let Output::PlayerEdit(PlayerEditResult::Validated(edit)) = engine.project(&frame).unwrap()
    else {
        panic!("missing edit");
    };
    let current = Snapshot::from_view(&frame["workshop"])
        .unwrap()
        .current_lines;
    let target = edit.target_line_index.unwrap();
    let pending =
        crate::workshop_edit::Pending::stage(&current, &current, edit.lines.clone(), target)
            .unwrap();
    assert_eq!(pending.edits(), edit.edits);
    assert!(
        serde_json::to_value(&edit).unwrap()["edits"][0]
            .get("atom_ids")
            .is_none()
    );
    let mut changed = edit.lines;
    changed[(target + 1) % 3].surface_text = "対象外".into();
    assert!(crate::workshop_edit::Pending::stage(&current, &current, changed, target).is_err());
    assert!(pending.validate(&pending.lines).is_err());
}

#[test]
fn malformed_proposal_cannot_borrow_a_target_from_an_earlier_candidate() {
    let cases: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let case = cases
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["op"] == "player_edit" && c["expected"]["text"].is_string())
        .unwrap();
    let engine = Engine {
        readings: serde_json::from_value(case["readings"].clone()).unwrap(),
    };
    let mut frame = case["input"].clone();
    frame["op"] = "player_edit".into();
    frame["text"] = "さくらいろにして".into();
    frame["proposal"] =
        json!({"replacement_text":"さくらいろ","target_fragment":"","line_index":null});
    frame["workshop"]["conversation_candidate"] =
        json!({"proposal":{"replacement_text":"さくらいろ","target_fragment":"","line_index":0}});
    assert!(matches!(
        engine.project(&frame).unwrap(),
        Output::PlayerEdit(PlayerEditResult::Validated(EditResult {
            text: Some(_),
            ..
        }))
    ));
    for (key, value) in [
        ("line_index", json!("bad")),
        ("line_index", json!(-1)),
        ("line_index", json!(u64::MAX)),
        ("line_index", json!(true)),
        ("line_index", json!(0.5)),
        ("target_fragment", json!(true)),
        ("replacement_text", json!(23)),
    ] {
        let mut malformed = frame.clone();
        malformed["proposal"][key] = value;
        assert!(
            matches!(
                engine.project(&malformed).unwrap(),
                Output::PlayerEdit(PlayerEditResult::Rejected { .. })
            ),
            "{malformed}"
        );
    }
}
