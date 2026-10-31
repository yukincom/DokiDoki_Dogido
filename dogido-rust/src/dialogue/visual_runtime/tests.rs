//! 見込み: 画像取得と変化を分ければ、無変化でも最新画像を使い、変化だけを一度通知できる。合格: 比較状態・画像保持・通知・取消・要求への添付が一致する。確認範囲: 模擬画像と固定HTTP応答。実撮影・実モデルの理解は対象外。
use super::*;
use base64::{Engine as _, engine::general_purpose::STANDARD};
use sha2::{Digest, Sha256};
const OWNER: &str = "11111111-1111-4111-8111-111111111111";
const PNG: &str = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8v0AAAAASUVORK5CYII=";
// 比較元は現在画像とは別の撮影。局所変化1マスを固定し、Javaの変化検出そのものは模擬する。
fn comparison(status: &str, at: DateTime<Utc>) -> Value {
    json!({"status":status,"reference":if status == "not_compared" {Value::Null} else {
        json!({"capture_id":"view_22222222-2222-4222-8222-222222222222",
            "captured_at":at-chrono::Duration::seconds(4),"sha256":"a".repeat(64)})
    },"cells":if status == "changed" {
        json!([{"row":5,"column":3,"local_change":true,"brightness":"none"}])
    } else {json!([])}})
}
fn pending() -> (State,AdapterCommandResult,DateTime<Utc>) {
    let now=Utc::now();
    let context: Context=serde_json::from_value(crate::body::tests::fixture()["sample"]["context"].clone()).unwrap();
    let mut state=State {enabled:true,..Default::default()};state.arm("s",Some(context.clone()),now-chrono::Duration::milliseconds(10));
    let result=serde_json::from_value(json!({"command_id":state.commands(now)[0]["command_id"],"command_type":"capture_body_view","status":"succeeded","executed_at":now,
        "body_view":{"body_entity_uuid":context.body_id,"body_epoch":context.epoch,"owner_uuid":context.owner_id,"dimension":context.dimension,
        "camera_restored":true,"png_base64":PNG,"sha256":format!("{:x}",Sha256::digest(STANDARD.decode(PNG).unwrap())),
        "width":1,"height":1,"captured_at":now,"annotation_status":"available","capture_id":"view_12345678-1234-1234-1234-123456789abc",
        "motion_sequence":1,"scene_comparison":comparison("changed",now)}})).unwrap();
    (state,result,now)
}
// 見込み: 同じ撮影要求を再利用し、成功画像の再送で通知を増やさない。合格: 命令IDを保ち、受理後の次命令だけIDと停止番号を更新し、snapshotにPNGを含めない。確認範囲: 模擬結果による受付状態。
#[test]
fn stop_request_reuses_sequence_and_acknowledges_accepted_image_exactly_once() {
    let (mut state,result,now)=pending();let command=state.commands(now)[0].clone();let context=state.context.clone();
    state.arm("s",context.clone(),now);assert_eq!(state.commands(now)[0],command);
    assert!(state.receive(&result,now));assert!(state.ready);assert_eq!(state.latest.as_ref().unwrap().sequence,1);
    assert!(state.receive(&result,now));state.arm("s",context,now);
    assert_ne!(state.commands(now)[0]["command_id"],command["command_id"]);
    assert_eq!(state.commands(now)[0]["after_motion_sequence"],1);
    assert!(!state.snapshot().to_string().contains(PNG));
}
// 見込み: 無変化でも画像を保持し、身体文脈を失えば通常画像を破棄する。合格: unchangedでlatestあり・readyなし、文脈消失後は画像と命令なし。確認範囲: 固定比較結果を使うRust受信。
#[test]
fn equivalent_fourth_image_updates_cache_but_creates_no_change_and_context_loss_clears_it() {
    let (mut state,mut result,now)=pending();let view=&mut result.body_view.as_mut().unwrap().0;
    view["scene_comparison"]=comparison("unchanged",now);
    state.receive(&result,now);assert!(!state.ready);assert!(state.latest.is_some());
    state.arm("s",None,now);assert!(state.latest.is_none());assert!(state.commands(now).is_empty());
}
// 見込み: 撮影identity・時刻・比較契約の破損はモデルへ届かない。合格: 各破損または期限超過で受信を完了しlatestを作らない。確認範囲: 模擬JSONの受理境界。撮影と検出精度は別確認。
#[test]
fn invalid_images_cannot_become_model_material_and_expired_requests_cannot_renew_their_results() {
    for key in ["body_entity_uuid","body_epoch","owner_uuid","dimension","camera_restored","sha256","captured_at","capture_id","motion_sequence","comparison","timeout"] {
        let (mut state,mut result,mut now)=pending();let view=&mut result.body_view.as_mut().unwrap().0;
        match key {"camera_restored"=>view[key]=json!(false),"motion_sequence"=>view[key]=json!(0),
            "comparison"=>view["scene_comparison"]["cells"]=json!([]),"timeout"=>now+=chrono::Duration::seconds(11),_=>view[key]=json!("wrong")};
        assert!(state.receive(&result,now));assert!(state.latest.is_none(),"{key}");
    }
}
// 見込み: 画像本文はモデル添付に限定できる。合格: 模擬受信とSpeechのJSON・Debug表現にPNG文字列がない。確認範囲: Rustの該当serialize/Debug経路。外部ログ全体の監査ではない。
#[test]
fn image_body_never_appears_in_serialized_events_debug_or_speech() {
    let (mut state,result,now)=pending();state.receive(&result,now);
    assert!(!serde_json::to_string(&result).unwrap().contains(PNG));assert!(!format!("{result:?}").contains(PNG));
    let mut action=crate::environment::notice::action();action.image=Some(Arc::new(state.latest.unwrap().image));
    assert!(!json!(action).to_string().contains(PNG));assert!(!format!("{action:?}").contains(PNG));
}
fn wire(sequence: i64) -> crate::events::GameEvent {
    let mut body = crate::body::tests::fixture();
    body["age_ms"] = json!(0);
    body["sample"]["server_tick"] = json!(100+sequence);
    body["sample"]["snapshot_revision"] = json!(200+sequence);
    body["sample"]["senses"]["source_server_tick"] = json!(100+sequence);
    crate::events::GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"image-test","observed_at":Utc::now(),"sequence":sequence,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20},"world":{"time_phase":"day","local_light":15,"danger_darkness_score":0},
        "body_observation":body})).unwrap()
}
fn server(wait_ms: u64) -> (Arc<Dialogue>, Source) {
    let mut config = super::super::DialogueConfig {audio_enabled:false, llm_enabled:false, ..Default::default()};
    config.chat_image.enabled = true;
    config.chat_image.wait_ms = wait_ms;
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    let d = Dialogue::new(config).unwrap();
    d.register("s", "test", false);
    d.bind_body_owner("s", Some(OWNER));
    d.set_execution_capabilities("s", &[CAPABILITY.into()]);
    d.observe("s", wire(1), None);
    {
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        s.epoch = 7; s.current_turn = "turn".into();
    }
    let source = Source::new(&d,"s","turn",7).unwrap();
    (d, source)
}

