//! パート1: 観測受付→共通投影→通常／環境のモデル要求を通す受入テスト。
//! 期待値は既知の配置から固定し、本体の計算や文章化関数で生成しない。
//! 句読点・文の分割は固定せず、主語・基準・方向・高さ・距離を確認する。
use super::{Dialogue, DialogueConfig, body_runtime, chat_context, environment_runtime};
use crate::{
    chat_materials,
    events::GameEvent,
    types::{GenerationRequest, Role},
};
use serde_json::{Value, json};
use std::sync::Arc;

const OWNER: &str = "11111111-1111-4111-8111-111111111111";
const INVISIBLE: &str = "ドギドからプレイヤーの姿は見えていない";

fn event(sequence: i64, change: impl FnOnce(&mut Value)) -> GameEvent {
    let mut body: Value =
        serde_json::from_str(include_str!("fixtures/body-observation-v3.json")).unwrap();
    body["age_ms"] = json!(0);
    body["sample"]["server_tick"] = json!(100 + sequence);
    body["sample"]["senses"]["source_server_tick"] = json!(100 + sequence);
    body["sample"]["snapshot_revision"] = json!(200 + sequence);
    body["sample"]["self_pose"]["position"] = json!({"x":-6,"y":68,"z":6});
    body["sample"]["self_pose"]["eye_position"] = json!({"x":-6,"y":69,"z":6});
    body["sample"]["self_pose"]["yaw"] = json!(-45);
    body["sample"]["player_pose"]["position"] = json!({"x":0,"y":64,"z":0});
    body["sample"]["player_pose"]["yaw"] = json!(0);
    body["sample"]["vision"]["player_visibility"] = json!("not_visible");
    body["sample"]["vision"]["reason"] = json!("occluded");
    change(&mut body);
    GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"position-acceptance",
        "observed_at":chrono::Utc::now(),"sequence":sequence,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20},
        "world":{"biome":"plains","time_phase":"day","sky_visible":true,"local_light":15,"danger_darkness_score":0},
        "body_observation":body})).unwrap()
}

fn server() -> Arc<Dialogue> {
    let mut config = DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        ..Default::default()
    };
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    let d = Dialogue::new(config).unwrap();
    d.register("s", "試験プレイヤー", true);
    d.bind_body_owner("s", Some(OWNER));
    d
}

fn requests(
    d: &Dialogue,
    event: &GameEvent,
    question: &str,
) -> (Value, GenerationRequest, GenerationRequest) {
    let data = d.data.lock().unwrap();
    let s = &data.sessions["s"];
    let native =
        chat_context::capture(s, event, &d.config.combat, Default::default(), None).unwrap();
    let input = chat_materials::PlayerInput {
        raw_text: question.into(),
        semantic_text: question.into(),
        ..Default::default()
    };
    let chat_materials::Prepared::Reply(reply) = chat_materials::prepare(
        native.sensory_event.as_ref().unwrap(),
        &native.settings,
        &input,
        &native.context,
        &native.snapshot,
        &chat_context::CatalogLabels,
    )
    .unwrap() else {
        panic!("通常返答の生成要求まで到達すること");
    };
    let reply_request = reply.request("acceptance-no-model-call", 256).unwrap();
    let sensory = body_runtime::event(s, event);
    // 環境判断の発火条件は別パート。ここでは選択済みの環境発話へ同じ観測を渡す。
    let mut action = crate::combat::model::Speech::new("reassurance_notice", "");
    action.delivery = crate::combat::model::Delivery::Ambient;
    crate::environment::reaction::attach(&mut action, &sensory, &d.config.combat);
    assert!(environment_runtime::refresh_dialogue_context(
        s,
        std::slice::from_mut(&mut action)
    ));
    let environment = crate::environment::reaction::Prepared::new(
        &json!({
        "kind":"environment_reaction","model":"acceptance-no-model-call","max_tokens":256,
        "fallback_text":"","details":action.leaf.unwrap().details}),
        "acceptance-no-model-call",
    )
    .unwrap();
    (
        json!(native.context.world_context.unwrap()),
        reply_request,
        environment.request,
    )
}

