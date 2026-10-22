//! player入力を閉じた語彙・文型で調べ、静粛要求・戦況質問・句の保存/修正へ分類する。
//! 原文と共有input-policy.jsonからProjectionを返す純粋な読取り処理。
//! 呼び手が現在状態・権限・根拠と合わせて採否を決めるため、分類だけでは保存や操作を実行しない。
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::LazyLock;

static POLICY: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("input-policy.json")).expect("checked input policy")
});
fn words(key: &str) -> impl Iterator<Item = &'static str> {
    POLICY[key]
        .as_array()
        .expect("checked input vocabulary")
        .iter()
        .map(|v| v.as_str().expect("checked input word"))
}
fn has(text: &str, key: &str) -> bool {
    words(key).any(|word| text.contains(word))
}
use crate::compat::fold_kana as fold;
use crate::compat::is_dogido_whitespace as space;
fn compile(pattern: &str) -> Regex {
    // 入力仕様上の空白はUnicode空白とU+001C..U+001F。正規表現内でも同じ集合へ広げる。
    Regex::new(&pattern.replace(r"\s", r"[\s\u001c-\u001f]")).expect("checked input regex")
}
static SHORT_INVENTORY: LazyLock<Regex> = LazyLock::new(|| {
    // 真偽値だけを使う検索なので、非対応の先読みは同じ末尾境界を消費して検査する。
    let pattern = POLICY["short_inventory"]
        .as_str()
        .unwrap()
        .replace(r"(?=$|[?？!！。、,])", r"(?:$|[?？!！。、,])");
    compile(&pattern)
});
static DIRECTION_STRIP: LazyLock<Regex> =
    LazyLock::new(|| compile(POLICY["direction_strip"].as_str().unwrap()));
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Projection {
    pub wants_quiet: bool,
    pub should_block_ambient: bool,
    pub asks_hostile_count: bool,
    pub asks_hostile_direction: bool,
    pub asks_dragon_direction: bool,
    pub asks_save_last_haiku: bool,
    pub asks_inventory: bool,
    pub asks_about_sound: bool,
    pub player_haiku_text: Option<String>,
    pub revised_haiku_text: Option<String>,
}
fn payload(raw: &str, prefix_key: &str) -> Option<String> {
    let text = raw.trim_matches(space);
    let prefix = words(prefix_key).find(|prefix| text.starts_with(prefix))?;
    let replaced = text[prefix.len()..].replace('　', " ");
    let text = replaced.trim_matches(space);
    if text.is_empty() {
        return None;
    }
    let mut pieces = Vec::new();
    for separator in ['\n', '/', '／', '|', '｜'] {
        if text.contains(separator) {
            pieces = text
                .split(separator)
                .map(|p| p.trim_matches(space))
                .filter(|p| !p.is_empty())
                .map(str::to_owned)
                .collect();
            break;
        }
    }
    if pieces.is_empty() {
        pieces.push(
            text.split(space)
                .filter(|p| !p.is_empty())
                .collect::<Vec<_>>()
                .join(" "),
        );
    }
    Some(pieces.into_iter().take(3).collect::<Vec<_>>().join("\n"))
}
/// `normalized`の再正規化はせず、句本文だけは改行・引用を保持した`raw`から読む。
/// slash commandの早期returnと保存可否は呼出側が既存の規則で判断する。
// TODO(request-classifier): enemy_information 接続後は、下の敵数・方角・ドラゴン語句を
// 「敵情報経路へ入れるか」の再判定に使わず、同一入力の分類結果で経路を決める。
// 現5分類は数／方角／対象を区別しないため、その属性抽出や所持品・音・静粛判定は別課題。
pub fn classify(raw: &str, normalized: &str) -> Projection {
    let text = fold(normalized);
    let has_dragon = words("DRAGON_KEYWORDS").any(|word| text.contains(&fold(word)));
    let has_direction = has(&text, "DIRECTION_QUERY_KEYWORDS");
    let compact_direction = DIRECTION_STRIP.replace_all(&text, "");
    let asks_hostile_direction = !text.is_empty()
        && !has_dragon
        && has_direction
        && (has(&text, "HOSTILE_DIRECTION_REFERENTS")
            || ["どっち", "どこ", "どのへん", "どの辺", "方向", "方角"]
                .contains(&compact_direction.as_ref()));
    let possession = has(&text, "POSSESSION_HINT_KEYWORDS");
    let asks_inventory = !text.is_empty()
        && (has(&text, "INVENTORY_TOPIC_KEYWORDS")
            || (possession && has(&text, "INVENTORY_ITEM_HINT_KEYWORDS"))
            || SHORT_INVENTORY.is_match(&text)
            || (possession
                && ["何", "なに", "どんな"]
                    .iter()
                    .any(|word| text.contains(word))));
    let compact_sound = text.replace([' ', '　'], "");
    let asks_about_sound = !text.is_empty()
        && (has(&text, "_MUSIC_TOPIC_MARKERS")
            || (has(&text, "_SOUND_TOPIC_MARKERS")
                && (has(&text, "_SOUND_QUERY_SHAPES")
                    || (compact_sound.chars().count() <= 5
                        && has(&compact_sound, "_SOUND_TOPIC_MARKERS")))));
    Projection {
        wants_quiet: !text.is_empty() && has(&text, "HUSH_KEYWORDS"),
        should_block_ambient: !normalized.is_empty(),
        asks_hostile_count: !text.is_empty()
            && has(&text, "HOSTILE_COUNT_QUERY_KEYWORDS")
            && (has(&text, "HOSTILE_QUERY_KEYWORDS")
                || text.contains("残り")
                || text.contains("あと")),
        asks_hostile_direction,
        asks_dragon_direction: !text.is_empty() && has_dragon && has_direction,
        asks_save_last_haiku: !text.is_empty()
            && (has(&text, "SAVE_LAST_HAIKU_KEYWORDS")
                || (text.contains("保存")
                    && (text.contains("今の") || text.contains("さっきの"))
                    && (text.contains("句") || text.contains("川柳")))),
        asks_inventory,
        asks_about_sound,
        player_haiku_text: payload(raw, "HAIKU_SAVE_PREFIXES"),
        revised_haiku_text: payload(raw, "HAIKU_REVISE_PREFIXES"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[derive(Deserialize)]
    struct Case {
        raw: String,
        normalized: String,
        expected: Projection,
    }
    #[test]
    fn pure_projection_matches_fixture_guardrails() {
        let cases: Vec<Case> =
            serde_json::from_str(include_str!("../fixtures/input-policy.json")).unwrap();
        for case in cases {
            assert_eq!(
                classify(&case.raw, &case.normalized),
                case.expected,
                "raw={:?}, normalized={:?}",
                case.raw,
                case.normalized
            );
        }
    }
    #[test]
    fn raw_multiline_and_interpreted_surface_stay_separate() {
        let value = classify("直し: 一行\n二行\n三行\n四行", "石炭ある？");
        assert_eq!(
            value.revised_haiku_text.as_deref(),
            Some("一行\n二行\n三行")
        );
        assert!(value.asks_inventory);
    }
}
