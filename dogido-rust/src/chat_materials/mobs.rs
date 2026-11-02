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

pub(super) fn observations(event: &GameEvent, snapshot: &Snapshot) -> String {
    let mut facts = vec![];
    if let Some(origin) = event.body_origin().filter(|origin| origin.available) {
        let yaw = origin.body["current"]["self"]["view_yaw"].as_f64();
        let mut visible = BTreeMap::new();
        for mob in observed_mobs(event) {
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
        if facts.is_empty() {
            facts.push("現在視認：一覧にモブの記録なし".into());
        }
        if event.visual_threats.len() >= 64 || event.passive_mobs.len() >= 64 {
            facts.push("個体一覧は件数上限。掲載した数は下限。".into());
        }
        if let Some(n) = event.world.visible_villager_count {
            facts.push(format!("同じ視認範囲の村人（一覧の件数制限前）：{n}体"));
        }
    } else {
        facts.push("現在視認：身体の観測を取得できず".into());
    }
    for past in &snapshot.recent.passive_sightings {
        facts.push(format!(
            "直近の視認：{}（{}秒前）",
            label(&past.mob_type),
            past.seconds_ago
        ));
    }
    for past in &snapshot.recent.visual_memos {
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
            observations(&event, &memory.snapshot_body(&event, &CatalogLabels))
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
