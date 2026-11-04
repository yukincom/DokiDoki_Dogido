//! 見込み: 実際に受理した直前の画像だけを組にできる。合格: 画像・撮影時刻・枠・方位・失効が期待どおり。範囲: Fabricの結果を模擬し、Rustの命令・受信・保持を通す。
use super::tests::{comparison, pending};
use super::*;
use crate::chat_image::{Heading, tests::image};

fn sequence(status: &str, fault: &str, old_yaw: Option<f64>, new_yaw: Option<f64>) -> State {
    let (mut state, template, start) = pending();
    let context = state.context.clone().unwrap();
    let mut reference = Value::Null;
    let current = if fault == "older" { 2 } else { 1 };
    for n in 0..=current {
        let mut receipt = template.clone();
        let mut at = start + chrono::Duration::seconds(n * 12);
        let mut context = context.clone();
        if n == current {
            match fault {
                "world" => context.dimension = "minecraft:the_nether".into(),
                "body" => context.epoch = uuid::Uuid::new_v4().to_string(),
                "session" => {
                    state = State {
                        enabled: true,
                        ..Default::default()
                    }
                }
                "expired" => at += chrono::Duration::seconds(30),
                _ => {}
            }
        }
        state.arm("s", Some(context.clone()), at);
        receipt.command_id = state.commands(at)[0]["command_id"].as_str().unwrap().into();
        let v = &mut receipt.body_view.as_mut().unwrap().0;
        v["dimension"] = json!(context.dimension);
        v["body_epoch"] = json!(context.epoch);
        v["capture_id"] = json!(format!("view_{}", uuid::Uuid::new_v4()));
        v["captured_at"] = json!(at);
        v["motion_sequence"] = json!(n + 1);
        v["annotation_status"] = json!(if n == 0 {
            "available"
        } else {
            "owner_not_in_render_list"
        });
        v["camera_yaw"] = json!(if n == 0 { old_yaw } else { new_yaw });
        v["scene_comparison"] = comparison("not_compared", at);
        if n == 0 {
            // 比較元と今回で異なる有効なPNGを使い、最新画像の複製では合格させない。
            let old = image();
            v["png_base64"] = json!(old.png_base64);
            v["sha256"] = json!(old.sha256);
            reference = json!({"capture_id":v["capture_id"],"captured_at":v["captured_at"],"sha256":v["sha256"]});
        }
        if n == current && status != "not_compared" {
            v["scene_comparison"] = comparison(status, at);
            v["scene_comparison"]["reference"] = reference.clone();
            match fault {
                "id" => {
                    v["scene_comparison"]["reference"]["capture_id"] =
                        json!(format!("view_{}", uuid::Uuid::new_v4()))
                }
                "hash" => v["scene_comparison"]["reference"]["sha256"] = json!("0".repeat(64)),
                "time" => {
                    v["scene_comparison"]["reference"]["captured_at"] =
                        json!(start + chrono::Duration::seconds(1))
                }
                _ => {}
            }
        }
        assert!(state.receive(&receipt, at));
        assert_eq!(state.latest.as_ref().unwrap().sequence, (n + 1) as u64);
    }
    state
}

// 見込み: 12秒間隔の2枚は、直前の照合済み画像と現在の画像を組にできる。
// 合格: PNG・時刻・PLAYER枠を各画像で区別し、北から東への旋回と通知を一度確定。範囲: 模擬受付・重複ACKは既存試験で確認。
#[test]
fn changed_keeps_exact_old_pixels_time_and_box_separate_from_current() {
    let state = sequence("changed", "", Some(179.0), Some(-90.0));
    let pair = &state.latest.as_ref().unwrap().image;
    let ComparedFrames::Changed { previous } = &pair.frames else {
        panic!("比較元が必要");
    };
    assert_eq!(previous.png_base64, image().png_base64);
    assert_eq!(pair.current.png_base64, super::tests::PNG);
    assert_eq!(
        previous.capture_id,
        pair.scene_comparison
            .as_ref()
            .unwrap()
            .reference
            .as_ref()
            .unwrap()
            .capture_id
    );
    let delta = DateTime::parse_from_rfc3339(&pair.current.captured_at).unwrap()
        - DateTime::parse_from_rfc3339(&previous.captured_at).unwrap();
    assert_eq!(delta.num_seconds(), 12);
    assert!(previous.has_player_box());
    assert!(!pair.current.has_player_box());
    assert_eq!(pair.turned_to, Some(Heading::East));
    assert!(state.ready);
}

// 見込み: 方位は画素とは独立して決まり、未取得の姿勢を補わない。
// 合格: 無変化でも北→東だけ方位あり、±180度の折返し・yaw欠落は方位なし、全件通知なし。範囲: 模擬撮影の受理。
#[test]
fn unchanged_can_turn_and_wraparound_or_missing_pose_does_not_claim_a_turn() {
    for (before, after, expected) in [
        (Some(180.0), Some(-90.0), Some(Heading::East)),
        (Some(179.0), Some(-179.0), None),
        (Some(180.0), None, None),
        (None, Some(90.0), None),
    ] {
        let state = sequence("unchanged", "", before, after);
        let image = &state.latest.as_ref().unwrap().image;
        assert!(matches!(image.frames, ComparedFrames::Unchanged));
        assert_eq!(image.turned_to, expected);
        assert!(!state.ready);
    }
}

// 見込み: 旧画像の照合失敗は現在の撮影成功と分けられる。
// 合格: 直前より古い実画像、ID・hash・時刻の不一致、身体・世界・接続の交代、失効、未比較は今回PNGのみ、方位・通知なし。範囲: 実受信処理。
#[test]
fn unmatched_expired_or_replaced_context_uses_only_current_frame() {
    for fault in [
        "id", "hash", "time", "older", "body", "world", "session", "expired", "initial",
    ] {
        let state = sequence(
            if fault == "initial" {
                "not_compared"
            } else {
                "changed"
            },
            fault,
            Some(90.0),
            Some(0.0),
        );
        let image = &state.latest.as_ref().unwrap().image;
        assert!(
            matches!(image.frames, ComparedFrames::NotCompared),
            "{fault}"
        );
        assert_eq!(image.turned_to, None);
        assert!(!state.ready);
        assert!(!image.metadata_text().contains("scene_comparison"), "{fault}");
        assert_eq!(image.current.png_base64, super::tests::PNG);
    }
}
