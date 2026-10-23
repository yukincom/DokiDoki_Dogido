//! 分類済みの周囲モブ質問を、身体の現在視認だけで解決する。
//! 名前は正本カタログと観測済み名札、実数は個体一覧を使う。
use std::collections::{BTreeMap, BTreeSet};

use super::{bare_direction, contains_any, folded, has_direction};
use crate::{
    combat::model::{Delivery, Speech},
    events::{
        CardinalDirection, Direction, GameEvent, HorizontalDirection, MobEnvironment,
        VerticalRelation,
    },
};

struct Mob<'a> {
    kind: &'a str,
    name: Option<&'a str>,
    distance: Option<f64>,
    direction: &'a Direction,
    hostile: bool,
}

enum Target {
    Kinds(BTreeSet<String>),
    Named(BTreeSet<String>),
    Hostiles,
    All,
}
impl Target {
    fn includes(&self, mob: &Mob<'_>) -> bool {
        match self {
            Self::Kinds(kinds) => kinds.contains(mob.kind),
            Self::Named(names) => mob.name.is_some_and(|name| names.contains(name)),
            Self::Hostiles => mob.hostile,
            Self::All => true,
        }
    }
    fn label(&self) -> String {
        match self {
            Self::Kinds(kinds) => kinds
                .iter()
                .map(|kind| label(kind))
                .collect::<Vec<_>>()
                .join("と"),
            Self::Named(names) => names.iter().cloned().collect::<Vec<_>>().join("と"),
            Self::Hostiles => "敵".into(),
            Self::All => "モブ".into(),
        }
    }
}

fn label(kind: &str) -> String {
    crate::chat_catalog::catalog()
        .mob_entry(kind)
        .and_then(|entry| entry["label"].as_str())
        .unwrap_or(kind)
        .to_owned()
}

// environment={} は遮蔽中の追跡保持。距離があっても現在の視認個体へ昇格させない。
fn currently_visible(environment: Option<&MobEnvironment>) -> bool {
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
            hostile: true,
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
            hostile: false,
        });
    }
    mobs
}

