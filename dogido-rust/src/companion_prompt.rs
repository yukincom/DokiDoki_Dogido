//! One personality source for every model path that speaks as Dogido.
//! Internal extractors/validators have their own contracts and do not impersonate him.
//! JSONはラボ・配布と共有する編集用資産。配置先がPythonパッケージでも、発話要求の構築はRust。
//! 現在はLazyLockの初回参照でJSONを読む。ビルド時・起動時の全キー検査は行っていない。
use serde_json::Value;
use std::sync::LazyLock;

static TEXT: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../dogido_server/llm/companion_prompts.json"
    ))
    .expect("companion prompt source")
});

pub fn base() -> &'static str {
    TEXT["base"].as_str().unwrap()
}
pub fn mode(mode: &str) -> &'static str {
    TEXT["modes"][mode].as_str().unwrap_or("")
}
pub fn grounding() -> &'static str {
    TEXT["grounding"].as_str().unwrap()
}
/// 出力契約を機能専用のものへ差し替える際も、共有人格・観測方針は同じ正本を使う。
pub fn choice_contract() -> &'static str {
    TEXT["choice_contract"].as_str().unwrap()
}
pub fn speech(mode_name: &str) -> String {
    let role = if mode_name == "workshop" {
        TEXT["workshop_identity"].as_str().unwrap()
    } else {
        ""
    };
    [
        base(),
        role,
        mode(mode_name),
        TEXT["speech_contract"].as_str().unwrap(),
    ]
    .into_iter()
    .filter(|s| !s.is_empty())
    .collect::<Vec<_>>()
    .join("\n")
}
/// The caller owns the purpose; no model call classifies this branch.
#[derive(Clone, Copy)]
pub enum DialoguePurpose {
    Reply,
    Ambient,
}

/// Smell guidance is attached only for a current, code-resolved smell observation.
pub fn dialogue(mode_name: &str, purpose: DialoguePurpose, smell_observed: bool) -> String {
    let purpose_key = match purpose {
        DialoguePurpose::Reply => "reply",
        DialoguePurpose::Ambient => "ambient",
    };
    [
        base(),
        mode(mode_name),
        grounding(),
        if smell_observed {
            TEXT["observed_smell"].as_str().unwrap()
        } else {
            ""
        },
        TEXT["dialogue"][purpose_key].as_str().unwrap(),
        choice_contract(),
    ]
    .into_iter()
    .filter(|s| !s.is_empty())
    .collect::<Vec<_>>()
    .join("\n")
}

/// Expand references in generated task assets from the same source as live dialogue.
pub fn expand(text: &str) -> String {
    text.replace("__DOGIDO_SHARED_BASE__", base())
        .replace(
            "__DOGIDO_WORKSHOP_IDENTITY__",
            TEXT["workshop_identity"].as_str().unwrap(),
        )
        .replace("__DOGIDO_WORKSHOP_MODE__", mode("workshop"))
}
