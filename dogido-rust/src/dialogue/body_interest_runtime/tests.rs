//! 模擬HTTP応答を使い、実Dialogueの候補受理・生成・配送・ACKと取消を往復させる。
//! ここでのinspect成功はモデルの判断品質やMinecraft内の到着を検証した意味にはしない。
use super::*;
use crate::{dialogue::{Dialogue,DialogueConfig}, events::GameEvent};
use axum::{Json, Router, routing::post};
use std::{sync::{Arc,Mutex},time::Duration};

const OWNER: &str = "11111111-1111-4111-8111-111111111111";
const COW: &str = "44444444-4444-4444-8444-444444444444";

struct Mock {
    url: String,
    seen: Arc<Mutex<Vec<Value>>>,
    task: tokio::task::JoinHandle<()>,
}
impl Drop for Mock { fn drop(&mut self) { self.task.abort(); } }
impl Mock {
    async fn new(delay: Duration, output: &'static str) -> Self {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let recorded = seen.clone();
        let app = Router::new().route("/v1/chat/completions",post(move |Json(input): Json<Value>| {
            let recorded = recorded.clone();
            async move {
                recorded.lock().unwrap().push(input);
                tokio::time::sleep(delay).await;
                Json(json!({"id":"mock-interest","object":"chat.completion","created":0,"model":"fixture",
                    "choices":[{"index":0,"message":{"role":"assistant","content":output},"finish_reason":"stop"}],
                    "usage":{"prompt_tokens":10,"completion_tokens":10,"total_tokens":20}}))
            }
        }));
        let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}/v1",socket.local_addr().unwrap());
        let task = tokio::spawn(async move { axum::serve(socket,app).await.unwrap(); });
        Self { url, seen, task }
    }
}
fn server(mock: &Mock, capability: bool) -> Arc<Dialogue> {
    let mut c = DialogueConfig { base_url: mock.url.clone(), model: "fixture".into(), audio_enabled: false, llm_enabled: true, ..Default::default() };
    c.haiku.enabled = false;
    c.haiku.memory_enabled = false;
    let d = Dialogue::new(c).unwrap();
    d.register("s","呼称",false);
    d.bind_body_owner("s",Some(OWNER));
    if capability { d.set_execution_capabilities("s", &["body.inspect_target.v2".into()]); }
    d
}
fn wire(sequence: i64) -> Value {
    let mut body = crate::body::tests::fixture();
    body["age_ms"] = json!(0);
    body["sample"]["server_tick"] = json!(100+sequence);
    body["sample"]["snapshot_revision"] = json!(200+sequence);
    body["sample"]["senses"]["source_server_tick"] = json!(100+sequence);
    body["sample"]["player_pose"]["position"]["z"] = json!(24.0);
    let sample = &body["sample"];
    json!({"schema_version":"2026-05-24","adapter":"body-interest-test","observed_at":chrono::Utc::now(),"sequence":sequence,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20},
        "world":{"biome":"plains","time_phase":"day","sky_visible":true,"local_light":15,"danger_darkness_score":0},
        "body_observation":body,
        "body_autonomy":{"version":2,"status":"observed","context":sample["context"],"server_tick":sample["server_tick"],
            "snapshot_revision":sample["snapshot_revision"],"age_ms":0,"enabled":true,"mode":"follow","follow_distance":16.0,"warp_distance":24.0,
            "activity":"holding","reason":"follow_wait",
            "attention":{"scope":"ambient","expansion":1.0},
            "reassurance":{"value":20.0,"owner_distance":4.0,"owner_visible":true,"face_visible":false,"owner_working":false},"candidates":[{"id":format!("e:{COW}"),"kind":"entity","type_id":"minecraft:cow","label":"ウシ",
                "position":{"x":12.0,"y":64.0,"z":23.0},"entity_id":COW}]}})
}
fn observe(d: &Arc<Dialogue>, v: Value) -> Value { d.observe("s",GameEvent::parse(v).unwrap(),None) }

