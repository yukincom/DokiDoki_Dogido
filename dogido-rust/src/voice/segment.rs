//! パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
use std::{collections::VecDeque, time::Instant};

pub const FRAME_MS: usize = 30;
pub const FRAME_BYTES: usize = 960;

pub struct Segment {
    pub pcm: Vec<u8>,
    pub duration_ms: usize,
    pub voiced_ms: usize,
    pub captured_at: Instant,
}

/// 5秒分の30msフレームを数える診断値。音声の切り出し条件には使わない。
#[derive(Default)]
struct LevelWindow {
    frames: usize,
    loud_frames: usize,
    peak_rms: u32,
    short_segments_dropped: usize,
}

/// PCMを含まない音量・区間化の観測。録音中の長さは先行音も含む。
pub struct CaptureLevels {
    pub window_ms: usize,
    pub rms_threshold: u32,
    pub peak_rms: u32,
    pub above_threshold_ms: usize,
    pub recording_ms: usize,
    pub short_segments_dropped: usize,
    pub minimum_voiced_ms: usize,
    pub required_silence_ms: usize,
}

/// 30msフレームのRMSで発話区間を切り出す。語頭を残すため300msの先行音を保持し、
/// 各時間設定を整数フレームへ切り捨てて、最小発話・連続無音・最大長の境界を判定する。
pub struct Segmenter {
    threshold: u32,
    silence_frames: usize,
    minimum_frames: usize,
    maximum_frames: usize,
    pre_roll: VecDeque<[u8; FRAME_BYTES]>,
    speech: Vec<u8>,
    recording: bool,
    voiced: usize,
    silent: usize,
    levels: LevelWindow,
}

pub fn rms(frame: &[u8; FRAME_BYTES]) -> u32 {
    let sum: u64 = frame
        .as_chunks::<2>()
        .0
        .iter()
        .map(|s| {
            let n = i16::from_le_bytes([s[0], s[1]]) as i64;
            (n * n) as u64
        })
        .sum();
    ((sum as f64 / 480.0).sqrt()) as u32
}

impl Segmenter {
    pub fn new(threshold: u32, silence_ms: usize, minimum_ms: usize, maximum_ms: usize) -> Self {
        Self {
            threshold,
            silence_frames: (silence_ms / FRAME_MS).max(1),
            minimum_frames: (minimum_ms / FRAME_MS).max(1),
            maximum_frames: (maximum_ms / FRAME_MS).max(1),
            pre_roll: VecDeque::with_capacity(10),
            speech: Vec::new(),
            recording: false,
            voiced: 0,
            silent: 0,
            levels: LevelWindow::default(),
        }
    }

    pub fn push(&mut self, frame: [u8; FRAME_BYTES]) -> Option<Segment> {
        let frame_rms = rms(&frame);
        let loud = frame_rms >= self.threshold;
        self.levels.frames += 1;
        self.levels.loud_frames += usize::from(loud);
        self.levels.peak_rms = self.levels.peak_rms.max(frame_rms);
        if !self.recording {
            if self.pre_roll.len() == 10 {
                self.pre_roll.pop_front();
            }
            self.pre_roll.push_back(frame);
            if loud {
                self.recording = true;
                self.speech = self.pre_roll.iter().flatten().copied().collect();
                self.voiced = 1;
                self.silent = 0;
            }
            return None;
        }
        self.speech.extend_from_slice(&frame);
        if loud {
            self.voiced += 1;
            self.silent = 0;
        } else {
            self.silent += 1;
        }
        if self.silent < self.silence_frames
            && self.speech.len() / FRAME_BYTES < self.maximum_frames
        {
            return None;
        }
        self.recording = false;
        self.pre_roll.clear();
        let pcm = std::mem::take(&mut self.speech);
        if self.voiced < self.minimum_frames {
            self.levels.short_segments_dropped += 1;
        }
        (self.voiced >= self.minimum_frames).then(|| Segment {
            duration_ms: pcm.len() / FRAME_BYTES * FRAME_MS,
            voiced_ms: self.voiced * FRAME_MS,
            pcm,
            captured_at: Instant::now(),
        })
    }

