//! 現在のコード観測だけで答える敵の方角・距離・地上個体数。
//! 呼出側が観測の鮮度を検証する。過去の敵や音だけから位置を補作しない。
use super::{
    catalog,
    model::{Scope, Settings, Speech},
};
use crate::events::{
    CardinalDirection, GameEvent, HorizontalDirection, VerticalRelation, VisualThreat,
};

fn folded(text: &str) -> String {
    text.chars()
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).expect("kana")
            } else {
                c
            }
        })
        .collect()
}
fn contains_any(text: &str, words: &[&str]) -> bool {
    words.iter().any(|word| text.contains(&folded(word)))
}
fn has_direction(text: &str) -> bool {
    contains_any(
        text,
        &["どこ", "どっち", "方向", "方角", "どのへん", "どの辺"],
    )
}
fn bare_direction(text: &str) -> bool {
    let compact: String = text
        .chars()
        .filter(|c| !c.is_whitespace() && !"?？!！。、…・".contains(*c))
        .collect();
    matches!(
        compact.as_str(),
        "どっち" | "どこ" | "どのへん" | "どの辺" | "方向" | "方角"
    )
}
fn flying(kind: &str) -> bool {
    matches!(
        kind.strip_prefix("minecraft:").unwrap_or(kind),
        "blaze" | "ender_dragon" | "ghast" | "phantom" | "vex" | "wither"
    )
}
fn named_kind(text: &str) -> Option<String> {
    // 長い種名を優先し、村人ゾンビをゾンビへ取り違えない。
    catalog::labels()
        .as_object()
        .expect("labels")
        .iter()
        .filter_map(|(kind, label)| label.as_str().map(|name| (kind, folded(name))))
        .filter(|(_, name)| !name.is_empty() && text.contains(name))
        .max_by_key(|(_, name)| name.len())
        .map(|(kind, _)| kind.clone())
}

/// `last_named` は実際に選んだ単体警告の種と単調時計。15秒以内の追質問にだけ使う。
pub fn answer(
    event: &GameEvent,
    text: &str,
    settings: &Settings,
    last_named: Option<(&str, u64)>,
    now_ms: u64,
) -> Option<Speech> {
    let text = folded(text.trim());
    if text.is_empty() {
        return None;
    }
    if has_direction(&text) && text.contains("どらごん") {
        return Some(dragon_direction(event));
    }
    let hostile_referent = contains_any(&text, &["敵", "モンスター", "モブ", "あいつ", "そいつ"]);
    if contains_any(
        &text,
        &["何体", "なんたい", "何匹", "残り", "何人", "何個体"],
    ) && (hostile_referent || contains_any(&text, &["残り", "あと"]))
    {
        let count = event
            .combat
            .hostiles_within_scan_ground
            .or(event.combat.hostiles_within_30_ground)
            .unwrap_or_else(|| {
                event
                    .visual_threats
                    .iter()
                    .filter(|v| {
                        !flying(&v.r#type)
                            && v.distance
                                .is_some_and(|d| d <= settings.number("hostile_query_distance"))
                    })
                    .count() as i64
            });
        let range = event.combat.hostile_scan_distance.unwrap_or_else(|| {
            if event.combat.hostiles_within_scan_ground.is_none()
                && event.combat.hostiles_within_30_ground.is_some()
            {
                30.0
            } else {
                settings.number("hostile_query_distance")
            }
        }) as u64;
        let text = if count <= 0 {
            format!("{range}ブロック以内には今はおらんかな。")
        } else {
            format!("{range}ブロック以内には今は{count}体おるで。")
        };
        // 地上scan数はvisual個体リストとは別の観測。敵音があってもこの明示質問には答える。
        let speech = Speech::new("hostile_count", text);
        return Some(speech);
    }
    let named = named_kind(&text);
    if !has_direction(&text) || (!hostile_referent && named.is_none() && !bare_direction(&text)) {
        return None;
    }
    // 裸の「どっち？」は敵の観測がない会話では選択肢への問いかもしれない。
    if bare_direction(&text) && event.visual_threats.is_empty() {
        return None;
    }
    let candidates: Vec<_> = event
        .visual_threats
        .iter()
        .filter(|v| v.distance.is_some())
        .collect();
    let recent_named = last_named
        .filter(|(_, at)| now_ms.saturating_sub(*at) <= 15_000)
        .map(|(kind, _)| kind);
    let selected_kind = named.as_deref().or(recent_named);
    let closest = |kind: Option<&str>| {
        candidates
            .iter()
            .copied()
            .filter(|v| {
                kind.is_none_or(|kind| {
                    v.r#type.strip_prefix("minecraft:").unwrap_or(&v.r#type) == kind
                })
            })
            .min_by(|a, b| a.distance.unwrap().total_cmp(&b.distance.unwrap()))
    };
    // 名指しされた種がいなければ別種を指ささない。追質問の既知種が消えた時だけ最近傍へ。
    let target =
        closest(selected_kind).or_else(|| if named.is_none() { closest(None) } else { None });
    let Some(target) = target else {
        return Some(Speech::new(
            "hostile_direction",
            "今は方位と距離を確かめられへんわ。",
        ));
    };
    let text = if let Some(direction) = absolute_direction(event, target) {
        let distance = (target.distance.unwrap() + 0.5).floor().max(1.0) as u64;
        format!("えーと……{direction}や。だいたい{distance}ブロック先くらいやな。")
    } else {
        "今は方位までは分からんわ。".to_owned()
    };
    Some(Speech::visual(
        "hostile_direction",
        text,
        vec![crate::threats::identity(target)],
    ))
}

fn absolute_direction(event: &GameEvent, target: &VisualThreat) -> Option<&'static str> {
    use CardinalDirection::*;
    if let Some(cardinal) = target.direction.cardinal {
        return Some(match cardinal {
            North => "北",
            Northeast => "北東",
            East => "東",
            Southeast => "南東",
            South => "南",
            Southwest => "南西",
            West => "西",
            Northwest => "北西",
        });
    }
    use HorizontalDirection::*;
    let offset = match target.direction.horizontal? {
        Front => 0.0,
        FrontRight => 45.0,
        Right => 90.0,
        BackRight => 135.0,
        Back => 180.0,
        BackLeft => -135.0,
        Left => -90.0,
        FrontLeft => -45.0,
    };
    let yaw = (event.player.yaw? + offset).rem_euclid(360.0);
    Some(
        ["南", "南西", "西", "北西", "北", "北東", "東", "南東"]
            [((yaw + 22.5) / 45.0).floor() as usize % 8],
    )
}

