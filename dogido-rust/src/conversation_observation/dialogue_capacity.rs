//! 台帳から対話用のコピーを作る。周囲のブロックは普段は実測で少ない順に選んだ最大三種類、混雑時は省く。
//! 視界・位置・視認の時系列と、地点の天候・バイオームは削らない。
use serde_json::Value;

const MOBS: usize = 8;
pub(super) const LOCAL_HIGHLIGHTS: &str = "今いる場所にある少し特別なもの";
const SOUNDS: usize = 4;

/// 話題に合わせたモブの絞り込み前に、当回の一覧で混雑を判定する。
pub(crate) fn crowded(world: &Value) -> bool {
    let o = &world["observations"];
    let count = |key: &str| o[key].as_array().map_or(0, Vec::len);
    let sounds = o["hearing"]["sounds"].as_array().map_or(0, Vec::len);
    count("mob_states") + count("nearby_mob_types") + count("nearby_block_types") + sounds > 20
        || o["nearby_block_types_partial"] == true || o["nearby_mob_types_partial"] == true
}

// [遅延短縮計画 Part 2] docs/2026-11-23-response-latency-reduction-plan.md
// 旧: モデル入力用コピーにも多数のnull項目・長い内部basis文字列・重複するobserved_atが残り、spaced_jsonでトークンを圧迫している。
// 新: モデルへ渡す直前のコピーに限り、意味を落とさないnull項目や不要な内部メタを省いて軽量化する（必要に応じコンパクトJSON化も検討）。
// 意図: 正本台帳や視認・位置関係・匂い・天気・指差しの意味を一切損なわず、プロンプト読み込み時間（実測4.88秒）を1.5〜2.5秒短縮する。
fn strip_null_fields(value: &mut Value) {
    match value {
        Value::Object(object) => {
            object.retain(|key, child| {
                if child.is_null() {
                    // 変化の before/now の null は「なし→あり」「あり→なし」の意味を持つため保持する。
                    matches!(key.as_str(), "before" | "now")
                } else {
                    true
                }
            });
            for child in object.values_mut() {
                strip_null_fields(child);
            }
        }
        Value::Array(rows) => {
            for child in rows {
                strip_null_fields(child);
            }
        }
        _ => {}
    }
}

