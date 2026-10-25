//! 同じ接続・身体世代の候補だけを保持し、選択開始から命令の終端までを一件ずつ管理する。
//! 単調時計で鮮度と待機時間を測り、UTCはadapterへ渡す期限と実行結果の時刻照合に使う。
use super::contracts::{
    Activity, Candidate, CandidateKind, Mode, Observation, Observed, Reassurance, distance_squared, short_text,
};
use crate::body::contracts::{Context, Sample};
use crate::events::{AdapterCommandResult, AdapterCommandResultStatus};
use anyhow::{Result, anyhow, ensure};
use chrono::{DateTime, Duration as UtcDuration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    time::{Duration, Instant},
};

const FRESH_MS: u64 = 3000;
const ATTEMPT_INTERVAL: Duration = Duration::from_secs(3);
const COMMAND_TTL: Duration = Duration::from_secs(5);
const RESULT_DEADLINE: Duration = Duration::from_secs(35);
const CHOICE_LIMIT: usize = 8;
const VISIT_LIMIT: usize = 64;
const REVISIT_INTERVAL: Duration = Duration::from_secs(120);
const COMMAND_TYPE: &str = "inspect_body_target";

#[derive(Clone)]
struct Reading {
    data: Observed,
    received: Instant,
    age_ms: u64,
}
impl Reading {
    fn fresh(&self, now: Instant) -> bool {
        self.age_ms.saturating_add(
            now.saturating_duration_since(self.received)
                .as_millis()
                .min(u64::MAX as u128) as u64,
        ) < FRESH_MS
    }
}
struct Pending {
    id: String,
    context: Context,
    issued: Instant,
    utc: DateTime<Utc>,
    command: Value,
    target: Option<Candidate>,
}

/// 実際の到着通知を受理した対象だけを短時間保持する。種類の既見や選択開始とは別の記録。
struct Visit {
    id: String,
    kind: CandidateKind,
    type_id: String,
    arrived: Instant,
}

/// 共通の環境判断へ匿名候補・実測安心度・可能な行動を渡す。不変候補表は実行前照合用。
#[derive(Clone, Debug)]
pub struct Prepared {
    pub context: Context,
    pub candidates: Vec<Candidate>,
    pub model_candidates: Value,
    pub recent_choices: Value,
    pub reassurance: Value,
    pub available_actions: Vec<String>,
    pub after_owner_arrival: bool,
    pub image_capture: Option<(String, DateTime<Utc>)>,
    attempt: uuid::Uuid,
}

impl Prepared {
    /// コードが今回許可した行動と説明だけを渡す。選べない行動名を固定promptへ列挙しない。
    pub fn model_actions(&self) -> Value {
        Value::Object(self.available_actions.iter().map(|action| {
            let meaning = match action.as_str() {
                "owner_near" => "そばへ戻る",
                "owner_face" => "顔が見える位置へ近づく",
                "look_owner" => "その場でプレイヤーを見る",
                "inspect" => "見物",
                "inspect_image" => "画像の気になる所を見物",
                "stay" => "その場におる",
                _ => unreachable!("closed body action"),
            };
            (action.clone(), json!(meaning))
        }).collect())
    }
}

/// 接続内の一世代を保持する。選択8件・到着64件を上限とし、永続保存しない。
/// 周辺の新旧判定は共通の変化テーブルが所有し、この状態へ別の既見表を作らない。
#[derive(Default)]
pub struct State {
    generation: Option<Context>,
    accepted: Option<Sample>,
    current: Option<Reading>,
    watermark: Option<(u64, u64)>,
    choices: VecDeque<Value>,
    visits: VecDeque<Visit>,
    last_attempt: Option<Instant>,
    prepared: Option<uuid::Uuid>,
    pending: Option<Pending>,
    diagnostics: Vec<Value>,
    owner_arrival_pending: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Choice {
    action: Action,
    target_id: Option<String>,
    #[serde(default)]
    reason: Option<String>,
    #[serde(default)]
    image_target: Option<ImageTarget>,
}
/// モデルは見た目の分類・色・画面内の位置関係を返す。実物IDと位置は撮影時の対応表で解決する。
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ImageTarget { description: String, kind: String, color: String, horizontal: String, vertical: String }
impl ImageTarget {
    fn valid(&self) -> bool {
        short_text(&self.description,80)
            && ["any","mob","flower","plant","block","fluid"].contains(&self.kind.as_str())
            && ["any","red","orange","yellow","green","cyan","blue","purple","pink","brown","white","gray","black"].contains(&self.color.as_str())
            && ["any","left","center","right"].contains(&self.horizontal.as_str())
            && ["any","upper","middle","lower"].contains(&self.vertical.as_str())
            && (self.kind != "any" || self.color != "any" || self.horizontal != "any" || self.vertical != "any")
    }
}
#[derive(Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Action {
    Inspect,
    InspectImage,
    OwnerNear,
    OwnerFace,
    LookOwner,
    Stay,
}

