//! 選択履歴へ、実際に照合済みの実行結果を引き継ぐための受入試験。
//! executionは今回要求する最小のモデル表示契約。状態・命令照合は既存Stateをそのまま通す。
use super::*;
use crate::body::autonomy::contracts::{ActionResult, Attention, AttentionScope, Outcome};
use crate::body::contracts::Position;
use crate::events::AdapterCommandResultCommandType;
use std::collections::BTreeMap;

const ENTITY: &str = "22222222-2222-4222-8222-222222222222";
const OTHER: &str = "99999999-9999-4999-8999-999999999999";
fn utc() -> DateTime<Utc> {
    "2026-10-28T00:00:00Z".parse().unwrap()
}
pub(super) fn sample(tick: u64) -> Sample {
    let mut value = crate::body::tests::fixture()["sample"].clone();
    value["server_tick"] = json!(tick);
    value["snapshot_revision"] = json!(tick * 2);
    value["player_pose"]["position"] = json!({"x":0,"y":64,"z":0});
    serde_json::from_value(value).unwrap()
}
pub(super) fn observation(sample: &Sample) -> Observation {
    Observation::Observed(Box::new(Observed {
        version: 2,
        context: sample.context.clone(),
        server_tick: sample.server_tick,
        snapshot_revision: sample.snapshot_revision,
        age_ms: 0,
        enabled: true,
        mode: Mode::Follow,
        follow_distance: 16.0,
        warp_distance: 24.0,
        reassurance: Reassurance {
            value: 50.0,
            owner_distance: 2.0,
            owner_visible: true,
            face_visible: true,
            owner_working: false,
        },
        attention: Attention {
            scope: AttentionScope::Ambient,
            expansion: 1.0,
        },
        activity: Activity::Holding,
        reason: "follow_wait".into(),
        candidates: vec![Candidate {
            id: format!("e:{ENTITY}"),
            kind: CandidateKind::Entity,
            type_id: "minecraft:cow".into(),
            label: "ウシ".into(),
            position: Position {
                x: 2.0,
                y: 64.0,
                z: 0.0,
            },
            entity_id: Some(ENTITY.into()),
        }],
        action_result: None,
    }))
}
fn refresh(state: &mut State, tick: u64, now: Instant) {
    let sample = sample(tick);
    state.observe(Some(&observation(&sample)), Some(&sample), now, Some(0));
}
fn issue(at: Instant) -> (State, Value) {
    let mut state = State::default();
    refresh(&mut state, 1, at);
    let prepared = state.prepare(at).unwrap();
    let command = state
        .choose(
            &prepared,
            r#"{"action":"inspect","target_id":"c1","reason":"ウシを見たい"}"#,
            at,
            utc(),
        )
        .unwrap()
        .unwrap();
    (state, command)
}
fn result(
    command: &Value,
    status: AdapterCommandResultStatus,
    detail: &str,
) -> AdapterCommandResult {
    AdapterCommandResult {
        body_view: None,
        command_id: command["command_id"].as_str().unwrap().into(),
        command_type: AdapterCommandResultCommandType::InspectBodyTarget,
        status,
        executed_at: (utc() + UtcDuration::seconds(1)).fixed_offset(),
        selected_slot: None,
        selected_item_id: None,
        detail_code: detail.into(),
        extra: BTreeMap::from([
            ("body_id".into(), command["context"]["body_id"].clone()),
            ("epoch".into(), command["context"]["epoch"].clone()),
        ]),
    }
}
fn next_choices(state: &mut State, at: Instant) -> Value {
    refresh(state, 2, at + Duration::from_secs(3));
    state
        .prepare(at + Duration::from_secs(3))
        .expect("終端後の次の環境判断")
        .recent_choices
}

// ウシを見に行く命令へ実到着の成功結果が来る。次の選択材料へ選んだ対象・成功・実行時刻を一緒に渡す。
#[test]
fn acceptance_body_results_success_reaches_next_choice_as_observed_result() {
    let at = Instant::now();
    let (mut state, command) = issue(at);
    let report = result(&command, AdapterCommandResultStatus::Succeeded, "arrived");
    assert_eq!(
        state
            .observe_results(
                &[report.clone()],
                at + Duration::from_secs(1),
                utc() + UtcDuration::seconds(1)
            )
            .len(),
        1
    );
    let choices = next_choices(&mut state, at);
    assert_eq!(choices[0]["action"], "inspect");
    assert_eq!(choices[0]["label"], "ウシ");
    assert_eq!(
        choices[0]["execution"]["status"], "succeeded",
        "選んだ事実に実到着の結果を結び付ける"
    );
    assert_eq!(choices[0]["execution"]["detail_code"], "arrived");
    assert_eq!(
        choices[0]["execution"]["executed_at"],
        json!(report.executed_at)
    );
}

