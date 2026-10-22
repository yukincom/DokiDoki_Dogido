//! HTTP body_autonomy v2の閉じた契約。UUID・座標・種類は採取側の値として保持する。
//! 同一eventの身体実測との照合と鮮度判定はState、移動先の余白と安全確認はFabricが担当する。
use crate::body::contracts::{Context, Position, canonical_uuid};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

pub const MAX_CANDIDATES: usize = 24;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum Observation {
    Observed(Box<Observed>),
    Unavailable { version: u8, reason: String },
}

/// observedの全項目はstatusと同じ階層。距離はblock、ageは採取から送信までのms。
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Observed {
    pub version: u8,
    pub context: Context,
    pub server_tick: u64,
    pub snapshot_revision: u64,
    pub age_ms: u64,
    pub enabled: bool,
    pub mode: Mode,
    pub follow_distance: f64,
    pub warp_distance: f64,
    pub reassurance: Reassurance,
    pub attention: Attention,
    pub activity: Activity,
    pub reason: String,
    pub candidates: Vec<Candidate>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub action_result: Option<ActionResult>,
}

/// 同じ身体frameで実際に適用した注意範囲。視野の変化と世界の種類変化を区別する材料。
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Attention {
    pub scope: AttentionScope,
    pub expansion: f64,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AttentionScope { TargetOnly, Narrow, Ambient, Ordinary }

/// 身体server tickで測った安心度。値は0〜100、距離はblock。命令の受付・成功から補完しない。
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Reassurance {
    pub value: f64,
    pub owner_distance: f64,
    pub owner_visible: bool,
    pub face_visible: bool,
    pub owner_working: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Direct,
    Follow,
    Explore,
    Hold,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Activity {
    Unbound,
    OwnerAbsent,
    Holding,
    Following,
    Exploring,
    Calling,
    Retreating,
    Moving,
    Resting,
    Downed,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum CandidateKind {
    Entity,
    Block,
}

/// idはentityならe:UUID、blockならb:x,y,z:type_id。block位置はセル中心に固定する。
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Candidate {
    pub id: String,
    pub kind: CandidateKind,
    pub type_id: String,
    pub label: String,
    pub position: Position,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub entity_id: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Accepted,
    Succeeded,
    Rejected,
    Interrupted,
    Expired,
}

/// acceptedは開始受付であり、到着ではない。終端の消費はadapterの命令結果だけで行う。
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ActionResult {
    pub action_id: String,
    pub sequence: u64,
    pub outcome: Outcome,
    pub reason: String,
}

pub(crate) fn resource_id(value: &str) -> bool {
    let Some((namespace, path)) = value.split_once(':') else {
        return false;
    };
    !namespace.is_empty()
        && !path.is_empty()
        && value.len() <= 128
        && namespace
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"_.-".contains(&b))
        && path
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"_/.-".contains(&b))
}
pub(crate) fn position_valid(p: Position) -> bool {
    [p.x, p.y, p.z]
        .iter()
        .all(|v| v.is_finite() && v.abs() <= 30_000_000.0)
}
fn code(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}
pub(crate) fn short_text(value: &str, limit: usize) -> bool {
    !value.trim().is_empty()
        && value.chars().count() <= limit
        && !value.chars().any(char::is_control)
}
pub(crate) fn distance_squared(a: Position, b: Position) -> f64 {
    (a.x - b.x).powi(2) + (a.y - b.y).powi(2) + (a.z - b.z).powi(2)
}
impl Candidate {
    /// 種類ごとの識別子を実座標またはUUIDと照合し、表示名と有限な位置の上限を確認する。
    pub fn validate(&self) -> Result<()> {
        ensure!(
            resource_id(&self.type_id) && short_text(&self.label, 80),
            "body_autonomy_candidate_text"
        );
        ensure!(
            position_valid(self.position) && self.id.len() <= 192,
            "body_autonomy_candidate_position"
        );
        match self.kind {
            CandidateKind::Entity => {
                let entity = self.entity_id.as_deref().unwrap_or("");
                ensure!(
                    canonical_uuid(entity) && self.id == format!("e:{entity}"),
                    "body_autonomy_entity_identity"
                );
            }
            CandidateKind::Block => {
                ensure!(self.entity_id.is_none(), "body_autonomy_block_entity");
                let p = self.position;
                ensure!(
                    [p.x, p.y, p.z].iter().all(|v| *v == v.floor() + 0.5),
                    "body_autonomy_block_center"
                );
                let expected = format!(
                    "b:{},{},{}:{}",
                    p.x.floor() as i64,
                    p.y.floor() as i64,
                    p.z.floor() as i64,
                    self.type_id
                );
                ensure!(self.id == expected, "body_autonomy_block_identity");
            }
        }
        Ok(())
    }
}
impl Observation {
    /// 型変換後にも上限を確認する。候補IDの重複や別種類への偽装は入力全体を拒否する。
    pub fn validate(&self) -> Result<()> {
        let s = match self {
            Self::Unavailable { version, reason } => {
                ensure!(*version == 2 && code(reason), "body_autonomy_unavailable");
                return Ok(());
            }
            Self::Observed(s) => s,
        };
        ensure!(s.version == 2, "body_autonomy_version");
        ensure!(
            [s.server_tick, s.snapshot_revision, s.age_ms]
                .iter()
                .all(|v| *v <= i64::MAX as u64),
            "body_autonomy_counter"
        );
        let c = &s.context;
        ensure!(
            [&c.owner_id, &c.body_id, &c.epoch]
                .iter()
                .all(|v| canonical_uuid(v))
                && resource_id(&c.dimension),
            "body_autonomy_context"
        );
        ensure!(
            s.follow_distance.is_finite()
                && s.follow_distance > 0.0
                && s.follow_distance <= 64.0
                && s.warp_distance.is_finite()
                && s.warp_distance >= s.follow_distance
                && s.warp_distance <= 128.0,
            "body_autonomy_distances"
        );
        ensure!(
            code(&s.reason) && s.candidates.len() <= MAX_CANDIDATES,
            "body_autonomy_candidate_limit"
        );
        ensure!(
            s.reassurance.value.is_finite()
                && (0.0..=100.0).contains(&s.reassurance.value)
                && s.reassurance.owner_distance.is_finite()
                && s.reassurance.owner_distance >= 0.0
                && (!s.reassurance.face_visible || s.reassurance.owner_visible),
            "body_autonomy_reassurance"
        );
        ensure!(s.attention.expansion.is_finite() && (0.0..=1.0).contains(&s.attention.expansion)
            && match s.attention.scope {
                AttentionScope::Narrow => s.attention.expansion == 0.0,
                AttentionScope::TargetOnly | AttentionScope::Ordinary => s.attention.expansion == 1.0,
                AttentionScope::Ambient => true,
            }, "body_autonomy_attention");
        let mut ids = HashSet::new();
        for candidate in &s.candidates {
            candidate.validate()?;
            ensure!(
                ids.insert(candidate.id.as_str()),
                "body_autonomy_duplicate_candidate"
            );
        }
        if let Some(result) = &s.action_result {
            ensure!(
                canonical_uuid(&result.action_id)
                    && result.sequence <= i64::MAX as u64
                    && code(&result.reason),
                "body_autonomy_action_result"
            );
        }
        Ok(())
    }
}
