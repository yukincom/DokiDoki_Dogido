//! 移行前の周囲モブ質問用の定型応答。11月2日仕様では有無・数・方向を
//! 通常会話で扱い、ここで戦闘警告の判断は行わない。
mod mob_information;

use super::model::Speech;
use crate::events::GameEvent;

fn folded(text: &str) -> String {
    text.chars()
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).expect("kana")
            } else {
                c
            }
        })
        .collect()
}
fn contains_any(text: &str, words: &[&str]) -> bool {
    words.iter().any(|word| text.contains(&folded(word)))
}
fn has_direction(text: &str) -> bool {
    contains_any(
        text,
        &["どこ", "どっち", "方向", "方角", "どのへん", "どの辺"],
    )
}
fn bare_direction(text: &str) -> bool {
    let compact: String = text
        .chars()
        .filter(|c| !c.is_whitespace() && !"?？!！。、…・".contains(*c))
        .collect();
    matches!(
        compact.as_str(),
        "どっち" | "どこ" | "どのへん" | "どの辺" | "方向" | "方角"
    )
}
/// 移行前の呼出元から分類済みの質問を受け、対象・数・位置を現在観測へ照合する。
/// last_namedは実際に警告した種類で、短い追質問の対象補完だけに使う。
pub fn answer_classified(
    event: &GameEvent,
    text: &str,
    last_named: Option<(&str, u64)>,
    now_ms: u64,
) -> Option<Speech> {
    mob_information::answer(event, text, last_named, now_ms)
}
