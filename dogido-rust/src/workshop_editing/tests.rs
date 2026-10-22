use super::*;
use crate::workshop_edit;
use serde_json::json;
#[test]
fn legacy_inputs_keep_data_integrity_under_model_owned_edits() {
    let cases: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    assert_eq!(cases.as_array().unwrap().len(), 2006);
    for (index, case) in cases.as_array().unwrap().iter().enumerate() {
        let mut engine = Engine {
            readings: serde_json::from_value(case["readings"].clone()).unwrap(),
        };
        let f = &case["input"];
        let op = text(&case["op"]);
        let actual: Result<Value> = match op {
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
                loop {
                    match engine.project(&f) {
                        Err(error)
                            if matches!(
                                op,
                                "player_edit" | "discussion_candidate" | "explicit_discussion"
                            ) && error.downcast_ref::<NeedsReading>().is_some() =>
                        {
                            // 旧コーパスで照会されなかった語は辞書未解決として模擬する。
                            // 読みを推測せず、表記を保持したまま編集できることを確認する。
                            let source = error.downcast_ref::<NeedsReading>().unwrap().0.clone();
                            engine.readings.insert(source.clone(), source);
                        }
                        result => {
                            break result.and_then(|result| Ok(serde_json::to_value(result)?));
                        }
                    }
                }
            }
        };
        let actual =
            actual.unwrap_or_else(|e| panic!("case {index} {op} text={} error {e}", f["text"]));
        // 移植時の品質・語句拒否は新仕様の期待値にしない。入力は保持し、各成功差分の
        // 元句・対象外不変・行ID・本人語の出典を全件で確認する。具体的な表記は下の明示例でも確認する。
        if op == "player_edit" {
            if actual["text"].is_string() {
                let snapshot = Snapshot::from_view(&f["workshop"]).unwrap();
                let working = editing_records(&snapshot);
                let output: Vec<HaikuLine> =
                    serde_json::from_value(actual["lines"].clone()).unwrap();
                let target = actual["target_line_index"].as_u64().unwrap() as usize;
                assert!(target < 3, "case {index}");
                if let Some(selected) = f["proposal"]["line_index"].as_u64() {
                    assert_eq!(target, selected as usize, "case {index}: model target");
                }
                assert_eq!(output.len(), 3, "case {index}");
                for i in 0..3 {
                    assert_eq!(output[i].line_id, working[i].line_id, "case {index}");
                    if i != target {
                        assert_eq!(output[i], working[i], "case {index}: untouched line {i}");
                    }
                }
                assert_eq!(output[target].provenance, "player_explicit");
                assert!(output[target].source_atom_ids.is_empty());
                assert!(output[target].source_atoms.is_empty());
                assert_eq!(
                    actual["base_text"],
                    workshop_edit::editing_text(&snapshot.current_lines)
                );
                assert_eq!(actual["text"], workshop_edit::editing_text(&output));
                let pending =
                    workshop_edit::Pending::stage(&snapshot.current_lines, working, output, target)
                        .unwrap();
                assert_eq!(
                    actual["edits"],
                    serde_json::to_value(pending.edits()).unwrap()
                );
                if case["expected"]["text"].is_string() {
                    assert_eq!(
                        actual, case["expected"],
                        "case {index}: existing successful edit"
                    );
                }
            } else {
                assert!(
                    !actual["failure_reasons"].as_array().unwrap().is_empty(),
                    "case {index}"
                );
                for reason in actual["failure_reasons"].as_array().unwrap() {
                    assert!(
                        matches!(
                            reason.as_str().unwrap(),
                            "invalid_surface"
                                | "target_conflict"
                                | "target_fragment_not_readable"
                                | "ambiguous_target_fragment"
                                | "missing_target"
                                | "invalid_line_records"
                                | "pending_source_conflict"
                                | "no_change"
                                | "not_hiragana"
                        ),
                        "case {index}: {reason}"
                    );
                }
            }
        } else if matches!(op, "discussion_candidate" | "explicit_discussion") {
            if let Some(candidate) = actual["candidate"].as_object() {
                let proposal = &candidate["proposal"];
                assert!(
                    text(&f["text"]).contains(text(&proposal["replacement_text"])),
                    "case {index}"
                );
                assert_eq!(candidate["evidence"], f["text"], "case {index}");
                assert!(
                    proposal["line_index"].is_null()
                        || proposal["line_index"].as_u64().is_some_and(|i| i < 3),
                    "case {index}"
                );
                if case["expected"]["candidate"].is_object() {
                    assert_eq!(
                        proposal, &case["expected"]["candidate"]["proposal"],
                        "case {index}"
                    );
                }
            } else {
                assert!(
                    case["expected"]["candidate"].is_null(),
                    "case {index}: lost candidate"
                );
            }
        } else {
            assert_eq!(
                actual, case["expected"],
                "case {index} {op} text={}",
                f["text"]
            );
        }
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
        assert_eq!(first.len(), 3);
        assert_eq!(first[0].surface_text, "桜");
        assert_eq!(
            first[0].reading_text,
            if mode == "ok" { "さくら" } else { "" }
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
        // Python起動時間と辞書応答の期限を分ける。起動前のkillを辞書timeoutと数えない。
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            while !dir.join("pid").is_file() {
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("helper ready");
        let result = if mode == "cancel" {
            let (send, mut cancel) = tokio::sync::watch::channel(false);
            let trigger = async {
                tokio::time::timeout(std::time::Duration::from_secs(5), async {
                    while !dir.join("requests").is_file() {
                        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
                    }
                })
                .await
                .expect("request received");
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
fn retained_target_completes_only_an_omitted_model_target() {
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
    let PlayerEditResult::Validated(result) = result else {
        panic!("missing model edit");
    };
    assert_eq!(result.target_line_index, Some(2));
    assert_eq!(result.lines[2].surface_text, "さくらいろ");
    assert_eq!(result.lines[0], records[0]);
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

#[tokio::test]
async fn unknown_reading_edits_preserve_surface_and_can_be_edited_again() {
    let dir = dir();
    let mut helper = fake_helper(&dir, "unavailable");
    let mut engine = Engine::default();
    let base = engine
        .whole_verse("さくらのは\nくろいおのへと\nあさのいろ", "generated")
        .unwrap();
    let mut current = base.clone();
    for surface in ["未知の表記", "次の未知語"] {
        let view = json!({"current_lines":current,"emission":{"lines":base},"materials":{}});
        let frame = json!({"op":"player_edit","workshop":view,"text":format!("上五を{surface}にして"),"proposal":{"line_index":0,"target_fragment":"","replacement_text":surface}});
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine.run(&mut helper, &frame).await.unwrap()
        else {
            panic!("missing edit")
        };
        assert!(result.failure_reasons.is_empty(), "{result:?}");
        assert_eq!(result.lines[0].surface_text, surface);
        assert_eq!(result.lines[0].reading_text, "");
        let pending = workshop_edit::Pending::stage(&current, &current, result.lines, 0).unwrap();
        assert_eq!(pending.edits()[0].replacement_text, surface);
        current = pending.lines;
    }
    helper.finish(false).await.unwrap();
    reaped(&dir);
    std::fs::remove_dir_all(dir).unwrap();
}
