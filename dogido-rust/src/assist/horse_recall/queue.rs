//! 検証済みの対象を期限付き呼び戻し命令へ変え、同じ個体の実行結果だけを受理する。
//! 名前と依頼の解釈は会話側、共有ACK台帳はAssistState、実際の移動はFabricが担当する。
use super::contracts::{Choice, Prepared, Snapshot};
use crate::events::{
    AdapterCommandResult, AdapterCommandResultCommandType, AdapterCommandResultStatus, EventTime,
};
use anyhow::{Result, ensure};
use chrono::{DateTime, Duration, Utc};
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;

// 実行期限後の配送停止と結果待ちを分け、期限内の移動を次の観測で受け取れるようにする。
const RESULT_GRACE_SECONDS: i64 = 5;

/// UUIDはモデルの返却値ではなく、現在も一致する候補表からコードが確定する。
/// 発行から5秒だけ有効。同じIDの再配送ではFabric側が移動を重複実行しない。
#[derive(Clone, Debug, Serialize)]
pub struct Command {
    pub command_id: String,
    #[serde(rename = "type")]
    pub command_type: &'static str,
    pub issued_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub world_id: String,
    pub player_uuid: String,
    pub dimension: String,
    pub entity_id: String,
}

/// 馬の命令は一度に一件。剣のpending・成功抑制とは独立し、成功後の追加cooldownは設けない。
#[derive(Clone, Debug, Default)]
pub(crate) struct Queue {
    pending: Option<Command>,
}

impl Queue {
    /// 呼出元が期限切れを共有台帳へ退役させた後、元候補と最新観測を再照合して発行する。
    /// 生成待ちの間の改名・所有変更・乗車・ワールド移動をここで取り違えない。
    pub(crate) fn enqueue(
        &mut self,
        prepared: &Prepared,
        choice: &Choice,
        latest: &Snapshot,
        now: DateTime<Utc>,
    ) -> Result<Command> {
        ensure!(self.pending.is_none(), "horse_command_pending");
        let target = prepared.revalidate(choice, latest, now)?;
        let command = Command {
            command_id: uuid::Uuid::new_v4().to_string(),
            command_type: "recall_horse",
            issued_at: now,
            expires_at: now + Duration::seconds(5),
            world_id: latest.world_id.clone(),
            player_uuid: latest.player_uuid.clone(),
            dimension: latest.dimension.clone(),
            entity_id: target.entity_id.clone(),
        };
        self.pending = Some(command.clone());
        Ok(command)
    }

    /// 発行済みIDだけを消費し、実行期限と受信猶予・種類・個体・接続世代を照合する。
    /// 未知IDはNoneであり、ACKと重複判定は剣と共通のAssistStateに任せる。
    pub(crate) fn observe(
        &mut self,
        incoming: &AdapterCommandResult,
        now: DateTime<Utc>,
    ) -> Option<AdapterCommandResult> {
        if self.pending.as_ref()?.command_id != incoming.command_id {
            return None;
        }
        let command = self.pending.take().expect("matching pending command");
        let mut result = incoming.clone();
        // 発行時の種類を保ち、異なる種別の結果を剣の成功状態や失敗案内へ混ぜない。
        result.command_type = AdapterCommandResultCommandType::RecallHorse;
        if incoming.command_type != AdapterCommandResultCommandType::RecallHorse {
            result.status = AdapterCommandResultStatus::Failed;
            result.detail_code = "result_type_mismatch".into();
        } else if now >= command.expires_at + Duration::seconds(RESULT_GRACE_SECONDS) {
            result.status = AdapterCommandResultStatus::Expired;
            result.detail_code = "server_result_timeout".into();
        } else if result.status == AdapterCommandResultStatus::Succeeded {
            let in_time = match result.executed_at {
                EventTime::Aware(time) => time >= command.issued_at && time < command.expires_at,
                EventTime::Naive(_) => false,
            };
            if !in_time {
                result.status = AdapterCommandResultStatus::Failed;
                result.detail_code = "result_outside_validity_window".into();
            } else if result.extra.get("entity_id").and_then(Value::as_str)
                != Some(command.entity_id.as_str())
                || result.extra.get("world_id").and_then(Value::as_str)
                    != Some(command.world_id.as_str())
            {
                result.status = AdapterCommandResultStatus::Failed;
                result.detail_code = "result_mismatch".into();
            }
        }
        Some(result)
    }

