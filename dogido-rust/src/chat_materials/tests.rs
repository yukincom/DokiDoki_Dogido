use super::*;
use std::collections::HashMap;
#[derive(Deserialize)]
struct FixtureLabels {
    mobs: HashMap<String, String>,
    mob_fallback: HashMap<String, String>,
    hostiles: HashMap<String, String>,
    blocks: HashMap<String, String>,
}
impl Labels for FixtureLabels {
    fn mob_label(&self, id: &str) -> Option<&str> {
        let id = chat_catalog::strip(id).to_lowercase();
        self.mobs
            .get(id.strip_prefix("minecraft:").unwrap_or(&id))
            .map(String::as_str)
    }
    fn mob_fallback_label(&self, id: &str) -> Option<&str> {
        self.mob_fallback.get(id).map(String::as_str)
    }
    fn hostile_label(&self, id: &str) -> String {
        self.hostiles.get(id).cloned().unwrap_or_else(|| id.into())
    }
    fn block_label(&self, id: &str) -> Option<&str> {
        self.blocks
            .get(&chat_catalog::strip(id.split(':').next_back().unwrap()).to_lowercase())
            .map(String::as_str)
    }
}
fn labels() -> FixtureLabels {
    serde_json::from_str(include_str!("../../fixtures/chat-observation-labels.json")).unwrap()
}
#[derive(Deserialize)]
struct Row {
    event: GameEvent,
    settings: Settings,
    input: PlayerInput,
    context: Context,
    snapshot: Snapshot,
}
#[test]
fn all_existing_observation_frames_prepare_a_single_request_without_mutation() {
    let labels = labels();
    let mut count = 0;
    for line in include_str!("../../fixtures/chat-materials.jsonl").lines() {
        let row: Row = serde_json::from_str(line).unwrap();
        let before = serde_json::to_value(&row.snapshot).unwrap();
        match prepare(
            &row.event,
            &row.settings,
            &row.input,
            &row.context,
            &row.snapshot,
            &labels,
        )
        .unwrap()
        {
            Prepared::Reply(reply) => {
                let request = reply.request("fixture", 72).unwrap();
                assert_eq!(request.kind, "player_chat");
                assert_eq!(request.messages.len(), 2);
                assert!(
                    request.messages[1]
                        .content
                        .contains(reply.details["user_text"].as_str().unwrap())
                );
                assert!(reply.details.get("player_chat_plan_action").is_none());
                count += 1;
            }
            Prepared::Knowledge => assert!(row.input.knowledge_query_present),
            Prepared::Fixed(f) => assert_eq!(f.reason, "current_smell"),
        }
        assert_eq!(serde_json::to_value(&row.snapshot).unwrap(), before);
    }
    assert!(count > 300, "{count}");
}
#[test]
fn frozen_observation_history_and_unobserved_question_reach_one_reply() {
    let mut row: Row = serde_json::from_str(
        include_str!("../../fixtures/chat-materials.jsonl")
            .lines()
            .next()
            .unwrap(),
    )
    .unwrap();
    row.input = PlayerInput {
        raw_text: "猫はいる？".into(),
        semantic_text: "猫はいる？".into(),
        ..Default::default()
    };
    row.context.history.conversation_history = "プレイヤー: 前の話やで".into();
    let Prepared::Reply(reply) = prepare(
        &row.event,
        &row.settings,
        &row.input,
        &row.context,
        &row.snapshot,
        &labels(),
    )
    .unwrap() else {
        panic!("質問を固定回答へ置換しない")
    };
    let first = reply.request("mock", 72).unwrap();
    row.context.history.conversation_history = "あとで来た話".into();
    row.snapshot.current.passive_types.push("warden".into());
    let again = reply.request("mock", 72).unwrap();
    assert_eq!(
        serde_json::to_value(&first).unwrap(),
        serde_json::to_value(&again).unwrap()
    );
    assert!(first.messages[1].content.contains("前の話やで"));
    assert!(!first.messages[1].content.contains("あとで来た話"));
}
#[test]
fn named_observations_reach_the_request_without_promoting_player_report_to_fact() {
    let event = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"passive_mobs":[{"type":"cat","identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true}}]}),
    );
    let input = PlayerInput {
        raw_text: "クロちゃんが助けてくれた".into(),
        semantic_text: "クロちゃんが助けてくれた".into(),
        ..Default::default()
    };
    let Prepared::Reply(reply) = prepare(
        &event,
        &Settings::default(),
        &input,
        &Context::default(),
        &individual_snapshot(&event),
        &labels(),
    )
    .unwrap() else {
        panic!()
    };
    let facts = reply.details["named_mob_context"].as_str().unwrap();
    assert!(facts.contains("クロちゃん") && facts.contains("ネコ"));
    assert!(!facts.contains("助けて"));
    let request = reply.request("mock", 72).unwrap();
    assert!(request.messages[1].content.contains("individual:cat-1"));
    assert!(
        request.messages[1]
            .content
            .contains("現在観測されている対象")
    );
}

fn individual_event(at: &str, fields: Value) -> GameEvent {
    let mut value = json!({"schema_version":"2026-05-24","adapter":"test","observed_at":at,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld"},"world":{"biome":"plains","sky_visible":true,"time_phase":"day"}});
    value
        .as_object_mut()
        .unwrap()
        .extend(fields.as_object().unwrap().clone());
    GameEvent::parse(value).unwrap()
}

fn heard_pet() -> Value {
    json!({"type":"cat","source_id":"cat-1","sound_event":"entity.cat.ambient","direction":{"horizontal":"right"},"heard_ago_ms":0,
        "identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true}})
}

fn individual_snapshot(event: &GameEvent) -> Snapshot {
    let mut memory = crate::chat_observation::ChatObservationMemory::default();
    memory
        .observe(event, &Default::default(), &labels());
    memory.snapshot(event, &labels())
}

#[test]
fn named_sound_uses_latest_name_normalized_longest_match_and_actual_sound_kind() {
    let original = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"ambient_sounds":[heard_pet()]}),
    );
    let mut memory = crate::chat_observation::ChatObservationMemory::default();
    memory
        .observe(&original, &Default::default(), &labels());
    let renamed = individual_event(
        "2026-09-30T01:00:01Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"cat-1","custom_name":"シロ","tamed":true}}]}),
    );
    memory
        .observe(&renamed, &Default::default(), &labels());
    let snap = memory.snapshot(&renamed, &labels());
    let answer = individuals::hearing(&snap, "シロの声？");
    assert!(answer.summary.contains("シロの声") && !answer.summary.contains("クロちゃん"));
    let removed = individual_event(
        "2026-09-30T01:00:02Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"cat-1","tamed":true}}]}),
    );
    memory
        .observe(&removed, &Default::default(), &labels());
    let snap = memory.snapshot(&removed, &labels());
    assert!(
        !individuals::hearing(&snap, "何の音？")
            .summary
            .contains("クロちゃん")
    );

    let mixed = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"ambient_sounds":[heard_pet(),
        {"type":"wolf","source_id":"wolf-1","sound_event":"entity.wolf.step",
        "identity":{"entity_id":"wolf-1","custom_name":"くろ","tamed":true}}]}),
    );
    let snap = individual_snapshot(&mixed);
    let answer = individuals::hearing(&snap, "クロちゃんの声？");
    assert!(answer.summary.contains("クロちゃんの声") && !answer.summary.contains("くろの"));
    let context = individuals::context(&snap, "クロちゃんが助けてくれた", "", false);
    assert!(context.contains("クロちゃん") && !context.contains("オオカミ"));
    let answer = individuals::hearing(&snap, "くろの音？");
    assert!(answer.summary.contains("くろの足音") && !answer.summary.contains("くろの声"));
    let duplicate = individual_event(
        "2026-09-30T01:00:00Z",
        json!({"ambient_sounds":[heard_pet(),
        {"type":"wolf","source_id":"wolf-1","sound_event":"entity.wolf.ambient",
        "identity":{"entity_id":"wolf-1","custom_name":"くろちゃん","tamed":true}}]}),
    );
    assert!(individuals::hearing(&individual_snapshot(&duplicate), "クロちゃんの声？").ambiguous);
}

