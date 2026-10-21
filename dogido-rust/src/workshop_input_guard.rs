//! LLMが返した操作根拠を原文へ照合し、戦闘中断用の既存分類promptを組み立てる。
//! 語句による意味・意図の再判定はしない。対象・状態・保存の実行は呼出元が担当する。
use crate::compat::is_dogido_whitespace as space;
use crate::types::{ChatMessage, Role};
use serde_json::Value;
use std::sync::LazyLock;

static ASSETS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("workshop_input_guard/assets.json"))
        .expect("workshop prompt assets")
});

/// モデルが選んだ操作と原文を受け取り、根拠の存在だけを確認する。
/// 疑問・条件・引用・否定などの意味は再判定しない。原文にない根拠は補作しない。
pub fn state_change_safe(_action: &str, text: &str, evidence: &str) -> bool {
    !evidence.trim_matches(space).is_empty() && text.contains(evidence)
}

/// 分類結果の根拠を原文へ接続する。再開できる戦況かは既存runtimeが扱う。
pub fn combat_safe(action: &str, text: &str, evidence: &str) -> bool {
    matches!(action, "unrelated" | "uncertain") || state_change_safe(action, text, evidence)
}
pub fn combat_messages(verse: &str, text: &str) -> Vec<ChatMessage> {
    let verse = verse.trim_matches(space);
    let text = text.trim_matches(space);
    let p = &ASSETS["prompt"]["parts"];
    vec![
        ChatMessage {
            role: Role::System,
            content: ASSETS["prompt"]["system"].as_str().unwrap().into(),
        },
        ChatMessage {
            role: Role::User,
            content: format!(
                "{}{}{}{}{}",
                p[0].as_str().unwrap(),
                if verse.is_empty() {
                    "（句なし）"
                } else {
                    verse
                },
                p[1].as_str().unwrap(),
                if text.is_empty() {
                    "（聞き取れなかった）"
                } else {
                    text
                },
                p[2].as_str().unwrap().replace(
                    "{allowed_actions}",
                    &crate::workshop_combat_input::Action::ALL
                        .map(|a| a.name())
                        .join("、"),
                )
            ),
        },
    ]
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
            assert!(combat_safe("resume_workshop", raw, raw), "{raw}");
        }
        assert!(!state_change_safe(
            "close_workshop",
            "今日はここまで",
            "終了"
        ));
        assert!(!state_change_safe("close_workshop", "今日はここまで", ""));
        assert!(!combat_safe("close_workshop", "今日はここまで", "別の発話"));
    }
    #[test]
    fn full_paused_classification_messages_match_canonical() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("workshop_input_guard/prompts.json")).unwrap();
        for c in cases {
            assert_eq!(
                serde_json::to_value(combat_messages(
                    c["verse"].as_str().unwrap(),
                    c["text"].as_str().unwrap()
                ))
                .unwrap(),
                c["messages"]
            );
        }
    }
}
