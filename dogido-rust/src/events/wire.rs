//! PydanticのJSON入力で使われる数値・bool変換。未知フィールドには適用しない。
use chrono::{DateTime, FixedOffset, NaiveDate, NaiveDateTime, SecondsFormat, Utc};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::Value;
use std::collections::BTreeMap;

pub trait FromWire: Sized {
    fn from_wire(value: Value) -> Result<Self, String>;
}
pub fn deserialize<'de, D: Deserializer<'de>, T: FromWire>(d: D) -> Result<T, D::Error> {
    T::from_wire(Value::deserialize(d)?).map_err(serde::de::Error::custom)
}
impl FromWire for i64 {
    fn from_wire(value: Value) -> Result<Self, String> {
        if let Some(n) = value.as_i64() {
            return Ok(n);
        }
        if let Value::Bool(v) = value {
            return Ok(i64::from(v));
        }
        if let Value::String(s) = &value {
            let s = s.trim();
            let bytes = s.as_bytes();
            if bytes.iter().enumerate().any(|(i, b)| {
                *b == b'_'
                    && (i == 0
                        || i + 1 == bytes.len()
                        || !bytes[i - 1].is_ascii_digit()
                        || !bytes[i + 1].is_ascii_digit())
            }) {
                return Err("invalid integer separator".into());
            }
            let s = s.replace('_', "");
            let integer = if let Some((integer, fraction)) = s.split_once('.') {
                if fraction.is_empty() || !fraction.bytes().all(|b| b == b'0') {
                    return Err("expected an integer without fractional part".into());
                }
                integer
            } else {
                &s
            };
            return integer
                .parse()
                .map_err(|_| "expected a signed 64-bit integer".into());
        }
        let number = f64::from_wire(value)?;
        if number.is_finite()
            && number.fract() == 0.0
            && number >= i64::MIN as f64
            && number < -(i64::MIN as f64)
        {
            Ok(number as i64)
        } else {
            Err("expected a signed 64-bit integer".into())
        }
    }
}
impl FromWire for f64 {
    fn from_wire(value: Value) -> Result<Self, String> {
        let number = match value {
            Value::Number(n) => n.as_f64(),
            Value::Bool(b) => Some(if b { 1.0 } else { 0.0 }),
            Value::String(s) => s.trim().parse().ok(),
            _ => None,
        };
        number
            .filter(|n| n.is_finite())
            .ok_or_else(|| "expected a finite number".into())
    }
}
impl FromWire for bool {
    fn from_wire(value: Value) -> Result<Self, String> {
        match value {
            Value::Bool(b) => Ok(b),
            Value::Number(n) if n.as_f64() == Some(0.0) => Ok(false),
            Value::Number(n) if n.as_f64() == Some(1.0) => Ok(true),
            Value::String(s) => match s.to_ascii_lowercase().as_str() {
                "0" | "off" | "f" | "false" | "n" | "no" => Ok(false),
                "1" | "on" | "t" | "true" | "y" | "yes" => Ok(true),
                _ => Err("expected a boolean".into()),
            },
            _ => Err("expected a boolean".into()),
        }
    }
}
impl<T: FromWire> FromWire for Option<T> {
    fn from_wire(value: Value) -> Result<Self, String> {
        if value.is_null() {
            Ok(None)
        } else {
            T::from_wire(value).map(Some)
        }
    }
}
impl<T: FromWire> FromWire for BTreeMap<String, T> {
    fn from_wire(value: Value) -> Result<Self, String> {
        match value {
            Value::Object(object) => object
                .into_iter()
                .map(|(k, v)| Ok((k, T::from_wire(v)?)))
                .collect(),
            _ => Err("expected an object".into()),
        }
    }
}

/// naive時刻にタイムゾーンを補作せず保持する。ゲーム状態の時計変換は消費側が行う。
#[derive(Clone, Debug)]
pub enum EventTime {
    Aware(DateTime<FixedOffset>),
    Naive(NaiveDateTime),
}
impl EventTime {
    fn parse(value: Value) -> Result<Self, String> {
        if let Value::String(raw) = &value {
            let text = raw.replacen(' ', "T", 1);
            if let Ok(date) = DateTime::parse_from_rfc3339(&text) {
                return Ok(Self::Aware(date));
            }
            if let Ok(date) = NaiveDateTime::parse_from_str(&text, "%Y-%m-%dT%H:%M:%S%.f") {
                return Ok(Self::Naive(date));
            }
            if let Ok(date) = NaiveDate::parse_from_str(&text, "%Y-%m-%d") {
                return Ok(Self::Naive(date.and_hms_opt(0, 0, 0).unwrap()));
            }
        }
        // boolはtimestampとして受けない。
        if value.is_boolean() {
            return Err("expected a datetime".into());
        }
        let timestamp =
            f64::from_wire(value).map_err(|_| "expected an ISO 8601 datetime or timestamp")?;
        let seconds = if timestamp.abs() > 20_000_000_000.0 {
            timestamp / 1000.0
        } else {
            timestamp
        };
        let micros = (seconds * 1_000_000.0).round();
        if micros < i64::MIN as f64 || micros >= -(i64::MIN as f64) {
            return Err("datetime out of range".into());
        }
        DateTime::<Utc>::from_timestamp_micros(micros as i64)
            .map(|date| Self::Aware(date.fixed_offset()))
            .ok_or_else(|| "datetime out of range".into())
    }
}
impl<'de> Deserialize<'de> for EventTime {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        Self::parse(Value::deserialize(d)?).map_err(serde::de::Error::custom)
    }
}
impl Serialize for EventTime {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let text = match self {
            Self::Aware(time) => time.to_rfc3339_opts(SecondsFormat::Micros, true),
            Self::Naive(time) => time.format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        };
        serializer.serialize_str(&text)
    }
}
