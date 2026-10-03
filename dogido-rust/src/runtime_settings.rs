//! 共通既定値の唯一の読込口。人が編集する値は共有JSONに置く。
use serde_json::{Map, Value};
use std::sync::LazyLock;

static DEFAULTS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../dogido_server/runtime_defaults.json"))
        .expect("checked runtime defaults")
});

pub(crate) fn defaults(section: &str) -> &'static Map<String, Value> {
    DEFAULTS[section]
        .as_object()
        .expect("checked settings section")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn warnings_and_combat_share_defaults_without_losing_overrides() {
        let combat = crate::combat::model::Settings::default();
        let warnings = serde_json::to_value(crate::threats::Settings::default()).unwrap();
        for (key, expected) in defaults("combat") {
            assert_eq!(&combat.0[key], expected, "{key}");
            if let Some(actual) = warnings.get(key) {
                assert_eq!(actual.as_f64(), expected.as_f64(), "{key}");
            }
        }
        let custom = crate::combat::model::Settings::merged(
            json!({"panic_distance":9.25}).as_object().unwrap(),
        )
        .unwrap();
        assert_eq!(custom.number("panic_distance"), 9.25);
        assert_eq!(
            custom.number("rear_warning_distance"),
            combat.number("rear_warning_distance")
        );
        assert!(
            crate::combat::model::Settings::merged(json!({"typo":1}).as_object().unwrap()).is_err()
        );
    }
}
