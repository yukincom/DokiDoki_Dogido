//! 見込み: 画像取得と比較を分ければ、未比較を変化と取り違えず、必要な一枚を返答に使える。合格: 三状態の画像保持・変化時だけの通知・比較元と位置説明・一画像の実要求を照合。観測と応答は模擬、実モデルと描画は別確認。
use super::*;

const OWNER: &str = "11111111-1111-4111-8111-111111111111";

fn context() -> Context {
    serde_json::from_value(crate::body::tests::fixture()["sample"]["context"].clone()).unwrap()
}
fn receipt(state: &State, now: DateTime<Utc>, sequence: u64, status: &str) -> AdapterCommandResult {
    let command = &state.commands(now)[0];
    let image = crate::chat_image::tests::image();
    let context = context();
    serde_json::from_value(json!({"command_id":command["command_id"],"command_type":"capture_body_view",
        "status":"succeeded","executed_at":now,"body_view":{
        "body_entity_uuid":context.body_id,"body_epoch":context.epoch,"owner_uuid":context.owner_id,"dimension":context.dimension,
        "camera_restored":true,"png_base64":image.png_base64,"sha256":image.sha256,"width":image.width,"height":image.height,
        "captured_at":now,"annotation_status":"available","capture_id":if command["after_look"].is_object() {command["command_id"].clone()} else {json!(format!("view_{}",uuid::Uuid::new_v4()))},
        "motion_sequence":sequence,"scene_comparison":comparison(status, now)}})).unwrap()
}
fn comparison(status: &str, now: DateTime<Utc>) -> Value {
    json!({"status":status,"reference":if status == "not_compared" {Value::Null} else {
        json!({"capture_id":"view_00000000-0000-0000-0000-000000000100",
            "captured_at":now-chrono::Duration::seconds(4),"sha256":"b".repeat(64)})
    },"cells":if status == "changed" {json!([
        {"row":5,"column":3,"local_change":true,"brightness":"none"},
        {"row":6,"column":2,"local_change":true,"brightness":"none"},
        {"row":6,"column":3,"local_change":true,"brightness":"none"}
    ])} else {json!([])}})
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

// 見込み: 比較不能でも写真は利用でき、景色が変わったとは扱わずに済む。合格: 未比較の画像を受信・保持・通常抽出でき、metadataは未比較、発話機会なし。確認範囲: 模擬画像の実受信から返答用抽出まで。
#[tokio::test]
async fn acceptance_image_uncompared_is_usable_in_ordinary_reply_without_change_opportunity() {
    let (dialogue, source) = server();
    {
        let mut data = dialogue.data.lock().unwrap();
        let session = data.sessions.get_mut("s").unwrap();
        let now = Utc::now();
        let result = receipt(&session.visual, now, 1, "not_compared");
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
    assert_eq!(
        metadata["scene_comparison"],
        "画像は取得したが、比較できていない"
    );
    assert!(metadata.get("scene_change").is_none());
    assert_eq!(metadata["captured_at"], captured.image.captured_at);
}

// 見込み: 定期取得を変化の証拠と分ければ、同じ場面で発話機会を繰り返さない。合格: 12秒間隔の5画像が順に最新へ入り、readyは全回false。確認範囲: 時刻を進めた受信試験、実時間の撮影・発話は別確認。
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
        let result = receipt(&state, now, n as u64 + 1, "not_compared");
        assert!(state.receive(&result, now));
        assert!(!state.ready, "未比較の12秒周期を変化通知にしない: {n}");
        assert_eq!(
            state.latest.as_ref().map(|v| v.sequence),
            Some(n as u64 + 1),
            "新しい画像自体は更新する: {n}"
        );
    }
}

// 見込み: 比較済みの変化だけを通知すれば、無変化画像も使いながら重複契機を防げる。合格: changedは画像付き機会を1回、次のunchangedは最新を更新して機会0回。確認範囲: 実受信・消費、実発話は別確認。
#[tokio::test]
async fn acceptance_image_compared_change_emits_once_and_unchanged_still_updates_cache_control() {
    let (dialogue, _) = server();
    let mut data = dialogue.data.lock().unwrap();
    let session = data.sessions.get_mut("s").unwrap();
    let now = Utc::now();
    session
        .visual
        .receive(&receipt(&session.visual, now, 1, "changed"), now);
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
        .receive(&receipt(&session.visual, now, 2, "unchanged"), now);
    assert_eq!(session.visual.latest.as_ref().unwrap().sequence, 2);
    assert!(opportunity(session).is_none());
}

