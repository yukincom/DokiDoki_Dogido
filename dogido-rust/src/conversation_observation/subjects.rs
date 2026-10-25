//! 一人分の降水・接触・遮蔽を結び、共通観測の材料へ整理する。
//! playerの世界は身体へ置換する前の値、ドギドの世界と接触は受理済みの同じ身体sampleを使う。
//! 会話本文・行動の意図・乾燥は決めない。取得なしは不明のまま残し、状態変更や保存も行わない。
use crate::environment::precipitation::{self, PrecipitationContext, PrecipitationKind};
use crate::events::{GameEvent, MobEnvironment, WorldState};
use serde_json::{Value, json};

/// 既存の共通観測生成で一度呼び、二者を同じ外形へ揃える。観測日時は対象ごとに保持する。
pub(super) fn project(
    event: &GameEvent,
    observer_precipitation: &PrecipitationContext,
) -> anyhow::Result<Value> {
    let player = player_state(event)?;
    let dogido = match event.body_origin().filter(|o| o.available) {
        Some(origin) => {
            // 感覚コピーの生成時刻を測定時刻と誤認しないよう、元sampleの年齢を一度引く。
            let observed_at = event.observed_at
                - chrono::Duration::milliseconds(origin.age_ms.min(i64::MAX as u64) as i64);
            subject(
                "ドギド",
                "body_pose_client_world",
                observed_at.to_rfc3339(),
                &event.world,
                event.observer_environment(),
                observer_precipitation.precipitation_kind,
            )
        }
        None => json!({"subject":"ドギド","status":"unavailable"}),
    };
    Ok(json!({"player":player,"dogido":dogido}))
}

/// 身体が未取得でも、元イベントに含まれるplayer本人の実測を同じ外形で返す。
/// 緊急反応が安全事実を使うときも、通常会話と別の意味付けを作らない。
pub(crate) fn player_state(event: &GameEvent) -> anyhow::Result<Value> {
    let player_world = event.player_safety_world();
    let player_weather = {
        let climate = crate::world_catalog::catalog().climate(player_world.biome.as_deref())?;
        precipitation::from_observation(
            player_world,
            event.player.dimension.as_deref(),
            event.player.position.y,
            &climate,
            if event.body_origin().is_some() {
                &[]
            } else {
                &event.nearby_resources
            },
        )?
    };
    let player_time = event
        .body_origin()
        .map_or(event.observed_at, |o| o.player_observed_at);
    let mut player = subject(
        "プレイヤー",
        "player_entity_and_world",
        player_time.to_rfc3339(),
        player_world,
        event.player.environment.as_ref(),
        player_weather.precipitation_kind,
    );
    player["motion"] = json!(event.player.motion);
    player["vitals"] = json!({"health":event.player.health,"hunger":event.player.hunger,
        "air_supply_ticks":player_world.air_supply,"invulnerable":event.player.invulnerable,
        "active_status_effects":event.player.active_status_effects});
    Ok(player)
}

fn subject(
    label: &str,
    basis: &str,
    observed_at: String,
    world: &WorldState,
    environment: Option<&MobEnvironment>,
    precipitation: PrecipitationKind,
) -> Value {
    let water = environment.and_then(|e| e.touching_water);
    let submerged = environment.and_then(|e| e.submerged_in_water);
    let combined = environment.and_then(|e| e.touching_water_or_rain);
    let rain = rain_contact(environment);
    json!({
        "subject":label,"status":"observed","basis":basis,"observed_at":observed_at,
        "biome":world.biome,"precipitation_kind":precipitation,
        "overhead_cover":world.overhead_cover_type,"sky_visible":world.sky_visible,
        "contact":{"touching_water":water,"submerged_in_water":submerged,
            "touching_rain":environment.and_then(|e| e.touching_rain),
            "touching_lava":environment.and_then(|e| e.touching_lava),"touching_water_or_rain":combined,
            "on_ground":environment.and_then(|e| e.on_ground),"on_fire":environment.and_then(|e| e.on_fire)},
        "motion":null,"vitals":null,
        "relations":{
            "heat":heat_relation(label, environment),
            "water":water_relation(label, water, submerged),
            "rain_and_cover":rain_relation(label, precipitation, rain, world.overhead_cover_type.as_deref(), world.sky_visible)
        }
    })
}

