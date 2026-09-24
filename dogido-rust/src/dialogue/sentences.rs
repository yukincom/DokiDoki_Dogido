//! voicevox-sentence-stream の SentenceSplitter と同じ文境界。
//! 参照版・ライセンスは third-party/voicevox-sentence-stream/ を参照。

/// 読み補正・本文検査済みの文章だけを渡す。文字を捨てず、UTF-8境界で切る。
pub(super) fn sentences(text: &str) -> impl Iterator<Item = &str> {
    let mut remaining = text;
    std::iter::from_fn(move || {
        if remaining.is_empty() {
            return None;
        }
        let mut previous = '\0';
        let mut end = remaining.len();
        for (count, (offset, ch)) in remaining.char_indices().enumerate() {
            if matches!(ch, '。' | '！' | '？' | '\n')
                || (ch.is_whitespace() && matches!(previous, '.' | '!' | '?'))
                || count + 1 >= 180
            {
                end = offset + ch.len_utf8();
                break;
            }
            previous = ch;
        }
        let (sentence, rest) = remaining.split_at(end);
        remaining = rest;
        Some(sentence)
    })
}

#[cfg(test)]
mod tests {
    use super::sentences;

    #[test]
    fn short_japanese_replies_and_final_tail_are_kept() {
        assert_eq!(
            sentences("うん。そうやな！ほんま？続き").collect::<Vec<_>>(),
            ["うん。", "そうやな！", "ほんま？", "続き"]
        );
    }

    #[test]
    fn latin_punctuation_requires_whitespace_and_keeps_decimal() {
        assert_eq!(
            sentences("3.14だね。OK! Yes? 終わり\n次へ").collect::<Vec<_>>(),
            ["3.14だね。", "OK! ", "Yes? ", "終わり\n", "次へ"]
        );
    }

    #[test]
    fn long_unicode_and_whitespace_are_never_lost() {
        for text in [
            "あ🦮".repeat(181),
            " \n「うん。」\n\n 最後 ".into(),
            "".into(),
        ] {
            let chunks = sentences(&text).collect::<Vec<_>>();
            assert_eq!(chunks.concat(), text);
            assert!(chunks.iter().all(|s| s.chars().count() <= 180));
        }
    }
}
