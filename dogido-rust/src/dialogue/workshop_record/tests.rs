use super::*;
#[test]
fn records_only_result_facts_not_speech_or_model_payloads() {
    let value = result_fields(
        &json!({"text":"speech","error":"private server detail","llm_reports":["prompt"],
            "workshop_steps":[{"action":"inspect","speech":"hidden","checks":["meter"],"validation_codes":["meter_not_exact"]}]}),
    );
    assert!(value.get("text").is_none());
    assert!(value.get("error").is_none());
    assert!(value.get("llm_reports").is_none());
    assert!(value["steps"][0].get("speech").is_none());
    assert_eq!(value["steps"][0]["validation_codes"][0], "meter_not_exact");
}
#[tokio::test]
async fn writer_flushes_and_keeps_old_lines() {
    let dir = std::env::temp_dir().join(format!("dogido-record-test-{}", uuid::Uuid::new_v4()));
    let path = dir.join("turns.jsonl");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(&path, "{\"old\":true}\n").unwrap();
    let writer = Recorder::new(true);
    for n in 0..10 {
        writer.append(path.clone(), json!({"n":n}));
    }
    writer.flush().await;
    let data = std::fs::read_to_string(&path).unwrap();
    assert_eq!(data.lines().count(), 11);
    assert!(data.starts_with("{\"old\":true}\n"));
    std::fs::remove_dir_all(dir).unwrap();
}