pub(crate) fn compact(world: &mut Value, crowded: bool) {
    strip_null_fields(world);
    if let Some(changes) = world.get_mut("changes").and_then(Value::as_array_mut) {
        // 全種類の増減は内部比較の入力。前回の景色を今の場所の特徴として載せない。
        changes.retain(|row| row["kind"] != "nearby_block_types");
    }
    let Some(o) = world.get_mut("observations").and_then(Value::as_object_mut) else {
        return;
    };
    o.remove("nearby_block_types");
    o.remove("nearby_block_types_partial");
    if !crowded { return; }
    o.remove(LOCAL_HIGHLIGHTS);
    if let Some(rows) = o.get_mut("mob_states").and_then(Value::as_array_mut)
        && rows.len() > MOBS
    {
        // 「＋」の対象・名前のある個体・近づく個体、次に視認した敵。同順位は既存の観測順を守る。
        rows.sort_by_key(|r| {
            if r["basis"] == "look_target" {
                0
            } else if r["custom_name"].is_string() || r["approaching"] == true {
                1
            } else if r["basis"] == "visual" {
                2
            } else {
                3
            }
        });
        rows.truncate(MOBS);
        o.insert("mob_states_partial".into(), true.into());
    }
    let visible: Vec<_> = o
        .get("mob_states")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|r| r["catalog_id"].as_str().map(str::to_owned))
        .collect();
    if let Some(rows) = o.get_mut("nearby_mob_types").and_then(Value::as_array_mut)
        && rows.len() > MOBS
    {
        rows.sort_by_key(|r| !r["id"].as_str().is_some_and(|id| {
            visible.iter().any(|v| v.trim_start_matches("minecraft:") == id.trim_start_matches("minecraft:"))
        }));
        rows.truncate(MOBS);
        o.insert("nearby_mob_types_partial".into(), true.into());
    }
    if let Some(hearing) = o.get_mut("hearing")
        && let Some(rows) = hearing.get_mut("sounds").and_then(Value::as_array_mut)
        && rows.len() > SOUNDS
    {
        rows.sort_by_key(|r| {
            (
                r["category"] != "hostile",
                r["heard_ago_ms"].as_u64().unwrap_or(u64::MAX),
            )
        });
        rows.truncate(SOUNDS);
        hearing["truncated"] = true.into();
    }
    // 同じ一覧の直前値もモデルへ丸ごと重複送信しない。比較・発話判断・検討済みrevisionは元台帳のまま。
    if let Some(changes) = world.get_mut("changes").and_then(Value::as_array_mut) {
        for c in changes {
            if c["kind"] != "nearby_mob_types" { continue; }
            for k in ["before", "now"] {
                if let Some(rows) = c[k].as_array_mut()
                    && rows.len() > MOBS
                {
                    rows.truncate(MOBS);
                    c["partial"] = true.into();
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    // 見込み: 混雑時も相棒の位置、指した羊、身近な敵、雨は読める。後順位の一覧は全文ではなく部分観測として渡す。
    // 合格: 末尾の指差し対象/敵音も残し、時刻関係・天候は同一。元台帳は不変。ブロック本文と差分はない。モデル応答は未検査。
    #[test]
    fn crowded_model_copy_keeps_the_player_target_and_weather() {
        let blocks = (0..25).map(|n| json!({"id":n})).collect::<Vec<_>>();
        let mut mobs = (0..14)
            .map(|i| json!({"subject":i,"basis":"passive_observation"}))
            .collect::<Vec<_>>();
        mobs.push(json!({"label":"羊","basis":"look_target"}));
        let mut sounds = (0..6)
            .map(|n| json!({"heard_ago_ms":n}))
            .collect::<Vec<_>>();
        sounds.push(json!({"category":"hostile","description":"うなり声","heard_ago_ms":80}));
        let raw = json!({"observations":{"mob_states":mobs,"nearby_block_types":blocks,"image_timing":["撮影の約3秒後"],
            "body_observation":{"position":"左前"},"location_biome":"山","weather":"雪",LOCAL_HIGHLIGHTS:["花"],"hearing":{"sounds":sounds}},
            "changes":[{"kind":"nearby_block_types","before":blocks,"now":blocks}]});
        let mut model = raw.clone();
        let busy = crowded(&model);
        compact(&mut model, busy);
        let o = &model["observations"];
        for k in [
            "body_observation",
            "image_timing",
            "location_biome",
            "weather",
        ] {
            assert_eq!(o[k], raw["observations"][k]);
        }
        assert_eq!(o["mob_states"][0]["label"], "羊");
        assert_eq!(o["mob_states"].as_array().unwrap().len(), MOBS);
        assert!(o.get("nearby_block_types").is_none());
        assert!(o.get(LOCAL_HIGHLIGHTS).is_none());
        assert_eq!(o["hearing"]["sounds"][0]["description"], "うなり声");
        assert_eq!(o["hearing"]["truncated"], true);
        assert!(model["changes"].as_array().unwrap().is_empty());
        assert_eq!(
            raw["observations"]["nearby_block_types"]
                .as_array()
                .unwrap()
                .len(),
            25
        );
    }

    #[test]
    fn compact_strips_null_fields_without_mutating_source_or_change_sentinels() {
        let raw = json!({
            "observations": {
                "mob_states": [
                    {"catalog_id": "wolf", "custom_name": null, "hunger": null, "tamed": true, "basis": "look_target"}
                ],
                "weather": "clear",
                "unused": null
            },
            "changes": [
                {"kind": "weather", "before": null, "now": "rain"}
            ]
        });
        let mut model = raw.clone();
        compact(&mut model, false);
        let wolf = &model["observations"]["mob_states"][0];
        assert!(wolf.get("custom_name").is_none());
        assert!(wolf.get("hunger").is_none());
        assert_eq!(wolf["tamed"], true);
        assert!(model["observations"].get("unused").is_none());
        assert!(model["changes"][0].get("before").is_some());
        assert!(model["changes"][0]["before"].is_null());
        assert!(raw["observations"]["mob_states"][0]["hunger"].is_null());
    }
}
