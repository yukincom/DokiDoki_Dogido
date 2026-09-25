use super::*;
use crate::events::{AdapterCommandResult, GameEvent};
use chrono::{DateTime, Duration, Utc};
use serde_json::json;
fn now() -> DateTime<Utc> {
    DateTime::parse_from_rfc3339("2026-09-25T12:00:00Z")
        .unwrap()
        .with_timezone(&Utc)
}
fn event() -> GameEvent {
    GameEvent::parse(json!({"schema_version":"2026-05-24","game":"minecraft-java","adapter":"test","sequence":1,"observed_at":now(),"event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"player":{"name":"test","position":{"x":0,"y":64,"z":0},"dimension":"minecraft:overworld","hotbar":{"selected_slot":8,"slots":[{"slot":0,"item_id":"minecraft:stone_sword","count":1,"damage":0,"max_damage":131,"attack_damage":5,"weapon_kind":"sword"}]}},"world":{"time_phase":"day","weather":"clear","biome":"plains","local_light":15,"sky_visible":true,"danger_darkness_score":0},"combat":{"combat_active_hint":false}})).unwrap()
}
fn input(text: &str) -> Input {
    Input {
        raw_text: text.into(),
        ..Input::default()
    }
}
fn state() -> AssistState {
    AssistState::new([SELECT_HOTBAR_CAPABILITY.into()])
}
fn dispatch(s: Submission) -> Dispatch {
    match s {
        Submission::Handled(d) => d,
        _ => panic!("handled expected"),
    }
}
fn result(
    command: &SelectHotbarCommand,
    at: DateTime<Utc>,
    status: &str,
    slot: i64,
) -> AdapterCommandResult {
    serde_json::from_value(json!({"command_id":command.command_id,"command_type":"select_hotbar","status":status,"executed_at":at,"selected_slot":slot,"selected_item_id":command.expected_item_id})).unwrap()
}
#[test]
fn explicit_negative_quoted_and_voice_boundaries() {
    for text in [
        "剣",
        "ドギド、剣に持ち替えてください",
        "斧から剣に装備して",
        "剣を変更してください",
    ] {
        assert!(intent::is_explicit_select_sword_request(text), "{text}");
    }
    for text in [
        "けん",
        "県を変えて",
        "明日は剣にして",
        "剣にしてとは言ってない",
        "剣にしてほしくない",
        "「剣にして」",
        "剣にしたんだけど",
        "剣を作って",
        "剣の耐久値は？",
    ] {
        assert!(!intent::is_explicit_select_sword_request(text), "{text}");
    }
    assert_eq!(
        intent::interpret_voice_select_sword_request("時と県に変えて"),
        Some("時と剣に変えて".into())
    );
    assert!(intent::interpret_voice_select_sword_request("県を変えてという意味ではない").is_none());
    assert!(matches!(
        state().submit(input("県を変えて"), &event(), now(), false),
        Submission::NotHandled
    ));
    let d = dispatch(state().submit(
        Input {
            source: InputSource::Voice,
            ..input("県を変えて")
        },
        &event(),
        now(),
        false,
    ));
    assert_eq!(d.intent.source, "code_voice_asr");
    assert!(d.command.is_some());
}
#[test]
fn workshop_and_knowledge_ownership() {
    let mut s = state();
    for text in ["剣", "剣にして", "剣の方がよくない？"] {
        assert!(matches!(
            s.submit(
                Input {
                    workshop_open: true,
                    ..input(text)
                },
                &event(),
                now(),
                false
            ),
            Submission::NotHandled
        ));
    }
    assert!(
        dispatch(s.submit(
            Input {
                workshop_open: true,
                ..input("剣に装備して")
            },
            &event(),
            now(),
            true
        ))
        .command
        .is_some()
    );
    assert!(matches!(
        s.submit(
            Input {
                knowledge_query: true,
                ..input("剣に装備して")
            },
            &event(),
            now(),
            false
        ),
        Submission::NotHandled
    ));
}
#[test]
fn pending_retransmission_success_cooldown_and_duplicate_ack() {
    let mut s = state();
    let t = now();
    let c = dispatch(s.submit(input("剣"), &event(), t, false))
        .command
        .unwrap();
    assert_eq!(s.pending_commands(t)[0].command_id, c.command_id);
    assert!(
        dispatch(s.submit(
            input("剣"),
            &event(),
            t + Duration::milliseconds(100),
            false
        ))
        .command
        .is_none()
    );
    let r = result(&c, t + Duration::milliseconds(500), "succeeded", c.slot);
    let out = s.observe_results(&[r.clone(), r.clone()], t + Duration::seconds(1), false);
    assert_eq!(out.acknowledged_ids.len(), 1);
    assert_eq!(out.observed.len(), 1);
    assert!(out.feedback.is_none());
    assert!(s.pending_commands(t).is_empty());
    assert_eq!(
        dispatch(s.submit(input("剣"), &event(), t + Duration::seconds(2), false))
            .feedback
            .as_deref(),
        Some("もう持ち替えたで！")
    );
    assert!(
        s.observe_results(&[r], t + Duration::seconds(2), false)
            .observed
            .is_empty()
    );
    assert!(
        dispatch(s.submit(input("剣"), &event(), t + Duration::seconds(3), false))
            .command
            .is_some()
    );
}
#[test]
fn deadline_mismatch_and_unknown_results() {
    for (dt, slot, detail) in [
        (Duration::seconds(2), 0, "result_outside_validity_window"),
        (Duration::seconds(-1), 0, "result_outside_validity_window"),
        (Duration::seconds(1), 1, "result_mismatch"),
    ] {
        let mut s = state();
        let c = dispatch(s.submit(input("剣"), &event(), now(), false))
            .command
            .unwrap();
        let out = s.observe_results(
            &[result(&c, now() + dt, "succeeded", slot)],
            now() + Duration::seconds(2),
            false,
        );
        assert_eq!(out.observed[0].result.detail_code, detail);
        assert!(out.feedback.is_some());
        assert!(
            dispatch(s.submit(input("剣"), &event(), now() + Duration::seconds(2), true))
                .command
                .is_some()
        );
    }
    let mut s = state();
    let c = dispatch(s.submit(input("剣"), &event(), now(), false))
        .command
        .unwrap();
    let mut r = result(&c, now(), "failed", 0);
    r.command_id = "unknown".into();
    let out = s.observe_results(&[r], now(), false);
    assert_eq!(out.acknowledged_ids, vec!["unknown"]);
    assert!(!out.observed[0].known_command);
    assert!(out.feedback.is_none());
    assert_eq!(s.pending_commands(now()).len(), 1);
}
#[test]
fn submit_expiry_does_not_claim_success_and_keeps_result() {
    let mut s = state();
    let c = dispatch(s.submit(input("剣"), &event(), now(), false))
        .command
        .unwrap();
    let new = dispatch(s.submit(input("剣"), &event(), now() + Duration::seconds(2), false));
    assert!(new.command.is_some());
    assert_ne!(new.feedback.as_deref(), Some("もう持ち替えたで！"));
    let out = s.observe_results(&[], now() + Duration::seconds(2), false);
    assert_eq!(out.observed.len(), 1);
    assert_eq!(out.observed[0].result.command_id, c.command_id);
    assert_eq!(out.observed[0].result.detail_code, "server_result_timeout");
}
#[test]
fn fresh_completion_and_single_use_token() {
    let mut s = state();
    let i = input("剣の方がよくない？");
    let Submission::NeedsIntent(p) = s.submit(i.clone(), &event(), now(), false) else {
        panic!()
    };
    let payload = json!({"intent":"select_weapon","weapon_kind":"sword","is_request":true,"evidence":i.raw_text,"confidence":0.99});
    assert!(intent::contract_errors(&payload).is_empty());
    let again = p.clone();
    let d = dispatch(s.complete_intent(p, &payload, &i, &event(), now(), false));
    assert!(d.command.is_some());
    assert!(matches!(
        s.complete_intent(again, &payload, &i, &event(), now(), false),
        Submission::NotHandled
    ));
    let Submission::NeedsIntent(p) = s.submit(i.clone(), &event(), now(), false) else {
        panic!()
    };
    s.submit(input("やめて"), &event(), now(), false);
    assert!(matches!(
        s.complete_intent(p, &payload, &i, &event(), now(), false),
        Submission::NotHandled
    ));
}
#[test]
fn natural_payload_checks_whole_utterance_and_strict_shape() {
    for text in ["剣の方がよくない？", "ここは剣の方がいいん？"] {
        let p = json!({"intent":"select_weapon","weapon_kind":"sword","is_request":true,"evidence":text,"confidence":0.9});
        assert!(intent::validate_payload(&p, text, 0.9).is_some());
    }
    let mut p = json!({"intent":"select_weapon","weapon_kind":"sword","is_request":true,"evidence":"剣にして","confidence":0.99});
    for t in [
        "剣にしてという意味ではない",
        "剣にしてと言った",
        "「剣にして」",
        "剣にした",
    ] {
        assert!(intent::validate_payload(&p, t, 0.9).is_none());
    }
    p["confidence"] = json!(true);
    assert!(intent::validate_payload(&p, "剣にして", 0.9).is_none());
    assert!(!intent::contract_errors(&p).is_empty());
    p["confidence"] = json!("0.99");
    assert!(!intent::contract_errors(&p).is_empty());
}
#[test]
fn capability_and_current_snapshot_selection() {
    let d = dispatch(AssistState::new([]).submit(input("剣"), &event(), now(), false));
    assert!(d.command.is_none());
    assert_eq!(d.detail_code, "capability_missing");
    let mut s = state();
    let i = input("剣の方がいい？");
    let Submission::NeedsIntent(p) = s.submit(i.clone(), &event(), now(), false) else {
        panic!()
    };
    let mut value = serde_json::to_value(event()).unwrap();
    value["player"]["hotbar"]["slots"] = json!([]);
    let e = GameEvent::parse(value).unwrap();
    let payload = json!({"intent":"select_weapon","weapon_kind":"sword","is_request":true,"evidence":i.raw_text,"confidence":1.0});
    let d = dispatch(s.complete_intent(p, &payload, &i, &e, now(), false));
    assert!(d.command.is_none());
    assert_eq!(d.detail_code, "no_candidate");
}
#[test]
fn intent_is_bound_to_session_and_current_owner() {
    let mut first = state();
    let mut other = state();
    let i = input("剣の方がいい？");
    let Submission::NeedsIntent(p) = first.submit(i.clone(), &event(), now(), false) else {
        panic!()
    };
    other.submit(i.clone(), &event(), now(), false);
    let payload = json!({"intent":"select_weapon","weapon_kind":"sword","is_request":true,"evidence":i.raw_text,"confidence":1.0});
    assert!(matches!(
        other.complete_intent(p.clone(), &payload, &i, &event(), now(), false),
        Submission::NotHandled
    ));
    let changed = Input {
        workshop_open: true,
        ..i
    };
    assert!(matches!(
        first.complete_intent(p, &payload, &changed, &event(), now(), false),
        Submission::NotHandled
    ));
}
#[test]
fn naive_result_time_fails_closed_and_policy_never_issues() {
    let mut s = state();
    let c = dispatch(s.submit(input("剣"), &event(), now(), false))
        .command
        .unwrap();
    let mut r = result(&c, now(), "succeeded", c.slot);
    r.executed_at = crate::events::EventTime::Naive(now().naive_utc());
    let out = s.observe_results(&[r], now(), false);
    assert_eq!(
        out.observed[0].result.detail_code,
        "result_outside_validity_window"
    );
    for policy in [RiskPolicy::Deny, RiskPolicy::Confirm] {
        let mut s = state();
        s.set_policy(policy);
        assert!(
            dispatch(s.submit(input("剣"), &event(), now(), false))
                .command
                .is_none()
        );
    }
}