    /// 実行期限から5秒待っても結果が来ない命令を一度だけ退役する。
    /// 受信猶予は再配送や実行の許可を延ばさず、呼出元が同じIDを共有台帳へ登録する。
    pub(crate) fn expire(&mut self, now: DateTime<Utc>) -> Option<AdapterCommandResult> {
        if self.pending.as_ref()?.expires_at + Duration::seconds(RESULT_GRACE_SECONDS) > now {
            return None;
        }
        let command = self.pending.take().expect("expired pending command");
        Some(AdapterCommandResult {
            body_view: None,
            command_id: command.command_id,
            command_type: AdapterCommandResultCommandType::RecallHorse,
            status: AdapterCommandResultStatus::Expired,
            executed_at: EventTime::Aware(now.fixed_offset()),
            selected_slot: None,
            selected_item_id: None,
            detail_code: "server_result_timeout".into(),
            extra: BTreeMap::from([
                ("entity_id".into(), command.entity_id.into()),
                ("world_id".into(), command.world_id.into()),
            ]),
        })
    }

    pub(crate) fn pending(&self, now: DateTime<Utc>) -> Option<&Command> {
        self.pending.as_ref().filter(|c| c.expires_at > now)
    }
}

#[cfg(test)]
mod tests {
    //! 実移動やモデルを使わず、発行から結果ACKまでの境界を検査する。
    use super::*;
    use crate::assist::{AssistState, Input, SELECT_HOTBAR_CAPABILITY, Submission};
    use crate::events::GameEvent;
    use serde_json::json;

