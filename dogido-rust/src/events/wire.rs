//! Fabricが送るoffset付き日時の受信と、観測出力の日時表記を担当する。
//! 日時はT区切り・offset必須で受け、単位やタイムゾーンを推定して補わない。
//! 数値・真偽値はmodelsの型をそのまま受信し、値域とfield間の整合はmodels/semanticで検査する。
use chrono::{DateTime, FixedOffset, SecondsFormat};
use serde::{Deserialize, Deserializer, Serializer};

/// Fabricの観測・操作結果のoffset付き日時を受け取る。欠けた時差やUnix時刻の単位は推定しない。
pub fn deserialize_datetime<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<DateTime<FixedOffset>, D::Error> {
    let text = String::deserialize(deserializer)?;
    if text.as_bytes().get(10) != Some(&b'T') {
        return Err(serde::de::Error::custom(
            "expected a T-separated datetime with an offset",
        ));
    }
    DateTime::parse_from_rfc3339(&text).map_err(serde::de::Error::custom)
}

/// 観測の出力表記を小数秒6桁に揃える。入力されたoffsetは保持し、UTCだけZで出す。
pub fn serialize_datetime<S: Serializer>(
    time: &DateTime<FixedOffset>,
    serializer: S,
) -> Result<S::Ok, S::Error> {
    serializer.serialize_str(&time.to_rfc3339_opts(SecondsFormat::Micros, true))
}
