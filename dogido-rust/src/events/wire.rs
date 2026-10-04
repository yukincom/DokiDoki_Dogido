//! 既知fieldのJSON値をRustの数値・真偽値・時刻へ変換する通信入力層。
//! 既存adapterと保存済みfixtureの入力互換を保つため、数値文字列や0/1などの許容形を明示する。
//! Pythonを呼び出す処理ではなく、旧受信契約で許されていた変換をここで完結させる。
//! 値域とfield間の整合はmodels/semanticに任せ、extraに保持する未知fieldは変換しない。
use chrono::{DateTime, FixedOffset, NaiveDate, NaiveDateTime, SecondsFormat, Utc};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::Value;
use std::collections::BTreeMap;

/// 既知fieldのJSON値一件を変換し、許容形でなければ説明付きのエラーを返す。
pub trait FromWire: Sized {
    fn from_wire(value: Value) -> Result<Self, String>;
}
/// modelsのserde属性から呼ぶ共通入口。変換エラーを受信JSONのdeserialize失敗へ結び付ける。
pub fn deserialize<'de, D: Deserializer<'de>, T: FromWire>(d: D) -> Result<T, D::Error> {
    T::from_wire(Value::deserialize(d)?).map_err(serde::de::Error::custom)
}
// 整数・boolの0/1・整数表記の文字列を受け、実数なら有限で小数部0の範囲内だけ通す。
// 文字列の桁区切りアンダースコアと.00を許容し、小数の切り捨てによる別値への変換は行わない。
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
// 数値・数値文字列・boolの0/1を有限実数へ揃える。NaNや無限大は観測値へ入れない。
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
// JSON bool、数値0/1、列挙した大小文字を区別しない文字列だけを真偽値へ写す。
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
// nullはNoneとして残す。field自体の省略はmodelsのserde(default)が処理する。
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

/// イベント・操作結果の時刻。UTC offset付きと、タイムゾーンなしを区別して保持する。
/// FabricのISO offset時刻に加え、既存入力との互換用にnaive日付時刻とUnix timestampを受ける。
/// naive値へタイムゾーンを補作せず、ゲーム状態での時計変換は消費側へ渡す。
#[derive(Clone, Debug)]
pub enum EventTime {
    Aware(DateTime<FixedOffset>),
    Naive(NaiveDateTime),
}
impl EventTime {
    /// ISO時刻、naive日時、日付のみの順に読み、残りはUnix timestampとして解釈する。
    /// 数値は絶対値200億を超えるとミリ秒、それ以下は秒とする既存契約を維持する。
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
        // 入力の小数秒をマイクロ秒へ丸め、i64の表現範囲を確認してからchronoへ渡す。
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
// 保存済みfixtureとの表記互換のため小数秒は6桁。naive/offset付きの区別も出力へ残す。
impl Serialize for EventTime {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let text = match self {
            Self::Aware(time) => time.to_rfc3339_opts(SecondsFormat::Micros, true),
            Self::Naive(time) => time.format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        };
        serializer.serialize_str(&text)
    }
}
