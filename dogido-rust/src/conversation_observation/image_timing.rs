//! 固定した画像と同じ返答へ載せる観測の順序を、絶対時計・管理番号なしで伝える。
use crate::chat_image::Attachment;
use chrono::{DateTime, FixedOffset};
use serde_json::Value;

fn relative(at: &DateTime<FixedOffset>, image: &DateTime<FixedOffset>) -> String {
    let ms = (*at - *image).num_milliseconds();
    if ms == 0 {
        return "撮影と同時".into();
    }
    format!(
        "撮影の約{:.2}秒{}",
        ms.unsigned_abs() as f64 / 1000.,
        if ms > 0 { "後" } else { "前" }
    )
}

/// 空の観測を現在値にしない。視認変化の時刻は最新eventの時刻で代用しない。
pub(crate) fn attach(world: &mut Value, image: &Attachment) {
    let Ok(at) = DateTime::parse_from_rfc3339(&image.captured_at) else {
        return;
    };
    if !world["observations"].is_object()
        || world["observations"]["body_observation"]["status"] != "observed"
    {
        return;
    }
    let clock = |row: &Value| {
        row["observed_at"]
            .as_str()
            .and_then(|t| DateTime::parse_from_rfc3339(t).ok())
    };
    let Some(observed) = clock(&world["observations"]) else {
        return;
    };
    let mut facts = vec![format!("今の観測は{}。", relative(&observed, &at))];
    if let Some(change) = world["changes"].as_array().and_then(|rows| {
        rows.iter()
            .rev()
            .find(|r| r["kind"] == "body_player_visibility")
    }) && let Some(seen) = clock(change)
        && let (Some(before), Some(now)) = (
            change["before"]["player_visibility"].as_str(),
            change["now"]["player_visibility"].as_str(),
        )
        && before != now
        && ["visible", "not_visible"].contains(&before)
        && ["visible", "not_visible"].contains(&now)
    {
        let action = if now == "visible" {
            "見えるようになった"
        } else {
            "見えなくなった"
        };
        facts.push(format!(
            "{}にドギドからプレイヤーの姿が{action}。",
            relative(&seen, &at)
        ));
    }
    world["observations"]["image_timing"] = facts.into();
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    // 見込み・範囲: 実機の時刻差を固定したモデル用コピーで確認する。モデルの発話・実描画は別。
    // 合格: 画像の3.11秒後の視認喪失と、それより後の最新観測を混同せず、前後が逆なら撮影前と表示する。
    #[test]
    fn captured_frame_has_its_own_clock_and_visibility_edge() {
        let mut image = crate::chat_image::tests::image();
        image.captured_at = "2026-11-02T17:15:35.207Z".into();
        let mut world = json!({"observations":{"observed_at":"2026-11-02T17:15:40.207Z","body_observation":{"status":"observed"}},"changes":[{"kind":"body_player_visibility","observed_at":"2026-11-02T17:15:38.318Z","before":{"player_visibility":"visible"},"now":{"player_visibility":"not_visible"}}]});
        attach(&mut world, &image);
        assert_eq!(
            world["observations"]["image_timing"],
            json!([
                "今の観測は撮影の約5.00秒後。",
                "撮影の約3.11秒後にドギドからプレイヤーの姿が見えなくなった。"
            ])
        );
        image.captured_at = "2026-11-02T17:15:41.207Z".into();
        attach(&mut world, &image);
        assert_eq!(
            world["observations"]["image_timing"][0],
            "今の観測は撮影の約1.00秒前。"
        );
    }
}
