//! 通常会話へ一度だけ添えるPNGと撮影情報を検査する。
//! PNG・ハッシュ・寸法は受信照合に使う。時系列フレーム仕様ではこの診断情報をモデル本文に含めない。
//! 撮影の受付世代・鮮度・保持期限は呼出側が所有し、この型を履歴や保存JSONへ変換しない。
use anyhow::{Result, ensure};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use sha2::{Digest, Sha256};
use std::{fmt, ops::Deref, sync::Arc};

pub const MAX_PNG_BYTES: usize = 256 * 1024;
pub const MAX_BASE64_BYTES: usize = MAX_PNG_BYTES.div_ceil(3) * 4;
pub const MAX_EDGE: u32 = 192;

/// 一要求だけが借用する画像。Debugでも画像本文を出さず、serdeの保存対象にはしない。
/// captured_atはtimezone付きRFC3339、width/heightは縮小済みPNGの画素数。
#[derive(Clone)]
pub struct Frame {
    pub capture_id: String,
    pub camera_yaw: Option<f64>,
    pub png_base64: String,
    pub sha256: String,
    pub width: u32,
    pub height: u32,
    pub captured_at: String,
    pub annotation_status: String,
}

/// 既存の一枚送信はcurrentを参照する。時系列の利用側はframesから照合結果を受け取る。
#[derive(Clone, Debug)]
pub struct Attachment {
    pub current: Arc<Frame>,
    pub frames: ComparedFrames,
    pub turned_to: Option<Heading>,
    pub scene_comparison: Option<SceneComparison>,
}

mod comparison;
pub use comparison::{ComparisonReference, ComparisonStatus, SceneComparison};

