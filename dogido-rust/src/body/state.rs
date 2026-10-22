//! 生イベントだけから現在・前回の身体実測を管理する。統合済みplayer観測からは更新しない。
//! Instantによる鮮度とserverの世代・tick・revisionを分け、再送が古い値の寿命を延ばすのを防ぐ。
use super::contracts::{Context, Observation, Sample};
use serde_json::{Value, json};
use std::{collections::HashSet, time::Instant};
const FRESH_MS: u64 = 3000;

#[derive(Clone)]
struct Reading {
    sample: Sample,
    received: Instant,
    age_ms: u64,
}
impl Reading {
    fn age(&self, now: Instant) -> u64 {
        self.age_ms
            .saturating_add(now.saturating_duration_since(self.received).as_millis() as u64)
    }
}
#[derive(Default)]
pub struct State {
    current: Option<Reading>,
    previous: Option<Sample>,
    watermark: Option<Sample>,
    retired: HashSet<Context>,
    unavailable: Option<String>,
    context_limit_reached: bool,
}
impl State {
    /// 同一観測から作った操作候補の照合用。生eventではなく、owner・世代・鮮度を受理済みのsampleを返す。
    pub(crate) fn accepted(&self, now: Instant) -> Option<&Sample> {
        self.current.as_ref().filter(|r| r.age(now) < FRESH_MS).map(|r| &r.sample)
    }

