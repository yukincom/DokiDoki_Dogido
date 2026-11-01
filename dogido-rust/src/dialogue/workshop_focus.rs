//! 句の相談中は環境反応へ脱線せず、敵の観測に基づく中断条件を適用する。
use super::{Session, observation_fresh};
use crate::events::{EventName, GameEvent};

pub(super) fn active(s: &Session) -> bool {
    s.haiku.workshop.as_ref().is_some_and(|w| w.open)
}

pub(super) fn engaged(e: &GameEvent) -> bool {
    e.combat.player_engaged == Some(true)
}

/// 暗さや敵の根拠がない被ダメージを、敵接近と取り違えない。
/// 死亡・観測失効の停止と、既に始まった戦闘からの復帰は別に維持する。
pub(super) fn quiet(s: &Session) -> bool {
    active(s) && quiet_observation(s)
}

pub(super) fn quiet_observation(s: &Session) -> bool {
    observation_fresh(s)
        && s.latest.as_ref().is_some_and(|e| {
            !engaged(e)
                && e.event.name != EventName::PlayerDied
                && e.player.health.is_none_or(|h| h > 0.0)
        })
        && s.audio_latest.as_ref().is_none_or(|e| !engaged(e))
}

pub(super) fn owns_input(s: &Session) -> bool {
    quiet(s)
        && s.haiku
            .workshop
            .as_ref()
            .is_some_and(|w| !w.combat_paused())
}

pub(super) fn combat_completion(kind: &str) -> bool {
    matches!(
        kind,
        "hostile_defeated" | "creeper_detonated" | "aftermath" | "death"
    )
}

#[cfg(test)]
mod tests {
    use super::super::{empty_event, workshop_record::tests::fixture};
    use crate::events::GameEvent;
    use serde_json::json;

    // 模擬Fabric→川柳受付。視認・鳴き声・背後接近・点火だけでは相談を止めず、本人の交戦で止める。
    // 合格: 交戦通知まで相談は開いたまま。視覚・音が空でも交戦通知で中断する。実機のAIは対象外。
    #[tokio::test]
    async fn only_player_engagement_pauses_the_open_workshop() {
        let f = fixture();
        let mut frame = empty_event("試験");
        f.d.data
            .lock()
            .unwrap()
            .sessions
            .get_mut("s")
            .unwrap()
            .preview = false;
        for (index, (visual, heard, engaged)) in [
            (json!([{"type":"zombie","distance":2}]), false, false),
            (json!([]), true, false),
            (json!([{"type":"creeper","entity_id":"c","distance":2,"approaching":true,
                "direction":{"horizontal":"back"}}]), false, false),
            (json!([{"type":"creeper","entity_id":"c","distance":2,"fuse_active":true}]), false, false),
            (json!([]), false, true),
        ]
        .into_iter()
        .enumerate()
        {
            frame["sequence"] = json!(index + 1);
            frame["observed_at"] = json!(chrono::Utc::now());
            frame["visual_threats"] = visual;
            frame["auditory_threats"] = if heard {
                json!([{"label":"zombie","source_id":"z"}])
            } else {
                json!([])
            };
            frame["combat"] = json!({"combat_active_hint":true,"player_engaged":engaged});
            f.d.observe("s", GameEvent::parse(frame.clone()).unwrap(), None);
            let data = f.d.data.lock().unwrap();
            let s = &data.sessions["s"];
            let w = s.haiku.workshop.as_ref().unwrap();
            assert!(w.open);
            assert_eq!(w.combat_paused(), engaged);
            assert_eq!(super::owns_input(s), !engaged);
        }
        f.finish().await;
    }
}
