//! 見込み: 自発生成直前のコピーだけが継続表示になり、質問用の聴覚記録は保持される。
//! 合格: 受理済み模擬身体から通常・環境のモデル入力まで照合。モデル・音声・保存は無効、一時領域へ隔離。実機は対象外。
use super::*;
use serde_json::Value;

fn server() -> Arc<Dialogue> {
    let mut config = super::super::DialogueConfig { audio_enabled: false, llm_enabled: false, ..Default::default() };
    config.haiku.enabled = false;
    config.haiku.llm_enabled = false;
    config.haiku.memory_enabled = false;
    config.haiku.internal_capture_enabled = false;
    let root = std::env::temp_dir().join(format!("dogido-spontaneous-hearing-{}", uuid::Uuid::new_v4()));
    config.haiku.memory_dir = root.join("memory");
    config.haiku.capture_dir = Some(root.join("capture"));
    config.haiku.screenshot_dir = Some(root.join("images"));
    config.audio_dir = root.join("audio");
    let dialogue = Dialogue::new(config).unwrap();
    dialogue.register("hearing", "試験", true);
    dialogue
}

fn observe(s: &mut Session, tick: u64, sounds: Value) -> GameEvent {
    let mut raw = super::super::empty_event("試験");
    raw["player"]["dimension"] = json!("minecraft:overworld");
    raw["world"] = json!({"biome":"plains","time_phase":"day","sky_visible":true});
    raw["ambient_sounds"] = sounds;
    let event = super::super::body_runtime::install_test_senses(s, &GameEvent::parse(raw).unwrap(), tick);
    s.chat_observation.observe_body(&event, &Default::default(), &super::super::chat_context::CatalogLabels);
    s.conversation_observation.observe_origin(&event);
    s.conversation_observation.observe(crate::conversation_observation::project(&event, None, 32., 7., 5000).unwrap());
    event
}

fn sound() -> Value {
    json!({"type":"iron_golem","source_id":"44444444-4444-4444-8444-444444444444",
        "sound_event":"entity.iron_golem.step","direction":{"horizontal":"back"},
        "distance_band":"mid","certainty":"low","heard_ago_ms":1000})
}

fn action(s: &Session, event: &GameEvent, settings: &Settings) -> Speech {
    let mut action = Speech::new("reassurance_notice", "");
    action.delivery = Delivery::Ambient;
    crate::environment::reaction::attach(&mut action, event, settings);
    assert!(refresh_dialogue_context(s, std::slice::from_mut(&mut action)));
    action
}

fn submitted_hearing(action: &Speech) -> Value {
    let prepared = crate::environment::reaction::Prepared::new(
        &json!({"kind":"environment_reaction","model":"fixture","max_tokens":128,
            "fallback_text":"","details":action.leaf.as_ref().unwrap().details}), "fixture").unwrap();
    let input: Value = serde_json::from_str(&prepared.request.messages[1].content).unwrap();
    assert!(!prepared.request.messages[1].content.contains("44444444-4444-4444-8444-444444444444"));
    input["world_context"]["observations"]["hearing"].clone()
}

fn normal_hearing(s: &Session, event: &GameEvent) -> Value {
    let messages = crate::chat_prompt::messages(&json!({"user_text":"今の音は何？",
        "world_context":super::super::body_runtime::context_for(s, event),"dialogue_choice":true})).unwrap();
    let (_, text) = messages[1].content.split_once("【現在の環境観測と変化（会話履歴とは別）】\n").unwrap();
    let world: Value = serde_json::Deserializer::from_str(text).into_iter::<Value>().next().unwrap().unwrap();
    world["observations"]["hearing"].clone()
}

fn linked_sound(id: &str, kind: &str, event: &str, basis: &str) -> Value {
    let mut row = sound();
    row["source_id"] = json!(id);
    row["type"] = json!(kind);
    row["sound_event"] = json!(event);
    row["source_context"] = json!({"observed_source":{"entity_id":id,"type":kind,
        "basis":basis,"inferred":true,"retention_range":21.0}});
    row
}

