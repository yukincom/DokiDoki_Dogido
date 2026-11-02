//! 模擬観測と時計で、戦闘判断が選んだ発話・会話可否・中断指定を検査する。実音声・実機は対象外。
use dogido_rust::{
    combat::{
        core::Engine,
        model::{Mode, Settings},
    },
    events::GameEvent,
    threats,
};
use serde_json::{Value, json};

fn event(visual: Value, audio: Value) -> GameEvent {
    GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-09-25T00:00:00Z",
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"name":"fixture","dimension":"minecraft:overworld","health":20,"position":{"x":0,"z":0}},
        "world":{"sky_visible":true,"ceiling_height":20,"time_phase":"night","biome":"plains","local_light":10},
        "visual_threats":visual,"auditory_threats":audio})).unwrap()
}
fn visual(kind: &str, id: &str, d: f64) -> Value {
    json!({"type":kind,"entity_id":id,"distance":d,"direction":{"horizontal":"front","vertical":"same"}})
}
fn sound(id: &str) -> Value {
    json!({"label":"zombie","source_id":id,"spoken_name_allowed":true,"direction":{"horizontal":"right"},"distance_band":"close"})
}
fn rear_creeper(kind: &str, id: &str, fuse: bool) -> Value {
    let mut t = visual(kind, id, 2.0);
    t["direction"]["horizontal"] = "back_left".into();
    t["approaching"] = true.into();
    t["fuse_active"] = fuse.into();
    t
}
fn changed(e: &GameEvent, f: impl FnOnce(&mut Value)) -> GameEvent {
    let mut v = serde_json::to_value(e).unwrap();
    f(&mut v);
    GameEvent::parse(v).unwrap()
}
// 本人が実際に交戦している試験だけに使う。視認や音だけの模擬観測とは区別する。
fn engaged(e: GameEvent) -> GameEvent {
    changed(&e, |v| v["combat"]["player_engaged"] = json!(true))
}

fn tick(engine: &mut Engine, e: &GameEvent, at: u64) -> dogido_rust::combat::core::Decision {
    engine.observe(
        e,
        at,
        true,
        false,
        &Settings::default(),
        &threats::Settings::default(),
    )
}

// 見込み: 戦闘中は視線や遮蔽物が変わっても、遠隔通知は一個体一度。同種の別個体と戦闘終了後の出会いは区別する。
// 合格: 模擬観測でAとBを一度ずつ通知し、Aを見失って再発見しても無言。戦闘終了後に同じAを一度通知する。
#[test]
fn ranged_sighting_tracks_individuals_across_time_direction_and_partial_audio() {
    use dogido_rust::combat::{core::Decision, model::Scope};
    let notice = |d: Decision, id: &str| {
        assert!(d.chat_allowed && !d.stop_audio);
        let [speech] = &d.actions[..] else {
            panic!("expected one notice: {:?}", d.actions)
        };
        assert_eq!(speech.kind, "ranged_sighting");
        assert!(matches!(&speech.scope, Scope::Visual(ids) if ids == &[id]));
        assert_eq!(speech.text, "スケルトンおるで。");
        assert!(!speech.interrupt && speech.cue_id.is_none() && speech.leaf.is_none());
    };
    let mut engine = Engine::default();
    let a = changed(&event(json!([visual("skeleton", "a", 13.0)]), json!([])), |v| {
        v["combat"] = json!({"player_engaged":true, "opponents":[{"entity_id":"a","evidence":"targeting_player"}]});
    });
    notice(tick(&mut engine, &a, 0), "a");
    let turned = changed(&a, |v| {
        v["visual_threats"][0]["direction"]["horizontal"] = "back".into()
    });
    let partial = changed(&event(json!([]), json!([{"label":"skeleton","source_id":"a"}])), |v| {
        v["event"]["name"] = json!("hostile_audio_detected");
        v["event"]["source_kind"] = json!("auditory");
    });
    assert!(
        engine
            .observe(
                &partial,
                500,
                false,
                false,
                &Settings::default(),
                &threats::Settings::default()
            )
            .actions
            .is_empty()
    );
    assert!(tick(&mut engine, &turned, 180_000).actions.is_empty());
    let two = changed(&turned, |v| {
        v["visual_threats"]
            .as_array_mut()
            .unwrap()
            .push(visual("skeleton", "b", 14.0));
        v["combat"]["opponents"].as_array_mut().unwrap().push(json!({"entity_id":"b","evidence":"player_attack"}))
    });
    notice(tick(&mut engine, &two, 180_001), "b");
    assert!(tick(&mut engine, &two, 360_000).actions.is_empty());
    let only_b = changed(&two, |v| { v["visual_threats"].as_array_mut().unwrap().remove(0); });
    assert!(tick(&mut engine, &only_b, 361_000).actions.is_empty());
    assert!(tick(&mut engine, &two, 362_000).actions.is_empty());
    let ended = changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = json!("combat_ended");
        v["combat"] = json!({"player_engaged":false, "opponents":[]});
    });
    tick(&mut engine, &ended, 363_000);
    notice(tick(&mut engine, &a, 364_000), "a");
}

