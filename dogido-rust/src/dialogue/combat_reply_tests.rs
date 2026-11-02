//! 戦闘会話の受付・緊急中断・同じ入力の復帰と配送ACKを、sessionの境界で確認する。
//! モデル・スピーカーは起動せず、遅れて届く旧epochの結果も明示的に注入する。
use super::*;
use crate::combat::{
    core::Decision,
    model::{Mode, Speech},
};
use crate::playback::Status;

fn fixture() -> Arc<Dialogue> {
    let mut config = DialogueConfig::default();
    config.haiku.memory_enabled = false;
    let dialogue = Dialogue::new(config).unwrap();
    dialogue.register("s", "試験", true);
    let mut d = dialogue.data.lock().unwrap();
    let s = d.sessions.get_mut("s").unwrap();
    s.mode = Mode::Panic;
    s.chat_allowed = false;
    s.foreground.start_combat();
    drop(d);
    dialogue
}

fn active_reply(dialogue: &Dialogue, status: &str) {
    let mut d = dialogue.data.lock().unwrap();
    let s = d.sessions.get_mut("s").unwrap();
    s.current_turn = "question".into();
    s.epoch = 7;
    s.cancel = Some(watch::channel(false).0);
    let instance = s.instance_id.clone();
    d.rows
        .push_back(json!({"session_id":"s","session_instance":instance,
        "turn_id":"question","epoch":7,"player_input_text":"ここは何バイオーム？",
        "source":"voice","input_at_ms":17,"text":"古い返答。",
        "combat_resumable":true,"playback_status":status}));
}

fn apply(dialogue: &Arc<Dialogue>, actions: Vec<Speech>) {
    let decision = Decision {
        mode: Mode::Panic,
        chat_allowed: false,
        stop_audio: actions.iter().any(|a| a.interrupt),
        actions,
        dimension_changed: false,
        presence_only: false,
    };
    let mut jobs = dialogue.jobs.lock().unwrap();
    let mut d = dialogue.data.lock().unwrap();
    dialogue.apply_combat_decision(
        &mut d,
        &mut jobs,
        "s",
        decision,
        super::super::combat_runtime::DecisionContext {
            input: None,
            reason: Some("visual_hostile"),
            combat_priority: true,
        },
    );
}

fn urgent() -> Speech {
    let mut speech = Speech::new("creeper_fuse", "クリーパー危ない！");
    speech.interrupt = true;
    speech
}

#[tokio::test]
async fn combat_presence_allows_new_conversation_and_does_not_cancel_active_reply() {
    let dialogue = fixture();
    let response = dialogue.submit(Some("s"), "ドギド、ここは何バイオーム？", "text");
    assert_eq!(response["accepted"], true);
    assert!(response["queued"].is_null(), "{response}");
    {
        let d = dialogue.data.lock().unwrap();
        assert!(d.sessions["s"].cancel.is_some());
        assert!(d.sessions["s"].reply_queue.is_empty());
    }
    for _ in 0..3 {
        apply(&dialogue, vec![]);
    }
    assert!(dialogue.data.lock().unwrap().sessions["s"].cancel.is_some());
    dialogue.shutdown().await;
}

