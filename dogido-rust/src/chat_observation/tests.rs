//! 見込み: 保持の時刻・件数を保ったまま共通聴覚の記録を追加できる。合格: 旧fixtureの全既存項目は不変、新フィールドもJSON往復で不変。確認範囲: 模擬観測と保持。新しい音の意味・由来はacceptance/hearing.rsで照合。
use super::*;
use serde_json::{Value, json};
use std::collections::HashMap;

#[derive(Deserialize)]
struct FixtureLabels {
    mobs: HashMap<String, String>,
    mob_fallback: HashMap<String, String>,
    hostiles: HashMap<String, String>,
    blocks: HashMap<String, String>,
}
impl Labels for FixtureLabels {
    fn mob_label(&self, id: &str) -> Option<&str> {
        let id = strip(id).to_lowercase();
        self.mobs
            .get(id.strip_prefix("minecraft:").unwrap_or(&id))
            .map(String::as_str)
    }
    fn mob_fallback_label(&self, id: &str) -> Option<&str> {
        self.mob_fallback.get(id).map(String::as_str)
    }
    fn hostile_label(&self, id: &str) -> String {
        self.hostiles
            .get(id)
            .map_or_else(|| id.to_owned(), Clone::clone)
    }
    fn block_label(&self, id: &str) -> Option<&str> {
        self.blocks
            .get(&strip(id.split(':').next_back().unwrap()).to_lowercase())
            .map(String::as_str)
    }
}
fn labels() -> FixtureLabels {
    serde_json::from_str(include_str!("../../fixtures/chat-observation-labels.json")).unwrap()
}
#[derive(Deserialize)]
struct Trace {
    name: String,
    settings: Settings,
    steps: Vec<Step>,
}
#[derive(Deserialize)]
struct Step {
    event: GameEvent,
    accepted: bool,
    outcome_updates: NameOutcomeUpdate,
    expected: Value,
}
// 見込み: 観測情報の追加で既存保持の振る舞いを変えない。合格: 全旧fixture項目を厳密比較し、observationを含むsnapshotはJSON往復で一致。確認範囲: 固定した旧形式と保持。新観測の内容の正解は受入試験で別検査。
#[test]
fn persistent_observation_traces_match_fixture() {
    let labels = labels();
    let mut traces = 0;
    let mut steps = 0;
    for line in include_str!("../../fixtures/chat-observation.jsonl").lines() {
        let trace: Trace = serde_json::from_str(line).unwrap();
        let mut memory = ChatObservationMemory::new(trace.settings);
        for (i, row) in trace.steps.iter().enumerate() {
            if row.accepted {
                memory
                    .observe(&row.event, &row.outcome_updates, &labels);
            }
            let snapshot = memory.snapshot(&row.event, &labels);
            let mut actual = serde_json::to_value(&snapshot).unwrap();
            let restored: Snapshot = serde_json::from_value(actual.clone()).unwrap();
            assert_eq!(restored, snapshot, "{} step {i}: 新観測情報もJSON往復で保持", trace.name);
            // 移植時の正本fixtureは再生成しない。従来の全項目を厳密比較し、
            // 追加observationの内容はacceptance/hearing.rsで入力から独立に照合する。
            for memo in actual["recent"]["hearing_memos"].as_array_mut().unwrap() {
                memo.as_object_mut().unwrap().remove("observation");
            }
            assert_eq!(actual, row.expected, "{} step {i}", trace.name);
            assert!(memory.visual.len() <= 12);
            assert!(memory.hearing.len() <= 12);
            assert!(memory.home.len() <= 5);
            steps += 1;
        }
        traces += 1;
    }
    assert!(
        traces > 100 && steps > 500,
        "fixture unexpectedly small: {traces}/{steps}"
    );
}
fn frame(at: &str, fields: Value) -> GameEvent {
    let mut value = json!({"schema_version":"2026-05-24","adapter":"pure-test","observed_at":at,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld"},"world":{},"combat":{"hostile_outcomes":[]}});
    for (key, v) in fields.as_object().unwrap() {
        value[key] = v.clone();
    }
    GameEvent::parse(value).unwrap()
}
#[test]
fn snapshot_is_read_only_and_current_never_contains_recent_or_death_names() {
    let labels = labels();
    let mut memory = ChatObservationMemory::default();
    let start = frame(
        "2026-09-29T00:00:00Z",
        json!({"visual_threats":[{"type":"zombie","entity_id":"z"}],"passive_mobs":[{"type":"cow"}]}),
    );
    memory
        .observe(
            &start,
            &NameOutcomeUpdate {
                confirmed_types: vec!["witch".into()],
            },
            &labels,
        );
    let early = frame("2026-09-29T00:00:01Z", json!({}));
    let snapshot = memory.snapshot(&early, &labels);
    assert!(snapshot.current.visual_types.is_empty() && snapshot.current.passive_types.is_empty());
    assert_eq!(snapshot.name_context.types, vec!["zombie", "cow", "witch"]);
    let later = frame("2026-09-29T00:00:13Z", json!({}));
    assert!(
        memory
            .snapshot(&later, &labels)
            .recent
            .visual_memos
            .is_empty()
    );
    assert_eq!(memory.snapshot(&early, &labels), snapshot); // a read did not prune or renew
}
#[test]
fn explicit_empty_outcome_update_does_not_manufacture_a_death() {
    let labels = labels();
    let mut memory = ChatObservationMemory::default();
    let before = frame(
        "2026-09-29T00:00:00Z",
        json!({"visual_threats":[{"type":"zombie","entity_id":"z"}]}),
    );
    memory
        .observe(&before, &NameOutcomeUpdate::default(), &labels);
    let absent = frame("2026-09-29T00:00:09Z", json!({}));
    memory
        .observe(&absent, &NameOutcomeUpdate::default(), &labels);
    let later = frame("2026-09-29T00:00:11Z", json!({}));
    assert!(
        memory
            .snapshot(&later, &labels)
            .name_context
            .types
            .is_empty()
    );
    assert_eq!(
        memory
            .snapshot(&later, &labels)
            .recent
            .visual_types,
        vec!["zombie"]
    );
}

#[test]
fn repeated_sound_snapshots_preserve_actual_hearing_time_and_current_source_identity() {
    let labels = labels();
    let mut memory = ChatObservationMemory::default();
    let first = frame(
        "2026-09-30T01:00:00Z",
        json!({"ambient_sounds":[{
            "type":"enderman", "source_id":"heard-entity", "heard_ago_ms":0,
            "sound_event":"entity.enderman.ambient", "direction":{"horizontal":"front"}
        }]}),
    );
    memory
        .observe(&first, &NameOutcomeUpdate::default(), &labels);
    let heard = memory.hearing[0].heard_at_us;
    let repeated = frame(
        "2026-09-30T01:00:15Z",
        json!({"ambient_sounds":[{
            "type":"enderman", "source_id":"heard-entity", "heard_ago_ms":15000,
            "sound_event":"entity.enderman.ambient", "direction":{"horizontal":"back"}
        }]}),
    );
    memory
        .observe(&repeated, &NameOutcomeUpdate::default(), &labels);
    assert_eq!(memory.hearing.len(), 1);
    assert_eq!(memory.hearing[0].heard_at_us, heard);
    assert_eq!(memory.hearing[0].direction, "後ろ");
    let gone = frame("2026-09-30T01:00:21Z", json!({}));
    memory
        .observe(&gone, &NameOutcomeUpdate::default(), &labels);
    assert!(
        memory.hearing.is_empty(),
        "20 seconds from the actual sound, not its last resend"
    );
    let newer = frame(
        "2026-09-30T01:00:22Z",
        json!({"auditory_threats":[{
            "label":"zombie","source_id":"new-sound","heard_ago_ms":2000
        }]}),
    );
    memory
        .observe(&newer, &NameOutcomeUpdate::default(), &labels);
    assert_eq!(memory.hearing[0].heard_at_us, heard + 20_000_000);
    let mut invalid = json!(newer);
    invalid["auditory_threats"][0]["heard_ago_ms"] = json!(-1);
    assert!(GameEvent::parse(invalid).is_err());
}

#[test]
fn named_individual_memory_keeps_presence_rename_and_sound_age_separate() {
    let labels = labels();
    let mut memory = ChatObservationMemory::default();
    let named = frame(
        "2026-09-30T01:00:00Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"c","custom_name":"クロちゃん","tamed":true}}]}),
    );
    memory
        .observe(&named, &NameOutcomeUpdate::default(), &labels);
    let empty = frame("2026-09-30T01:00:15Z", json!({}));
    memory
        .observe(&empty, &NameOutcomeUpdate::default(), &labels);
    let snap = memory.snapshot(&empty, &labels);
    assert_eq!(snap.named_mobs[0].custom_name, "クロちゃん");
    assert!(!snap.named_mobs[0].current);
    assert!(snap.current.passive_types.is_empty());
    let renamed = frame(
        "2026-09-30T01:00:16Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"c","custom_name":"シロ","tamed":true}}]}),
    );
    memory
        .observe(&renamed, &NameOutcomeUpdate::default(), &labels);
    assert_eq!(
        memory.snapshot(&renamed, &labels).named_mobs.len(),
        1
    );
    assert_eq!(
        memory.snapshot(&renamed, &labels).named_mobs[0].custom_name,
        "シロ"
    );
    let unnamed = frame(
        "2026-09-30T01:00:17Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"c","tamed":true}}]}),
    );
    memory
        .observe(&unnamed, &NameOutcomeUpdate::default(), &labels);
    assert!(
        memory
            .snapshot(&unnamed, &labels)
            .named_mobs
            .is_empty()
    );
    let sound = frame(
        "2026-09-30T01:00:30Z",
        json!({"ambient_sounds":[{"type":"cat","source_id":"c","heard_ago_ms":10000,
        "identity":{"entity_id":"c","custom_name":"クロちゃん","tamed":true}}]}),
    );
    memory
        .observe(&sound, &NameOutcomeUpdate::default(), &labels);
    assert_eq!(
        memory.named_mobs[0].observed_at_us,
        memory.hearing[0].heard_at_us
    );
    let after_sound = frame("2026-09-30T01:00:41Z", json!({}));
    memory
        .observe(&after_sound, &NameOutcomeUpdate::default(), &labels);
    assert!(
        memory
            .snapshot(&after_sound, &labels)
            .hearing
            .candidates
            .is_empty()
    );
    let dimension = frame(
        "2026-09-30T01:00:42Z",
        json!({"player":{"dimension":"minecraft:the_nether"}}),
    );
    memory
        .observe(&dimension, &NameOutcomeUpdate::default(), &labels);
    assert!(
        memory
            .snapshot(&dimension, &labels)
            .named_mobs
            .is_empty()
    );
}

#[test]
fn retained_old_audio_never_reverts_a_newer_name_or_name_removal() {
    let labels = labels();
    let mut memory = ChatObservationMemory::default();
    let visual = frame(
        "2026-09-30T01:00:10Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"c","custom_name":"シロ","tamed":true}}]}),
    );
    memory
        .observe(&visual, &Default::default(), &labels);
    let old_audio = frame(
        "2026-09-30T01:00:15Z",
        json!({"ambient_sounds":[
        {"type":"cat","source_id":"c","heard_ago_ms":15000,"identity":{"entity_id":"c","custom_name":"クロ","tamed":true}}]}),
    );
    memory
        .observe(&old_audio, &Default::default(), &labels);
    let snap = memory.snapshot(&old_audio, &labels);
    assert_eq!(snap.named_mobs[0].custom_name, "シロ");
    assert!(!snap.named_mobs[0].current);
    let unnamed = frame(
        "2026-09-30T01:00:16Z",
        json!({"passive_mobs":[
        {"type":"cat","identity":{"entity_id":"c","tamed":true}}]}),
    );
    memory
        .observe(&unnamed, &Default::default(), &labels);
    let old_audio = frame(
        "2026-09-30T01:00:17Z",
        json!({"ambient_sounds":[
        {"type":"cat","source_id":"c","heard_ago_ms":17000,"identity":{"entity_id":"c","custom_name":"クロ","tamed":true}}]}),
    );
    memory
        .observe(&old_audio, &Default::default(), &labels);
    assert!(
        memory
            .snapshot(&old_audio, &labels)
            .named_mobs
            .is_empty()
    );
    let mut fresh = ChatObservationMemory::default();
    fresh
        .observe(&old_audio, &Default::default(), &labels);
    assert!(!fresh.snapshot(&old_audio, &labels).named_mobs[0].current);
}

#[test]
fn same_source_block_actions_survive_current_and_recent_hearing_without_claiming_an_actor() {
    let labels = labels();
    let mut memory = ChatObservationMemory::default();
    let sounds: Vec<_> = ["step", "break", "place"]
        .into_iter()
        .map(|action| {
            json!({
                "type":"block:stone", "source_id":"played:block:stone:1:2:3",
                "sound_event":format!("minecraft:block.stone.{action}"),
                "direction":{"horizontal":"front"}, "distance_band":"close"
            })
        })
        .collect();
    let event = frame("2026-10-18T01:00:00Z", json!({"ambient_sounds":sounds}));
    memory
        .observe(&event, &Default::default(), &labels);
    let snapshot = memory.snapshot(&event, &labels);
    assert_eq!(snapshot.recent.hearing_memos.len(), 3);
    let keys: std::collections::HashSet<_> = snapshot
        .recent
        .hearing_memos
        .iter()
        .map(|m| &m.dedupe_key)
        .collect();
    assert_eq!(keys.len(), 3);
    for (action, text) in [
        ("step", "石系の足音"),
        ("break", "石系ブロックの破壊音"),
        ("place", "石系ブロックの設置音"),
    ] {
        assert!(
            snapshot
                .recent
                .hearing_memos
                .iter()
                .any(|m| m.sound_event.as_deref()
                    == Some(format!("minecraft:block.stone.{action}").as_str()))
        );
        assert_eq!(snapshot.hearing.summary.matches(text).count(), 1);
    }
    assert_eq!(snapshot.hearing.summary.matches("発音者未確定").count(), 3);
    let later = frame("2026-10-18T01:00:10Z", json!({}));
    let recent = memory.snapshot(&later, &labels);
    assert_eq!(recent.hearing.summary.matches("ついさっき").count(), 3);
    assert_eq!(memory.snapshot(&event, &labels), snapshot);
    let expired = frame("2026-10-18T01:00:21Z", json!({}));
    assert!(
        memory
            .snapshot(&expired, &labels)
            .hearing
            .summary
            .is_empty()
    );
}

#[test]
fn actual_mob_steps_are_not_voices_and_block_noise_is_not_an_interaction() {
    let event = frame(
        "2026-10-18T01:00:00Z",
        json!({
            "auditory_threats":[{"label":"zombie","source_id":"z","sound_event":"entity.zombie.step"}],
            "ambient_sounds":[{"type":"wolf","source_id":"w","sound_event":"entity.wolf.step"},
                {"type":"block:campfire","sound_event":"block.campfire.crackle"}]
        }),
    );
    let snapshot = ChatObservationMemory::default()
        .snapshot(&event, &labels());
    assert!(snapshot.hearing.summary.contains("ゾンビの足音"));
    assert!(snapshot.hearing.summary.contains("オオカミの足音"));
    assert!(snapshot.hearing.summary.contains("はぜる音"));
    assert!(!snapshot.hearing.summary.contains("声"));
    assert!(!snapshot.hearing.summary.contains("設置音"));
    assert!(
        sound_description(Some("石"), "environment", Some("block.stone.hit"), false)
            .contains("打撃音")
    );
    assert!(
        sound_description(Some("石"), "environment", Some("block.stone.fall"), false)
            .contains("着地音")
    );
    assert_eq!(
        sound_description(
            Some("オオカミ"),
            "ambient",
            Some("entity.wolf.unknown"),
            false
        ),
        "オオカミの音"
    );
    assert_eq!(
        sound_description(None, "ambient", Some("unknown.future.step"), false),
        "音（種別未確定）"
    );
    assert_eq!(
        sound_description(Some("くろ"), "environment", Some("block.stone.step"), true),
        "くろの足音"
    );
}

#[test]
fn verified_owner_sound_names_emitter_separately_and_unresolved_sound_stays_unresolved() {
    let mut wire=crate::body::tests::fixture();let owner=wire["sample"]["context"]["owner_id"].as_str().unwrap().to_owned();
    wire["age_ms"]=json!(0);
    wire["sample"]["senses"]["ambient_sounds"]=json!([
        {"type":"block:stone","source_id":owner,"sound_event":"block.stone.step","heard_ago_ms":0,"direction":{"horizontal":"front"},"distance_band":"close"},
        {"type":"block:stone","source_id":"played:block:stone:0:0:0","sound_event":"block.stone.step","heard_ago_ms":0,"direction":{"horizontal":"front"},"distance_band":"close"}]);
    let observation=serde_json::from_value(wire).unwrap();let now=std::time::Instant::now();let mut body=crate::body::State::default();
    body.observe(Some(&observation),Some(&owner),Some("minecraft:overworld"),now,Some(0));
    let event=body.sensory_event(&frame("2026-10-22T01:00:00Z",json!({})),now, None);
    let mut memory=ChatObservationMemory::default();memory.observe(&event,&Default::default(),&labels());
    let snapshot=memory.snapshot(&event,&labels());
    assert!(snapshot.hearing.summary.contains("音：石系の足音／音の由来：プレイヤー"));
    assert!(snapshot.hearing.summary.contains("石系の足音（発音者未確定）"));
    assert_eq!(snapshot.recent.hearing_memos.iter().filter(|memo|memo.emitter.as_deref()==Some("プレイヤー")).count(),1);
    let later=memory.snapshot(&frame("2026-10-22T01:00:05Z",json!({})),&labels());
    assert!(later.hearing.summary.contains("音の由来：プレイヤー"));
}
