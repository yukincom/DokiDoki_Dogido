//! 見込み: 画像取得と比較を分ければ、未比較を変化と取り違えず、必要な一枚を返答に使える。合格: 三状態の画像保持・変化時だけの通知・比較元と撮影時の向き・一画像の実要求を照合。観測と応答は模擬、実モデルと描画は別確認。
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
    }})
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

// 見込み: 比較不能でも写真は利用でき、景色が変わったとは扱わずに済む。合格: 未比較の画像を受信・保持・通常抽出でき、metadataに比較説明なし、発話機会なし。確認範囲: 模擬画像の実受信から返答用抽出まで。
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
    assert!(metadata.get("scene_comparison").is_none());
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
    let mut changed = receipt(&session.visual, now, 1, "changed");
    super::tests::seed_reference(&mut session.visual, &mut changed);
    session.visual.receive(&changed, now);
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

// 見込み: Javaの比較結果をそのまま受ければ、通信境界で未比較と変化を取り違えない。合格: 3状態で画像を保持、referenceは診断に残り、Rustに比較元が無い時の機会は0回。確認範囲: 合成画素の実比較・符号化・JSON・Rust受信。
#[tokio::test]
async fn acceptance_image_java_protocol_to_rust_receipt_preserves_three_states() {
    let wire_fixture = JavaWireFixture::compile();
    for scenario in ["unchanged", "changed", "unknown"]
    {
        let (dialogue, source) = server();
        let now = Utc::now();
        let input = {
            let data = dialogue.data.lock().unwrap();
            let session = &data.sessions["s"];
            json!({"commands":session.visual.commands(now),"event":session.latest,"captured_at":now,"scenario":scenario})
        };
        let event = wire_fixture.event(&input);
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
            // Java起動の所要時間を撮影命令の期限に含めず、撮影から1秒後の受信を検証する。
            assert!(
                session
                    .visual
                    .receive(&event.command_results[0], now + chrono::Duration::seconds(1))
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
                false,
                "{scenario}: Javaだけにある比較元では変化を通知しない"
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

// Javaの準備は模擬の身体観測・撮影命令より先に済ませる。各試験の三場面で同じクラスを使う。
struct JavaWireFixture {
    classes: std::path::PathBuf,
    launch: Vec<String>,
}

impl JavaWireFixture {
    fn compile() -> Self {
        let classes = std::env::temp_dir().join(format!("dogido-image-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&classes).expect("画像試験用のクラス置き場を作成");
        let mut fixture = Self { classes, launch: Vec::new() };
        let output = std::process::Command::new("python3")
            .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/acceptance/java_wire.py"))
            .arg("--compile-to")
            .arg(&fixture.classes)
            .output()
            .expect("Java境界のコンパイラを起動");
        assert!(output.status.success(), "Java境界の事前ビルド: {}", String::from_utf8_lossy(&output.stderr));
        fixture.launch = serde_json::from_slice(&output.stdout).expect("Java fixtureの起動引数");
        fixture
    }

    // Javaが符号化した比較元の画素を、現在とは別の撮影ACKとして4回受ける。
    fn previous_captures(&self, input: &Value, now: DateTime<Utc>) -> VecDeque<Arc<Frame>> {
        let mut earlier = input.clone();
        earlier["scenario"] = json!("unknown");
        earlier["after_path"] = earlier["before_path"].clone();
        let event = self.event(&earlier);
        let mut past = State {enabled:true, ..Default::default()};
        for i in 0..4 {
            let at = now - chrono::Duration::seconds(4-i);
            past.arm("s", Some(context()), at);
            let mut receipt = event.command_results[0].clone();
            receipt.command_id = past.commands(at)[0]["command_id"].as_str().unwrap().into();
            let v = &mut receipt.body_view.as_mut().unwrap().0;
            v["capture_id"] = json!(format!("view_00000000-0000-0000-0000-{:012}",100+i));
            // Instant.toString()で書かれたJavaの表現（Z、3桁の組単位）に揃える。
            v["captured_at"] = json!(at.to_rfc3339_opts(chrono::SecondsFormat::AutoSi, true));
            v["motion_sequence"] = json!(i+1);
            past.receive(&receipt, at);
        }
        assert_eq!(past.history.len(), 4);
        past.history
    }

    fn event(&self, input: &Value) -> crate::events::GameEvent {
        use std::io::Write;
        use std::process::{Command, Stdio};
        let (java, args) = self.launch.split_first().expect("Java fixtureの実行ファイル");
        let mut child = Command::new(java)
            .args(args)
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
            "Java境界: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        crate::events::GameEvent::parse(serde_json::from_slice(&output.stdout).unwrap()).unwrap()
    }
}

impl Drop for JavaWireFixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.classes);
    }
}

// 見込み: 矛盾した比較情報を拒否すれば、誤った参照やマスで変化を説明しない。合格: 比較情報の不正時は検証済みの今回画像だけを保持し、変化は通知しない。確認範囲: 実Rust受信、Javaの計測精度はgolden照合で別確認。
#[test]
fn acceptance_image_invalid_comparison_preserves_only_current_png() {
    for fault in [
        "reference_missing",
        "future",
        "same_capture",
        "id",
        "hash",
        "unexpected_cells",
        "unknown_status",
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
            "unexpected_cells" => c["cells"] = json!([]),
            "unknown_status" => c["status"] = json!("unknown"),
            "not_compared_reference" => {
                c["status"] = json!("not_compared");
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
            matches!(state.latest.as_ref().unwrap().image.frames, ComparedFrames::NotCompared),
            "{fault}: 今回の画像に不正な比較元を結びつけない"
        );
        assert!(state.latest.as_ref().unwrap().image.scene_comparison.is_none(), "{fault}");
        assert!(!state.ready, "{fault}");
    }
}

// 見込み: Dの同じ照合結果を三経路に渡せば、比較元欠落も含め必要な1〜2枚だけが同じ一回生成へ届く。
// 合格: 通常・先行音声・環境の実HTTP要求で枚数と順序、相対時刻・枠・共通文を照合。画像側の台帳なし。Java入力と返答は固定、実Gemma・発声は別確認。
#[tokio::test]
async fn acceptance_image_three_routes_send_verified_frames_in_one_generation() {
    let wire_fixture = JavaWireFixture::compile();
    let sent = Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));
    let recorded = sent.clone();
    let app = axum::Router::new().route("/v1/chat/completions", axum::routing::post(move |axum::Json(v): axum::Json<Value>| {
        let streaming = v["stream"] == true;
        recorded.lock().unwrap().push(v);
        async move {
            use axum::response::IntoResponse;
            if streaming {
                let chunk = json!({"id":"fixture","object":"chat.completion.chunk","created":0,"model":"fixture",
                    "choices":[{"index":0,"delta":{"role":"assistant","content":"{\"action\":\"silent\",\"speech\":\"\"}"},"finish_reason":null}]});
                let end = json!({"id":"fixture","object":"chat.completion.chunk","created":0,"model":"fixture",
                    "choices":[{"index":0,"delta":{},"finish_reason":"stop"}]});
                ([("content-type", "text/event-stream")], format!("data: {chunk}\n\ndata: {end}\n\ndata: [DONE]\n\n")).into_response()
            } else {
                axum::Json(json!({"id":"fixture","object":"chat.completion","created":0,"model":"fixture",
                    "choices":[{"index":0,"message":{"role":"assistant","content":"{\"action\":\"silent\",\"speech\":\"\"}"},"finish_reason":"stop"}]})).into_response()
            }
        }
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
        ("missing", "not_compared"),
    ] {
        let (d, source) = server();
        let now = Utc::now();
        let input = {
            let data = d.data.lock().unwrap();
            let s = &data.sessions["s"];
            json!({"commands":s.visual.commands(now),"event":s.latest,"captured_at":now,"scenario":if scenario == "missing" {"changed"} else {scenario},
                "before_path":base.join(if scenario == "unchanged" {"after-native.png"} else {"before-native.png"}),
                "after_path":base.join("after-native.png")})
        };
        let received = wire_fixture.event(&input);
        let past = if matches!(scenario, "unknown" | "missing") { VecDeque::new() } else { wire_fixture.previous_captures(&input, now) };
        let (normal, action) = {
            let mut data = d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            s.visual.history = past;
            // 期限そのものの検査はvisual_runtimeの単体試験に任せる。
            s.visual.receive(&received.command_results[0], now + chrono::Duration::seconds(1));
            let image = &s.visual.latest.as_ref().expect("実画像を保持").image;
            if scenario != "missing" { assert_eq!(json!(image.scene_comparison)["status"], status); assert_eq!(
                json!(image.scene_comparison),
                json!(serde_json::from_value::<crate::chat_image::SceneComparison>(received.command_results[0].body_view.as_ref().unwrap().0["scene_comparison"].clone()).unwrap())
            ); }
            match (&image.frames, scenario) {
                (ComparedFrames::Changed { previous }, "changed") => {
                    assert_eq!(previous.sha256, image.scene_comparison.as_ref().unwrap().reference.as_ref().unwrap().sha256);
                    assert_ne!(previous.png_base64, image.current.png_base64);
                }
                (ComparedFrames::Unchanged, "unchanged") | (ComparedFrames::NotCompared, "unknown" | "missing") => {}
                _ => panic!("{scenario}: 比較元の実画像と今回画像を照合"),
            }
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
        super::super::chat_leaf_runtime::render_image(normal.clone(), &llm, &mut cancel, Some(&image), None)
            .await
            .unwrap();
        {
            let mut data = d.data.lock().unwrap();
            let session = data.sessions.get_mut("s").unwrap();
            session.delivery.enabled = true;
            session.delivery.voice_enabled = true;
            data.rows.push_back(json!({"session_id":"s","turn_id":"acceptance-turn","epoch":7,"playback_status":"generating"}));
        }
        let stream = super::super::speech_stream_runtime::Source::new(&d, "s", "acceptance-turn", 7).unwrap();
        stream.render(normal, None, &llm, Some(&image), vec![], Value::Null, &mut cancel).await.unwrap();
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
            assert_eq!(requests.len(), 3, "各経路一回だけ送信");
            for (route, request) in ["normal", "stream", "environment"].into_iter().zip(requests.iter()) {
                let messages = request["messages"].as_array().unwrap();
                let images: Vec<_> = messages
                    .iter()
                    .filter_map(|m| m["content"].as_array())
                    .flatten()
                    .filter(|part| part["type"] == "image_url")
                    .collect();
                let expected: Vec<_> = match &image.frames {
                    ComparedFrames::Changed { previous } => vec![previous.as_ref(), image.current.as_ref()],
                    _ => vec![image.current.as_ref()],
                };
                assert_eq!(images.len(), expected.len(), "{scenario}/{route}");
                for (part, frame) in images.iter().zip(&expected) {
                    assert_eq!(part["image_url"]["url"], frame.data_url());
                }
                assert_eq!(request["stream"], route == "stream");
                let content = messages.last().unwrap()["content"].as_array().unwrap();
                let image_text = content.iter().skip(1).filter_map(|part| part["text"].as_str()).collect::<Vec<_>>().join("\n");
                assert_eq!(image_text.matches("変わった").count(), usize::from(status == "changed"));
                assert_eq!(image_text.matches("変わらない").count(), usize::from(status == "unchanged"));
                assert_eq!(image_text.matches("景色は古い順").count(), usize::from(status == "changed"));
                assert_eq!(image_text.matches("緑のPLAYER枠").count(), expected.iter().filter(|frame| frame.has_player_box()).count());
                if status == "changed" {
                    let labels: Vec<_> = content.iter().enumerate().filter(|(_,part)| part["type"] == "image_url")
                        .map(|(index,_)| content[index-1]["text"].as_str().unwrap().lines().next().unwrap()).collect();
                    // Java fixtureは1秒刻みの4枚前。実際の4秒間隔を送り、周期既定値で置き換えない。
                    assert_eq!(labels, ["00:00", "00:04"]);
                }
                for forbidden in ["image_observation", "sha256", "captured_at", "annotation_status", "capture_id", "image/png", "横8", "行目", "scene_comparison"] {
                    assert!(!image_text.contains(forbidden), "{scenario}/{route}: {forbidden}");
                }
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
                assert!(!all_text.contains("image_observation:"));
                assert!(!all_text.contains("画像を横8×縦6"));
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
                        dir.join(format!("part5-{scenario}-{route}.json")),
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