#[derive(Clone, Default, Debug)]
pub enum ComparedFrames {
    #[default]
    NotCompared,
    Unchanged,
    Changed {
        previous: Arc<Frame>,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Heading {
    South,
    Southwest,
    West,
    Northwest,
    North,
    Northeast,
    East,
    Southeast,
}

impl Frame {
    pub fn has_player_box(&self) -> bool {
        self.annotation_status == "available"
    }

    pub(crate) fn heading(&self) -> Option<Heading> {
        use Heading::*;
        let yaw = self.camera_yaw.filter(|angle| angle.is_finite())?;
        let index = ((yaw.rem_euclid(360.0) + 22.5) / 45.0).floor() as usize % 8;
        Some(
            [
                South, Southwest, West, Northwest, North, Northeast, East, Southeast,
            ][index],
        )
    }

    pub(crate) fn data_url(&self) -> String {
        format!("data:image/png;base64,{}", self.png_base64)
    }
}

impl Deref for Attachment {
    type Target = Frame;
    fn deref(&self) -> &Frame {
        &self.current
    }
}

// 試験画像の破損・期限を作る時も、ほかの要求が借用中の画素は書き換えない。
#[cfg(test)]
impl std::ops::DerefMut for Attachment {
    fn deref_mut(&mut self) -> &mut Frame {
        Arc::make_mut(&mut self.current)
    }
}

impl fmt::Debug for Frame {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Frame")
            .field("png_base64", &"[redacted]")
            .field("sha256", &self.sha256)
            .field("width", &self.width)
            .field("height", &self.height)
            .field("captured_at", &self.captured_at)
            .field("annotation_status", &self.annotation_status)
            .field("camera_yaw", &self.camera_yaw)
            .finish()
    }
}

impl Attachment {
    /// 符号化長→decode→PNG/IHDR→寸法→SHA→撮影情報の順で確認する。
    /// decode前にも上限を設け、大きな入力を確保しない。鮮度や会話世代はここでは更新しない。
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.png_base64.is_empty() && self.png_base64.len() <= MAX_BASE64_BYTES,
            "chat_image_base64_size"
        );
        // decoderの詳細に入力文字を含めず、画像本文をエラー経由で記録しない。
        let bytes = STANDARD
            .decode(&self.png_base64)
            .map_err(|_| anyhow::anyhow!("chat_image_invalid_base64"))?;
        ensure!(bytes.len() <= MAX_PNG_BYTES, "chat_image_png_size");
        ensure!(
            bytes.len() >= 33
                && &bytes[..8] == b"\x89PNG\r\n\x1a\n"
                && bytes[8..12] == 13u32.to_be_bytes()
                && &bytes[12..16] == b"IHDR",
            "chat_image_invalid_png_header"
        );
        let width = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
        let height = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
        ensure!(
            (1..=MAX_EDGE).contains(&width) && (1..=MAX_EDGE).contains(&height),
            "chat_image_png_dimensions"
        );
        ensure!(
            width == self.width && height == self.height,
            "chat_image_dimension_mismatch"
        );
        ensure!(
            self.sha256.len() == 64 && self.sha256.bytes().all(|c| c.is_ascii_hexdigit()),
            "chat_image_invalid_sha256"
        );
        ensure!(
            format!("{:x}", Sha256::digest(&bytes)).eq_ignore_ascii_case(&self.sha256),
            "chat_image_sha256_mismatch"
        );
        ensure!(
            self.captured_at.len() <= 64
                && chrono::DateTime::parse_from_rfc3339(&self.captured_at).is_ok(),
            "chat_image_invalid_capture_time"
        );
        ensure!(
            matches!(
                self.annotation_status.as_str(),
                "available"
                    | "owner_not_in_render_list"
                    | "owner_invisible"
                    | "owner_occluded_or_unavailable"
                    | "owner_outside_or_near_plane"
                    | "owner_pose_unsupported"
            ),
            "chat_image_invalid_annotation_status"
        );
        if let Some(comparison) = &self.scene_comparison {
            comparison.validate(&self.captured_at)?;
        }
        Ok(())
    }

    /// 現行の単一画像用。時系列化ではこのJSONを外す。出力に不要なハッシュ・撮影時刻は受信検証だけに使う。
    pub fn metadata_text(&self) -> String {
        let mut metadata = serde_json::json!({
            "viewpoint":"dogido_client_render_eye", "media_type":"image/png",
            "captured_at":self.captured_at, "width":self.width, "height":self.height,
            "sha256":self.sha256, "annotation_status":self.annotation_status,
        });
        match self.frames {
            ComparedFrames::Changed { .. } => metadata["scene_comparison"] = "変わった".into(),
            ComparedFrames::Unchanged => metadata["scene_comparison"] = "変化がない".into(),
            ComparedFrames::NotCompared => {}
        }
        format!("image_observation: {metadata}")
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) fn image() -> Attachment {
        let png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==".to_owned();
        let bytes = STANDARD.decode(&png_base64).unwrap();
        Attachment {
            current: Arc::new(Frame {
                capture_id: format!("view_{}", uuid::Uuid::new_v4()),
                camera_yaw: None,
                sha256: format!("{:x}", Sha256::digest(bytes)),
                png_base64,
                width: 1,
                height: 1,
                captured_at: "2026-10-21T02:00:00Z".into(),
                annotation_status: "available".into(),
            }),
            frames: ComparedFrames::NotCompared,
            turned_to: None,
            scene_comparison: None,
        }
    }

    fn replace_bytes(image: &mut Attachment, bytes: &[u8]) {
        image.png_base64 = STANDARD.encode(bytes);
        image.sha256 = format!("{:x}", Sha256::digest(bytes));
    }

    #[test]
    fn accepts_bounded_png_and_keeps_image_out_of_debug_and_metadata() {
        let image = image();
        image.validate().unwrap();
        for text in [format!("{image:?}"), image.metadata_text()] {
            assert!(!text.contains(&image.png_base64));
            assert!(!text.contains("data:image/"));
            assert!(text.contains(&image.sha256));
        }
        assert!(format!("{image:?}").contains("[redacted]"));
    }

    #[test]
    fn rejects_invalid_base64_png_signature_and_ihdr() {
        let mut image = image();
        image.png_base64 = "not base64!".into();
        assert_eq!(
            image.validate().unwrap_err().to_string(),
            "chat_image_invalid_base64"
        );
        for index in [0, 11, 12] {
            let mut image = self::image();
            let mut bytes = STANDARD.decode(&image.png_base64).unwrap();
            bytes[index] ^= 1;
            replace_bytes(&mut image, &bytes);
            assert_eq!(
                image.validate().unwrap_err().to_string(),
                "chat_image_invalid_png_header"
            );
        }
        let mut image = self::image();
        let bytes = STANDARD.decode(&image.png_base64).unwrap();
        replace_bytes(&mut image, &bytes[..32]);
        assert!(image.validate().is_err());
    }

    #[test]
    fn rejects_dimension_mismatch_zero_and_oversized_header() {
        let mut image = image();
        image.width = 2;
        assert_eq!(
            image.validate().unwrap_err().to_string(),
            "chat_image_dimension_mismatch"
        );
        for (width, height) in [(0, 1), (1, 0), (193, 1), (1, 193)] {
            let mut image = self::image();
            let mut bytes = STANDARD.decode(&image.png_base64).unwrap();
            bytes[16..20].copy_from_slice(&u32::to_be_bytes(width));
            bytes[20..24].copy_from_slice(&u32::to_be_bytes(height));
            image.width = width;
            image.height = height;
            replace_bytes(&mut image, &bytes);
            assert_eq!(
                image.validate().unwrap_err().to_string(),
                "chat_image_png_dimensions"
            );
        }
    }

    #[test]
    fn rejects_both_encoded_and_decoded_size_overflow() {
        let mut image = image();
        image.png_base64 = "A".repeat(MAX_BASE64_BYTES + 1);
        assert_eq!(
            image.validate().unwrap_err().to_string(),
            "chat_image_base64_size"
        );
        replace_bytes(&mut image, &vec![0; MAX_PNG_BYTES + 1]);
        assert_eq!(image.png_base64.len(), MAX_BASE64_BYTES);
        assert_eq!(
            image.validate().unwrap_err().to_string(),
            "chat_image_png_size"
        );
    }

    #[test]
    fn rejects_changed_bytes_hash_and_unbounded_metadata() {
        let mut image = image();
        image.sha256 = "0".repeat(64);
        assert_eq!(
            image.validate().unwrap_err().to_string(),
            "chat_image_sha256_mismatch"
        );
        image.sha256 = "x".repeat(64);
        assert_eq!(
            image.validate().unwrap_err().to_string(),
            "chat_image_invalid_sha256"
        );
        let mut image = self::image();
        image.captured_at = "2026-10-21T02:00:00".into();
        assert_eq!(
            image.validate().unwrap_err().to_string(),
            "chat_image_invalid_capture_time"
        );
        let mut image = self::image();
        image.annotation_status = "available\nextra instructions".into();
        assert_eq!(
            image.validate().unwrap_err().to_string(),
            "chat_image_invalid_annotation_status"
        );
    }
}
