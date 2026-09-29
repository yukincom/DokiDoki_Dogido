//! Internal delivery lifecycle, serialized only at API/log boundaries.
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Ready,
    Generating,
    Queued,
    Started,
    Completed,
    Cancelled,
    Failed,
    Unsupported,
    Quiet,
    NotSelected,
    AudioDisabled,
}

impl Status {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ready => "ready",
            Self::Generating => "generating",
            Self::Queued => "queued",
            Self::Started => "started",
            Self::Completed => "completed",
            Self::Cancelled => "cancelled",
            Self::Failed => "failed",
            Self::Unsupported => "unsupported",
            Self::Quiet => "quiet",
            Self::NotSelected => "not_selected",
            Self::AudioDisabled => "audio_disabled",
        }
    }

    /// Ending a turn does not imply that the player heard it.
    pub const fn is_terminal(self) -> bool {
        match self {
            Self::Ready | Self::Generating | Self::Queued | Self::Started => false,
            Self::Completed
            | Self::Cancelled
            | Self::Failed
            | Self::Unsupported
            | Self::Quiet
            | Self::NotSelected
            | Self::AudioDisabled => true,
        }
    }

    pub const fn ends_playback(self) -> bool {
        match self {
            Self::Completed | Self::Cancelled | Self::Failed | Self::AudioDisabled => true,
            Self::Ready
            | Self::Generating
            | Self::Queued
            | Self::Started
            | Self::Unsupported
            | Self::Quiet
            | Self::NotSelected => false,
        }
    }
}

impl std::fmt::Display for Status {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

impl From<Status> for serde_json::Value {
    fn from(value: Status) -> Self {
        value.as_str().into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn wire_values_and_terminal_semantics_stay_distinct() {
        for status in [
            Status::Ready,
            Status::Generating,
            Status::Queued,
            Status::Started,
            Status::Completed,
            Status::Cancelled,
            Status::Failed,
            Status::Unsupported,
            Status::Quiet,
            Status::NotSelected,
            Status::AudioDisabled,
        ] {
            let wire = serde_json::to_value(status).unwrap();
            assert_eq!(wire, status.as_str());
            assert_eq!(serde_json::from_value::<Status>(wire).unwrap(), status);
            assert!(!status.ends_playback() || status.is_terminal());
        }
        assert!(Status::AudioDisabled.ends_playback());
        assert!(Status::NotSelected.is_terminal());
        assert!(!Status::NotSelected.ends_playback());
        assert!(serde_json::from_str::<Status>("\"complete\"").is_err());
    }
}
