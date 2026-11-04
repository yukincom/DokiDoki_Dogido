//! 既存の視認と変化を、通常対話・環境反応の送信時だけ現在／直前へ分ける。
//! 話題の選別・混雑時の省略より前に種類を取り出す。保存・観測・戦闘判断は変更しない。
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Default)]
pub(crate) struct Sight {
    current: BTreeMap<String, String>,
    previous: BTreeMap<String, String>,
    partial: bool,
}

fn id(value: &str) -> &str {
    value.strip_prefix("minecraft:").unwrap_or(value)
}

fn label(kind: &str, row: &Value) -> String {
    row["label"].as_str().map(str::to_owned)
        .unwrap_or_else(|| crate::environment::ambient::mob_label(kind))
}

fn rows(value: &Value, quantities: bool) -> BTreeMap<String, String> {
    value.as_array().into_iter().flatten().filter_map(|row| {
        let kind = id(row["id"].as_str()?);
        let mut text = label(kind, row);
        if quantities {
            if let Some(n) = row["quantity"].as_u64() {
                text.push_str(&format!("（{n}体）"));
            } else if let Some(n) = row["quantity"].as_str() {
                text.push_str(&format!("（{n}）"));
            }
            if let Some(groups) = row["profession_breakdown"]["groups"].as_array() {
                let groups = rows(&Value::Array(groups.clone()), true).into_values().collect::<Vec<_>>();
                if !groups.is_empty() { text.push_str(&format!("：{}", groups.join("、"))); }
            }
        }
        Some((kind.to_owned(), text))
    }).collect()
}

fn hostiles(value: &Value) -> BTreeMap<String, String> {
    value.as_object().into_iter().flat_map(|v| v.keys()).map(|kind| {
        (id(kind).to_owned(), crate::environment::ambient::mob_label(kind))
    }).collect()
}

impl Sight {
    pub(crate) fn from_world(world: &Value) -> Self {
        let o = &world["observations"];
        let mut current = rows(&o["nearby_mob_types"], true);
        for (kind, count) in o["visible_hostiles"].as_object().into_iter().flatten() {
            let already_seen = current.contains_key(id(kind));
            let text = current.entry(id(kind).to_owned())
                .or_insert_with(|| crate::environment::ambient::mob_label(kind));
            if let Some(n) = count.as_u64() {
                // AmbientMobObserverと敵の視認は個体ごとに排他的。双方の数量を同じ集合と誤記しない。
                text.push_str(&format!("（{}交戦相手{n}体を視認）", if already_seen { "ほかに" } else { "" }));
            }
        }
        for row in o["mob_states"].as_array().into_iter().flatten() {
            // companion候補は視認を保証しない。個体の状態から種類の視認を補うのも実視認だけ。
            if !matches!(row["basis"].as_str(), Some("visual" | "passive_observation")) { continue; }
            if let Some(kind) = row["catalog_id"].as_str() {
                current.entry(id(kind).to_owned()).or_insert_with(|| label(kind, row));
            }
        }
        let partial = o["nearby_mob_types_partial"] == true
            || !o["nearby_mob_types"].is_array() || !o["visible_hostiles"].is_object()
            || o["body_observation"]["current"]["senses"]["incomplete"].as_array()
                .is_some_and(|items| items.iter().any(|v| v == "visual_threats"));
        let changes = world["changes"].as_array();
        let mut previous = BTreeMap::new();
        if !partial && !changes.is_some_and(|rows| rows.iter().any(|c| c["kind"] == "dimension")) {
            for change in changes.into_iter().flatten().filter(|c| c["partial"] != true) {
                let before = match change["kind"].as_str() {
                    Some("nearby_mob_types") if change["now"].is_array() => rows(&change["before"], false),
                    Some("visible_hostiles") if change["now"].is_object() => hostiles(&change["before"]),
                    _ => continue,
                };
                previous.extend(before.into_iter().filter(|(kind, _)| !current.contains_key(kind)));
            }
        }
        Self { current, previous, partial }
    }

