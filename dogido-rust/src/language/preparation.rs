//! 国語対話の指示文と、計算・検索済みの値だけを使う回答。外部通信しない。
use crate::{
    text_format::spaced_json,
    types::{ChatMessage, Role},
};
use icu_normalizer::ComposingNormalizer;
use serde_json::{Value, json};
use std::sync::LazyLock;

static SYSTEMS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("prompts.json")).expect("checked language prompts")
});

pub(super) fn messages(kind: &str, details: &Value) -> Vec<ChatMessage> {
    let mut background = details.as_object().expect("language details").clone();
    let current = background.shift_remove("current").unwrap_or(json!({}));
    vec![
        ChatMessage {
            role: Role::System,
            content: crate::companion_prompt::expand(
                SYSTEMS[kind].as_str().expect("language kind"),
            ),
        },
        ChatMessage {
            role: Role::User,
            content: format!(
                "{}\n以上は背景。今回応答する最新の発話はこちら：\n{}",
                spaced_json(&Value::Object(background)),
                spaced_json(&current)
            ),
        },
    ]
}

pub(super) fn comparison_fact(interpretation: &Value, text: &str) -> Option<Value> {
    let target = interpretation["target"].as_str()?;
    if interpretation["facet"] != "comparison" || target.chars().count() != 1 {
        return None;
    }
    let count = text.matches(target).count();
    (count >= 2).then(|| json!({
        "id":format!("input-character:{:x}", target.chars().next().unwrap() as u32),
        "title_ja":"入力文字の比較",
        "text_ja":format!("今回の入力に同一文字『{target}』が{count}回ある。文字列比較の結果。"),
        "claim_status":"input_character_comparison", "sources":[],
    }))
}

pub(super) fn fixed_reply(interpretation: &Value, facts: &[Value]) -> Option<Value> {
    let target = interpretation["target"].as_str()?;
    let normalized = ComposingNormalizer::new_nfkc().normalize(target);
    if interpretation["facet"] == "mora_count" {
        // 計算対象と資料の表記を同じ空白規則で照合する。C0区切りも前後の空白として除く。
        let surface = normalized
            .trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c));
        for fact in facts {
            let c = &fact["calculation"];
            if c["operation"] == "mora_count"
                && c["surface"].as_str() == Some(surface)
                && let Some(value) = c["value"].as_u64()
                && let Some(id) = fact["id"].as_str()
            {
                return Some(answer(
                    format!("「{surface}」は{value}音やで。"),
                    id,
                    "発話にある確定かなを既存の音数計算で数えた。",
                ));
            }
        }
    }
    if interpretation["facet"] == "grade" {
        let character: String = normalized
            .chars()
            .map(|c| match c {
                '0' => '零',
                '1' => '一',
                '2' => '二',
                '3' => '三',
                '4' => '四',
                '5' => '五',
                '6' => '六',
                '7' => '七',
                '8' => '八',
                '9' => '九',
                _ => c,
            })
            .collect();
        if character.chars().count() != 1 {
            return None;
        }
        for fact in facts {
            let allocation = &fact["allocation"];
            if allocation["character"] == character
                && allocation["scope"] == "character_only"
                && let Some(grade @ 1..=6) = allocation["school_grade"].as_u64()
                && let Some(id) = fact["id"].as_str()
            {
                return Some(answer(
                    format!("「{character}」は小学{grade}年生で習う漢字やで。"),
                    id,
                    "既に検索した学年別漢字配当表の字種の配当。",
                ));
            }
        }
    }
    None
}

fn answer(text: String, id: &str, application: &str) -> Value {
    json!({"status":"answer","text":text,"fact_ids":[id],"application":application,
        "missing":"","missing_kind":"none","clarification":""})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn checkpoint_preparation_matches_fixture() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../fixtures/language-preparation.json")).unwrap();
        for case in fixture["prompts"].as_array().unwrap() {
            assert_eq!(
                serde_json::to_value(messages(case["kind"].as_str().unwrap(), &case["details"]))
                    .unwrap(),
                case["expected"]
            );
        }
        for case in fixture["replies"].as_array().unwrap() {
            assert_eq!(
                fixed_reply(&case["interpretation"], case["facts"].as_array().unwrap())
                    .unwrap_or(Value::Null),
                case["expected"],
                "{}",
                case["name"]
            );
        }
        for case in fixture["comparisons"].as_array().unwrap() {
            let facts: Vec<_> = comparison_fact(
                &json!({"facet":"comparison","target":case["target"]}),
                case["text"].as_str().unwrap(),
            )
            .into_iter()
            .collect();
            assert_eq!(json!(facts), case["expected"]);
        }
    }
}
