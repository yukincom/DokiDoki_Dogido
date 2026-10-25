//! FabricのイベントJSONを型付き観測へ変換し、判断器へ渡す受信入口。
//! modelsでJSONの型・範囲、wireで日時表記、semanticでfieldの組合せを検査する。
//! GameEventは検査成功後だけ作り、所持品の元の並びも内部metadataとして保持する。
//! 重複の判定・観測鮮度・戦況更新・音声配送は、受理後のSessionと各判断器が担当する。
mod models;
mod semantic;
mod wire;

pub use models::*;
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;
use std::ops::Deref;

pub(crate) trait Validate {
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
pub struct GameEvent(
    EventData,
    #[serde(skip)] Vec<String>,
    #[serde(skip)] Option<crate::body::senses::Origin>,
);

impl GameEvent {
    /// 撮影待機へ画像を渡した後、観測の複製・保持から一時画像だけを解放する。
    pub(crate) fn discard_command_images(&mut self) {
        for result in &mut self.0.command_results { result.body_view = None; }
    }

    /// 受理済み身体状態が作る感覚用コピー。player/combat/look_targetは元の意味を保つ。
    /// 身体の欠損は空の感覚と未取得metadataになり、playerの世界観測を補完に使わない。
    pub(crate) fn with_body_senses(
        &self,
        senses: Option<&crate::body::senses::Sample>,
        origin: crate::body::senses::Origin,
    ) -> Self {
        let mut data = self.0.clone();
        data.world = senses.map(|s| s.world.clone()).unwrap_or_default();
        data.passive_mobs = senses.map(|s| s.passive_mobs.clone()).unwrap_or_default();
        data.visual_threats = senses.map(|s| s.visual_threats.clone()).unwrap_or_default();
        data.nearby_resources = senses
            .map(|s| s.nearby_resources.clone())
            .unwrap_or_default();
        data.dropped_items = senses.map(|s| s.dropped_items.clone()).unwrap_or_default();
        data.nearby_composition = senses.map(|s| s.nearby_composition.clone());
        data.smell_observation = senses.map(|s| s.smell_observation.clone());
        data.auditory_threats = senses
            .map(|s| s.auditory_threats.clone())
            .unwrap_or_default();
        data.ambient_sounds = senses.map(|s| s.ambient_sounds.clone()).unwrap_or_default();
        data.companion_candidates.clear();
        // 時刻を現在へ揃え、実際の音の経過時間には身体snapshotの年齢も加える。
        // 同じ音の再送で「今聞いた」へ若返らせない。
        data.observed_at = chrono::Utc::now().fixed_offset();
        let age = origin.age_ms.min(i64::MAX as u64) as i64;
        for sound in &mut data.auditory_threats {
            sound.heard_ago_ms = Some(sound.heard_ago_ms.unwrap_or(0).saturating_add(age));
        }
        for sound in &mut data.ambient_sounds {
            sound.heard_ago_ms = Some(sound.heard_ago_ms.unwrap_or(0).saturating_add(age));
        }
        for value in [
            &mut data.world.rain_sound_recent_ms,
            &mut data.world.thunder_sound_recent_ms,
            &mut data.world.ominous_sound_recent_ms,
            &mut data.world.nearby_lightning_strike_recent_ms,
            &mut data.world.ender_eye_launch_recent_ms,
        ]
        .into_iter()
        .flatten()
        {
            *value = value.saturating_add(age);
        }
        Self(data, self.1.clone(), Some(origin))
    }
    /// 身体の観測者は内部でのみ付く。HTTP入力が同名の値を送ってもこのmetadataにはならない。
    pub(crate) fn body_origin(&self) -> Option<&crate::body::senses::Origin> {
        self.2.as_ref()
    }
    /// 共通観測の受け手本人の接触。身体が未測定ならplayerの値へ戻さずNoneを返す。
    pub(crate) fn observer_environment(&self) -> Option<&MobEnvironment> {
        match self.body_origin() {
            Some(origin) => origin.environment.as_ref(),
            None => self.player.environment.as_ref(),
        }
    }
    pub(crate) fn observer_y(&self) -> Option<f64> {
        self.2
            .as_ref()
            .map(|o| o.position.map(|p| p.y))
            .unwrap_or(self.player.position.y)
    }
    /// プレイヤーの酸素・復活点・足元危険は内部に分離し、身体のworldへ混ぜない。
    pub(crate) fn player_safety_world(&self) -> &WorldState {
        self.2
            .as_ref()
            .map(|o| &o.player_safety)
            .unwrap_or(&self.world)
    }
    pub(crate) fn observer_x(&self) -> Option<f64> {
        self.2
            .as_ref()
            .map(|o| o.position.map(|p| p.x))
            .unwrap_or(self.player.position.x)
    }
    pub(crate) fn observer_z(&self) -> Option<f64> {
        self.2
            .as_ref()
            .map(|o| o.position.map(|p| p.z))
            .unwrap_or(self.player.position.z)
    }
    /// Original validated wire inventory key order, before the typed BTreeMap.
    /// Internal observation metadata; never serialized into the adapter schema.
    pub fn inventory_order(&self) -> &[String] {
        &self.1
    }

    /// A read projection for material selection; the accepted observation stays intact.
    pub(crate) fn retaining_passive_mobs(&self, predicate: impl Fn(&PassiveMob) -> bool) -> Self {
        let mut data = self.0.clone();
        data.passive_mobs.retain(predicate);
        Self(data, self.1.clone(), self.2.clone())
    }

    /// Per-turn subject priority; never mutates accepted observations or ambient selection.
    pub(crate) fn with_passive_mobs(&self, mobs: Vec<PassiveMob>) -> Self {
        let mut data = self.0.clone();
        data.passive_mobs = mobs;
        Self(data, self.1.clone(), self.2.clone())
    }

    /// 元JSONの所持品順を取り出し、型・範囲と再帰的検証に成功した場合だけGameEventを返す。
    /// JSON numberはserde_jsonで有限値に限られ、整数・実数・真偽値はmodelsの型で照合する。
    /// 不正な入力は理由文字列で拒否し、未検査のEventDataを通常の観測処理へ渡さない。
    pub fn parse(value: Value) -> Result<Self, String> {
        let inventory_order = value
            .get("inventory")
            .and_then(Value::as_object)
            .map(|items| items.keys().cloned().collect())
            .unwrap_or_default();
        let data: EventData = serde_json::from_value(value).map_err(|e| e.to_string())?;
        data.validate()?;
        Ok(Self(data, inventory_order, None))
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

#[cfg(test)]
mod tests;