// 見込み: 部分通知でAが対象外と確定したら、至近のAを以前の視認や敵数からパニックの根拠に戻さない。
// 合格: 模擬エンジンでA・Bによるパニックが、Bだけの交戦通知で警戒へ戻る。続く音通知が相手欄を省略しても警戒のまま。
#[test]
fn released_opponent_is_not_carried_from_full_sight_into_later_partial_events() {
    let mut engine = Engine::default();
    let both = changed(&event(json!([visual("zombie", "a", 2.0), visual("skeleton", "b", 14.0)]), json!([])), |v| {
        v["combat"] = json!({"player_engaged":true,"hostiles_within_10":2,
            "opponents":[{"entity_id":"a","evidence":"targeting_player"},{"entity_id":"b","evidence":"player_attack"}]});
    });
    assert_eq!(tick(&mut engine, &both, 0).mode, Mode::Panic);
    let mut partial = serde_json::to_value(event(json!([]), json!([]))).unwrap();
    partial["event"]["name"] = json!("hostile_audio_detected");
    partial["event"]["source_kind"] = json!("auditory");
    partial["combat"]["opponents"] = json!([{"entity_id":"b","evidence":"player_attack"}]);
    for at in [50, 100] {
        let decision = engine.observe(&GameEvent::parse(partial.clone()).unwrap(), at, false, false,
            &Settings::default(), &threats::Settings::default());
        assert_eq!(decision.mode, Mode::Alert);
        partial["combat"].as_object_mut().unwrap().remove("opponents");
    }
}

// 見込み: 導火が先なら遠隔敵の初回を消費しない。交戦中も同じ遠隔敵だけを時間で読み直さない。
// 合格: 模擬戦闘で導火→ガストの一言→発話なし。IDのない視認・音だけでは初回通知を作らない。
#[test]
fn ranged_sighting_yields_to_fuse_and_requires_a_visual_id() {
    let mut engine = Engine::default();
    let ghast = engaged(event(json!([visual("ghast", "g", 12.0)]), json!([])));
    let fused = changed(&ghast, |v| {
        let mut creeper = visual("creeper", "c", 4.0);
        creeper["fuse_active"] = true.into();
        v["visual_threats"].as_array_mut().unwrap().push(creeper);
    });
    assert_eq!(tick(&mut engine, &fused, 0).actions[0].kind, "creeper_fuse");
    let d = tick(&mut engine, &ghast, 1000);
    assert_eq!(d.actions.len(), 1);
    assert_eq!(d.actions[0].text, "ガストおるで。");
    assert!(!d.stop_audio);
    assert!(tick(&mut engine, &ghast, 180_000).actions.is_empty());
    let no_id = changed(&ghast, |v| {
        v["visual_threats"][0]["entity_id"] = Value::Null
    });
    assert!(tick(&mut engine, &no_id, 181_000).actions.is_empty());
    let heard = event(json!([]), json!([{"label":"skeleton","source_id":"s"}]));
    assert!(tick(&mut engine, &heard, 182_000).actions.is_empty());
}

