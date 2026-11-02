//! モブ質問に渡す身体視点の現在視認と直近の視認。種類の決定は会話モデルに委ねる。
use crate::{
    chat_observation::Snapshot,
    events::{
        CardinalDirection, Direction, GameEvent, HorizontalDirection, MobEnvironment,
        VerticalRelation,
    },
};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

struct Mob<'a> {
    kind: &'a str,
    name: Option<&'a str>,
    distance: Option<f64>,
    direction: &'a Direction,
}

fn label(kind: &str) -> String {
    crate::chat_catalog::catalog()
        .mob_entry(kind)
        .and_then(|entry| entry["label"].as_str())
        .unwrap_or(kind)
        .to_owned()
}

// environment={} は遮蔽中の追跡保持。距離があっても現在の視認個体へ昇格させない。
pub(crate) fn currently_visible(environment: Option<&MobEnvironment>) -> bool {
    environment.is_some_and(|e| {
        e.touching_water.is_some()
            || e.submerged_in_water.is_some()
            || e.touching_water_or_rain.is_some()
            || e.on_ground.is_some()
            || e.on_fire.is_some()
    })
}

fn observed_mobs(event: &GameEvent) -> Vec<Mob<'_>> {
    let mut mobs = Vec::new();
    let mut ids = BTreeSet::new();
    for v in &event.visual_threats {
        if !currently_visible(v.environment.as_ref()) {
            continue;
        }
        let id = v
            .identity
            .as_ref()
            .map(|i| i.entity_id.as_str())
            .or(v.entity_id.as_deref());
        if id.is_some_and(|id| !ids.insert(id)) {
            continue;
        }
        mobs.push(Mob {
            kind: v.r#type.strip_prefix("minecraft:").unwrap_or(&v.r#type),
            name: v.identity.as_ref().and_then(|i| i.custom_name.as_deref()),
            distance: v.distance,
            direction: &v.direction,
        });
    }
    for v in &event.passive_mobs {
        let id = v.identity.as_ref().map(|i| i.entity_id.as_str());
        if id.is_some_and(|id| !ids.insert(id)) {
            continue;
        }
        mobs.push(Mob {
            kind: v.r#type.strip_prefix("minecraft:").unwrap_or(&v.r#type),
            name: v.identity.as_ref().and_then(|i| i.custom_name.as_deref()),
            distance: v.distance,
            direction: &v.direction,
        });
    }
    mobs
}

fn direction(direction: &Direction, body_yaw: Option<f64>) -> Option<String> {
    use CardinalDirection::*;
    use HorizontalDirection::*;
    let absolute = direction.cardinal.map(|cardinal| match cardinal {
        North => "北",
        Northeast => "北東",
        East => "東",
        Southeast => "南東",
        South => "南",
        Southwest => "南西",
        West => "西",
        Northwest => "北西",
    });
    let relative = direction.horizontal.map(|horizontal| match horizontal {
        Front => ("前", 0.0),
        FrontRight => ("右前", 45.0),
        Right => ("右", 90.0),
        BackRight => ("右後ろ", 135.0),
        Back => ("後ろ", 180.0),
        BackLeft => ("左後ろ", -135.0),
        Left => ("左", -90.0),
        FrontLeft => ("左前", -45.0),
    });
    let absolute = absolute.or_else(|| {
        body_yaw.zip(relative).map(|(yaw, (_, offset))| {
            ["南", "南西", "西", "北西", "北", "北東", "東", "南東"]
                [(((yaw + offset).rem_euclid(360.0) + 22.5) / 45.0).floor() as usize % 8]
        })
    });
    let horizontal = match (relative, absolute) {
        (Some((relative, _)), Some(absolute)) => Some(format!("{relative}の{absolute}側")),
        (Some((relative, _)), None) => Some(relative.to_owned()),
        (None, Some(absolute)) => Some(absolute.to_owned()),
        (None, None) => None,
    };
    match (horizontal, direction.vertical) {
        (Some(horizontal), Some(VerticalRelation::Above)) => Some(format!("{horizontal}の上の方")),
        (Some(horizontal), Some(VerticalRelation::Below)) => Some(format!("{horizontal}の下の方")),
        (Some(horizontal), _) => Some(horizontal),
        (None, Some(VerticalRelation::Above)) => Some("上の方".into()),
        (None, Some(VerticalRelation::Below)) => Some("下の方".into()),
        _ => None,
    }
}