    /// 会話・環境・川柳が使う感覚用コピー。検証済みの現世代と鮮度を満たした感覚だけを渡す。
    /// 元eventのplayer/combat/指差しは変えず、未取得時もプレイヤーの感覚へ戻さない。
    pub(crate) fn sensory_event(
        &self,
        event: &crate::events::GameEvent,
        now: Instant,
    ) -> crate::events::GameEvent {
        let reading = self.current.as_ref().filter(|r| r.age(now) < FRESH_MS);
        let sample = reading.map(|r| &r.sample);
        let senses = sample.and_then(|s| match &s.senses {
            super::senses::Senses::Observed(s) => Some(s.as_ref()),
            super::senses::Senses::Unavailable { .. } => None,
        });
        event.with_body_senses(
            senses,
            super::senses::Origin {
                player_safety: event.world.clone(),
                player_observed_at: event.observed_at.clone(),
                context: sample.map(|s| s.context.clone()),
                revision: sample.map(|s| s.snapshot_revision),
                position: sample.map(|s| s.self_pose.position),
                available: senses.is_some(),
                age_ms: reading.map(|r| r.age(now)).unwrap_or(0),
                body: self
                    .project(now)
                    .unwrap_or_else(|| json!({"status":"unavailable","reason":"not_sampled"})),
            },
        )
    }
    fn clear(&mut self, reason: &str) {
        self.current = None;
        self.previous = None;
        self.unavailable = Some(reason.into());
    }
    /// ownerは登録UUID、dimensionは同じ生eventのplayer世界、transport_ageはevent時計からの経過ms。
    /// 省略は未更新。未取得・世界移動は比較を消すが、重複排除用の最終番号は残す。
    pub fn observe(
        &mut self,
        observation: Option<&Observation>,
        owner: Option<&str>,
        dimension: Option<&str>,
        now: Instant,
        transport_age: Option<u64>,
    ) {
        if self.context_limit_reached {
            return;
        }
        if let Some(current) = &self.current
            && dimension.is_some_and(|d| d != current.sample.context.dimension)
        {
            self.clear("dimension_mismatch");
        }
        let Some(observation) = observation else {
            return;
        };
        let Observation::Observed { sample, age_ms, .. } = observation else {
            if let Observation::Unavailable { reason, .. } = observation {
                self.clear(reason);
            }
            return;
        };
        let sample = sample.as_ref();
        if owner != Some(sample.context.owner_id.as_str()) {
            self.clear("owner_mismatch");
            return;
        }
        if dimension != Some(sample.context.dimension.as_str()) {
            self.clear("dimension_mismatch");
            return;
        }
        let age = transport_age.unwrap_or(u64::MAX).saturating_add(*age_ms);
        if self.retired.contains(&sample.context) {
            return;
        }
        if let Some(old) = &self.watermark {
            if old.context == sample.context {
                if sample.server_tick <= old.server_tick
                    || sample.snapshot_revision <= old.snapshot_revision
                {
                    return;
                }
            } else if age < FRESH_MS {
                // 上限に達した異常な世代増殖も古い世代を再受理しない。接続し直すまで未取得扱い。
                if self.retired.len() >= 256 {
                    self.context_limit_reached = true;
                    self.clear("context_limit");
                    return;
                }
                self.retired.insert(old.context.clone());
            }
        }
        if age >= FRESH_MS {
            self.clear("stale");
            return;
        }
        self.previous = self
            .current
            .as_ref()
            .filter(|r| r.sample.context == sample.context && r.age(now) < FRESH_MS)
            .map(|r| r.sample.clone());
        self.current = Some(Reading {
            sample: sample.clone(),
            received: now,
            age_ms: age,
        });
        self.watermark = Some(sample.clone());
        self.unavailable = None;
    }
    /// 既存の診断snapshotだけへ、受理済み姿勢と系列識別子を一組で返す。
    /// ageは取得からのms。3秒以上は姿勢を返さず、再送や別世代を連続移動として採点するのを防ぐ。
    /// 会話の観測投影・changesとは別の読み取り専用コピーで、保存や状態更新は行わない。
    pub(crate) fn diagnostic_sample(&self, now: Instant) -> Value {
        let Some(reading) = self.current.as_ref().filter(|r| r.age(now) < FRESH_MS) else {
            return json!({"version":1,"status":"unavailable",
                "reason":self.unavailable.as_deref().unwrap_or(if self.current.is_some() {"stale"} else {"not_sampled"})});
        };
        let s = &reading.sample;
        json!({"version":1,"status":"observed","age_ms":reading.age(now),
            "context":s.context,"server_tick":s.server_tick,"snapshot_revision":s.snapshot_revision,
            "self_pose":s.self_pose,"player_pose":s.player_pose,"vision":s.vision})
    }
    /// 内部の差分比較と診断用の不変コピー。モデルへ渡す際はContext::for_modelで姿勢を除く。
    pub fn project(&self, now: Instant) -> Option<Value> {
        let Some(reading) = &self.current else {
            return self
                .unavailable
                .as_ref()
                .map(|reason| json!({"status":"unavailable","reason":reason}));
        };
        if reading.age(now) >= FRESH_MS {
            return Some(json!({"status":"unavailable","reason":"stale"}));
        }
        let s = &reading.sample;
        Some(
            json!({"current":raw(s),"status":"observed","pose_basis":"server_pose","dimension":s.context.dimension,
            "coordinate_system":{"position_unit":"block","x_positive":"east","y_positive":"up","z_positive":"south",
                "yaw_unit":"degree","yaw_zero":"south","yaw_positive_90":"west",
                "pitch_unit":"degree","pitch_positive":"down"},
            "age_ms":reading.age(now),"previous":self.previous.as_ref().map(raw),
            "sample_interval_ticks":self.previous.as_ref().map(|p| s.server_tick-p.server_tick)}),
        )
    }
}
fn raw(s: &Sample) -> Value {
    json!({"vision":s.vision,"senses":s.senses.summary(),"position_relation":super::position::calculate(s),"self":{"subject":"ドギド","position":s.self_pose.position,"eye_position":s.self_pose.eye_position,
            "yaw":s.self_pose.yaw,"view_yaw":s.self_pose.view_yaw,"view_pitch":s.self_pose.view_pitch},
        "player":{"subject":"プレイヤー","position":s.player_pose.position,"yaw":s.player_pose.yaw,"pitch":s.player_pose.pitch}})
}