#[test]
fn burning_start_waits_for_current_speech_and_survives_partial_events() {
    let mut engine = Engine::default();
    let s = Settings::default();
    let ws = threats::Settings::default();
    let seen = event(json!([visual("skeleton", "s", 13.54)]), json!([]));
    assert!(
        tick(&mut engine, &seen, 0)
            .actions
            .iter()
            .any(|a| a.kind == "ranged_sighting")
    );
    let burning = changed(&seen, |v| v["visual_threats"][0]["on_fire"] = true.into());
    // 実機で発見音声の終了133ms前に燃焼が始まった順序を再現する。
    assert!(
        engine
            .observe(&burning, 4954, true, true, &s, &ws)
            .actions
            .is_empty()
    );
    let partial = changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = "hostile_audio_detected".into();
        v["event"]["source_kind"] = "auditory".into();
    });
    assert!(
        engine
            .observe(&partial, 5000, false, true, &s, &ws)
            .actions
            .is_empty()
    );
    let ready = tick(&mut engine, &burning, 5087);
    let reaction = ready
        .actions
        .iter()
        .find(|a| a.kind == "newly_burning_visual")
        .expect("先の台詞が終わったら、まだ視認中で燃えている個体へ一度反応する");
    assert_eq!(
        reaction.leaf.as_ref().unwrap().details["hostile"],
        "スケルトン"
    );
    assert!(!ready.stop_audio);
    for at in [6000, 26000] {
        assert!(
            tick(&mut engine, &burning, at)
                .actions
                .iter()
                .all(|a| a.kind != "newly_burning_visual")
        );
    }
}

#[test]
fn pending_burning_is_discarded_when_extinguished_lost_or_dimension_changes() {
    let s = Settings::default();
    let ws = threats::Settings::default();
    for reason in ["extinguished", "lost", "dimension"] {
        let mut engine = Engine::default();
        let seen = event(json!([visual("skeleton", "s", 13.54)]), json!([]));
        tick(&mut engine, &seen, 0);
        let burning = changed(&seen, |v| v["visual_threats"][0]["on_fire"] = true.into());
        engine.observe(&burning, 4954, true, true, &s, &ws);
        let gone = match reason {
            "extinguished" => seen.clone(),
            "lost" => event(json!([]), json!([])),
            _ => changed(&event(json!([]), json!([])), |v| {
                v["player"]["dimension"] = "minecraft:the_nether".into();
            }),
        };
        engine.observe(&gone, 5000, true, true, &s, &ws);
        for at in [5087, 26000] {
            assert!(
                tick(&mut engine, &gone, at)
                    .actions
                    .iter()
                    .all(|a| a.kind != "newly_burning_visual"),
                "{reason}"
            );
        }
    }
}

#[test]
fn pending_burning_keeps_fuse_priority_and_existing_cooldown() {
    let mut engine = Engine::default();
    let fire = changed(
        &event(json!([visual("skeleton", "s", 13.54)]), json!([])),
        |v| {
            v["visual_threats"][0]["on_fire"] = true.into();
        },
    );
    let fuse = changed(&fire, |v| {
        let mut creeper = visual("creeper", "c", 5.0);
        creeper["fuse_active"] = true.into();
        v["visual_threats"].as_array_mut().unwrap().push(creeper);
    });
    assert_eq!(tick(&mut engine, &fuse, 0).actions[0].kind, "creeper_fuse");
    assert!(
        tick(&mut engine, &fire, 2000)
            .actions
            .iter()
            .any(|a| a.kind == "newly_burning_visual")
    );
    let extinguished = changed(&fire, |v| v["visual_threats"][0]["on_fire"] = false.into());
    tick(&mut engine, &extinguished, 3000);
    assert!(
        tick(&mut engine, &fire, 4000)
            .actions
            .iter()
            .all(|a| a.kind != "newly_burning_visual")
    );
    assert!(
        tick(&mut engine, &fire, 22000)
            .actions
            .iter()
            .any(|a| a.kind == "newly_burning_visual")
    );
    assert!(
        tick(&mut engine, &fire, 43000)
            .actions
            .iter()
            .all(|a| a.kind != "newly_burning_visual")
    );
}

