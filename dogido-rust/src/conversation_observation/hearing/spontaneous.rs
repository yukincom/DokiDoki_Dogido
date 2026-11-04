//! 自発発話の入力コピーだけを音源・音種で集約する。聴覚記録・質問用の投影は変えない。
use crate::chat_observation::SoundCandidate;
use serde_json::{Value, json};
use std::collections::HashMap;

type Key = (String, String, String);

/// 前回の自発生成へ提示した音だけ。身体世代とともにリセットし、発話成否とは分ける。
#[derive(Default)]
pub(crate) struct Spontaneous {
    previous: HashMap<Key, i64>,
    retention_us: u64,
}

fn key(sound: &SoundCandidate) -> Option<Key> {
    let memo = &sound.memo;
    let source = memo.source_id.as_deref().filter(|s| !s.is_empty())?;
    let event = memo.sound_event.as_deref().filter(|s| !s.is_empty())?;
    // pos:/played:も同じ追跡キー内だけでまとめる。別位置を同じ個体と推定しない。
    // 警戒の区分が変わった音を、前の通常音の継続にはしない。
    Some((memo.kind.clone(), source.to_owned(), event.trim_start_matches("minecraft:").to_owned()))
}

impl Spontaneous {
    /// 判断間隔が長くても、実際に聴取が続いた音は継続する。既出でない音を提示済みにしない。
    pub(crate) fn observe(&mut self, event: &crate::events::GameEvent) {
        if self.previous.is_empty() { return; }
        let sounds = crate::chat_observation::current_hearing_candidates(event, &crate::chat_observation::CatalogLabels);
        self.refresh(event.observed_at.timestamp_micros(), sounds);
    }
    fn refresh(&mut self, now_us: i64, sounds: Vec<SoundCandidate>) {
        for sound in sounds {
            if let Some(previous) = key(&sound).and_then(|key| self.previous.get_mut(&key))
                && sound.memo.heard_at_us.abs_diff(*previous) <= self.retention_us {
                *previous = (*previous).max(sound.memo.heard_at_us);
            }
        }
        self.previous.retain(|_, heard_at| now_us.saturating_sub(*heard_at).max(0) as u64 <= self.retention_us);
    }
    pub(crate) fn present(&mut self, hearing: &mut Value, candidates: Vec<SoundCandidate>, retention_ms: u64) {
        if !matches!(hearing["status"].as_str(), Some("observed" | "partial")) {
            self.previous.clear();
            return;
        }
        let Some(now) = hearing["observed_at"].as_str()
            .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok()) else { return; };
        self.retention_us = retention_ms.saturating_mul(1000);
        let mut positions = HashMap::<Key, usize>::new();
        let mut groups = Vec::<SoundCandidate>::new();
        for sound in candidates {
            if let Some(key) = key(&sound) {
                if let Some(&index) = positions.get(&key) {
                    let before = &groups[index];
                    if (sound.memo.heard_at_us, sound.current) > (before.memo.heard_at_us, before.current) {
                        groups[index] = sound;
                    }
                    continue;
                }
                positions.insert(key, groups.len());
            }
            // 音源・音種の欠損を同じ「不明」グループへ集めない。
            groups.push(sound);
        }
        let mut next = HashMap::new();
        let mut rows = Vec::with_capacity(groups.len());
        for sound in groups {
            let mut fact = super::memo_fact(&sound.memo, now.timestamp_micros());
            if !sound.current { fact["basis"] = json!("retained_sound"); }
            if let Some(key) = key(&sound) {
                let continued = self.previous.get(&key).is_some_and(|previous| {
                    sound.memo.heard_at_us.abs_diff(*previous) <= self.retention_us
                });
                fact["presentation"] = json!(if continued { "継続提示" } else { "初回提示" });
                next.insert(key, sound.memo.heard_at_us);
            }
            rows.push(fact);
        }
        hearing["sounds"] = json!(rows);
        self.previous = next;
    }
}

#[cfg(test)]
mod tests;