pub(in crate::dialogue) struct Fixture {
    pub d: Arc<Dialogue>,
    root: PathBuf,
}
impl Fixture {
    pub async fn records(&self) -> Vec<Value> {
        self.d.workshop_records.flush().await;
        let path = self
            .root
            .join("sessions/s/long_term/haiku_workshop_turns.jsonl");
        std::fs::read_to_string(path)
            .unwrap_or_default()
            .lines()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect()
    }
    pub async fn finish(self) {
        self.d.shutdown().await;
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}
pub(in crate::dialogue) fn fixture() -> Fixture {
    use crate::haiku_record::{PreparedEmission, Workshop};
    let root =
        std::env::temp_dir().join(format!("dogido-record-boundary-{}", uuid::Uuid::new_v4()));
    let mut config = DialogueConfig {
        audio_enabled: false,
        base_url: "http://127.0.0.1:1/v1".into(),
        ..Default::default()
    };
    config.haiku.enabled = false;
    config.haiku.memory_enabled = true;
    config.haiku.memory_dir = root.clone();
    config.web.enabled = false;
    let d = Dialogue::new(config).unwrap();
    d.register("s", "試験", true);
    let lines:Vec<Value>=[("upper","上五","くさちのひ"),("middle","中七","くろきつるぎの"),("lower","下五","かげのさむさ")]
        .iter().enumerate().map(|(i,(position,name,text))| json!({"line_id":format!("line_{}",i+1),
            "line_index":i,"position":position,"canonical_name":name,"surface_text":text,"reading_text":text,
            "source_atom_ids":[],"source_atoms":[],"provenance":"fixture"})).collect();
    let prepared: PreparedEmission =
        serde_json::from_value(json!({"text":"くさちのひ くろきつるぎの かげのさむさ",
        "lines":lines,"materials":{}}))
        .unwrap();
    let w = Workshop::open(
        prepared.complete(chrono::Utc::now()).unwrap(),
        None,
        Instant::now(),
    );
    d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .haiku
        .workshop = Some(w);
    Fixture { d, root }
}
fn attempt(f: &Fixture, raw: &str, semantic: Option<&str>, epoch: u64, private: bool) -> Attempt {
    Attempt::new(
        f.d.workshop_record_state("s"),
        Input {
            raw: raw.into(),
            semantic: semantic.map(str::to_owned),
            private,
            epoch: Some(epoch),
        },
    )
}
#[tokio::test]
async fn result_preserves_raw_semantic_and_epoch_without_double_counting() {
    let f = fixture();
    let a = attempt(&f, "しらかばを変えて", Some("しらかばをかえて"), 7, false);
    let after = f.d.workshop_record_state("s");
    let result = json!({"turn_id":"t","epoch":7,"playback_status":"cancelled","workshop_action":"stage_player_edit"});
    f.d.record_workshop("s", "decision", &a.before, &after, &a.input, &result);
    f.d.record_workshop_result("s", &a, &after, &result);
    f.d.record_workshop_result("s", &a.clone(), &after, &result);
    let rows = f.records().await;
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[1]["player_text"], "しらかばを変えて");
    assert_eq!(rows[1]["semantic_player_text"], "しらかばをかえて");
    assert_eq!(rows[1]["epoch"], 7);
    assert_eq!(rows[1]["result"]["epoch"], 7);
    assert_eq!(rows[1]["result"]["playback_status"], "cancelled");
    f.finish().await;
}
#[tokio::test]
async fn old_epoch_never_reads_replayed_rows_result_and_new_epoch_is_distinct() {
    let f = fixture();
    let old = attempt(&f, "元入力", Some("解釈文"), 3, false);
    let new = attempt(&f, "元入力", Some("解釈文"), 4, false);
    f.d.data
        .lock()
        .unwrap()
        .rows
        .push_back(json!({"session_id":"s","turn_id":"same","epoch":4,
        "playback_status":"completed","workshop_action":"accept_pending"}));
    f.d.record_workshop_turn("s", "same", &old);
    f.d.record_workshop_turn("s", "same", &old);
    f.d.record_workshop_turn("s", "same", &new);
    let rows = f.records().await;
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0]["epoch"], 3);
    assert_eq!(rows[0]["result"]["reason"], "superseded_attempt");
    assert!(rows[0]["result"]["workshop_action"].is_null());
    assert_eq!(rows[1]["epoch"], 4);
    assert_eq!(rows[1]["result"]["workshop_action"], "accept_pending");
    f.finish().await;
}
#[tokio::test]
async fn private_input_is_excluded_from_admission_decision_and_terminal_after_web_ends() {
    let f = fixture();
    {
        let mut data = f.d.data.lock().unwrap();
        data.sessions.get_mut("s").unwrap().web.state.research =
            Some(crate::language::web::Research {
                question: "合成の質問".into(),
                target: "合成".into(),
                pages: vec![],
                phase: "reading".into(),
                search_results: vec![],
                search_url: "https://private.example/secret".into(),
            });
    }
    // Code-only path: the ordinary turn is accepted while web research is active.
    // Clear it before the spawned turn finishes, like an end-reading response.
    let got = f.d.submit(Some("s"), "気にせんで", "text");
    assert_eq!(got["accepted"], true, "{got}");
    f.d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .web
        .state
        .clear(false);
    let jobs = std::mem::take(&mut *f.d.jobs.lock().unwrap());
    for job in jobs {
        job.await.unwrap();
    }
    assert!(f.records().await.is_empty());
    let private = attempt(
        &f,
        "https://private.example/secret ページ引用",
        Some("秘密引用"),
        12,
        true,
    );
    let after = f.d.workshop_record_state("s");
    let result = json!({"turn_id":"private","epoch":12,"playback_status":"completed",
        "workshop_steps":[{"evidence":"ページ引用","action":"respond"}]});
    f.d.record_workshop(
        "s",
        "input_admission",
        &private.before,
        &after,
        &private.input,
        &result,
    );
    f.d.record_workshop(
        "s",
        "decision",
        &private.before,
        &after,
        &private.input,
        &result,
    );
    f.d.record_workshop_result("s", &private, &after, &result);
    assert!(f.records().await.is_empty());
    f.finish().await;
}
#[tokio::test]
async fn game_event_first_admission_is_not_a_forward_and_does_not_repeat_on_duplicate() {
    let f = fixture();
    f.d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .preview = false;
    let mut raw = super::super::empty_event("試験");
    raw["sequence"] = 44.into();
    raw["event"]["name"] = "ambient_mob_detected".into();
    raw["meta"] = json!({"user_text":"合成の会話入力"});
    let event: GameEvent = serde_json::from_value(raw).unwrap();
    let result = f.d.observe("s", event.clone(), Some("first-input"));
    assert_eq!(
        result["player_input"]["reason"], "fresh_safe_snapshot_required",
        "{result}"
    );
    assert!(result.get("_workshop_direct_input").is_none());
    f.d.observe("s", event, Some("first-input"));
    let rows = f.records().await;
    let admissions: Vec<_> = rows
        .iter()
        .filter(|r| {
            r["event_kind"] == "input_admission" || r["event_kind"] == "forwarded_admission"
        })
        .collect();
    assert_eq!(admissions.len(), 1, "{rows:?}");
    assert_eq!(admissions[0]["event_kind"], "input_admission");
    assert!(admissions[0]["semantic_player_text"].is_null());
    let g = f.d.data.lock().unwrap().sessions["s"].input_generation;
    f.d.submit_inner(
        Some("s"),
        "合成の会話入力",
        "text",
        true,
        Some((g, None)),
        true,
    );
    assert_eq!(
        f.records().await.last().unwrap()["event_kind"],
        "forwarded_admission"
    );
    f.finish().await;
}
#[tokio::test]
async fn identical_lifecycle_transition_is_recorded_once_with_session_bounded_state() {
    let f = fixture();
    let before = f.d.workshop_record_state("s");
    let mut after = before.clone();
    after["combat_paused"] = true.into();
    for reason in ["workshop_tick", "game_event"] {
        f.d.record_workshop(
            "s",
            "lifecycle",
            &before,
            &after,
            &Input::default(),
            &json!({"reason":reason}),
        );
    }
    assert_eq!(f.records().await.len(), 1);
    // A later resume and pause is a different transition and must remain visible.
    f.d.record_workshop(
        "s",
        "lifecycle",
        &after,
        &before,
        &Input::default(),
        &json!({"reason":"resume"}),
    );
    f.d.record_workshop(
        "s",
        "lifecycle",
        &before,
        &after,
        &Input::default(),
        &json!({"reason":"pause"}),
    );
    assert_eq!(f.records().await.len(), 3);
    f.d.close("s");
    assert!(f.d.workshop_records.transitions.lock().unwrap().is_empty());
    f.finish().await;
}