fn user_material(request: &GenerationRequest) -> String {
    request
        .messages
        .iter()
        .filter(|m| m.role == Role::User)
        .map(|m| m.content.as_str())
        .collect::<Vec<_>>()
        .join("\n")
}

fn clauses(value: &Value) -> Vec<String> {
    let mut out = Vec::new();
    fn visit(value: &Value, out: &mut Vec<String>) {
        match value {
            Value::String(text) => out.extend(
                text.split('。')
                    .filter(|s| !s.is_empty())
                    .map(|s| s.replace(['、', ' '], "")),
            ),
            Value::Array(rows) => rows.iter().for_each(|row| visit(row, out)),
            Value::Object(fields) => fields.values().for_each(|row| visit(row, out)),
            _ => {}
        }
    }
    visit(value, &mut out);
    out
}

fn assert_direction(value: &Value, subject: &str, reference: &str, direction: &str, height: &str) {
    let lines = clauses(&value["facts"]);
    let target = format!("{subject}は");
    let base = format!("{reference}の");
    let bearing = format!("向きを基準に{direction}にいる");
    assert_eq!(
        lines
            .iter()
            .filter(|s| s.starts_with(&target) && s.contains(&base) && s.contains(&bearing))
            .count(),
        1,
        "{subject}の位置を{reference}の向きで{direction}と一度だけ明示すること: {lines:?}"
    );
    let vertical = format!("{subject}の足元は{reference}の足元より{height}にある");
    assert_eq!(
        lines.iter().filter(|s| s.as_str() == vertical).count(),
        1,
        "足元の上下を主語付きで明示すること: {lines:?}"
    );
}

fn assert_distance(value: &Value, blocks: u64) {
    let lines: Vec<_> = clauses(&value["facts"])
        .into_iter()
        .filter(|s| s.contains("距離") && s.contains("ブロック"))
        .collect();
    assert_eq!(lines.len(), 1, "距離は一か所に表示すること: {lines:?}");
    assert!(
        lines[0].contains(&format!("距離は{blocks}ブロック")),
        "会話用距離は実距離の小数点以下切り捨て（{blocks}ブロック）: {lines:?}"
    );
}

fn assert_position(value: &Value, dogido_direction: &str, player_direction: &str) {
    assert_direction(value, "ドギド", "プレイヤー", dogido_direction, "上");
    assert_direction(value, "プレイヤー", "ドギド", player_direction, "下");
}

fn current_body(world: &Value) -> &Value {
    &world["observations"]["body_observation"]["current"]
}

async fn check_route(scope: &str) {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let event = event(1, |_| {});
    d.observe("s", event.clone(), None);
    let (world, reply, ambient) = requests(&d, &event, "ドギドどこ？");
    let text = match scope {
        "common" => world["observations"].to_string(),
        "reply" => user_material(&reply),
        "environment" => user_material(&ambient),
        _ => unreachable!(),
    };
    let relation = &current_body(&world)["position_relation"];
    assert_direction(relation, "ドギド", "プレイヤー", "右前", "上");
    assert_direction(relation, "プレイヤー", "ドギド", "左", "下");
    // 最終要求にも投影済みの各事実が一度だけ届くこと（systemの指示文一致ではない）。
    for clause in clauses(&relation["facts"])
        .into_iter()
        .chain([INVISIBLE.to_string()])
    {
        assert_eq!(
            text.replace(['、', ' '], "").matches(&clause).count(),
            1,
            "{scope}: 共通観測の事実が一度だけ届くこと: {clause}\n{text}"
        );
    }
    for raw in [
        "relative_positions",
        "coordinate_system",
        "view_yaw",
        "eye_position",
        OWNER,
    ] {
        assert!(
            !text.contains(raw),
            "{scope}: 生の座標・視点の選択をモデルへ戻さない: {raw}"
        );
    }
    drop(permit);
    d.shutdown().await;
}

