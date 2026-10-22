//! 通常会話の任意画像を待つ設定。既定値はPython起動と共通のruntime_defaultsだけから読む。
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub enabled: bool,
    pub stop_reactions_enabled: bool,
    /// 初回の静止画像が未受信の場合の待機上限ミリ秒。追加撮影はせず、時間切れは文字会話へ戻す。
    pub wait_ms: u64,
}
impl Default for Settings {
    fn default() -> Self {
        let all: serde_json::Value = serde_json::from_str(include_str!("../../dogido_server/runtime_defaults.json")).expect("runtime defaults");
        Self { enabled: all["chat_image"]["enabled"].as_bool().expect("chat image enabled"),
            stop_reactions_enabled: all["chat_image"]["stop_reactions_enabled"].as_bool().expect("stop image reactions"),
            wait_ms: all["chat_image"]["wait_ms"].as_u64().expect("chat image wait") }
    }
}
impl Settings {
    pub fn validate(&self) -> Result<()> {
        ensure!((100..=5000).contains(&self.wait_ms), "chat image wait_ms must be 100..5000");
        Ok(())
    }
}
