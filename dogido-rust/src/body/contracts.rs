//! HTTP身体観測v1の閉じた契約。座標はblock、yawは度、ageは受信からのms。
//! GameEvent入口がvalidateを呼ぶ。欠損値は作らず、未取得はUnavailableで表す。
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum Observation {
    Observed {
        version: u8,
        basis: Basis,
        sample: Sample,
        age_ms: u64,
    },
    Unavailable {
        version: u8,
        reason: String,
    },
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Basis {
    ServerPose,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Sample {
    pub context: Context,
    pub server_tick: u64,
    pub snapshot_revision: u64,
    pub self_pose: SelfPose,
    pub player_pose: PlayerPose,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub owner_id: String,
    pub body_id: String,
    pub epoch: String,
    pub dimension: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SelfPose {
    pub position: Position,
    pub eye_position: Position,
    pub yaw: f64,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PlayerPose {
    pub position: Position,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Position {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

pub fn canonical_uuid(value: &str) -> bool {
    uuid::Uuid::parse_str(value).is_ok_and(|id| id.to_string() == value)
}
impl Position {
    fn valid(self) -> bool {
        [self.x, self.y, self.z]
            .iter()
            .all(|n| n.is_finite() && n.abs() <= 30_000_000.0)
    }
}
impl Observation {
    /// 形式変換後に範囲とUUIDを確認。owner本人との照合・鮮度・順序は接続のStateが担当する。
    pub fn validate(&self) -> Result<(), String> {
        let valid = match self {
            Self::Unavailable { version, reason } => {
                *version == 1
                    && !reason.is_empty()
                    && reason.len() <= 64
                    && reason.bytes().all(|b| b.is_ascii_lowercase() || b == b'_')
            }
            Self::Observed {
                version,
                sample: s,
                age_ms,
                ..
            } => {
                let c = &s.context;
                *version == 1
                    && *age_ms <= i64::MAX as u64
                    && s.server_tick <= i64::MAX as u64
                    && s.snapshot_revision <= i64::MAX as u64
                    && [&c.owner_id, &c.body_id, &c.epoch]
                        .iter()
                        .all(|v| canonical_uuid(v))
                    && !c.dimension.is_empty()
                    && c.dimension.len() <= 128
                    && c.dimension.contains(':')
                    && c.dimension.bytes().all(|b| {
                        b.is_ascii_lowercase() || b.is_ascii_digit() || b"_:/.-".contains(&b)
                    })
                    && s.self_pose.position.valid()
                    && s.self_pose.eye_position.valid()
                    && s.player_pose.position.valid()
                    && s.self_pose.yaw.is_finite()
                    && (-180.0..180.0).contains(&s.self_pose.yaw)
            }
        };
        if valid {
            Ok(())
        } else {
            Err("body_observation: invalid version, identity or measurements".into())
        }
    }
}
