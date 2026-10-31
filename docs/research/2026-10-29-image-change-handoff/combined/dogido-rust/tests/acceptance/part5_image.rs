//! 入力契約の受入試験。撮影の取得と差分の証拠を分け、実際の画像受信・会話抽出へ通す。
//! 未比較の画素を保管することと、自発的な変化通知を出さないことを同時に要求する。
use super::*;

const OWNER: &str = "11111111-1111-4111-8111-111111111111";

fn context() -> Context {
    serde_json::from_value(crate::body::tests::fixture()["sample"]["context"].clone()).unwrap()
}
fn receipt(
    state: &State,
    now: DateTime<Utc>,
    sequence: u64,
    changed: bool,
    compared: bool,
) -> AdapterCommandResult {
    let command = &state.commands(now)[0];
    let image = crate::chat_image::tests::image();
    let context = context();
    serde_json::from_value(json!({"command_id":command["command_id"],"command_type":"capture_body_view",
        "status":"succeeded","executed_at":now,"body_view":{
        "body_entity_uuid":context.body_id,"body_epoch":context.epoch,"owner_uuid":context.owner_id,"dimension":context.dimension,
        "camera_restored":true,"png_base64":image.png_base64,"sha256":image.sha256,"width":image.width,"height":image.height,
        "captured_at":now,"annotation_status":"available","capture_id":command["command_id"],
        "motion_sequence":sequence,"visual_change":changed,"compared_to_four_before":compared}})).unwrap()
}
fn server() -> (Arc<Dialogue>, Source) {
    let mut config = super::super::DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        ..Default::default()
    };
    config.chat_image.enabled = true;
    config.chat_image.wait_ms = 100;
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    let dialogue = Dialogue::new(config).unwrap();
    dialogue.register("s", "test", false);
    dialogue.bind_body_owner("s", Some(OWNER));
    dialogue.set_execution_capabilities("s", &[CAPABILITY.into(), AFTER_LOOK_CAPABILITY.into()]);
    let mut body = crate::body::tests::fixture();
    body["age_ms"] = json!(0);
    let event = crate::events::GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"acceptance","observed_at":Utc::now(),"sequence":1,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20},
        "world":{"time_phase":"day","local_light":15,"danger_darkness_score":0},"body_observation":body})).unwrap();
    dialogue.observe("s", event, None);
    {
        let mut data = dialogue.data.lock().unwrap();
        let session = data.sessions.get_mut("s").unwrap();
        session.current_turn = "acceptance-turn".into();
        session.epoch = 7;
    }
    let source = Source::new(&dialogue, "s", "acceptance-turn", 7).unwrap();
    (dialogue, source)
}

// 未比較の画像を受信する。最新画像として通常返答へ届き、自発的な変化通知を作らない。
#[tokio::test]
async fn acceptance_image_uncompared_is_usable_in_ordinary_reply_without_change_opportunity() {
    let (dialogue, source) = server();
    {
        let mut data = dialogue.data.lock().unwrap();
        let session = data.sessions.get_mut("s").unwrap();
        let now = Utc::now();
        let result = receipt(&session.visual, now, 1, false, false);
        assert!(session.visual.receive(&result, now));
        assert!(
            session.visual.latest.is_some(),
            "未比較でも取得した画像は保持する"
        );
        assert!(
            opportunity(session).is_none(),
            "未比較は景色が変化した証拠ではない"
        );
    }
    let (_sender, mut cancel) = watch::channel(false);
    let captured = source
        .capture(&mut cancel)
        .await
        .expect("通常の返答は未比較の最新画像も使える");
    captured.image.validate().unwrap();
    let metadata: Value = serde_json::from_str(
        captured
            .image
            .metadata_text()
            .strip_prefix("image_observation: ")
            .unwrap(),
    )
    .unwrap();
    assert_eq!(metadata["scene_change"], "not_compared");
    assert_eq!(metadata["scene_comparison"]["visual_change"], false);
    assert_eq!(metadata["captured_at"], captured.image.captured_at);
}

// 12秒間隔で未比較画像が5回来る。画像だけを更新し、変化通知を追加しない。
#[test]
fn acceptance_image_twelve_second_uncompared_refreshes_never_report_scene_change() {
    let start = Utc::now() - chrono::Duration::seconds(48);
    let mut state = State {
        enabled: true,
        ..Default::default()
    };
    for n in 0..5 {
        let now = start + chrono::Duration::seconds(n * 12);
        state.arm(
            "s",
            Some(context()),
            now - chrono::Duration::milliseconds(1),
        );
        let result = receipt(&state, now, n as u64 + 1, false, false);
        assert!(state.receive(&result, now));
        assert!(!state.ready, "未比較の12秒周期を変化通知にしない: {n}");
        assert_eq!(
            state.latest.as_ref().map(|v| v.sequence),
            Some(n as u64 + 1),
            "新しい画像自体は更新する: {n}"
        );
    }
}

