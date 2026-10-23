//! 分類済みの身体依頼を一件の期限付き命令に結び、実行結果を同じ入力世代へ照合する。
//! 意図・座標・所有者の推測やモデル呼出しは行わない。実移動と向きの適用は身体Modが所有する。
use super::contracts::{Context, canonical_uuid};
use crate::events::{
    AdapterCommandResult, AdapterCommandResultCommandType, AdapterCommandResultStatus,
};
use anyhow::{Result, ensure};
use chrono::{DateTime, Duration as UtcDuration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    time::{Duration, Instant},
};

pub const CAPABILITY: &str = "body.request.v1";
const COMMAND_TTL: Duration = Duration::from_secs(5);
const RESULT_TTL: Duration = Duration::from_secs(35);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Recall,
    LookDirection,
    LookTarget,
    LookPlayer,
    LookRelativeFront,
    LookRelativeBack,
    LookRelativeLeft,
    LookRelativeRight,
    LookRelativeFrontLeft,
    LookRelativeFrontRight,
    LookRelativeBackLeft,
    LookRelativeBackRight,
}

struct Pending {
    command: Value,
    context: Option<Context>,
    mode: Mode,
    generation: u64,
    issued: Instant,
    utc: DateTime<Utc>,
    original: String,
}

/// 操作の結果。受付・送信待ちを成功へ昇格させない。
pub struct Report {
    pub command_id: String,
    pub original: String,
    pub value: Value,
    pub feedback: Option<&'static str>,
}

#[derive(Default)]
pub struct State {
    enabled: bool,
    pending: Option<Pending>,
    reports: VecDeque<Report>,
}

impl State {
    pub fn set_enabled(&mut self, enabled: bool) {
        self.enabled = enabled;
        if !enabled {
            self.cancel();
        }
    }

    /// BERTへ変更済み。旧判定へ戻さない。呼出元で分類と原文を確認した依頼だけ受ける。
    #[allow(clippy::too_many_arguments)]
    pub fn issue(
        &mut self,
        mode: Mode,
        generation: u64,
        owner: &str,
        dimension: &str,
        context: Option<Context>,
        original: &str,
        now: Instant,
        utc: DateTime<Utc>,
    ) -> Result<Value> {
        ensure!(self.enabled, "body_request_capability_missing");
        ensure!(
            canonical_uuid(owner) && !dimension.is_empty(),
            "body_request_identity_missing"
        );
        ensure!(
            mode == Mode::Recall || context.is_some(),
            "body_request_context_missing"
        );
        if let Some(context) = &context {
            ensure!(
                context.owner_id == owner
                    && context.dimension == dimension
                    && canonical_uuid(&context.body_id)
                    && canonical_uuid(&context.epoch),
                "body_request_context_mismatch"
            );
        }
        self.cancel();
        let command = json!({"type":"body_request", "command_id":uuid::Uuid::new_v4().to_string(),
            "issued_at":utc, "expires_at":utc+UtcDuration::seconds(5),
            "input_generation":generation, "owner_id":owner, "dimension":dimension,
            "mode":mode, "context":context});
        self.pending = Some(Pending {
            command: command.clone(),
            context,
            mode,
            generation,
            issued: now,
            utc,
            original: original.to_owned(),
        });
        Ok(command)
    }

    fn finish(&mut self, status: &str, detail: &str, feedback: Option<&'static str>) {
        if let Some(p) = self.pending.take() {
            self.reports.push_back(Report {
                command_id: p.command["command_id"].as_str().unwrap().to_owned(),
                original: p.original,
                value: json!({"command_id":p.command["command_id"],"mode":p.mode,
                    "input_generation":p.generation,"status":status,"detail_code":detail}),
                feedback,
            });
            while self.reports.len() > 16 {
                self.reports.pop_front();
            }
        }
    }

    pub fn cancel(&mut self) {
        self.finish("cancelled", "input_or_session_changed", None);
    }