#[test]
fn shared_world_context_is_used_once_without_legacy_place_or_weather_materials() {
    let event = individual_event(
        "2026-10-06T00:00:00Z",
        json!({
        "player":{"dimension":"minecraft:overworld","position":{"y":123}},
        "world":{"biome":"meadow","sky_visible":true,"weather":"clear","local_light":8,"ceiling_height":24}}),
    );
    let world = crate::conversation_observation::project(&event, None, 10.0, 3.0, 5000).unwrap();
    let context = Context {
        world_context: Some(world.clone()),
        ..Context::default()
    };
    let input = PlayerInput {
        raw_text: "いい天気だね".into(),
        semantic_text: "いい天気だね".into(),
        ..PlayerInput::default()
    };
    let Prepared::Reply(prepared) = prepare(
        &event,
        &Settings::default(),
        &input,
        &context,
        &individual_snapshot(&event),
        &labels(),
    )
    .unwrap() else {
        panic!()
    };
    assert_eq!(prepared.details["world_context"], json!(world));
    for key in [
        "place_context",
        "biome",
        "space_kind",
        "sky_visible",
        "time_phase",
        "weather",
        "weather_label",
        "weather_context",
        "precipitation_kind",
    ] {
        assert!(
            prepared.details.get(key).is_none(),
            "duplicate material: {key}"
        );
    }
    let messages = prepared.request("mock", 512).unwrap().messages;
    let text = messages
        .iter()
        .map(|m| m.content.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(text.matches("開けた地上").count(), 1);
    assert!(!text.contains("天井おおよそ") && !text.contains("高さY"));
}

#[test]
fn retained_companion_name_never_restores_current_presence() {
    let named = individual_event(
        "2026-10-06T02:00:00Z",
        json!({"companion_candidates":[
        {"type":"cat","identity":{"entity_id":"cat-1","custom_name":"クロちゃん","tamed":true}}]}),
    );
    let gone = individual_event("2026-10-06T02:00:01Z", json!({}));
    let mut memory = crate::chat_observation::ChatObservationMemory::default();
    memory
        .observe(&named, &Default::default(), &labels());
    memory
        .observe(&gone, &Default::default(), &labels());
    let snapshot = memory.snapshot(&gone, &labels());
    assert!(!snapshot.named_mobs[0].current);
    assert!(individuals::focus(&gone, &snapshot, &Context::default(), "クロちゃんは？").is_none());
    assert!(individuals::current_observations(&snapshot, "クロちゃんは？").is_empty());
}

#[test]
fn current_companion_name_survives_the_retained_name_table_limit() {
    let regular = (0..4)
        .map(|i| json!({"type":"cow", "identity":{"entity_id":format!("cow-{i}")}}))
        .collect::<Vec<_>>();
    let companions = (0..32)
        .map(|i| {
            json!({"type":"cat", "identity":{
        "entity_id":format!("cat-{i}"), "custom_name":format!("個体番号{i}号"), "tamed":true}})
        })
        .collect::<Vec<_>>();
    let event = individual_event(
        "2026-10-06T02:00:00Z",
        json!({
        "passive_mobs":regular, "companion_candidates":companions}),
    );
    let snapshot = individual_snapshot(&event);
    assert!(
        !snapshot
            .named_mobs
            .iter()
            .any(|row| row.entity_id == "cat-31")
    );
    let focus =
        individuals::focus(&event, &snapshot, &Context::default(), "個体番号31号は？").unwrap();
    assert_eq!(
        focus.event.passive_mobs[0]
            .identity
            .as_ref()
            .unwrap()
            .entity_id,
        "cat-31"
    );
    assert!(
        individuals::current_observations(&focus.snapshot, "個体番号31号は？")
            .iter()
            .any(|row| row.entity_id == "individual:cat-31")
    );
    assert!(individuals::focus(&event, &snapshot, &Context::default(), "いい天気だね").is_none());
    assert!(
        !snapshot
            .named_mobs
            .iter()
            .any(|row| row.entity_id == "cat-31")
    );
}

// 見込み: 聴覚を共通観測へ集約しても、雑談と音の質問で3種の音を使える。合格: 足音・破壊音・設置音が各1回、由来unknown、旧音メモ欄なし。確認範囲: 模擬eventから実要求。実際の発音者採取と返答は別。
#[test]
fn block_sound_actions_reach_both_ordinary_chat_and_sound_question_requests() {
    let event = individual_event(
        "2026-10-18T01:00:00Z",
        json!({"ambient_sounds":[
            {"type":"block:stone","source_id":"same-place","sound_event":"block.stone.step"},
            {"type":"block:stone","source_id":"same-place","sound_event":"block.stone.break"},
            {"type":"block:stone","source_id":"same-place","sound_event":"block.stone.place"}
        ]}),
    );
    let snapshot = individual_snapshot(&event);
    let context = Context {
        world_context: Some(crate::conversation_observation::project(&event, None, 10.0, 3.0, 5000).unwrap()),
        ..Default::default()
    };
    for asks_about_sound in [false, true] {
        let text = if asks_about_sound {
            "今の音は何？"
        } else {
            "少し休もう"
        };
        let input = PlayerInput {
            raw_text: text.into(),
            semantic_text: text.into(),
            asks_about_sound,
            ..Default::default()
        };
        let Prepared::Reply(reply) = prepare(
            &event,
            &Settings::default(),
            &input,
            &context,
            &snapshot,
            &labels(),
        )
        .unwrap() else {
            panic!("expected ordinary reply");
        };
        let request = reply.request("test", 72).unwrap();
        assert_eq!(request.kind, "player_chat");
        let facts = &request.messages[1].content;
        for text in [
            "石系の足音",
            "石系ブロックの破壊音",
            "石系ブロックの設置音",
        ] {
            assert_eq!(facts.matches(text).count(), 1, "{text}");
        }
        assert_eq!(facts.matches("\"emitter_attribution\": \"unknown\"").count(), 3);
        assert!(reply.details.get("hearing_block").is_none());
        assert!(!facts.contains(&snapshot.hearing.summary));
    }
}
