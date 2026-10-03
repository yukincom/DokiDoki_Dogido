//! HTTPの上限・接続応答設定。起動時に検証し、実際のrouter/runtimeへ渡す。
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub accepted_schema_version: String,
    pub heartbeat_interval_ms: u64,
    pub max_batch_size: usize,
    pub max_body_kb: usize,
}

impl Default for Settings {
    fn default() -> Self {
        serde_json::from_value(Value::Object(
            crate::runtime_settings::defaults("server").clone(),
        ))
        .expect("checked server defaults")
    }
}

impl Settings {
    pub fn merged(overrides: &Value) -> anyhow::Result<Self> {
        let mut values = crate::runtime_settings::defaults("server").clone();
        values.extend(
            overrides
                .as_object()
                .ok_or_else(|| anyhow::anyhow!("server settings must be an object"))?
                .clone(),
        );
        let settings: Self = serde_json::from_value(Value::Object(values))?;
        anyhow::ensure!(
            !settings.accepted_schema_version.trim().is_empty(),
            "schema version must not be empty"
        );
        anyhow::ensure!(
            settings.heartbeat_interval_ms > 0
                && settings.max_batch_size > 0
                && settings.max_body_kb > 0
                && settings.max_body_kb.checked_mul(1024).is_some(),
            "server heartbeat and limits must be positive and fit in memory sizes"
        );
        Ok(settings)
    }
}
