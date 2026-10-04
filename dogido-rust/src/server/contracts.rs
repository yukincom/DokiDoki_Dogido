//! 接続開始・heartbeat・player入力など、HTTP入口の要求型。
//! 観測本体はevents、会話や操作の採否は各runtimeが検証し、この型は受付時の外形を表す。
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
    // 接続メタデータの追加項目を保持する。観測capabilitiesと実行権限は専用フィールドへ分離する。
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
