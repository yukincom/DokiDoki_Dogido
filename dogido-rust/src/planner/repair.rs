//! 本人の訂正だけを短期会話へ注記するための検証。履歴・世界・保存は変更しない。
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::{Action, Details};

pub const FIELDS: [&str; 5] = [
    "repair_action",
    "repair_target_turn_id",
    "repair_target_quote",
    "repair_signal_quote",
    "repair_replacement_quote",
];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RepairPayload {
    pub target_turn_id: String,
    pub target_quote: String,
    pub signal_quote: String,
    pub replacement_quote: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Repair {
    pub action: Action,
    pub target_turn_id: String,
    pub target_quote: String,
    pub signal_quote: String,
    pub replacement_quote: String,
    pub current_text: String,
}

impl Repair {
    pub fn prompt_fields(&self) -> Value {
        json!({"repair_action": self.action,
            "repair_target_turn_id": self.target_turn_id,
            "repair_target_quote": self.target_quote,
            "repair_signal_quote": self.signal_quote,
            "repair_replacement_quote": self.replacement_quote})
    }

    pub fn fallback(&self) -> String {
        if self.action == Action::ClarifyRepair {
            format!(
                "「{}」のところ、どういう意味やった？",
                self.target_quote.chars().take(40).collect::<String>()
            )
        } else {
            "あ、そういうことやったんやな。取り違えてごめんな。".into()
        }
    }
}

pub fn note(fields: &Value) -> String {
    let target = string(fields, "repair_target_quote");
    if string(fields, "repair_action") == "repair_conversation" {
        format!(
            "本人による会話の訂正:「{target}」への言い直しは「{}」。世界観測ではない。",
            string(fields, "repair_replacement_quote")
        )
    } else {
        format!("会話の修復待ち:「{target}」への異議。正しい意味はまだ未確定。")
    }
}

fn string<'a>(row: &'a Value, key: &str) -> &'a str {
    row.get(key).and_then(Value::as_str).unwrap_or("")
}

/// 呼び出し元は実再生completed済みの履歴だけを渡す。queued発話では継続しない。
pub fn pending(history: &[Value]) -> Value {
    let Some(pair) = history
        .get(history.len().saturating_sub(2)..)
        .filter(|p| p.len() == 2)
    else {
        return json!({});
    };
    let (player, reply) = (&pair[0], &pair[1]);
    if string(player, "role") != "user"
        || string(reply, "role") != "assistant"
        || string(reply, "turn_id") != format!("{}:reply", string(player, "turn_id"))
        || string(player, "repair_action") != "clarify_repair"
    {
        return json!({});
    }
    Value::Object(
        FIELDS
            .iter()
            .map(|key| ((*key).into(), Value::String(string(player, key).into())))
            .collect(),
    )
}

// Pythonの正規表現と同じ閉じた4組。未閉鎖の引用は範囲にしない。
fn quoted_ranges(raw: &str) -> Vec<std::ops::Range<usize>> {
    let mut ranges = Vec::new();
    let mut offset = 0;
    while offset < raw.len() {
        let ch = raw[offset..].chars().next().unwrap();
        let close = match ch {
            '「' => Some('」'),
            '『' => Some('』'),
            '“' => Some('”'),
            '"' => Some('"'),
            _ => None,
        };
        if let Some(end) = close.and_then(|c| {
            raw[offset + ch.len_utf8()..]
                .find(c)
                .map(|i| offset + ch.len_utf8() + i + c.len_utf8())
        }) {
            ranges.push(offset..end);
            offset = end;
        } else {
            offset += ch.len_utf8();
        }
    }
    ranges
}