    /// 約5秒ごとに診断窓だけを回収する。進行中の録音・無音判定は継続する。
    pub fn take_levels(&mut self) -> Option<CaptureLevels> {
        if self.levels.frames * FRAME_MS < 5000 {
            return None;
        }
        let levels = std::mem::take(&mut self.levels);
        Some(CaptureLevels {
            window_ms: levels.frames * FRAME_MS,
            rms_threshold: self.threshold,
            peak_rms: levels.peak_rms,
            above_threshold_ms: levels.loud_frames * FRAME_MS,
            recording_ms: if self.recording {
                self.speech.len() / FRAME_BYTES * FRAME_MS
            } else {
                0
            },
            short_segments_dropped: levels.short_segments_dropped,
            minimum_voiced_ms: self.minimum_frames * FRAME_MS,
            required_silence_ms: self.silence_frames * FRAME_MS,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn frame(sample: i16) -> [u8; FRAME_BYTES] {
        let mut f = [0; FRAME_BYTES];
        for s in f.as_chunks_mut::<2>().0 {
            s.copy_from_slice(&sample.to_le_bytes());
        }
        f
    }
    #[test]
    fn full_scale_does_not_overflow() {
        assert_eq!(rms(&frame(i16::MIN)), 32768);
        assert_eq!(rms(&frame(700)), 700);
        assert_eq!(rms(&frame(0)), 0);
    }
    #[test]
    fn pre_roll_and_800ms_floor_match_canonical_contract() {
        let mut s = Segmenter::new(700, 800, 350, 30_000);
        for _ in 0..20 {
            assert!(s.push(frame(0)).is_none());
        }
        for _ in 0..11 {
            assert!(s.push(frame(700)).is_none());
        }
        for _ in 0..25 {
            assert!(s.push(frame(0)).is_none());
        }
        let result = s.push(frame(0)).unwrap();
        assert_eq!(result.voiced_ms, 330);
        assert_eq!(result.duration_ms, (9 + 11 + 26) * 30);
        assert!(result.pcm[..9 * FRAME_BYTES].iter().all(|b| *b == 0));
    }
    #[test]
    fn short_noise_dropped_and_state_reset() {
        let mut s = Segmenter::new(700, 30, 60, 30_000);
        assert!(s.push(frame(900)).is_none());
        assert!(s.push(frame(0)).is_none());
        s.push(frame(900));
        s.push(frame(900));
        assert_eq!(s.push(frame(0)).unwrap().duration_ms, 90);
    }
    #[test]
    fn continuous_speech_is_bounded() {
        let mut s = Segmenter::new(700, 800, 30, 90);
        for _ in 0..2 {
            assert!(s.push(frame(900)).is_none());
        }
        assert_eq!(s.push(frame(900)).unwrap().duration_ms, 90);
        assert!(s.push(frame(900)).is_none());
    }

    #[test]
    fn level_window_reports_dropped_short_audio_and_resets_only_counters() {
        let mut s = Segmenter::new(700, 800, 350, 30_000);
        for i in 0..167 {
            assert!(s.push(frame(if i < 10 { 1000 } else { 650 })).is_none());
            if i < 166 {
                assert!(s.take_levels().is_none());
            }
        }
        let levels = s.take_levels().unwrap();
        assert_eq!(levels.window_ms, 5010);
        assert_eq!(levels.rms_threshold, 700);
        assert_eq!(levels.peak_rms, 1000);
        assert_eq!(levels.above_threshold_ms, 300);
        assert_eq!(levels.recording_ms, 0);
        assert_eq!(levels.short_segments_dropped, 1);
        assert_eq!(levels.minimum_voiced_ms, 330);
        assert_eq!(levels.required_silence_ms, 780);
        for _ in 0..167 {
            assert!(s.push(frame(0)).is_none());
        }
        let quiet = s.take_levels().unwrap();
        assert_eq!(quiet.peak_rms, 0);
        assert_eq!(quiet.above_threshold_ms, 0);
        assert_eq!(quiet.short_segments_dropped, 0);
    }

    #[test]
    fn taking_levels_does_not_cut_an_active_segment() {
        let mut s = Segmenter::new(700, 800, 350, 30_000);
        for _ in 0..167 {
            assert!(s.push(frame(900)).is_none());
        }
        let levels = s.take_levels().unwrap();
        assert_eq!(levels.recording_ms, 5010);
        assert_eq!(levels.above_threshold_ms, 5010);
        for _ in 0..25 {
            assert!(s.push(frame(0)).is_none());
        }
        let segment = s.push(frame(0)).unwrap();
        assert_eq!(segment.voiced_ms, 5010);
        assert_eq!(segment.duration_ms, 5790);
    }
}