#[test]
fn quiet_needs_three_commands_and_resets_only_after_aftermath() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([visual("zombie", "z", 4.0)]), json!([])));
    assert_eq!(tick(&mut engine, &e, 0).mode, Mode::Panic);
    let s = Settings::default();
    let ws = threats::Settings::default();
    for at in [2000, 4000] {
        assert_eq!(
            engine.input(&e, "静かにして", at, &s, &ws).unwrap().mode,
            Mode::Panic
        );
    }
    let d = engine.input(&e, "うるさい", 6000, &s, &ws).unwrap();
    assert_eq!(d.mode, Mode::SuppressedPanic);
    assert_eq!(d.actions[0].cue_id, Some("suppressed_gasp"));
    assert_eq!(tick(&mut engine, &e, 15000).mode, Mode::SuppressedPanic);
    let empty = event(json!([]), json!([]));
    assert_eq!(tick(&mut engine, &empty, 16000).mode, Mode::Normal);
    assert_eq!(engine.shut_up_count, 3);
    let end = changed(&empty, |v| v["event"]["name"] = "combat_ended".into());
    assert_eq!(tick(&mut engine, &end, 17000).mode, Mode::Aftermath);
    assert_eq!(tick(&mut engine, &empty, 24999).mode, Mode::Aftermath);
    assert_eq!(tick(&mut engine, &empty, 25000).mode, Mode::Normal);
    assert_eq!(engine.shut_up_count, 0);
}
// 本人が交戦した後も、通常の撃破・安堵は確認した事実だけを渡す。実音声を使わず、
// 同種の別個体、同一個体の再送、終了だけで届く死因を通し、発話・音声停止・材料の宛先を検査する。
#[test]
fn ordinary_defeat_and_relief_leave_conversation_open_and_report_each_new_outcome() {
    let mut engine = Engine::default();
    let fight = engaged(event(json!([visual("zombie", "first", 4.0)]), json!([])));
    assert_eq!(tick(&mut engine, &fight, 0).mode, Mode::Panic);

    let result = |id: &str, kind: &str, cause: &str| {
        json!({"entity_id":id,"type":kind,"outcome":cause,"evidence":"server_death_event"})
    };
    let defeat = |name: &str, outcomes: Value| changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = name.into();
        v["combat"] = json!({"hostile_outcomes":outcomes});
    });
    let first = result("first", "zombie", "player_kill");
    let immediate = tick(&mut engine, &defeat("hostile_defeated", json!([first, first])), 1000);
    assert!(immediate.actions.is_empty() && !immediate.stop_audio && immediate.chat_allowed);
    assert_eq!(engine.take_defeat_changes(), ["プレイヤーがゾンビを倒した"]);
    let end = defeat("combat_ended", json!([first, result("second", "zombie", "other_death"),
        result("third", "skeleton", "explosion_death")]));
    let relief = tick(&mut engine, &end, 2500);
    assert_eq!(relief.mode, Mode::Aftermath);
    assert!(relief.actions.is_empty() && !relief.stop_audio && relief.chat_allowed);
    assert_eq!(engine.take_defeat_changes(), ["ゾンビが倒れた", "スケルトンが爆発で倒れた"]);
    assert!(engine.take_defeat_changes().is_empty());
    assert!(tick(&mut engine, &end, 3000).actions.is_empty());
    assert!(engine.take_defeat_changes().is_empty());
}

