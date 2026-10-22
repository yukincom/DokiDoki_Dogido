//! 受理済み身体観測の位置・向き・視認を、会話の既存差分台帳へ追加する。
//! 種類ごとに直近の変化一件だけを保持し、通知機会や会話履歴は変更しない。
use super::{Change, Context};
use serde_json::{Value, json};

const KINDS: [&str; 6] = [
    "body_self_position",
    "body_player_position",
    "body_self_orientation",
    "body_player_orientation",
    "body_player_visibility",
    "body_position_relation",
];

fn fields(current: &Value) -> [Value; 6] {
    [
        current["self"]["position"].clone(),
        current["player"]["position"].clone(),
        json!({"yaw":current["self"]["yaw"],"view_yaw":current["self"]["view_yaw"],
            "view_pitch":current["self"]["view_pitch"]}),
        json!({"yaw":current["player"]["yaw"],"pitch":current["player"]["pitch"]}),
        json!({"player_visibility":current["vision"]["player_visibility"],
            "reason":current["vision"]["reason"]}),
        current["position_relation"].clone(),
    ]
}

/// 同じ身体の連続した受理済み観測を比較する。座標はblock、角度はdegreeの実測値。
/// 初回・失効・再取得では比較を再開するだけにし、途切れた前後の移動を捏造しない。
pub(super) fn append(previous: &Value, next: &mut Context) {
    let before = &previous["body_observation"];
    let now = &next.observations["body_observation"];
    if before["status"] != "observed" || now["status"] != "observed" || !now["previous"].is_object()
    {
        next.changes.retain(|c| !KINDS.contains(&c.kind.as_str()));
        return;
    }
    for ((kind, before), now) in KINDS
        .into_iter()
        .zip(fields(&before["current"]))
        .zip(fields(&now["current"]))
    {
        if before == now {
            continue;
        }
        next.revision = next.revision.saturating_add(1);
        next.changes.retain(|c| c.kind != kind);
        next.changes.push(Change {
            kind: kind.into(),
            before,
            now,
            observed_at: next.observations["observed_at"]
                .as_str()
                .unwrap_or("")
                .into(),
            revision: next.revision,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::conversation_observation::State;

    fn frame(n: i64, visible: &str) -> Context {
        let mut c = Context {
            observations: json!({"observed_at":"now","dimension":"overworld"}),
            ..Context::default()
        };
        c.observations["body_observation"] = json!({"status":"observed","previous":{},
            "current":{"self":{"position":{"x":n,"y":64,"z":0},"yaw":n,"view_yaw":n,"view_pitch":0},
            "player":{"position":{"x":n+2,"y":64,"z":0},"yaw":-n,"pitch":0},
            "vision":{"player_visibility":visible,"reason":if visible=="visible" {"in_view"} else {"outside_fov"}}}});
        c
    }

    #[test]
    fn changes_are_bounded_survive_stationary_frames_and_ack_only_offered_revision() {
        let mut s = State::default();
        s.observe(frame(0, "visible"));
        assert!(s.context().unwrap().changes.is_empty());
        for n in 1..100 {
            s.observe(frame(n, if n % 2 == 0 { "visible" } else { "not_visible" }));
            assert_eq!(s.context().unwrap().changes.len(), 5);
        }
        let offered = s.context().unwrap();
        s.observe(frame(99, "not_visible"));
        assert_eq!(s.context().unwrap(), offered);
        assert_eq!(offered.changes[0].before["x"], 98);
        assert_eq!(offered.changes[0].now["x"], 99);
        s.observe(frame(100, "visible"));
        s.considered(offered.revision);
        let latest = s.context().unwrap();
        assert_eq!(latest.changes.len(), 5);
        s.considered(latest.revision);
        assert!(s.context().unwrap().changes.is_empty());
        assert_eq!(
            s.context().unwrap().observations,
            frame(100, "visible").observations
        );
    }

    #[test]
    fn observation_gap_clears_pending_changes_and_restarts_baseline() {
        let mut s = State::default();
        s.observe(frame(0, "visible"));
        s.observe(frame(1, "not_visible"));
        let mut resumed = frame(20, "visible");
        resumed.observations["body_observation"]["previous"] = Value::Null;
        s.observe(resumed);
        assert!(s.context().unwrap().changes.is_empty());
        s.observe(frame(21, "not_visible"));
        assert_eq!(s.context().unwrap().changes[0].before["x"], 20);
        let mut unavailable = frame(99, "visible");
        unavailable.observations["body_observation"] =
            json!({"status":"unavailable","reason":"stale"});
        s.observe(unavailable);
        assert!(s.context().unwrap().changes.is_empty());
        s.observe(frame(22, "visible"));
        assert!(s.context().unwrap().changes.is_empty());
    }

    #[test]
    fn unknown_vision_keeps_pose_changes_and_reason_only_changes_are_reported() {
        let mut s = State::default();
        s.observe(frame(0, "visible"));
        let mut unavailable = frame(1, "unavailable");
        unavailable.observations["body_observation"]["current"]["vision"]["reason"] =
            json!("area_unloaded");
        s.observe(unavailable);
        let c = s.context().unwrap();
        assert_eq!(c.changes.len(), 5);
        assert_eq!(c.changes[4].now["player_visibility"], "unavailable");
        s.considered(c.revision);
        s.observe(frame(1, "not_visible"));
        let c = s.context().unwrap();
        assert_eq!(c.changes.len(), 1);
        s.considered(c.revision);
        let mut occluded = frame(1, "not_visible");
        occluded.observations["body_observation"]["current"]["vision"]["reason"] =
            json!("occluded");
        s.observe(occluded);
        let c = s.context().unwrap();
        assert_eq!(c.changes.len(), 1);
        assert_eq!(c.changes[0].before["reason"], "outside_fov");
        assert_eq!(c.changes[0].now["reason"], "occluded");
    }

    #[test]
    fn body_changes_alone_do_not_create_a_periodic_speech_opportunity() {
        let mut s = State::default();
        s.observe(frame(0, "visible"));
        s.sample_notice(0, 100, true);
        s.observe(frame(10, "not_visible"));
        s.sample_notice(100, 100, true);
        assert!(s.noticing_context().is_none());
        assert_eq!(s.context().unwrap().changes.len(), 5);
    }
}
