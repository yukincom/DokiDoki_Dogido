//! 通常会話へ一度だけ添えるPNGと撮影情報を検査する。
//! 入力はbase64・画像hash・寸法・撮影情報、出力は検証結果と本文を含まない説明だけ。
//! 撮影の受付世代・鮮度・保持期限は呼出側が所有し、この型を履歴や保存JSONへ変換しない。
use anyhow::{Result, ensure};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use sha2::{Digest, Sha256};
use std::fmt;

pub const MAX_PNG_BYTES: usize = 256 * 1024;
pub const MAX_BASE64_BYTES: usize = MAX_PNG_BYTES.div_ceil(3) * 4;
pub const MAX_EDGE: u32 = 192;

/// 一要求だけが借用する画像。Debugでも画像本文を出さず、serdeの保存対象にはしない。
/// captured_atはtimezone付きRFC3339、width/heightは縮小済みPNGの画素数。
#[derive(Clone)]
pub struct Attachment {
    pub png_base64: String,
    pub sha256: String,
    pub width: u32,
    pub height: u32,
    pub captured_at: String,
    pub annotation_status: String,
    pub scene_comparison: Option<SceneComparison>,
}

/// Fabricが同じ身体世代・地点の4枚前と比較した結果。未比較を「変化なし」へ補完しない。
#[derive(Clone, Debug, serde::Serialize)]
pub struct SceneComparison {
    pub visual_change: bool,
    pub compared_to_four_before: bool,
}

impl fmt::Debug for Attachment {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Attachment")
            .field("png_base64", &"[redacted]")
            .field("sha256", &self.sha256)
            .field("width", &self.width)
            .field("height", &self.height)
            .field("captured_at", &self.captured_at)
            .field("annotation_status", &self.annotation_status)
            .field("scene_comparison", &self.scene_comparison)
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
        ensure!(self.scene_comparison.as_ref().is_none_or(|c| c.visual_change || c.compared_to_four_before),
            "chat_image_unverified_no_change");
        Ok(())
    }

    /// 検証済みの撮影情報だけを今回のuser本文へ添える。人格指示・現在性の推定は加えない。
    pub fn metadata_text(&self) -> String {
        format!(
            "image_observation: {}",
            serde_json::json!({
                "viewpoint":"dogido_client_render_eye", "media_type":"image/png",
                "captured_at":self.captured_at, "width":self.width, "height":self.height,
                "sha256":self.sha256, "annotation_status":self.annotation_status,
                "scene_comparison":self.scene_comparison,
                "scene_change":self.scene_comparison.as_ref().map(|c| if !c.compared_to_four_before {
                    "not_compared"
                } else if c.visual_change { "changed" } else { "unchanged" }),
            })
        )
    }

    pub(crate) fn data_url(&self) -> String {
        format!("data:image/png;base64,{}", self.png_base64)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) fn image() -> Attachment {
        let png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==".to_owned();
        let bytes = STANDARD.decode(&png_base64).unwrap();
        Attachment {
            sha256: format!("{:x}", Sha256::digest(bytes)),
            png_base64,
            width: 1,
            height: 1,
            captured_at: "2026-10-21T02:00:00Z".into(),
            annotation_status: "available".into(),
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
