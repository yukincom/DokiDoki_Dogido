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
fn classified(
    text: &str,
    label: crate::request_classifier::Label,
    source: &str,
) -> crate::request_classifier::Classification {
    let mut probabilities = [0.0; crate::request_classifier::LABEL_COUNT];
    let index = crate::request_classifier::LABELS
        .iter()
        .position(|candidate| *candidate == label)
        .unwrap();
    probabilities[index] = 1.0;
    crate::request_classifier::Classification {
        text: text.into(),
        source: source.into(),
        manifest_sha256: "fixture-bert23".into(),
        prediction: crate::request_classifier::Prediction {
            label,
            probabilities,
            token_count: 8,
            inference_ms: 0.1,
        },
    }
}
fn sword(text: &str) -> crate::request_classifier::Classification {
    classified(text, crate::request_classifier::Label::EquipSword, "text")
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
fn bert_request_result_owns_request_intent_and_preserves_original_text() {
    // BERTへ変更済み。旧判定へ戻さない。原文語句で分類結果を再判定しない。
    for (text, source) in [
        ("剣", "text"),
        ("そっちの武器に替えて", "text"),
        ("県を変えて", "voice"),
    ] {
        let classification = classified(text, crate::request_classifier::Label::EquipSword, source);
        let d = dispatch(state().submit_classified(&classification, &event(), now(), false));
        assert_eq!(d.intent.source, "modernbert");
        assert_eq!(d.intent.evidence, text);
        assert!(d.command.is_some());
    }
}

#[test]
fn all_other_bert_labels_leave_sword_execution_unhandled() {
    for label in crate::request_classifier::LABELS {
        if label == crate::request_classifier::Label::EquipSword {
            continue;
        }
        let mut state = state();
        assert!(matches!(
            state.submit_classified(
                &classified("剣に装備して", label, "text"),
                &event(),
                now(),
                false
            ),
            Submission::NotHandled
        ));
        assert!(state.commands(now()).is_empty(), "{label:?}");
    }
}

#[test]
fn pending_retransmission_success_cooldown_and_duplicate_ack() {
    let mut s = state();
    let t = now();
    let c = dispatch(s.submit_classified(&sword("剣"), &event(), t, false))
        .command
        .unwrap();
    assert_eq!(s.pending_commands(t)[0].command_id, c.command_id);
    assert!(
        dispatch(s.submit_classified(
            &sword("剣"),
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
        dispatch(s.submit_classified(&sword("剣"), &event(), t + Duration::seconds(2), false))
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
        dispatch(s.submit_classified(&sword("剣"), &event(), t + Duration::seconds(3), false))
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
        let c = dispatch(s.submit_classified(&sword("剣"), &event(), now(), false))
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
            dispatch(s.submit_classified(
                &sword("剣"),
                &event(),
                now() + Duration::seconds(2),
                true
            ))
            .command
            .is_some()
        );
    }
    let mut s = state();
    let c = dispatch(s.submit_classified(&sword("剣"), &event(), now(), false))
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
    let c = dispatch(s.submit_classified(&sword("剣"), &event(), now(), false))
        .command
        .unwrap();
    let new =
        dispatch(s.submit_classified(&sword("剣"), &event(), now() + Duration::seconds(2), false));
    assert!(new.command.is_some());
    assert_ne!(new.feedback.as_deref(), Some("もう持ち替えたで！"));
    let out = s.observe_results(&[], now() + Duration::seconds(2), false);
    assert_eq!(out.observed.len(), 1);
    assert_eq!(out.observed[0].result.command_id, c.command_id);
    assert_eq!(out.observed[0].result.detail_code, "server_result_timeout");
}
#[test]
fn classified_submission_retains_pending_command_deduplication() {
    let mut s = state();
    let classification = sword("そっちへ替えて");
    let first = dispatch(s.submit_classified(&classification, &event(), now(), false))
        .command
        .unwrap();
    let duplicate = dispatch(s.submit_classified(&classification, &event(), now(), false));
    assert_eq!(duplicate.status, "pending");
    assert!(duplicate.command.is_none());
    assert_eq!(s.commands(now()).len(), 1);
    assert_eq!(s.commands(now())[0]["command_id"], first.command_id);
}

#[test]
fn bert_probability_is_diagnostic_without_an_extra_threshold() {
    let mut classification = sword("剣に替えて");
    classification.prediction.probabilities = [0.8 / 22.0; crate::request_classifier::LABEL_COUNT];
    classification.prediction.probabilities[2] = 0.2;
    let d = dispatch(state().submit_classified(&classification, &event(), now(), false));
    assert!(d.command.is_some());
    assert_eq!(d.intent.confidence, 0.2);
}

#[test]
fn capability_and_current_snapshot_selection() {
    let classification = sword("剣に替えて");
    let d =
        dispatch(AssistState::new([]).submit_classified(&classification, &event(), now(), false));
    assert!(d.command.is_none());
    assert_eq!(d.detail_code, "capability_missing");
    let mut value = serde_json::to_value(event()).unwrap();
    value["player"]["hotbar"]["slots"] = json!([]);
    let e = GameEvent::parse(value).unwrap();
    let d = dispatch(state().submit_classified(&classification, &e, now(), false));
    assert!(d.command.is_none());
    assert_eq!(d.detail_code, "no_candidate");
}

#[test]
fn classified_commands_and_result_ownership_remain_per_session() {
    let mut first = state();
    let mut other = state();
    let first_command = dispatch(first.submit_classified(&sword("剣"), &event(), now(), false))
        .command
        .unwrap();
    let other_command = dispatch(other.submit_classified(&sword("剣"), &event(), now(), false))
        .command
        .unwrap();
    assert_ne!(first_command.command_id, other_command.command_id);
    let report = result(&first_command, now(), "succeeded", first_command.slot);
    let out = other.observe_results(&[report], now(), false);
    assert!(!out.observed[0].known_command);
    assert_eq!(
        other.pending_commands(now())[0].command_id,
        other_command.command_id
    );
}

#[test]
fn naive_result_time_fails_closed_and_policy_never_issues() {
    let mut s = state();
    let c = dispatch(s.submit_classified(&sword("剣"), &event(), now(), false))
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
            dispatch(s.submit_classified(&sword("剣"), &event(), now(), false))
                .command
                .is_none()
        );
    }
}