fn signal(text: &str) -> bool {
    [
        "違う",
        "ちがう",
        "ちゃう",
        "じゃなく",
        "ではなく",
        "そうじゃない",
        "そうやない",
        "そういう意味じゃない",
        "そういう意味ではない",
        "言い間違",
        "言いまちが",
        "聞き間違",
        "聞きまちが",
        "聞き違",
        "勘違い",
        "訂正",
        "言い直",
        "いいなお",
        "って意味",
        "という意味",
    ]
    .iter()
    .any(|s| text.contains(s))
        || ["だ", "です", "や", "を言", "って"]
            .iter()
            .any(|s| text.contains(&format!("のこと{s}")))
}

pub fn has_signal(raw: &str) -> bool {
    let mut unquoted = raw.to_owned();
    for range in quoted_ranges(raw).into_iter().rev() {
        unquoted.replace_range(range, "");
    }
    signal(&unquoted)
}

fn bare(text: &str) -> bool {
    let text = text
        .strip_prefix("いや")
        .map(|s| s.trim_start_matches(|c: char| c == '、' || c == ',' || super::is_space(c)))
        .unwrap_or(text);
    [
        "違う",
        "ちがう",
        "ちゃう",
        "そうじゃない",
        "そうやない",
        "そういう意味じゃない",
        "そういう意味ではない",
    ]
    .iter()
    .any(|word| {
        let Some(mut rest) = text.strip_prefix(word) else {
            return false;
        };
        while let Some(suffix) = ["です", "んだ", "って", "よ", "で", "ね"]
            .iter()
            .find(|s| rest.starts_with(**s))
        {
            rest = &rest[suffix.len()..];
        }
        rest.chars()
            .all(|c| "。！!？?".contains(c) || super::is_space(c))
    })
}

pub fn parse(action: Action, payload: &RepairPayload, details: &Details) -> Option<Repair> {
    if !action.is_repair() {
        return None;
    }
    for (value, min, max) in [
        (&payload.target_turn_id, 1, 180),
        (&payload.target_quote, 1, 160),
        (&payload.signal_quote, 0, 160),
        (&payload.replacement_quote, 0, 160),
    ] {
        if !(min..=max).contains(&value.chars().count()) {
            return None;
        }
    }
    let target = details
        .history
        .iter()
        .find(|row| string(row, "turn_id") == payload.target_turn_id)?;
    if payload.target_turn_id == "current"
        || !["user", "assistant"].contains(&string(target, "role"))
        || !string(target, "text").contains(&payload.target_quote)
    {
        return None;
    }
    let pending = pending(&details.history);
    let followup = string(&pending, "repair_target_turn_id") == payload.target_turn_id
        && string(&pending, "repair_target_quote") == payload.target_quote;
    let raw = details
        .current
        .get("raw_text")
        .and_then(Value::as_str)
        .unwrap_or_else(|| string(&details.current, "text"));
    if payload.signal_quote.is_empty() {
        if !followup {
            return None;
        }
    } else {
        // byte位置を保った空白化。同じ語が引用内外に出ても、全出現を照合する。
        let mut unquoted = raw.to_owned();
        for range in quoted_ranges(raw).into_iter().rev() {
            unquoted.replace_range(range.clone(), &" ".repeat(range.len()));
        }
        if !raw
            .match_indices(&payload.signal_quote)
            .any(|(start, value)| signal(&unquoted[start..start + value.len()]))
        {
            return None;
        }
    }
    let replacement = &payload.replacement_quote;
    if action == Action::RepairConversation {
        let trimmed = replacement.trim_matches(super::is_space);
        if trimmed.is_empty()
            || !raw.contains(replacement)
            || bare(trimmed)
            || ["うん", "そう", "はい", "いいえ"]
                .contains(&replacement.trim_matches(|c| "。！!？? ".contains(c)))
        {
            return None;
        }
    } else if !replacement.is_empty() {
        return None;
    }
    Some(Repair {
        action,
        target_turn_id: payload.target_turn_id.clone(),
        target_quote: payload.target_quote.clone(),
        signal_quote: payload.signal_quote.clone(),
        replacement_quote: replacement.clone(),
        current_text: raw.into(),
    })
}
