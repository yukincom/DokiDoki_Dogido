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
    let material =
        crate::catalog_knowledge::mob(entry["label"].as_str()?, None, false, "player_named")?;
    serde_json::to_string(&material).ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_one_explicit_name_reads_description_tags() {
        let material: serde_json::Value =
            serde_json::from_str(&named_mob("エンダーマン").unwrap()).unwrap();
        assert_eq!(
            material["general"]["poetic"],
            chat_catalog::catalog().mob_entry("enderman").unwrap()["poetic"]
        );
        assert_eq!(material["basis"], serde_json::json!(["player_named"]));
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