fn dragon_direction(event: &GameEvent) -> Speech {
    let target = event
        .visual_threats
        .iter()
        .find(|v| v.r#type.strip_prefix("minecraft:").unwrap_or(&v.r#type) == "ender_dragon");
    let explicit = event
        .combat
        .dragon_horizontal
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty());
    let horizontal = explicit.map(str::to_owned).or_else(|| {
        target
            .and_then(|v| v.direction.horizontal)
            .and_then(|v| serde_json::to_value(v).ok())
            .and_then(|v| v.as_str().map(str::to_owned))
    });
    let Some(horizontal) = horizontal else {
        return Speech::new(
            "dragon_direction",
            catalog::text("boss", &["ender_dragon", "direction_unknown"]),
        );
    };
    let above = if explicit.is_some() {
        event.combat.dragon_vertical.as_deref() == Some("above")
    } else {
        target.is_some_and(|v| v.direction.vertical == Some(VerticalRelation::Above))
    };
    let direction = match horizontal.as_str() {
        "front" => "前",
        "front_right" => "右前",
        "right" => "右",
        "back_right" => "右後ろ",
        "back" => "後ろ",
        "back_left" => "左後ろ",
        "left" => "左",
        "front_left" => "左前",
        _ => "近く",
    };
    let key = if above {
        "direction_above"
    } else {
        "direction_answer"
    };
    let mut result = Speech::new(
        "dragon_direction",
        catalog::text("boss", &["ender_dragon", key]).replace("{direction}", direction),
    );
    if let Some(target) = target {
        result.scope = Scope::Visual(vec![crate::threats::identity(target)]);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};
    fn event(visual: Value, combat: Value, yaw: Option<f64>) -> GameEvent {
        GameEvent::parse(json!({"schema_version":"2026-05-24","game":"minecraft-java","adapter":"dogido-fabric-client",
            "observed_at":"2026-09-25T00:00:00Z", "sequence":1,
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"yaw":yaw},"visual_threats":visual,"combat":combat})).unwrap()
    }
    fn mob(kind: &str, d: f64, horizontal: &str, cardinal: Option<&str>) -> Value {
        json!({"type":kind,"entity_id":kind,"distance":d,"direction":{"horizontal":horizontal,"cardinal":cardinal}})
    }
    #[test]
    fn current_named_target_and_last_spoken_species_win_over_nearest() {
        let e = event(
            json!([
                mob("zombie", 8.0, "back", Some("north")),
                mob("creeper", 11.6, "front_left", Some("southeast"))
            ]),
            json!({}),
            Some(0.0),
        );
        let answer = answer(
            &e,
            "どっち？",
            &Settings::default(),
            Some(("creeper", 1000)),
            2000,
        )
        .unwrap();
        assert_eq!(
            answer.text,
            "えーと……南東や。だいたい12ブロック先くらいやな。"
        );
        assert!(
            super::answer(
                &e,
                "ゾンビどこ？",
                &Settings::default(),
                Some(("creeper", 1000)),
                2000
            )
            .unwrap()
            .text
            .contains("北や")
        );
        assert!(
            super::answer(
                &e,
                "どっち？",
                &Settings::default(),
                Some(("creeper", 1000)),
                17000
            )
            .unwrap()
            .text
            .contains("北や")
        );
        assert!(
            super::answer(&e, "スケルトンどこ？", &Settings::default(), None, 2000)
                .unwrap()
                .text
                .contains("確かめられへん")
        );
        assert!(
            super::answer(&e, "赤と青、どっちがいい？", &Settings::default(), None, 0).is_none()
        );
    }
    #[test]
    fn yaw_fallback_and_unknown_preserve_observation_limits() {
        let e = event(
            json!([mob("zombie", 0.1, "front_left", None)]),
            json!({}),
            Some(0.0),
        );
        assert_eq!(
            answer(&e, "敵どこ？", &Settings::default(), None, 0)
                .unwrap()
                .text,
            "えーと……南東や。だいたい1ブロック先くらいやな。"
        );
        let e = event(json!([mob("zombie", 8.0, "front", None)]), json!({}), None);
        assert_eq!(
            answer(&e, "敵どこ？", &Settings::default(), None, 0)
                .unwrap()
                .text,
            "今は方位までは分からんわ。"
        );
        let empty = event(json!([]), json!({}), None);
        assert!(answer(&empty, "どっち？", &Settings::default(), None, 0).is_none());
        assert!(
            answer(&empty, "ゾンビどこ？", &Settings::default(), None, 0)
                .unwrap()
                .text
                .contains("確かめられへん")
        );
    }
    #[test]
    fn count_uses_scan_contract_and_excludes_flying_in_visual_fallback() {
        for (combat, expected) in [
            (
                json!({"hostile_scan_distance":16,"hostiles_within_scan_ground":3}),
                "16ブロック以内には今は3体おるで。",
            ),
            (
                json!({"hostiles_within_30_ground":3}),
                "30ブロック以内には今は3体おるで。",
            ),
            (json!({}), "16ブロック以内には今は1体おるで。"),
        ] {
            let e = event(
                json!([
                    mob("zombie", 8.0, "front", None),
                    mob("phantom", 8.0, "front", None),
                    mob("skeleton", 17.0, "front", None)
                ]),
                combat,
                None,
            );
            assert_eq!(
                answer(&e, "敵残り何体？", &Settings::default(), None, 0)
                    .unwrap()
                    .text,
                expected
            );
        }
        let e = event(json!([]), json!({}), None);
        assert!(answer(&e, "松明何個？", &Settings::default(), None, 0).is_none());
        assert_eq!(
            answer(&e, "敵何体？", &Settings::default(), None, 0)
                .unwrap()
                .text,
            "16ブロック以内には今はおらんかな。"
        );
    }
    #[test]
    fn dragon_query_precedes_other_hostiles_and_uses_boss_observation() {
        let e = event(
            json!([mob("zombie", 2.0, "front", None)]),
            json!({"dragon_horizontal":"back","dragon_vertical":"above"}),
            None,
        );
        assert_eq!(
            answer(&e, "ドラゴンどっち？", &Settings::default(), None, 0)
                .unwrap()
                .text,
            "ドラゴンは後ろの上空や！"
        );
        let e = event(json!([]), json!({}), None);
        assert!(
            answer(&e, "ドラゴンどこ？", &Settings::default(), None, 0)
                .unwrap()
                .text
                .contains("見失っとる")
        );
    }
}