/// 身体側の通常追従距離を変更した観測。3blockの回復域とは分けて実位置も合わせる。
fn range_wire(sequence: i64, distance: f64, follow_distance: f64) -> Value {
    let mut value = wire(sequence);
    value["body_autonomy"]["follow_distance"] = json!(follow_distance);
    value["body_autonomy"]["reassurance"]["owner_distance"] = json!(distance);
    value["body_observation"]["sample"]["player_pose"]["position"]["z"] = json!(20.0+distance);
    value
}
fn inside_wire(sequence: i64) -> Value { range_wire(sequence,8.0,8.0) }
fn outside_wire(sequence: i64) -> Value { range_wire(sequence,9.0,8.0) }
fn leave_range(d: &Arc<Dialogue>) -> Value {
    observe(d,inside_wire(0));
    observe(d,outside_wire(1))
}

/// 通常範囲内での見物は、既存の周囲変化による判断機会へつなぐ。
/// 退出通知では範囲外の見物候補を出せないため、命令配送の検査とは入口を分ける。
fn environment_decision(d: &Arc<Dialogue>, value: Value) -> Value {
    let event=GameEvent::parse(value.clone()).unwrap();
    let response=observe(d,value);
    let mut action=Speech::new("surroundings_notice","");
    action.delivery=Delivery::Ambient;
    action.scope=crate::combat::model::Scope::Safe;
    let (turn,cancel)={
        let mut data=d.data.lock().unwrap();
        let s=data.sessions.get_mut("s").unwrap();
        let sensed=super::super::body_runtime::event(s,&event);
        crate::environment::reaction::attach(&mut action,&sensed,&json!({}),&d.config.combat);
        s.conversation_observation.sample_notice(1000,1,true);
        let mut changed=s.conversation_observation.context().unwrap();
        changed.observations["structure"]=json!("fixture_notice");
        s.conversation_observation.observe(changed);
        s.conversation_observation.sample_notice(1001,1,true);
        Dialogue::queue_actions(&mut data,"s",&[action.clone()],None)
    };
    tokio::spawn(d.clone().run_warning("s".into(),turn,vec![action],cancel));
    response
}
async fn until(condition: impl Fn() -> bool) {
    tokio::time::timeout(Duration::from_secs(3),async {
        while !condition() { tokio::time::sleep(Duration::from_millis(5)).await; }
    }).await.expect("bounded test wait");
}
async fn completed(d: &Dialogue, mock: &Mock) {
    until(|| !mock.seen.lock().unwrap().is_empty()).await;
    until(|| d.data.lock().unwrap().sessions["s"].body_interest_pending.is_none()).await;
}
const INSPECT: &str = r#"{"action":"silent","speech":"","body_action":{"action":"inspect","target_id":"c1"}}"#;

fn arrived(command: &Value, sequence: i64) -> Value {
    let mut receipt = wire(sequence);
    receipt["command_results"] = json!([{"command_id":command["command_id"],"command_type":"inspect_body_target",
        "status":"succeeded","executed_at":chrono::Utc::now(),"detail_code":"arrived",
        "body_id":command["context"]["body_id"],"epoch":command["context"]["epoch"]}]);
    receipt
}

/// 新しいfresh観測で30秒後の再選択可否を確認する。実時間待ちや実モデルを使わない。
fn can_revisit_after_thirty_seconds(d: &Dialogue) -> bool {
    let now = Instant::now() + Duration::from_secs(31);
    let observation = wire(100);
    let sample = serde_json::from_value(observation["body_observation"]["sample"].clone()).unwrap();
    let autonomy = serde_json::from_value(observation["body_autonomy"].clone()).unwrap();
    let mut data = d.data.lock().unwrap();
    let s = data.sessions.get_mut("s").unwrap();
    s.body_autonomy.observe(Some(&autonomy),Some(&sample),now,Some(0));
    s.body_autonomy.prepare(now).is_some_and(|p| !p.candidates.is_empty())
}