/// 合成の接触値だけでは水と雨を分けられない場合がある。直接の雨実測を優先し、不足時も論理的に確定する範囲だけ返す。
fn rain_contact(environment: Option<&MobEnvironment>) -> Option<bool> {
    let e = environment?;
    e.touching_rain
        .or(match (e.touching_water, e.touching_water_or_rain) {
            (_, Some(false)) => Some(false),
            (Some(false), Some(true)) => Some(true),
            _ => None,
        })
}

fn heat_relation(label: &str, environment: Option<&MobEnvironment>) -> String {
    match (
        environment.and_then(|e| e.touching_lava),
        environment.and_then(|e| e.on_fire),
    ) {
        (Some(true), Some(true)) => format!("{label}は溶岩に触れ、炎上している"),
        (Some(true), _) => format!("{label}は溶岩に触れている"),
        (Some(false), Some(true)) => {
            format!("{label}は溶岩に触れていないが、炎上している。炎上の原因は未確認")
        }
        (_, Some(true)) => format!("{label}は炎上している。溶岩との接触は未確認"),
        (Some(false), Some(false)) => format!("{label}の溶岩への接触と炎上はない"),
        _ => format!("{label}の溶岩への接触・炎上は未確認"),
    }
}

fn water_relation(label: &str, water: Option<bool>, submerged: Option<bool>) -> String {
    match (water, submerged) {
        (_, Some(true)) => format!("{label}の眼の位置は水面より下にある"),
        (Some(true), Some(false)) => {
            format!("{label}は水に触れているが、眼の位置は水面より下ではない")
        }
        (Some(true), None) => format!("{label}は水に触れている。水没は未確認"),
        (Some(false), _) => format!("{label}は水に触れていない"),
        _ => format!("{label}の水への接触は未確認"),
    }
}

fn rain_relation(
    label: &str,
    precipitation: PrecipitationKind,
    rain: Option<bool>,
    cover: Option<&str>,
    sky: Option<bool>,
) -> String {
    use PrecipitationKind::{Rain, Snow, Unknown};
    match (precipitation, rain) {
        (Rain, Some(true)) => {
            if cover == Some("foliage") {
                format!("{label}の頭上には木の葉があるが、本人には雨が当たっている")
            } else {
                format!("雨が降っており、{label}にも雨が当たっている")
            }
        }
        (Rain, Some(false)) => match (cover, sky) {
            (Some("foliage"), Some(false)) => {
                format!("雨が降っているが、{label}は木の葉の下で雨に当たっていない")
            }
            (Some("stone" | "wood" | "solid"), Some(false)) => {
                format!("雨が降っているが、{label}は頭上の遮蔽物の下で雨に当たっていない")
            }
            _ => format!("雨が降っているが、{label}には雨が当たっていない。遮蔽の理由は未確認"),
        },
        (Rain, None) => format!("雨が降っている。{label}に雨が当たっているかは未確認"),
        (Snow, Some(true)) | (PrecipitationKind::None, Some(true)) => {
            format!("{label}の雨への接触は観測されているが、現在の降水種別と一致していない")
        }
        (Snow, _) => format!("降雪中。{label}に雪が当たっているかは未観測"),
        (PrecipitationKind::None, _) => "現在の降水はない".into(),
        (Unknown, Some(true)) => format!("降水種別は不明だが、{label}の雨への接触は観測されている"),
        (Unknown, Some(false)) => format!("降水種別は不明。{label}の雨への接触はない"),
        (Unknown, None) => format!("降水種別と{label}の雨への接触は未確認"),
    }
}

/// 既存の差分比較で日時更新だけを変化に数えない。接触・遮蔽の変化は既存の周期通知へ渡す。
pub(crate) fn signature(value: &Value) -> Value {
    let mut result = value.clone();
    if let Some(subjects) = result.as_object_mut() {
        for subject in subjects.values_mut().filter_map(Value::as_object_mut) {
            subject.remove("observed_at");
            // 体力・速度の小刻みな更新で周期通知を増やさない。危険の入口は専用のコード判定が所有する。
            subject.remove("motion");
            subject.remove("vitals");
            // 接地の小刻みな更新は通知にしない。炎上の変化は整理済みheat関係で一度比較する。
            if let Some(contact) = subject.get_mut("contact").and_then(Value::as_object_mut) {
                contact.remove("on_ground");
                contact.remove("on_fire");
            }
        }
    }
    result
}

#[cfg(test)]
mod tests;
