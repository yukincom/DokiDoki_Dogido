//! 見込み: 聴いた音を共通観測へ一度だけ載せ、保持後も時刻と由来を残せば、音源を取り違えにくくなる。合格: 通常・環境の実入力で音種・時刻・確度・由来が一致する。観測は模擬で、Fabric採取と実モデルの返答は別確認。
use super::{Dialogue, DialogueConfig, body_runtime, chat_context, environment_runtime};
use crate::{
    chat_materials,
    events::GameEvent,
    types::{GenerationRequest, Role},
};
use chrono::{DateTime, Duration, Utc};
use serde_json::{Value, json};
use std::sync::Arc;

const OWNER: &str = "11111111-1111-4111-8111-111111111111";
const SOURCE: &str = "44444444-4444-4444-8444-444444444444";
const STONE_STEP: &str = "minecraft:block.stone.step";

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

fn sound(kind: &str, event: Option<&str>, source: Option<&str>, age_ms: u64) -> Value {
    json!({"type":kind,"source_id":source,"sound_event":event,
        "direction":{"horizontal":"right"},"distance_band":"close",
        "certainty":"high","heard_ago_ms":age_ms})
}

fn event(change: impl FnOnce(&mut Value)) -> GameEvent {
    let mut body: Value =
        serde_json::from_str(include_str!("fixtures/body-observation-v3.json")).unwrap();
    body["age_ms"] = json!(150);
    body["sample"]["senses"]["ambient_sounds"] =
        json!([sound("block:stone", Some(STONE_STEP), Some(SOURCE), 1200)]);
    let mut raw = json!({"schema_version":"2026-05-24","adapter":"hearing-acceptance",
        "observed_at":Utc::now()-Duration::milliseconds(500),"sequence":1,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20},
        "world":{"biome":"plains","time_phase":"day","sky_visible":true,"local_light":15,"danger_darkness_score":0},
        "body_observation":body});
    change(&mut raw);
    GameEvent::parse(raw).unwrap()
}

struct Requests {
    common: Value,
    normal: GenerationRequest,
    environment: GenerationRequest,
    prior_sound_summary: String,
}

fn requests(d: &Dialogue, raw: &GameEvent) -> Requests {
    let data = d.data.lock().unwrap();
    let s = &data.sessions["s"];
    let native = chat_context::capture(s, raw, &d.config.combat, Default::default(), None).unwrap();
    let input = chat_materials::PlayerInput {
        raw_text: "今はどんな音が聞こえる？".into(),
        semantic_text: "今はどんな音が聞こえる？".into(),
        asks_about_sound: true,
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
        panic!("音の質問が通常の一回生成へ到達すること");
    };
    let mut action = crate::combat::model::Speech::new("reassurance_notice", "");
    action.delivery = crate::combat::model::Delivery::Ambient;
    crate::environment::reaction::attach(
        &mut action,
        &body_runtime::event(s, raw),
        &d.config.combat,
    );
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
    let result = Requests {
        common: json!(native.context.world_context.unwrap()),
        normal: reply.request("acceptance-no-model-call", 256).unwrap(),
        environment: environment.request,
        prior_sound_summary: native.snapshot.hearing.summary,
    };
    // 投影途中だけでなく両方の最終user入力を読む。時刻・確度・由来の欠落も比較する。
    let normal = user_material(&result.normal);
    let world_text = normal
        .split_once("【現在の環境観測と変化（会話履歴とは別）】\n")
        .expect("通常入力の観測欄")
        .1;
    let normal_world = serde_json::Deserializer::from_str(world_text)
        .into_iter::<Value>()
        .next()
        .unwrap()
        .unwrap();
    assert_eq!(normal_world["observations"]["hearing"], *hearing(&result));
    assert_eq!(
        environment_material(&result)["world_context"]["observations"]["hearing"],
        *hearing(&result)
    );
    result
}

async fn capture(raw: &GameEvent) -> Requests {
    let d = server();
    // 通常の観測受付は通し、モデル／配送workerは直列枠の外で止める。
    let permit = d.serial.acquire().await.unwrap();
    d.observe("s", raw.clone(), None);
    let result = requests(&d, raw);
    drop(permit);
    d.shutdown().await;
    result
}