#[test]
fn sound_partial_does_not_clear_visual_or_rearm_fuse() {
    let mut engine = Engine::default();
    let s = Settings::default();
    let ws = threats::Settings::default();
    let e = changed(
        &event(json!([visual("creeper", "c", 5.0)]), json!([])),
        |v| v["visual_threats"][0]["fuse_active"] = true.into(),
    );
    assert_eq!(tick(&mut engine, &e, 0).actions[0].kind, "creeper_fuse");
    let partial = changed(&event(json!([]), json!([sound("heard")])), |v| {
        v["event"]["name"] = "hostile_audio_detected".into();
        v["event"]["source_kind"] = "auditory".into();
    });
    let d = engine.observe(&partial, 2000, false, true, &s, &ws);
    assert_eq!(d.mode, Mode::Panic);
    assert!(!d.chat_allowed);
    assert!(
        tick(&mut engine, &e, 3000)
            .actions
            .iter()
            .all(|a| a.kind != "creeper_fuse")
    );
}
#[test]
fn low_health_rearms_above_five_or_missing_health() {
    let mut engine = Engine::default();
    let e = changed(
        &event(json!([visual("zombie", "z", 8.0)]), json!([])),
        |v| v["player"]["health"] = 5.into(),
    );
    assert_eq!(tick(&mut engine, &e, 0).actions[0].kind, "low_health");
    assert!(
        tick(&mut engine, &e, 2000)
            .actions
            .iter()
            .all(|a| a.kind != "low_health")
    );
    let unknown = changed(&e, |v| v["player"]["health"] = Value::Null);
    tick(&mut engine, &unknown, 3000);
    assert_eq!(tick(&mut engine, &e, 4000).actions[0].kind, "low_health");
}
#[test]
fn auditory_first_fourth_tenth_and_sixty_second_reset() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([]), json!([sound("heard")])));
    for n in 1..=11 {
        let d = tick(&mut engine, &e, n * 2000);
        assert_eq!(
            d.actions.iter().any(|a| a.kind == "auditory_hostile"),
            matches!(n, 1 | 4 | 10),
            "{n}"
        );
        if n == 10 {
            assert!(d.actions[0].text.contains("こっち来んよな"));
        }
    }
    let d = tick(&mut engine, &e, 82000);
    assert!(d.actions[0].text.contains("右でゾンビの声"));
}
#[test]
fn auditory_busy_defers_latest_milestone_without_marking_visual_seen() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([]), json!([sound("heard")])));
    assert!(
        engine
            .observe(
                &e,
                0,
                true,
                true,
                &Settings::default(),
                &threats::Settings::default()
            )
            .actions
            .is_empty()
    );
    assert!(
        tick(&mut engine, &e, 1000).actions[0]
            .text
            .contains("ゾンビの声")
    );
}
#[test]
fn auditory_chasing_uses_horizontal_displacement_and_name_permission() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([]), json!([sound("heard")])));
    for n in 1..10 {
        tick(&mut engine, &e, n * 2000);
    }
    let moved = changed(&e, |v| {
        v["player"]["position"]["x"] = 4.6.into();
        v["auditory_threats"][0]["spoken_name_allowed"] = false.into();
    });
    let d = tick(&mut engine, &moved, 20000);
    assert_eq!(d.actions[0].text, "やっこさん、まだ追ってきよるよ！");
}
#[test]
fn fused_creeper_beats_suppressed_breath() {
    let mut engine = Engine::default();
    let e = event(json!([visual("creeper", "c", 5.0)]), json!([]));
    tick(&mut engine, &e, 0);
    for at in [2000, 4000, 6000] {
        engine.input(
            &e,
            "黙れ",
            at,
            &Settings::default(),
            &threats::Settings::default(),
        );
    }
    let fuse = changed(&e, |v| v["visual_threats"][0]["fuse_active"] = true.into());
    let d = tick(&mut engine, &fuse, 8000);
    assert_eq!(d.actions[0].kind, "creeper_fuse");
    assert!(d.actions[0].interrupt);
}
#[test]
fn skeleton_scream_does_not_consume_low_health_warning() {
    let mut engine = Engine::default();
    let e = changed(
        &event(json!([visual("skeleton", "s", 8.0)]), json!([])),
        |v| {
            v["combat"] = json!({"recent_damage_ms":0});
            v["player"]["health"] = 5.into();
        },
    );
    assert_eq!(
        tick(&mut engine, &e, 0).actions[0].kind,
        "skeleton_damage_ambush"
    );
    let later = changed(&e, |v| v["combat"]["recent_damage_ms"] = 1500.into());
    assert!(
        tick(&mut engine, &later, 1500)
            .actions
            .iter()
            .any(|a| a.kind == "low_health")
    );
}
#[test]
fn stalled_tracks_individuals_and_partial_does_not_reset_it() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([visual("zombie", "z", 8.0)]), json!([])));
    tick(&mut engine, &e, 0);
    let partial = changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = "ambient_mob_detected".into()
    });
    engine.observe(
        &partial,
        30000,
        false,
        false,
        &Settings::default(),
        &threats::Settings::default(),
    );
    assert_eq!(
        tick(&mut engine, &e, 60000).actions[0].kind,
        "stalled_visual"
    );
    let new = changed(&e, |v| v["visual_threats"][0]["entity_id"] = "z2".into());
    assert!(
        tick(&mut engine, &new, 62000)
            .actions
            .iter()
            .all(|a| a.kind != "stalled_visual")
    );
}
#[test]
fn dark_alert_respects_light_and_submersion_without_creating_combat() {
    let mut engine = Engine::default();
    let e = changed(&event(json!([]), json!([])), |v| {
        v["world"]["danger_darkness_score"] = 0.9.into();
        v["world"]["local_light"] = 4.into();
    });
    assert_eq!(tick(&mut engine, &e, 0).mode, Mode::Normal);
    let dark = changed(&e, |v| v["world"]["local_light"] = 3.into());
    assert_eq!(tick(&mut engine, &dark, 1000).mode, Mode::Alert);
    let water = changed(&dark, |v| v["world"]["is_submerged"] = true.into());
    assert_eq!(tick(&mut engine, &water, 2000).mode, Mode::Normal);
}
#[test]
fn engaged_ordinary_hostiles_and_water_use_regular_fallback() {
    for kind in [
        "witch",
        "pillager",
        "endermite",
        "silverfish",
        "slime",
        "magma_cube",
        "drowned",
        "enderman",
    ] {
        let mut engine = Engine::default();
        let e = engaged(event(json!([visual(kind, "enemy", 8.0)]), json!([])));
        assert!(
            tick(&mut engine, &e, 0)
                .actions
                .iter()
                .any(|a| a.text.contains(&dogido_rust::combat::model::label(kind))),
            "{kind}"
        );
    }
}