// 見込み: adapterで対象へ紐付けた音は、個体IDを出さずに本人・モブの音として質問と自発入力へ届く。
// 合格: 両入力の各音に対応するemitterと観測根拠が付き、音種は不変。紐付け結果は模擬でJava採取・実モデルは別確認。
#[test]
fn linked_hearing_reaches_both_model_inputs_as_player_and_observed_mob() {
    let dialogue = server();
    let mut data = dialogue.data.lock().unwrap();
    let s = data.sessions.get_mut("hearing").unwrap();
    let initial = observe(s, 1, json!([]));
    let owner = initial.body_origin().unwrap().context.as_ref().unwrap().owner_id.clone();
    let event = observe(s, 2, json!([
        linked_sound(&owner, "player", "block.grass.step", "observed"),
        linked_sound("44444444-4444-4444-8444-444444444444", "iron_golem", "entity.iron_golem.step", "observed")
    ]));
    let mut pending = action(s, &event, &dialogue.config.combat);
    prepare_spontaneous_hearing(s, &mut pending, &dialogue.config.combat);
    for hearing in [normal_hearing(s, &event), submitted_hearing(&pending)] {
        let rows = hearing["sounds"].as_array().unwrap();
        assert_eq!(rows.len(), 2);
        for (event, emitter) in [("block.grass.step", "プレイヤー"), ("entity.iron_golem.step", "アイアンゴーレム")] {
            let row = rows.iter().find(|row| row["sound_event"] == event).unwrap();
            assert_eq!(row["emitter"], emitter);
            assert_eq!(row["emitter_attribution"], "inferred");
            assert_eq!(row["emitter_observation"], "観測した対象に紐付く音");
        }
        assert!(!hearing.to_string().contains(&owner));
    }
}

// 見込み: 以前に観測した対象の同種音が外周で続いても未特定へ戻さず、現在見えたとは記録しない。
// 合格: 自発は同UUIDの継続一件、質問・保持音にも対象名と以前の観測に基づく根拠・元時刻が残る。模擬入力のみ。
#[test]
fn linked_hearing_continuity_and_question_retention_keep_the_source_without_new_visual_evidence() {
    let dialogue = server();
    let mut data = dialogue.data.lock().unwrap();
    let s = data.sessions.get_mut("hearing").unwrap();
    let id = "44444444-4444-4444-8444-444444444444";
    let event = observe(s, 1, json!([linked_sound(id,"iron_golem","entity.iron_golem.step","observed")]));
    let mut first = action(s, &event, &dialogue.config.combat);
    prepare_spontaneous_hearing(s, &mut first, &dialogue.config.combat);
    let mut continued = linked_sound(id,"iron_golem","entity.iron_golem.step","continued");
    continued["distance_band"] = json!("far");
    let event = observe(s, 2, json!([continued]));
    let mut next = action(s, &event, &dialogue.config.combat);
    prepare_spontaneous_hearing(s, &mut next, &dialogue.config.combat);
    let hearing = submitted_hearing(&next);
    assert_eq!(hearing["sounds"].as_array().unwrap().len(), 1);
    assert_eq!(hearing["sounds"][0]["presentation"], "継続提示");
    assert_eq!(hearing["sounds"][0]["emitter"], "アイアンゴーレム");
    assert_eq!(hearing["sounds"][0]["emitter_observation"], "以前に観測した対象の同種の継続音");
    assert_eq!(hearing["sounds"][0]["distance_band"], "far");
    let event = observe(s, 3, json!([]));
    let retained = normal_hearing(s, &event);
    let row = &retained["sounds"][0];
    assert_eq!(row["emitter"], "アイアンゴーレム");
    assert_eq!(row["emitter_observation"], "以前に観測した対象の同種の継続音");
    assert_eq!(row["basis"], "retained_sound");
    assert!(row["heard_ago_ms"].as_i64().unwrap() >= 1000);
}