// P01: 既知の位置で、誰が誰の向きを基準にどちらにいるかを主語付きで渡す。位置が分かっても、姿は見えない状態を区別する。
#[tokio::test]
async fn common_observation_distinguishes_subject_reference_and_invisible_position() {
    check_route("common").await;
}
// P02: 「ドギドどこ？」の通常返答に、プレイヤー基準の位置を含む正しい事実が届くことを確認する。
#[tokio::test]
async fn ordinary_reply_receives_subject_facts() {
    check_route("reply").await;
}
// P03: 環境から自発的に話す場合にも、通常返答と同じ主語付きの位置・視認情報が届くことを確認する。
#[tokio::test]
async fn environment_receives_the_same_subject_facts() {
    check_route("environment").await;
}

// P04: プレイヤーだけが半回転したとき、プレイヤー基準の方向だけが変わり、ドギド基準の方向・上下・距離は保たれる。
#[tokio::test]
async fn rotating_only_player_changes_only_player_reference() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let mut distances = Vec::new();
    for (seq, yaw, direction) in [(1, 0, "右前"), (2, -180, "左後ろ")] {
        let e = event(seq, |b| b["sample"]["player_pose"]["yaw"] = json!(yaw));
        d.observe("s", e.clone(), None);
        let (world, _, _) = requests(&d, &e, "ドギドどこ？");
        assert_position(&current_body(&world)["position_relation"], direction, "左");
        distances.push(
            clauses(&current_body(&world)["position_relation"]["facts"])
                .into_iter()
                .filter(|s| s.contains("距離"))
                .collect::<Vec<_>>(),
        );
    }
    assert_eq!(distances[0], distances[1]);
    assert_eq!(distances[0].len(), 1);
    drop(permit);
    d.shutdown().await;
}

// P05: ドギドだけが半回転したとき、ドギド基準の方向だけが変わり、プレイヤー基準の方向・上下・距離は保たれる。
#[tokio::test]
async fn rotating_only_dogido_changes_only_dogido_reference() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let mut distances = Vec::new();
    for (seq, yaw, direction) in [(1, -45, "左"), (2, 135, "右")] {
        let e = event(seq, |b| b["sample"]["self_pose"]["yaw"] = json!(yaw));
        d.observe("s", e.clone(), None);
        let (world, _, _) = requests(&d, &e, "プレイヤーはドギドから見てどこ？");
        assert_position(
            &current_body(&world)["position_relation"],
            "右前",
            direction,
        );
        distances.push(
            clauses(&current_body(&world)["position_relation"]["facts"])
                .into_iter()
                .filter(|s| s.contains("距離"))
                .collect::<Vec<_>>(),
        );
    }
    assert_eq!(distances[0], distances[1]);
    assert_eq!(distances[0].len(), 1);
    drop(permit);
    d.shutdown().await;
}

async fn check_distance(distance: f64, expected: u64) {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let e = event(1, |b| {
        b["sample"]["self_pose"]["position"] = json!({"x":distance,"y":64,"z":0});
        b["sample"]["self_pose"]["eye_position"] = json!({"x":distance,"y":65,"z":0});
    });
    d.observe("s", e.clone(), None);
    let (world, reply, ambient) = requests(&d, &e, "何ブロック離れている？");
    let relation = &current_body(&world)["position_relation"];
    assert_distance(relation, expected);
    let fact = clauses(&relation["facts"])
        .into_iter()
        .find(|s| s.contains("距離"))
        .unwrap();
    for request in [reply, ambient] {
        assert_eq!(
            user_material(&request)
                .replace(['、', ' '], "")
                .matches(&fact)
                .count(),
            1
        );
    }
    drop(permit);
    d.shutdown().await;
}
// P06: 9.38ブロックの距離は、小数点以下を切り捨てて9ブロックと渡す。
#[tokio::test]
async fn distance_9_38_is_9_blocks() {
    check_distance(9.38, 9).await;
}
// P07: 9.99ブロックを先に四捨五入して10にせず、9ブロックと渡す。
#[tokio::test]
async fn distance_9_99_is_9_not_10_blocks() {
    check_distance(9.99, 9).await;
}
// P08: 0.99ブロックの距離は1へ丸めず、0ブロックと渡す。
#[tokio::test]
async fn distance_0_99_is_0_not_1_block() {
    check_distance(0.99, 0).await;
}
// P09: 10.01ブロックの距離は10ブロックと渡し、小数表示を残さない。
#[tokio::test]
async fn distance_10_01_is_10_blocks_without_decimal_suffix() {
    check_distance(10.01, 10).await;
}

