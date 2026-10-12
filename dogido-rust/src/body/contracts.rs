//! HTTP身体観測v2の閉じた契約。座標はblock、yaw/pitchは度、ageは受信からのms。
//! GameEvent入口がvalidateを呼ぶ。欠損値は作らず、未取得はUnavailableで表す。
//! 姿勢のserver実測と身体の眼による視認は別の根拠で保持し、視覚だけ未取得でも姿勢は受理する。
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum Observation {
    Observed {
        version: u8,
        pose_basis: PoseBasis,
        // 詳細な観測がない通常eventまで大きくならないよう、同じJSONのsampleを間接保持する。
        sample: Box<Sample>,
        age_ms: u64,
    },
    Unavailable {
        version: u8,
        reason: String,
    },
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PoseBasis {
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
    pub vision: Vision,
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
    pub view_yaw: f64,
    pub view_pitch: f64,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PlayerPose {
    pub position: Position,
    pub yaw: f64,
    pub pitch: f64,
}
/// 同じsampleのtickで身体の眼から取得した可視性。範囲はblock、視野角は水平・垂直の全角度。
/// 姿勢が正常でも視認不能の理由をUnavailableで保持し、座標取得を可視へ読み替えない。
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Vision {
    pub basis: VisionBasis,
    pub range_blocks: f64,
    pub horizontal_fov_degrees: f64,
    pub vertical_fov_degrees: f64,
    pub player_visibility: PlayerVisibility,
    pub reason: VisibilityReason,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VisionBasis {
    BodyEyeRaycastFov,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PlayerVisibility {
    Visible,
    NotVisible,
    Unavailable,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VisibilityReason {
    InView,
    OutOfRange,
    OutsideFov,
    Occluded,
    AreaUnloaded,
    BodyDowned,
    OwnerUnavailable,
    InvalidPose,
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
fn valid_yaw(value: f64) -> bool {
    value.is_finite() && (-180.0..180.0).contains(&value)
}
fn valid_pitch(value: f64) -> bool {
    value.is_finite() && (-90.0..=90.0).contains(&value)
}
impl Vision {
    /// 採取側で固定した範囲を契約上限内で確認し、可視性と理由の食い違いを拒否する。
    fn valid(&self) -> bool {
        [
            (self.range_blocks, 64.0),
            (self.horizontal_fov_degrees, 360.0),
            (self.vertical_fov_degrees, 180.0),
        ]
        .iter()
        .all(|(value, maximum)| value.is_finite() && *value > 0.0 && value <= maximum)
            && matches!(
                (self.player_visibility, self.reason),
                (PlayerVisibility::Visible, VisibilityReason::InView)
                    | (
                        PlayerVisibility::NotVisible,
                        VisibilityReason::OutOfRange
                            | VisibilityReason::OutsideFov
                            | VisibilityReason::Occluded
                    )
                    | (
                        PlayerVisibility::Unavailable,
                        VisibilityReason::AreaUnloaded
                            | VisibilityReason::BodyDowned
                            | VisibilityReason::OwnerUnavailable
                            | VisibilityReason::InvalidPose
                    )
            )
    }
}
impl Observation {
    /// 形式変換後に範囲とUUIDを確認。owner本人との照合・鮮度・順序は接続のStateが担当する。
    pub fn validate(&self) -> Result<(), String> {
        let valid = match self {
            Self::Unavailable { version, reason } => {
                *version == 2
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
                *version == 2
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
                    && valid_yaw(s.self_pose.yaw)
                    && valid_yaw(s.self_pose.view_yaw)
                    && valid_pitch(s.self_pose.view_pitch)
                    && valid_yaw(s.player_pose.yaw)
                    && valid_pitch(s.player_pose.pitch)
                    && s.vision.valid()
            }
        };
        if valid {
            Ok(())
        } else {
            Err("body_observation: invalid version, identity, pose or vision".into())
        }
    }
}
