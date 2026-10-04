//! FabricのイベントJSONを型付き観測へ変換し、判断器へ渡す受信入口。
//! wireで既知fieldの値を変換し、modelsで型・範囲、semanticでfieldの組合せを検査する。
//! GameEventは検査成功後だけ作り、所持品の元の並びも内部metadataとして保持する。
//! 重複の判定・観測鮮度・戦況更新・音声配送は、受理後のSessionと各判断器が担当する。
mod models;
mod semantic;
mod wire;

pub use models::*;
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;
use std::ops::Deref;
pub use wire::EventTime;

trait Validate {
    fn validate(&self) -> Result<(), String>;
}
impl<T: Validate> Validate for Option<T> {
    fn validate(&self) -> Result<(), String> {
        self.as_ref().map_or(Ok(()), Validate::validate)
    }
}
impl<T: Validate> Validate for Vec<T> {
    fn validate(&self) -> Result<(), String> {
        self.iter().try_for_each(Validate::validate)
    }
}
fn ensure(condition: bool, message: &str) -> Result<(), String> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}

/// 受信契約を通過した観測。内部のEventDataはDerefで読めるが、直接の書換えは公開しない。
#[derive(Clone, Debug, Serialize)]
#[serde(transparent)]
pub struct GameEvent(EventData, #[serde(skip)] Vec<String>);

impl GameEvent {
    /// Original validated wire inventory key order, before the typed BTreeMap.
    /// Internal observation metadata; never serialized into the adapter schema.
    pub fn inventory_order(&self) -> &[String] {
        &self.1
    }

    /// A read projection for material selection; the accepted observation stays intact.
    pub(crate) fn retaining_passive_mobs(&self, predicate: impl Fn(&PassiveMob) -> bool) -> Self {
        let mut data = self.0.clone();
        data.passive_mobs.retain(predicate);
        Self(data, self.1.clone())
    }

    /// Keep world observations while another conversation owner handles this input.
    pub(crate) fn without_player_input(&self) -> Self {
        let mut data = self.0.clone();
        data.meta.user_text = None;
        Self(data, self.1.clone())
    }

    /// 元JSONの所持品順を取り出し、型変換と再帰的検証に成功した場合だけGameEventを返す。
    /// 不正な入力は理由文字列で拒否し、未検査のEventDataを通常の観測処理へ渡さない。
    pub fn parse(value: Value) -> Result<Self, String> {
        let inventory_order = value
            .get("inventory")
            .and_then(Value::as_object)
            .map(|items| items.keys().cloned().collect())
            .unwrap_or_default();
        let data: EventData = serde_json::from_value(value).map_err(|e| e.to_string())?;
        data.validate()?;
        Ok(Self(data, inventory_order))
    }
}
impl Deref for GameEvent {
    type Target = EventData;
    fn deref(&self) -> &EventData {
        &self.0
    }
}
impl<'de> Deserialize<'de> for GameEvent {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Self::parse(Value::deserialize(deserializer)?).map_err(serde::de::Error::custom)
    }
}

/// イベント一括受付の外形。各要素にもGameEventの同じ受信検証を適用する。
#[derive(Deserialize)]
pub struct BatchEvents {
    #[serde(default)]
    pub events: Vec<GameEvent>,
}
