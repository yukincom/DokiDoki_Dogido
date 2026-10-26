//! 明示の向き変更から一枚の撮影と通常返答への接続を、模擬HTTPで検査する。
use super::*;

fn completed_look(context: Context, generation: u64) -> crate::body::requests::CompletedLook {
    crate::body::requests::CompletedLook {
        command_id: uuid::Uuid::new_v4().to_string(),
        context,
        input_generation: generation,
        executed_at: Utc::now(),
    }
}
#[tokio::test]
async fn look_capture_excludes_old_images_and_is_bound_to_capture_body_input_and_session() {
    for change in [
        "none",
        "moving",
        "unavailable",
        "capture_id",
        "body",
        "generation",
        "session",
        "cancel",
    ] {
        let (d, source) = server(100);
        let (_, mut old, _) = pending();
        let request;
        {
            let mut data = d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            old.command_id = s.visual.commands(Utc::now())[0]["command_id"]
                .as_str()
                .unwrap()
                .into();
            old.body_view.as_mut().unwrap().0["captured_at"] = json!(Utc::now());
            s.visual.receive(&old, Utc::now());
            s.visual.after_look_enabled = true;
            request = s
                .visual
                .arm_after_look(
                    "s",
                    completed_look(s.visual.context.clone().unwrap(), s.input_generation),
                    Utc::now(),
                )
                .unwrap();
            assert!(!s.visual.ready);
        }
        let source = source.after_look(Some(request.clone()));
        let (tx, mut cancel) = watch::channel(false);
        assert!(
            source.capture(&mut cancel).await.is_none(),
            "old cached image must not complete a look"
        );
        {
            let mut data = d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            let mut image = old.clone();
            image.command_id = request.capture_id.clone();
            let view = &mut image.body_view.as_mut().unwrap().0;
            view["captured_at"] = json!(Utc::now());
            view["capture_id"] = json!(request.capture_id);
            view["motion_sequence"] = json!(2);
            match change {
                "capture_id" => {
                    view["capture_id"] = json!(old.body_view.as_ref().unwrap().0["capture_id"])
                }
                "body" => view["body_epoch"] = json!(uuid::Uuid::new_v4().to_string()),
                "generation" => s.input_generation += 1,
                "session" => s.instance_id = "replacement-session".into(),
                "cancel" => tx.send(true).unwrap(),
                _ => (),
            }
            s.visual.receive(&image, Utc::now());
            s.visual.receive(&image, Utc::now());
            if change=="moving" || change=="unavailable" {
                let mut frame=serde_json::to_value(wire(5)).unwrap();
                if change=="moving" {
                    frame["body_observation"]["sample"]["self_pose"]["position"]["x"]=json!(50.0);
                    frame["body_observation"]["sample"]["self_pose"]["view_yaw"]=json!(135.0);
                    frame["body_observation"]["sample"]["self_pose"]["view_pitch"]=json!(45.0);
                } else {frame["body_observation"]=json!({"version":3,"status":"unavailable","reason":"stale_or_missing"});}
                crate::dialogue::body_runtime::observe(s,&crate::events::GameEvent::parse(frame).unwrap());
                s.visual.arm("s",None,Utc::now());
                assert!(s.visual.look.as_ref().unwrap().image.is_some());
            }
            assert!(
                !s.visual.ready,
                "explicit look must not create an extra ambient comment"
            );
        }
        assert_eq!(
            source.capture(&mut cancel).await.is_some(),
            matches!(change,"none"|"moving"|"unavailable"),
            "{change}"
        );
    }
}

