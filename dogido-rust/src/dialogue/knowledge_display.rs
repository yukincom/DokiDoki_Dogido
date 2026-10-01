//! 出典は発言と一緒に上限付き履歴へ置く。別の無期限cacheを作らない。
use crate::knowledge::Source;
use serde_json::{Value, json};
use std::collections::HashMap;

pub fn attach(row: &mut Value, result: &Value) {
    if result["knowledge_status"].is_null() {
        return;
    }
    row["category"] = "knowledge".into();
    row["knowledge_status"] = result["knowledge_status"].clone();
    let sources: Vec<Source> =
        serde_json::from_value(result["references"].clone()).unwrap_or_default();
    let references = sources
        .into_iter()
        .map(|source| {
            let id = source.display_id();
            let mut reference = serde_json::to_value(source).unwrap();
            reference["reference_id"] = id.into();
            reference
        })
        .collect::<Vec<_>>();
    row["reference_ids"] = json!(
        references
            .iter()
            .map(|r| &r["reference_id"])
            .collect::<Vec<_>>()
    );
    row["references"] = json!(references);
}

pub fn collect(rows: &[&Value]) -> Vec<Value> {
    let mut references: Vec<Value> = Vec::new();
    let mut indices = HashMap::new();
    for row in rows {
        for reference in row["references"].as_array().into_iter().flatten() {
            let Some(id) = reference["reference_id"].as_str() else {
                continue;
            };
            let index = *indices.entry(id).or_insert_with(|| {
                let mut entry = reference.clone();
                entry["first_seen_at"] = row["created_at"].clone();
                entry["utterance_ids"] = json!([]);
                references.push(entry);
                references.len() - 1
            });
            let entry = &mut references[index];
            entry["last_seen_at"] = row["created_at"].clone();
            entry["utterance_ids"]
                .as_array_mut()
                .unwrap()
                .push(row["utterance_id"].clone());
        }
    }
    references
}