// 見込み: 通常返答は既存の最新画像を再利用できる。合格: 新命令を増やさず同じ撮影IDを2回取得し、画像欠落時の取消でNoneになる。確認範囲: Sourceと模擬受信。実撮影なし。
#[tokio::test]
async fn ordinary_reply_uses_latest_image_without_starting_another_capture_and_can_cancel_empty_wait() {
    let (d,source)=server(100);let (_tx,mut cancel)=watch::channel(false);
    let before=d.data.lock().unwrap().sessions["s"].visual.commands(Utc::now());
    assert!(source.capture(&mut cancel).await.is_none());
    assert_eq!(d.data.lock().unwrap().sessions["s"].visual.commands(Utc::now()),before);
    let (_,mut result,_)=pending();result.command_id=before[0]["command_id"].as_str().unwrap().into();
    result.body_view.as_mut().unwrap().0["captured_at"]=json!(Utc::now());
    d.data.lock().unwrap().sessions.get_mut("s").unwrap().visual.receive(&result,Utc::now());
    let first=source.capture(&mut cancel).await.unwrap();let second=source.capture(&mut cancel).await.unwrap();
    assert_eq!(first.capture_id,second.capture_id);
    let (tx,mut cancelled)=watch::channel(false);tx.send(true).unwrap();
    d.data.lock().unwrap().sessions.get_mut("s").unwrap().visual.latest=None;
    assert!(source.capture(&mut cancelled).await.is_none());
}
// 見込み: 比較変化だけが既存noticeを一度作る。合格: changedで画像付きnoticeが一件、unchangedで最新画像だけ更新し、先行noticeのPNGを維持する。確認範囲: 固定比較結果と環境文脈更新。
#[tokio::test]
async fn changed_image_creates_one_existing_notice_with_frozen_image_and_unchanged_image_does_not() {
    let (d,_)=server(100);let (_,mut result,_)=pending();
    let mut data=d.data.lock().unwrap();let s=data.sessions.get_mut("s").unwrap();
    result.command_id=s.visual.commands(Utc::now())[0]["command_id"].as_str().unwrap().into();
    result.body_view.as_mut().unwrap().0["captured_at"]=json!(Utc::now());s.visual.receive(&result,Utc::now());
    let mut action=opportunity(s).unwrap();assert!(action.image.is_some());assert!(opportunity(s).is_none());
    assert!(action.leaf.as_ref().unwrap().details["reaction_context"].is_null());
    assert!(super::super::environment_runtime::refresh_dialogue_context(s, std::slice::from_mut(&mut action)));
    assert!(action.leaf.as_ref().unwrap().details["reaction_context"]["world_context"]["changes"].as_array().unwrap().iter().any(|c|c["kind"]=="body_view"));
    let context=s.visual.context.clone();s.visual.arm("s",context,Utc::now());
    result.command_id=s.visual.commands(Utc::now())[0]["command_id"].as_str().unwrap().into();let view=&mut result.body_view.as_mut().unwrap().0;
    view["captured_at"]=json!(Utc::now());view["motion_sequence"]=json!(2);view["scene_comparison"]=comparison("unchanged",Utc::now());
    s.visual.receive(&result,Utc::now());assert!(opportunity(s).is_none());assert_eq!(s.visual.latest.as_ref().unwrap().sequence,2);
    assert_eq!(action.image.as_ref().unwrap().png_base64,PNG);
}
// 画像受理から既存の環境入力までを通す。変わらないカメと匂いは現在表に残し、天候だけを差分にする。
fn environment_with_image() -> (Arc<Dialogue>, crate::combat::model::Speech) {
    let (d, _) = server(100);
    let (_, mut result, _) = pending();
    let mut data = d.data.lock().unwrap();
    let s = data.sessions.get_mut("s").unwrap();
    let mut frame = super::super::body_runtime::context(s).unwrap();
    frame.observations["nearby_mob_types"] = json!([{"id":"turtle","label":"カメ","quantity":3}]);
    frame.observations["smell"] = json!({"status":"present","components":[{"smell_id":"flower","effective_strength":3}]});
    frame.observations["weather"] = json!("clear");
    s.conversation_observation.observe(frame.clone());
    let revision = s.conversation_observation.context().unwrap().revision;
    s.conversation_observation.considered(revision);
    frame.observations["weather"] = json!("rain");
    s.conversation_observation.observe(frame);
    s.history.push("previous", "assistant", "カメさん、砂浜でゆっくりしとるなぁ。");
    result.command_id = s.visual.commands(Utc::now())[0]["command_id"].as_str().unwrap().into();
    result.body_view.as_mut().unwrap().0["captured_at"] = json!(Utc::now());
    s.visual.receive(&result, Utc::now());
    let mut action = opportunity(s).unwrap();
    action.delivery = crate::combat::model::Delivery::Ambient;
    let event = super::super::body_runtime::event(s, s.latest.as_ref().unwrap());
    crate::environment::reaction::attach(&mut action, &event, &d.config.combat);
    assert!(super::super::environment_runtime::refresh_dialogue_context(s, std::slice::from_mut(&mut action)));
    drop(data);
    (d, action)
}

