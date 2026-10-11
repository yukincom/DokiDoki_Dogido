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
    /// 実player UUID。旧adapterは省略可。送信する場合はcanonical UUIDをHTTP入口で検証する。
    #[serde(default, deserialize_with = "player_uuid")]
    pub player_uuid: Option<String>,
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

fn player_uuid<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    let value = Option::<String>::deserialize(d)?;
    if value
        .as_deref()
        .is_some_and(|s| !crate::body::contracts::canonical_uuid(s))
    {
        return Err(serde::de::Error::custom(
            "player_uuid must be canonical UUID",
        ));
    }
    Ok(value)
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
    #[serde(default)]
    pub voice_generation: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VoiceModeRequest {
    pub session_id: String,
    pub enabled: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DisplayedRequest {
    pub session_id: String,
    pub display_ids: Vec<String>,
}

#[derive(Default, Deserialize)]
pub struct SnapshotQuery {
    pub session_id: Option<String>,
}

#[derive(Deserialize)]
pub struct WorkshopQuery {
    pub session_id: String,
}

#[cfg(test)]
mod body_registration_tests {
    use super::*;
    #[test]
    fn body_legacy_session_and_invalid_registration_contract() {
        let mut registration = serde_json::json!({"adapter_name":"fabric","adapter_version":"1","schema_version":"2026-05-24","player_name":"表示名"});
        let old: SessionRequest = serde_json::from_value(registration.clone()).unwrap();
        assert!(old.player_uuid.is_none());
        registration["player_uuid"] = serde_json::json!("表示名");
        assert!(serde_json::from_value::<SessionRequest>(registration.clone()).is_err());
        registration["player_uuid"] = serde_json::json!("11111111-1111-4111-8111-111111111111");
        assert!(
            serde_json::from_value::<SessionRequest>(registration)
                .unwrap()
                .player_uuid
                .is_some()
        );
    }
}
