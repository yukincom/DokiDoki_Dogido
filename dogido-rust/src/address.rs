//! 国語の学習中に出た別の話題を、通常会話へ渡してよいか確認するための判定と保留データ。
//! 最近の学習に無関係で、呼びかけも話題転換語もない入力を保留候補にする。
//! 呼び直し → 元の話題を短く示す確認 → 確認音声の再生完了 → 肯定、の順に元入力を解放する。
//! Sessionのaddress_runtimeが一件の保留を所有し、置換・期限切れ・拒否・割込み時に片づける。
//! このモジュールは次のActionを返す。実際の再送・履歴更新・音声配送はSession側が行い、
//! 保留中も元のturn ID・入力時刻・記録制限を引き継いで、別の新規発話として扱わない。
use crate::playback::Status as PlaybackStatus;
use icu_normalizer::ComposingNormalizer;
use serde_json::Value;

/// 宛先確認後に再利用する元の入力。input_atはDialogue時計の経過ミリ秒。
#[derive(Clone, Debug)]
pub struct Request {
    pub turn: String,
    pub text: String,
    pub source: String,
    pub input_at: u64,
    /// 入力受理時の記録制限。表示行が消えた後の再送にも引き継ぐ、モデルには渡さない値。
    pub record_private: bool,
}
/// 原入力の期限と、確認turnが聞こえたかを持つ保留一件。
#[derive(Debug)]
pub struct Pending {
    pub original: Request,
    pub expires_at: u64,
    /// 確認turn IDと実再生済みフラグ。Noneなら、次の呼び直しで確認を提示できる。
    pub repair: Option<(String, bool)>,
}
/// 通常routingへ通す／確認を話す／無言で待つ／元入力を再送する／破棄する、の閉じた指示。
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    Pass,
    Ask(String),
    Wait(&'static str),
    Accept,
    Decline,
}
/// この閉じた呼びかけ・確認語には操作や知識質問が含まれない。
/// それ以外の宛先入力は、既存parserの所有権判定を先に通す。
pub fn plain_control(text: &str) -> bool {
    let normalized = ComposingNormalizer::new_nfkc().normalize(text);
    let compact: String = normalized
        .chars()
        .filter(|c| !punctuation(*c) && !"：:".contains(*c))
        .collect();
    if [
        "うん",
        "はい",
        "ええで",
        "いいよ",
        "聞いて",
        "お願い",
        "そうして",
        "いや",
        "いいえ",
        "違う",
        "ちがう",
        "やめとく",
        "もういい",
        "聞いてる",
        "聞いてますか",
    ]
    .contains(&compact.as_str())
    {
        return true;
    }
    let mut body = compact.as_str();
    if let Some(prefix) = ["あれ", "ねえ", "ねぇ", "なあ", "おーい"]
        .iter()
        .find(|p| body.starts_with(**p))
    {
        body = &body[prefix.len()..];
    }
    body.strip_prefix("ドギド").is_some_and(|s| {
        [
            "",
            "いる",
            "おる",
            "聞いてる",
            "聞いてますか",
            "きいてる",
            "きいてますか",
        ]
        .contains(&s)
    })
}
use crate::compat::is_dogido_whitespace as space;
fn punctuation(c: char) -> bool {
    space(c) || "、,。.!！?？".contains(c)
}
fn direct_call(text: &str) -> bool {
    let mut text = text;
    if let Some(prefix) = ["あれ", "ねえ", "ねぇ", "なあ", "おーい"]
        .iter()
        .find(|p| text.starts_with(**p))
    {
        text = text[prefix.len()..].trim_start_matches(|c| punctuation(c) || "：:".contains(c));
    }
    let Some(body) = text.strip_prefix("ドギド") else {
        return false;
    };
    body.is_empty()
        || (body.starts_with(|c| punctuation(c) || "：:".contains(c))
            && !body
                .trim_start_matches(|c| punctuation(c) || "：:".contains(c))
                .contains('\n'))
        || [
            "いる",
            "おる",
            "聞いてる",
            "聞いてますか",
            "きいてる",
            "きいてますか",
        ]
        .iter()
        .any(|p| {
            body.strip_prefix(p)
                .is_some_and(|s| s.chars().all(|c| space(c) || "。.!！?？".contains(c)))
        })
}
fn shift(text: &str) -> bool {
    let mut text = text;
    if let Some(prefix) = ["あの", "えっと"].iter().find(|p| text.starts_with(**p)) {
        text = text[prefix.len()..].trim_start_matches(|c| space(c) || "、,。".contains(c));
    }
    [
        "ところで",
        "さて",
        "そういえば",
        "それはそうと",
        "話変わるけど",
        "話は変わるけど",
        "話を変わるけど",
        "別の話",
    ]
    .iter()
    .any(|p| text.starts_with(p))
}
/// 名前での呼びかけ、宛先の明言、聞いているかの確認を、幅・空白を正規化して検出する。
pub fn addressed(text: &str) -> bool {
    let normalized = ComposingNormalizer::new_nfkc().normalize(text);
    let text = normalized.trim_matches(space);
    direct_call(text)
        || [
            "ドギドに言った",
            "ドギドに言うた",
            "お前に言った",
            "君に言った",
            "あなたに言った",
        ]
        .iter()
        .any(|p| text.contains(p))
        || ["聞いてる", "聞いてますか"].iter().any(|p| {
            text.trim_end_matches(|c| space(c) || "。.!！?？".contains(c))
                .ends_with(p)
        })
}
/// 学習から別の話題へ移る入力を、宛先確認まで保留するかを返す。
/// submitted・previous・fresh_msは同じ時計のミリ秒。今回の入力受理と直前の入力／会話完了の近さを測り、
/// モデルの生成待ち時間によって「しばらく会話していない」と判断が変わることを防ぐ。
pub fn should_hold(
    text: &str,
    interpretation: &Value,
    submitted: u64,
    previous: Option<u64>,
    fresh_ms: u64,
) -> bool {
    let normalized = ComposingNormalizer::new_nfkc().normalize(text);
    let text = normalized.trim_matches(space);
    let related = interpretation["topic"] == "language"
        && matches!(
            interpretation["relation"].as_str(),
            Some("continue" | "resume" | "correct")
        );
    !related
        && !direct_call(text)
        && !shift(text)
        && previous.is_some_and(|at| submitted.saturating_sub(at) < fresh_ms)
}
/// 確認音声で引用する話題を、空白をまとめた最大48文字にする（長い場合は末尾を省略記号にする）。
pub fn summary(text: &str) -> String {
    let text = text
        .split(space)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    let text = text.trim_matches(|c| "『』「」 。.!！?？".contains(c));
    if text.chars().count() <= 48 {
        text.into()
    } else {
        text.chars().take(47).chain(['…']).collect()
    }
}
impl Pending {
    /// 入力時刻から期限を作る。ttlの単位はミリ秒で、最低1秒を確保する。
    pub fn new(original: Request, ttl: u64) -> Self {
        Self {
            expires_at: original.input_at.saturating_add(ttl.max(1000)),
            original,
            repair: None,
        }
    }
    pub fn expired(&self, now: u64) -> bool {
        now >= self.expires_at
    }
    /// 次の入力に対する指示を返す。保留本体の消費は呼び手が行う。
    /// 確認をまだ聞いていない肯定はWaitにし、元入力への同意と取り違えない。
    pub fn input(&self, text: &str) -> Action {
        let Some((_, completed)) = &self.repair else {
            return if addressed(text) {
                Action::Ask(format!(
                    "あっ、ごめん！ オレに言うてたんやな。 さっきの『{}』のこと、今から聞いてええ？",
                    summary(&self.original.text)
                ))
            } else {
                Action::Pass
            };
        };
        if !completed {
            return Action::Wait("confirmation_before_repair_completed");
        }
        let compact: String = ComposingNormalizer::new_nfkc()
            .normalize(text)
            .chars()
            .filter(|c| !punctuation(*c))
            .collect();
        match compact.as_str() {
            "うん" | "はい" | "ええで" | "いいよ" | "聞いて" | "お願い" | "そうして" => {
                Action::Accept
            }
            "いや" | "いいえ" | "違う" | "ちがう" | "やめとく" | "もういい" => {
                Action::Decline
            }
            _ => Action::Wait("ambiguous_confirmation"),
        }
    }
    /// 対応する確認turnの実再生結果だけを反映する。
    /// 失敗・取消・音声無効なら確認を未提示へ戻し、次の呼び直しで聞き直せるようにする。
    pub fn playback(&mut self, turn: &str, status: PlaybackStatus) {
        if let Some((id, heard)) = self.repair.as_mut().filter(|(id, _)| id == turn) {
            let _ = id;
            match status {
                PlaybackStatus::Completed => *heard = true,
                PlaybackStatus::Failed
                | PlaybackStatus::Cancelled
                | PlaybackStatus::AudioDisabled => self.repair = None,
                _ => (),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn pending() -> Pending {
        Pending::new(
            Request {
                record_private: false,
                turn: "original".into(),
                text: "家を作りたい".into(),
                source: "voice".into(),
                input_at: 10,
            },
            300000,
        )
    }
    #[test]
    fn freshness_is_measured_at_input_and_explicit_or_related_handoffs_bypass_it() {
        assert!(should_hold(
            "家を作りたい",
            &json!({}),
            120009,
            Some(10),
            120000
        ));
        assert!(!should_hold(
            "家を作りたい",
            &json!({}),
            120010,
            Some(10),
            120000
        ));
        assert!(!should_hold("家を作りたい", &json!({}), 0, None, 120000));
        for text in [
            "ドギド、家を作りたい",
            "えっと、ところで家を作りたい",
            "ﾄﾞｷﾞﾄﾞ：家を作りたい",
        ] {
            assert!(!should_hold(text, &json!({}), 11, Some(10), 120000));
        }
        for relation in ["continue", "resume", "correct"] {
            assert!(!should_hold(
                "そうなんだ",
                &json!({"topic":"language","relation":relation}),
                11,
                Some(10),
                120000
            ));
        }
    }
    #[test]
    fn only_completed_repair_can_release_and_failure_allows_address_retry() {
        let mut p = pending();
        assert_eq!(p.input("うん"), Action::Pass);
        assert!(matches!(p.input("ねえ、ドギド"), Action::Ask(_)));
        p.repair = Some(("repair".into(), false));
        p.playback("wrong", PlaybackStatus::Completed);
        assert_eq!(
            p.input("うん"),
            Action::Wait("confirmation_before_repair_completed")
        );
        for status in [
            PlaybackStatus::Failed,
            PlaybackStatus::Cancelled,
            PlaybackStatus::AudioDisabled,
        ] {
            p.repair = Some(("repair".into(), false));
            p.playback("repair", status);
            assert!(p.repair.is_none());
            assert!(matches!(p.input("聞いてる？"), Action::Ask(_)));
            // A late completion cannot turn a silent/failed repair into consent.
            p.playback("repair", PlaybackStatus::Completed);
            assert_eq!(p.input("うん"), Action::Pass);
        }
        p.repair = Some(("repair2".into(), false));
        p.playback("repair2", PlaybackStatus::Completed);
        assert_eq!(p.input("うん！"), Action::Accept);
        assert_eq!(p.input("もういい"), Action::Decline);
        assert_eq!(
            p.input("はいって言ったら？"),
            Action::Wait("ambiguous_confirmation")
        );
        assert!(!p.expired(300009));
        assert!(p.expired(300010));
    }
    #[test]
    fn summary_keeps_unicode_character_boundary() {
        assert_eq!(summary("「 家を\n\t建てたい！ 」"), "家を 建てたい");
        assert_eq!(summary(&"桜".repeat(60)), format!("{}…", "桜".repeat(47)));
        assert!(!addressed("ドギドの話"));
    }
    #[test]
    fn python_address_fixtures() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("../fixtures/address.json")).unwrap();
        for case in cases {
            let text = case["text"].as_str().unwrap();
            assert_eq!(addressed(text), case["addressed"], "addressed: {text:?}");
            assert_eq!(
                !should_hold(text, &serde_json::json!({}), 0, Some(0), 120000),
                case["explicit_transition"],
                "transition: {text:?}"
            );
            assert_eq!(summary(text), case["summary"], "summary: {text:?}");
            let mut p = pending();
            p.repair = Some(("heard".into(), true));
            let decision = match p.input(text) {
                Action::Accept => "accept",
                Action::Decline => "decline",
                _ => "uncertain",
            };
            assert_eq!(decision, case["confirmation"], "confirmation: {text:?}");
        }
    }
}