#[tokio::test]
async fn cancellation_is_applied_before_recording_arrival() {
    for transition in ["epoch", "unavailable", "pause", "input", "mode"] {
        let mock = Mock::new(Duration::ZERO,INSPECT).await;
        let d = server(&mock,true);
        environment_decision(&d,wire(1)); completed(&d,&mock).await;
        let command = observe(&d,wire(2))["commands"][0].clone();
        let mut receipt = arrived(&command,3);
        match transition {
            "epoch" => {
                receipt["body_observation"]["sample"]["context"]["epoch"] = json!(COW);
                receipt["body_autonomy"]["context"]["epoch"] = json!(COW);
            }
            "unavailable" => receipt["body_observation"] = json!({"version":3,"status":"unavailable","reason":"not_bound"}),
            "pause" => receipt["world"]["game_paused"] = json!(true),
            "input" => { d.submit(Some("s"), "こんにちは", "text"); },
            _ => receipt["body_autonomy"]["mode"] = json!("direct"),
        }
        assert_eq!(observe(&d,receipt)["commands"],json!([]),"{transition}");
        assert!(can_revisit_after_thirty_seconds(&d),"cancelled arrival must not add a visit: {transition}");
        d.shutdown().await;
    }
}

#[tokio::test]
async fn a_receipt_on_a_duplicate_event_still_records_one_real_arrival() {
    let mock = Mock::new(Duration::ZERO,INSPECT).await;
    let d = server(&mock,true);
    environment_decision(&d,wire(1)); completed(&d,&mock).await;
    let command = observe(&d,wire(2))["commands"][0].clone();
    let receipt = arrived(&command,2);
    for _ in 0..2 {
        let response = observe(&d,receipt.clone());
        assert_eq!(response["commands"],json!([]));
        assert_eq!(response["acknowledged_command_ids"],json!([command["command_id"]]));
    }
    assert!(!can_revisit_after_thirty_seconds(&d));
    d.shutdown().await;
}

#[tokio::test]
async fn model_choice_reaches_existing_commands_and_terminal_ack_once() {
    let mock = Mock::new(Duration::ZERO,INSPECT).await;
    let d = server(&mock,true);
    let initial = environment_decision(&d,wire(1));
    assert_eq!(initial["commands"],json!([]));
    completed(&d,&mock).await;
    let delivered = observe(&d,wire(2));
    let command = delivered["commands"][0].clone();
    assert_eq!(command["type"],"inspect_body_target");
    assert!(uuid::Uuid::parse_str(command["command_id"].as_str().unwrap()).is_ok());
    assert_eq!(command["target"]["entity_id"],COW);
    assert_eq!(command["target"]["position"],json!({"x":12.0,"y":64.0,"z":23.0}));
    let retry = observe(&d,wire(3));
    assert_eq!(retry["commands"][0],command);
    let mut receipt = wire(4);
    receipt["command_results"] = json!([{"command_id":command["command_id"],"command_type":"inspect_body_target",
        "status":"succeeded","executed_at":chrono::Utc::now(),"detail_code":"arrived",
        "body_id":command["context"]["body_id"],"epoch":command["context"]["epoch"]}]);
    let acknowledged = observe(&d,receipt.clone());
    assert_eq!(acknowledged["commands"],json!([]));
    assert_eq!(acknowledged["acknowledged_command_ids"],json!([command["command_id"]]));
    assert_eq!(observe(&d,receipt)["commands"],json!([]));
    {
        let requests = mock.seen.lock().unwrap();
        assert_eq!(requests.len(),1);
        let messages = requests[0]["messages"].as_array().unwrap();
        assert_eq!(messages[0]["content"],crate::companion_prompt::environment(false,true));
        let input: Value = serde_json::from_str(messages[1]["content"].as_str().unwrap()).unwrap();
        assert_eq!(input["body_options"]["candidates"][0]["id"],"c1");
        assert!(input["body_options"]["candidates"][0].get("position").is_none());
        assert!(!input["body_options"]["candidates"].to_string().contains(COW));
        assert!(d.data.lock().unwrap().sessions["s"].history.rows().iter().any(|r| r["reaction"] == "silent"));
    }
    d.shutdown().await;
}

#[tokio::test]
async fn observed_candidates_do_not_enable_missing_execution_capability() {
    let mock = Mock::new(Duration::ZERO,INSPECT).await;
    let d = server(&mock,false);
    assert_eq!(leave_range(&d)["commands"],json!([]));
    assert!(d.data.lock().unwrap().sessions["s"].body_interest_pending.is_none());
    assert!(mock.seen.lock().unwrap().is_empty());
    d.shutdown().await;
}