// P10: 観測が取得できなくなったら、通常返答・環境反応のどちらも過去の位置や視認情報で穴埋めしない。
#[tokio::test]
async fn unavailable_observation_clears_previous_position_in_both_model_routes() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let e = event(1, |_| {});
    d.observe("s", e.clone(), None);
    let (old_world, _, _) = requests(&d, &e, "ドギドどこ？");
    assert_eq!(
        current_body(&old_world)["position_relation"]["position_status"],
        "available"
    );
    let unknown =
        event(
            2,
            |b| *b = json!({"version":3,"status":"unavailable","reason":"not_spawned"}),
        );
    d.observe("s", unknown.clone(), None);
    let (world, reply, ambient) = requests(&d, &unknown, "今も右前にいる？");
    assert_eq!(
        world["observations"]["body_observation"]["status"],
        "unavailable"
    );
    assert_eq!(world["observations"]["sensory_status"], "unavailable");
    assert!(current_body(&world).is_null());
    for request in [reply, ambient] {
        let text = user_material(&request);
        for old_fact in clauses(&current_body(&old_world)["position_relation"]["facts"]) {
            assert!(
                !text.replace(['、', ' '], "").contains(&old_fact),
                "未取得時に過去の位置を埋め戻さない: {old_fact}"
            );
        }
        assert!(
            !text.contains(INVISIBLE),
            "未取得を視認不可という現在観測へ変えない"
        );
    }
    drop(permit);
    d.shutdown().await;
}

// P11: 見える／見えないだけが変わったとき、既知の位置・上下・距離は変えない。
#[tokio::test]
async fn changing_visibility_preserves_known_position() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let first = event(1, |_| {});
    d.observe("s", first.clone(), None);
    let (before, _, _) = requests(&d, &first, "見えている？");
    let visible = event(2, |b| {
        b["sample"]["vision"]["player_visibility"] = json!("visible");
        b["sample"]["vision"]["reason"] = json!("in_view");
    });
    d.observe("s", visible.clone(), None);
    let (after, _, _) = requests(&d, &visible, "見えている？");
    assert_eq!(
        current_body(&before)["position_relation"],
        current_body(&after)["position_relation"]
    );
    assert!(
        current_body(&before)["vision"]
            .to_string()
            .contains(INVISIBLE)
    );
    assert!(
        current_body(&after)["vision"]
            .to_string()
            .contains("ドギドからプレイヤーの姿が見えている")
    );
    drop(permit);
    d.shutdown().await;
}

// P12: 変化前後の表示は主語付きの事実に揃え、内部で変化を比べるための方向・距離データは保つ。
#[tokio::test]
async fn position_changes_use_subject_facts_and_internal_comparison_keeps_numeric_data() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let first = event(1, |_| {});
    d.observe("s", first.clone(), None);
    let second = event(2, |b| b["sample"]["player_pose"]["yaw"] = json!(-180));
    d.observe("s", second.clone(), None);
    let (world, _, _) = requests(&d, &second, "向きを変えたらどこ？");
    let delta = world["changes"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["kind"] == "body_position_relation")
        .expect("位置関係の変化を保持");
    assert_position(&delta["before"], "右前", "左");
    assert_position(&delta["now"], "左後ろ", "左");
    assert_eq!(delta["now"], current_body(&world)["position_relation"]);
    {
        let data = d.data.lock().unwrap();
        let internal = data.sessions["s"]
            .conversation_observation
            .context()
            .unwrap();
        let change = internal
            .changes
            .iter()
            .find(|c| c.kind == "body_position_relation")
            .unwrap();
        assert_eq!(
            change.before["relative_positions"][1]["horizontal"],
            "front_right"
        );
        assert_eq!(
            change.now["relative_positions"][1]["horizontal"],
            "back_left"
        );
        assert!(change.before.get("facts").is_none() && change.now.get("facts").is_none());
    }
    drop(permit);
    d.shutdown().await;
}