    fn refresh(
        &mut self,
        generation: u64,
        owner: Option<&str>,
        dimension: Option<&str>,
        context: Option<&Context>,
        active: bool,
        now: Instant,
        utc: DateTime<Utc>,
    ) {
        let Some(p) = &self.pending else {
            return;
        };
        if !active
            || !self.enabled
            || generation != p.generation
            || owner != p.command["owner_id"].as_str()
            || dimension != p.command["dimension"].as_str()
            || p.mode != Mode::Recall && p.context.as_ref() != context
        {
            self.cancel();
        } else if now.saturating_duration_since(p.issued) >= RESULT_TTL
            || utc < p.utc
            || utc >= p.utc + UtcDuration::seconds(35)
        {
            self.finish(
                "expired",
                "result_timeout",
                Some("動いた結果を確認できへんかったわ。"),
            );
        }
    }

    /// 再配送は同じIDで5秒だけ。入力やworldの変更後に古い依頼を再発行しない。
    #[allow(clippy::too_many_arguments)]
    pub fn commands(
        &mut self,
        generation: u64,
        owner: Option<&str>,
        dimension: Option<&str>,
        context: Option<&Context>,
        active: bool,
        now: Instant,
        utc: DateTime<Utc>,
    ) -> Vec<Value> {
        self.refresh(generation, owner, dimension, context, active, now, utc);
        self.pending
            .as_ref()
            .filter(|p| {
                now.saturating_duration_since(p.issued) < COMMAND_TTL
                    && utc < p.utc + UtcDuration::seconds(5)
            })
            .map(|p| vec![p.command.clone()])
            .unwrap_or_default()
    }

