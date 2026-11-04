//! Dが照合したフレームを、そのまま最後のuser入力へ撮影順に添える。
//! 画素・参照・身体方位の比較や、欠落した比較元の代用はここでは行わない。
use super::*;
use crate::chat_image::{Attachment, ComparedFrames, Frame, Heading};

pub(super) fn request_with_image(
    input: &GenerationRequest,
    image: Option<&Attachment>,
) -> Result<CompletionRequest> {
    let mut request = to_rig_request(input);
    let Some(image) = image else {
        return Ok(request);
    };
    ensure!(
        matches!(input.kind.as_str(), "player_chat" | "environment_reaction")
            || crate::reaction_leaf::KINDS.contains(&input.kind.as_str()),
        "chat_image_wrong_generation_kind"
    );
    image.validate()?;
    let index = input
        .messages
        .iter()
        .rposition(|message| message.role == Role::User)
        .context("chat_image_user_message_missing")?;
    let mut parts = vec![json!({"type":"text","text":input.messages[index].content})];
    match &image.frames {
        ComparedFrames::Changed { previous } => {
            text(&mut parts, crate::companion_prompt::chat_image_sequence());
            text(&mut parts, "変わった");
            turn(&mut parts, image.turned_to);
            frame(&mut parts, previous, Some("00:00"));
            let old = chrono::DateTime::parse_from_rfc3339(&previous.captured_at)?;
            let now = chrono::DateTime::parse_from_rfc3339(&image.current.captured_at)?;
            let milliseconds = (now - old).num_milliseconds();
            let seconds = milliseconds / 1000;
            let fraction = milliseconds % 1000;
            let mut time = format!("{:02}:{:02}", seconds / 60, seconds % 60);
            if fraction != 0 {
                time.push_str(&format!(".{fraction:03}"));
            }
            frame(&mut parts, &image.current, Some(&time));
        }
        ComparedFrames::Unchanged => {
            text(&mut parts, "変わらない");
            turn(&mut parts, image.turned_to);
            frame(&mut parts, &image.current, None);
        }
        ComparedFrames::NotCompared => frame(&mut parts, &image.current, None),
    }
    request.additional_params.as_mut().unwrap()["messages"][index]["content"] = json!(parts);
    Ok(request)
}

fn text(parts: &mut Vec<Value>, value: &str) {
    parts.push(json!({"type":"text","text":value}));
}

fn frame(parts: &mut Vec<Value>, frame: &Frame, time: Option<&str>) {
    let mut description = time.unwrap_or("").to_owned();
    if frame.has_player_box() {
        if !description.is_empty() {
            description.push('\n');
        }
        description.push_str("緑のPLAYER枠はプレイヤーの位置やで");
    }
    if !description.is_empty() {
        text(parts, &description);
    }
    parts.push(json!({"type":"image_url","image_url":{"url":frame.data_url()}}));
}

fn turn(parts: &mut Vec<Value>, heading: Option<Heading>) {
    let Some(heading) = heading else { return };
    let heading = match heading {
        Heading::North => "北",
        Heading::Northeast => "北東",
        Heading::East => "東",
        Heading::Southeast => "南東",
        Heading::South => "南",
        Heading::Southwest => "南西",
        Heading::West => "西",
        Heading::Northwest => "北西",
    };
    text(parts, &format!("体が{heading}向きに変わったで。さっき気になった方向を確認するために、そちらを向いたところやで。今見えたものと、さっき気になったことを見比べてな。"));
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn request(image: &Attachment) -> Vec<Value> {
        let input = GenerationRequest {
            schema_version: 1,
            kind: "player_chat".into(),
            model: "fixture".into(),
            messages: vec![crate::types::ChatMessage {
                role: Role::User,
                content: "景色は？".into(),
            }],
            temperature: 0.0,
            max_tokens: 72,
            enable_thinking: false,
        };
        request_with_image(&input, Some(image))
            .unwrap()
            .additional_params
            .unwrap()["messages"][0]["content"]
            .as_array()
            .unwrap()
            .clone()
    }

    // 見込み: 枠の説明を各フレームに結び付ければ、過去だけにあった枠を現在へ移さない。
    // 合格: 新旧の枠4組合せで、各画像直前の時刻・説明が個別一致。Dの照合は固定し、実描画は対象外。
    #[test]
    fn player_box_belongs_only_to_its_frame_and_relative_time_keeps_fraction() {
        for (old_box, current_box) in [(false, false), (false, true), (true, false), (true, true)] {
            let mut image = crate::chat_image::tests::image();
            let mut previous = image.current.as_ref().clone();
            previous.annotation_status = if old_box {
                "available"
            } else {
                "owner_invisible"
            }
            .into();
            previous.captured_at = "2026-10-21T01:58:47.655Z".into();
            image.annotation_status = if current_box {
                "available"
            } else {
                "owner_invisible"
            }
            .into();
            image.frames = ComparedFrames::Changed {
                previous: Arc::new(previous),
            };
            let parts = request(&image);
            let labels: Vec<_> = parts
                .iter()
                .enumerate()
                .filter(|(_, p)| p["type"] == "image_url")
                .map(|(i, _)| parts[i - 1]["text"].as_str().unwrap())
                .collect();
            for (label, time, boxed) in [
                (labels[0], "00:00", old_box),
                (labels[1], "01:12.345", current_box),
            ] {
                assert_eq!(
                    label,
                    if boxed {
                        format!("{time}\n緑のPLAYER枠はプレイヤーの位置やで")
                    } else {
                        time.into()
                    }
                );
            }
        }
    }

    // 見込み: Dの三状態と方位を使えば、撮影姿勢からEが比較をやり直さない。
    // 合格: yaw未指定でも渡された八方位を一度表示。無変化は一枚・時刻なし、未比較は比較説明なし。実方位計測はD側で確認。
    #[test]
    fn unchanged_turn_uses_supplied_heading_and_uncompared_has_no_comparison_or_clock() {
        for (heading, name) in [
            (Heading::North, "北"),
            (Heading::Northeast, "北東"),
            (Heading::East, "東"),
            (Heading::Southeast, "南東"),
            (Heading::South, "南"),
            (Heading::Southwest, "南西"),
            (Heading::West, "西"),
            (Heading::Northwest, "北西"),
        ] {
            let mut image = crate::chat_image::tests::image();
            image.annotation_status = "owner_invisible".into();
            image.frames = ComparedFrames::Unchanged;
            image.turned_to = Some(heading);
            let parts = request(&image);
            assert_eq!(
                parts,
                vec![
                    json!({"type":"text","text":"景色は？"}),
                    json!({"type":"text","text":"変わらない"}),
                    json!({"type":"text","text":format!("体が{name}向きに変わったで。さっき気になった方向を確認するために、そちらを向いたところやで。今見えたものと、さっき気になったことを見比べてな。")}),
                    json!({"type":"image_url","image_url":{"url":image.data_url()}})
                ]
            );
            image.frames = ComparedFrames::NotCompared;
            image.turned_to = None;
            assert_eq!(request(&image), vec![parts[0].clone(), parts[3].clone()]);
        }
    }
}
