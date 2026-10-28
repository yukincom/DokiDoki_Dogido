//! 履歴に発言時刻・返答／自発／警告の区別・返答先が残るかを調べる。実際の受付・配送確定・復元処理を通し、未配送や重複を発話済みにしないことも確認する。実音声や画面は起動しない。
use super::{Dialogue, DialogueConfig, PlaybackStatus, history::History};
use crate::combat::model::{Delivery, Speech};
use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use std::{sync::Arc, time::Duration};
#[path = "history_metadata/support.rs"]
mod support;
use support::*;

// H01: プレイヤーの発言を受け付け、本文・受付時刻・入力の種類が履歴へ残ることを確認する。
#[tokio::test]
async fn input_admission_records_time_and_player_input_kind() {
    let d = dialogue();
    // 生成jobの実行だけを止め、受付の記録処理は本体へ通す。分類済みの通常入力を渡す。
    let permit = d.serial.acquire().await.unwrap();
    let before = Utc::now();
    let accepted = d.submit_inner(Some(SESSION), QUESTION, "text", true, None, true);
    let after = Utc::now();
    assert_eq!(
        accepted["accepted"], true,
        "入力fixtureが通常受付へ到達すること: {accepted}"
    );
    let recorded = rows(&d);
    drop(permit);
    d.shutdown().await;
    assert_eq!(recorded.len(), 1);
    assert_eq!(recorded[0]["role"], "user");
    assert_eq!(recorded[0]["text"], QUESTION);
    assert_eq!(recorded[0]["turn_id"], accepted["turn_id"]);
    assert_metadata(&recorded[0], "player_input", None);
    assert_time(&recorded[0], before, after);
}

// H02: 音声開始だけでは記録せず、完了後に時刻と元の問いを残す。途中で別の入力が来ても、返答先を取り違えない。
#[tokio::test]
async fn audio_completion_records_delivery_time_and_actual_reply_target() {
    let d = dialogue();
    let result = queued_reply(&d);
    assert_no_assistant(&d);
    assert!(d.update(SESSION, TURN, 0, PlaybackStatus::Started, Some(&result)));
    assert_no_assistant(&d);
    // 直近のuser行から返答先を推測すると、別の入力へ誤接続する配置。
    d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut(SESSION)
        .unwrap()
        .history
        .push("other-input", "user", "これは別の入力");
    tokio::time::sleep(Duration::from_millis(1100)).await;
    let before = Utc::now();
    assert!(d.update(SESSION, TURN, 0, PlaybackStatus::Completed, Some(&result)));
    let after = Utc::now();
    let row = reply_row(&d, TURN);
    d.shutdown().await;
    assert_eq!(row["role"], "assistant");
    assert_eq!(row["text"], ANSWER);
    assert_metadata(&row, "reply", Some(TURN));
    assert_time(&row, before, after);
}

// H03: 音声OFFでは画面への表示確認後に返答を記録する。表示成功を音声の再生完了へ置き換えない。
#[tokio::test]
async fn text_ack_records_reply_metadata_without_faking_audio_completion() {
    let d = dialogue();
    d.enable_text_delivery(SESSION, Some(false));
    let result = queued_reply(&d);
    let id = display_id(&d, TURN);
    assert!(d.update(
        SESSION,
        TURN,
        0,
        PlaybackStatus::AudioDisabled,
        Some(&result)
    ));
    assert_no_assistant(&d);
    let before = Utc::now();
    assert_eq!(
        d.acknowledge_display(SESSION, std::slice::from_ref(&id))["displayed"],
        json!([id])
    );
    let after = Utc::now();
    let row = reply_row(&d, TURN);
    let playback = d.snapshot(Some(SESSION))["utterances"][0]["playback_status"].clone();
    d.shutdown().await;
    assert_eq!(playback, "audio_disabled");
    assert_metadata(&row, "reply", Some(TURN));
    assert_time(&row, before, after);
}

