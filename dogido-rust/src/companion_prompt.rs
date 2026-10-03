//! One personality source for every model path that speaks as Dogido.
//! Internal extractors/validators have their own contracts and do not impersonate him.
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
pub fn dialogue(mode_name: &str) -> String {
    [
        base(),
        mode(mode_name),
        grounding(),
        TEXT["dialogue"].as_str().unwrap(),
        TEXT["choice_contract"].as_str().unwrap(),
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