#[test]
fn busy_leaf_is_preempted_by_new_rear_enemy_only_once() {
    let mut engine = Engine::default();
    tick(&mut engine, &event(json!([]), json!([])), 0);
    let rear = changed(
        &event(json!([visual("zombie", "z", 2.0)]), json!([])),
        |v| {
            v["event"]["name"] = "threat_approaching".into();
            v["event"]["source_kind"] = "visual".into();
            v["visual_threats"][0]["direction"]["horizontal"] = "back".into();
            v["visual_threats"][0]["approaching"] = true.into();
        },
    );
    let d = engine.observe(
        &rear,
        2000,
        true,
        true,
        &Settings::default(),
        &threats::Settings::default(),
    );
    assert!(d.stop_audio);
    assert!(d.actions.iter().any(|a| a.kind.starts_with("ushiro_")));
    assert!(
        engine
            .observe(
                &rear,
                4000,
                true,
                true,
                &Settings::default(),
                &threats::Settings::default()
            )
            .actions
            .is_empty()
    );
}

// 模擬観測。背後接近が先なら名前／志村の警告が出て、同じ個体の点火がその最中でも割り込む。
// 合格: 「うしろ」→導火の順で、導火後の観測には「うしろ」の本文も悲鳴もない。
#[test]
fn rear_approach_before_fuse_keeps_both_warnings_in_order() {
    let mut engine = Engine::default();
    let frame = |fuse| event(json!([rear_creeper("creeper", "a", fuse)]), json!([]));
    let rear = tick(&mut engine, &frame(false), 0);
    assert!(rear.stop_audio && rear.actions.iter().any(|a| a.text.ends_with("うしろ！うしろ〜！")));
    let fuse = engine.observe(&frame(true), 50, true, true, &Settings::default(), &threats::Settings::default());
    assert!(fuse.stop_audio && fuse.actions.len() == 1 && fuse.actions[0].kind == "creeper_fuse");
    assert_no_rear(&tick(&mut engine, &frame(true), 100));
}