// H04: 表示確認と音声完了がどちらの順に届いても、一度だけ記録し、最初の時刻・発言の種類・返答先を保つ。
#[tokio::test]
async fn duplicate_ack_and_audio_completion_preserve_first_record_in_either_order() {
    for text_first in [true, false] {
        let d = dialogue();
        d.enable_text_delivery(SESSION, Some(false));
        let result = queued_reply(&d);
        let id = display_id(&d, TURN);
        if text_first {
            d.acknowledge_display(SESSION, std::slice::from_ref(&id));
        } else {
            assert!(d.update(SESSION, TURN, 0, PlaybackStatus::Completed, Some(&result)));
        }
        let first = reply_row(&d, TURN);
        // 秒精度の時刻でも、二度目へ更新してしまう実装を検出できる間隔。
        tokio::time::sleep(Duration::from_millis(1100)).await;
        d.acknowledge_display(SESSION, std::slice::from_ref(&id));
        assert!(d.update(SESSION, TURN, 0, PlaybackStatus::Completed, Some(&result)));
        d.acknowledge_display(SESSION, &[id]);
        let recorded = rows(&d);
        {
            let data = d.data.lock().unwrap();
            assert_eq!(data.sessions[SESSION].history.completed_pairs().len(), 1);
            assert_eq!(data.sessions[SESSION].haiku.material_turns.len(), 1);
        }
        d.shutdown().await;
        assert_eq!(
            recorded.iter().filter(|r| r["role"] == "assistant").count(),
            1
        );
        assert_eq!(
            recorded.iter().find(|r| r["role"] == "assistant").unwrap(),
            &first,
            "text_first={text_first}: 後続通知で時刻・契機・返答先を更新しない"
        );
    }
}

async fn check_source(delivery: Delivery, expected: &str) {
    let d = dialogue();
    d.enable_text_delivery(SESSION, Some(false));
    d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut(SESSION)
        .unwrap()
        .history
        .push(TURN, "user", QUESTION);
    // 本文・kindを同一にする。配送元が変わるときだけ、履歴の発話契機が変わるべき。
    let mut action = Speech::new("metadata_fixture", "朝になったな。");
    action.delivery = delivery;
    let (turn, id) = {
        let mut data = d.data.lock().unwrap();
        let (turn, _cancel) = Dialogue::queue_actions(&mut data, SESSION, &[action], None);
        let id = Dialogue::publish_text(
            &mut data,
            SESSION,
            &turn,
            super::text_delivery::Kind::Warning,
        )
        .unwrap();
        (turn, id)
    };
    assert_no_assistant(&d);
    let before = Utc::now();
    assert_eq!(
        d.acknowledge_display(SESSION, std::slice::from_ref(&id))["displayed"],
        json!([id])
    );
    let after = Utc::now();
    let row = reply_row(&d, &turn);
    d.shutdown().await;
    assert_eq!(row["text"], "朝になったな。");
    assert_metadata(&row, expected, None);
    assert_time(&row, before, after);
}
// H05: 自分から話した「朝になったな」を自発発言として記録し、直前の問いへの返答にしない。
#[tokio::test]
async fn ambient_delivery_is_spontaneous_without_invented_reply_target() {
    check_source(Delivery::Ambient, "spontaneous").await;
}
// H06: 自発発言と同じ本文でも、戦闘の警告経路から届いたものは警告として記録する。単語から種類を推測しない。
#[tokio::test]
async fn combat_delivery_is_warning_even_when_its_words_match_ambient() {
    check_source(Delivery::Combat, "warning").await;
}
// H07: 環境の緊急警告を、普通の雑談や直前の問いへの返答と区別して記録する。
#[tokio::test]
async fn urgent_environment_delivery_is_warning_even_when_its_words_match_ambient() {
    check_source(Delivery::UrgentEnvironment, "warning").await;
}

// H08: 保存した履歴を復元しても、元の時刻・発言の種類・返答先を保つ。復元した現在時刻へ書き換えない。
#[tokio::test]
async fn snapshot_json_and_real_restore_preserve_time_kind_and_reply_target() {
    let source = dialogue();
    source
        .open_text_workshop(SESSION, emission(), "saved".into())
        .unwrap();
    let mut recovery =
        json!({"dialogue":source.snapshot(None),"workshop":source.workshop_snapshot(SESSION, 0)});
    let expected = saved_rows();
    // 保存済み属性が復元で落ちる問題を、入力側の未実装と独立して検出する。
    recovery["dialogue"]["sessions"][0]["history"] = json!(expected);
    let encoded = serde_json::to_vec(&recovery).unwrap();
    let decoded: Value = serde_json::from_slice(&encoded).unwrap();
    let restored = dialogue();
    restored
        .open_text_workshop(SESSION, emission(), "saved".into())
        .unwrap();
    restored
        .restore_text_conversation(SESSION, &decoded)
        .unwrap();
    let actual = rows(&restored);
    source.shutdown().await;
    restored.shutdown().await;
    assert_eq!(
        actual, expected,
        "復元時刻への付替え・契機や返答先の脱落を認めない"
    );
}