fn hearing(result: &Requests) -> &Value {
    &result.common["observations"]["hearing"]
}

fn sounds(result: &Requests) -> &[Value] {
    hearing(result)["sounds"]
        .as_array()
        .expect("共通観測のhearing.soundsに実際に届いた音を載せる")
}

fn heard_time(hearing: &Value, sound: &Value) -> DateTime<Utc> {
    let observed = DateTime::parse_from_rfc3339(
        hearing["observed_at"]
            .as_str()
            .expect("聴覚の観測時刻を明示する"),
    )
    .unwrap()
    .with_timezone(&Utc);
    observed
        - Duration::milliseconds(
            sound["heard_ago_ms"]
                .as_i64()
                .expect("元の聴取からの経過msを残す"),
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

fn environment_material(result: &Requests) -> Value {
    serde_json::from_str(&user_material(&result.environment)).unwrap()
}

// 見込み: 受信と身体側の経過時間を残せば、古い音を今鳴った音にしない。合格: 石の足音・highを保持し、観測時刻−身体150ms−音1200msから復元した聴取時刻が10ms以内で一致。確認範囲: 模擬観測から共通入力まで。
#[tokio::test]
async fn common_sound_retains_event_and_original_heard_time() {
    let raw = event(|_| {});
    let expected = raw.observed_at.with_timezone(&Utc) - Duration::milliseconds(150 + 1200);
    let result = capture(&raw).await;
    let hearing = hearing(&result);
    assert_eq!(hearing["status"], "observed");
    assert_eq!(hearing["truncated"], false);
    let sound = &sounds(&result)[0];
    assert_eq!(sound["sound_event"], STONE_STEP);
    assert_eq!(sound["certainty"], "high");
    assert!(
        sound["description"]
            .as_str()
            .is_some_and(|s| s.contains("足音")),
        "何の音かを示す"
    );
    assert!(
        (heard_time(hearing, sound) - expected)
            .num_milliseconds()
            .abs()
            <= 10,
        "聴取時刻は元event−身体age−元音age。時計のms丸めだけを許す"
    );
}

// 見込み: 共通聴覚へ集約すれば、同じ足音を別々の根拠として重ねない。合格: 通常生成のuser入力に石の足音IDが1回だけあり、共通観測の音も1件。確認範囲: 実入力の構成、返答品質は未検査。
#[tokio::test]
async fn ordinary_request_receives_sound_fact_exactly_once() {
    let result = capture(&event(|_| {})).await;
    assert_eq!(
        user_material(&result.normal).matches(STONE_STEP).count(),
        1,
        "通常生成へ共通の音事実を一度だけ渡す"
    );
    assert_eq!(sounds(&result).len(), 1);
}

// 見込み: 通常・環境で同じ聴覚を使えば、発話契機が違っても音の事実を共有できる。合格: 環境入力の足音IDが1回、hearing全体が通常の共通観測と一致。確認範囲: 入力生成まで、自発発話の選択は別確認。
#[tokio::test]
async fn environment_request_receives_same_sound_fact_exactly_once() {
    let result = capture(&event(|_| {})).await;
    let text = user_material(&result.environment);
    assert_eq!(
        text.matches(STONE_STEP).count(),
        1,
        "環境生成にも実音の事実が必要"
    );
    assert_eq!(
        environment_material(&result)["world_context"]["observations"]["hearing"],
        *hearing(&result)
    );
}

// 見込み: 旧音メモを表示から外せば、現在観測の音を二重に説明しない。合格: 内部の音要約は存在し、最終user入力には旧要約0回・旧聴覚見出し0回・実音ID1回。確認範囲: 実際の生成要求の文字列と観測。
#[tokio::test]
async fn ordinary_request_does_not_repeat_the_same_hearing_memo() {
    let result = capture(&event(|_| {})).await;
    assert!(
        !result.prior_sound_summary.is_empty(),
        "実音から既存メモまで到達するfixture"
    );
    assert!(
        user_material(&result.normal)
            .matches(&result.prior_sound_summary)
            .count()
            == 0,
        "旧要約は最終入力から外す"
    );
    assert_eq!(sounds(&result).len(), 1, "音自体を削除して重複を消さない");
    assert!(!user_material(&result.normal).contains("聴覚の手掛かり"));
    assert_eq!(user_material(&result.normal).matches(STONE_STEP).count(), 1);
}

// 見込み: 未確定音も残せば、聞こえなかったことにせず不明さを伝えられる。合格: 音1件、sound_eventとemitterはnull、certaintyはlow、説明は未確定。確認範囲: 模擬観測から共通入力、モデルの説明は別確認。
#[tokio::test]
async fn unknown_sound_keeps_uncertainty_without_inventing_a_source() {
    let raw = event(|v| {
        let mut unknown = sound("unknown", None, None, 300);
        unknown["certainty"] = json!("low");
        v["body_observation"]["sample"]["senses"]["ambient_sounds"] = json!([unknown]);
    });
    let result = capture(&raw).await;
    let rows = sounds(&result);
    assert_eq!(rows.len(), 1);
    assert!(rows[0]["sound_event"].is_null());
    assert!(rows[0]["emitter"].is_null());
    assert_eq!(rows[0]["certainty"], "low");
    assert!(
        rows[0]["description"]
            .as_str()
            .is_some_and(|s| s.contains("未確定") || s.contains("不明"))
    );
}

// 見込み: source UUIDを照合すれば、playerという音種名だけで本人の音としない。合格: owner一致はプレイヤー、別UUIDは本人以外、双方confirmed。確認範囲: 同定結果の入力到達、UUID採取は模擬。
#[tokio::test]
async fn player_emitter_confirmation_requires_actual_owner_source_match() {
    let raw = event(|v| {
        v["body_observation"]["sample"]["senses"]["ambient_sounds"] = json!([
            sound(
                "player",
                Some("minecraft:entity.player.attack.strong"),
                Some(OWNER),
                400
            ),
            sound(
                "player",
                Some("minecraft:entity.player.hurt"),
                Some(SOURCE),
                500
            ),
        ])
    });
    let result = capture(&raw).await;
    let rows = sounds(&result);
    let own = rows
        .iter()
        .find(|s| s["sound_event"] == "minecraft:entity.player.attack.strong")
        .unwrap();
    let other = rows
        .iter()
        .find(|s| s["sound_event"] == "minecraft:entity.player.hurt")
        .unwrap();
    assert_eq!(own["emitter"], "プレイヤー");
    assert_eq!(own["emitter_attribution"], "confirmed");
    assert_eq!(other["emitter_attribution"], "confirmed");
    assert_eq!(other["emitter"], "本人以外", "別UUIDを本人由来にしない");
}

// 見込み: 同一記録だけをまとめれば、重複を減らし別の音は残せる。合格: 同じ足音2件と破壊音1件から2種が各1件残り、通常・環境入力にも各IDが1回。確認範囲: 実重複排除と要求生成。
#[tokio::test]
async fn duplicate_sound_is_collapsed_but_distinct_sound_event_survives() {
    let raw = event(|v| {
        let step = sound("block:stone", Some(STONE_STEP), Some(SOURCE), 1200);
        v["body_observation"]["sample"]["senses"]["ambient_sounds"] = json!([
            step,
            step,
            sound(
                "block:stone",
                Some("minecraft:block.stone.break"),
                Some(SOURCE),
                800
            )
        ]);
    });
    let result = capture(&raw).await;
    let rows = sounds(&result);
    assert_eq!(rows.len(), 2);
    for id in [STONE_STEP, "minecraft:block.stone.break"] {
        assert_eq!(rows.iter().filter(|s| s["sound_event"] == id).count(), 1);
        for request in [&result.normal, &result.environment] {
            assert_eq!(user_material(request).matches(id).count(), 1);
        }
    }
}

// 見込み: 音の確度と種名の呼称可否を残せば、候補を確定した敵と取り違えにくい。合格: 敵の音1件にlow・spoken_name_allowed=false・emitter=nullが残る。確認範囲: 模擬音から共通観測、実発話は別確認。
#[tokio::test]
async fn hostile_sound_preserves_adapter_uncertainty() {
    let raw = event(|v| {
        let senses = &mut v["body_observation"]["sample"]["senses"];
        senses["ambient_sounds"] = json!([]);
        senses["auditory_threats"] = json!([{"label":"zombie","sound_event":"minecraft:entity.zombie.ambient",
            "direction":{"horizontal":"left"},"distance_band":"far","certainty":"low",
            "spoken_name_allowed":false,"heard_ago_ms":650}]);
    });
    let result = capture(&raw).await;
    let rows = sounds(&result);
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["certainty"], "low");
    assert_eq!(rows[0]["spoken_name_allowed"], false);
    assert!(rows[0]["emitter"].is_null());
}

// 見込み: 採取完了・打切り・未取得を分ければ、情報不足を無音確認としない。合格: 空の3場面でstatusとtruncatedが対応し、音を補わない。確認範囲: 共通観測の状態表示、自発反応は別確認。
#[tokio::test]
async fn empty_truncated_and_unavailable_hearing_are_distinct() {
    for (status, truncated) in [
        ("observed", false),
        ("observed", true),
        ("unavailable", false),
    ] {
        let raw = event(|v| {
            let senses = &mut v["body_observation"]["sample"]["senses"];
            senses["ambient_sounds"] = json!([]);
            senses["hearing_truncated"] = json!(truncated);
            if status == "unavailable" {
                *senses = json!({"version":1,"status":"unavailable","basis":"body_pose_client_world",
                    "source_server_tick":100,"reason":"not_sampled"});
            }
        });
        let result = capture(&raw).await;
        assert_eq!(hearing(&result)["status"], status);
        if status == "observed" {
            assert_eq!(hearing(&result)["truncated"], truncated);
        }
        assert!(
            hearing(&result)["sounds"]
                .as_array()
                .is_none_or(Vec::is_empty)
        );
    }
}

// 見込み: 辞書と実観測を分ければ、見えたウシの鳴き声を聞いたとは扱わない。合格: 「もー」は両要求の辞書だけにあり、観測音・実音ID・内部音要約は空。確認範囲: 入力の配置、モデルの読み取りは別確認。
#[tokio::test]
async fn control_catalog_sound_tags_remain_general_without_heard_evidence() {
    let raw = event(|v| {
        let senses = &mut v["body_observation"]["sample"]["senses"];
        senses["ambient_sounds"] = json!([]);
        senses["passive_mobs"] = json!([{"type":"cow","distance":5,"certainty":"high"}]);
    });
    let result = capture(&raw).await;
    let knowledge = &result.common["catalog_knowledge"];
    assert!(
        knowledge.to_string().contains("もー"),
        "辞書の音表現を本当に含むfixture"
    );
    assert!(!result.common["observations"].to_string().contains("もー"));
    assert!(
        hearing(&result)["sounds"]
            .as_array()
            .is_none_or(Vec::is_empty)
    );
    assert!(result.prior_sound_summary.is_empty());
    let environment = environment_material(&result);
    assert!(
        environment["catalog_knowledge"]
            .to_string()
            .contains("もー")
    );
    assert!(environment["world_context"]["catalog_knowledge"].is_null());
    for request in [&result.normal, &result.environment] {
        let text = user_material(request);
        assert!(text.contains("もー"), "表現材料は辞書として利用できる");
        assert!(
            !text.contains("minecraft:entity.cow.ambient"),
            "視認・辞書だけで聴取記録を作らない"
        );
    }
}

// 見込み: 身体の聴覚を正とすれば、本人だけが聞いた音をドギドへ移さない。合格: 本人側だけのウシの実音IDは共通観測・通常要求・環境要求に入らず、内部音要約も空。確認範囲: 模擬二視点の受信と投影。
#[tokio::test]
async fn control_player_side_sound_is_not_substituted_for_dogido_hearing() {
    let raw = event(|v| {
        v["body_observation"]["sample"]["senses"]["ambient_sounds"] = json!([]);
        v["ambient_sounds"] = json!([sound(
            "cow",
            Some("minecraft:entity.cow.ambient"),
            Some(SOURCE),
            200
        )]);
    });
    let result = capture(&raw).await;
    assert!(result.prior_sound_summary.is_empty());
    assert!(
        !result.common["observations"]
            .to_string()
            .contains("minecraft:entity.cow.ambient")
    );
    for request in [&result.normal, &result.environment] {
        assert!(!user_material(request).contains("minecraft:entity.cow.ambient"));
    }
}

// source_contextは発音時の調査結果を渡す現行契約。期待する発音者や確定／推定の答えは入力せず、本体の受信・投影結果で照合する。Fabricが実世界からこの根拠を採取できるかは別の試験。
fn attribution_event(
    sound_event: &str,
    activity: &str,
    change: impl FnOnce(&mut Value),
) -> GameEvent {
    event(|raw| {
        let heard_at = DateTime::parse_from_rfc3339(raw["observed_at"].as_str().unwrap())
            .unwrap()
            .with_timezone(&Utc)
            - Duration::milliseconds(150 + 1200);
        let position = json!({"x":0.0,"y":64.0,"z":0.0});
        raw["player"]["position"] = position.clone();
        raw["body_observation"]["sample"]["player_pose"]["position"] = position.clone();
        raw["body_observation"]["sample"]["self_pose"]["position"] =
            json!({"x":0.0,"y":64.0,"z":4.0});
        raw["body_observation"]["sample"]["self_pose"]["eye_position"] =
            json!({"x":0.0,"y":65.0,"z":4.0});
        let mut received = sound("block:stone", Some(sound_event), None, 1200);
        received["source_context"] = json!({
            "heard_at":heard_at,
            "source_position":position,
            "player":{"id":OWNER,"position":position,"activity":activity,"observed_at":heard_at},
            "survey":{"status":"complete","scope":"hearing_range","observed_at":heard_at,
                "receiver_position":{"x":0.0,"y":64.0,"z":4.0},"radius_blocks":16.0,"other_emitters":[]}
        });
        change(&mut received);
        let senses = &mut raw["body_observation"]["sample"]["senses"];
        senses["passive_mobs"] = json!([]);
        senses["visual_threats"] = json!([]);
        senses["auditory_threats"] = json!([]);
        senses["ambient_sounds"] = json!([received]);
    })
}

async fn assert_attribution(raw: &GameEvent, expected: &str, expected_sound: &str) {
    let result = capture(raw).await;
    let rows = sounds(&result);
    assert_eq!(rows.len(), 1, "音源不明でも聞いた音は捨てない");
    let row = &rows[0];
    assert_eq!(
        row["sound_event"], expected_sound,
        "足音と破壊音を取り違えない"
    );
    assert_eq!(
        row["certainty"], "high",
        "音を聞いた確度と、発音者の確定／推定は別に扱う"
    );
    assert_eq!(
        row["emitter_attribution"], expected,
        "2026-10-29追加方針: 確定・推定・未確定を分ける"
    );
    if expected == "unknown" {
        assert!(row["emitter"].is_null(), "根拠不足を本人由来へ変えない");
    } else {
        assert_eq!(row["emitter"], "プレイヤー");
    }
    assert_eq!(
        environment_material(&result)["world_context"]["observations"]["hearing"],
        *hearing(&result)
    );
    for request in [&result.normal, &result.environment] {
        let text = user_material(request);
        assert_eq!(text.matches(expected_sound).count(), 1);
        assert!(
            text.contains("\"emitter_attribution\"") && text.contains(&format!("\"{expected}\"")),
            "通常／環境のモデル入力まで確定と推定の区別を残す"
        );
    }
}

// 見込み: 同時点の歩行・位置・範囲調査が揃えば、本人由来という推測を渡せる。合格: source欠落とpos追跡キーの両方で足音・highを残し、両要求にプレイヤー/inferredが1件。確認範囲: 根拠は模擬、Rustの推定と投影。
#[tokio::test]
async fn player_footsteps_are_inferred_from_complete_concurrent_context() {
    for source in [None, Some("pos:block.stone.step:0:16:0")] {
        let raw = attribution_event(STONE_STEP, "walking", |sound| {
            sound["source_id"] = json!(source);
        });
        let wire = serde_json::to_value(&raw).unwrap();
        let received = &wire["body_observation"]["sample"]["senses"]["ambient_sounds"][0];
        assert_eq!(
            received["source_id"],
            json!(source),
            "位置由来の追跡IDを本人IDに作り替えない"
        );
        assert_eq!(
            received["source_context"]["survey"]["status"], "complete",
            "推定根拠が実受信を通る"
        );
        assert_attribution(&raw, "inferred", STONE_STEP).await;
    }
}

// 見込み: 同時点の破壊動作と候補不在を渡せば、破壊音の由来を推定できる。合格: 通常・環境入力に石の破壊音を1件残し、プレイヤー/inferred・highを示す。確認範囲: 根拠は模擬、実音採取は別確認。
#[tokio::test]
async fn player_breaking_sound_is_inferred_without_changing_sound_kind() {
    let event_id = "minecraft:block.stone.break";
    let raw = attribution_event(event_id, "breaking", |_| {});
    assert_attribution(&raw, "inferred", event_id).await;
}

// 見込み: 視認一覧の空と範囲調査の完了を分ければ、情報不足から本人由来と決めない。合格: 視認限定・打切り・未取得・根拠欠落の各条件で音は残し、emitter=null/unknown。確認範囲: 実推定と両要求。
#[tokio::test]
async fn empty_visible_mob_list_is_not_complete_sound_source_evidence() {
    for missing in ["visible_only", "truncated", "unavailable", "missing"] {
        let raw = attribution_event(STONE_STEP, "walking", |sound| match missing {
            "visible_only" => sound["source_context"]["survey"]["scope"] = json!("visible_only"),
            "missing" => {
                sound.as_object_mut().unwrap().remove("source_context");
            }
            status => sound["source_context"]["survey"]["status"] = json!(status),
        });
        assert_attribution(&raw, "unknown", STONE_STEP).await;
    }
}

// 見込み: 見えない候補も調査根拠へ含めれば、本人の歩行だけで音源を決めない。合格: 隠れたモブと別プレイヤーの各条件で音を残し、emitter=null/unknownが両要求へ届く。確認範囲: 候補は模擬、実推定と投影。
#[tokio::test]
async fn hidden_mob_or_other_player_prevents_exclusion_based_attribution() {
    for kind in ["mob", "player"] {
        let raw = attribution_event(STONE_STEP, "walking", |sound| {
            sound["source_context"]["survey"]["other_emitters"] = json!([
                {"kind":kind,"id":SOURCE,"position":{"x":1.0,"y":64.0,"z":0.0}}
            ]);
        });
        assert_attribution(&raw, "unknown", STONE_STEP).await;
    }
}

// 見込み: 別時点・遠い位置・異なる動作を根拠にしなければ、古い音へ今の状況を付け替えない。合格: 調査時刻・本人時刻・距離・停止・動作違いの各条件で、足音も破壊音もunknownのまま残る。確認範囲: 模擬根拠の実推定。
#[tokio::test]
async fn mismatched_time_position_or_player_action_keeps_source_unknown() {
    for mismatch in [
        "survey_time",
        "player_time",
        "source_position",
        "idle",
        "wrong_action",
    ] {
        for (event_id, activity) in [
            (STONE_STEP, "walking"),
            ("minecraft:block.stone.break", "breaking"),
        ] {
            let raw = attribution_event(event_id, activity, |sound| {
                let context = &mut sound["source_context"];
                let old_time = DateTime::parse_from_rfc3339(context["heard_at"].as_str().unwrap())
                    .unwrap()
                    - Duration::seconds(30);
                match mismatch {
                    "survey_time" => context["survey"]["observed_at"] = json!(old_time),
                    "player_time" => context["player"]["observed_at"] = json!(old_time),
                    "source_position" => context["source_position"]["x"] = json!(10.0),
                    "idle" => context["player"]["activity"] = json!("idle"),
                    "wrong_action" => {
                        context["player"]["activity"] = json!(if activity == "walking" {
                            "breaking"
                        } else {
                            "walking"
                        })
                    }
                    _ => unreachable!(),
                }
            });
            assert_attribution(&raw, "unknown", event_id).await;
        }
    }
}

// 見込み: UUID同定を状況推定より優先すれば、本人付近の別個体の音を本人由来としない。合格: 同じ推定根拠でもowner UUIDはプレイヤー、別UUIDは本人以外として両方confirmed。確認範囲: 実同定と通常・環境への投影。
#[tokio::test]
async fn direct_source_identity_takes_precedence_over_circumstantial_evidence() {
    for (source_id, expected) in [(OWNER, "プレイヤー"), (SOURCE, "本人以外")] {
        let raw = attribution_event(STONE_STEP, "walking", |sound| {
            sound["source_id"] = json!(source_id);
        });
        let result = capture(&raw).await;
        let row = &sounds(&result)[0];
        assert_eq!(row["emitter_attribution"], "confirmed");
        assert_eq!(row["emitter"], expected);
        assert_eq!(
            environment_material(&result)["world_context"]["observations"]["hearing"],
            *hearing(&result)
        );
        for request in [&result.normal, &result.environment] {
            assert_eq!(user_material(request).matches(STONE_STEP).count(), 1);
            assert!(user_material(request).contains(expected));
        }
    }
}

fn later_without_sounds(first: &GameEvent, unavailable: bool) -> GameEvent {
    let mut value = serde_json::to_value(first).unwrap();
    value["observed_at"] = json!(first.observed_at + Duration::milliseconds(200));
    value["sequence"] = json!(2);
    let sample = &mut value["body_observation"]["sample"];
    sample["server_tick"] = json!(101);
    sample["snapshot_revision"] = json!(201);
    sample["senses"]["source_server_tick"] = json!(101);
    sample["senses"]["ambient_sounds"] = json!([]);
    sample["senses"]["auditory_threats"] = json!([]);
    if unavailable {
        sample["senses"] = json!({"version":1,"status":"unavailable","basis":"body_pose_client_world",
            "source_server_tick":101,"reason":"not_sampled"});
    }
    GameEvent::parse(value).unwrap()
}

// 見込み: 音が現在一覧から消えても、保持した元の事実を使えば由来と時刻を若返らせない。合格: 確定・推定・打切り未確定の3条件で、音1件・元時刻・low・由来・調査状態が両要求に残る。確認範囲: 模擬観測2回と実保持経路。
#[tokio::test]
async fn retained_sounds_preserve_time_certainty_and_attribution_in_both_requests() {
    for (name, source, survey, attribution) in [
        ("confirmed", Some(OWNER), "complete", "confirmed"),
        ("inferred", None, "complete", "inferred"),
        ("truncated", None, "truncated", "unknown"),
    ] {
        let first = attribution_event(STONE_STEP, "walking", |sound| {
            sound["source_id"] = json!(source);
            sound["certainty"] = json!("low");
            sound["source_context"]["survey"]["status"] = json!(survey);
        });
        let d = server();
        let permit = d.serial.acquire().await.unwrap();
        d.observe("s", first.clone(), None);
        let before = requests(&d, &first);
        let original = sounds(&before)[0].clone();
        let next = later_without_sounds(&first, false);
        d.observe("s", next.clone(), None);
        let result = requests(&d, &next);
        assert_eq!(sounds(&result).len(), 1, "{name}: 保持音を失わない");
        let row = &sounds(&result)[0];
        assert_eq!(row["basis"], "retained_sound");
        assert_eq!(row["certainty"], "low");
        assert_eq!(row["emitter_attribution"], attribution);
        assert_eq!(row["source_survey_status"], survey);
        for key in [
            "sound_event",
            "emitter",
            "description",
            "direction",
            "distance_band",
        ] {
            assert_eq!(row[key], original[key], "{name}: {key}");
        }
        assert!(
            (heard_time(hearing(&result), row) - heard_time(hearing(&before), &original))
                .num_milliseconds()
                .abs()
                <= 10
        );
        assert_eq!(
            environment_material(&result)["world_context"]["observations"]["hearing"],
            *hearing(&result)
        );
        for request in [&result.normal, &result.environment] {
            assert_eq!(user_material(request).matches(STONE_STEP).count(), 1);
        }
        drop(permit);
        d.shutdown().await;
    }
}

// 見込み: 身体感覚の未取得を保持音で埋めなければ、今聞こえた事実と誤解させない。合格: 音を受けた次の未取得観測ではhearing=unavailable、soundsは空、両要求に旧実音IDなし。確認範囲: 実保持後の共通投影、実モデルは未検査。
#[tokio::test]
async fn unavailable_body_does_not_replay_retained_hearing() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let first = event(|_| {});
    d.observe("s", first.clone(), None);
    assert_eq!(sounds(&requests(&d, &first)).len(), 1);
    let next = later_without_sounds(&first, true);
    d.observe("s", next.clone(), None);
    let result = requests(&d, &next);
    assert_eq!(hearing(&result)["status"], "unavailable");
    assert!(sounds(&result).is_empty());
    for request in [&result.normal, &result.environment] {
        assert!(!user_material(request).contains(STONE_STEP));
    }
    drop(permit);
    d.shutdown().await;
}

