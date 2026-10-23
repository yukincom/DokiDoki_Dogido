//! LLMが返した操作根拠を原文へ照合する。
//! 語句による意味・意図の再判定はしない。対象・状態・保存の実行は呼出元が担当する。
use crate::compat::is_dogido_whitespace as space;
/// モデルが選んだ操作と原文を受け取り、根拠の存在だけを確認する。
/// 疑問・条件・引用・否定などの意味は再判定しない。原文にない根拠は補作しない。
pub fn state_change_safe(_action: &str, text: &str, evidence: &str) -> bool {
    !evidence.trim_matches(space).is_empty() && text.contains(evidence)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn model_actions_are_not_reclassified_by_wording() {
        for raw in [
            "ともしびをしろいはなに変えて",
            "もし変えるならこれにして",
            "これに変えない？",
            "これにしようかな",
            "「これに変えて」と言った",
            "これに変えてって聞いた",
            "終了しない",
            "上五はそのままで終了",
            "うん",
        ] {
            for action in [
                "stage_player_edit",
                "accept_pending",
                "reject_pending",
                "close_workshop",
            ] {
                assert!(state_change_safe(action, raw, raw), "{action}: {raw}");
            }
        }
        assert!(!state_change_safe(
            "close_workshop",
            "今日はここまで",
            "終了"
        ));
        assert!(!state_change_safe("close_workshop", "今日はここまで", ""));
    }
}
