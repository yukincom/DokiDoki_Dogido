//! Small dictionary description for one explicitly named kind. Never presence evidence.
use crate::{chat_catalog, chat_validation};

pub(super) fn named_mob(user: &str) -> Option<String> {
    let mut names = chat_validation::mentioned(user);
    names.sort();
    names.dedup();
    let [name] = names.as_slice() else {
        return None;
    };
    let mut entries = chat_catalog::catalog()
        .all_mob_entries()
        .values()
        .filter(|entry| entry["label"].as_str() == Some(name.as_str()));
    let entry = entries.next()?;
    if entries.next().is_some() {
        return None;
    }
    let hints = entry["poetic"]["visual_tags"]
        .as_array()?
        .iter()
        .filter_map(|v| v.as_str())
        .take(3)
        .collect::<Vec<_>>();
    (!hints.is_empty()).then(|| format!("{name}：{}", hints.join("、")))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_one_explicit_name_reads_description_tags() {
        assert_eq!(
            named_mob("エンダーマン"),
            Some("エンダーマン：黒い、長身、紫の目".into())
        );
        assert_eq!(
            named_mob("エンダーマン、エンダーマンだよ"),
            named_mob("エンダーマン")
        );
        for input in [
            "そうだね",
            "ちっちゃいね",
            "長身の黒いやつ",
            "これ何",
            "エンダーマンとゾンビ",
        ] {
            assert!(named_mob(input).is_none(), "{input}");
        }
    }
}
