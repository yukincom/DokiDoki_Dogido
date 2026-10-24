//! 保留からFIFOへの所有権移譲だけを確認する。モデル・音声は起動しない。
use super::super::{reply_queue::Request, workshop_record::tests::Fixture};
use super::*;

fn paused(f: &Fixture, clear: bool) {
    let mut d = f.d.data.lock().unwrap();
    let s = d.sessions.get_mut("s").unwrap();
    // 戦闘前に川柳側で分類が完了した一件の保留を模擬する。
    let binding = super::super::request_runtime::Binding::capture(s);
    let mut classification = crate::request_classifier::fixture(
        "気にせんで",
        "text",
        crate::request_classifier::Label::NotTarget,
    );
    classification.mode = crate::request_classifier::Mode::Senryu;
    classification.prediction.probabilities = vec![1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
    s.request_classification
        .restore(s.input_generation, binding, Some(classification));
    s.preview = false;
    s.chat_allowed = clear;
    s.received = Some(Instant::now());
    let mut event = empty_event("試験");
    event["combat"] = json!({"combat_active_hint":!clear});
    s.latest = Some(crate::events::GameEvent::parse(event).unwrap());
    s.haiku.workshop.as_mut().unwrap().pause(Instant::now());
}

async fn saturated_handoff() -> (Fixture, String, String) {
    let f = super::super::workshop_record::tests::fixture();
    paused(&f, true);
    let original = {
        let mut jobs = f.d.jobs.lock().unwrap();
        let mut d = f.d.data.lock().unwrap();
        let result =
            f.d.hold_paused_input(&mut d, &mut jobs, "s", "気にせんで", "text")
                .unwrap();
        while jobs.len() < 16 {
            jobs.push(tokio::spawn(std::future::pending::<()>()));
        }
        result["turn_id"].as_str().unwrap().to_owned()
    };
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            let finished = f.d.jobs.lock().unwrap()[0].is_finished();
            if finished {
                break;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    let target = {
        let d = f.d.data.lock().unwrap();
        let s = &d.sessions["s"];
        assert!(s.combat_input.is_none());
        assert_eq!(
            s.reply_queue.len(),
            1,
            "temporary rejection must retain its owner"
        );
        assert!(s.reply_checked.is_none());
        assert!(!s.haiku.workshop.as_ref().unwrap().combat_paused());
        let original = d.rows.iter().find(|r| r["turn_id"] == original).unwrap();
        assert_eq!(original["combat_input_outcome"], "resumed");
        assert_eq!(original["forwarded_input"]["accepted"], true);
        assert_eq!(
            original["forwarded_input"]["turn_id"],
            s.reply_queue[0].request.turn
        );
        s.reply_queue[0].request.turn.clone()
    };
    (f, original, target)
}

async fn clear_saturation(f: &Fixture) {
    let jobs = std::mem::take(&mut *f.d.jobs.lock().unwrap());
    for job in jobs {
        if !job.is_finished() {
            job.abort();
        }
        let _ = job.await;
    }
}

#[tokio::test]
async fn full_submission_queue_retains_combat_input_and_retries_once() {
    let (f, original, target) = saturated_handoff().await;
    clear_saturation(&f).await;
    f.d.resume_reply_input("s");
    let jobs = std::mem::take(&mut *f.d.jobs.lock().unwrap());
    for job in jobs {
        job.await.unwrap();
    }
    f.d.resume_reply_input("s");
    {
        let d = f.d.data.lock().unwrap();
        let s = &d.sessions["s"];
        assert!(s.reply_queue.is_empty());
        assert!(s.combat_input.is_none());
        assert_eq!(s.epoch, 1);
        assert_eq!(d.rows.iter().filter(|r| r["turn_id"] == target).count(), 1);
        assert!(d.rows.iter().any(|r| r["turn_id"] == original));
        assert_eq!(
            d.rows.iter().find(|r| r["turn_id"] == target).unwrap()["player_input_text"],
            "気にせんで"
        );
    }
    let records = f.records().await;
    assert_eq!(
        records
            .iter()
            .filter(|r| r["turn_id"] == target && r["event_kind"] == "turn_result")
            .count(),
        1,
        "{records:?}"
    );
    f.finish().await;
}

#[tokio::test]
async fn reconnect_with_same_generation_cannot_receive_the_old_combat_input() {
    let (f, _, target) = saturated_handoff().await;
    let (instance, generation) = {
        let d = f.d.data.lock().unwrap();
        (
            d.sessions["s"].instance_id.clone(),
            d.sessions["s"].input_generation,
        )
    };
    f.d.close("s");
    f.d.register("s", "新しい接続", true);
    f.d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut("s")
        .unwrap()
        .input_generation = generation;
    clear_saturation(&f).await;
    let result = f.d.submit_recorded_guarded(
        Some("s"),
        "気にせんで",
        "text",
        true,
        Some((generation, Some(target.clone()))),
        true,
        super::super::workshop_record::Admission {
            forwarded: true,
            private: false,
        },
        Some(&instance),
        None,
    );
    assert_eq!(result["reason"], "superseded_session");
    f.d.resume_reply_input("s");
    {
        let d = f.d.data.lock().unwrap();
        assert!(d.sessions["s"].reply_queue.is_empty());
        assert_eq!(d.sessions["s"].epoch, 0);
        let row = d.rows.iter().find(|r| r["turn_id"] == target).unwrap();
        assert_eq!(row["playback_status"], "cancelled");
        assert_eq!(row["resolution"], "session_closed");
    }
    f.finish().await;
}

#[tokio::test]
async fn held_combat_input_reserves_the_ninth_fifo_slot() {
    let f = super::super::workshop_record::tests::fixture();
    paused(&f, false);
    {
        let mut jobs = f.d.jobs.lock().unwrap();
        let mut d = f.d.data.lock().unwrap();
        for n in 0..8 {
            Dialogue::queue_combat_reply(
                &mut d,
                "s",
                Request {
                    turn: format!("older-{n}"),
                    text: format!("先の入力{n}"),
                    source: "text".into(),
                    input_at: 0,
                    record_private: false,
                    classification: None,
                    binding: None,
                },
                super::super::workshop_record::Attempt::new(Value::Null, Default::default()),
            );
        }
        let held =
            f.d.hold_paused_input(&mut d, &mut jobs, "s", "気にせんで", "text")
                .unwrap();
        assert_eq!(held["accepted"], true);
        assert_eq!(
            super::super::reply_queue::occupied_slots(&d.sessions["s"]),
            9
        );
        let extra =
            f.d.queue_reply_input(&mut d, &mut jobs, "s", "次の入力", "text", true)
                .unwrap();
        assert_eq!(extra["reason"], "reply_queue_full");
        assert_eq!(d.sessions["s"].reply_queue.len(), 8);
        assert!(d.sessions["s"].combat_input.is_some());
    }
    f.finish().await;
}