// 見込み: 調査打切りを残せば、自発話題と質問への推測回答を分ける材料になる。合格: 音はunknown・low・truncatedで両要求に1回、質問は通常だけに入る。確認範囲: 実入力を採取。沈黙と推測回答の選択はpart45_model_casesで別評価。
#[tokio::test]
async fn truncated_sound_keeps_unknown_source_and_exports_both_model_inputs() {
    let raw = attribution_event(STONE_STEP, "walking", |sound| {
        sound["certainty"] = json!("low");
        sound["source_context"]["survey"]["status"] = json!("truncated");
    });
    let result = capture(&raw).await;
    let row = &sounds(&result)[0];
    assert_eq!(row["emitter_attribution"], "unknown");
    assert!(row["emitter"].is_null());
    assert_eq!(row["certainty"], "low");
    assert_eq!(row["source_survey_status"], "truncated");
    for request in [&result.normal, &result.environment] {
        assert_eq!(user_material(request).matches(STONE_STEP).count(), 1);
    }
    assert!(user_material(&result.normal).contains("今はどんな音が聞こえる？"));
    assert!(!user_material(&result.environment).contains("今はどんな音が聞こえる？"));
    if let Some(dir) = std::env::var_os("DOGIDO_ACCEPTANCE_PAYLOAD_DIR") {
        let dir = std::path::PathBuf::from(dir);
        std::fs::create_dir_all(&dir).unwrap();
        for (route, request) in [
            ("question", &result.normal),
            ("spontaneous", &result.environment),
        ] {
            std::fs::write(
                dir.join(format!("part4-truncated-{route}.json")),
                serde_json::to_vec_pretty(request).unwrap(),
            )
            .unwrap();
        }
    }
}