fn assert_no_rear(d: &dogido_rust::combat::core::Decision) {
    assert!(d.actions.iter().all(|a| !a.kind.starts_with("ushiro_") && a.cue_id != Some("ushiro_scream")), "{:?}", d.actions);
}

// 模擬戦闘。導火済みの個体への「うしろ」は、接近が続いても、視界を出て戻っても重ねない。
// 合格: 同時なら導火だけ。再点火の警告と、別個体への「うしろ」はともに残る。実音声は対象外。
#[test]
fn fuse_suppresses_only_its_own_creepers_later_rear_warning() {
    use dogido_rust::combat::model::Scope;
    for kind in ["creeper", "charged_creeper"] {
        let mut engine = Engine::default();
        let lit = rear_creeper(kind, "a", true);
        let off = rear_creeper(kind, "a", false);
        let mut at = 0;
        let mut next = |mobs| {
            at += 50;
            tick(&mut engine, &event(mobs, json!([])), at)
        };
        let first = next(json!([lit]));
        assert!(first.stop_audio && first.actions.len() == 1 && first.actions[0].kind == "creeper_fuse");
        assert_no_rear(&next(json!([lit])));
        next(json!([]));
        assert_no_rear(&next(json!([off])));
        let relit = next(json!([lit]));
        assert_eq!(relit.actions[0].kind, "creeper_fuse");
        let other = next(json!([lit, rear_creeper(kind, "b", false)]));
        let rear = other.actions.iter().filter(|a| a.kind.starts_with("ushiro_")).collect::<Vec<_>>();
        assert_eq!(rear.len(), 1);
        assert!(matches!(&rear[0].scope, Scope::Visual(ids) if ids == &["b"]));
    }
}
#[test]
fn stale_visuals_and_transient_sonic_observation_are_not_refreshed_by_partial() {
    let mut engine = Engine::default();
    let e = changed(
        &event(json!([visual("warden", "w", 5.0)]), json!([])),
        |v| {
            v["world"]["ominous_sound_kind"] = "warden_sonic_boom".into();
            v["world"]["ominous_sound_recent_ms"] = 0.into();
        },
    );
    let d = tick(&mut engine, &e, 0);
    assert_eq!(d.actions[0].kind, "warden_sonic_boom");
    assert!(d.actions.iter().any(|a| a.kind == "boss_visual"));
    let partial = changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = "ambient_mob_detected".into()
    });
    let d = engine.observe(
        &partial,
        5000,
        false,
        false,
        &Settings::default(),
        &threats::Settings::default(),
    );
    assert!(d.actions.iter().all(|a| a.kind != "warden_sonic_boom"));
    let d = engine.observe(
        &partial,
        10001,
        false,
        false,
        &Settings::default(),
        &threats::Settings::default(),
    );
    assert_eq!(d.mode, Mode::Normal);
    assert!(!d.chat_allowed);
}
#[test]
fn engaged_rain_preserves_regular_warning_and_water_skeleton_gasps() {
    let mut engine = Engine::default();
    let rain = changed(
        &event(json!([visual("zombie", "z", 8.0)]), json!([])),
        |v| {
            v["world"]["time_phase"] = "day".into();
            v["world"]["weather"] = "rain".into();
        },
    );
    let rain = engaged(rain);
    assert!(
        tick(&mut engine, &rain, 0)
            .actions
            .iter()
            .any(|a| a.kind == "daylight_rain")
    );
    assert!(
        tick(&mut engine, &rain, 2000)
            .actions
            .iter()
            .any(|a| a.kind == "visual_hostile")
    );
    let mut engine = Engine::default();
    let water = changed(
        &event(json!([visual("skeleton", "s", 8.0)]), json!([])),
        |v| {
            v["world"]["time_phase"] = "day".into();
            v["visual_threats"][0]["in_water"] = true.into();
        },
    );
    let d = tick(&mut engine, &water, 0);
    assert_eq!(d.actions[0].cue_id, Some("spot_hostile_gasp"));
    assert_eq!(d.actions[1].kind, "daylight_water");
}