// 見込み: 生成直前に観測と履歴を更新しても、選んだ撮影は固定できる。合格: 新天候・沈黙履歴を反映し、既知のカメと匂い・撮影identity・一件の画像契機を維持する。確認範囲: 模擬session。
#[tokio::test]
async fn image_notice_refreshes_all_current_facts_and_history_in_the_common_context() {
    let (d, mut action) = environment_with_image();
    let before = action.leaf.as_ref().unwrap().details["reaction_context"].clone();
    let image = action.image.clone();
    let mut data = d.data.lock().unwrap();
    let s = data.sessions.get_mut("s").unwrap();
    let mut frame = s.conversation_observation.context().unwrap();
    frame.observations["weather"] = json!("snow");
    s.conversation_observation.observe(frame);
    s.history.silent("last-silence");
    for _ in 0..2 {
        assert!(super::super::environment_runtime::refresh_dialogue_context(s, std::slice::from_mut(&mut action)));
        let current = &action.leaf.as_ref().unwrap().details["reaction_context"];
        assert_eq!(current["world_context"]["observations"]["weather"], "snow");
        for key in ["nearby_mob_types", "smell"] {
            assert_eq!(current["world_context"]["observations"][key], before["world_context"]["observations"][key]);
        }
        let changes = current["world_context"]["changes"].as_array().unwrap();
        assert_eq!(changes.iter().filter(|c| c["kind"] == "body_view").count(), 1);
        assert!(!changes.iter().any(|c| c["kind"] == "nearby_mob_types" || c["kind"] == "smell"));
        assert_eq!(current["recent"]["conversation"].as_array().unwrap().last().unwrap()["reaction"], "silent");
        assert_eq!(current["visual_capture"], before["visual_capture"]);
        assert!(Arc::ptr_eq(action.image.as_ref().unwrap(), image.as_ref().unwrap()));
    }
}

