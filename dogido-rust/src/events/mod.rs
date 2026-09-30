//! Fabric受信契約。外形・範囲・意味検査を通過したイベントだけを公開する。
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

    pub fn parse(mut value: Value) -> Result<Self, String> {
        // 旧名をextraにも残すPythonのbefore-validatorと同じ優先順。
        if let Some(object) = value.as_object_mut()
            && !object.contains_key("passive_mobs")
            && let Some(legacy) = object.get("peaceful_mobs").cloned()
        {
            object.insert("passive_mobs".into(), legacy);
        }
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

#[derive(Deserialize)]
pub struct BatchEvents {
    #[serde(default)]
    pub events: Vec<GameEvent>,
}
