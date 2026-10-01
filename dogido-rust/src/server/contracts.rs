//! 接続段階で移した契約だけ。世界観測・会話・操作の型を代用しない。
pub use crate::ingress::InputSource;
use chrono::{DateTime, FixedOffset};
use serde::Deserialize;
use serde_json::{Map, Value};

#[derive(Clone, Deserialize)]
pub struct SessionRequest {
    pub adapter_name: String,
    pub adapter_version: String,
    #[serde(default = "minecraft")]
    pub game: String,
    pub schema_version: String,
    pub player_name: String,
    pub profile_name: Option<String>,
    pub call_name: Option<String>,
    #[serde(default)]
    pub capabilities: Vec<String>,
    #[serde(default)]
    pub execution_capabilities: Vec<String>,
    // Pythonのextra=allowを維持。観測と実行capabilityは別々に保持する。
    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

fn minecraft() -> String {
    "minecraft-java".into()
}

#[derive(Deserialize)]
pub struct HeartbeatRequest {
    pub last_sequence: Option<u64>,
    pub sent_at: DateTime<FixedOffset>,
}

#[derive(Deserialize)]
pub struct PlayerInputRequest {
    pub session_id: Option<String>,
    pub text: String,
    #[serde(default)]
    pub source: InputSource,
}

#[derive(Default, Deserialize)]
pub struct SnapshotQuery {
    pub session_id: Option<String>,
}

#[derive(Deserialize)]
pub struct WorkshopQuery {
    pub session_id: String,
}
