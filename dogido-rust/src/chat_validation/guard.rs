use crate::reaction_leaf::sanitize::{compact, forbidden, re, repeated, strip};
use serde_json::Value;
use std::sync::LazyLock;
static LABELS: LazyLock<Vec<String>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("labels.json")).expect("catalog speech labels")
});

fn matches(text: &str) -> Vec<(usize, usize, &'static str)> {
    // Byte offsets are sufficient because every match is a complete UTF-8 label.
    let mut covered = vec![false; text.len()];
    let mut found = vec![];
    for label in LABELS.iter() {
        let mut start = 0;
        while let Some(offset) = text[start..].find(label) {
            let begin = start + offset;
            let end = begin + label.len();
            if !covered[begin..end].iter().any(|v| *v) {
                covered[begin..end].fill(true);
                found.push((begin, end, label.as_str()));
            }
            start = begin + text[begin..].chars().next().unwrap().len_utf8();
        }
    }
    found
}
pub(crate) fn mentioned(text: &str) -> Vec<String> {
    matches(text)
        .iter()
        .map(|(_, _, s)| (*s).to_owned())
        .collect()
}
pub(super) fn rewrite(text: &str, d: &Value) -> (String, Vec<(String, String)>) {
    let Some(rules) = d["speech_name_corrections"].as_object() else {
        return (text.into(), vec![]);
    };
    let rules: std::collections::HashMap<_, _> = rules
        .iter()
        .filter_map(|(s, t)| t.as_str().map(|t| (strip(s), strip(t))))
        .filter(|(s, t)| !s.is_empty() && !t.is_empty() && s != t)
        .collect();
    let mut spans: Vec<_> = matches(text)
        .into_iter()
        .filter_map(|(a, b, source)| rules.get(source).map(|target| (a, b, source, *target)))
        .collect();
    spans.sort_by(|a, b| b.cmp(a));
    let mut corrected = text.to_owned();
    let mut applied = vec![];
    for (a, b, source, target) in spans {
        corrected.replace_range(a..b, target);
        applied.push((source.into(), target.into()));
    }
    applied.reverse();
    (corrected, applied)
}
fn unlisted(text: &str, d: &Value) -> bool {
    let mut labels = d["allowed_speech_labels"]
        .as_array()
        .map(|v| {
            v.iter()
                .filter_map(Value::as_str)
                .map(|s| strip(s).to_owned())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    // The smell source may be named as a smell, but is never a visual entity.
    let smell = &d["world_context"]["observations"]["smell"];
    if smell["status"] == "present" && has(text, SMELL) {
        labels.extend(
            mentioned(smell["description"].as_str().unwrap_or(""))
                .iter()
                .map(|s| strip(s).to_owned()),
        );
    }
    matches(text)
        .iter()
        .any(|(_, _, label)| !labels.iter().any(|s| s == label))
}
const SMELL: &[&str] = &[
    "匂",
    "におい",
    "臭",
    "くさい",
    "くさっ",
    "香り",
    "香る",
    "鼻につ",
];
fn has(text: &str, values: &[&str]) -> bool {
    values.iter().any(|v| text.contains(v))
}
fn positive(text: &str) -> bool {
    // These exclusions cover the four closed conditional sensing patterns;
    // they are not a general negation classifier.
    for c in re(r"(?:匂い|におい|臭い|香り)(?:が|は|の)?(する|した|して|漂|残|来|きた)")
        .captures_iter(text)
    {
        let tail = &text[c.get(0).unwrap().end()..];
        let blocked = match &c[1] {
            "する" | "した" => tail.starts_with("なら"),
            "して" => tail.starts_with("たら") || tail.starts_with("れば"),
            _ => false,
        };
        if !blocked {
            return true;
        }
    }
    for pattern in [r"(?:匂|臭|にお)う", r"香る"] {
        if re(pattern)
            .find_iter(text)
            .any(|m| !text[m.end()..].starts_with("なら"))
        {
            return true;
        }
    }
    for c in re(r"(?:匂|臭|にお)(って|った)").captures_iter(text) {
        let tail = &text[c.get(0).unwrap().end()..];
        let blocked = if &c[1] == "って" {
            tail.starts_with("たら") || tail.starts_with("れば")
        } else {
            tail.starts_with('ら')
        };
        if !blocked {
            return true;
        }
    }
    re(r"(?:なんか|何か).{0,5}(?:臭い|くさい)").is_match(text) || has(text, &["くさっ", "臭っ"])
}
fn smell_question(text: &str) -> bool {
    (text.ends_with('？')||text.ends_with('?')) && (
        re(r"(?:匂い|におい|臭い|香り)(?:が|は|の)?(?:する|した|してる|漂う|残る|来た|きた)(?:ん|の|か|と思う|って感じ)?[？?]$").is_match(text)
        ||re(r"(?:匂う|におう|臭う|香る|くさい|生臭い|臭い)(?:ん|の|か|と思う)?[？?]$").is_match(text))
}
fn olfactory(text: &str, d: &Value) -> bool {
    if d["world_context"]["observations"]["smell"]["status"] == "present" {
        return false;
    }
    let text = compact(text);
    if text.is_empty() || !has(&text, SMELL) {
        return false;
    }
    let user = compact(d["user_text"].as_str().unwrap_or(""));
    if has(&user, SMELL) {
        let figurative = [
            "言葉",
            "ことば",
            "句",
            "川柳",
            "俳句",
            "表現",
            "文章",
            "文体",
            "作品",
            "物語",
            "詩",
            "比喩",
            "ニュアンス",
        ]
        .iter()
        .any(|v| text.contains(v) && user.contains(v));
        let actual = re(r"[^。！？!?]+[。！？!?]?")
            .find_iter(&text)
            .any(|m| !smell_question(m.as_str()) && positive(m.as_str()));
        if figurative || !actual {
            return false;
        }
    }
    true
}
fn dismissive(text: &str) -> bool {
    let text = compact(text);
    [r"(?:^|[、。！？!?])(?:もう)?(?:ほっとけ|放っとけ|ほっといて|放っといて)(?:や|よ|くれ)?[。！？!?]*$",
        r"(?:^|[、。！？!?])(?:あっち|向こう)(?:へ|に)?(?:行け|行って)(?:や|よ)?[。！？!?]*$",
        r"(?:^|[、。！？!?])(?:うるさい|うるさいわ|黙れ)[。！？!?]*$",
        r"(?:話しかけ|構わ)(?:んといて|ないで)[。！？!?]*$"].iter().any(|p|re(p).is_match(&text))
}
fn travel(text: &str, d: &Value) -> bool {
    if d["player_turn_plan"] != "return_home" && d["safety_priority"] != "seek_safe_place" {
        return false;
    }
    let text = compact(text);
    re(r"(?:遠出(?:し|する|して|せえ)|遠くまで(?:行|い)|もう少し(?:先|遠く).{0,8}(?:行|進)|(?:冒険|探索)(?:しよ|しよう|続けよ|続けよう|行こ|行こう)|先へ(?:行|進)|出かけ(?:よ|よう|へんか))").is_match(&text)
        &&!re(r"(?:遠出.{0,6}(?:やめ|控え|せん)|遠くまで行か(?:ん|ず)|先へ行か(?:ん|ず)|探索.{0,6}(?:やめ|控え|せん)|冒険.{0,6}(?:やめ|控え|せん))").is_match(&text)
}
/// The reason priority follows player_chat_style_rejection_reason, not the
/// incidental order of the boolean checks in is_style_acceptable.
pub(super) fn style_issue(text: &str, d: &Value) -> Option<&'static str> {
    if has(text, &["溶岩に飛び", "溶岩に入", "奈落に飛び", "Voidに"]) || forbidden(text, d)
    {
        return Some("unsafe_combat_advice");
    }
    if d["speech_whitelist_enforce"] == true && unlisted(text, d) {
        return Some("unobserved_entity_name");
    }
    if olfactory(text, d) {
        return Some("unsupported_olfactory_claim");
    }
    if dismissive(text) {
        return Some("dismissive_tone");
    }
    if travel(text, d) {
        return Some("conflicting_travel_guidance");
    }
    if repeated(text)||text.contains("んかやんか")||re(r"(やわ|やん|やろ|やんか)(?:[！？!?,，．。…〜ー\s]{0,3})(やわ|やん|やろ|やんか)(?:[！？!?,，．。…〜ー\s]{0,3})(やわ|やん|やろ|やんか)").is_match(text){return Some("broken_repetition");}
    None
}