// 見込み: 環境判断へ一枚と共通観測を渡し、モデルが選んだ沈黙を受け取れる。合格: HTTP要求一件に画像・天候・匂い・履歴・共有指示が揃いsilentになる。確認範囲: 固定応答。実モデルの判断は未検査。
#[tokio::test]
async fn existing_environment_model_gets_one_image_request_and_can_select_silence() {
    let sent=Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));let recorded=sent.clone();
    let app=axum::Router::new().route("/v1/chat/completions",axum::routing::post(move |axum::Json(payload):axum::Json<Value>| {
        recorded.lock().unwrap().push(payload);async {axum::Json(json!({"id":"fixture","object":"chat.completion","created":0,"model":"fixture",
        "choices":[{"index":0,"message":{"role":"assistant","content":"{\"action\":\"silent\",\"speech\":\"\"}"},"finish_reason":"stop"}]}))}
    }));
    let socket=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();let url=format!("http://{}/v1",socket.local_addr().unwrap());
    let http=tokio::spawn(async move {axum::serve(socket,app).await.unwrap();});
    let llm=crate::llm::RigLlm::new(&url,None,Duration::from_secs(2)).unwrap();
    let mut config=super::super::DialogueConfig {model:"fixture".into(),..Default::default()};config.reading_engine="off".into();
    let (_d, action) = environment_with_image();
    let input=json!({"kind":"environment_reaction","model":"fixture","max_tokens":200,"fallback_text":"",
        "details":action.leaf.as_ref().unwrap().details});
    let (_tx,mut cancel)=watch::channel(false);
    let result=super::super::reaction_runtime::render_image(&config,&super::super::tts_runtime::Dictionary::default(),&llm,&input,&mut cancel,action.image.as_deref()).await.unwrap();
    assert_eq!(result["environment_reaction_outcome"],"silent");assert!(!result.to_string().contains(PNG));
    let requests=sent.lock().unwrap();assert_eq!(requests.len(),1);
    assert_eq!(requests[0]["messages"].as_array().unwrap().last().unwrap()["content"][1]["image_url"]["url"],format!("data:image/png;base64,{PNG}"));
    assert!(requests[0]["messages"][0]["content"].as_str().unwrap().contains(crate::companion_prompt::chat_image()));
    let content = &requests[0]["messages"].as_array().unwrap().last().unwrap()["content"];
    let (context, metadata) = content[0]["text"].as_str().unwrap().split_once("\n\nimage_observation: ").unwrap();
    let context: Value = serde_json::from_str(context).unwrap();
    let metadata: Value = serde_json::from_str(metadata).unwrap();
    assert_eq!(metadata["captured_at"], action.image.as_ref().unwrap().captured_at);
    let observations = &context["world_context"]["observations"];
    assert_eq!(observations["nearby_mob_types"][0]["id"], "turtle");
    assert_eq!(observations["nearby_mob_types"][0]["quantity"], 3);
    assert_eq!(observations["weather"], "rain");
    assert_eq!(observations["smell"]["components"][0]["effective_strength"], 3);
    assert_eq!(observations["body_observation"]["status"], "observed");
    let changes = context["world_context"]["changes"].as_array().unwrap();
    assert!(changes.iter().any(|c| c["kind"] == "weather"));
    assert!(changes.iter().any(|c| c["kind"] == "body_view"));
    assert!(!changes.iter().any(|c| c["kind"] == "nearby_mob_types" || c["kind"] == "smell"));
    let history = context["recent"]["conversation"].as_str().unwrap();
    assert!(history.contains("ドギド: カメさん、砂浜でゆっくりしとるなぁ。"));
    assert!(history.contains("契機不明"));
    assert!(context["situation"].as_str().unwrap().contains("今はプレイヤーから新しい発言を受けてへん"));
    let shared = crate::reaction_leaf::context("surroundings_notice", &json!({})).unwrap();
    assert_eq!(context["situation"], shared["situation"]);
    let rule = "同じ意味合いの話をしたばかりだったら、黙ってな。プレイヤーはちゃんと聞いとるから大丈夫やで。";
    assert_eq!(requests[0]["messages"][0]["content"].as_str().unwrap().matches(rule).count(), 1);
    assert!(!context["situation"].as_str().unwrap().contains(rule));
    assert_eq!(result["observation_revision"], context["world_context"]["revision"]);
    assert!(!context.to_string().contains(PNG));
    // 添付の有無で、共通の状況文・全観測・変化・履歴が削られない。
    let mut without_image = input.clone();
    without_image["details"]["reaction_context"].as_object_mut().unwrap().remove("visual_capture");
    let plain = crate::environment::reaction::Prepared::new(&without_image, "fixture").unwrap();
    let plain: Value = serde_json::from_str(&plain.request.messages[1].content).unwrap();
    for key in ["situation", "world_context", "recent"] { assert_eq!(context[key], plain[key]); }
    http.abort();
}
// 見込み: 画像行動の選択肢に応じた契約を一回の応答へ添えられる。合格: HTTP要求一件、画像指示一回、inspect_image有効時だけ専用契約を含み固定body_actionを受理する。確認範囲: 模擬HTTP。
#[tokio::test]
async fn image_movement_uses_one_reply_and_only_available_image_guidance() {
    for inspect in [false,true] {
        let sent=Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));let recorded=sent.clone();
        let choice=json!({"action":"inspect_image","target_id":null,"image_target":{"description":"左の黄色い花",
            "kind":"flower","color":"yellow","horizontal":"left","vertical":"any"}});
        let reply=json!({"action":"speak","speech":"あの花、見てくるわ。","body_action":choice});
        let app=axum::Router::new().route("/v1/chat/completions",axum::routing::post(move |axum::Json(payload):axum::Json<Value>| {
            recorded.lock().unwrap().push(payload);let text=reply.to_string();async move {axum::Json(json!({"id":"fixture","object":"chat.completion","created":0,"model":"fixture",
            "choices":[{"index":0,"message":{"role":"assistant","content":text},"finish_reason":"stop"}]}))}
        }));
        let socket=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();let url=format!("http://{}/v1",socket.local_addr().unwrap());
        let http=tokio::spawn(async move {axum::serve(socket,app).await.unwrap();});
        let llm=crate::llm::RigLlm::new(&url,None,Duration::from_secs(2)).unwrap();
        let mut config=super::super::DialogueConfig {model:"fixture".into(),..Default::default()};config.reading_engine="off".into();
        let (mut state,result,now)=pending();state.receive(&result,now);let captured=state.latest.unwrap();
        let available=if inspect {json!({"stay":"その場におる","inspect_image":"画像を見物"})} else {json!({"stay":"その場におる","owner_face":"顔を見る"})};
        let input=json!({"kind":"environment_reaction","model":"fixture","max_tokens":300,"fallback_text":"",
            "details":{"reaction_context":{"topic":"surroundings","visual_capture":{"captured_at":captured.image.captured_at},
                "body_options":{"available_actions":available,"candidates":[]},"world_context":{"observations":{},"changes":[]}}}});
        let (_tx,mut cancel)=watch::channel(false);
        let result=super::super::reaction_runtime::render_image(&config,&super::super::tts_runtime::Dictionary::default(),&llm,&input,&mut cancel,Some(&captured.image)).await.unwrap();
        assert_eq!(result["body_action"],choice);
        let requests=sent.lock().unwrap();assert_eq!(requests.len(),1);
        let system=requests[0]["messages"][0]["content"].as_str().unwrap();
        assert_eq!(system.matches("これは今あんたが見とる風景やで。").count(),1);
        assert!(system.contains(crate::companion_prompt::image_move_notice()));
        assert_eq!(system.contains(crate::companion_prompt::image_body_contract()),inspect);
        assert!(!system.contains("対象がある行動の target_id") && !system.contains("x は左0") && !system.contains("たとえば"));
        let content=requests[0]["messages"].as_array().unwrap().last().unwrap()["content"][0]["text"].as_str().unwrap();
        assert!(!content.contains("これは今あんたが見とる風景"));
        http.abort();
    }
}
// 見込み: 現在画像の利用可否は再取得した身体世代で照合できる。合格: 同世代ではtrue、新epoch観測後はfalse。確認範囲: Source.refreshの模擬観測。明示撮影後の保持契約とは別。
#[tokio::test]
async fn refresh_rechecks_the_body_in_the_same_snapshot() {
    let (d, source) = server(2500);
    let (input, original) = {
        let data = d.data.lock().unwrap();
        let s = &data.sessions["s"];
        let event = s.latest.as_ref().unwrap().clone();
        let native = chat_context::capture(s, &event, &d.config.combat, Default::default(), None).unwrap();
        (bridge::Input::native(json!({}), event, native), s.body_observation.accepted(Instant::now()).unwrap().context.clone())
    };
    assert!(source.refresh(&input, Some(&original)).unwrap().1);
    let mut newer = serde_json::to_value(wire(2)).unwrap();
    newer["body_observation"]["sample"]["context"]["epoch"] = json!("99999999-9999-4999-8999-999999999999");
    d.observe("s", crate::events::GameEvent::parse(newer).unwrap(), None);
    assert!(!source.refresh(&input, Some(&original)).unwrap().1);
}