#[tokio::test]
async fn look_success_continues_once_with_a_new_image_or_only_acknowledges_on_timeout() {
    for deliver_image in [true, false] {
        let sent = Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));
        let recorded = sent.clone();
        let app = axum::Router::new().route("/v1/chat/completions",axum::routing::post(move |axum::Json(v):axum::Json<Value>| {
            recorded.lock().unwrap().push(v); async {
                tokio::time::sleep(Duration::from_millis(700)).await;
                axum::Json(json!({"id":"fixture","object":"chat.completion","created":0,"model":"fixture",
                "choices":[{"index":0,"message":{"role":"assistant","content":"{\"action\":\"speak\",\"speech\":\"あの木、葉っぱがよう茂っとるな。\"}"},"finish_reason":"stop"}]})) }
        }));
        let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let mut config = crate::dialogue::DialogueConfig {
            base_url: format!("http://{}/v1", socket.local_addr().unwrap()),
            model: "fixture".into(),
            reading_engine: "off".into(),
            audio_enabled: false,
            ..Default::default()
        };
        config.chat_image.enabled = true;
        config.chat_image.wait_ms = if deliver_image { 2000 } else { 100 };
        config.haiku.enabled = false;
        config.haiku.memory_enabled = false;
        let http = tokio::spawn(async move {
            axum::serve(socket, app).await.unwrap();
        });
        // 分類器有効時の再送受付を通す。採用済み分類を注入するのでworker起動は不要。
        let classifier_dir =
            std::env::temp_dir().join(format!("dogido-look-classifier-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&classifier_dir).unwrap();
        std::fs::write(classifier_dir.join("adopted-model.json"),serde_json::to_vec(&json!({
            "schema_version":1,"path_base":"repository_root","classifier_mode":"normal",
            "label_to_id":crate::request_classifier::Mode::Normal.labels().iter().enumerate()
                .map(|(i,label)| (serde_json::to_value(label).unwrap().as_str().unwrap().to_owned(),i)).collect::<std::collections::BTreeMap<_,_>>(),
            "evaluated_input":{"max_history_utterances":0,"max_length":128,"truncation":false}
        })).unwrap()).unwrap();
        config.request_classifier.normal_model_root = Some(classifier_dir.clone());
        config.request_classifier.python = Some("/usr/bin/python3".into());
        let d = Dialogue::new(config).unwrap();
        d.register("s", "試験", false);
        d.bind_body_owner("s", Some(OWNER));
        d.set_execution_capabilities(
            "s",
            &[
                CAPABILITY.into(),
                AFTER_LOOK_CAPABILITY.into(),
                crate::body::requests::CAPABILITY.into(),
            ],
        );
        d.observe("s", wire(1), None);
        let (_, mut image, _) = pending();
        let command;
        let context;
        let turn;
        {
            let mut data = d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            image.command_id = s.visual.commands(Utc::now())[0]["command_id"]
                .as_str()
                .unwrap()
                .into();
            image.body_view.as_mut().unwrap().0["captured_at"] = json!(Utc::now());
            s.visual.receive(&image, Utc::now());
            s.visual.ready = false;
            context = s
                .body_observation
                .accepted(Instant::now())
                .unwrap()
                .context
                .clone();
            s.input_generation = 7;
            let binding = crate::dialogue::request_runtime::Binding::capture(s);
            s.request_classification.restore(
                7,
                binding,
                Some(crate::request_classifier::Classification {
                    mode: crate::request_classifier::Mode::Normal,
                    text: "あの木を見て".into(),
                    source: "text".into(),
                    manifest_sha256: "fixture".into(),
                    prediction: crate::request_classifier::Prediction {
                        label: crate::request_classifier::Label::LookTarget,
                        probabilities: vec![0.0; 16],
                        token_count: 7,
                        inference_ms: 1.0,
                    },
                }),
            );
            let accepted = d
                .body_request_input(&mut data, &mut vec![], "s", "あの木を見て", "text")
                .unwrap();
            turn = accepted["turn_id"].as_str().unwrap().to_owned();
            command = crate::dialogue::body_request_runtime::commands(
                data.sessions.get_mut("s").unwrap(),
            )
            .pop()
            .unwrap();
        }
        let ack = json!({"command_id":command["command_id"],"command_type":"body_request","status":"succeeded","detail_code":"facing_target",
            "executed_at":Utc::now(),"owner_id":context.owner_id,"dimension":context.dimension,"input_generation":7,
            "body_id":context.body_id,"epoch":context.epoch});
        let mut event = serde_json::to_value(wire(2)).unwrap();
        event["command_results"] = json!([ack.clone(), ack.clone()]);
        d.observe("s", crate::events::GameEvent::parse(event).unwrap(), None);
        let capture = d.data.lock().unwrap().sessions["s"]
            .visual
            .commands(Utc::now())
            .pop()
            .unwrap();
        assert_eq!(capture["after_look"]["command_id"], command["command_id"]);
        assert_eq!(capture["after_look"]["input_generation"], 7);
        let duplicate = d.submit(Some("s"), "あの木を見て", "text");
        assert_eq!(duplicate["deduplicated"], true, "{duplicate}");
        assert_eq!(duplicate["turn_id"], turn);
        assert_eq!(d.data.lock().unwrap().sessions["s"].input_generation, 7);

        if deliver_image {
            image.command_id = capture["command_id"].as_str().unwrap().into();
            let view = &mut image.body_view.as_mut().unwrap().0;
            view["captured_at"] = json!(Utc::now());
            view["capture_id"] = capture["command_id"].clone();
            view["motion_sequence"] = json!(2);
            // BodyView intentionally does not serialize its pixels; inject the received wire payload explicitly.
            let mut receipt = serde_json::to_value(&image).unwrap();
            receipt["body_view"] = image.body_view.as_ref().unwrap().0.clone();
            let mut event = serde_json::to_value(wire(3)).unwrap();
            event["command_results"] = json!([receipt, ack]);
            d.observe("s", crate::events::GameEvent::parse(event).unwrap(), None);
            // 模擬LLMへ画像が届き、生成中になってから身体を動かす。その後の観測遅延でも返答を止めない。
            tokio::time::timeout(Duration::from_secs(2),async {
                while sent.lock().unwrap().is_empty() {tokio::time::sleep(Duration::from_millis(5)).await;}
            }).await.unwrap();
            let mut moved=serde_json::to_value(wire(4)).unwrap();
            moved["body_observation"]["sample"]["self_pose"]["position"]["x"]=json!(35.0);
            moved["body_observation"]["sample"]["self_pose"]["view_yaw"]=json!(120.0);
            moved["body_observation"]["sample"]["self_pose"]["view_pitch"]=json!(40.0);
            d.observe("s",crate::events::GameEvent::parse(moved).unwrap(),None);
            let mut data=d.data.lock().unwrap();let s=data.sessions.get_mut("s").unwrap();
            s.received=Some(Instant::now()-Duration::from_secs(11));
        }
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                if d.data
                    .lock()
                    .unwrap()
                    .rows
                    .iter()
                    .any(|r| r["turn_id"] == turn && !r["text"].as_str().unwrap_or("").is_empty())
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        let rows: Vec<Value> = d
            .data
            .lock()
            .unwrap()
            .rows
            .iter()
            .filter(|r| r["turn_id"] == turn)
            .cloned()
            .collect();
        assert_eq!(rows.len(), 1);
        assert_eq!(
            rows[0]["text"],
            if deliver_image {
                "あの木、葉っぱがよう茂っとるな。"
            } else {
                "そっち向いたで。"
            }
        );
        {
            let sent = sent.lock().unwrap();
            assert_eq!(sent.len(), usize::from(deliver_image));
            if deliver_image {
                let content = &sent[0]["messages"].as_array().unwrap().last().unwrap()["content"];
                assert_eq!(
                    content[1]["image_url"]["url"],
                    format!("data:image/png;base64,{PNG}")
                );
                let facts = content[0]["text"].as_str().unwrap();
                assert!(facts.contains("あの木を見て") && facts.contains("向きへの変更を完了した"));
                assert!(
                    facts.contains(
                        image.body_view.as_ref().unwrap().0["captured_at"]
                            .as_str()
                            .unwrap()
                    )
                );
            }
        }
        d.shutdown().await;
        http.abort();
        std::fs::remove_dir_all(classifier_dir).unwrap();
    }
}

