use super::*;
use crate::playback::Status;
use std::sync::Arc;

fn dialogue() -> Arc<Dialogue> {
    let mut config = DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        ..Default::default()
    };
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    let dialogue = Dialogue::new(config).unwrap();
    dialogue.register("s", "プレイヤー", true);
    dialogue.enable_text_delivery("s", Some(false));
    dialogue
}

fn reply(dialogue: &Dialogue, turn: &str) -> (String, Value) {
    let epoch = {
        let mut d = dialogue.data.lock().unwrap();
        let s = d.sessions.get_mut("s").unwrap();
        s.current_turn = turn.into();
        let epoch = s.epoch;
        let instance = s.instance_id.clone();
        d.rows
            .push_back(json!({"session_id":"s","session_instance":instance,
            "turn_id":turn,"epoch":epoch,"player_input_text":"こんにちは"}));
        epoch
    };
    let result = json!({"text":"こんにちはや。","spoken_text":"こんにちはや。"});
    assert!(dialogue.update("s", turn, epoch, Status::Queued, Some(&result)));
    let snapshot = dialogue.snapshot(Some("s"));
    let id = snapshot["utterances"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["turn_id"] == turn)
        .unwrap()["display_id"]
        .as_str()
        .unwrap()
        .to_owned();
    (id, result)
}

#[tokio::test]
async fn visible_reply_is_history_once_and_never_fake_audio_completion() {
    let dialogue = dialogue();
    let (id, result) = reply(&dialogue, "reply");
    dialogue.update("s", "reply", 0, Status::AudioDisabled, Some(&result));
    assert!(
        dialogue.data.lock().unwrap().sessions["s"]
            .history
            .completed_pairs()
            .is_empty()
    );
    assert_eq!(
        dialogue.acknowledge_display("s", std::slice::from_ref(&id))["displayed"],
        json!([id])
    );
    assert_eq!(
        dialogue.snapshot(None)["utterances"][0]["playback_status"],
        "audio_disabled"
    );
    assert_eq!(
        dialogue.data.lock().unwrap().sessions["s"]
            .history
            .completed_pairs()
            .len(),
        1
    );
    dialogue.acknowledge_display("s", &[id]);
    dialogue.update("s", "reply", 0, Status::Completed, Some(&result));
    {
        let d = dialogue.data.lock().unwrap();
        assert_eq!(d.sessions["s"].history.completed_pairs().len(), 1);
        assert_eq!(d.sessions["s"].haiku.material_turns.len(), 1);
    }
    dialogue.shutdown().await;
}

#[tokio::test]
async fn another_session_cancelled_reply_and_stale_epoch_cannot_confirm_display() {
    let dialogue = dialogue();
    let (id, _) = reply(&dialogue, "reply");
    assert_eq!(
        dialogue.acknowledge_display("other", std::slice::from_ref(&id))["rejected"],
        json!([id])
    );
    {
        let mut d = dialogue.data.lock().unwrap();
        d.rows[0]["playback_status"] = "cancelled".into();
    }
    assert_eq!(
        dialogue.acknowledge_display("s", std::slice::from_ref(&id))["rejected"],
        json!([id])
    );
    {
        let mut d = dialogue.data.lock().unwrap();
        d.rows[0]["playback_status"] = "audio_disabled".into();
        d.sessions.get_mut("s").unwrap().epoch += 1;
    }
    assert_eq!(
        dialogue.acknowledge_display("s", std::slice::from_ref(&id))["rejected"],
        json!([id])
    );
    assert!(
        dialogue.data.lock().unwrap().sessions["s"]
            .history
            .completed_pairs()
            .is_empty()
    );
    dialogue.shutdown().await;
}

#[tokio::test]
async fn voice_switch_cancels_active_work_and_rejects_previous_capture_generation() {
    let dialogue = dialogue();
    let on = dialogue.set_voice_enabled("s", true);
    let generation = on["voice_generation"].as_str().unwrap().to_owned();
    assert_eq!(
        dialogue.validate_voice_input(Some("s"), None).unwrap()["reason"],
        "stale_voice_generation"
    );
    let (id, _) = reply(&dialogue, "reply");
    let (cancel, receiver) = watch::channel(false);
    dialogue
        .data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .cancel = Some(cancel);
    let off = dialogue.set_voice_enabled("s", false);
    assert!(*receiver.borrow());
    assert!(!dialogue.output_config("s").audio_enabled);
    assert_eq!(dialogue.set_voice_enabled("s", false), off);
    assert_eq!(
        dialogue.submit(Some("s"), "うおおお", "voice")["reason"],
        "voice_disabled"
    );
    assert_eq!(
        dialogue.acknowledge_display("s", std::slice::from_ref(&id))["rejected"],
        json!([id])
    );
    dialogue.set_voice_enabled("s", true);
    assert_eq!(
        dialogue
            .validate_voice_input(Some("s"), Some(&generation))
            .unwrap()["reason"],
        "stale_voice_generation"
    );
    let current = dialogue.voice_context();
    assert!(
        dialogue
            .validate_voice_input(Some("s"), current["voice_generation"].as_str())
            .is_none()
    );
    dialogue.shutdown().await;
}

#[tokio::test]
async fn recognition_log_is_bounded_separate_from_history_and_suppressed_while_off() {
    let dialogue = dialogue();
    dialogue.record_voice_input(Some("s"), "休止中", "voice");
    assert!(
        dialogue.data.lock().unwrap().sessions["s"]
            .delivery
            .voice_inputs
            .is_empty()
    );
    dialogue.set_voice_enabled("s", true);
    for n in 0..205 {
        dialogue.record_voice_input(Some("s"), &format!("入力{n}"), "voice");
    }
    {
        let d = dialogue.data.lock().unwrap();
        let s = &d.sessions["s"];
        assert_eq!(s.delivery.voice_inputs.len(), 200);
        assert_eq!(s.delivery.voice_inputs[0]["text"], "入力5");
        assert!(s.history.rows().is_empty());
    }
    dialogue.shutdown().await;
}