pub(super) fn observations(event: &GameEvent, snapshot: &Snapshot, user: &str, history: &str) -> String {
    let current_topics = crate::conversation_observation::chat_topic_mobs(user, "", false);
    let past_topics = crate::conversation_observation::chat_topic_mobs(user, history, true);
    let matches = |kind: &str, topics: &[String]| {
        let kind = kind.strip_prefix("minecraft:").unwrap_or(kind);
        topics.is_empty() || topics.iter().any(|topic| topic == kind)
    };
    let mut facts = vec![];
    if let Some(origin) = event.body_origin().filter(|origin| origin.available) {
        let yaw = origin.body["current"]["self"]["view_yaw"].as_f64();
        let mut visible = BTreeMap::new();
        let observed = observed_mobs(event);
        let no_observations = observed.is_empty();
        for mob in observed.into_iter().filter(|mob| matches(mob.kind, &current_topics)) {
            visible.entry(label(mob.kind)).or_insert_with(Vec::new).push(json!({
                "名前":mob.name,"方向":direction(mob.direction, yaw),"距離（ブロック）":mob.distance
            }));
        }
        for (kind, individuals) in visible {
            facts.push(format!(
                "現在視認：{kind} {}体 {}",
                individuals.len(),
                json!(individuals)
            ));
        }
        if facts.is_empty() && no_observations {
            facts.push("現在視認：一覧にモブの記録なし".into());
        }
        if event.visual_threats.len() >= 64 || event.passive_mobs.len() >= 64 {
            facts.push("個体一覧は件数上限。掲載した数は下限。".into());
        }
        if matches("villager", &current_topics) && let Some(n) = event.world.visible_villager_count {
            facts.push(format!("同じ視認範囲の村人（一覧の件数制限前）：{n}体"));
        }
    } else {
        facts.push("現在視認：身体の観測を取得できず".into());
    }
    for past in snapshot.recent.passive_sightings.iter().filter(|past| matches(&past.mob_type, &past_topics)) {
        facts.push(format!(
            "直近の視認：{}（{}秒前）",
            label(&past.mob_type),
            past.seconds_ago
        ));
    }
    for past in snapshot.recent.visual_memos.iter().filter(|past| matches(&past.mob_type, &past_topics)) {
        let seconds = event
            .observed_at
            .timestamp_micros()
            .saturating_sub(past.seen_at_us)
            / 1_000_000;
        facts.push(format!(
            "視認履歴：{}（{}秒前）",
            label(&past.mob_type),
            seconds.max(0)
        ));
    }
    facts.push("今見える種類・数・向きはオレの視認範囲で答えてな。今回の画像に写ってるもんも、撮影時に見えたもんとして話せるで。直近の視認はその時のこと、音は聞こえたこととして扱ってな。一覧から消えただけで、おらんとは決めつけんといて。".into());
    facts.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::body::{State, contracts::Observation};
    use crate::chat_observation::{CatalogLabels, ChatObservationMemory};
    use std::time::{Duration, Instant};

    // 見込み: Bの身体視認とFの話題選択を同じ要求に通し、過去と現在の出典を分ける。
    // 合格: 名前つき質問では対象を絞り、省略した続きでは現在候補とパンダの過去視認を残す。
    // 確認範囲: 模擬身体から完成したモデル要求。実際の画像やモデル応答は対象外。
    #[tokio::test]
    async fn combined_request_selects_current_and_recent_topic_mobs() {
        let sent = std::sync::Arc::new(std::sync::Mutex::new(Vec::<serde_json::Value>::new()));
        let recorded = sent.clone();
        let app = axum::Router::new().route("/v1/chat/completions", axum::routing::post(
            move |axum::Json(payload): axum::Json<serde_json::Value>| {
                recorded.lock().unwrap().push(payload);
                async { axum::Json(json!({"id":"fixture","object":"chat.completion","created":0,"model":"fixture",
                    "choices":[{"index":0,"message":{"role":"assistant","content":"{\"action\":\"silent\",\"speech\":\"\"}"},"finish_reason":"stop"}]})) }
            }));
        let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}/v1", socket.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(socket, app).await.unwrap() });
        let llm = crate::llm::RigLlm::new(&url, None, Duration::from_secs(2)).unwrap();
        let mut body = crate::body::tests::fixture();
        let senses = &mut body["sample"]["senses"];
        senses["passive_mobs"] = json!([
            {"type":"panda","identity":{"entity_id":"panda-1"},"environment":{"on_ground":true}},
            {"type":"villager","identity":{"entity_id":"villager-1"},"environment":{"on_ground":true},"is_baby":false,"profession":"farmer"}
        ]);
        senses["world"]["visible_villager_count"] = json!(1);
        senses["world"]["time_of_day"] = json!(3000);
        senses["ambient_sounds"] = json!([
            {"type":"panda","sound_event":"entity.panda.ambient","heard_ago_ms":1000},
            {"type":"cow","sound_event":"entity.cow.ambient","heard_ago_ms":1000}
        ]);
        let reading: Observation = serde_json::from_value(body).unwrap();
        let at = Instant::now();
        let mut state = State::default();
        state.observe(Some(&reading), Some("11111111-1111-4111-8111-111111111111"),
            Some("minecraft:overworld"), at, Some(0));
        let raw = GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture",
            "observed_at":"2026-11-02T00:00:00Z","sequence":1,
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"dimension":"minecraft:overworld"},"world":{}})).unwrap();
        let event = state.sensory_event(&raw, at, None);
        let memory = ChatObservationMemory::new(Default::default());
        let mut snapshot = memory.snapshot_body(&event, &CatalogLabels);
        snapshot.recent.passive_sightings.push(crate::chat_observation::PassiveSighting {
            mob_type:"panda".into(), seconds_ago: 12,
        });
        let mut context = crate::chat_materials::Context::default();
        context.world_context = Some(crate::conversation_observation::project(&event, None, 10., 3., 5000).unwrap().for_model());
        let build = |event: &GameEvent, text: &str, history: &str, mob_question: bool| {
            let mut context = context.clone();
            context.history.conversation_history = history.into();
            context.world_context = Some(crate::conversation_observation::project(event, None, 10., 3., 5000).unwrap().for_model());
            let input = crate::chat_materials::PlayerInput { raw_text:text.into(),semantic_text:text.into(),asks_mob_information:mob_question,..Default::default() };
            let crate::chat_materials::Prepared::Reply(reply) = crate::chat_materials::prepare(
                event,&Default::default(),&input,&context,&snapshot,&CatalogLabels).unwrap() else { panic!() };
            reply.request("fixture", 128).unwrap()
        };
        let first_request = build(&event, "パンダはいる？", "", true);
        llm.generate(&first_request).await.unwrap();
        let first = &sent.lock().unwrap()[0]["messages"][1]["content"].as_str().unwrap().to_owned();
        assert!(first.contains("現在視認：パンダ") && first.contains("直近の視認：パンダ（12秒前）"), "{first}");
        assert!(!first.contains("現在視認：村人") && !first.contains("村人の日課"), "{first}");
        assert!(first.contains("entity.panda.ambient") && !first.contains("entity.cow.ambient"), "{first}");
        let catalogue = first.split("【対象のカタログ情報（一般的特徴・表現材料）】\n")
            .nth(1).unwrap().lines().next().unwrap();
        let catalogue: serde_json::Value = serde_json::from_str(catalogue).unwrap();
        assert_eq!(catalogue["entries"].as_array().unwrap().len(), 1);
        assert_eq!(catalogue["entries"][0]["id"], "panda");
        let first_system = sent.lock().unwrap()[0]["messages"][0]["content"].as_str().unwrap().to_owned();
        let description = catalogue["entries"][0]["general"]["poetic"]["role"].as_str().unwrap();
        assert_eq!(first_system.matches(description).count() + first.matches(description).count(), 1);
        let no_panda = event.with_passive_mobs(event.passive_mobs.iter().filter(|m| m.r#type != "panda").cloned().collect());
        let follow_request = build(&no_panda, "さっきいたよね？", "user: パンダはいる？\nassistant: 見えたで", true);
        llm.generate(&follow_request).await.unwrap();
        let follow = &sent.lock().unwrap()[1]["messages"][1]["content"].as_str().unwrap().to_owned();
        assert!(follow.contains("直近の視認：パンダ（12秒前）") && follow.contains("entity.panda.ambient")
            && !follow.contains("現在視認：パンダ") && follow.contains("現在視認：村人")
            && !follow.contains("村人の日課"), "{follow}");
        let others_request = build(&no_panda, "他にはいる？", "user: パンダはいる？\nassistant: 見えたで", true);
        llm.generate(&others_request).await.unwrap();
        let others = &sent.lock().unwrap()[2]["messages"][1]["content"].as_str().unwrap().to_owned();
        assert!(others.contains("現在視認：村人") && others.contains("直近の視認：パンダ（12秒前）"), "{others}");
        let villager_request = build(&event, "村人はいま仕事の時間？", "", false);
        llm.generate(&villager_request).await.unwrap();
        let villager = &sent.lock().unwrap()[3]["messages"][1]["content"].as_str().unwrap().to_owned();
        assert!(villager.contains("村人の日課") && villager.contains("仕事の時間"), "{villager}");
        assert!(!villager.contains("近くの生き物: パンダ") && !villager.contains("過去の視認（現在の在否は未確認）: パンダ"), "{villager}");
        assert_eq!(sent.lock().unwrap().len(), 4);
        server.abort();
    }

    // 模擬身体の視認一覧でID重複・遮蔽を除き、一覧上限と期限切れを分ける。実際の視認・応答は対象外。
    #[test]
    fn visible_counts_use_distinct_body_entities_and_respect_observation_limits() {
        let mut body = crate::body::tests::fixture();
        let senses = &mut body["sample"]["senses"];
        let wolf = json!({"type":"wolf","identity":{"entity_id":"one"},"environment":{"on_ground":true},
            "direction":{"horizontal":"right"}});
        senses["visual_threats"] = json!([wolf, {"type":"zombie","environment":{}}]);
        senses["passive_mobs"] = json!([wolf]);
        for i in 1..64 {
            senses["passive_mobs"]
                .as_array_mut()
                .unwrap()
                .push(json!({"type":"panda","identity":{"entity_id":i.to_string()}}));
        }
        senses["world"]["visible_villager_count"] = json!(5);
        let reading: Observation = serde_json::from_value(body).unwrap();
        reading.validate().unwrap();
        let at = Instant::now();
        let mut state = State::default();
        state.observe(
            Some(&reading),
            Some("11111111-1111-4111-8111-111111111111"),
            Some("minecraft:overworld"),
            at,
            Some(0),
        );
        let raw = GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture",
            "observed_at":"2026-11-02T00:00:00Z","sequence":1,"event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"dimension":"minecraft:overworld"},"world":{"visible_villager_count":99}}))
        .unwrap();
        let memory = ChatObservationMemory::new(Default::default());
        let project = |now| {
            let event = state.sensory_event(&raw, now, None);
            observations(&event, &memory.snapshot_body(&event, &CatalogLabels), "", "")
        };
        let fresh = project(at);
        assert!(fresh.contains("現在視認：オオカミ 1体"), "{fresh}");
        assert!(fresh.contains("現在視認：パンダ 63体") && fresh.contains("掲載した数は下限"));
        assert!(fresh.contains("件数制限前）：5体") && !fresh.contains("ゾンビ"));
        let stale = project(at + Duration::from_secs(10));
        assert!(stale.contains("現在視認：身体の観測を取得できず"));
        assert!(!stale.contains("99体") && !stale.contains("パンダ"));
    }
}
