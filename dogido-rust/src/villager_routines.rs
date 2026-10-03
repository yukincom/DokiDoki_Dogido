//! Current villagers' daily timetable, never a claim of measured movement/sleep.
use crate::events::GameEvent;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Routine {
    pub label: String,
    pub is_baby: Option<bool>,
    pub profession: Option<String>,
    pub activity: String,
    pub activity_label: String,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub basis: String,
    pub usage: String,
    pub villagers: Vec<Routine>,
}

fn normalized(raw: &str) -> String {
    let value = raw.trim().to_lowercase();
    value
        .strip_prefix("minecraft:")
        .unwrap_or(&value)
        .to_owned()
}

// Java 1.21.11 data/minecraft/timeline/villager_schedule.json. Rest wraps midnight.
// Job availability still gates whether an adult has a work activity.
pub fn activity(time: i64, baby: bool, profession: Option<&str>) -> &'static str {
    let t = time.rem_euclid(24000);
    if t < 10 || t >= 12000 {
        return "sleep";
    }
    if baby {
        return if (3000..6000).contains(&t) || t >= 10000 {
            "play"
        } else {
            "wander"
        };
    }
    if t >= 11000 {
        return "wander";
    }
    if t >= 9000 {
        return "gather";
    }
    let profession = profession.map(normalized).unwrap_or_default();
    if t >= 2000
        && !["", "unknown", "unregistered", "-", "none", "nitwit"].contains(&profession.as_str())
    {
        "work"
    } else {
        "wander"
    }
}

fn label(activity: &str) -> &'static str {
    match activity {
        "sleep" => "休息・睡眠の時間",
        "work" => "仕事の時間",
        "gather" => "集会の時間",
        "play" => "遊びの時間",
        "wander" => "自由に過ごす時間",
        _ => "生活予定は未確認",
    }
}

pub fn project(event: &GameEvent) -> Option<Context> {
    let mut rows = Vec::new();
    for mob in event
        .passive_mobs
        .iter()
        .filter(|m| normalized(&m.r#type) == "villager")
    {
        let profession = mob.profession.as_deref().map(normalized);
        let state = match (event.world.time_of_day, mob.is_baby) {
            (Some(time), Some(false))
                if (2000..9000).contains(&time.rem_euclid(24000))
                    && profession.as_deref().is_none_or(|id| {
                        crate::entry_catalog::PASSIVE["items"]["villager"]["professions"]
                            .get(id)
                            .is_none()
                    }) =>
            {
                "unknown"
            }
            (Some(time), Some(baby)) => activity(time, baby, profession.as_deref()),
            _ => "unknown",
        };
        let label = crate::catalog_knowledge::mob(
            "villager",
            profession.as_deref(),
            mob.is_baby == Some(true),
            "visual",
        )
        .map(|entry| entry.label)
        .unwrap_or_else(|| "村人".into());
        let row = Routine {
            label,
            is_baby: mob.is_baby,
            profession,
            activity: state.into(),
            activity_label: self::label(state).into(),
        };
        if !rows.contains(&row) {
            rows.push(row);
        }
    }
    if rows.is_empty() {
        let observed = event.world.visible_villager_count.is_some_and(|n| n > 0)
            || event.look_target.as_ref().is_some_and(|target| {
                target.kind == "entity" && normalized(&target.name) == "villager"
            });
        if !observed {
            return None;
        }
        rows.push(Routine {
            label: "村人（年齢・職業未確認）".into(),
            is_baby: None,
            profession: None,
            activity: "unknown".into(),
            activity_label: label("unknown").into(),
        });
    }
    rows.sort_by(|a, b| {
        (&a.label, &a.profession, a.is_baby).cmp(&(&b.label, &b.profession, b.is_baby))
    });
    Some(Context {
        basis:"current_observed_villagers_and_game_time".into(),
        usage:"今、観測範囲におる村人について、ゲーム内の時刻と年齢・職業から求めた日課や。特徴を話題にするときは、今が遊び・仕事・集会・休息のどの時間かも踏まえてな。実際に寝た、走った、働いたことを確認した記録とは別やで。時刻や年齢が分からん場合、仕事の時間帯に職業が分からん場合は、生活予定も未確認や。".into(),
        villagers:rows,
    })
}

pub fn separate(context: &mut serde_json::Map<String, Value>) {
    let routine = context
        .get_mut("world_context")
        .and_then(Value::as_object_mut)
        .and_then(|world| world.shift_remove("villager_routines"));
    if let Some(routine) = routine.filter(|value| !value.is_null()) {
        context.insert("villager_routines".into(), routine);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn frame(time: Value, mobs: Value) -> GameEvent {
        GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-10-03T00:00:00Z",
          "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
          "player":{"dimension":"minecraft:overworld"},"world":{"time_of_day":time,"sky_visible":false},"passive_mobs":mobs})).unwrap()
    }
    #[test]
    fn child_and_adult_follow_vanilla_timeline_boundaries() {
        for (t, expected) in [
            (0, "sleep"),
            (9, "sleep"),
            (10, "wander"),
            (2999, "wander"),
            (3000, "play"),
            (5999, "play"),
            (6000, "wander"),
            (9999, "wander"),
            (10000, "play"),
            (11000, "play"),
            (11999, "play"),
            (12000, "sleep"),
            (23999, "sleep"),
            (24010, "wander"),
        ] {
            assert_eq!(activity(t, true, None), expected, "child {t}");
        }
        for (t, expected) in [
            (10, "wander"),
            (1999, "wander"),
            (2000, "work"),
            (8999, "work"),
            (9000, "gather"),
            (10999, "gather"),
            (11000, "wander"),
            (12000, "sleep"),
        ] {
            assert_eq!(activity(t, false, Some("farmer")), expected, "adult {t}");
        }
        assert_eq!(activity(4000, false, Some("none")), "wander");
    }
    #[test]
    fn only_current_villagers_get_routines_and_clock_unknown_stays_unknown() {
        for profession in [
            Value::Null,
            json!("unknown"),
            json!("unregistered"),
            json!("mod_job"),
        ] {
            let adult = json!([{"type":"villager","is_baby":false,"profession":profession}]);
            assert_eq!(
                project(&frame(json!(4000), adult.clone()))
                    .unwrap()
                    .villagers[0]
                    .activity,
                "unknown"
            );
            assert_eq!(
                project(&frame(json!(13000), adult)).unwrap().villagers[0].activity,
                "sleep"
            );
        }
        assert!(project(&frame(json!(13000), json!([]))).is_none());
        assert!(project(&frame(json!(13000), json!([{"type":"cow"}]))).is_none());
        let child = json!([{"type":"minecraft:villager","is_baby":true}]);
        assert_eq!(
            project(&frame(json!(4000), child.clone()))
                .unwrap()
                .villagers[0]
                .activity,
            "play"
        );
        assert_eq!(
            project(&frame(json!(13000), child.clone()))
                .unwrap()
                .villagers[0]
                .activity,
            "sleep"
        );
        assert_eq!(
            project(&frame(Value::Null, child)).unwrap().villagers[0].activity,
            "unknown"
        );
        assert_eq!(
            project(&frame(json!(4000), json!([{"type":"villager"}])))
                .unwrap()
                .villagers[0]
                .activity,
            "unknown"
        );
    }
}