// 比較済み差分の次に同等画像が来る。差分だけ一度通知し、同等画像もキャッシュへ残す。
#[tokio::test]
async fn acceptance_image_compared_change_emits_once_and_unchanged_still_updates_cache_control() {
    let (dialogue, _) = server();
    let mut data = dialogue.data.lock().unwrap();
    let session = data.sessions.get_mut("s").unwrap();
    let now = Utc::now();
    session
        .visual
        .receive(&receipt(&session.visual, now, 1, true, true), now);
    let action = opportunity(session).expect("比較済みの差分には既存の発話機会がある");
    assert!(action.image.is_some());
    assert_eq!(
        action.leaf.as_ref().unwrap().details["visual_change_trigger"],
        true
    );
    assert!(opportunity(session).is_none());
    session.visual.arm("s", Some(context()), Utc::now());
    let now = Utc::now();
    session
        .visual
        .receive(&receipt(&session.visual, now, 2, false, true), now);
    assert_eq!(session.visual.latest.as_ref().unwrap().sequence, 2);
    assert!(opportunity(session).is_none());
}

// 明示撮影後に位置・向きが変化し身体観測も欠ける。取得済み画像は同じ返答で使い続ける。
#[tokio::test]
async fn acceptance_image_explicit_capture_survives_changed_pose_and_missing_body_sample_control() {
    let (dialogue, source) = server();
    let request = {
        let mut data = dialogue.data.lock().unwrap();
        let session = data.sessions.get_mut("s").unwrap();
        let now = Utc::now();
        let look = crate::body::requests::CompletedLook {
            command_id: "look-command".into(),
            context: context(),
            input_generation: session.input_generation,
            executed_at: now,
        };
        let request = session.visual.arm_after_look("s", look, now).unwrap();
        let now = Utc::now();
        session
            .visual
            .receive(&receipt(&session.visual, now, 1, true, true), now);
        assert!(
            opportunity(session).is_none(),
            "明示撮影から自発発話を重ねない"
        );
        // 撮影後に別姿勢へ進んだ後の観測欠落も、取得済み画像の棄却条件にしない。
        let mut moved = crate::body::tests::fixture();
        moved["sample"]["self_pose"]["position"]["x"] = json!(100.0);
        moved["sample"]["self_pose"]["eye_position"]["x"] = json!(100.0);
        moved["sample"]["self_pose"]["yaw"] = json!(90.0);
        moved["age_ms"] = json!(0);
        moved["sample"]["server_tick"] = json!(999);
        moved["sample"]["snapshot_revision"] = json!(999);
        moved["sample"]["senses"]["source_server_tick"] = json!(999);
        let moved = serde_json::from_value(moved).unwrap();
        session.body_observation.observe(
            Some(&moved),
            Some(OWNER),
            Some("minecraft:overworld"),
            Instant::now(),
            Some(0),
        );
        assert_eq!(
            session
                .body_observation
                .accepted(Instant::now())
                .unwrap()
                .self_pose
                .position
                .x,
            100.0
        );
        // 元の撮影姿勢は画像に属し、Sourceは現姿勢を要求しない。
        assert!(captured_look(session, &request));
        request
    };
    let source = source.after_look(Some(request));
    let (_sender, mut cancel) = watch::channel(false);
    assert!(
        source.capture(&mut cancel).await.is_some(),
        "撮影後の位置・向き変更で画像を失わない"
    );
    dialogue
        .data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .body_observation = Default::default();
    assert!(
        source.capture(&mut cancel).await.is_some(),
        "撮影後の一時的な身体観測欠落で画像を失わない"
    );
}

// Java本体で比較した三状態を実通信JSONでRustへ渡す。全画像を保持し、比較済み差分だけが一度の発話機会になる。
#[tokio::test]
async fn acceptance_image_java_protocol_to_rust_receipt_preserves_three_states() {
    use std::io::Write;
    use std::process::{Command, Stdio};
    for (scenario, expected_change) in [("unchanged", false), ("changed", true), ("unknown", false)]
    {
        let (dialogue, source) = server();
        let now = Utc::now();
        let input = {
            let data = dialogue.data.lock().unwrap();
            let session = &data.sessions["s"];
            json!({"commands":session.visual.commands(now),"event":session.latest,"captured_at":now,"scenario":scenario})
        };
        let mut child = Command::new("python3")
            .arg(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/tests/acceptance/java_wire.py"
            ))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(input.to_string().as_bytes())
            .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(
            output.status.success(),
            "Java境界の準備・実行エラー: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let event =
            crate::events::GameEvent::parse(serde_json::from_slice(&output.stdout).unwrap())
                .unwrap();
        assert_eq!(event.command_results.len(), 1);
        {
            let mut data = dialogue.data.lock().unwrap();
            let session = data.sessions.get_mut("s").unwrap();
            assert!(
                session
                    .visual
                    .receive(&event.command_results[0], Utc::now())
            );
            assert!(
                session.visual.latest.is_some(),
                "{scenario}: 実際に符号化した画像が保持される"
            );
            assert_eq!(
                opportunity(session).is_some(),
                expected_change,
                "{scenario}: 未比較は景色の変化ではない"
            );
            assert!(
                opportunity(session).is_none(),
                "同じ画像で発話機会を重複させない"
            );
        }
        let (_sender, mut cancel) = watch::channel(false);
        assert!(
            source.capture(&mut cancel).await.is_some(),
            "{scenario}: 通常返答でも同じ画像を使える"
        );
    }
}
