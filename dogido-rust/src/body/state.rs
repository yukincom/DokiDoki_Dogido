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
    /// 会話開始時の不変コピー。UUIDは運搬照合用なのでモデルへ渡さず、実測と座標規約だけを出す。
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
            json!({"status":"observed","basis":"server_pose","dimension":s.context.dimension,
            "coordinate_system":{"position_unit":"block","x_positive":"east","y_positive":"up","z_positive":"south",
                "yaw_unit":"degree","yaw_zero":"south","yaw_positive_90":"west"},
            "age_ms":reading.age(now),"current":raw(s),"previous":self.previous.as_ref().map(raw),
            "sample_interval_ticks":self.previous.as_ref().map(|p| s.server_tick-p.server_tick)}),
        )
    }
}
fn raw(s: &Sample) -> Value {
    json!({"self":{"subject":"ドギド","position":s.self_pose.position,"eye_position":s.self_pose.eye_position,"yaw":s.self_pose.yaw},
        "player":{"subject":"プレイヤー","position":s.player_pose.position}})
}