    /// 見出しへ移した一覧を元の曖昧なキーでも重複送信しない。個体の実測詳細は保持する。
    pub(crate) fn present(&self, world: &mut Value) -> String {
        if let Some(o) = world.get_mut("observations").and_then(Value::as_object_mut) {
            for key in ["nearby_mob_types", "nearby_mob_types_partial", "visible_hostiles"] {
                o.remove(key);
            }
            for row in o.get_mut("mob_states").and_then(Value::as_array_mut).into_iter().flatten() {
                if matches!(row["basis"].as_str(), Some("visual" | "passive_observation")) {
                    row["basis"] = "いま見えとる個体".into();
                }
            }
        }
        if let Some(changes) = world.get_mut("changes").and_then(Value::as_array_mut) {
            changes.retain(|c| !matches!(c["kind"].as_str(), Some("nearby_mob_types" | "visible_hostiles")));
        }
        let mut text = "## いま見えとるもん\n".to_owned();
        if self.partial { text.push_str("（視認情報は一部または未取得）\n"); }
        for value in self.current.values() { text.push_str(&format!("- {value}\n")); }
        text.push_str("\n## さっきまで見えとったもん\n");
        for value in self.previous.values() { text.push_str(&format!("- {value}\n")); }
        text
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    // 見込み: 匂い・候補と視認を分ければ、同時に見える種類と見えなくなった種類を読める。
    // 合格: 以下の現在／過去の集合が一致し、元台帳は不変。純粋な送信表示の試験で、実モデルの解釈は対象外。
    fn world() -> Value {
        json!({"observations":{"nearby_mob_types":[{"id":"minecraft:zombie","label":"ゾンビ","quantity":2},
            {"id":"villager","label":"村人","quantity":1}], "visible_hostiles":{},
            "mob_states":[{"catalog_id":"iron_golem","label":"アイアンゴーレム","basis":"passive_observation"},
                {"catalog_id":"wolf","label":"オオカミ","basis":"companion_observation","tamed":true}],
            "smell":{"components":[{"smell_id":"creeper"}]}, "hearing":{"sounds":[{"description":"クモの音"}]}},
            "changes":[{"kind":"nearby_mob_types","before":[{"id":"zombie","label":"ゾンビ"},
                {"id":"panda","label":"パンダ"}],"now":[{"id":"villager"}]}]})
    }

    // 見込み: 戻ってきたゾンビは過去欄から外れ、姿のないオオカミ・匂い・音は現在欄へ入らない。
    // 合格: 現在3種類・過去パンダだけ。元値と個体の手懐け情報は保持。モデル・実機は未検査。
    #[test]
    fn visible_species_are_distinct_from_past_smell_and_candidates() {
        let original = world();
        let mut model = original.clone();
        let sight = Sight::from_world(&model);
        assert_eq!(sight.current.keys().map(String::as_str).collect::<Vec<_>>(), ["iron_golem", "villager", "zombie"]);
        assert_eq!(sight.previous.keys().map(String::as_str).collect::<Vec<_>>(), ["panda"]);
        let text = sight.present(&mut model);
        assert!(text.contains("- ゾンビ（2体）") && text.contains("## さっきまで見えとったもん\n- パンダ\n"));
        assert!(model["observations"].get("visible_hostiles").is_none());
        assert!(model["observations"].get("nearby_mob_types").is_none());
        assert_eq!(model["observations"]["mob_states"][1]["tamed"], true);
        assert_eq!(model["observations"]["smell"], original["observations"]["smell"]);
        assert_eq!(model["observations"]["hearing"], original["observations"]["hearing"]);
        assert_eq!(original, world());
    }

    // 見込み: 一部取得・未取得・世界変更を、姿が消えた証拠にしない。
    // 合格: それぞれ過去欄が空で、確実な現在視認は残る。送信投影だけを検査。
    #[test]
    fn incomplete_or_changed_world_does_not_invent_disappearance() {
        for mode in 0..4 {
            let mut w = world();
            match mode {
                0 => w["observations"]["nearby_mob_types_partial"] = true.into(),
                1 => w["observations"]["visible_hostiles"] = Value::Null,
                2 => w["observations"]["nearby_mob_types"] = Value::Null,
                _ => w["changes"].as_array_mut().unwrap().push(json!({"kind":"dimension"})),
            }
            let sight = Sight::from_world(&w);
            assert!(sight.previous.is_empty());
            assert!(sight.current.contains_key("iron_golem"));
        }
    }

    // 見込み: 非交戦の同種と交戦相手を併記しても、数量や旧視認を混同しない。
    // 合格: 周囲2体と別の交戦相手1体、旧スケルトンは過去だけ。送信投影の単体試験。
    #[test]
    fn combat_and_ambient_sightings_keep_their_own_counts() {
        let mut w = world();
        w["observations"]["visible_hostiles"] = json!({"zombie":1});
        w["changes"].as_array_mut().unwrap().push(json!({"kind":"visible_hostiles", "before":{"skeleton":2}, "now":{"zombie":1}}));
        let sight = Sight::from_world(&w);
        assert_eq!(sight.current["zombie"], "ゾンビ（2体）（ほかに交戦相手1体を視認）");
        assert!(sight.previous.contains_key("skeleton") && !sight.current.contains_key("skeleton"));
        assert!(!sight.previous.contains_key("zombie"));
    }

    // 見込み: 話題選別・混雑省略の前に視認を写せば、他の種類を過去扱いせず同じ場面に残せる。
    // 合格: 通常質問と環境反応の両送信本文に現在11種、過去パンダだけ。実モデル応答・描画は未検査。
    #[test]
    fn chat_and_environment_keep_all_visible_species_before_topic_filtering() {
        let mut w = world();
        let additional = ["cow", "sheep", "pig", "cat", "chicken", "rabbit", "horse", "turtle"];
        for kind in additional {
            w["observations"]["nearby_mob_types"].as_array_mut().unwrap()
                .push(json!({"id":kind,"label":crate::environment::ambient::mob_label(kind),"quantity":1}));
        }
        w["observations"]["nearby_block_types"] = json!([1,2,3,4,5,6,7,8,9,10,11]);
        for change in w["changes"].as_array_mut().unwrap() {
            change["observed_at"] = "2026-11-04T13:00:00Z".into();
            change["revision"] = 1.into();
        }
        let original = w.clone();
        let chat = crate::chat_prompt::messages(&json!({"user_text":"村人は？","world_context":w})).unwrap();
        let reaction = crate::environment::reaction::Prepared::new(&json!({
            "kind":"environment_reaction","model":"fixture","max_tokens":128,"fallback_text":"",
            "details":{"reaction_context":{"topic":"reassurance","world_context":w,"observations":{},"properties":{}}}
        }), "fixture").unwrap();
        for text in [&chat[1].content, &reaction.request.messages[1].content] {
            let (_, sight) = text.split_once("## いま見えとるもん\n").unwrap();
            let (current, previous) = sight.split_once("## さっきまで見えとったもん\n").unwrap();
            assert_eq!(current.lines().filter(|s| s.starts_with("- ")).count(), 11);
            for name in ["ゾンビ", "村人", "アイアンゴーレム"] { assert!(current.contains(name)); }
            assert!(previous.starts_with("- パンダ\n\n"));
            assert!(!text.contains("\"visible_hostiles\"") && !text.contains("\"nearby_mob_types\""));
        }
        assert_eq!(w, original);
    }
}
