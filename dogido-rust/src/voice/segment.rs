use std::{collections::VecDeque, time::Instant};

pub const FRAME_MS: usize = 30;
pub const FRAME_BYTES: usize = 960;

pub struct Segment {
    pub pcm: Vec<u8>,
    pub duration_ms: usize,
    pub voiced_ms: usize,
    pub captured_at: Instant,
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
        }
    }

    pub fn push(&mut self, frame: [u8; FRAME_BYTES]) -> Option<Segment> {
        let loud = rms(&frame) >= self.threshold;
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
        (self.voiced >= self.minimum_frames).then(|| Segment {
            duration_ms: pcm.len() / FRAME_BYTES * FRAME_MS,
            voiced_ms: self.voiced * FRAME_MS,
            pcm,
            captured_at: Instant::now(),
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
    fn pre_roll_and_800ms_floor_match_python() {
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
}