#[test]
fn darkness_keeps_chat_allowed_on_complete_and_passive_partial_observations() {
    let mut engine = Engine::default();
    let settings = Settings::default();
    let warnings = threats::Settings::default();
    let dark = changed(&event(json!([]), json!([])), |v| {
        v["world"]["local_light"] = json!(3);
        v["world"]["danger_darkness_score"] = json!(0.97);
        v["combat"] = json!({"combat_active_hint":false,"recent_damage_ms":356650});
    });
    for (at, complete) in [(0, true), (1000, false), (2000, false), (3000, true)] {
        let observed = if complete {
            dark.clone()
        } else {
            changed(&dark, |v| {
                v["event"]["name"] = json!("ambient_mob_detected");
                v["event"]["source_kind"] = json!("visual");
                v["passive_mobs"] = json!([{"type":"dogido","distance":2.0}]);
            })
        };
        let decision = engine.observe(&observed, at, complete, true, &settings, &warnings);
        assert_eq!(decision.mode, Mode::Alert);
        assert!(decision.chat_allowed, "complete={complete}, at={at}");
        assert!(!decision.stop_audio);
        assert!(decision.actions.is_empty());
    }
    for danger in ["visual", "auditory", "damage", "hint"] {
        let mut engine = Engine::default();
        engine.observe(&dark, 0, true, true, &settings, &warnings);
        let threat = changed(&dark, |v| match danger {
            "visual" => v["visual_threats"] = json!([visual("zombie", "enemy", 12.0)]),
            "auditory" => v["auditory_threats"] = json!([sound("enemy")]),
            "damage" => v["combat"]["recent_damage_ms"] = json!(0),
            _ => v["combat"]["combat_active_hint"] = json!(true),
        });
        assert_eq!(
            engine
                .observe(&threat, 1000, false, true, &settings, &warnings)
                .chat_allowed,
            danger != "damage",
            "{danger}"
        );
    }
}

// 見込み: 普通の敵を見聞きしただけなら、個体数でpanicになっても会話が続く。
// 合格: 実戦闘エンジンへ完全観測・音の部分通知・離脱を渡し、警告発話・音声停止・会話保留が全てない。
#[test]
fn ordinary_sight_count_and_sound_stay_observations_until_engagement() {
    let mut engine = Engine::default();
    let one = changed(
        &event(json!([visual("zombie", "z1", 2.)]), json!([])),
        |v| {
            v["combat"] = json!({"combat_active_hint":true, "player_engaged":false});
        },
    );
    let two = changed(&one, |v| {
        v["visual_threats"]
            .as_array_mut()
            .unwrap()
            .push(visual("zombie", "z2", 6.))
    });
    let heard = changed(&one, |v| {
        v["event"]["name"] = json!("hostile_audio_detected");
        v["visual_threats"] = json!([]);
        v["auditory_threats"] = json!([sound("unseen")]);
    });
    let clear = changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = json!("combat_ended")
    });
    let rainy = changed(&one, |v| {
        v["world"]["time_phase"] = json!("day");
        v["world"]["weather"] = json!("rain");
    });
    let behind = changed(&one, |v| {
        v["visual_threats"][0]["direction"]["horizontal"] = json!("back")
    });
    for (i, (frame, full)) in [
        (&one, true),
        (&behind, true),
        (&rainy, true),
        (&two, true),
        (&heard, false),
        (&clear, true),
        (&clear, true),
    ]
    .into_iter()
    .enumerate()
    {
        let d = engine.observe(
            frame,
            i as u64 * 100,
            full,
            false,
            &Settings::default(),
            &threats::Settings::default(),
        );
        assert!(
            d.chat_allowed && !d.stop_audio && d.actions.is_empty(),
            "{i}: {d:?}"
        );
    }
}