// 見込み: 遠隔敵の通知中も返答は継続。視認省略と交戦解除を区別し、解除された個体を古い完全観測から読み直さない。
// 合格: 模擬セッションで返答完了後の通知が視認省略なら1件、視界外・部分通知による解除なら0件。実音声は使わない。
#[tokio::test]
async fn ranged_sighting_waits_for_reply_completion_and_drops_a_departed_target() {
    for (stage, preserved, change) in [
        ("generating", false, "audio_only"),
        ("started", false, "out_of_sight"),
        ("started", true, "target_cleared"),
        ("started", true, "audio_only"),
    ] {
        let mut config = DialogueConfig {
            audio_enabled: false,
            llm_enabled: false,
            ..DialogueConfig::default()
        };
        config.haiku.enabled = false;
        config.haiku.memory_enabled = false;
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "試験", true);
        active_reply(&dialogue, stage);
        if preserved {
            super::super::reply_output::preserve(&mut dialogue.data.lock().unwrap(), "s");
        }
        let mut frame = empty_event("試験");
        frame["visual_threats"] = json!([{"type":"skeleton","entity_id":"s1","distance":13}]);
        frame["combat"]["player_engaged"] = json!(true);
        frame["combat"]["opponents"] = json!([{"entity_id":"s1","evidence":"targeting_player"}]);
        for seq in 1..=2 {
            frame["sequence"] = json!(seq);
            dialogue.observe("s", GameEvent::parse(frame.clone()).unwrap(), None);
            let d = dialogue.data.lock().unwrap();
            let s = &d.sessions["s"];
            assert_eq!(d.rows[0]["playback_status"], stage);
            assert!(s.warning.is_none() && s.reply_queue.is_empty());
            assert!(s.chat_allowed && s.foreground.combat_active);
            assert_eq!(
                s.pending_warning.as_ref().unwrap()[0].kind,
                "ranged_sighting"
            );
        }
        frame["sequence"] = json!(3);
        frame["visual_threats"] = json!([]);
        if change != "out_of_sight" {
            frame["event"]["name"] = json!("hostile_audio_detected");
            frame["event"]["source_kind"] = json!("auditory");
            if change == "target_cleared" {
                frame["combat"]["opponents"] = json!([]);
            } else {
                frame["combat"].as_object_mut().unwrap().remove("opponents");
            }
        }
        dialogue.observe("s", GameEvent::parse(frame.clone()).unwrap(), None);
        if change == "target_cleared" {
            // 後続の部分通知で相手欄が省略されても、解除された個体は戻らない。
            frame["sequence"] = json!(4);
            frame["combat"].as_object_mut().unwrap().remove("opponents");
            dialogue.observe("s", GameEvent::parse(frame).unwrap(), None);
            let d = dialogue.data.lock().unwrap();
            let current = d.sessions["s"].environment_latest.as_ref().unwrap();
            assert!(current.visual_threats.is_empty());
            assert_eq!(current.combat.opponents.as_ref().unwrap().len(), 0);
        }
        assert!(dialogue.update(
            "s",
            "question",
            7,
            Status::Completed,
            Some(&json!({"text":"返答の最後。"}))
        ));
        {
            let mut jobs = dialogue.jobs.lock().unwrap();
            let mut d = dialogue.data.lock().unwrap();
            dialogue.start_pending(&mut d, &mut jobs, "s");
            assert_eq!(d.rows[0]["playback_status"], "completed");
            assert!(d.sessions["s"].pending_warning.is_none());
            assert_eq!(
                d.rows
                    .iter()
                    .filter(|r| r["combat_actions"][0]["kind"] == "ranged_sighting")
                    .count(),
                usize::from(change == "audio_only")
            );
        }
        dialogue.shutdown().await;
    }
}

#[tokio::test]
async fn relief_and_defeat_wait_for_the_ordinary_reply_even_with_old_interrupt_flag() {
    for kind in ["aftermath", "hostile_defeated"] {
        let dialogue = fixture();
        active_reply(&dialogue, "started");
        let mut speech = Speech::new(kind, "ほっとしたで。");
        speech.interrupt = true;
        apply(&dialogue, vec![speech]);
        let mut jobs = vec![];
        {
            let mut d = dialogue.data.lock().unwrap();
            dialogue.start_pending(&mut d, &mut jobs, "s");
            assert!(d.sessions["s"].cancel.is_some());
            assert!(d.sessions["s"].reply_queue.is_empty());
            assert!(d.sessions["s"].pending_warning.is_some());
        }
        assert!(jobs.is_empty());
        dialogue.shutdown().await;
    }
}