// H09: 古い記録にない時刻・発言の種類・返答先は不明のまま扱う。本文やIDの見た目から補わない。
#[test]
fn legacy_rows_keep_missing_time_kind_and_reply_target_unknown() {
    let mut history = History::default();
    // IDと本文が警告らしくても、実入力や配送元の情報は存在しない。
    history.restore_row(
        &json!({"turn_id":"warning:legacy:reply","role":"assistant","text":"危ないで！"}),
    );
    let row = &history.rows()[0];
    assert_eq!(row["text"], "危ないで！");
    assert!(row["occurred_at"].is_null());
    assert!(row["reply_to_turn_id"].is_null());
    assert!(row["utterance_kind"].is_null() || row["utterance_kind"] == "unknown");
}

// H10: 再生失敗・取消・古い処理の完了通知を、発話済みの履歴や川柳の会話材料へ入れない。
#[tokio::test]
async fn failed_cancelled_and_old_epoch_results_do_not_create_spoken_history() {
    for status in [PlaybackStatus::Failed, PlaybackStatus::Cancelled] {
        let d = dialogue();
        let result = queued_reply(&d);
        assert!(d.update(SESSION, TURN, 0, status, Some(&result)));
        assert_no_assistant(&d);
        d.shutdown().await;
    }
    let d = dialogue();
    let result = queued_reply(&d);
    d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut(SESSION)
        .unwrap()
        .epoch += 1;
    assert!(!d.update(SESSION, TURN, 0, PlaybackStatus::Completed, Some(&result)));
    assert_no_assistant(&d);
    d.shutdown().await;
}

// H11: 別の接続や古い処理の表示確認で、現在の返答を表示済みにしない。
#[tokio::test]
async fn wrong_session_and_old_epoch_display_ack_cannot_confirm_history() {
    let d = dialogue();
    d.enable_text_delivery(SESSION, Some(false));
    queued_reply(&d);
    let id = display_id(&d, TURN);
    assert_eq!(
        d.acknowledge_display("another-session", std::slice::from_ref(&id))["rejected"],
        json!([id])
    );
    d.data
        .lock()
        .unwrap()
        .sessions
        .get_mut(SESSION)
        .unwrap()
        .epoch += 1;
    assert_eq!(
        d.acknowledge_display(SESSION, std::slice::from_ref(&id))["rejected"],
        json!([id])
    );
    assert_no_assistant(&d);
    d.shutdown().await;
}

// H12: 沈黙は「黙った」という出来事として残し、発言や会話一往復、川柳の材料には数えない。
#[tokio::test]
async fn silence_remains_event_and_never_becomes_spoken_reply_or_haiku_material() {
    let d = dialogue();
    pending_reply(&d);
    let result = json!({"text":"","dialogue_action":"silent"});
    assert!(d.update(SESSION, TURN, 0, PlaybackStatus::Quiet, Some(&result)));
    let recorded = rows(&d);
    assert_no_assistant(&d);
    d.shutdown().await;
    let event = recorded
        .iter()
        .find(|r| r["turn_id"] == format!("{TURN}:reply"))
        .unwrap();
    assert_eq!(event["role"], "event");
    assert_eq!(event["reaction"], "silent");
}

// H13: 20往復と新しい補助40行を保持し、20往復時に古い10往復を要約する条件を保つ。履歴削減で問題を隠さない。
#[test]
fn retention_and_compaction_still_keep_twenty_turns_and_forty_auxiliary_rows() {
    let mut history = History::default();
    for n in 0..20 {
        if n == 19 {
            assert!(history.compaction().is_none(), "19往復では圧縮を開始しない");
        }
        let turn = format!("turn-{n}");
        history.push(&turn, "user", &format!("問い{n}"));
        history.push(&turn, "assistant", &format!("返答{n}"));
    }
    for n in 0..50 {
        history.push(&format!("ambient-{n}"), "assistant", &format!("自発{n}"));
    }
    let rows = history.rows();
    assert_eq!(rows.iter().filter(|r| r["role"] == "user").count(), 20);
    assert_eq!(rows.len(), 80, "履歴を減らして反復問題を隠さない");
    assert!(!rows.iter().any(|r| r["text"] == "自発9"));
    assert!(rows.iter().any(|r| r["text"] == "自発10"));
    assert_eq!(
        history.compaction().unwrap().turns,
        10,
        "20往復時に古い10往復を圧縮する条件を保つ"
    );
}
