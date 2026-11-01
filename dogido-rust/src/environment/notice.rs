//! 共有観測から、個体・座標に依存しない周辺の構成と、その変化を取り出す。
//! 比較と間隔は会話観測のStateが所有し、モデルは既存の環境対話で発話／沈黙を選ぶ。
//! Snapshotは前回の定期観測。発話・沈黙・失敗に関係なく、観測の周期で比較基準を進める。
use crate::conversation_observation::{Change, Context};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

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

/// 身体の視認配列は遮られた個体を含まない。個体ID・順番ではなく種類ごとに数える。
pub(crate) fn visible_hostiles(event: &crate::events::GameEvent) -> BTreeMap<&str, usize> {
    let mut counts = BTreeMap::new();
    for target in &event.visual_threats {
        *counts.entry(target.r#type.as_str()).or_default() += 1;
    }
    counts
}

pub fn snapshot(context: &Context) -> Snapshot {
    let observations = &context.observations;
    let mut table: Table = [
        "nearby_mob_types",
        "visible_hostiles",
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
        "subject_states",
    ]
    .into_iter()
    .map(|key| (key.to_owned(), observations[key].clone()))
    .collect();
    table.insert(
        "nearby_mob_types".into(),
        mob_kind_signature(&observations["nearby_mob_types"]),
    );
    let hearing = &observations["hearing"];
    if hearing["observer"] == "dogido" && let Some(sounds) = hearing["sounds"].as_array() {
        // 時刻・方向・距離・個体IDでは同じ声を毎回新規扱いしない。呼称の確度は既存の聴覚表現に従う。
        let descriptions: BTreeSet<_> = sounds
            .iter()
            .filter(|sound| sound["category"] == "hostile")
            .filter_map(|sound| sound["description"].as_str())
            .collect();
        table.insert("heard_hostile_sounds".into(), json!(descriptions));
    }
    if let Some(smell) = table.get_mut("smell") {
        *smell = super::ambient::smell_change_signature(smell);
    }
    if let Some(states) = table.get_mut("subject_states") {
        *states = crate::conversation_observation::subjects::signature(states);
    }
    Snapshot {
        revision: context.revision,
        dimension: observations["dimension"].clone(),
        table,
    }
}

/// 数量・職業構成は当回の会話材料に留め、種類の到着には数えない。
pub fn mob_kind_signature(value: &Value) -> Value {
    value
        .as_array()
        .map(|rows| {
            json!(
                rows.iter()
                    .map(|row| json!({"id":row["id"],"label":row["label"]}))
                    .collect::<Vec<_>>()
            )
        })
        .unwrap_or(Value::Null)
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
        // 視野外へ出た個体や保持期限を過ぎた音は、撃破や鳴き止んだ証拠にしない。
        let noticed = match key.as_str() {
            "visible_hostiles" => before.as_object().zip(value.as_object()).is_some_and(|(a, b)| {
                b.iter().any(|(kind, count)| count.as_u64() > a.get(kind).and_then(Value::as_u64))
            }),
            "heard_hostile_sounds" => before.as_array().zip(value.as_array()).is_some_and(|(a, b)| {
                b.iter().any(|description| !a.contains(description))
            }),
            _ => before != value,
        };
        if noticed {
            changes.push(Change {
                kind: key.clone(),
                before: before.clone(),
                now: value.clone(),
                observed_at: observed_at(current),
                revision: current.revision,
            });
        }
    }
    // 戦闘の遷移は全snapshot間にも起きる。既存の未消費差分をこの比較周期に一度だけ含める。
    changes.extend(
        current
            .changes
            .iter()
            .filter(|c| {
                matches!(c.kind.as_str(), crate::conversation_observation::PLAYER_HOSTILES_CLEARED
                    | crate::conversation_observation::PLAYER_HOSTILE_DEFEATED)
                    && c.revision > previous.revision
            })
            .cloned(),
    );
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