impl State {
    fn lose_current(&mut self) {
        self.current = None;
        self.owner_arrival_pending = false;
        self.cancel();
    }
    /// acceptedはbody::Stateが同じ接続で鮮度・所有者・世代を受理したsample。
    /// 観測の省略は更新しないが、身体の根拠消失では候補と未完了命令を失効させる。
    pub fn observe(
        &mut self,
        observation: Option<&Observation>,
        accepted: Option<&Sample>,
        now: Instant,
        transport_age: Option<u64>,
    ) {
        let Some(accepted) = accepted else {
            self.accepted = None;
            self.lose_current();
            return;
        };
        if self.generation.as_ref() != Some(&accepted.context) {
            self.lose_current();
            self.generation = Some(accepted.context.clone());
            self.watermark = None;
            self.choices.clear();
            self.visits.clear();
            self.last_attempt = None;
        }
        self.accepted = Some(accepted.clone());
        let Some(observation) = observation else {
            return;
        };
        if observation.validate().is_err() {
            self.lose_current();
            return;
        }
        let Observation::Observed(data) = observation else {
            self.lose_current();
            return;
        };
        if data.context != accepted.context
            || data.server_tick != accepted.server_tick
            || data.snapshot_revision != accepted.snapshot_revision
        {
            return;
        }
        if self.watermark.is_some_and(|(tick, revision)| {
            data.server_tick <= tick || data.snapshot_revision <= revision
        }) {
            return;
        }
        // 未取得後も最終番号は保持し、同じsampleの再送で候補の寿命を復活させない。
        self.watermark = Some((data.server_tick, data.snapshot_revision));
        let age_ms = transport_age
            .unwrap_or(u64::MAX)
            .saturating_add(data.age_ms);
        if age_ms >= FRESH_MS {
            self.lose_current();
            return;
        }
        // follow_settlingは初回待機や高速移動でも現れるため、それだけを到着扱いしない。
        // 同一世代のfreshな追従から実到着後の待機へ移った時だけ、一回の相談を保持する。
        let previous = self.current.as_ref().filter(|previous| previous.fresh(now));
        if previous.is_none() { self.owner_arrival_pending = false; }
        let followed = previous.is_some_and(|previous| previous.data.enabled
            && previous.data.mode == Mode::Follow && previous.data.activity == Activity::Following);
        if Self::owner_arrival_holding(data) {
            if followed && matches!(data.reason.as_str(), "follow_settling" | "within_follow_distance") {
                self.owner_arrival_pending = true;
            }
        } else {
            self.owner_arrival_pending = false;
        }
        self.current = Some(Reading {
            data: data.as_ref().clone(),
            received: now,
            age_ms,
        });
        if !self.available(now) {
            self.cancel();
        }
    }
    /// freshなFOLLOWの待機・自主見物中だけ有効。追従・呼出し・危険回避などの優先行動では失効する。
    pub fn available(&self, now: Instant) -> bool {
        self.current.as_ref().is_some_and(|r| {
            r.fresh(now)
                && r.data.enabled
                && r.data.mode == Mode::Follow
                && matches!(
                    r.data.activity,
                    Activity::Holding | Activity::Exploring | Activity::Moving
                )
                && self
                    .accepted
                    .as_ref()
                    .is_some_and(|a| a.context == r.data.context)
        })
    }
    fn expire(&mut self, now: Instant, utc: Option<DateTime<Utc>>) {
        if !self.pending.as_ref().is_some_and(|p| {
            now.saturating_duration_since(p.issued) >= RESULT_DEADLINE
                || utc.is_some_and(|u| u >= p.utc + UtcDuration::seconds(35))
        }) {
            return;
        }
        let pending = self.pending.take().expect("expired pending");
        self.diagnostics.push(
            json!({"command_id":pending.id,"type":COMMAND_TYPE,"status":"expired",
            "detail_code":"server_result_timeout","context":pending.context}),
        );
    }
    /// 共通の環境判断が発火条件を計算するための実測。古い身体の値は返さない。
    pub fn reassurance(&self, now: Instant) -> Option<&Reassurance> {
        self.current.as_ref().filter(|r| r.fresh(now))
            .filter(|r| self.accepted.as_ref().is_some_and(|a| a.context == r.data.context))
            .map(|r| &r.data.reassurance)
    }
    /// 同じfreshな身体観測の通常追従距離（block）。近接による安心度回復の距離とは別の設定。
    /// 会話中も境界を比較できるよう、モデル生成の準備可否では制限しない。
    pub fn follow_distance(&self, now: Instant) -> Option<f64> {
        self.current.as_ref().filter(|r| r.fresh(now))
            .filter(|r| self.accepted.as_ref().is_some_and(|a| a.context == r.data.context))
            .map(|r| r.data.follow_distance)
    }
    /// adapterが同じframeへ実際に適用した注意範囲を返す。身体の行動名から視野を推測しない。
    pub fn attention(&self, now: Instant) -> Option<Value> {
        let reading = self.current.as_ref().filter(|r| r.fresh(now))
            .filter(|r| self.accepted.as_ref().is_some_and(|a| a.context == r.data.context))?;
        let mut value = json!(reading.data.attention);
        value["basis"] = json!("body_attention");
        Some(value)
    }
    /// 実到着後の相談を保持できる同一FOLLOWの待機。距離だけでは到着を作らない。
    fn owner_arrival_holding(data: &Observed) -> bool {
        data.enabled && data.mode == Mode::Follow && data.activity == Activity::Holding
            && data.reassurance.owner_distance <= data.follow_distance
            && matches!(data.reason.as_str(), "owner_near_dwell" | "follow_settling" | "within_follow_distance")
    }
    /// 実到着を観測した後の一件だけ。休止や制御変更は呼出側も明示的に失効させる。
    pub fn owner_arrival_pending(&self, now: Instant) -> bool {
        self.owner_arrival_pending && self.available(now)
            && self.current.as_ref().is_some_and(|r| Self::owner_arrival_holding(&r.data))
    }
    pub fn cancel_owner_arrival(&mut self) { self.owner_arrival_pending = false; }
    /// 到着後のそばに留まる・その場の注視・顔への接近は帰還直後の間にも進める。通常の見物・次の接近は従来の待機を守る。
    fn ready_activity(data: &Observed, after_owner_arrival: bool) -> bool {
        if after_owner_arrival { return Self::owner_arrival_holding(data); }
        data.activity == Activity::Holding
            && !matches!(data.reason.as_str(), "inspection_dwell" | "owner_near_dwell" | "owner_face_dwell" | "follow_settling" | "requested_look")
    }
    /// 見物候補の半径・同一対象の再訪間隔は、判断機会と実際の候補準備で同じ条件を使う。
    fn inspect_candidates(&self, data: &Observed, now: Instant) -> Vec<Candidate> {
        let Some(accepted) = &self.accepted else { return Vec::new(); };
        if data.reassurance.owner_distance > data.follow_distance { return Vec::new(); }
        data.candidates.iter().filter(|candidate| {
            distance_squared(accepted.player_pose.position, candidate.position) <= data.follow_distance.powi(2)
                && !self.visits.iter().any(|visit| visit.id == candidate.id && visit.kind == candidate.kind
                    && visit.type_id == candidate.type_id && now.saturating_duration_since(visit.arrived) < REVISIT_INTERVAL)
        }).cloned().collect()
    }
    /// 同じ環境判断を起こせる待機状態だけ、安心度と距離の境界を返す。予約や差分消費はしない。
    pub fn ready_reassurance(&self, now: Instant) -> Option<Value> {
        if !self.available(now) || self.prepared.is_some()
            || self.pending.as_ref().is_some_and(|p| now.saturating_duration_since(p.issued) < RESULT_DEADLINE)
            || !self.owner_arrival_pending(now)
                && self.last_attempt.is_some_and(|at| now.saturating_duration_since(at) < ATTEMPT_INTERVAL) {
            return None;
        }
        let data = &self.current.as_ref()?.data;
        if !Self::ready_activity(data, self.owner_arrival_pending(now)) {
            return None;
        }
        let mut value = json!(data.reassurance);
        value["follow_distance"] = json!(data.follow_distance);
        value["warp_distance"] = json!(data.warp_distance);
        value["has_inspection_candidates"] = json!(!self.inspect_candidates(data, now).is_empty());
        Some(value)
    }
    /// 待機中に3秒以上空けて共通判断の一回分を予約する。環境の発火周期は呼出側が所有する。
    /// 身体側の到着後の間を守り、直近120秒に実到着した同一対象だけを候補から除く。
    pub fn prepare(&mut self, now: Instant) -> Option<Prepared> {
        self.expire(now, None);
        if !self.available(now)
            || self.pending.is_some()
            || self.prepared.is_some()
            || !self.owner_arrival_pending(now) && self.last_attempt
                .is_some_and(|at| now.saturating_duration_since(at) < ATTEMPT_INTERVAL)
        {
            return None;
        }
        let data = &self.current.as_ref()?.data;
        let after_owner_arrival = self.owner_arrival_pending(now);
        if !Self::ready_activity(data, after_owner_arrival) {
            return None;
        }
        self.visits.retain(|v| now.saturating_duration_since(v.arrived) < REVISIT_INTERVAL);
        let candidates = if after_owner_arrival { Vec::new() } else { self.inspect_candidates(data, now) };
        let mut available_actions = vec!["stay".to_owned(), "look_owner".to_owned()];
        if !after_owner_arrival { available_actions.push("owner_near".to_owned()); }
        if !data.reassurance.owner_working { available_actions.push("owner_face".to_owned()); }
        if !candidates.is_empty() { available_actions.push("inspect".to_owned()); }
        let model_candidates = Value::Array(candidates.iter().enumerate().map(|(i,c)| json!({
            "id":format!("c{}",i+1), "kind":c.kind, "type_id":c.type_id, "label":c.label
        })).collect());
        let attempt = uuid::Uuid::new_v4();
        let result = Prepared {
            context: data.context.clone(),
            candidates,
            model_candidates,
            recent_choices: Value::Array(self.choices.iter().cloned().collect()),
            reassurance: json!(data.reassurance),
            available_actions,
            after_owner_arrival,
            image_capture: None,
            attempt,
        };
        // 帰還後のstay・注視・沈黙・失敗や顔への到着で再通知しない。新しい実帰還だけが次を作る。
        self.owner_arrival_pending = false;
        self.last_attempt = Some(now);
        self.prepared = Some(attempt);
        Some(result)
    }
    /// 閉じた選択JSONを読み、準備時と最新の同一候補・身体世代・所有者距離を再照合して発行する。
    /// 移動した候補を最新座標へすり替えず、モデルが選んだ元候補をそのままFabricへ渡す。
    pub fn choose(
        &mut self,
        prepared: &Prepared,
        output: &str,
        now: Instant,
        utc: DateTime<Utc>,
    ) -> Result<Option<Value>> {
        ensure!(
            self.prepared == Some(prepared.attempt),
            "body_autonomy_preparation_expired"
        );
        // この試行は成功・stay・形式不正のいずれでも一度だけ消費する。
        self.prepared = None;
        self.expire(now, Some(utc));
        ensure!(
            self.available(now) && self.pending.is_none(),
            "body_autonomy_unavailable"
        );
        let current = &self.current.as_ref().expect("available reading").data;
        ensure!(Self::ready_activity(current, prepared.after_owner_arrival), "body_autonomy_not_ready");
        ensure!(
            current.context == prepared.context,
            "body_autonomy_context_changed"
        );
        ensure!(output.len() <= 8192, "body_autonomy_choice_too_large");
        let value = crate::text_format::extract_object(output)
            .ok_or_else(|| anyhow!("body_autonomy_choice_object"))?;
        // target_idはstayでも明示nullが必要。Optionの省略を許すserde既定とは分けて確認する。
        ensure!(
            value.get("target_id").is_some(),
            "body_autonomy_choice_target_missing"
        );
        let choice: Choice = serde_json::from_value(value)?;
        ensure!(
            choice.reason.as_ref().is_none_or(|reason| short_text(reason, 160)),
            "body_autonomy_choice_reason"
        );
        let action_name = serde_json::to_value(choice.action)?.as_str().expect("action enum").to_owned();
        ensure!(prepared.available_actions.contains(&action_name), "body_autonomy_action_not_available");
        ensure!(choice.action == Action::InspectImage || choice.image_target.is_none(), "body_autonomy_unexpected_image_target");
        let mut image_target = None;
        let target = match choice.action {
            Action::InspectImage => {
                ensure!(choice.target_id.is_none(), "body_autonomy_image_target_id");
                let (capture_id, captured_at) = prepared.image_capture.as_ref().ok_or_else(|| anyhow!("body_autonomy_image_missing"))?;
                ensure!(utc >= *captured_at && utc-*captured_at < UtcDuration::seconds(30), "body_autonomy_image_expired");
                let selection=choice.image_target.as_ref().ok_or_else(|| anyhow!("body_autonomy_image_description_missing"))?;
                ensure!(selection.valid(), "body_autonomy_image_description_invalid");
                let mut value=serde_json::to_value(selection)?;
                value["capture_id"]=json!(capture_id);
                image_target=Some(value);
                None
            }
            Action::Stay => {
                ensure!(choice.target_id.is_none(), "body_autonomy_stay_target");
                None
            }
            Action::OwnerNear | Action::OwnerFace | Action::LookOwner => {
                ensure!(choice.target_id.is_none(), "body_autonomy_owner_target");
                ensure!(choice.action != Action::OwnerFace || !current.reassurance.owner_working,
                    "body_autonomy_owner_working");
                None
            }
            Action::Inspect => {
                let id = choice
                    .target_id
                    .as_deref()
                    .ok_or_else(|| anyhow!("body_autonomy_inspect_target"))?;
                let index = prepared
                    .candidates
                    .iter()
                    .enumerate()
                    .find_map(|(i, _)| (id == format!("c{}", i + 1)).then_some(i))
                    .ok_or_else(|| anyhow!("body_autonomy_unknown_target"))?;
                let selected = &prepared.candidates[index];
                let latest = current
                    .candidates
                    .iter()
                    .find(|c| c.id == selected.id)
                    .ok_or_else(|| anyhow!("body_autonomy_target_disappeared"))?;
                ensure!(
                    latest.kind == selected.kind
                        && latest.type_id == selected.type_id
                        && latest.entity_id == selected.entity_id,
                    "body_autonomy_target_changed"
                );
                ensure!(
                    distance_squared(latest.position, selected.position) <= 1.5_f64.powi(2),
                    "body_autonomy_target_moved"
                );
                let owner = self
                    .accepted
                    .as_ref()
                    .expect("available sample")
                    .player_pose
                    .position;
                ensure!(
                    distance_squared(owner, selected.position) <= current.follow_distance.powi(2)
                        && distance_squared(owner, latest.position)
                            <= current.follow_distance.powi(2),
                    "body_autonomy_outside_follow_range"
                );
                Some(selected.clone())
            }
        };
        self.choices.push_back(json!({"type_id":target.as_ref().map(|c| &c.type_id),"label":target.as_ref().map(|c| &c.label),
            "action":action_name,"reason":choice.reason}));
        while self.choices.len() > CHOICE_LIMIT {
            self.choices.pop_front();
        }
        if choice.action == Action::Stay { return Ok(None); }
        let id = uuid::Uuid::new_v4().to_string();
        let mut command = json!({"command_id":id,"type":COMMAND_TYPE,"issued_at":utc,
            "expires_at":utc+UtcDuration::seconds(5),"context":prepared.context,
            "purpose":if choice.action==Action::InspectImage {"inspect"} else {&action_name},"target":target});
        if let Some(image_target)=image_target {command["image_target"]=image_target;}
        self.pending = Some(Pending {
            id,
            context: prepared.context.clone(),
            issued: now,
            utc,
            command: command.clone(),
            target,
        });
        Ok(Some(command))
    }
    /// 同じ命令を発行後5秒だけ再送する。結果待ちは別の35秒期限まで維持する。
    pub fn commands(&self, now: Instant, utc: DateTime<Utc>) -> Vec<Value> {
        self.pending
            .as_ref()
            .filter(|p| {
                self.available(now)
                    && self
                        .current
                        .as_ref()
                        .is_some_and(|r| r.data.context == p.context)
                    && now.saturating_duration_since(p.issued) < COMMAND_TTL
                    && utc >= p.utc
                    && utc < p.utc + UtcDuration::seconds(5)
            })
            .map(|p| vec![p.command.clone()])
            .unwrap_or_default()
    }
    /// 自分の命令ID・種類・body/epochと発行後35秒内の終端だけを一度受理する。
    /// 受付を成功へ昇格させず、移動はarrived・その場の注視はfacing_targetを待つ。ACKは共有台帳が担当する。
    pub fn observe_results(
        &mut self,
        incoming: &[AdapterCommandResult],
        now: Instant,
        utc: DateTime<Utc>,
    ) -> Vec<Value> {
        self.expire(now, Some(utc));
        let mut out = std::mem::take(&mut self.diagnostics);
        // 古い姿勢や制御変更の後に届いた結果で、次の世代の訪問済み対象を増やさない。
        if !self.available(now) {
            self.cancel();
            return out;
        }
        for result in incoming {
            let Some(pending) = &self.pending else {
                break;
            };
            if result.command_id != pending.id
                || serde_json::to_value(result.command_type).ok().as_ref()
                    != Some(&json!(COMMAND_TYPE))
                || result.extra.get("body_id").and_then(Value::as_str)
                    != Some(pending.context.body_id.as_str())
                || result.extra.get("epoch").and_then(Value::as_str)
                    != Some(pending.context.epoch.as_str())
            {
                continue;
            }
            let executed = result.executed_at;
            if executed < pending.utc
                || executed >= pending.utc + UtcDuration::seconds(35)
                || executed > utc
            {
                continue;
            }
            if result.status == AdapterCommandResultStatus::Succeeded
                && result.detail_code != if pending.command["purpose"] == "look_owner" { "facing_target" } else { "arrived" }
            {
                continue;
            }
            out.push(json!({"command_id":pending.id,"type":COMMAND_TYPE,"status":result.status,"detail_code":result.detail_code,
                "executed_at":result.executed_at,"context":pending.context}));
            if result.status == AdapterCommandResultStatus::Succeeded
                && pending.command["purpose"] == "owner_near"
                && self.current.as_ref().is_some_and(|r| Self::owner_arrival_holding(&r.data)) {
                // 自分が発行した接近の実到着だけを使う。顔・見物の到着や単なる受付は次の相談を作らない。
                self.owner_arrival_pending = true;
            }
            if result.status == AdapterCommandResultStatus::Succeeded && let Some(target) = &pending.target {
                // owner接近の成功は見物の訪問記録へ混ぜず、安心度も身体の次回実測まで更新しない。
                // idは元の実対象。c1等の一回限りのモデルIDや種類名では別個体を巻き込んでしまう。
                self.visits.retain(|v| v.id != target.id);
                self.visits.push_back(Visit { id: target.id.clone(), kind: target.kind,
                    type_id: target.type_id.clone(), arrived: now });
                while self.visits.len() > VISIT_LIMIT { self.visits.pop_front(); }
            }
            self.pending = None;
        }
        out
    }
    /// 入力優先・制御変更時に待機命令と生成中の選択を取消す。候補・選択履歴は保持する。
    pub fn cancel_preparation(&mut self) {
        self.prepared = None;
    }
    pub fn cancel(&mut self) {
        self.pending = None;
        self.prepared = None;
    }
}