// ウシを見に行く命令が経路不成立で失敗する。次の選択材料は失敗理由と時刻を持ち、到着としない。
#[test]
fn acceptance_body_results_failure_reaches_next_choice_without_claiming_arrival() {
    let at = Instant::now();
    let (mut state, command) = issue(at);
    let report = result(
        &command,
        AdapterCommandResultStatus::Failed,
        "path_unavailable",
    );
    assert_eq!(
        state
            .observe_results(
                &[report.clone()],
                at + Duration::from_secs(1),
                utc() + UtcDuration::seconds(1)
            )
            .len(),
        1
    );
    let choices = next_choices(&mut state, at);
    assert_eq!(
        choices[0]["execution"]["status"], "failed",
        "次の判断で到着失敗を参照できる"
    );
    assert_eq!(choices[0]["execution"]["detail_code"], "path_unavailable");
    assert_eq!(
        choices[0]["execution"]["executed_at"],
        json!(report.executed_at)
    );
    assert!(!choices.to_string().contains("arrived"));
}

// 命令を取り消した後で遅れた成功が来る。次の判断には取消が残り、成功へ変わらない。
#[test]
fn acceptance_body_results_cancel_is_carried_forward_and_late_success_cannot_overwrite_it() {
    let at = Instant::now();
    let (mut state, command) = issue(at);
    state.cancel();
    assert!(
        state
            .observe_results(
                &[result(
                    &command,
                    AdapterCommandResultStatus::Succeeded,
                    "arrived"
                )],
                at + Duration::from_secs(1),
                utc() + UtcDuration::seconds(1)
            )
            .is_empty()
    );
    let choices = next_choices(&mut state, at);
    assert_eq!(
        choices[0]["execution"]["status"], "cancelled",
        "取消した選択を単なる選択済みで残さない"
    );
    assert!(!choices.to_string().contains("arrived"));
}

// 命令受付だけが観測される。結果待ちを保ち、履歴にもpendingを表示して到着へ昇格させない。
#[test]
fn acceptance_body_results_waiting_and_adapter_acceptance_do_not_mean_completion() {
    let at = Instant::now();
    let (mut state, command) = issue(at);
    let next = sample(2);
    let mut observed = observation(&next);
    let Observation::Observed(data) = &mut observed else {
        unreachable!()
    };
    data.action_result = Some(ActionResult {
        action_id: command["command_id"].as_str().unwrap().into(),
        sequence: 1,
        outcome: Outcome::Accepted,
        reason: "started".into(),
    });
    state.observe(
        Some(&observed),
        Some(&next),
        at + Duration::from_secs(1),
        Some(0),
    );
    assert!(
        state.prepare(at + Duration::from_secs(1)).is_none(),
        "受付だけで前の移動は完了しない"
    );
    assert_eq!(
        state
            .commands(at + Duration::from_secs(1), utc() + UtcDuration::seconds(1))
            .len(),
        1
    );
    assert_eq!(
        state.choices.back().unwrap()["execution"]["status"],
        "pending",
        "次の判断へ渡す選択記録でも未完了を明示する"
    );
}

// 別命令ID・別身体世代の成功が届く。未完了を維持し、正しい結果だけを一度受け取る。
#[test]
fn acceptance_body_results_wrong_command_or_epoch_cannot_complete_choice_control() {
    let at = Instant::now();
    let (mut state, command) = issue(at);
    let good = result(&command, AdapterCommandResultStatus::Succeeded, "arrived");
    for wrong in ["id", "epoch"] {
        let mut report = good.clone();
        if wrong == "id" {
            report.command_id = OTHER.into();
        } else {
            report.extra.insert("epoch".into(), json!(OTHER));
        }
        assert!(
            state
                .observe_results(
                    &[report],
                    at + Duration::from_secs(1),
                    utc() + UtcDuration::seconds(1)
                )
                .is_empty()
        );
        assert!(state.pending.is_some());
        assert!(state.prepare(at + Duration::from_secs(1)).is_none());
    }
    assert_eq!(
        state
            .observe_results(
                &[good.clone()],
                at + Duration::from_secs(1),
                utc() + UtcDuration::seconds(1)
            )
            .len(),
        1
    );
    assert!(
        state
            .observe_results(
                &[good],
                at + Duration::from_secs(1),
                utc() + UtcDuration::seconds(1)
            )
            .is_empty()
    );
}

// 10回の選択後に身体世代が変わる。履歴は8件以内で、新世代へ旧選択を引き継がない。
#[test]
fn acceptance_body_results_history_stays_bounded_and_old_generation_cannot_leak_control() {
    let at = Instant::now();
    let mut state = State::default();
    for i in 0..10 {
        let time = at + Duration::from_secs(i * 4);
        refresh(&mut state, i + 1, time);
        let prepared = state.prepare(time).unwrap();
        assert_eq!(
            prepared.recent_choices.as_array().unwrap().len(),
            (i as usize).min(8)
        );
        assert!(
            state
                .choose(
                    &prepared,
                    r#"{"action":"stay","target_id":null,"reason":"待つ"}"#,
                    time,
                    utc() + UtcDuration::seconds(i as i64 * 4)
                )
                .unwrap()
                .is_none()
        );
    }
    let mut next = sample(20);
    next.context.epoch = OTHER.into();
    state.observe(
        Some(&observation(&next)),
        Some(&next),
        at + Duration::from_secs(40),
        Some(0),
    );
    assert_eq!(
        state
            .prepare(at + Duration::from_secs(40))
            .unwrap()
            .recent_choices,
        json!([])
    );
}