    fn now() -> DateTime<Utc> {
        DateTime::parse_from_rfc3339("2026-10-11T00:00:00Z")
            .unwrap()
            .with_timezone(&Utc)
    }
    fn prepared() -> Prepared {
        let snapshot: Snapshot = serde_json::from_value(json!({
            "schema_version":2,"status":"available",
            "world_id":"10000000-0000-0000-0000-000000000001","revision":1,"observed_at":now(),
            "player_uuid":"20000000-0000-0000-0000-000000000001","dimension":"minecraft:overworld",
            "candidates":[{"entity_id":"30000000-0000-0000-0000-000000000001","custom_name":"buchi",
                "tamed":true,"entity_type":"horse","owner_uuid":"20000000-0000-0000-0000-000000000001","adult":true,"equipped":true,"has_vehicle":false,"alive":true,"dimension":"minecraft:overworld",
                "has_passengers":false,"leashed":false}]
        }))
        .unwrap();
        Prepared::new(snapshot, "ぶちを呼んで").unwrap()
    }
    fn choice() -> Choice {
        Choice {
            candidate_id: "h1".into(),
            evidence: "ぶちを呼んで".into(),
            confidence: 0.99,
        }
    }
    fn state() -> AssistState {
        AssistState::new([
            super::super::CAPABILITY.into(),
            SELECT_HOTBAR_CAPABILITY.into(),
        ])
    }
    fn issue(state: &mut AssistState) -> Command {
        let p = prepared();
        state
            .enqueue_horse(&p, &choice(), &p.snapshot, now())
            .unwrap()
    }
    fn result(command: &Command) -> AdapterCommandResult {
        // AdapterCommandResult.extraはflatten。wireにextraオブジェクトは作らない。
        serde_json::from_value(
            json!({"command_id":command.command_id,"command_type":"recall_horse",
            "status":"succeeded","executed_at":now()+Duration::milliseconds(500),
            "entity_id":command.entity_id,"world_id":command.world_id}),
        )
        .unwrap()
    }
    fn event_value() -> Value {
        json!({"schema_version":"2026-05-24","game":"minecraft-java","adapter":"test","sequence":1,
            "observed_at":now(),"event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"name":"test","position":{"x":0,"y":64,"z":0},"dimension":"minecraft:overworld",
                "hotbar":{"selected_slot":8,"slots":[{"slot":0,"item_id":"minecraft:stone_sword","count":1,"damage":0,"max_damage":131,"attack_damage":5,"weapon_kind":"sword"}]}},
            "world":{"time_phase":"day","weather":"clear","biome":"plains","local_light":15,"sky_visible":true,"danger_darkness_score":0},
            "combat":{"combat_active_hint":false}})
    }
    fn sword(state: &mut AssistState, time: DateTime<Utc>) -> crate::assist::Dispatch {
        let input = Input {
            raw_text: "剣".into(),
            ..Input::default()
        };
        let Submission::Handled(dispatch) = state.submit(
            input,
            &GameEvent::parse(event_value()).unwrap(),
            time,
            false,
        ) else {
            panic!("expected explicit sword request");
        };
        dispatch
    }

    #[test]
    fn horse_queue_revalidates_capability_snapshot_and_target_before_issue() {
        let p = prepared();
        let mut unavailable = AssistState::new([]);
        assert!(!unavailable.supports_horse_recall());
        assert!(
            unavailable
                .enqueue_horse(&p, &choice(), &p.snapshot, now())
                .is_err()
        );
        for mutate in [
            |s: &mut Snapshot| s.observed_at -= Duration::seconds(4),
            |s: &mut Snapshot| s.observed_at += Duration::seconds(2),
            |s: &mut Snapshot| s.world_id = "10000000-0000-0000-0000-000000000002".into(),
            |s: &mut Snapshot| s.player_uuid = "20000000-0000-0000-0000-000000000002".into(),
            |s: &mut Snapshot| s.candidates[0].custom_name = Some("別の名前".into()),
            |s: &mut Snapshot| s.candidates[0].owner_uuid = None,
            |s: &mut Snapshot| s.candidates[0].has_passengers = true,
            |s: &mut Snapshot| s.candidates.clear(),
        ] {
            let mut latest = p.snapshot.clone();
            mutate(&mut latest);
            let mut state = state();
            assert!(state.enqueue_horse(&p, &choice(), &latest, now()).is_err());
            assert!(state.commands(now()).is_empty());
        }
        let mut latest = p.snapshot.clone();
        latest.revision += 1;
        assert!(state().enqueue_horse(&p, &choice(), &latest, now()).is_ok());
    }

    #[test]
    fn horse_queue_retransmits_one_id_and_new_input_does_not_cancel_issued_movement() {
        let mut state = state();
        let command = issue(&mut state);
        assert!(uuid::Uuid::parse_str(&command.command_id).is_ok());
        assert_eq!(command.expires_at - command.issued_at, Duration::seconds(5));
        let p = prepared();
        assert!(
            state
                .enqueue_horse(&p, &choice(), &p.snapshot, now())
                .is_err()
        );
        state.invalidate_intent();
        state.submit(
            Input {
                raw_text: "今日は晴れやな".into(),
                ..Input::default()
            },
            &GameEvent::parse(event_value()).unwrap(),
            now(),
            false,
        );
        let outgoing = state.commands(now() + Duration::seconds(1));
        assert_eq!(outgoing.len(), 1);
        assert_eq!(outgoing[0]["type"], "recall_horse");
        assert_eq!(outgoing[0]["command_id"], command.command_id);
        assert_eq!(outgoing[0]["entity_id"], command.entity_id);
        assert!(state.pending_commands(now()).is_empty());
    }

    #[test]
    fn horse_queue_unknown_and_duplicate_results_share_ack_without_success_evidence() {
        let mut state = state();
        let command = issue(&mut state);
        let mut unknown = result(&command);
        unknown.command_id = uuid::Uuid::new_v4().to_string();
        let out = state.observe_results(&[unknown.clone()], now() + Duration::seconds(1), false);
        assert_eq!(out.acknowledged_ids, [unknown.command_id]);
        assert!(!out.observed[0].known_command);
        assert!(out.feedback.is_none());
        assert_eq!(state.commands(now()).len(), 1);
        let success = result(&command);
        let out = state.observe_results(
            &[success.clone(), success.clone()],
            now() + Duration::seconds(1),
            false,
        );
        assert_eq!(
            out.acknowledged_ids.as_slice(),
            std::slice::from_ref(&command.command_id)
        );
        assert_eq!(out.observed.len(), 1);
        assert!(out.observed[0].known_command);
        assert_eq!(
            out.observed[0].result.status,
            AdapterCommandResultStatus::Succeeded
        );
        assert!(out.feedback.is_none());
        assert!(state.commands(now()).is_empty());
        let out = state.observe_results(&[success], now() + Duration::seconds(2), false);
        assert_eq!(out.acknowledged_ids, [command.command_id]);
        assert!(out.observed.is_empty());
    }

    #[test]
    fn horse_queue_rejects_wrong_type_identity_missing_fields_and_bad_time() {
        type Mutation = (fn(&mut AdapterCommandResult), &'static str);
        let cases: [Mutation; 7] = [
            (
                |r: &mut AdapterCommandResult| {
                    r.command_type = AdapterCommandResultCommandType::SelectHotbar
                },
                "result_type_mismatch",
            ),
            (
                |r: &mut AdapterCommandResult| {
                    r.extra.insert(
                        "entity_id".into(),
                        "30000000-0000-0000-0000-000000000002".into(),
                    );
                },
                "result_mismatch",
            ),
            (
                |r: &mut AdapterCommandResult| {
                    r.extra.insert(
                        "world_id".into(),
                        "10000000-0000-0000-0000-000000000002".into(),
                    );
                },
                "result_mismatch",
            ),
            (
                |r: &mut AdapterCommandResult| {
                    r.extra.remove("entity_id");
                },
                "result_mismatch",
            ),
            (
                |r: &mut AdapterCommandResult| {
                    r.executed_at =
                        EventTime::Aware((now() - Duration::milliseconds(1)).fixed_offset())
                },
                "result_outside_validity_window",
            ),
            (
                |r: &mut AdapterCommandResult| {
                    r.executed_at = EventTime::Aware((now() + Duration::seconds(5)).fixed_offset())
                },
                "result_outside_validity_window",
            ),
            (
                |r: &mut AdapterCommandResult| r.executed_at = EventTime::Naive(now().naive_utc()),
                "result_outside_validity_window",
            ),
        ];
        for (mutate, detail) in cases {
            let mut state = state();
            let command = issue(&mut state);
            let mut incoming = result(&command);
            mutate(&mut incoming);
            let out = state.observe_results(&[incoming], now() + Duration::seconds(1), false);
            assert!(out.observed[0].known_command);
            assert_eq!(
                out.observed[0].result.command_type,
                AdapterCommandResultCommandType::RecallHorse
            );
            assert_eq!(
                out.observed[0].result.status,
                AdapterCommandResultStatus::Failed
            );
            assert_eq!(out.observed[0].result.detail_code, detail);
            assert_eq!(
                out.feedback.as_deref(),
                Some(if detail == "result_outside_validity_window" {
                    "呼び戻しの結果を時間内に確認できへんかったわ。その子の様子を見てな。"
                } else {
                    "呼び戻しを確認できへんかったわ。呼んだ子の状態を見てな。"
                })
            );
            assert!(state.commands(now()).is_empty());
        }
    }

    #[test]
    fn horse_queue_expiry_is_once_and_late_success_does_not_resurrect_it() {
        for incoming_first in [false, true] {
            let mut state = state();
            let command = issue(&mut state);
            let success = result(&command);
            let input = if incoming_first {
                vec![success.clone()]
            } else {
                vec![]
            };
            let out = state.observe_results(&input, now() + Duration::seconds(10), false);
            assert_eq!(out.observed.len(), 1);
            assert_eq!(
                out.observed[0].result.status,
                AdapterCommandResultStatus::Expired
            );
            assert_eq!(out.observed[0].result.command_id, command.command_id);
            assert_eq!(out.observed[0].result.detail_code, "server_result_timeout");
            assert!(
                state
                    .observe_results(&[], now() + Duration::seconds(11), false)
                    .observed
                    .is_empty()
            );
            let late = state.observe_results(&[success], now() + Duration::seconds(11), false);
            assert_eq!(late.acknowledged_ids, [command.command_id]);
            assert!(late.observed.is_empty());
        }
        let mut state = state();
        let old = issue(&mut state);
        let mut p = prepared();
        p.snapshot.observed_at += Duration::seconds(10);
        let new = state
            .enqueue_horse(&p, &choice(), &p.snapshot, now() + Duration::seconds(10))
            .unwrap();
        assert_ne!(old.command_id, new.command_id);
        let out = state.observe_results(&[], now() + Duration::seconds(10), false);
        assert_eq!(out.observed.len(), 1);
        assert_eq!(out.observed[0].result.command_id, old.command_id);
    }

    #[test]
    fn horse_queue_waits_for_receipt_without_extending_execution_or_retransmission() {
        for receipt_ms in [5_200, 9_999] {
            let mut state = state();
            let command = issue(&mut state);
            // 次の観測で成功が届くまで、期限切れを失敗として記録せず、再配送も止める。
            let out = state.observe_results(&[], now() + Duration::seconds(5), false);
            assert!(out.observed.is_empty());
            assert!(out.feedback.is_none());
            assert!(state.commands(now() + Duration::seconds(5)).is_empty());
            let mut p = prepared();
            p.snapshot.observed_at += Duration::seconds(5);
            assert!(
                state
                    .enqueue_horse(&p, &choice(), &p.snapshot, now() + Duration::seconds(5))
                    .is_err()
            );
            let mut success = result(&command);
            success.executed_at =
                EventTime::Aware((now() + Duration::milliseconds(4_700)).fixed_offset());
            let out = state.observe_results(
                &[success.clone()],
                now() + Duration::milliseconds(receipt_ms),
                false,
            );
            assert_eq!(
                out.observed[0].result.status,
                AdapterCommandResultStatus::Succeeded
            );
            assert!(out.feedback.is_none());
            assert!(
                state
                    .observe_results(&[success], now() + Duration::seconds(10), false)
                    .observed
                    .is_empty()
            );
        }
        let mut state = state();
        let command = issue(&mut state);
        let mut too_late = result(&command);
        too_late.executed_at = EventTime::Aware((now() + Duration::seconds(5)).fixed_offset());
        let out = state.observe_results(&[too_late], now() + Duration::seconds(6), false);
        assert_eq!(
            out.observed[0].result.status,
            AdapterCommandResultStatus::Failed
        );
        assert_eq!(
            out.observed[0].result.detail_code,
            "result_outside_validity_window"
        );
    }

    #[test]
    fn horse_queue_keeps_hotbar_pending_feedback_and_success_cooldown_separate() {
        let mut state = state();
        let hotbar = sword(&mut state, now()).command.unwrap();
        let horse = issue(&mut state);
        assert_eq!(state.commands(now()).len(), 2);
        assert_eq!(
            state.pending_commands(now())[0].command_id,
            hotbar.command_id
        );
        let hotbar_result: AdapterCommandResult = serde_json::from_value(json!({
            "command_id":hotbar.command_id,"command_type":"select_hotbar","status":"succeeded",
            "executed_at":now()+Duration::milliseconds(200),"selected_slot":hotbar.slot,
            "selected_item_id":hotbar.expected_item_id}))
        .unwrap();
        state.observe_results(&[hotbar_result], now() + Duration::milliseconds(300), false);
        let mut failure = result(&horse);
        failure.status = AdapterCommandResultStatus::Rejected;
        let out = state.observe_results(&[failure], now() + Duration::seconds(1), false);
        assert!(out.feedback.unwrap().starts_with("呼び戻し"));
        assert_eq!(
            sword(&mut state, now() + Duration::seconds(1)).detail_code,
            "command_cooldown"
        );
        assert!(
            sword(&mut state, now() + Duration::seconds(3))
                .command
                .is_some()
        );

        let mut state = self::state();
        let horse = issue(&mut state);
        state.observe_results(&[result(&horse)], now() + Duration::seconds(1), false);
        assert!(
            sword(&mut state, now() + Duration::seconds(1))
                .command
                .is_some()
        );
    }

    #[test]
    fn horse_queue_event_contract_is_optional_and_validated_before_acceptance() {
        assert!(
            GameEvent::parse(event_value())
                .unwrap()
                .owned_horses
                .is_none()
        );
        let mut event = event_value();
        event["owned_horses"] = serde_json::to_value(prepared().snapshot).unwrap();
        assert!(
            GameEvent::parse(event.clone())
                .unwrap()
                .owned_horses
                .is_some()
        );
        for (field, value) in [
            ("schema_version", json!(1)),
            ("world_id", json!("not-a-uuid")),
            ("revision", json!(0)),
        ] {
            let mut invalid = event.clone();
            invalid["owned_horses"][field] = value;
            assert!(GameEvent::parse(invalid).is_err());
        }
    }
}
