//! Current utterance's explicit return plan; never saved as world state.
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::sync::LazyLock;
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Travel {
    pub action: String,
    pub evidence: String,
}
static OBLIGATION: LazyLock<[Regex; 2]> = LazyLock::new(|| {
    [
        Regex::new(r"(?:帰|かえ)ら(?:なくちゃ|なきゃ|なあかん|ないと|んと)").unwrap(),
        Regex::new(r"(?:戻|もど)ら(?:なくちゃ|なきゃ|なあかん|ないと|んと)").unwrap(),
    ]
});
static NEGATION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:帰らない(?:よ|で|つもり)|帰りたくない|帰らん(?:で|つもり)|戻らない(?:よ|で|つもり)|戻りたくない|戻らん(?:で|つもり))").unwrap()
});
static EXPLICIT: LazyLock<[Regex; 2]> = LazyLock::new(|| {
    let target = r"(?:お?家|うち|自宅|拠点|リスポーン地点|ベッド)";
    let action = r"(?:帰ろう|帰ろ|帰る(?:わ|で|ね|よ)?|帰ります|戻ろう|戻ろ|戻る(?:わ|で|ね|よ)?|戻ります|向かおう|向かう|向かいます)";
    [
        Regex::new(&format!("{target}.{{0,8}}?{action}")).unwrap(),
        Regex::new(&format!("{action}.{{0,8}}?{target}")).unwrap(),
    ]
});
pub(super) fn extract(text: &str) -> Travel {
    let text: String = text
        .chars()
        .filter(|c| !crate::knowledge::query::space(*c))
        .collect();
    for pattern in OBLIGATION.iter() {
        for found in pattern.find_iter(&text) {
            let part = found.as_str();
            if (part.ends_with("ないと") || part.ends_with("んと"))
                && text[found.end()..].starts_with(['思', '考', '言'])
            {
                continue;
            }
            return Travel {
                action: "return_home".into(),
                evidence: part.into(),
            };
        }
    }
    if !NEGATION.is_match(&text) {
        for pattern in EXPLICIT.iter() {
            if let Some(found) = pattern.find(&text) {
                return Travel {
                    action: "return_home".into(),
                    evidence: found.as_str().into(),
                };
            }
        }
    }
    Travel {
        action: "none".into(),
        evidence: String::new(),
    }
}