fn target(
    text: &str,
    mobs: &[Mob<'_>],
    last_named: Option<(&str, u64)>,
    now_ms: u64,
) -> Option<Target> {
    let names: BTreeSet<_> = mobs
        .iter()
        .filter_map(|mob| mob.name)
        .filter(|name| !name.trim().is_empty() && text.contains(&folded(name)))
        .map(str::to_owned)
        .collect();
    if !names.is_empty() {
        return Some(Target::Named(names));
    }

    // 長い呼称に含まれる短い種名だけを落とす。「村人ゾンビ」を二種へ広げず、
    // 別々に指定された「村人とゾンビ」は両方保持する。同じ別名を持つ亜種も保持する。
    let mut matches = Vec::new();
    for (kind, entry) in crate::chat_catalog::catalog().all_mob_entries() {
        let aliases = std::iter::once(entry["label"].as_str())
            .chain(
                ["spoken_aliases", "observed_speech_aliases"]
                    .into_iter()
                    .flat_map(|field| {
                        entry[field]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .map(|v| v.as_str())
                    }),
            )
            .flatten();
        for alias in aliases {
            let alias = folded(alias);
            if alias.is_empty() {
                continue;
            }
            for (start, _) in text.match_indices(&alias) {
                matches.push((start, start + alias.len(), kind));
            }
        }
    }
    let kinds = matches
        .iter()
        .filter(|(start, end, _)| {
            !matches.iter().any(|(other_start, other_end, _)| {
                other_start <= start && other_end >= end && (other_start < start || other_end > end)
            })
        })
        .map(|(_, _, kind)| (*kind).clone())
        .collect::<BTreeSet<_>>();
    if !kinds.is_empty() {
        return Some(Target::Kinds(kinds));
    }
    if bare_direction(text) || contains_any(text, &["あいつ", "そいつ", "さっきの敵", "今の敵"])
    {
        return last_named
            .filter(|(_, at)| now_ms.saturating_sub(*at) <= 15_000)
            .map(|(kind, _)| {
                Target::Kinds(BTreeSet::from([kind
                    .strip_prefix("minecraft:")
                    .unwrap_or(kind)
                    .to_owned()]))
            });
    }
    if contains_any(text, &["敵", "モンスター"]) {
        return Some(Target::Hostiles);
    }
    if contains_any(
        text,
        &[
            "モブ",
            "生き物",
            "動物",
            "何かいる",
            "何かおる",
            "なんかいる",
            "なんかおる",
            "何がいる",
            "何がおる",
            "なにがいる",
            "なにがおる",
            "周りに何",
            "周りには何",
            "周辺に何",
            "辺りに何",
        ],
    ) {
        return Some(Target::All);
    }
    None
}

fn speech(text: impl Into<String>) -> Speech {
    let mut speech = Speech::new("mob_information", text);
    speech.delivery = Delivery::PlayerReply;
    // 友好個体はScope::Visualの敵リスト照合へ渡さない。配送前に同じ質問を再計算する。
    speech
}

fn summary(mobs: &[&Mob<'_>], minimum: bool) -> String {
    let mut counts = BTreeMap::<&str, usize>::new();
    for mob in mobs {
        *counts.entry(mob.kind).or_default() += 1;
    }
    let qualifier = if minimum { "少なくとも" } else { "" };
    let mut parts: Vec<_> = counts
        .iter()
        .take(6)
        .map(|(kind, count)| format!("{}が{qualifier}{count}体", label(kind)))
        .collect();
    if counts.len() > 6 {
        parts.push(format!("ほか{}種類", counts.len() - 6));
    }
    parts.join("、")
}

pub(super) fn answer(
    event: &GameEvent,
    text: &str,
    last_named: Option<(&str, u64)>,
    now_ms: u64,
) -> Option<Speech> {
    let text = folded(text.trim());
    if text.is_empty() {
        return None;
    }
    let Some(origin) = event.body_origin().filter(|origin| origin.available) else {
        return Some(speech("今はオレの周りのモブを確かめられへんわ。"));
    };
    let mobs = observed_mobs(event);
    let Some(target) = target(&text, &mobs, last_named, now_ms) else {
        return Some(speech("どのモブのことか、今の観測では特定できへんわ。"));
    };
    let selected: Vec<_> = mobs.iter().filter(|mob| target.includes(mob)).collect();
    let target_label = target.label();
    let asks_count = contains_any(
        &text,
        &[
            "何体",
            "なんたい",
            "何匹",
            "なんびき",
            "何人",
            "何頭",
            "何個体",
            "数",
            "残り",
        ],
    );
    let asks_location = has_direction(&text)
        || contains_any(
            &text,
            &[
                "位置",
                "場所",
                "どれくらい",
                "どのくらい",
                "距離",
                "近い",
                "遠い",
            ],
        );
    let list_may_be_capped = event.visual_threats.len() >= 64
        || (!matches!(&target, Target::Hostiles) && event.passive_mobs.len() >= 64);
    // 村人の件数だけは身体の同じ視認scanから、個体一覧の件数制限前に採取される。
    let villager_count = match &target {
        Target::Kinds(kinds) if kinds.len() == 1 && kinds.contains("villager") => {
            event.world.visible_villager_count
        }
        _ => None,
    };
    let count = villager_count
        .map(|count| count.max(0) as usize)
        .unwrap_or(selected.len());
    if count == 0 {
        return Some(speech(if list_may_be_capped && villager_count.is_none() {
            format!(
                "今の観測一覧では{target_label}を確認できへん。全部を数え切れてへんから、おらんとは言い切れへんわ。"
            )
        } else {
            format!("オレから今見える範囲では、{target_label}は見当たらへんわ。")
        }));
    }
    if asks_count {
        let qualifier = if list_may_be_capped && villager_count.is_none() {
            "少なくとも"
        } else {
            ""
        };
        return Some(speech(format!(
            "オレから今見える範囲では、{target_label}を合わせて{qualifier}{count}体確認できるで。"
        )));
    }
    if !asks_location {
        let lists_kinds = matches!(&target, Target::All | Target::Hostiles)
            || matches!(&target, Target::Kinds(kinds) if kinds.len() > 1);
        return Some(speech(if lists_kinds {
            format!(
                "オレから今見える範囲では、{}確認できるで。",
                summary(&selected, list_may_be_capped)
            )
        } else {
            format!("オレから今見える範囲に、{target_label}がおるで。")
        }));
    }
    let closest = selected.iter().copied().min_by(|a, b| {
        a.distance
            .unwrap_or(f64::INFINITY)
            .total_cmp(&b.distance.unwrap_or(f64::INFINITY))
    });
    let Some(closest) = closest else {
        return Some(speech(format!(
            "{target_label}は見えてるけど、個体ごとの位置までは確認できへんわ。"
        )));
    };
    let subject = closest
        .name
        .map(str::to_owned)
        .unwrap_or_else(|| label(closest.kind));
    let direction = direction(
        closest.direction,
        origin.body["current"]["self"]["view_yaw"].as_f64(),
    );
    let distance = closest.distance.map(|d| (d + 0.5).floor().max(1.0) as u64);
    let text = match (direction, distance) {
        (Some(direction), Some(distance)) => {
            format!("{subject}は、オレから見て{direction}、だいたい{distance}ブロック先におるで。")
        }
        (Some(direction), None) => {
            format!("{subject}は、オレから見て{direction}におるで。距離までは分からへん。")
        }
        (None, Some(distance)) => {
            format!("{subject}は、オレからだいたい{distance}ブロック先や。方角までは分からへん。")
        }
        (None, None) => format!("{subject}は見えてるけど、方角と距離までは分からへん。"),
    };
    Some(speech(text))
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::body::{State, contracts::Observation};
    use crate::combat::{core::Engine, model::Settings};
    use serde_json::{Value, json};
    use std::time::{Duration, Instant};

    fn mob(kind: &str, id: &str, distance: f64, horizontal: &str, cardinal: Option<&str>) -> Value {
        json!({"type":kind,"entity_id":id,"identity":{"entity_id":id},
            "environment":{"on_ground":true},"distance":distance,
            "direction":{"horizontal":horizontal,"cardinal":cardinal}})
    }
    fn observation(
        visual: Value,
        passive: Value,
        world: Value,
        yaw: f64,
    ) -> (GameEvent, State, Instant) {
        let raw = GameEvent::parse(json!({"schema_version":"2026-05-24","game":"minecraft-java",
            "adapter":"dogido-fabric-client","observed_at":"2026-10-23T00:00:00Z","sequence":1,
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"yaw":-90.0,"dimension":"minecraft:overworld"},
            "visual_threats":[mob("zombie","player-only",1.0,"front",Some("east"))],
            "passive_mobs":[mob("sheep","player-sheep",1.0,"front",None)],
            "world":{"visible_villager_count":99},
            "combat":{"hostile_scan_distance":16,"hostiles_within_scan_ground":99,
                "dragon_horizontal":"back","dragon_vertical":"above"}})).unwrap();
        let mut value = crate::body::tests::fixture();
        value["sample"]["self_pose"]["yaw"] = json!(yaw);
        value["sample"]["self_pose"]["view_yaw"] = json!(yaw);
        value["sample"]["senses"]["world"] = world;
        value["sample"]["senses"]["visual_threats"] = visual;
        value["sample"]["senses"]["passive_mobs"] = passive;
        let observation: Observation = serde_json::from_value(value).unwrap();
        observation.validate().unwrap();
        let at = Instant::now();
        let mut state = State::default();
        state.observe(
            Some(&observation),
            Some("11111111-1111-4111-8111-111111111111"),
            Some("minecraft:overworld"),
            at,
            Some(0),
        );
        (raw, state, at)
    }
    fn event(visual: Value, passive: Value, world: Value, yaw: f64) -> GameEvent {
        let (raw, state, at) = observation(visual, passive, world, yaw);
        state.sensory_event(&raw, at)
    }
    fn classified(event: &GameEvent, text: &str, recent: Option<(&str, u64)>, now: u64) -> Speech {
        // BERTへ変更済み。旧判定へ戻さない。依頼性は分類済み入口の契約として与える。
        let answer = crate::combat::queries::answer_classified(event, text, recent, now).unwrap();
        assert_eq!(answer.kind, "mob_information");
        assert_eq!(answer.delivery, Delivery::PlayerReply);
        assert!(matches!(answer.scope, crate::combat::model::Scope::Event));
        answer
    }

    #[test]
    fn bert_named_target_and_recent_reference_never_substitute_another_species() {
        let e = event(
            json!([
                mob("zombie", "z1", 8.0, "back", Some("north")),
                mob("creeper", "c1", 11.6, "front_left", Some("southeast"))
            ]),
            json!([]),
            json!({}),
            0.0,
        );
        let answer = classified(&e, "どっち？", Some(("creeper", 1000)), 2000);
        assert!(
            answer.text.contains("クリーパー")
                && answer.text.contains("南東")
                && answer.text.contains("12ブロック")
        );
        assert!(
            classified(&e, "ゾンビどこ？", Some(("creeper", 1000)), 2000)
                .text
                .contains("北")
        );
        assert!(
            classified(&e, "どっち？", Some(("creeper", 1000)), 17000)
                .text
                .contains("特定できへん")
        );
        for text in ["腐れ野郎はどこ？", "赤と青、どっちがいい？"] {
            let reply = classified(&e, text, None, 2000);
            assert!(
                reply.text.contains("特定できへん"),
                "{text}: {}",
                reply.text
            );
            assert!(!reply.text.contains("ゾンビ") && !reply.text.contains("クリーパー"));
        }
        assert!(
            classified(&e, "スケルトンどこ？", None, 2000)
                .text
                .contains("見当たらへん")
        );
        let settings = Settings::default();
        let warnings = crate::threats::Settings::default();
        let mut engine = Engine::default();
        assert!(
            engine
                .input_with_enemy_request(&e, "敵どこ？", 0, &settings, &warnings, false)
                .is_none()
        );
        let decision = engine
            .input_with_enemy_request(&e, "敵どこ？", 0, &settings, &warnings, true)
            .unwrap();
        assert_eq!(decision.actions[0].kind, "mob_information");
    }

    #[test]
    fn bert_query_uses_body_yaw_and_missing_or_stale_senses_never_use_player() {
        let (raw, state, at) = observation(
            json!([]),
            json!([mob("cow", "cow1", 0.1, "front", None)]),
            json!({}),
            90.0,
        );
        let body = state.sensory_event(&raw, at);
        let reply = classified(&body, "牛どこ？", None, 0);
        assert!(reply.text.contains("西") && reply.text.contains("1ブロック"));
        assert!(!reply.text.contains("東"));
        assert!(
            classified(&body, "ゾンビどこ？", None, 0)
                .text
                .contains("見当たらへん")
        );
        assert!(
            classified(&raw, "敵どこ？", None, 0)
                .text
                .contains("確かめられへん")
        );
        let stale = state.sensory_event(&raw, at + Duration::from_secs(3));
        assert!(
            classified(&stale, "牛どこ？", None, 0)
                .text
                .contains("確かめられへん")
        );
        let empty = event(json!([]), json!([]), json!({}), 0.0);
        assert!(
            classified(&empty, "牛どこ？", None, 0)
                .text
                .contains("見当たらへん")
        );
    }

    #[test]
    fn bert_counts_named_friendly_and_hostile_mobs_with_observation_limits() {
        let e = event(
            json!([
                mob("zombie", "z1", 8.0, "front", None),
                mob("phantom", "p1", 8.0, "front", None),
                mob("skeleton", "s1", 17.0, "front", None)
            ]),
            json!([
                mob("cow", "cow1", 3.0, "front", None),
                mob("cow", "cow2", 4.0, "front", None),
                mob("sheep", "sheep1", 5.0, "front", None)
            ]),
            json!({}),
            0.0,
        );
        assert!(classified(&e, "敵は何体？", None, 0).text.contains("3体"));
        assert!(classified(&e, "牛は何匹？", None, 0).text.contains("2体"));
        assert!(classified(&e, "羊は何匹？", None, 0).text.contains("1体"));
        let listing = classified(&e, "周りに何がいる？", None, 0).text;
        assert!(
            listing.contains("ウシが2体")
                && listing.contains("ヒツジが1体")
                && listing.contains("ファントムが1体")
        );
        assert!(!listing.contains("99"));
        assert!(
            classified(&e, "知らない相手は何匹？", None, 0)
                .text
                .contains("特定できへん")
        );
        let capped: Vec<_> = (0..64)
            .map(|i| mob("cow", &format!("cow{i}"), 3.0, "front", None))
            .collect();
        let e = event(
            json!([]),
            json!(capped),
            json!({"visible_villager_count":120}),
            0.0,
        );
        assert!(
            classified(&e, "牛は何匹？", None, 0)
                .text
                .contains("少なくとも64体")
        );
        let villagers = classified(&e, "村人は何人？", None, 0).text;
        assert!(villagers.contains("120体") && !villagers.contains("少なくとも"));
        assert!(
            classified(&e, "羊は何匹？", None, 0)
                .text
                .contains("おらんとは言い切れへん")
        );
        let e = event(
            json!([mob("zombie_villager", "zv1", 3.0, "front", None)]),
            json!([mob("villager", "v1", 4.0, "front", None)]),
            json!({"visible_villager_count":1}),
            0.0,
        );
        assert!(
            classified(&e, "村人ゾンビは何体？", None, 0)
                .text
                .contains("合わせて1体")
        );
    }

    #[test]
    fn bert_dragon_query_uses_body_vision_and_excludes_retained_hidden_targets() {
        let mut dragon = mob("ender_dragon", "dragon1", 12.0, "front_left", None);
        dragon["direction"]["vertical"] = json!("above");
        let e = event(
            json!([dragon.clone(), mob("zombie", "z1", 2.0, "front", None)]),
            json!([]),
            json!({}),
            0.0,
        );
        let reply = classified(&e, "ドラゴンどっち？", None, 0).text;
        assert!(
            reply.contains("エンダードラゴン")
                && reply.contains("左前")
                && reply.contains("上の方")
        );
        assert!(!reply.contains("後ろ"));
        dragon["environment"] = json!({});
        let hidden = event(
            json!([dragon, mob("zombie", "z1", 2.0, "front", None)]),
            json!([]),
            json!({}),
            0.0,
        );
        assert!(
            classified(&hidden, "ドラゴンどこ？", None, 0)
                .text
                .contains("見当たらへん")
        );
        assert!(
            classified(&hidden, "敵何体？", None, 0)
                .text
                .contains("合わせて1体")
        );
    }
}
