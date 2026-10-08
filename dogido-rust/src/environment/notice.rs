//! 共有観測から、個体・座標に依存しない周辺の構成と、その変化を取り出す。
//! 比較と間隔は会話観測のStateが所有し、モデルは既存の環境対話で発話／沈黙を選ぶ。
//! Snapshotは前回の定期観測。発話・沈黙・失敗に関係なく、観測の周期で比較基準を進める。
use crate::conversation_observation::{Change, Context};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeMap;

pub const KIND: &str = "surroundings_notice";

/// 観測値の比較対象。値がnullなら今回未確認であり、空集合（範囲内で未検出）と分ける。
pub type Table = BTreeMap<String, Value>;

/// 前回の定期観測の周辺構成。生成本文から作った情報は入らない。
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub revision: u64,
    pub dimension: Value,
    pub table: Table,
}

pub fn snapshot(context: &Context) -> Snapshot {
    let observations = &context.observations;
    let mut table: Table = [
        "nearby_mob_types",
        "nearby_block_types",
        "location_biome",
        "place",
        "structure",
        "biome_scene_visible",
        "overhead_cover",
        "weather",
        "rain_heard",
        "time_phase",
        "smell",
    ]
    .into_iter()
    .map(|key| (key.to_owned(), observations[key].clone()))
    .collect();
    if let Some(smell) = table.get_mut("smell").and_then(Value::as_object_mut) {
        // 向きや同種の勝者が変わっただけでは新しい匂いの気づきにしない。
        // 方角・説明は当回の観測には残すが、気づきの再発火には使わない。
        smell.remove("direction_estimate");
        smell.remove("description");
    }
    Snapshot {
        revision: context.revision,
        dimension: observations["dimension"].clone(),
        table,
    }
}

/// 同じ種類の個体交代・個数・並び・座標は、元の種類集合に含めない。
/// Tableの文字列一覧は受信時と投影時にソート・重複除去してから比較する。
pub fn changes(previous: &Snapshot, current: &Context) -> Vec<Change> {
    let now = snapshot(current);
    let mut changes = Vec::new();
    if previous.dimension != now.dimension {
        changes.push(Change {
            kind: "dimension".into(),
            before: previous.dimension.clone(),
            now: now.dimension.clone(),
            observed_at: observed_at(current),
            revision: current.revision,
        });
    }
    for (key, value) in &now.table {
        let before = previous.table.get(key).unwrap_or(&Value::Null);
        if before != value {
            changes.push(Change {
                kind: key.clone(),
                before: before.clone(),
                now: value.clone(),
                observed_at: observed_at(current),
                revision: current.revision,
            });
        }
    }
    changes
}

fn observed_at(context: &Context) -> String {
    context.observations["observed_at"]
        .as_str()
        .unwrap_or("")
        .into()
}

/// 当回の変化から発話機会を作る。種類の出入りでは生成中の発話を取り消さない。
pub fn action() -> crate::combat::model::Speech {
    use crate::combat::model::{LeafRequest, Scope, Speech};
    let mut action = Speech::new(KIND, "周りの様子、ちょっと変わったな。");
    action.scope = Scope::Safe;
    action.leaf = Some(LeafRequest {
        kind: super::reaction::KIND.into(),
        details: json!({}),
        temperature: 0.65,
    });
    action
}

#[cfg(test)]
mod tests;