#[tokio::test]
async fn pause_or_manual_interrupt_discards_already_selected_command() {
    for pause in [true,false] {
        let mock = Mock::new(Duration::ZERO,INSPECT).await;
        let d = server(&mock,true);
        environment_decision(&d,wire(1));
        completed(&d,&mock).await;
        assert_eq!(d.data.lock().unwrap().sessions["s"].body_autonomy.commands(Instant::now(),chrono::Utc::now()).len(),1);
        let mut next = wire(2);
        if pause { next["world"]["game_paused"] = json!(true); } else { d.interrupt("s"); }
        assert_eq!(observe(&d,next)["commands"],json!([]));
        assert!(d.data.lock().unwrap().sessions["s"].body_autonomy.commands(Instant::now(),chrono::Utc::now()).is_empty());
        d.shutdown().await;
    }
}

#[tokio::test]
async fn new_body_epoch_cancels_inflight_generation_and_releases_conversation_slot() {
    let mock = Mock::new(Duration::from_secs(2),INSPECT).await;
    let d = server(&mock,true);
    environment_decision(&d,wire(1));
    until(|| !mock.seen.lock().unwrap().is_empty()).await;
    let mut changed = wire(2);
    let epoch = json!("55555555-5555-4555-8555-555555555555");
    changed["body_observation"]["sample"]["context"]["epoch"] = epoch.clone();
    changed["body_autonomy"]["context"]["epoch"] = epoch;
    assert_eq!(observe(&d,changed)["commands"],json!([]));
    tokio::time::timeout(Duration::from_millis(800),async {
        // 候補台帳は観測受理時に失効し、共有環境生成はmonitorが取消して会話枠を返す。
        until(|| d.data.lock().unwrap().sessions["s"].body_interest_pending.is_none()
            && d.serial.available_permits() == 1).await;
    }).await.unwrap();
    assert_eq!(d.serial.available_permits(),1);
    assert!(d.data.lock().unwrap().sessions["s"].body_autonomy.commands(Instant::now(),chrono::Utc::now()).is_empty());
    assert_eq!(mock.seen.lock().unwrap().len(),1);
    d.shutdown().await;
}

