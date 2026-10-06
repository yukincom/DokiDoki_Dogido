use super::*;
use regex::Regex;
static COMBAT_RESUME: LazyLock<Regex> = LazyLock::new(|| {
    let data: Value = serde_json::from_str(include_str!("followup_patterns.json"))
        .expect("canonical combat resume reply");
    let pattern = data["combat_accept"].as_str().unwrap();
    Regex::new(&format!(
        r"\A(?:{})\z",
        pattern.replace(r"\s", r"[\s\x1c-\x1f]")
    ))
    .expect("canonical combat resume regex")
});
/// Used only when the combat classifier is unavailable. The caller validates
/// the original utterance and current combat state before resuming.
pub fn fixed_combat_resume(text: &str) -> bool {
    let text = strip(text);
    !text.is_empty() && !text.ends_with(['?', '？']) && COMBAT_RESUME.is_match(text)
}