// 見込み: 無変化画像も既存判断の材料になり、偽の画像変化を作らない。合格: 天候差分を維持し、4種のHTTP要求に同じPNGと「変化がない」を添える。確認範囲: 固定応答による要求構築。実モデル未使用。
#[tokio::test]
async fn unchanged_image_is_attached_to_periodic_and_ordinary_decisions_without_a_fake_change() {
    let (d,source)=server(100); let (_,mut receipt,_)=pending();
    let mut action=crate::environment::notice::action();
    action.delivery=crate::combat::model::Delivery::Ambient;
    let frozen;
    {
        let mut data=d.data.lock().unwrap(); let s=data.sessions.get_mut("s").unwrap();
        receipt.command_id=s.visual.commands(Utc::now())[0]["command_id"].as_str().unwrap().into();
        let v=&mut receipt.body_view.as_mut().unwrap().0;
        v["captured_at"]=json!(Utc::now()); v["scene_comparison"]=comparison("unchanged",Utc::now());
        s.visual.receive(&receipt,Utc::now()); assert!(opportunity(s).is_none());
        let mut frame=super::super::body_runtime::context(s).unwrap();
        s.conversation_observation.observe(frame.clone()); s.conversation_observation.sample_notice(10,1,true);
        frame.observations["weather"]=json!("rain");
        s.conversation_observation.observe(frame); s.conversation_observation.sample_notice(11,1,true);
        let event=super::super::body_runtime::event(s,s.latest.as_ref().unwrap());
        crate::environment::reaction::attach(&mut action,&event,&d.config.combat);
        assert!(super::super::environment_runtime::refresh_dialogue_context(s,std::slice::from_mut(&mut action)));
        frozen=action.leaf.as_ref().unwrap().details["reaction_context"]["world_context"].clone();
        assert!(action.image.is_some());
        assert!(!frozen["changes"].as_array().unwrap().iter().any(|c|c["kind"]=="body_view"));
        assert!(frozen["changes"].as_array().unwrap().iter().any(|c|c["kind"]=="weather"));
        let capture=&action.leaf.as_ref().unwrap().details["reaction_context"]["visual_capture"];
        assert!(capture.get("visual_change").is_none() && capture.get("scene_comparison").is_none());
        assert_eq!(action.image.as_ref().unwrap().scene_comparison.as_ref().unwrap().status,crate::chat_image::ComparisonStatus::Unchanged);
        let mut changed=s.conversation_observation.context().unwrap(); changed.observations["weather"]=json!("clear");
        s.conversation_observation.observe(changed);
        assert!(super::super::environment_runtime::refresh_dialogue_context(s,std::slice::from_mut(&mut action)));
        let preserved=&action.leaf.as_ref().unwrap().details["reaction_context"]["world_context"];
        assert_eq!(preserved["revision"],frozen["revision"]); assert_eq!(preserved["changes"],frozen["changes"]);
        assert_eq!(preserved["observations"]["weather"],"clear");
    }
    let (_tx,mut cancel)=watch::channel(false);
    let image=source.capture(&mut cancel).await.unwrap().image;
    assert_eq!(image.sha256,action.image.as_ref().unwrap().sha256);
    assert!(image.metadata_text().contains("変化がない"));
    // 比較結果を付けた同じPNGが実際のモデル要求へ入ることまで確認する。
    let sent=Arc::new(std::sync::Mutex::new(Vec::<Value>::new())); let recorded=sent.clone();
    let app=axum::Router::new().route("/v1/chat/completions",axum::routing::post(move |axum::Json(v):axum::Json<Value>| {
        recorded.lock().unwrap().push(v);async {axum::Json(json!({"id":"fixture","object":"chat.completion","created":0,"model":"fixture","choices":[{"index":0,"message":{"role":"assistant","content":"{\"action\":\"silent\",\"speech\":\"\"}"},"finish_reason":"stop"}]}))}
    }));
    let socket=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let llm=crate::llm::RigLlm::new(&format!("http://{}/v1",socket.local_addr().unwrap()),None,Duration::from_secs(2)).unwrap();
    let http=tokio::spawn(async move {axum::serve(socket,app).await.unwrap();});
    let config=super::super::DialogueConfig {model:"fixture".into(),reading_engine:"off".into(),..Default::default()};
    let input=json!({"kind":"environment_reaction","model":"fixture","max_tokens":200,"fallback_text":"","details":action.leaf.as_ref().unwrap().details});
    super::super::reaction_runtime::render_image(&config,&super::super::tts_runtime::Dictionary::default(),&llm,&input,&mut cancel,Some(&image)).await.unwrap();
    for kind in ["portal_appearance","darkness_escape","emergency_shelter_relief"] {
        let input=json!({"kind":kind,"model":"fixture","max_tokens":config.max_tokens,"temperature":0.65,"fallback_text":"",
            "details":{"conversation_context":{"world_context":frozen}}});
        super::super::reaction_runtime::render_image(&config,&super::super::tts_runtime::Dictionary::default(),&llm,&input,&mut cancel,Some(&image)).await.unwrap();
    }
    let requests=sent.lock().unwrap(); assert_eq!(requests.len(),4);
    for request in requests.iter() {
        let content=&request["messages"].as_array().unwrap().last().unwrap()["content"];
        assert_eq!(content[1]["image_url"]["url"],format!("data:image/png;base64,{PNG}"));
        assert!(content[0]["text"].as_str().unwrap().contains("変化がない"));
        assert!(request["messages"][0]["content"].as_str().unwrap().contains(crate::companion_prompt::chat_image()));
    }
    http.abort();
}

// 見込み: 古い無変化結果を現在の証拠にしない。合格: 31秒前の画像は通常Sourceと環境添付の双方で使えない。確認範囲: 撮影時刻を操作した模擬状態。明示注視の保持は別。
#[tokio::test]
async fn old_image_cannot_claim_the_current_scene_is_unchanged() {
    let (d,source)=server(100); let (mut state,mut receipt,now)=pending();
    let v=&mut receipt.body_view.as_mut().unwrap().0;
    v["scene_comparison"]=comparison("unchanged",Utc::now());
    state.receive(&receipt,now);
    state.latest.as_mut().unwrap().image.captured_at=(Utc::now()-chrono::Duration::seconds(31)).to_rfc3339();
    d.data.lock().unwrap().sessions.get_mut("s").unwrap().visual=state;
    let (_tx,mut cancel)=watch::channel(false); assert!(source.capture(&mut cancel).await.is_none());
    let mut action=crate::environment::notice::action();
    let data=d.data.lock().unwrap();attach_latest(&data.sessions["s"],&mut action);assert!(action.image.is_none());
}


#[path = "look_tests.rs"]
mod look_tests;