#[tokio::test]
async fn malformed_model_choice_has_no_movement_and_no_automatic_retry() {
    let mock = Mock::new(Duration::ZERO,r#"{"action":"silent","speech":"","body_action":{"action":"inspect","target_id":"invented"}}"#).await;
    let d = server(&mock,true);
    environment_decision(&d,wire(1));
    completed(&d,&mock).await;
    assert_eq!(observe(&d,wire(2))["commands"],json!([]));
    d.shutdown().await;
    assert_eq!(mock.seen.lock().unwrap().len(),1);
}

#[tokio::test]
async fn expired_original_body_sample_cancels_a_fresh_candidate_command() {
    let mock = Mock::new(Duration::ZERO,INSPECT).await;
    let d = server(&mock,true);
    environment_decision(&d,wire(1));
    completed(&d,&mock).await;
    {
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        assert!(s.body_autonomy.available(Instant::now()));
        let body: crate::body::contracts::Observation = serde_json::from_value(wire(1)["body_observation"].clone()).unwrap();
        s.body_observation = crate::body::State::default();
        s.body_observation.observe(Some(&body),Some(OWNER),Some("minecraft:overworld"),Instant::now()-Duration::from_secs(4),Some(0));
        assert!(s.body_observation.accepted(Instant::now()).is_none());
        refresh(s,d.clock.elapsed().as_millis() as u64);
        assert!(s.body_autonomy.commands(Instant::now(),chrono::Utc::now()).is_empty());
    }
    d.shutdown().await;
}

#[test]
fn range_exit_is_once_until_return_and_starts_with_a_baseline() {
    let context: Context = serde_json::from_value(wire(1)["body_autonomy"]["context"].clone()).unwrap();
    let mut notice = BoundaryNotice::default();
    notice.observe(&context,9.0,8.0);
    assert!(!notice.pending,"first observation is not an exit");
    for distance in [3.0,4.0,8.0] {
        notice.observe(&context,distance,8.0);
        assert!(!notice.pending,"recovery distance is not the notification boundary");
    }
    notice.observe(&context,8.001,8.0);
    assert!(notice.pending);
    notice.considered();
    for distance in [8.001,9.0,12.0,20.0] {
        notice.observe(&context,distance,8.0);
        assert!(!notice.pending,"staying outside must not notify again");
    }
    notice.observe(&context,8.0,8.0);
    notice.observe(&context,9.0,8.0);
    assert!(notice.pending,"returning inside arms the next exit");
    notice.observe(&context,2.0,8.0);
    assert!(!notice.pending,"return before dispatch cancels the held exit");
}

#[test]
fn a_new_body_generation_does_not_inherit_a_range_exit() {
    let context: Context = serde_json::from_value(wire(1)["body_autonomy"]["context"].clone()).unwrap();
    let mut notice = BoundaryNotice::default();
    notice.observe(&context,2.0,8.0);
    notice.observe(&context,9.0,8.0);
    assert!(notice.pending);
    let mut changed = context.clone();
    changed.epoch = "55555555-5555-4555-8555-555555555555".into();
    notice.observe(&changed,9.0,8.0);
    assert!(!notice.pending);
    notice.observe(&changed,2.0,8.0);
    notice.observe(&changed,9.0,8.0);
    assert!(notice.pending);
}

#[test]
fn changing_the_follow_setting_resets_the_boundary_without_faking_an_exit() {
    let context: Context = serde_json::from_value(wire(1)["body_autonomy"]["context"].clone()).unwrap();
    let mut notice=BoundaryNotice::default();
    notice.observe(&context,7.0,8.0);
    notice.observe(&context,7.0,6.0);
    assert!(!notice.pending,"shrinking the setting is not owner movement");
    notice.observe(&context,6.0,6.0);
    notice.observe(&context,7.0,6.0);
    assert!(notice.pending);
    notice.observe(&context,7.0,10.0);
    assert!(!notice.pending,"a setting change discards the old pending boundary");
    notice.observe(&context,10.0,10.0);
    notice.observe(&context,10.001,10.0);
    assert!(notice.pending);
}

/// HTTPモデルを呼ばず、共有warning turnへの準備と返却結果の配線だけを検査する。
fn direct_decision() -> (Arc<Dialogue>, String) {
    let d = Dialogue::new(DialogueConfig { llm_enabled:false, audio_enabled:false, ..Default::default() }).unwrap();
    d.register("s","呼称",false);
    d.bind_body_owner("s",Some(OWNER));
    d.set_execution_capabilities("s", &["body.inspect_target.v2".into()]);
    let event=GameEvent::parse(outside_wire(1)).unwrap();
    let mut data=d.data.lock().unwrap();
    let s=data.sessions.get_mut("s").unwrap();
    let inside=GameEvent::parse(inside_wire(0)).unwrap();
    super::super::body_runtime::observe(s,&inside);
    observe_boundary(s,Instant::now());
    super::super::body_runtime::observe(s,&event);
    observe_boundary(s,Instant::now());
    s.latest=Some(event.clone());
    s.environment_latest=Some(event.clone());
    let mut action=Speech::new(KIND,"");
    action.delivery=Delivery::Ambient;
    crate::environment::reaction::attach(&mut action,&event,&json!({}),&d.config.combat);
    action.leaf.as_mut().unwrap().details["reaction_context"]["world_context"]=json!({"observations":{}});
    let (turn,_rx)=Dialogue::queue_actions(&mut data,"s",&[action.clone()],None);
    let s=data.sessions.get_mut("s").unwrap();
    attach(s,&turn,&mut action,0);
    assert!(s.body_interest_pending.is_some());
    assert_eq!(action.leaf.unwrap().details["reaction_context"]["world_context"]["observations"]["reassurance"]["value"],20.0);
    drop(data);
    (d,turn)
}

#[test]
fn code_only_own_warning_accepts_body_choice_and_completion_retains_delivery() {
    let (d,turn)=direct_decision();
    let mut data=d.data.lock().unwrap();
    let s=data.sessions.get_mut("s").unwrap();
    apply(s,&turn,Some(&json!({"body_action":{"action":"owner_near","target_id":null}})),1);
    refresh(s,2);
    let commands=s.body_autonomy.commands(Instant::now(),chrono::Utc::now());
    assert_eq!(commands.len(),1);
    assert_eq!(commands[0]["purpose"],"owner_near");
    assert_eq!(commands[0]["target"],Value::Null);
    finish(s,&turn);
    s.warning=None;
    refresh(s,3);
    assert_eq!(s.body_autonomy.commands(Instant::now(),chrono::Utc::now()),commands);
}

#[test]
fn code_only_stale_input_and_unrelated_warning_cannot_apply_body_choice() {
    for changed_turn in [false,true] {
        let (d,turn)=direct_decision();
        let mut data=d.data.lock().unwrap();
        let s=data.sessions.get_mut("s").unwrap();
        if changed_turn { s.warning.as_mut().unwrap().turn="another".into(); }
        else { s.input_generation += 1; }
        apply(s,&turn,Some(&json!({"body_action":{"action":"owner_near","target_id":null}})),1);
        assert!(s.body_autonomy.commands(Instant::now(),chrono::Utc::now()).is_empty());
    }
}

#[test]
fn code_only_invalid_body_selection_never_executes_fallback_move() {
    let (d,turn)=direct_decision();
    let mut data=d.data.lock().unwrap();
    let s=data.sessions.get_mut("s").unwrap();
    apply(s,&turn,Some(&json!({"text":"そやな。","body_action":{"action":"inspect","target_id":"missing"}})),1);
    assert!(s.body_autonomy.commands(Instant::now(),chrono::Utc::now()).is_empty());
    assert_eq!(s.warning.as_ref().unwrap().turn,turn);
}

#[tokio::test]
async fn low_reassurance_and_candidates_do_not_start_periodic_model_calls() {
    let mock = Mock::new(Duration::ZERO,INSPECT).await;
    let d = server(&mock,true);
    for sequence in 1..=5 {
        let mut event = inside_wire(sequence);
        event["body_autonomy"]["reassurance"]["value"] = json!(if sequence < 3 { 0.0 } else { 100.0 });
        observe(&d,event);
    }
    assert!(mock.seen.lock().unwrap().is_empty());
    assert!(d.data.lock().unwrap().sessions["s"].warning.is_none());
    assert!(!d.data.lock().unwrap().sessions["s"].body_interest_notice.pending);
    d.shutdown().await;
}

#[tokio::test]
async fn an_outside_baseline_and_staying_outside_do_not_repeat_the_model_call() {
    let mock = Mock::new(Duration::ZERO,r#"{"action":"silent","speech":"","body_action":{"action":"stay","target_id":null}}"#).await;
    let d = server(&mock,true);
    observe(&d,outside_wire(1));
    assert!(d.data.lock().unwrap().sessions["s"].warning.is_none());
    assert!(mock.seen.lock().unwrap().is_empty());
    observe(&d,inside_wire(2));
    observe(&d,outside_wire(3));
    completed(&d,&mock).await;
    for sequence in 4..=20 { observe(&d,outside_wire(sequence)); }
    assert_eq!(mock.seen.lock().unwrap().len(),1);
    assert!(!d.data.lock().unwrap().sessions["s"].body_interest_notice.pending);
    d.shutdown().await;
}

#[tokio::test]
async fn a_busy_range_exit_is_held_once_and_a_return_before_dispatch_cancels_it() {
    for returns_inside in [false,true] {
        let mock = Mock::new(Duration::ZERO,r#"{"action":"silent","speech":"","body_action":{"action":"stay","target_id":null}}"#).await;
        let d = server(&mock,true);
        observe(&d,inside_wire(0));
        d.data.lock().unwrap().sessions.get_mut("s").unwrap().last_player_input = Some(d.clock.elapsed().as_millis() as u64);
        observe(&d,outside_wire(1));
        observe(&d,outside_wire(2));
        assert!(mock.seen.lock().unwrap().is_empty());
        assert!(d.data.lock().unwrap().sessions["s"].body_interest_notice.pending);
        if returns_inside { observe(&d,inside_wire(3)); }
        d.data.lock().unwrap().sessions.get_mut("s").unwrap().last_player_input = None;
        observe(&d,if returns_inside { inside_wire(4) } else { outside_wire(4) });
        if returns_inside {
            assert!(mock.seen.lock().unwrap().is_empty());
            assert!(!d.data.lock().unwrap().sessions["s"].body_interest_notice.pending);
            assert!(d.data.lock().unwrap().sessions["s"].warning.is_none());
        } else {
            completed(&d,&mock).await;
            assert_eq!(mock.seen.lock().unwrap().len(),1);
            assert!(!d.data.lock().unwrap().sessions["s"].body_interest_notice.pending);
        }
        d.shutdown().await;
    }
}

#[tokio::test]
async fn returning_while_waiting_for_the_generation_slot_discards_the_queued_exit() {
    let mock = Mock::new(Duration::ZERO,INSPECT).await;
    let d = server(&mock,true);
    let slot = d.serial.acquire().await.unwrap();
    leave_range(&d);
    assert!(d.data.lock().unwrap().sessions["s"].warning.is_some());
    observe(&d,inside_wire(2));
    assert!(!d.data.lock().unwrap().sessions["s"].body_interest_notice.pending);
    drop(slot);
    until(|| d.data.lock().unwrap().sessions["s"].warning.is_none()
        && d.serial.available_permits() == 1).await;
    assert!(mock.seen.lock().unwrap().is_empty());
    d.shutdown().await;
}

#[tokio::test]
async fn unified_request_exposes_only_current_action_names_and_descriptions() {
    for working in [false,true] {
        for candidates in [false,true] {
            let mock=Mock::new(Duration::ZERO,r#"{"action":"silent","speech":"","body_action":{"action":"stay","target_id":null}}"#).await;
            let d=server(&mock,true);
            let mut event=wire(1);
            event["body_autonomy"]["reassurance"]["owner_working"]=json!(working);
            if !candidates { event["body_autonomy"]["candidates"]=json!([]); }
            environment_decision(&d,event); completed(&d,&mock).await;
            {
                let requests=mock.seen.lock().unwrap();
                assert_eq!(requests.len(),1);
                let system=requests[0]["messages"][0]["content"].as_str().unwrap();
                assert!(system.contains("周囲の変化と会話も見て、"));
                for action in ["owner_near","owner_face","inspect","stay"] { assert!(!system.contains(action)); }
                let input:Value=serde_json::from_str(requests[0]["messages"][1]["content"].as_str().unwrap()).unwrap();
                assert_ne!(input["trigger"]["kind"],"owner_left_reassurance_range");
                let actions=&input["body_options"]["available_actions"];
                assert_eq!(actions["owner_near"],"そばへ戻る");
                assert_eq!(actions["stay"],"その場におる");
                assert_eq!(actions.get("owner_face").is_some(),!working);
                assert_eq!(actions.get("inspect").is_some(),candidates);
            }
            assert!(d.data.lock().unwrap().sessions["s"].body_autonomy.commands(Instant::now(),chrono::Utc::now()).is_empty());
            d.shutdown().await;
        }
    }
}

#[tokio::test]
async fn an_exit_uses_the_observed_follow_setting_instead_of_the_recovery_distance() {
    for follow_distance in [8.0,10.0] {
        let mock=Mock::new(Duration::ZERO,r#"{"action":"silent","speech":"","body_action":{"action":"stay","target_id":null}}"#).await;
        let d=server(&mock,true);
        for (sequence,distance) in [3.0,4.0,follow_distance].into_iter().enumerate() {
            observe(&d,range_wire(sequence as i64,distance,follow_distance));
            assert!(d.data.lock().unwrap().sessions["s"].warning.is_none());
            assert!(mock.seen.lock().unwrap().is_empty());
        }
        observe(&d,range_wire(3,follow_distance+0.1,follow_distance));
        completed(&d,&mock).await;
        {
            let requests=mock.seen.lock().unwrap();
            assert_eq!(requests.len(),1);
            let input:Value=serde_json::from_str(requests[0]["messages"][1]["content"].as_str().unwrap()).unwrap();
            assert_eq!(input["trigger"],json!({"kind":"owner_left_reassurance_range","range_blocks":follow_distance}));
            assert!(input["body_options"]["available_actions"].get("inspect").is_none());
        }
        observe(&d,range_wire(4,follow_distance+1.0,follow_distance));
        assert_eq!(mock.seen.lock().unwrap().len(),1);
        d.shutdown().await;
    }
}

fn follow_arrival_wire(sequence: i64, arrived: bool, working: bool) -> Value {
    let mut value=range_wire(sequence,if arrived {2.0} else {13.0},8.0);
    value["body_autonomy"]["activity"]=json!(if arrived {"holding"} else {"following"});
    value["body_autonomy"]["reason"]=json!(if arrived {"follow_settling"} else {"distance_follow"});
    value["body_autonomy"]["reassurance"]["owner_working"]=json!(working);
    value
}

#[tokio::test]
async fn return_arrival_uses_one_existing_request_with_only_current_face_or_stay_choices() {
    for working in [false,true] {
        let mock=Mock::new(Duration::ZERO,r#"{"action":"silent","speech":"","body_action":{"action":"stay","target_id":null}}"#).await;
        let d=server(&mock,true);
        observe(&d,follow_arrival_wire(1,false,false));
        assert!(mock.seen.lock().unwrap().is_empty());
        observe(&d,follow_arrival_wire(2,true,working)); completed(&d,&mock).await;
        {
            let requests=mock.seen.lock().unwrap(); assert_eq!(requests.len(),1);
            assert_eq!(requests[0]["messages"][0]["content"],crate::companion_prompt::environment(false,true));
            let input:Value=serde_json::from_str(requests[0]["messages"][1]["content"].as_str().unwrap()).unwrap();
            assert_eq!(input["trigger"]["kind"],"owner_arrived_near");
            let options=&input["body_options"];
            assert_eq!(options["candidates"],json!([]));
            assert_eq!(options["available_actions"].as_object().unwrap().len(),if working {1} else {2});
            assert!(options["available_actions"].get("stay").is_some());
            assert_eq!(options["available_actions"].get("owner_face").is_some(),!working);
            assert_eq!(input["world_context"]["observations"]["reassurance"]["owner_working"],working);
        }
        for seq in 3..12 { assert_eq!(observe(&d,follow_arrival_wire(seq,true,working))["commands"],json!([])); }
        assert_eq!(mock.seen.lock().unwrap().len(),1);
        d.shutdown().await;
    }
}

#[tokio::test]
async fn face_chosen_after_return_reaches_delivery_during_settling_and_does_not_loop() {
    let mock=Mock::new(Duration::ZERO,r#"{"action":"silent","speech":"","body_action":{"action":"owner_face","target_id":null}}"#).await;
    let d=server(&mock,true);
    observe(&d,follow_arrival_wire(1,false,false));
    observe(&d,follow_arrival_wire(2,true,false)); completed(&d,&mock).await;
    let command=observe(&d,follow_arrival_wire(3,true,false))["commands"][0].clone();
    assert_eq!(command["purpose"],"owner_face");
    let mut done=arrived(&command,4);
    done["body_autonomy"]["reason"]=json!("owner_face_dwell");
    observe(&d,done);
    for seq in 5..10 { observe(&d,follow_arrival_wire(seq,true,false)); }
    assert_eq!(mock.seen.lock().unwrap().len(),1);
    assert!(!d.data.lock().unwrap().sessions["s"].body_autonomy.owner_arrival_pending(Instant::now()));
    d.shutdown().await;
}

#[tokio::test]
async fn a_queued_return_checks_work_and_departure_again_before_generation() {
    for departs in [false,true] {
        let mock=Mock::new(Duration::ZERO,r#"{"action":"silent","speech":"","body_action":{"action":"stay","target_id":null}}"#).await;
        let d=server(&mock,true); let slot=d.serial.acquire().await.unwrap();
        observe(&d,follow_arrival_wire(1,false,false)); observe(&d,follow_arrival_wire(2,true,false));
        assert!(d.data.lock().unwrap().sessions["s"].warning.is_some());
        observe(&d,follow_arrival_wire(3,!departs,true));
        drop(slot);
        until(|| d.data.lock().unwrap().sessions["s"].warning.is_none() && d.serial.available_permits()==1).await;
        let requests=mock.seen.lock().unwrap();
        if departs { assert!(requests.is_empty()); }
        else {
            assert_eq!(requests.len(),1);
            let input:Value=serde_json::from_str(requests[0]["messages"][1]["content"].as_str().unwrap()).unwrap();
            assert_eq!(input["body_options"]["available_actions"],json!({"stay":"その場におる"}));
        }
        drop(requests); d.shutdown().await;
    }
}
