//! BERTが受理した周囲モブ質問を、身体の現在観測だけで解決する。
//! BERTへ変更済み。旧判定へ戻さない。
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
/// 依頼性は共通BERTで確定済み。対象・数・位置だけを現在観測へ照合する。
/// last_namedは実際に警告した種類で、短い追質問の対象補完だけに使う。
pub fn answer_classified(
    event: &GameEvent,
    text: &str,
    last_named: Option<(&str, u64)>,
    now_ms: u64,
) -> Option<Speech> {
    mob_information::answer(event, text, last_named, now_ms)
}