// 見込み: 敵の保持音にも確度と呼称可否を残せば、時間が経っただけで種名を確定しない。合格: 保持後もlow・spoken_name_allowed=false・unknown・元時刻が両要求に残る。確認範囲: 模擬の二回観測から実保持・投影まで。
#[tokio::test]
async fn retained_hostile_sound_keeps_name_permission_and_original_time() {
    let first = event(|v| {
        let senses = &mut v["body_observation"]["sample"]["senses"];
        senses["ambient_sounds"] = json!([]);
        senses["auditory_threats"] = json!([{"label":"zombie","sound_event":"minecraft:entity.zombie.ambient",
            "direction":{"horizontal":"left"},"distance_band":"far","certainty":"low",
            "spoken_name_allowed":false,"heard_ago_ms":650}]);
    });
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    d.observe("s", first.clone(), None);
    let before = requests(&d, &first);
    let next = later_without_sounds(&first, false);
    d.observe("s", next.clone(), None);
    let result = requests(&d, &next);
    assert_eq!(sounds(&result).len(), 1);
    let row = &sounds(&result)[0];
    assert_eq!(row["basis"], "retained_sound");
    assert_eq!(row["certainty"], "low");
    assert_eq!(row["spoken_name_allowed"], false);
    assert_eq!(row["emitter_attribution"], "unknown");
    assert!(row["emitter"].is_null());
    assert!(
        (heard_time(hearing(&result), row) - heard_time(hearing(&before), &sounds(&before)[0]))
            .num_milliseconds()
            .abs()
            <= 10
    );
    assert_eq!(
        environment_material(&result)["world_context"]["observations"]["hearing"],
        *hearing(&result)
    );
    for request in [&result.normal, &result.environment] {
        assert_eq!(
            user_material(request)
                .matches("minecraft:entity.zombie.ambient")
                .count(),
            1
        );
    }
    drop(permit);
    d.shutdown().await;
}