#[tokio::test]
async fn warning_display_confirms_only_authoritative_text_once() {
    let dialogue = dialogue();
    let (turn, _cancel, id) = {
        let mut d = dialogue.data.lock().unwrap();
        let (turn, cancel) = Dialogue::queue_actions(
            &mut d,
            "s",
            &[crate::combat::model::Speech::new("warning", "危ないで！")],
            None,
        );
        let id = Dialogue::publish_text(&mut d, "s", &turn, Kind::Warning).unwrap();
        (turn, cancel, id)
    };
    assert_eq!(
        dialogue.acknowledge_display("s", std::slice::from_ref(&id))["displayed"],
        json!([id])
    );
    let before = dialogue.snapshot(None)["sessions"][0]["history"].clone();
    {
        let mut d = dialogue.data.lock().unwrap();
        assert!(dialogue.confirm_warning_delivery(&mut d, "s", &turn, false));
    }
    assert_eq!(dialogue.snapshot(None)["sessions"][0]["history"], before);
    assert_eq!(
        dialogue.snapshot(None)["utterances"][0]["playback_status"],
        "queued"
    );
    dialogue.shutdown().await;
}

// 分類済みの終了を注入し、分類器や生成モデルを呼ばずRustの反映境界を検査する。
fn pending_close(dialogue: &Dialogue) -> (u64, Value, crate::haiku_record::Workshop) {
    let mut d = dialogue.data.lock().unwrap();
    let s = d.sessions.get_mut("s").unwrap();
    s.current_turn = "close".into();
    let w = s.haiku.workshop.as_mut().unwrap();
    let mut proposal = w.current_lines.clone();
    proposal[0].surface_text = "さくらいろ".into();
    proposal[0].reading_text = "さくらいろ".into();
    proposal[0].provenance = "player_explicit".into();
    proposal[0].source_atom_ids.clear();
    proposal[0].source_atoms.clear();
    w.pending = Some(crate::workshop_edit::Pending::stage(
        &w.current_lines, &w.current_lines, proposal, 0,
    ).unwrap());
    w.begin_editing(Some(0));
    w.revision_id = Some("kept_revision".into());
    w.version = 7;
    let before = w.clone();
    let epoch = s.epoch;
    let instance = s.instance_id.clone();
    let result = json!({"text":"元の句はそのままにして、案は採用せずにここで終わるで。",
        "workshop_action":"close_workshop", "workshop_version":w.version, "workshop_id":w.hud_id});
    d.rows.push_back(json!({"session_id":"s","session_instance":instance,"turn_id":"close",
        "epoch":epoch,"player_input_text":"そうおしまいにしてください","workshop_id":before.hud_id}));
    (epoch, result, before)
}

#[tokio::test]
async fn pending_close_keeps_canonical_and_confirms_its_text_once_after_version_changes() {
    let f = crate::dialogue::workshop_record::tests::fixture();
    f.d.enable_text_delivery("s", Some(false));
    let (epoch, result, before) = pending_close(&f.d);
    assert!(f.d.update("s", "close", epoch, Status::Queued, Some(&result)));
    {
        let data = f.d.data.lock().unwrap();
        let w = data.sessions["s"].haiku.workshop.as_ref().unwrap();
        assert!(!w.open && w.pending.is_none() && w.editing.is_none());
        assert_eq!(w.version, before.version + 1);
        assert_eq!(w.current_lines, before.current_lines);
        assert_eq!(w.revision_id, before.revision_id);
        assert!(w.dialogue.is_empty());
    }
    f.d.update("s", "close", epoch, Status::AudioDisabled, Some(&result));
    let snapshot = f.d.snapshot(Some("s"));
    let id = snapshot["utterances"].as_array().unwrap().iter()
        .find(|row| row["turn_id"] == "close").unwrap()["display_id"].as_str().unwrap().to_owned();
    assert_eq!(f.d.acknowledge_display("s", std::slice::from_ref(&id))["displayed"], json!([id]));
    f.d.acknowledge_display("s", &[id]);
    f.d.update("s", "close", epoch, Status::Completed, Some(&result));
    assert_eq!(f.d.data.lock().unwrap().sessions["s"].haiku.workshop.as_ref().unwrap().dialogue.len(), 1);
    f.finish().await;
}

#[tokio::test]
async fn stale_or_paused_pending_close_cannot_discard_the_current_proposal() {
    for cause in ["version", "workshop", "epoch", "combat"] {
        let f = crate::dialogue::workshop_record::tests::fixture();
        let (epoch, result, before) = pending_close(&f.d);
        {
            let mut data = f.d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            let w = s.haiku.workshop.as_mut().unwrap();
            match cause {
                "version" => w.version += 1,
                "workshop" => w.hud_id = "replacement_workshop".into(),
                "epoch" => s.epoch += 1,
                "combat" => { w.pause(std::time::Instant::now()); }
                _ => unreachable!(),
            }
        }
        assert!(!f.d.update("s", "close", epoch, Status::Queued, Some(&result)), "{cause}");
        {
            let data = f.d.data.lock().unwrap();
            let w = data.sessions["s"].haiku.workshop.as_ref().unwrap();
            assert!(w.open, "{cause}");
            assert_eq!(w.pending, before.pending, "{cause}");
            assert_eq!(w.current_lines, before.current_lines, "{cause}");
            assert_eq!(w.revision_id, before.revision_id, "{cause}");
            assert_eq!(w.editing, before.editing, "{cause}");
        }
        f.finish().await;
    }
}
