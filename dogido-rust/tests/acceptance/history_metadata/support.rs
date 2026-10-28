//! パート2の入力と配送の試験補助。履歴の期待値や成功結果は生成しない。
//! 未生成の返答台帳だけを準備し、履歴確定は本体のupdate／表示ACKへ通す。
use super::*;

pub(super) const SESSION: &str = "history-metadata-acceptance";
pub(super) const TURN: &str = "63c9853b-d8ba-4f2c-804d-30a54ef54023";
pub(super) const QUESTION: &str = "今日は石を集めたよ";
pub(super) const ANSWER: &str = "ようけ集めたな。";

pub(super) fn dialogue() -> Arc<Dialogue> {
    let mut config = DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        ..Default::default()
    };
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    let d = Dialogue::new(config).unwrap();
    d.register(SESSION, "受入試験", true);
    d
}

pub(super) fn rows(d: &Dialogue) -> Vec<Value> {
    d.snapshot(Some(SESSION))["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["session_id"] == SESSION)
        .unwrap()["history"]
        .as_array()
        .unwrap()
        .clone()
}

pub(super) fn reply_row(d: &Dialogue, turn: &str) -> Value {
    rows(d)
        .into_iter()
        .find(|r| r["turn_id"] == format!("{turn}:reply"))
        .expect("配送済みの発話が履歴へ残ること")
}

pub(super) fn pending_reply(d: &Dialogue) -> Value {
    let mut data = d.data.lock().unwrap();
    let s = data.sessions.get_mut(SESSION).unwrap();
    s.current_turn = TURN.into();
    let instance = s.instance_id.clone();
    let epoch = s.epoch;
    data.rows
        .push_back(json!({"session_id":SESSION,"session_instance":instance,
        "turn_id":TURN,"utterance_id":TURN,"epoch":epoch,"source":"text",
        "player_input_text":QUESTION,"conversation_route":"casual",
        "created_at":"2001-01-01T00:00:00Z","playback_status":"generating"}));
    json!({"text":ANSWER,"spoken_text":ANSWER})
}

pub(super) fn queued_reply(d: &Dialogue) -> Value {
    let result = pending_reply(d);
    assert!(d.update(SESSION, TURN, 0, PlaybackStatus::Queued, Some(&result)));
    result
}

pub(super) fn display_id(d: &Dialogue, turn: &str) -> String {
    d.snapshot(Some(SESSION))["utterances"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["turn_id"] == turn)
        .unwrap()["display_id"]
        .as_str()
        .unwrap()
        .into()
}

pub(super) fn assert_metadata(row: &Value, kind: &str, target: Option<&str>) {
    assert_eq!(
        row["utterance_kind"], kind,
        "台詞やIDの見た目ではなく実際の発話契機を記録する"
    );
    assert_eq!(
        row["reply_to_turn_id"],
        json!(target),
        "返答先は実入力ID。対象なしはnull"
    );
    assert!(
        row["occurred_at"]
            .as_str()
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
            .is_some(),
        "新規記録にはRFC3339の発生時刻が必要: {row}"
    );
}

pub(super) fn assert_time(row: &Value, before: DateTime<Utc>, after: DateTime<Utc>) {
    let text = row["occurred_at"].as_str().expect("occurred_atが必要");
    let time = DateTime::parse_from_rfc3339(text)
        .unwrap()
        .with_timezone(&Utc);
    // 秒精度も許容。生成時刻との取り違えは、配送試験の1.1秒の間隔と古いcreated_atで検出する。
    assert!(
        before.timestamp() <= time.timestamp() && time.timestamp() <= after.timestamp(),
        "入力記録／最初の配送確定時刻を保存する: {text}, {before}..{after}"
    );
}

pub(super) fn assert_no_assistant(d: &Dialogue) {
    assert!(rows(d).iter().all(|r| r["role"] != "assistant"));
    let data = d.data.lock().unwrap();
    assert!(data.sessions[SESSION].history.completed_pairs().is_empty());
    assert!(data.sessions[SESSION].haiku.material_turns.is_empty());
}

pub(super) fn emission() -> crate::haiku_record::Emission {
    // 復元APIに必要な「未編集の元句」だけを既存のテストfixtureから使う。
    let fixture: Value = serde_json::from_str(include_str!(
        "../../python/fixtures/workshop_meaning_cases.json"
    ))
    .unwrap();
    let view = &fixture["contexts"]["grass"];
    let e = &view["emission"];
    serde_json::from_value::<crate::haiku_record::PreparedEmission>(json!({
        "text":e["reading_text"],"surface_text":null,"reading_text":e["reading_text"],
        "lines":e["lines"],"materials":view["materials"],"interpretation":null,"preface":null,
        "biome":null,"structure":null,"time_phase":null,"dimension":null,"event_sequence":null,"route":"haiku"
    })).unwrap().complete(Utc::now()).unwrap()
}

pub(super) fn saved_rows() -> Vec<Value> {
    vec![
        json!({"turn_id":TURN,"role":"user","text":QUESTION,
            "occurred_at":"2020-01-01T00:00:00Z","utterance_kind":"player_input","reply_to_turn_id":null}),
        json!({"turn_id":format!("{TURN}:reply"),"role":"assistant","text":ANSWER,
            "occurred_at":"2020-01-01T00:00:02Z","utterance_kind":"reply","reply_to_turn_id":TURN}),
        json!({"turn_id":"ambient:reply","role":"assistant","text":"朝になったな。",
            "occurred_at":"2020-01-01T00:00:10Z","utterance_kind":"spontaneous","reply_to_turn_id":null}),
        json!({"turn_id":"warning:reply","role":"assistant","text":"朝になったな。",
            "occurred_at":"2020-01-01T00:00:12Z","utterance_kind":"warning","reply_to_turn_id":null}),
    ]
}
