//! 身体の視覚・環境・嗅覚・聴覚を既存イベント型で受ける閉じた契約。
//! 受信時の検証はここ、owner・世代・鮮度と感覚用コピーの生成はbody::Stateが所有する。
//! プレイヤーの体力・所持品・指差し・戦闘状態はこの観測へ複製しない。
use super::contracts::{Context, Position};
use crate::events::{self, Validate};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum Senses {
    Observed(Box<Sample>),
    Unavailable {
        version: u8,
        basis: Basis,
        source_server_tick: u64,
        reason: UnavailableReason,
    },
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Sample {
    pub version: u8,
    pub basis: Basis,
    pub source_server_tick: u64,
    pub world: events::WorldState,
    pub passive_mobs: Vec<events::PassiveMob>,
    pub visual_threats: Vec<events::VisualThreat>,
    pub nearby_resources: Vec<events::NearbyResource>,
    pub dropped_items: Vec<events::DroppedItem>,
    pub nearby_composition: events::NearbyComposition,
    pub smell_observation: events::SmellObservation,
    pub auditory_threats: Vec<events::AuditoryThreat>,
    pub ambient_sounds: Vec<events::AmbientSound>,
    pub hearing_basis: HearingBasis,
    pub hearing_truncated: bool,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Basis {
    BodyPoseClientWorld,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HearingBasis {
    BodyServerAndPositionedClientSounds,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UnavailableReason {
    AreaUnloaded,
    BodyDowned,
    InvalidPose,
    NotSampled,
    BudgetExhausted,
}

/// 受理済み身体snapshotから作った読み取り用metadata。通信のfieldとしては公開しない。
/// positionは身体の足元、bodyは生成時に診断へ残す同じ現在・前回の投影。
#[derive(Clone, Debug)]
pub(crate) struct Origin {
    pub context: Option<Context>,
    pub revision: Option<u64>,
    pub position: Option<Position>,
    pub available: bool,
    pub age_ms: u64,
    pub body: Value,
    pub player_safety: events::WorldState,
    pub player_observed_at: events::EventTime,
}
impl Senses {
    /// モデルへは採取の状態と根拠だけを渡す。感覚本文は共通observationsで一回だけ投影する。
    pub(crate) fn summary(&self) -> Value {
        match self {
            Self::Observed(s) => json!({"status":"observed","version":s.version,"basis":s.basis,
                "source_server_tick":s.source_server_tick,"hearing_basis":s.hearing_basis,"hearing_truncated":s.hearing_truncated}),
            Self::Unavailable {
                version,
                basis,
                source_server_tick,
                reason,
            } => json!({"status":"unavailable",
                "version":version,"basis":basis,"source_server_tick":source_server_tick,"reason":reason}),
        }
    }
    /// APIの姿勢と感覚が同じtickかを確認し、既存の各観測型の検証も再利用する。
    pub fn validate(&self, expected_tick: u64) -> Result<(), String> {
        let (version, tick) = match self {
            Self::Observed(s) => (s.version, s.source_server_tick),
            Self::Unavailable {
                version,
                source_server_tick,
                ..
            } => (*version, *source_server_tick),
        };
        if version != 1 || tick != expected_tick {
            return Err("body senses version or tick mismatch".into());
        }
        let Self::Observed(s) = self else {
            return Ok(());
        };
        if [
            s.passive_mobs.len(),
            s.visual_threats.len(),
            s.nearby_resources.len(),
            s.dropped_items.len(),
            s.auditory_threats.len(),
            s.ambient_sounds.len(),
        ]
        .into_iter()
        .any(|n| n > 64)
        {
            return Err("body senses list limit".into());
        }
        if s.world.air_supply.is_some()
            || s.world.respawn_point_set.is_some()
            || s.world.respawn_distance.is_some()
            || !s.world.extra.is_empty()
        {
            return Err("body senses world contains player ownership or unknown fields".into());
        }
        if s.auditory_threats
            .iter()
            .any(|sound| sound.heard_ago_ms.is_none())
            || s.ambient_sounds
                .iter()
                .any(|sound| sound.heard_ago_ms.is_none())
        {
            return Err("body hearing requires original age".into());
        }
        s.world.validate()?;
        s.passive_mobs.validate()?;
        s.visual_threats.validate()?;
        s.nearby_resources.validate()?;
        s.dropped_items.validate()?;
        s.nearby_composition.validate()?;
        s.smell_observation.validate()?;
        s.auditory_threats.validate()?;
        s.ambient_sounds.validate()?;
        // 既存型が拡張fieldを保持しても、感覚一件が無制限に会話材料を占めないようにする。
        if serde_json::to_vec(s).map_err(|e| e.to_string())?.len() > 65_536 {
            return Err("body senses encoded size limit".into());
        }
        Ok(())
    }
}