    /// 共有ACKとは別に、発行した命令の終端だけを一度受理する。
    #[allow(clippy::too_many_arguments)]
    pub fn observe(
        &mut self,
        incoming: &[AdapterCommandResult],
        generation: u64,
        owner: Option<&str>,
        dimension: Option<&str>,
        context: Option<&Context>,
        active: bool,
        now: Instant,
        utc: DateTime<Utc>,
    ) -> Vec<Report> {
        self.refresh(generation, owner, dimension, context, active, now, utc);
        for r in incoming {
            let Some(p) = &self.pending else {
                break;
            };
            if r.command_type != AdapterCommandResultCommandType::BodyRequest
                || r.command_id != p.command["command_id"].as_str().unwrap()
                || r.extra.get("input_generation").and_then(Value::as_u64) != Some(p.generation)
                || r.extra.get("owner_id").and_then(Value::as_str) != p.command["owner_id"].as_str()
                || r.extra.get("dimension").and_then(Value::as_str)
                    != p.command["dimension"].as_str()
            {
                continue;
            }
            let executed = r.executed_at;
            if executed < p.utc || executed > utc || executed >= p.utc + UtcDuration::seconds(35) {
                continue;
            }
            let success = r.status == AdapterCommandResultStatus::Succeeded;
            if success {
                let body_id = r.extra.get("body_id").and_then(Value::as_str);
                let epoch = r.extra.get("epoch").and_then(Value::as_str);
                if !body_id.is_some_and(canonical_uuid) || !epoch.is_some_and(canonical_uuid) {
                    continue;
                }
                if p.mode == Mode::Recall {
                    // 呼び出しのwarpはepochが変わる。本人・次元・命令IDを保った実到着だけを受ける。
                    if r.detail_code != "arrived"
                        || p.context
                            .as_ref()
                            .is_some_and(|c| body_id != Some(c.body_id.as_str()))
                    {
                        continue;
                    }
                } else if r.detail_code != "facing_target"
                    || p.context.as_ref().is_none_or(|c| {
                        body_id != Some(c.body_id.as_str()) || epoch != Some(c.epoch.as_str())
                    })
                {
                    continue;
                }
            }
            let feedback = if success {
                if p.mode == Mode::Recall {
                    "来たで。"
                } else {
                    "そっち向いたで。"
                }
            } else if p.mode == Mode::Recall {
                "今はそっちへ行けへんかったわ。"
            } else {
                "今はそっちを向けへんかったわ。"
            };
            self.finish(
                match r.status {
                    AdapterCommandResultStatus::Succeeded => "succeeded",
                    AdapterCommandResultStatus::Rejected => "rejected",
                    AdapterCommandResultStatus::Failed => "failed",
                    AdapterCommandResultStatus::Expired => "expired",
                },
                &r.detail_code,
                Some(feedback),
            );
        }
        self.reports.drain(..).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const OWNER: &str = "10000000-0000-4000-8000-000000000001";
    const OTHER_OWNER: &str = "10000000-0000-4000-8000-000000000002";
    const BODY: &str = "20000000-0000-4000-8000-000000000001";
    const OTHER_BODY: &str = "20000000-0000-4000-8000-000000000002";
    const EPOCH: &str = "30000000-0000-4000-8000-000000000001";
    const NEXT_EPOCH: &str = "30000000-0000-4000-8000-000000000002";
    const DIMENSION: &str = "minecraft:overworld";
    const GENERATION: u64 = 7;
    const MODES: [(Mode, &str); 12] = [
        (Mode::Recall, "recall"),
        (Mode::LookDirection, "look_direction"),
        (Mode::LookTarget, "look_target"),
        (Mode::LookPlayer, "look_player"),
        (Mode::LookRelativeFront, "look_relative_front"),
        (Mode::LookRelativeBack, "look_relative_back"),
        (Mode::LookRelativeLeft, "look_relative_left"),
        (Mode::LookRelativeRight, "look_relative_right"),
        (Mode::LookRelativeFrontLeft, "look_relative_front_left"),
        (Mode::LookRelativeFrontRight, "look_relative_front_right"),
        (Mode::LookRelativeBackLeft, "look_relative_back_left"),
        (Mode::LookRelativeBackRight, "look_relative_back_right"),
    ];

    struct Fixture {
        state: State,
        now: Instant,
        utc: DateTime<Utc>,
        context: Context,
    }
    impl Fixture {
        fn new() -> Self {
            let mut state = State::default();
            state.set_enabled(true);
            Self {
                state,
                now: Instant::now(),
                utc: DateTime::parse_from_rfc3339("2026-10-23T03:00:00Z")
                    .unwrap()
                    .with_timezone(&Utc),
                context: Context {
                    owner_id: OWNER.into(),
                    body_id: BODY.into(),
                    epoch: EPOCH.into(),
                    dimension: DIMENSION.into(),
                },
            }
        }
        fn issue(&mut self, mode: Mode) -> Value {
            self.state
                .issue(
                    mode,
                    GENERATION,
                    OWNER,
                    DIMENSION,
                    Some(self.context.clone()),
                    "身体への原文の依頼",
                    self.now,
                    self.utc,
                )
                .unwrap()
        }
        fn result(&self, command: &Value, status: &str, detail: &str) -> Value {
            json!({"command_id":command["command_id"],"command_type":"body_request","status":status,
                "executed_at":self.utc+UtcDuration::seconds(1),"detail_code":detail,
                "input_generation":GENERATION,"owner_id":OWNER,"dimension":DIMENSION,
                "body_id":BODY,"epoch":EPOCH})
        }
        fn observe(&mut self, rows: Vec<Value>) -> Vec<Report> {
            let results: Vec<AdapterCommandResult> = rows
                .into_iter()
                .map(|value| serde_json::from_value(value).unwrap())
                .collect();
            self.state.observe(
                &results,
                GENERATION,
                Some(OWNER),
                Some(DIMENSION),
                Some(&self.context),
                true,
                self.now + Duration::from_secs(2),
                self.utc + UtcDuration::seconds(2),
            )
        }
        fn commands_after(&mut self, millis: u64) -> Vec<Value> {
            self.state.commands(
                GENERATION,
                Some(OWNER),
                Some(DIMENSION),
                Some(&self.context),
                true,
                self.now + Duration::from_millis(millis),
                self.utc + UtcDuration::milliseconds(millis as i64),
            )
        }
    }

    #[test]
    fn capability_is_required_and_disabling_it_cancels_without_success_feedback() {
        let mut f = Fixture::new();
        f.state.set_enabled(false);
        assert!(
            f.state
                .issue(
                    Mode::Recall,
                    GENERATION,
                    OWNER,
                    DIMENSION,
                    None,
                    "来て",
                    f.now,
                    f.utc
                )
                .is_err()
        );
        f.state.set_enabled(true);
        let command = f.issue(Mode::LookPlayer);
        f.state.set_enabled(false);
        assert!(f.commands_after(1).is_empty());
        let reports = f.observe(vec![]);
        assert_eq!(reports.len(), 1);
        assert_eq!(
            reports[0].command_id,
            command["command_id"].as_str().unwrap()
        );
        assert_eq!(reports[0].value["status"], "cancelled");
        assert!(reports[0].feedback.is_none());
    }

    #[test]
    fn all_twelve_modes_use_the_v1_wire_contract_and_only_recall_allows_no_context() {
        for (mode, wire) in MODES {
            let mut f = Fixture::new();
            let command = f.issue(mode);
            assert_eq!(command["mode"], wire);
            assert_eq!(command["type"], "body_request");
            assert_eq!(command["input_generation"], GENERATION);
            assert_eq!(command["owner_id"], OWNER);
            assert!(canonical_uuid(command["command_id"].as_str().unwrap()));
            let no_context = f.state.issue(
                mode, GENERATION, OWNER, DIMENSION, None, "依頼", f.now, f.utc,
            );
            assert_eq!(no_context.is_ok(), mode == Mode::Recall);
        }
        assert_eq!(CAPABILITY, "body.request.v1");
    }

    #[test]
    fn identity_and_context_must_be_concrete_and_owned() {
        let mut f = Fixture::new();
        for owner in ["", "player", "10000000-0000-4000-8000-00000000000A"] {
            assert!(
                f.state
                    .issue(
                        Mode::Recall,
                        GENERATION,
                        owner,
                        DIMENSION,
                        None,
                        "来て",
                        f.now,
                        f.utc
                    )
                    .is_err()
            );
        }
        assert!(
            f.state
                .issue(
                    Mode::Recall,
                    GENERATION,
                    OWNER,
                    "",
                    None,
                    "来て",
                    f.now,
                    f.utc
                )
                .is_err()
        );
        for change in 0..4 {
            let mut context = f.context.clone();
            match change {
                0 => context.owner_id = OTHER_OWNER.into(),
                1 => context.dimension = "minecraft:the_nether".into(),
                2 => context.body_id = "missing".into(),
                _ => context.epoch = "missing".into(),
            }
            assert!(
                f.state
                    .issue(
                        Mode::LookPlayer,
                        GENERATION,
                        OWNER,
                        DIMENSION,
                        Some(context),
                        "見て",
                        f.now,
                        f.utc
                    )
                    .is_err()
            );
        }
    }

    #[test]
    fn retries_keep_one_id_for_five_seconds_and_wait_for_results_until_thirty_five_seconds() {
        let mut f = Fixture::new();
        let command = f.issue(Mode::LookRelativeRight);
        assert_eq!(f.commands_after(0), vec![command.clone()]);
        assert_eq!(f.commands_after(4_999), vec![command.clone()]);
        assert!(f.commands_after(5_000).is_empty());
        assert!(f.state.pending.is_some());
        assert!(f.commands_after(34_999).is_empty());
        assert!(f.state.pending.is_some());
        assert!(f.commands_after(35_000).is_empty());
        let reports = f.state.observe(
            &[],
            GENERATION,
            Some(OWNER),
            Some(DIMENSION),
            Some(&f.context),
            true,
            f.now + Duration::from_secs(35),
            f.utc + UtcDuration::seconds(35),
        );
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].value["status"], "expired");
        assert_eq!(reports[0].value["detail_code"], "result_timeout");
        assert!(reports[0].feedback.is_some());
    }

    #[test]
    fn both_monotonic_and_utc_clocks_bound_commands_and_clock_rollback_expires() {
        let mut f = Fixture::new();
        f.issue(Mode::LookPlayer);
        assert!(
            f.state
                .commands(
                    GENERATION,
                    Some(OWNER),
                    Some(DIMENSION),
                    Some(&f.context),
                    true,
                    f.now + Duration::from_secs(5),
                    f.utc + UtcDuration::seconds(1)
                )
                .is_empty()
        );
        let mut f = Fixture::new();
        f.issue(Mode::LookPlayer);
        assert!(
            f.state
                .commands(
                    GENERATION,
                    Some(OWNER),
                    Some(DIMENSION),
                    Some(&f.context),
                    true,
                    f.now + Duration::from_secs(1),
                    f.utc + UtcDuration::seconds(5)
                )
                .is_empty()
        );
        let reports = f.state.observe(
            &[],
            GENERATION,
            Some(OWNER),
            Some(DIMENSION),
            Some(&f.context),
            true,
            f.now + Duration::from_secs(2),
            f.utc - UtcDuration::seconds(1),
        );
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].value["status"], "expired");
    }

    #[test]
    fn input_owner_dimension_epoch_and_pause_changes_cancel_old_look_commands() {
        for changed in 0..5 {
            let mut f = Fixture::new();
            let command = f.issue(Mode::LookPlayer);
            let mut context = f.context.clone();
            if changed == 3 {
                context.epoch = NEXT_EPOCH.into();
            }
            let generation = if changed == 0 {
                GENERATION + 1
            } else {
                GENERATION
            };
            let owner = if changed == 1 { OTHER_OWNER } else { OWNER };
            let dimension = if changed == 2 {
                "minecraft:the_nether"
            } else {
                DIMENSION
            };
            assert!(
                f.state
                    .commands(
                        generation,
                        Some(owner),
                        Some(dimension),
                        Some(&context),
                        changed != 4,
                        f.now + Duration::from_millis(1),
                        f.utc + UtcDuration::milliseconds(1)
                    )
                    .is_empty()
            );
            let late = f.result(&command, "succeeded", "facing_target");
            let reports = f.observe(vec![late]);
            assert_eq!(reports.len(), 1);
            assert_eq!(reports[0].value["status"], "cancelled");
            assert!(reports[0].feedback.is_none());
            assert!(f.observe(vec![]).is_empty());
        }
    }

    #[test]
    fn results_must_match_command_kind_id_generation_owner_dimension_and_time() {
        for changed in 0..7 {
            let mut f = Fixture::new();
            let command = f.issue(Mode::LookPlayer);
            let mut result = f.result(&command, "succeeded", "facing_target");
            match changed {
                0 => result["command_type"] = json!("inspect_body_target"),
                1 => result["command_id"] = json!("40000000-0000-4000-8000-000000000001"),
                2 => result["input_generation"] = json!(GENERATION + 1),
                3 => result["owner_id"] = json!(OTHER_OWNER),
                4 => result["dimension"] = json!("minecraft:the_nether"),
                5 => result["executed_at"] = json!(f.utc - UtcDuration::seconds(1)),
                _ => result["executed_at"] = json!(f.utc + UtcDuration::seconds(3)),
            }
            assert!(
                f.observe(vec![result]).is_empty(),
                "invalid result case {changed}"
            );
            assert!(f.state.pending.is_some());
            let good = f.result(&command, "succeeded", "facing_target");
            assert_eq!(f.observe(vec![good]).len(), 1);
        }
    }

    #[test]
    fn look_success_requires_actual_facing_target_for_the_same_body_and_epoch() {
        for changed in 0..5 {
            let mut f = Fixture::new();
            let command = f.issue(Mode::LookTarget);
            let mut result = f.result(&command, "succeeded", "facing_target");
            match changed {
                0 => result["detail_code"] = json!("queued"),
                1 => result["body_id"] = json!(OTHER_BODY),
                2 => result["epoch"] = json!(NEXT_EPOCH),
                3 => result["body_id"] = Value::Null,
                _ => result["epoch"] = json!("not-an-epoch"),
            }
            assert!(f.observe(vec![result]).is_empty());
            let good = f.result(&command, "succeeded", "facing_target");
            let reports = f.observe(vec![good.clone(), good.clone()]);
            assert_eq!(reports.len(), 1);
            assert_eq!(reports[0].feedback, Some("そっち向いたで。"));
            assert_eq!(reports[0].original, "身体への原文の依頼");
            assert!(f.observe(vec![good]).is_empty());
        }
    }

    #[test]
    fn accepted_or_queued_status_is_not_a_terminal_adapter_result() {
        let mut f = Fixture::new();
        let command = f.issue(Mode::Recall);
        for status in ["accepted", "queued"] {
            assert!(
                serde_json::from_value::<AdapterCommandResult>(
                    f.result(&command, status, "started")
                )
                .is_err()
            );
        }
        let premature = f.result(&command, "succeeded", "started");
        assert!(f.observe(vec![premature]).is_empty());
        assert!(f.state.pending.is_some());
    }

    #[test]
    fn terminal_failures_end_the_request_once_without_claiming_success() {
        for status in ["rejected", "failed", "expired"] {
            let mut f = Fixture::new();
            let command = f.issue(Mode::Recall);
            let mut result = f.result(&command, status, "body_chunk_not_loaded");
            result.as_object_mut().unwrap().remove("body_id");
            result.as_object_mut().unwrap().remove("epoch");
            let reports = f.observe(vec![result.clone(), result.clone()]);
            assert_eq!(reports.len(), 1);
            assert_eq!(reports[0].value["status"], status);
            assert_eq!(reports[0].feedback, Some("今はそっちへ行けへんかったわ。"));
            assert!(f.observe(vec![result]).is_empty());
        }
    }

    #[test]
    fn recall_allows_same_body_epoch_change_and_first_spawn_but_not_body_replacement() {
        let mut f = Fixture::new();
        let command = f.issue(Mode::Recall);
        f.context.epoch = NEXT_EPOCH.into();
        let mut result = f.result(&command, "succeeded", "arrived");
        result["epoch"] = json!(NEXT_EPOCH);
        let reports = f.observe(vec![result]);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].feedback, Some("来たで。"));

        let mut f = Fixture::new();
        let command = f
            .state
            .issue(
                Mode::Recall,
                GENERATION,
                OWNER,
                DIMENSION,
                None,
                "来て",
                f.now,
                f.utc,
            )
            .unwrap();
        let mut result = f.result(&command, "succeeded", "arrived");
        result["body_id"] = json!(OTHER_BODY);
        assert_eq!(f.observe(vec![result]).len(), 1);

        let mut f = Fixture::new();
        let command = f.issue(Mode::Recall);
        let mut result = f.result(&command, "succeeded", "arrived");
        result["body_id"] = json!(OTHER_BODY);
        assert!(
            f.observe(vec![result]).is_empty(),
            "an existing recalled body cannot be replaced by a different ID"
        );
        let good = f.result(&command, "succeeded", "arrived");
        assert_eq!(f.observe(vec![good]).len(), 1);
    }

    #[test]
    fn replacement_request_cancels_the_old_id_and_keeps_the_new_request() {
        let mut f = Fixture::new();
        let first = f.issue(Mode::LookPlayer);
        let second = f.issue(Mode::LookRelativeBack);
        assert_ne!(first["command_id"], second["command_id"]);
        let late = f.result(&first, "succeeded", "facing_target");
        let reports = f.observe(vec![late]);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].command_id, first["command_id"].as_str().unwrap());
        assert_eq!(reports[0].value["status"], "cancelled");
        assert_eq!(f.commands_after(3_000), vec![second]);
    }
}