#[tokio::test]
async fn look_reply_survives_body_changes_after_capture_and_only_new_input_cancels() {
    for changed in ["body", "input", "pause"] {
        let (d, _) = server(100);
        let (tx, rx) = watch::channel(false);
        {
            let mut data = d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            let context = s
                .body_observation
                .accepted(Instant::now())
                .unwrap()
                .context
                .clone();
            s.cancel = Some(tx);
            let row = json!({"session_id":"s","session_instance":s.instance_id,"turn_id":"turn","epoch":7,
                "playback_status":"generating","look_capture_id":"reserved","look_context":context,"input_generation":s.input_generation});
            match changed {
                "body" => {
                    let mut event = serde_json::to_value(wire(2)).unwrap();
                    event["body_observation"]["sample"]["context"]["epoch"] =
                        json!(uuid::Uuid::new_v4().to_string());
                    crate::dialogue::body_runtime::observe(
                        s,
                        &crate::events::GameEvent::parse(event).unwrap(),
                    );
                }
                "input" => s.input_generation += 1,
                _ => s.foreground.game_paused = true,
            }
            data.rows.push_back(row);
        }
        let accepted=d.update(
                "s",
                "turn",
                7,
                crate::playback::Status::Queued,
                Some(&json!({"text":"旧画像の返答"}))
            );
        assert_eq!(accepted,changed!="input","{changed}");
        assert_eq!(*rx.borrow(),changed=="input");
        let data = d.data.lock().unwrap();
        let row = data.rows.iter().find(|r| r["turn_id"] == "turn").unwrap();
        assert_eq!(row["playback_status"],if changed=="input" {"cancelled"} else {"queued"});
        assert_eq!(row["text"].is_null(),changed=="input");
    }
}

#[tokio::test]
async fn image_missing_ack_checks_input_identity_without_requiring_a_current_body() {
    for changed in ["none", "body", "generation", "epoch", "turn", "session", "event", "removed"] {
        let (d, source) = server(100);
        let source = {
            let mut data = d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            let context = s.visual.context.clone().unwrap();
            let source = source.after_look(Some(LookCapture {
                capture_id: "reserved".into(),
                look: completed_look(context, s.input_generation),
            }));
            match changed {
                "body" => s.body_observation = Default::default(),
                "generation" => s.input_generation += 1,
                "epoch" => s.epoch += 1,
                "turn" => s.current_turn = "next".into(),
                "session" => s.instance_id = "replacement".into(),
                "event" => s.latest = None,
                _ => (),
            }
            if changed == "removed" { data.sessions.remove("s"); }
            source
        };
        assert_eq!(source.input_current(), matches!(changed, "none" | "body"), "{changed}");
        d.shutdown().await;
    }
}