// 見込み: 同じ音の再提示だけが継続になり、質問側は音の年齢・由来を含む元の材料を引き続き参照できる。
// 合格: 初回→継続、更新予約のみでは初回、保持音はretained_sound。通常チャット入力と保存メモは前後で一致。模擬入力のみ。
#[test]
fn spontaneous_hearing_projection_preserves_question_records() {
    let dialogue = server();
    let mut data = dialogue.data.lock().unwrap();
    let s = data.sessions.get_mut("hearing").unwrap();
    let event = observe(s, 1, json!([sound(), sound()]));
    let before = super::super::body_runtime::context_for(s, &event);
    let snapshot = s.chat_observation.snapshot_body(&event, &super::super::chat_context::CatalogLabels);
    let mut pending = action(s, &event, &dialogue.config.combat);
    assert!(refresh_dialogue_context(s, std::slice::from_mut(&mut pending)));
    prepare_spontaneous_hearing(s, &mut pending, &dialogue.config.combat);
    let first = submitted_hearing(&pending);
    assert_eq!(first["sounds"].as_array().unwrap().len(), 1);
    assert_eq!(first["sounds"][0]["presentation"], "初回提示");
    let mut next = action(s, &event, &dialogue.config.combat);
    prepare_spontaneous_hearing(s, &mut next, &dialogue.config.combat);
    assert_eq!(submitted_hearing(&next)["sounds"][0]["presentation"], "継続提示");
    let after = super::super::body_runtime::context_for(s, &event);
    assert_eq!(before.observations["hearing"], after.observations["hearing"]);
    assert_eq!(snapshot, s.chat_observation.snapshot_body(&event, &super::super::chat_context::CatalogLabels));
    let normal = |world: crate::conversation_observation::Context| {
        crate::chat_prompt::messages(&json!({"user_text":"今の音は何？", "world_context":world, "dialogue_choice":true})).unwrap()
    };
    assert_eq!(normal(before)[1].content, normal(after)[1].content);
    assert!(!normal(super::super::body_runtime::context(s).unwrap())[1].content.contains("提示"));
    let retained = observe(s, 2, json!([]));
    let mut next = action(s, &retained, &dialogue.config.combat);
    prepare_spontaneous_hearing(s, &mut next, &dialogue.config.combat);
    assert_eq!(submitted_hearing(&next)["sounds"][0]["basis"], "retained_sound");
    assert_eq!(submitted_hearing(&next)["sounds"][0]["presentation"], "継続提示");
    assert_eq!(s.chat_observation.snapshot_body(&retained, &super::super::chat_context::CatalogLabels).recent.hearing_memos.len(), 1);
}

// 見込み: 緊急警告や川柳再開を通常の継続音へ変換せず、身体世代を越えて既出扱いしない。
// 合格: 対象外actionは完全一致、世代リセット後は初回提示。模型の配線試験で実警告・実移動は対象外。
#[test]
fn spontaneous_hearing_scope_and_body_reset_are_preserved() {
    let dialogue = server();
    let mut data = dialogue.data.lock().unwrap();
    let s = data.sessions.get_mut("hearing").unwrap();
    let event = observe(s, 1, json!([sound()]));
    for (kind, delivery) in [("reassurance_notice", Delivery::UrgentEnvironment), ("workshop_resume", Delivery::Ambient)] {
        let mut value = action(s, &event, &dialogue.config.combat);
        value.kind = kind;
        value.delivery = delivery;
        let before = json!(value);
        prepare_spontaneous_hearing(s, &mut value, &dialogue.config.combat);
        assert_eq!(json!(value), before);
    }
    let mut question = action(s, &event, &dialogue.config.combat);
    s.warning = Some(super::super::warnings::Active {
        turn: "question".into(), actions: vec![question.clone()], cancel: tokio::sync::watch::channel(false).0,
        started: false, input: Some("今の音は何？".into()), protected_until: None,
    });
    let before = json!(question);
    prepare_spontaneous_hearing(s, &mut question, &dialogue.config.combat);
    assert_eq!(json!(question), before);
    s.warning = None;
    let mut value = action(s, &event, &dialogue.config.combat);
    prepare_spontaneous_hearing(s, &mut value, &dialogue.config.combat);
    assert_eq!(submitted_hearing(&value)["sounds"][0]["presentation"], "初回提示");
    let mut unavailable = event.body_origin().unwrap().clone();
    unavailable.available = false;
    s.conversation_observation.observe_origin(&event.with_body_senses(None, unavailable));
    s.conversation_observation.observe_origin(&event);
    s.conversation_observation.observe(crate::conversation_observation::project(&event, None, 32., 7., 5000).unwrap());
    let mut value = action(s, &event, &dialogue.config.combat);
    prepare_spontaneous_hearing(s, &mut value, &dialogue.config.combat);
    assert_eq!(submitted_hearing(&value)["sounds"][0]["presentation"], "初回提示");
}
