//! 実モデルなしで、静止画像の配送・変化抑制・最新一枚・環境生成への添付と失効を検査する。
use super::*;
use base64::{Engine as _, engine::general_purpose::STANDARD};
use sha2::{Digest, Sha256};
const OWNER: &str = "11111111-1111-4111-8111-111111111111";
const PNG: &str = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8v0AAAAASUVORK5CYII=";
fn pending() -> (State,AdapterCommandResult,DateTime<Utc>) {
    let now=Utc::now();
    let context: Context=serde_json::from_value(crate::body::tests::fixture()["sample"]["context"].clone()).unwrap();
    let mut state=State {enabled:true,..Default::default()};state.arm("s",Some(context.clone()),now-chrono::Duration::milliseconds(10));
    let result=serde_json::from_value(json!({"command_id":state.commands(now)[0]["command_id"],"command_type":"capture_body_view","status":"succeeded","executed_at":now,
        "body_view":{"body_entity_uuid":context.body_id,"body_epoch":context.epoch,"owner_uuid":context.owner_id,"dimension":context.dimension,
        "camera_restored":true,"png_base64":PNG,"sha256":format!("{:x}",Sha256::digest(STANDARD.decode(PNG).unwrap())),
        "width":1,"height":1,"captured_at":now,"annotation_status":"available","capture_id":"view_12345678-1234-1234-1234-123456789abc",
        "motion_sequence":1,"visual_change":true,"compared_to_four_before":false}})).unwrap();
    (state,result,now)
}
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
#[test]
fn equivalent_fourth_image_updates_cache_but_creates_no_change_and_context_loss_clears_it() {
    let (mut state,mut result,now)=pending();let view=&mut result.body_view.as_mut().unwrap().0;
    view["visual_change"]=json!(false);view["compared_to_four_before"]=json!(true);
    state.receive(&result,now);assert!(!state.ready);assert!(state.latest.is_some());
    state.arm("s",None,now);assert!(state.latest.is_none());assert!(state.commands(now).is_empty());
}
#[test]
fn invalid_images_cannot_become_model_material_and_expired_requests_cannot_renew_their_results() {
    for key in ["body_entity_uuid","body_epoch","owner_uuid","dimension","camera_restored","sha256","captured_at","capture_id","motion_sequence","comparison","timeout"] {
        let (mut state,mut result,mut now)=pending();let view=&mut result.body_view.as_mut().unwrap().0;
        match key {"camera_restored"=>view[key]=json!(false),"motion_sequence"=>view[key]=json!(0),
            "comparison"=>view["visual_change"]=json!(false),"timeout"=>now+=chrono::Duration::seconds(11),_=>view[key]=json!("wrong")};
        assert!(state.receive(&result,now));assert!(state.latest.is_none(),"{key}");
    }
}
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
#[tokio::test]
async fn changed_image_creates_one_existing_notice_with_frozen_image_and_unchanged_image_does_not() {
    let (d,_)=server(100);let (_,mut result,_)=pending();
    let mut data=d.data.lock().unwrap();let s=data.sessions.get_mut("s").unwrap();
    result.command_id=s.visual.commands(Utc::now())[0]["command_id"].as_str().unwrap().into();
    result.body_view.as_mut().unwrap().0["captured_at"]=json!(Utc::now());s.visual.receive(&result,Utc::now());
    let action=opportunity(s).unwrap();assert!(action.image.is_some());assert!(opportunity(s).is_none());
    assert!(action.leaf.as_ref().unwrap().details["reaction_context"]["world_context"]["changes"].as_array().unwrap().iter().any(|c|c["kind"]=="body_view"));
    let context=s.visual.context.clone();s.visual.arm("s",context,Utc::now());
    result.command_id=s.visual.commands(Utc::now())[0]["command_id"].as_str().unwrap().into();let view=&mut result.body_view.as_mut().unwrap().0;
    view["captured_at"]=json!(Utc::now());view["motion_sequence"]=json!(2);view["visual_change"]=json!(false);view["compared_to_four_before"]=json!(true);
    s.visual.receive(&result,Utc::now());assert!(opportunity(s).is_none());assert_eq!(s.visual.latest.as_ref().unwrap().sequence,2);
    assert_eq!(action.image.as_ref().unwrap().png_base64,PNG);
}
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
    let (mut state,result,now)=pending();state.receive(&result,now);let captured=state.latest.unwrap();
    let input=json!({"kind":"environment_reaction","model":"fixture","max_tokens":200,"fallback_text":"",
        "details":{"reaction_context":{"topic":"surroundings","visual_capture":{"captured_at":captured.image.captured_at},"world_context":{"observations":{},"changes":[]}}}});
    let (_tx,mut cancel)=watch::channel(false);
    let result=super::super::reaction_runtime::render_image(&config,&llm,&input,&mut cancel,Some(&captured.image)).await.unwrap();
    assert_eq!(result["environment_reaction_outcome"],"silent");assert!(!result.to_string().contains(PNG));
    let requests=sent.lock().unwrap();assert_eq!(requests.len(),1);
    assert_eq!(requests[0]["messages"].as_array().unwrap().last().unwrap()["content"][1]["image_url"]["url"],format!("data:image/png;base64,{PNG}"));
    assert!(requests[0]["messages"][0]["content"].as_str().unwrap().contains(crate::companion_prompt::chat_image()));
    http.abort();
}
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
        let result=super::super::reaction_runtime::render_image(&config,&llm,&input,&mut cancel,Some(&captured.image)).await.unwrap();
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