// 見込み: 明示撮影をその返答へ保持すれば、身体が動いても先に撮った画像を使える。合格: 撮影後の位置・向き変更と観測欠落の両方で同じcaptureを取得でき、自発機会は0回。確認範囲: 模擬姿勢と実保持、実移動は別確認。
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
            .receive(&receipt(&session.visual, now, 1, "changed"), now);
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

// 見込み: Javaの比較結果をそのまま受ければ、通信境界で未比較と変化を取り違えない。合格: 3状態で画像を保持、referenceとcellsが受信前後で一致し、changedだけ機会1回。確認範囲: 合成画素の実比較・符号化・JSON・Rust受信。
#[tokio::test]
async fn acceptance_image_java_protocol_to_rust_receipt_preserves_three_states() {
    for (scenario, expected_change) in [("unchanged", false), ("changed", true), ("unknown", false)]
    {
        let (dialogue, source) = server();
        let now = Utc::now();
        let input = {
            let data = dialogue.data.lock().unwrap();
            let session = &data.sessions["s"];
            json!({"commands":session.visual.commands(now),"event":session.latest,"captured_at":now,"scenario":scenario})
        };
        let event = java_event(&input);
        assert_eq!(event.command_results.len(), 1);
        let wire = &event.command_results[0].body_view.as_ref().unwrap().0["scene_comparison"];
        assert_eq!(
            wire["status"],
            if scenario == "unknown" {
                "not_compared"
            } else {
                scenario
            }
        );
        if scenario == "unknown" {
            assert!(wire["reference"].is_null());
        } else {
            assert_eq!(
                wire["reference"]["capture_id"],
                "view_00000000-0000-0000-0000-000000000100"
            );
        }
        {
            let mut data = dialogue.data.lock().unwrap();
            let session = data.sessions.get_mut("s").unwrap();
            assert!(
                receive_java_result(session, &event.command_results[0], scenario, "synthetic")
            );
            assert!(
                session.visual.latest.is_some(),
                "{scenario}: 実際に符号化した画像が保持される"
            );
            assert_eq!(
                json!(
                    session
                        .visual
                        .latest
                        .as_ref()
                        .unwrap()
                        .image
                        .scene_comparison
                ),
                json!(
                    serde_json::from_value::<crate::chat_image::SceneComparison>(wire.clone())
                        .unwrap()
                )
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

fn java_event(input: &Value) -> crate::events::GameEvent {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let started = Instant::now();
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
    if std::env::var("DOGIDO_ACCEPTANCE_TIMING").as_deref() == Ok("1") {
        eprint!("{}", String::from_utf8_lossy(&output.stderr));
        eprintln!("IMAGE_TIMING {}", json!({
            "stage":"java_event", "scenario":input["scenario"],
            "image_kind":if input.get("before_path").is_some() {"stored_png"} else {"synthetic"},
            "subprocess_ms":started.elapsed().as_secs_f64()*1000.0,
        }));
    }
    assert!(
        output.status.success(),
        "Java境界: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    crate::events::GameEvent::parse(serde_json::from_slice(&output.stdout).unwrap()).unwrap()
}

// 見込み: Javaの準備時間と受信時の期限残量を記録すれば、画像欠損と期限切れを区別できる。
// 合格条件は既存assertのまま。実時刻・本体の期限・画像を変えず、テストの受信結果だけ計測する。
fn receive_java_result(session: &mut Session, result: &AdapterCommandResult, scenario: &str, image_kind: &str) -> bool {
    let now = Utc::now();
    let pending = session.visual.pending.as_ref().map(|p| (p.issued, p.expires));
    let started = Instant::now();
    let acknowledged = session.visual.receive(result, now);
    if std::env::var("DOGIDO_ACCEPTANCE_TIMING").as_deref() == Ok("1") {
        eprintln!("IMAGE_TIMING {}", json!({
            "stage":"rust_receive", "scenario":scenario, "image_kind":image_kind,
            "receive_ms":started.elapsed().as_secs_f64()*1000.0,
            "command_age_ms":pending.map(|(issued,_)| (now-issued).num_milliseconds()),
            "deadline_remaining_ms":pending.map(|(_,expires)| (expires-now).num_milliseconds()),
            "request_expired":pending.map(|(_,expires)| now>=expires),
            "acknowledged":acknowledged, "image_kept":session.visual.latest.is_some(),
        }));
    }
    acknowledged
}

// 見込み: 矛盾した比較情報を拒否すれば、誤った参照やマスで変化を説明しない。合格: 参照・時刻・ID・hash・状態・範囲・重複・無判定マスの各不正で画像保持なし。確認範囲: 実Rust受信、Javaの計測精度はgolden照合で別確認。
#[test]
fn acceptance_image_invalid_comparison_never_enters_latest_cache() {
    for fault in [
        "reference_missing",
        "future",
        "same_capture",
        "id",
        "hash",
        "row",
        "column",
        "duplicate",
        "empty_changed",
        "empty_cell",
        "unknown_status",
        "unchanged_cells",
        "not_compared_reference",
        "legacy_only",
    ] {
        let now = Utc::now();
        let mut state = State {
            enabled: true,
            ..Default::default()
        };
        state.arm(
            "s",
            Some(context()),
            now - chrono::Duration::milliseconds(1),
        );
        let mut result = receipt(&state, now, 1, "changed");
        let view = &mut result.body_view.as_mut().unwrap().0;
        let capture_id = view["capture_id"].clone();
        let c = &mut view["scene_comparison"];
        match fault {
            "reference_missing" => c["reference"] = Value::Null,
            "future" => c["reference"]["captured_at"] = json!(now + chrono::Duration::seconds(1)),
            "same_capture" => c["reference"]["capture_id"] = capture_id,
            "id" => c["reference"]["capture_id"] = json!("not-a-capture"),
            "hash" => c["reference"]["sha256"] = json!("z".repeat(64)),
            "row" => c["cells"][0]["row"] = json!(7),
            "column" => c["cells"][0]["column"] = json!(0),
            "duplicate" => c["cells"][1] = c["cells"][0].clone(),
            "empty_changed" => c["cells"] = json!([]),
            "empty_cell" => c["cells"][0]["local_change"] = json!(false),
            "unknown_status" => c["status"] = json!("unknown"),
            "unchanged_cells" => c["status"] = json!("unchanged"),
            "not_compared_reference" => {
                c["status"] = json!("not_compared");
                c["cells"] = json!([]);
            }
            "legacy_only" => {
                view.as_object_mut().unwrap().remove("scene_comparison");
                view["visual_change"] = json!(true);
                view["compared_to_four_before"] = json!(true);
            }
            _ => unreachable!(),
        }
        assert!(state.receive(&result, now));
        assert!(
            state.latest.is_none(),
            "{fault}: ACKを画像の受理成功と数えない"
        );
        assert!(!state.ready, "{fault}");
    }
}

// 見込み: 行列から説明を作れば、検出位置を余分な領域へ広げず明暗も伝えられる。合格: L字3マスの行・列が一致し、明暗混在は両方を表示。比較元ID・hash・率・強度を説明に混ぜない。確認範囲: 実説明生成、画像の意味判断は別確認。
#[test]
fn acceptance_image_location_description_keeps_exact_cells_and_both_brightness_signs() {
    let now = Utc::now();
    let plain: crate::chat_image::SceneComparison =
        serde_json::from_value(comparison("changed", now)).unwrap();
    assert_eq!(
        plain.description(),
        "画像を横8×縦6に分けた位置。行は上から、列は左から数える。\n画面の左側、下部にある範囲。上から5行目は左から3列目、上から6行目は左から2〜3列目。\n画像の明るさの変化は検出していない。"
    );
    let mut mixed = comparison("changed", now);
    mixed["cells"][0]["brightness"] = json!("lighter");
    mixed["cells"][1]["brightness"] = json!("darker");
    let mixed: crate::chat_image::SceneComparison = serde_json::from_value(mixed).unwrap();
    let text = mixed.description();
    assert!(text.ends_with("明るくなった領域と暗くなった領域をそれぞれ検出した。"));
    for forbidden in [
        "view_",
        "bbbbbbbb",
        "local_top_mean",
        "fraction",
        "%",
        "強度",
    ] {
        assert!(!text.contains(forbidden));
    }
}

// 見込み: 本体の一画像経路を通せば、比較の説明を重ねず同じ画像を通常・環境で使える。合格: 三状態各2要求に現在PNG1枚と説明1回、比較元は内部に保持。確認範囲: 実画像→Java→Rust→模擬HTTP。無変化時の環境契機と全応答は固定、意味理解は未判定。
#[tokio::test]
async fn acceptance_image_both_model_routes_send_one_current_image_and_one_comparison() {
    let sent = Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));
    let recorded = sent.clone();
    let app = axum::Router::new().route("/v1/chat/completions", axum::routing::post(move |axum::Json(v): axum::Json<Value>| {
        recorded.lock().unwrap().push(v);
        async { axum::Json(json!({"id":"fixture","object":"chat.completion","created":0,"model":"fixture",
            "choices":[{"index":0,"message":{"role":"assistant","content":"{\"action\":\"silent\",\"speech\":\"\"}"},"finish_reason":"stop"}]})) }
    }));
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let llm = crate::llm::RigLlm::new(
        &format!("http://{}/v1", socket.local_addr().unwrap()),
        None,
        Duration::from_secs(5),
    )
    .unwrap();
    let http = tokio::spawn(async move {
        axum::serve(socket, app).await.unwrap();
    });
    let base = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("docs/research/2026-10-29-image-change-handoff/image-review/grid8x6-v1/pair_06");
    for (scenario, status) in [
        ("unknown", "not_compared"),
        ("unchanged", "unchanged"),
        ("changed", "changed"),
    ] {
        let (d, source) = server();
        let now = Utc::now();
        let input = {
            let data = d.data.lock().unwrap();
            let s = &data.sessions["s"];
            json!({"commands":s.visual.commands(now),"event":s.latest,"captured_at":now,"scenario":scenario,
                "before_path":base.join(if scenario == "unchanged" {"after-native.png"} else {"before-native.png"}),
                "after_path":base.join("after-native.png")})
        };
        let received = java_event(&input);
        let (normal, action) = {
            let mut data = d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            receive_java_result(s, &received.command_results[0], scenario, "stored_png");
            let image = &s.visual.latest.as_ref().expect("実画像を保持").image;
            assert_eq!(json!(image.scene_comparison)["status"], status);
            assert_eq!(
                json!(image.scene_comparison),
                json!(serde_json::from_value::<crate::chat_image::SceneComparison>(received.command_results[0].body_view.as_ref().unwrap().0["scene_comparison"].clone()).unwrap())
            );
            let native = chat_context::capture(
                s,
                s.latest.as_ref().unwrap(),
                &d.config.combat,
                Default::default(),
                None,
            )
            .unwrap();
            let player = crate::chat_materials::PlayerInput {
                raw_text: "今の景色はどんな感じ？".into(),
                semantic_text: "今の景色はどんな感じ？".into(),
                ..Default::default()
            };
            let crate::chat_materials::Prepared::Reply(reply) = crate::chat_materials::prepare(
                native.sensory_event.as_ref().unwrap(),
                &native.settings,
                &player,
                &native.context,
                &native.snapshot,
                &chat_context::CatalogLabels,
            )
            .unwrap() else {
                panic!("通常一回生成");
            };
            // 未比較・無変化では画像契機がないので、既存の安堵反応を試験の契機として与える。
            let mut action = opportunity(s)
                .unwrap_or_else(|| crate::combat::model::Speech::new("reassurance_notice", ""));
            assert!(opportunity(s).is_none());
            action.delivery = crate::combat::model::Delivery::Ambient;
            crate::environment::reaction::attach(
                &mut action,
                &super::super::body_runtime::event(s, s.latest.as_ref().unwrap()),
                &d.config.combat,
            );
            assert!(super::super::environment_runtime::refresh_dialogue_context(
                s,
                std::slice::from_mut(&mut action)
            ));
            (reply.request("fixture", 256).unwrap(), action)
        };
        let (_tx, mut cancel) = watch::channel(false);
        let image = source
            .capture(&mut cancel)
            .await
            .expect("同じ現在画像")
            .image;
        assert_eq!(image.sha256, action.image.as_ref().unwrap().sha256);
        super::super::chat_leaf_runtime::render_image(normal, &llm, &mut cancel, Some(&image))
            .await
            .unwrap();
        let config = super::super::DialogueConfig {
            model: "fixture".into(),
            reading_engine: "off".into(),
            ..Default::default()
        };
        let env_input = json!({"kind":"environment_reaction","model":"fixture","max_tokens":256,"fallback_text":"","details":action.leaf.as_ref().unwrap().details});
        super::super::reaction_runtime::render_image(
            &config,
            &super::super::tts_runtime::Dictionary::default(),
            &llm,
            &env_input,
            &mut cancel,
            action.image.as_deref(),
        )
        .await
        .unwrap();
        {
            let mut requests = sent.lock().unwrap();
            assert_eq!(requests.len(), 2, "各経路一回だけ送信");
            for (route, request) in ["normal", "environment"].into_iter().zip(requests.iter()) {
                let messages = request["messages"].as_array().unwrap();
                let images: Vec<_> = messages
                    .iter()
                    .filter_map(|m| m["content"].as_array())
                    .flatten()
                    .filter(|part| part["type"] == "image_url")
                    .collect();
                assert_eq!(images.len(), 1);
                assert_eq!(
                    images[0]["image_url"]["url"],
                    format!("data:image/png;base64,{}", image.png_base64)
                );
                let text = messages.last().unwrap()["content"][0]["text"]
                    .as_str()
                    .unwrap();
                let all_text = messages
                    .iter()
                    .flat_map(|m| match &m["content"] {
                        Value::String(value) => vec![value.as_str()],
                        Value::Array(parts) => parts
                            .iter()
                            .filter(|part| part["type"] == "text")
                            .filter_map(|part| part["text"].as_str())
                            .collect(),
                        _ => Vec::new(),
                    })
                    .collect::<Vec<_>>()
                    .join("\n");
                assert_eq!(all_text.matches("image_observation: ").count(), 1);
                let encoded_description =
                    json!(image.scene_comparison.as_ref().unwrap().description()).to_string();
                assert_eq!(
                    all_text.matches(&encoded_description).count(),
                    1,
                    "説明本文も要求全体で一回"
                );
                let (context, metadata) = text.split_once("\n\nimage_observation: ").unwrap();
                let metadata: Value = serde_json::from_str(metadata).unwrap();
                assert_eq!(
                    metadata["scene_comparison"],
                    image.scene_comparison.as_ref().unwrap().description()
                );
                assert!(!context.contains("scene_comparison"));
                assert!(!all_text.contains("view_00000000-0000-0000-0000-000000000100"));
                for forbidden in [
                    "pixel_mean_difference",
                    "pixel_changed_fraction",
                    "local_top_mean",
                    "compared_to_four_before",
                ] {
                    assert!(!all_text.contains(forbidden));
                }
                assert_eq!(
                    messages[0]["content"]
                        .as_str()
                        .unwrap()
                        .matches(crate::companion_prompt::chat_image())
                        .count(),
                    1
                );
                if let Some(dir) = std::env::var_os("DOGIDO_ACCEPTANCE_PAYLOAD_DIR") {
                    let dir = std::path::PathBuf::from(dir);
                    std::fs::create_dir_all(&dir).unwrap();
                    std::fs::write(
                        dir.join(format!("part5-{status}-{route}.json")),
                        serde_json::to_vec_pretty(request).unwrap(),
                    )
                    .unwrap();
                }
            }
            requests.clear();
        }
        d.shutdown().await;
    }
    http.abort();
}
