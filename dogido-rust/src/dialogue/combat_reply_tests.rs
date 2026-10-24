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

#[tokio::test]
async fn repeated_urgent_interrupt_keeps_one_original_question_for_each_delivery_stage() {
    for stage in ["generating", "queued", "started"] {
        let dialogue = fixture();
        active_reply(&dialogue, stage);
        apply(&dialogue, vec![urgent()]);
        apply(&dialogue, vec![urgent()]);
        {
            let d = dialogue.data.lock().unwrap();
            let s = &d.sessions["s"];
            assert!(s.cancel.is_none());
            assert_eq!(s.reply_queue.len(), 1);
            let retained = &s.reply_queue[0].request;
            assert_eq!(retained.turn, "question");
            assert_eq!(retained.text, "ここは何バイオーム？");
            assert_eq!(retained.source, "voice");
            assert_eq!(retained.input_at, 17);
            assert_eq!(d.rows[0]["playback_status"], "cancelled");
        }
        dialogue.shutdown().await;
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
async fn resumed_question_has_new_display_and_epoch_and_rejects_old_callbacks() {
    let dialogue = fixture();
    active_reply(&dialogue, "started");
    dialogue.enable_text_delivery("s", Some(true));
    let old_display = {
        let mut d = dialogue.data.lock().unwrap();
        Dialogue::publish_text(
            &mut d,
            "s",
            "question",
            super::super::text_delivery::Kind::Reply {
                epoch: 7,
                result: json!({"text":"古い返答。"}),
            },
        )
        .unwrap()
    };
    apply(&dialogue, vec![urgent()]);
    {
        let mut d = dialogue.data.lock().unwrap();
        Dialogue::cancel_warning(&mut d, "s", "test_warning_finished");
    }
    dialogue.resume_reply_input("s");
    let epoch = {
        let d = dialogue.data.lock().unwrap();
        let row = &d.rows[0];
        assert_eq!(row["turn_id"], "question");
        assert_eq!(row["input_at_ms"], 17);
        assert!(row["display_id"].is_null());
        assert_eq!(row["text"], "");
        row["epoch"].as_u64().unwrap()
    };
    assert!(epoch > 7);
    assert!(!dialogue.update(
        "s",
        "question",
        7,
        Status::Completed,
        Some(&json!({"text":"古い返答。"}))
    ));
    dialogue.acknowledge_display("s", &[old_display.clone()]);
    let result = json!({"text":"今の返答やで。"});
    assert!(dialogue.update("s", "question", epoch, Status::Queued, Some(&result)));
    let new_display = {
        let mut d = dialogue.data.lock().unwrap();
        assert_ne!(d.rows[0]["delivery_confirmed"], true);
        Dialogue::publish_text(
            &mut d,
            "s",
            "question",
            super::super::text_delivery::Kind::Reply { epoch, result },
        )
        .unwrap()
    };
    assert_ne!(new_display, old_display);
    dialogue.acknowledge_display("s", &[new_display.clone(), new_display]);
    {
        let d = dialogue.data.lock().unwrap();
        assert_eq!(d.rows[0]["delivery_confirmed"], true);
        assert_eq!(
            d.sessions["s"]
                .history
                .rows()
                .iter()
                .filter(|r| r["turn_id"] == "question:reply")
                .count(),
            1
        );
    }
    dialogue.shutdown().await;
}
