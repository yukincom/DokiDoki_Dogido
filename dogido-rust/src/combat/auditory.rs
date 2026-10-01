//! 音観測は視認集合と独立して数える。音の空配列では視認や個体の導火edgeを消さない。
use super::{
    catalog,
    model::{LeafRequest, Scope, Settings, Speech, elapsed, label},
};
use crate::{
    events::{AuditoryThreat, DistanceBand, GameEvent, HorizontalDirection as H},
    threats,
};
use std::collections::{HashMap, HashSet};

#[derive(Clone)]
struct Presence {
    count: u32,
    last_seen: u64,
    first_x: Option<f64>,
    first_z: Option<f64>,
    pending: Option<u32>,
    held_busy: bool,
}
#[derive(Default)]
pub struct Auditory {
    presence: HashMap<String, Presence>,
    last_occluded: Option<u64>,
    last_warden_chasing: Option<u64>,
}
pub fn key(t: &AuditoryThreat) -> String {
    t.source_id
        .as_ref()
        .filter(|id| !id.is_empty())
        .cloned()
        .unwrap_or_else(|| {
            t.direction
                .horizontal
                .map(|d| {
                    serde_json::to_value(d)
                        .unwrap()
                        .as_str()
                        .unwrap()
                        .to_owned()
                })
                .unwrap_or_else(|| "nearby".into())
        })
}
pub fn rank(t: &AuditoryThreat) -> u8 {
    match t.distance_band {
        Some(DistanceBand::Touching) => 0,
        Some(DistanceBand::VeryClose) => 1,
        Some(DistanceBand::Close) => 2,
        Some(DistanceBand::Mid) => 3,
        Some(DistanceBand::Far) => 4,
        None => 99,
    }
}
fn direction(t: &AuditoryThreat) -> &'static str {
    match t.direction.horizontal {
        Some(H::Front) => "前",
        Some(H::FrontRight) => "右前",
        Some(H::Right) => "右",
        Some(H::BackRight) => "右後ろ",
        Some(H::Back) => "後ろ",
        Some(H::BackLeft) => "左後ろ",
        Some(H::Left) => "左",
        Some(H::FrontLeft) => "左前",
        None => "近く",
    }
}
pub fn unseen(e: &GameEvent) -> Vec<&AuditoryThreat> {
    let visible: HashSet<_> = e
        .visual_threats
        .iter()
        .filter_map(|t| t.entity_id.as_deref())
        .filter(|id| !id.is_empty())
        .collect();
    e.auditory_threats
        .iter()
        .filter(|t| {
            t.source_id
                .as_deref()
                .is_none_or(|id| !visible.contains(id))
        })
        .collect()
}
impl Auditory {
    pub fn observe(&mut self, e: &GameEvent, now: u64, busy: bool, s: &Settings) {
        self.presence
            .retain(|_, p| now.saturating_sub(p.last_seen) < s.ms("hostile_comment_cooldown_ms"));
        for t in unseen(e) {
            let p = self.presence.entry(key(t)).or_insert(Presence {
                count: 0,
                last_seen: now,
                first_x: e.player.position.x,
                first_z: e.player.position.z,
                pending: None,
                held_busy: false,
            });
            p.count = p.count.saturating_add(1);
            p.last_seen = now;
            if matches!(p.count, 1 | 4 | 10) {
                p.pending = Some(p.count);
                p.held_busy = busy;
            } else if !p.held_busy {
                p.pending = None;
            }
        }
    }
    /// busy中に1/4/10回目を通過しても、次の配送可能時に最新の一件を届ける。
    pub fn select(
        &mut self,
        e: &GameEvent,
        now: u64,
        occluded: bool,
        s: &Settings,
        policy: &threats::Policy,
        ws: &threats::Settings,
    ) -> Option<Speech> {
        let mut candidates = unseen(e);
        candidates.sort_by_key(|t| rank(t));
        let t = candidates.into_iter().find(|t| {
            !policy.commented_recent(&key(t), now, ws)
                && self
                    .presence
                    .get(&key(t))
                    .is_some_and(|p| p.pending.is_some())
        })?;
        let k = key(t);
        let p = self.presence.get(&k)?;
        let count = p.pending?;
        let other = !matches!(
            e.player.dimension.as_deref().unwrap_or(""),
            "" | "overworld" | "minecraft:overworld"
        );
        let generic = other
            && (e.visual_threats.len() >= s.ms("other_realm_swarm_visual_threshold") as usize
                || e.auditory_threats.len()
                    >= s.ms("other_realm_audio_generic_threshold") as usize);
        let name = (t.spoken_name_allowed && !generic).then(|| label(&t.label));
        let mut speech = if e.visual_threats.is_empty() && occluded {
            if !elapsed(
                now,
                self.last_occluded,
                s.ms("occluded_hostile_presence_comment_cooldown_ms"),
            ) {
                return None;
            }
            self.last_occluded = Some(now);
            let mut speech = Speech::new(
                "occluded_hostile_presence",
                "敵対モブがおるな……ちょっと気になるわ。",
            );
            let seed = format!(
                "{}:{}:{}:{}",
                t.sound_event.as_deref().unwrap_or("unknown"),
                e.sequence.unwrap_or(0),
                direction(t),
                rank(t)
            );
            let hint = ["反響", "足音", "気配", "自分ツッコミ"]
                [seed.chars().map(|c| c as usize).sum::<usize>() % 4];
            speech.leaf = Some(LeafRequest {
                kind: "occluded_hostile_presence".into(),
                temperature: 0.58,
                details: serde_json::json!({"player_name":super::core::call_name(e,s),"biome":e.world.biome.as_deref().unwrap_or("unknown"),
                    "time_phase":e.world.time_phase,"direction":direction(t),"hostile":name.as_deref().unwrap_or("敵対モブ"),
                    "distance_band":t.distance_band,"certainty":t.certainty,"sound_event":t.sound_event.as_deref().unwrap_or("unknown"),"variation_hint":hint}),
            });
            speech
        } else {
            let stage = match count {
                1 => "single",
                4 => "persistent",
                _ => {
                    let distance = match (
                        e.player.position.x,
                        e.player.position.z,
                        p.first_x,
                        p.first_z,
                    ) {
                        (Some(x), Some(z), Some(ox), Some(oz)) => (x - ox).hypot(z - oz),
                        _ => 0.0,
                    };
                    if distance <= s.number("auditory_ignore_distance") {
                        "shadowing"
                    } else {
                        "chasing"
                    }
                }
            };
            let warden = stage == "chasing" && t.label.trim().eq_ignore_ascii_case("warden");
            if warden {
                if !elapsed(
                    now,
                    self.last_warden_chasing,
                    s.ms("warden_chasing_comment_cooldown_ms"),
                ) {
                    return None;
                }
                self.last_warden_chasing = Some(now);
            }
            let variant = format!(
                "{stage}_{}",
                if name.is_some() && !warden {
                    "named"
                } else {
                    "unknown"
                }
            );
            let text = catalog::text("combat", &["auditory_presence", &variant])
                .replace("{direction}", direction(t))
                .replace("{label}", name.as_deref().unwrap_or(""));
            Speech::new("auditory_hostile", text)
        };
        speech.scope = Scope::Auditory(vec![k.clone()]);
        self.presence.get_mut(&k)?.pending = None;
        self.presence.get_mut(&k)?.held_busy = false;
        Some(speech)
    }
}
