//! Local precipitation resolved from current altitude, catalog climate and world weather.
//! The full context contains internal numbers; only to_prompt_details is prompt material.
use crate::events::{GameEvent, Weather};
use anyhow::{Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::Number;

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Input {
    pub current_y: Option<f64>,
    pub biome_temperature: Option<f64>,
    pub snow_start_y: Option<i64>,
    pub biome_group_id: String,
    pub biome_downfall: Option<f64>,
    pub weather: String,
    pub dimension: Option<String>,
}

/// Values already resolved by the existing biome catalog. Unknown values stay None.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Climate {
    pub biome_temperature: Option<f64>,
    pub snow_start_y: Option<i64>,
    pub biome_group_id: String,
    pub biome_downfall: Option<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PrecipitationKind {
    Snow,
    Rain,
    None,
    Unknown,
}
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct PrecipitationContext {
    // Number retains integral JSON representation without silently saturating large finite Y.
    pub current_y: Option<Number>,
    pub biome_temperature: Option<f64>,
    pub snow_start_y: Option<i64>,
    pub snowfall_zone: Option<bool>,
    pub precipitation_kind: PrecipitationKind,
    pub precipitation_possible: bool,
    pub thunder_active: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct PromptDetails {
    pub precipitation_kind: PrecipitationKind,
}

impl PrecipitationContext {
    /// 表示と川柳の規則の両方で、ワールド天候より観測地点の降水を優先する。
    pub(crate) fn weather_key(&self, world_weather: Option<Weather>) -> &'static str {
        match (self.precipitation_kind, world_weather) {
            (PrecipitationKind::Snow, _) => "snow",
            (PrecipitationKind::Rain, Some(Weather::Thunder)) => "thunder",
            (PrecipitationKind::Rain, _) => "rain",
            (PrecipitationKind::None, Some(Weather::Clear)) => "clear",
            (PrecipitationKind::None, _) => "none",
            _ => "unknown",
        }
    }

    pub fn to_prompt_details(&self) -> PromptDetails {
        PromptDetails {
            precipitation_kind: self.precipitation_kind,
        }
    }
}

fn rounded_y(value: f64) -> Result<Number> {
    if value.is_nan() {
        bail!("ValueError: cannot convert float NaN to integer");
    }
    if !value.is_finite() {
        bail!("OverflowError: cannot convert float infinity to integer");
    }
    let rounded = value.round_ties_even(); // Python int(round(y)), including negative half ties.
    if rounded == 0.0 {
        return Ok(0.into());
    }
    let integral = format!("{rounded:.0}");
    if let Ok(value) = integral.parse::<i64>() {
        return Ok(value.into());
    }
    if let Ok(value) = integral.parse::<u64>() {
        return Ok(value.into());
    }
    // serde_json has no arbitrary precision integer; preserve the finite f64
    // instead of saturating to i64 or rejecting a finite Python coordinate.
    Ok(Number::from_f64(rounded).expect("finite rounded coordinate"))
}
fn at_or_above(y: &Number, threshold: i64) -> bool {
    if let Some(value) = y.as_i64() {
        value >= threshold
    } else if let Some(value) = y.as_u64() {
        threshold < 0 || value >= threshold as u64
    } else {
        y.as_f64().expect("finite rounded Y").is_sign_positive()
    }
}
fn remove_namespace_lower(value: &str) -> String {
    value
        .strip_prefix("minecraft:")
        .unwrap_or(value)
        .to_lowercase()
}

/// Exact closed rules from state_machine.precipitation.resolve_precipitation_context.
/// Sky visibility and shelter are separate facts; they do not turn ongoing precipitation into clear weather.
pub fn resolve_precipitation_context(input: &Input) -> Result<PrecipitationContext> {
    let current_y = input.current_y.map(rounded_y).transpose()?;
    let dimension = remove_namespace_lower(input.dimension.as_deref().unwrap_or(""));
    let weather = input.weather.to_lowercase();
    let group = input.biome_group_id.to_lowercase();
    let disabled = matches!(dimension.as_str(), "the_nether" | "the_end")
        || group == "dry"
        || input.biome_downfall.is_some_and(|n| n <= 0.0);
    let snowfall_zone = if disabled {
        Some(false)
    } else if let Some(threshold) = input.snow_start_y {
        current_y.as_ref().map(|y| at_or_above(y, threshold))
    } else {
        input
            .biome_temperature
            .map(|temperature| temperature < 0.15)
    };
    let precipitation_kind = if weather == "clear" || disabled {
        PrecipitationKind::None
    } else if matches!(weather.as_str(), "rain" | "thunder") {
        match snowfall_zone {
            Some(true) => PrecipitationKind::Snow,
            Some(false) => PrecipitationKind::Rain,
            None => PrecipitationKind::Unknown,
        }
    } else {
        PrecipitationKind::Unknown
    };
    Ok(PrecipitationContext {
        current_y,
        biome_temperature: input.biome_temperature,
        snow_start_y: input.snow_start_y,
        snowfall_zone,
        precipitation_kind,
        precipitation_possible: !disabled,
        thunder_active: weather == "thunder" && !disabled,
    })
}

/// 現在イベントと呼出側が取得した気候を、降水判定への入力に投影する。
/// カタログ検索・状態変更は行わない。
pub fn from_event(event: &GameEvent, climate: &Climate) -> Result<PrecipitationContext> {
    from_observation(
        &event.world,
        event.body_origin().and_then(|o| o.context.as_ref()).map(|c| c.dimension.as_str())
            .or(event.player.dimension.as_deref()),
        event.observer_y(),
        climate,
    )
}


/// 一人分の世界・高さ・気候を既存の降水判定へ渡す。主体を跨いで値を補完しない。
pub fn from_observation(
    world: &crate::events::WorldState,
    dimension: Option<&str>,
    current_y: Option<f64>,
    climate: &Climate,
) -> Result<PrecipitationContext> {
    resolve_precipitation_context(&Input {
        current_y,
        biome_temperature: climate.biome_temperature,
        snow_start_y: climate.snow_start_y,
        biome_group_id: climate.biome_group_id.clone(),
        biome_downfall: climate.biome_downfall,
        weather: match world.weather {
            Some(Weather::Clear) => "clear",
            Some(Weather::Rain) => "rain",
            Some(Weather::Thunder) => "thunder",
            None => "unknown",
        }
        .into(),
        dimension: dimension.map(str::to_owned),
    })
}


#[cfg(test)]
#[path = "precipitation_tests.rs"]
mod tests;
