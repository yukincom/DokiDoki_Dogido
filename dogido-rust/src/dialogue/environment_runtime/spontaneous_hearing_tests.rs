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
    observe_scene(s, tick, json!({"ambient_sounds":sounds}), true)
}

fn observe_scene(s: &mut Session, tick: u64, fields: Value, player_visible: bool) -> GameEvent {
    let mut raw = super::super::empty_event("試験");
    raw["player"]["dimension"] = json!("minecraft:overworld");
    raw["world"] = json!({"biome":"plains","time_phase":"day","sky_visible":true});
    for (key, value) in fields.as_object().unwrap() { raw[key] = value.clone(); }
    let mut raw = json!(crate::body::tests::with_senses(raw, tick));
    raw["body_observation"]["sample"]["vision"]["player_visibility"] = json!(if player_visible {"visible"} else {"not_visible"});
    raw["body_observation"]["sample"]["vision"]["reason"] = json!(if player_visible {"in_view"} else {"occluded"});
    let raw = GameEvent::parse(raw).unwrap();
    s.player_uuid = Some("11111111-1111-4111-8111-111111111111".into());
    super::super::body_runtime::observe(s, &raw);
    s.latest = Some(raw.clone());
    s.environment_latest = Some(raw.clone());
    let event = super::super::body_runtime::event(s, &raw);
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
    let input: Value = serde_json::from_str(prepared.request.messages[1].content.lines().last().unwrap()).unwrap();
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

// 見込み: 現在は見えない本人・モブに紐付けた音は、個体IDを出さずに質問と自発入力へ届く。
// 合格: 両入力の各音に対応するemitterと観測根拠が付き、音種は不変。紐付け結果は模擬でJava採取・実モデルは別確認。
#[test]
fn linked_hearing_reaches_both_model_inputs_as_player_and_observed_mob() {
    let dialogue = server();
    let mut data = dialogue.data.lock().unwrap();
    let s = data.sessions.get_mut("hearing").unwrap();
    let initial = observe(s, 1, json!([]));
    let owner = initial.body_origin().unwrap().context.as_ref().unwrap().owner_id.clone();
    let event = observe_scene(s, 2, json!({"ambient_sounds":[
        linked_sound(&owner, "player", "block.grass.step", "observed"),
        linked_sound("44444444-4444-4444-8444-444444444444", "iron_golem", "entity.iron_golem.step", "observed")
    ]}), false);
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

// 見込み: 現在見える同個体の実音だけを外せば、音への偏りを減らしつつ見えない同種・不明音を残せる。
// 合格: 通常/自発とも本人と可視ゴーレムの実音なし、別個体・未紐付け音あり。辞書本文と生音・保持記録は維持。模擬身体のみ。
#[test]
fn visible_sources_hide_live_sounds_in_both_inputs_but_keep_catalogue_and_records() {
    let dialogue = server();
    let mut data = dialogue.data.lock().unwrap();
    let s = data.sessions.get_mut("hearing").unwrap();
    let owner = "11111111-1111-4111-8111-111111111111";
    let visible = "44444444-4444-4444-8444-444444444444";
    let hidden = "55555555-5555-4555-8555-555555555555";
    let mut hidden_sound = linked_sound(hidden, "iron_golem", "entity.iron_golem.step", "continued");
    hidden_sound["distance_band"] = json!("far");
    let event = observe_scene(s, 1, json!({
        "passive_mobs":[{"type":"iron_golem","identity":{"entity_id":visible},"environment":{"on_ground":true}}],
        "ambient_sounds":[linked_sound(owner,"player","block.grass.step","observed"),
            linked_sound(visible,"iron_golem","entity.iron_golem.step","observed"), hidden_sound,
            {"type":"unknown","source_id":"pos:unbound","sound_event":"block.stone.step","heard_ago_ms":500},
            {"type":"block:water","sound_event":"block.water.ambient","heard_ago_ms":500}]
    }), true);
    assert_eq!(event.ambient_sounds.len(), 5);
    assert_eq!(crate::chat_observation::current_hearing_candidates(&event, &crate::chat_observation::CatalogLabels).len(), 5);
    let snapshot = s.chat_observation.snapshot_body(&event, &super::super::chat_context::CatalogLabels);
    assert_eq!(snapshot.recent.hearing_memos.len(), 5);
    let mut pending = action(s, &event, &dialogue.config.combat);
    prepare_spontaneous_hearing(s, &mut pending, &dialogue.config.combat);
    for hearing in [normal_hearing(s, &event), submitted_hearing(&pending)] {
        let rows = hearing["sounds"].as_array().unwrap();
        assert_eq!(rows.len(), 3, "{hearing}");
        assert!(!rows.iter().any(|row| row["sound_event"] == "block.grass.step"));
        let golem = rows.iter().filter(|row| row["sound_event"] == "entity.iron_golem.step").collect::<Vec<_>>();
        assert_eq!(golem.len(), 1);
        assert_eq!(golem[0]["distance_band"], "far");
        assert!(rows.iter().any(|row| row["sound_event"] == "block.stone.step"));
        assert!(rows.iter().any(|row| row["sound_event"] == "block.water.ambient"));
    }
    let context = super::super::body_runtime::context_for(s, &event);
    let catalogue = context.catalog_knowledge.unwrap();
    let golem = catalogue.entries.iter().find(|entry| entry.id == "iron_golem").unwrap();
    assert_eq!(golem.general, crate::catalog_knowledge::mob("iron_golem", None, false, "fixture").unwrap().general);
    assert_eq!(snapshot, s.chat_observation.snapshot_body(&event, &super::super::chat_context::CatalogLabels));
}

// 見込み: 保持音にも今の視認を適用すれば、見えた後に旧音が復活せず、再び見えなくなると元の音齢で使える。
// 合格: 視認なし→あり→なしで両入力の実音が1→0→1、メモは常に1件、復帰時はretained_sound。模擬・保持期間内のみ。
#[test]
fn visibility_filters_retained_sounds_without_deleting_them() {
    let dialogue = server();
    let mut data = dialogue.data.lock().unwrap();
    let s = data.sessions.get_mut("hearing").unwrap();
    let id = "44444444-4444-4444-8444-444444444444";
    for (tick, visible, current, expected) in [(1,false,true,1), (2,true,false,0), (3,false,false,1)] {
        let event = observe_scene(s, tick, json!({
            "ambient_sounds":if current {json!([linked_sound(id,"iron_golem","entity.iron_golem.step","continued")])} else {json!([])},
            "passive_mobs":if visible {json!([{"type":"iron_golem","identity":{"entity_id":id},"environment":{"on_ground":true}}])} else {json!([])}
        }), true);
        let mut pending = action(s, &event, &dialogue.config.combat);
        prepare_spontaneous_hearing(s, &mut pending, &dialogue.config.combat);
        for hearing in [normal_hearing(s, &event), submitted_hearing(&pending)] {
            assert_eq!(hearing["sounds"].as_array().unwrap().len(), expected, "tick={tick}: {hearing}");
            if tick == 3 {
                assert_eq!(hearing["sounds"][0]["basis"], "retained_sound");
                assert!(hearing["sounds"][0]["heard_ago_ms"].as_i64().unwrap() >= 1000);
            }
        }
        let snapshot = s.chat_observation.snapshot_body(&event, &super::super::chat_context::CatalogLabels);
        assert_eq!(snapshot.recent.hearing_memos.len(), 1);
        if tick == 2 {
            let knowledge = super::super::body_runtime::context_for(s, &event).catalog_knowledge.unwrap();
            let golem = knowledge.entries.iter().find(|entry| entry.id == "iron_golem").unwrap();
            assert!(!golem.basis.iter().any(|basis| basis == "heard"));
        }
    }
}

// 見込み: 敵の追跡保持は視認と違う。遮蔽敵の音を残し、視認へ変わった後の古い文字列差分もモデルへ戻さない。
// 合格: 環境の実測なし→ありで両入力が1→0、生の警告材料は維持、旧音差分はモデルコピーだけ除去。模擬入力のみ。
#[test]
fn occluded_hostile_sound_remains_until_current_visibility_and_old_changes_do_not_leak() {
    let dialogue = server();
    let mut data = dialogue.data.lock().unwrap();
    let s = data.sessions.get_mut("hearing").unwrap();
    let id = "66666666-6666-4666-8666-666666666666";
    for (tick, environment, expected) in [(1,json!({}),1), (2,json!({"on_ground":true}),0)] {
        let event = observe_scene(s, tick, json!({
            "visual_threats":[{"type":"zombie","entity_id":id,"distance":8,"environment":environment}],
            "auditory_threats":[{"label":"zombie","source_id":id,"spoken_name_allowed":true,
                "sound_event":"entity.zombie.ambient","heard_ago_ms":1000}]
        }), true);
        assert_eq!(event.auditory_threats.len(), 1);
        let mut pending = action(s, &event, &dialogue.config.combat);
        prepare_spontaneous_hearing(s, &mut pending, &dialogue.config.combat);
        for hearing in [normal_hearing(s, &event), submitted_hearing(&pending)] {
            assert_eq!(hearing["sounds"].as_array().unwrap().len(), expected, "{hearing}");
        }
        let mut original = super::super::body_runtime::context_for(s, &event);
        original.changes.push(crate::conversation_observation::Change {
            kind:"heard_hostile_sounds".into(), before:json!(["古い音"]), now:json!(["うなり声"]),
            observed_at:event.observed_at.to_rfc3339(), revision:1,
        });
        let model = original.clone().for_model();
        assert!(original.changes.iter().any(|change| change.kind == "heard_hostile_sounds"));
        assert!(!model.changes.iter().any(|change| change.kind == "heard_hostile_sounds"));
        if tick == 2 {
            let knowledge = model.catalog_knowledge.unwrap();
            let zombie = knowledge.entries.iter().find(|entry| entry.id == "zombie").unwrap();
            assert!(!zombie.basis.iter().any(|basis| basis == "heard"));
        }
    }
}

// 見込み: 敵の直接entity_idを優先すれば、付随identityが不一致でも別個体の音を隠さない。
// 合格: 直接IDの音だけ除外し、identity側の別UUID音は残る。受理済み模擬観測から両モデル入力まで。
#[test]
fn hostile_visibility_uses_direct_entity_id_before_inconsistent_identity() {
    let dialogue = server();
    let mut data = dialogue.data.lock().unwrap();
    let s = data.sessions.get_mut("hearing").unwrap();
    let visible = "66666666-6666-4666-8666-666666666666";
    let other = "77777777-7777-4777-8777-777777777777";
    let event = observe_scene(s, 1, json!({
        "visual_threats":[{"type":"zombie","entity_id":visible,"identity":{"entity_id":other},"environment":{"on_ground":true}}],
        "auditory_threats":[
            {"label":"zombie","source_id":visible,"spoken_name_allowed":true,"sound_event":"entity.zombie.ambient","heard_ago_ms":0,"distance_band":"close"},
            {"label":"zombie","source_id":other,"spoken_name_allowed":true,"sound_event":"entity.zombie.ambient","heard_ago_ms":0,"distance_band":"far"}]
    }), true);
    let mut pending = action(s, &event, &dialogue.config.combat);
    prepare_spontaneous_hearing(s, &mut pending, &dialogue.config.combat);
    for hearing in [normal_hearing(s, &event), submitted_hearing(&pending)] {
        assert_eq!(hearing["sounds"].as_array().unwrap().len(), 1);
        assert_eq!(hearing["sounds"][0]["distance_band"], "far");
    }
}