// 模擬sessionで生成中・再生中に両種の警告を注入。本文を止め、警告完了後もその質問と同時入力を再送しない。
#[tokio::test]
async fn rear_and_fuse_discard_interrupted_and_overlapping_inputs() {
    for kind in ["ushiro_classic", "ushiro_named", "creeper_fuse"] {
        for stage in ["generating", "started"] {
            let dialogue = fixture();
            active_reply(&dialogue, stage);
            let mut warning = Speech::new(kind, "危ない！");
            warning.interrupt = true;
            apply(&dialogue, vec![warning]);
            for source in ["voice", "text"] {
                let other = dialogue.submit(Some("s"), "いま何が見える？", source);
                assert_eq!(other["reason"], "urgent_combat_warning");
            }
            {
                let mut d = dialogue.data.lock().unwrap();
                assert!(d.sessions["s"].cancel.is_none());
                assert_eq!(d.rows[0]["playback_status"], "cancelled");
                Dialogue::cancel_warning(&mut d, "s", "test_warning_finished");
            }
            dialogue.resume_reply_input("s");
            let d = dialogue.data.lock().unwrap();
            assert!(d.sessions["s"].reply_queue.is_empty() && d.sessions["s"].cancel.is_none());
            assert_eq!(d.rows[0]["epoch"], 7);
            drop(d);
            dialogue.shutdown().await;
        }
    }
}

#[tokio::test]
async fn confirmed_text_is_not_requeued_even_while_audio_is_active() {
    let dialogue = fixture();
    active_reply(&dialogue, "started");
    dialogue.enable_text_delivery("s", Some(true));
    let result = json!({"text":"ここはタイガやで。"});
    let display = {
        let mut d = dialogue.data.lock().unwrap();
        Dialogue::publish_text(
            &mut d,
            "s",
            "question",
            super::super::text_delivery::Kind::Reply { epoch: 7, result },
        )
        .unwrap()
    };
    dialogue.acknowledge_display("s", &[display]);
    assert_eq!(
        dialogue.data.lock().unwrap().rows[0]["delivery_confirmed"],
        true
    );
    apply(&dialogue, vec![urgent()]);
    assert!(
        dialogue.data.lock().unwrap().sessions["s"]
            .reply_queue
            .is_empty()
    );
    dialogue.shutdown().await;
}

#[tokio::test]
async fn duplicate_active_question_is_not_queued_behind_nonurgent_warning() {
    let dialogue = fixture();
    active_reply(&dialogue, "started");
    apply(
        &dialogue,
        vec![Speech::new("hostile_defeated", "倒せたな。")],
    );
    let response = dialogue.submit(Some("s"), "ここは何バイオーム？", "voice");
    assert_eq!(response["deduplicated"], true);
    assert_eq!(response["turn_id"], "question");
    assert!(
        dialogue.data.lock().unwrap().sessions["s"]
            .reply_queue
            .is_empty()
    );
    dialogue.shutdown().await;
}

#[tokio::test]
async fn issued_operation_is_never_retained_as_an_unanswered_question() {
    let dialogue = fixture();
    active_reply(&dialogue, "started");
    dialogue.data.lock().unwrap().rows[0]["horse_command_id"] = "issued-command".into();
    apply(&dialogue, vec![urgent()]);
    assert!(
        dialogue.data.lock().unwrap().sessions["s"]
            .reply_queue
            .is_empty()
    );
    dialogue.shutdown().await;
}

#[tokio::test]
async fn interrupted_question_rejects_late_playback_and_display() {
    // 模擬音声・表示。割込み前に作られた表示IDや音声完了が遅れて届いても返答を確定しない。
    let dialogue = fixture();
    active_reply(&dialogue, "started");
    dialogue.enable_text_delivery("s", Some(true));
    let display = {
        let mut d = dialogue.data.lock().unwrap();
        Dialogue::publish_text(&mut d, "s", "question",
            super::super::text_delivery::Kind::Reply { epoch: 7, result: json!({"text":"古い返答。"}) }).unwrap()
    };
    apply(&dialogue, vec![urgent()]);
    dialogue.acknowledge_display("s", &[display]);
    assert!(!dialogue.update("s", "question", 7, Status::Completed, Some(&json!({"text":"古い返答。"}))));
    {
        let d = dialogue.data.lock().unwrap();
        assert_eq!(d.rows[0]["playback_status"], "cancelled");
        assert_ne!(d.rows[0]["delivery_confirmed"], true);
        assert!(!d.sessions["s"].history.rows().iter().any(|r| r["turn_id"] == "question:reply"));
    }
    dialogue.shutdown().await;
}
