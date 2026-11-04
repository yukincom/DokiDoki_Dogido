//! 見込み: 同じ音源・音種は一行の継続材料になり、別音源や質問用記録は保たれる。
//! 合格: 最新の観測事実、提示区分、保持音の元時刻、入力記録の不変を照合する。実モデル・実音・音声配送は対象外。
use super::*;

fn sound(source: Option<&str>, event: &str, age_ms: i64, current: bool) -> SoundCandidate {
    SoundCandidate { current, memo: serde_json::from_value(json!({
        "kind":"ambient", "mob_type":"iron_golem", "label_ja":"アイアンゴーレム",
        "direction":"後ろ", "distance_band":"近く", "heard_at_us":1_000_000_000-age_ms*1000,
        "dedupe_key":"internal", "source_id":source, "sound_event":event,
        "observation":{"description":"アイアンゴーレムの足音", "sound_event":event,
            "direction":{"horizontal":"back"}, "certainty":"low", "heard_ago_ms":age_ms,
            "emitter":null, "emitter_attribution":"unknown", "source_survey_status":"truncated"}
    })).unwrap() }
}

fn hearing() -> Value {
    json!({"status":"observed", "observer":"dogido", "observed_at":"1970-01-01T00:16:40Z",
        "rain_heard":false, "sounds":[]})
}

// 見込み: 同じ音源・音種の古い音や再配送は一行になり、次の判断でも新着には戻らない。
// 合格: 最新の確度・由来だけが残り、方向変更後も継続提示。元の全記録は完全一致で保持する。純粋な投影試験。
#[test]
fn groups_source_and_kind_and_marks_next_presentation_continued() {
    let mut state = Spontaneous::default();
    let mut newest = sound(Some("golem-a"), "entity.iron_golem.step", 1000, true);
    newest.memo.observation.as_mut().unwrap()["certainty"] = json!("high");
    let originals = vec![sound(Some("golem-a"), "minecraft:entity.iron_golem.step", 6000, false), newest];
    let saved = originals.clone();
    let mut input = hearing();
    state.present(&mut input, originals.clone(), 20_000);
    assert_eq!(input["sounds"].as_array().unwrap().len(), 1);
    let first = &input["sounds"][0];
    assert_eq!(first["presentation"], "初回提示");
    assert_eq!(first["heard_ago_ms"], 1000);
    assert_eq!(first["certainty"], "high");
    assert_eq!(first["emitter_attribution"], "unknown");
    assert_eq!(first["source_survey_status"], "truncated");
    assert!(first.get("basis").is_none());
    assert!(!input.to_string().contains("golem-a"));
    let mut later = originals[1].clone();
    later.memo.observation.as_mut().unwrap()["direction"] = json!({"horizontal":"front"});
    state.present(&mut input, vec![later], 20_000);
    assert_eq!(input["sounds"][0]["presentation"], "継続提示");
    assert_eq!(input["sounds"][0]["direction"]["horizontal"], "front");
    assert_eq!(originals, saved);
}

// 見込み: 音源・音種・警戒区分の違う音は消さず、追跡不能な音を同じ個体としてまとめない。
// 合格: 別ID、別音種、位置キーの別区画、ID欠落2件が各行で残る。音源同定の実機精度は対象外。
#[test]
fn keeps_distinct_sources_kinds_unknowns_and_hostility_separate() {
    let mut hostile = sound(Some("a"), "entity.iron_golem.step", 1000, true);
    hostile.memo.kind = "hostile".into();
    hostile.memo.observation.as_mut().unwrap()["category"] = json!("hostile");
    let rows = vec![
        sound(Some("a"), "entity.iron_golem.step", 1000, true),
        sound(Some("b"), "entity.iron_golem.step", 1000, true),
        sound(Some("a"), "entity.iron_golem.hurt", 1000, true),
        sound(Some("pos:step:1:2:3"), "entity.iron_golem.step", 1000, true),
        sound(Some("pos:step:2:2:3"), "entity.iron_golem.step", 1000, true),
        sound(None, "entity.iron_golem.step", 1000, true),
        sound(None, "entity.iron_golem.step", 2000, true), hostile,
    ];
    let mut state = Spontaneous::default();
    let mut input = hearing();
    state.present(&mut input, rows, 20_000);
    assert_eq!(input["sounds"].as_array().unwrap().len(), 8);
    assert!(input["sounds"][5].get("presentation").is_none());
    assert!(input["sounds"][6].get("presentation").is_none());
    assert_eq!(input["sounds"][7]["category"], "hostile");
}

// 見込み: 保持音の再提示は新しい発音にならず、保持窓を越えた再出現や失効後は継続扱いしない。
// 合格: 経過時間が増えbasisを維持し、20秒超の別発音・unavailable後・空観測後は初回になる。台帳と投影のみ。
#[test]
fn retained_age_expiry_and_unavailable_do_not_create_false_continuity() {
    let mut state = Spontaneous::default();
    let mut input = hearing();
    let row = sound(Some("a"), "step", 1000, true);
    state.present(&mut input, vec![row.clone()], 20_000);
    input["observed_at"] = json!("1970-01-01T00:16:45Z");
    let mut retained = row.clone();
    retained.current = false;
    state.present(&mut input, vec![retained], 20_000);
    assert_eq!(input["sounds"][0]["heard_ago_ms"], 6000);
    assert_eq!(input["sounds"][0]["basis"], "retained_sound");
    assert_eq!(input["sounds"][0]["presentation"], "継続提示");
    let mut later = row.clone();
    later.memo.heard_at_us += 30_000_000;
    input["observed_at"] = json!("1970-01-01T00:17:10Z");
    state.present(&mut input, vec![later.clone()], 20_000);
    assert_eq!(input["sounds"][0]["presentation"], "初回提示");
    state.present(&mut json!({"status":"unavailable","sounds":[]}), vec![], 20_000);
    state.present(&mut input, vec![later.clone()], 20_000);
    assert_eq!(input["sounds"][0]["presentation"], "初回提示");
    state.present(&mut input, vec![], 20_000);
    state.present(&mut input, vec![later], 20_000);
    assert_eq!(input["sounds"][0]["presentation"], "初回提示");
}

// 見込み: 自発判断の間隔が20秒を超えても、同じ音の実聴取が続いていれば初回へ戻らない。
// 合格: 10秒ごとの実観測を挟んだ40秒後は継続、観測のない30秒後の再出現は初回。観測列の状態試験のみ。
#[test]
fn continuous_observations_bridge_long_intervals_between_presentations() {
    let mut state = Spontaneous::default();
    let mut input = hearing();
    let mut row = sound(Some("a"), "step", 1000, true);
    state.present(&mut input, vec![row.clone()], 20_000);
    for seconds in [10, 20, 30, 40] {
        row.memo.heard_at_us = 999_000_000 + seconds * 1_000_000;
        state.refresh(1_000_000_000 + seconds * 1_000_000, vec![row.clone()]);
    }
    input["observed_at"] = json!("1970-01-01T00:17:20Z");
    state.present(&mut input, vec![row.clone()], 20_000);
    assert_eq!(input["sounds"][0]["presentation"], "継続提示");
    row.memo.heard_at_us += 30_000_000;
    state.refresh(1_070_000_000, vec![row.clone()]);
    input["observed_at"] = json!("1970-01-01T00:17:50Z");
    state.present(&mut input, vec![row], 20_000);
    assert_eq!(input["sounds"][0]["presentation"], "初回提示");
}
