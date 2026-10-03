use super::*;
use crate::{combat::model::LeafRequest, events::GameEvent};
use serde_json::json;
use std::time::{Duration, Instant};

fn sound(kind: &str, age: i64) -> GameEvent {
    GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":1,
        "observed_at":chrono::Utc::now(),"event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld"},"world":{"biome":"deep_dark","ominous_sound_kind":kind,"ominous_sound_recent_ms":age}})).unwrap()
}
fn reaction(kind: &str) -> Speech {
    let mut speech = Speech::new("deep_dark_ominous_sound", "聞こえたな。");
    speech.leaf = Some(LeafRequest {
        kind: "deep_dark_ominous_sound".into(),
        details: json!({"ominous_kind":kind}),
        temperature: 0.6,
    });
    speech
}
fn fixture() -> Arc<Dialogue> {
    let dialogue = Dialogue::new(crate::dialogue::DialogueConfig::default()).unwrap();
    dialogue.register("s", "試験", false);
    dialogue
}

#[test]
fn stronger_sounds_cancel_lower_generation_or_protected_playback() {
    for (lower, upper) in [
        ("sculk_sensor", "sculk_shrieker"),
        ("sculk_shrieker", "warden_heartbeat"),
        ("warden_heartbeat", "warden_presence"),
    ] {
        for started in [false, true] {
            let dialogue = fixture();
            let mut data = dialogue.data.lock().unwrap();
            let (turn, rx) = Dialogue::queue_actions(&mut data, "s", &[reaction(lower)], None);
            let active = data
                .sessions
                .get_mut("s")
                .unwrap()
                .warning
                .as_mut()
                .unwrap();
            active.started = started;
            active.protected_until = Some(Instant::now() + Duration::from_secs(60));
            dialogue.preempt_ominous_reaction(&mut data, "s", &sound(upper, 0));
            assert!(*rx.borrow(), "{lower} -> {upper}, started={started}");
            assert!(data.sessions["s"].warning.is_none());
            let row = data.rows.iter().find(|r| r["turn_id"] == turn).unwrap();
            assert_eq!(row["cancel_reason"], "higher_priority_ominous_sound");
        }
    }
}

#[test]
fn stronger_sound_drops_a_queued_lower_reaction_but_preserves_unrelated_pending_question() {
    let dialogue = fixture();
    let mut data = dialogue.data.lock().unwrap();
    data.sessions.get_mut("s").unwrap().pending_warning = Some(vec![reaction("sculk_sensor")]);
    dialogue.preempt_ominous_reaction(&mut data, "s", &sound("sculk_shrieker", 0));
    assert!(data.sessions["s"].pending_warning.is_none());
    let (_, rx) = Dialogue::queue_actions(&mut data, "s", &[reaction("sculk_sensor")], None);
    let s = data.sessions.get_mut("s").unwrap();
    s.pending_warning = Some(vec![Speech::new("hostile_direction", "右やで。")]);
    s.pending_input = Some("敵はどこ？".into());
    dialogue.preempt_ominous_reaction(&mut data, "s", &sound("sculk_shrieker", 0));
    assert!(*rx.borrow());
    assert_eq!(
        data.sessions["s"].pending_warning.as_ref().unwrap()[0].kind,
        "hostile_direction"
    );
    assert_eq!(
        data.sessions["s"].pending_input.as_deref(),
        Some("敵はどこ？")
    );
}

#[test]
fn lower_equal_unknown_and_stale_sounds_do_not_interrupt() {
    for (kind, age) in [
        ("sculk_sensor", 0),
        ("sculk_shrieker", 0),
        ("warden_heartbeat", 0),
        ("warden_presence", 0),
        ("unknown", 0),
        ("warden_sonic_boom", 2501),
        ("warden_presence", 30001),
        ("warden_presence", -1),
    ] {
        let dialogue = fixture();
        let mut data = dialogue.data.lock().unwrap();
        let (_, rx) = Dialogue::queue_actions(&mut data, "s", &[reaction("warden_presence")], None);
        dialogue.preempt_ominous_reaction(&mut data, "s", &sound(kind, age));
        assert!(!*rx.borrow(), "{kind}, age={age}");
        assert!(data.sessions["s"].warning.is_some());
    }
}

#[test]
fn sonic_and_combat_warnings_cannot_be_replaced_by_ordinary_warden_sounds() {
    for actions in [
        vec![Speech::new("warden_sonic_boom", "ぎゃあ！")],
        vec![
            Speech::new("warden_sonic_boom", "ぎゃあ！"),
            reaction("sculk_sensor"),
        ],
        vec![Speech::new("hostile_spotted", "右や！")],
    ] {
        let dialogue = fixture();
        let mut data = dialogue.data.lock().unwrap();
        let (_, rx) = Dialogue::queue_actions(&mut data, "s", &actions, None);
        dialogue.preempt_ominous_reaction(&mut data, "s", &sound("warden_presence", 0));
        assert!(!*rx.borrow());
        assert!(data.sessions["s"].warning.is_some());
    }
    let dialogue = fixture();
    let mut data = dialogue.data.lock().unwrap();
    let (_, rx) = Dialogue::queue_actions(&mut data, "s", &[reaction("warden_presence")], None);
    dialogue.preempt_ominous_reaction(&mut data, "s", &sound("warden_sonic_boom", 0));
    assert!(*rx.borrow());
}

#[test]
fn cancellation_frees_busy_and_selects_shrieker_in_the_same_observation() {
    let dialogue = fixture();
    let mut data = dialogue.data.lock().unwrap();
    let sensor = sound("sculk_sensor", 1001);
    let first = data.sessions.get_mut("s").unwrap().combat.observe(
        &sensor,
        2000,
        true,
        false,
        &dialogue.config.combat,
        &dialogue.config.warnings,
    );
    assert_eq!(first.actions.len(), 1);
    assert_eq!(
        first.actions[0].leaf.as_ref().unwrap().details["ominous_kind"],
        "sculk_sensor"
    );
    let (_, rx) = Dialogue::queue_actions(&mut data, "s", &first.actions, None);
    let shrieker = sound("sculk_shrieker", 0);
    dialogue.preempt_ominous_reaction(&mut data, "s", &shrieker);
    assert!(*rx.borrow());
    let s = data.sessions.get_mut("s").unwrap();
    let busy = s.warning.is_some() || s.pending_warning.is_some();
    assert!(!busy);
    let next = s.combat.observe(
        &shrieker,
        2001,
        true,
        busy,
        &dialogue.config.combat,
        &dialogue.config.warnings,
    );
    assert_eq!(next.actions.len(), 1);
    assert_eq!(
        next.actions[0].leaf.as_ref().unwrap().details["ominous_kind"],
        "sculk_shrieker"
    );
}
