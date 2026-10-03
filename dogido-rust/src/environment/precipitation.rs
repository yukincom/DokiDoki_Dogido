//! Local precipitation resolved from current altitude, catalog climate and observed blocks.
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
    pub nearby_block_names: Vec<String>,
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
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SnowEvidence {
    ObservedSurface,
    ActiveSnowfall,
    None,
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
    pub surface_snow_observed: bool,
    pub snow_evidence: SnowEvidence,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct PromptDetails {
    pub precipitation_kind: PrecipitationKind,
    pub precipitation_possible: bool,
    pub thunder_active: bool,
    pub snowfall_environment: &'static str,
    pub surface_snow_observed: bool,
    pub weather_context: String,
}

impl PrecipitationContext {
    pub fn snow_can_be_scene_material(&self) -> bool {
        self.snow_evidence != SnowEvidence::None
    }
    pub fn prompt_line(&self) -> String {
        let mut parts = vec![match self.precipitation_kind {
            PrecipitationKind::Snow => "現在の降水は雪",
            PrecipitationKind::Rain => "現在の降水は雨",
            PrecipitationKind::None => "現在は降水なし",
            PrecipitationKind::Unknown => "現在の降水種別は不明",
        }];
        if self.thunder_active {
            parts.push("雷鳴あり");
        }
        parts.push(if !self.precipitation_possible {
            "降水環境なし"
        } else {
            match self.snowfall_zone {
                Some(true) => "降雪環境あり",
                Some(false) => "降雪環境なし",
                None => "降雪環境は不明",
            }
        });
        if self.surface_snow_observed {
            parts.extend(["地表の積雪を実測", "地表の雪を現在場面の材料にできる"]);
        } else if self.precipitation_kind == PrecipitationKind::Snow {
            parts.extend([
                "地表の積雪は未観測",
                "降っている雪だけを現在場面の材料にできる",
            ]);
        } else {
            parts.extend(["周辺の積雪は未観測", "雪や積雪を現在場面の材料にしない"]);
        }
        parts.join("。") + "。"
    }
    pub fn to_prompt_details(&self) -> PromptDetails {
        PromptDetails {
            precipitation_kind: self.precipitation_kind,
            precipitation_possible: self.precipitation_possible,
            thunder_active: self.thunder_active,
            snowfall_environment: match self.snowfall_zone {
                Some(true) => "yes",
                Some(false) => "no",
                None => "unknown",
            },
            surface_snow_observed: self.surface_snow_observed,
            weather_context: self.prompt_line(),
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
/// Sky visibility is a separate environment projection; it must gate prompt publication.
pub fn resolve_precipitation_context(input: &Input) -> Result<PrecipitationContext> {
    let current_y = input.current_y.map(rounded_y).transpose()?;
    let dimension = remove_namespace_lower(input.dimension.as_deref().unwrap_or(""));
    let weather = input.weather.to_lowercase();
    let group = input.biome_group_id.to_lowercase();
    let surface_snow_observed = input.nearby_block_names.iter().any(|name| {
        matches!(
            remove_namespace_lower(name).as_str(),
            "snow" | "snow_block" | "powder_snow"
        )
    });
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
    let snow_evidence = if surface_snow_observed {
        SnowEvidence::ObservedSurface
    } else if precipitation_kind == PrecipitationKind::Snow {
        SnowEvidence::ActiveSnowfall
    } else {
        SnowEvidence::None
    };
    Ok(PrecipitationContext {
        current_y,
        biome_temperature: input.biome_temperature,
        snow_start_y: input.snow_start_y,
        snowfall_zone,
        precipitation_kind,
        precipitation_possible: !disabled,
        thunder_active: weather == "thunder" && !disabled,
        surface_snow_observed,
        snow_evidence,
    })
}

/// 現在イベントと呼出側が取得した気候を、降水判定への入力に投影する。
/// カタログ検索・状態変更は行わない。積雪の実測根拠にはtypeがblockの近接資源だけを使う。
pub fn from_event(event: &GameEvent, climate: &Climate) -> Result<PrecipitationContext> {
    resolve_precipitation_context(&Input {
        current_y: event.player.position.y,
        biome_temperature: climate.biome_temperature,
        snow_start_y: climate.snow_start_y,
        biome_group_id: climate.biome_group_id.clone(),
        biome_downfall: climate.biome_downfall,
        weather: match event.world.weather {
            Some(Weather::Clear) => "clear",
            Some(Weather::Rain) => "rain",
            Some(Weather::Thunder) => "thunder",
            None => "unknown",
        }
        .into(),
        dimension: event.player.dimension.clone(),
        nearby_block_names: event
            .nearby_resources
            .iter()
            .filter(|r| r.r#type.to_lowercase() == "block")
            .map(|r| r.name.clone())
            .collect(),
    })
}

#[cfg(test)]
#[path = "precipitation_tests.rs"]
mod tests;
