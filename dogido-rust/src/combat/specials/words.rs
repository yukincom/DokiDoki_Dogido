use super::*;
use crate::events::EventTime;
use std::{cmp::Ordering, sync::LazyLock};

use crate::entry_catalog::BIOMES;
use crate::entry_catalog::NEUTRAL;
static FALLBACKS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../../data/fallbacks/general.json"))
        .expect("fallback catalog")
});
static THREATS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../threat_catalog.json")).expect("threat catalog")
});

pub(super) fn norm(v: &str) -> String {
    let lowered = v.trim().to_lowercase();
    lowered
        .strip_prefix("minecraft:")
        .unwrap_or(&lowered)
        .to_owned()
}
pub(super) fn is_overworld(d: &str) -> bool {
    matches!(d, "" | "overworld" | "minecraft:overworld")
}
pub(super) fn is_other_dimension(d: &str) -> bool {
    matches!(
        d,
        "the_nether" | "the_end" | "minecraft:the_nether" | "minecraft:the_end"
    )
}
pub(super) fn is_boss(k: &str) -> bool {
    matches!(
        norm(k).as_str(),
        "warden" | "wither" | "ender_dragon" | "elder_guardian" | "ravager"
    )
}
pub(super) fn is_flying(k: &str) -> bool {
    matches!(
        norm(k).as_str(),
        "blaze" | "ender_dragon" | "ghast" | "phantom" | "vex" | "wither"
    )
}
pub(super) fn is_ranged(k: &str) -> bool {
    THREATS["ranged"]
        .as_array()
        .expect("ranged catalog")
        .iter()
        .any(|x| x.as_str() == Some(norm(k).as_str()))
}
pub(super) fn is_neutral(k: &str) -> bool {
    NEUTRAL["items"].get(norm(k)).is_some()
}
pub(super) fn burns_in_daylight(k: &str) -> bool {
    crate::mob_environment::burns_in_daylight(k)
}
pub(super) fn daylight(e: &GameEvent) -> bool {
    matches!(
        e.world.time_phase,
        Some(TimePhase::Morning | TimePhase::Day)
    ) && e.world.sky_visible == Some(true)
}
pub(super) fn is_perch(p: &str) -> bool {
    matches!(
        p,
        "landing_approach"
            | "landing"
            | "sitting_flaming"
            | "sitting_scanning"
            | "sitting_attacking"
    )
}
pub(super) fn dragon_phase(e: &GameEvent) -> Option<String> {
    e.combat
        .dragon_phase
        .as_deref()
        .map(norm)
        .filter(|v| !v.is_empty())
}
pub(super) fn dragon_seen(e: &GameEvent) -> bool {
    e.visual_threats
        .iter()
        .any(|t| norm(&t.r#type) == "ender_dragon")
}
pub(super) fn ground_count(e: &GameEvent, s: &Settings) -> usize {
    e.combat
        .hostiles_within_scan_ground
        .map(|n| n.max(0) as usize)
        .unwrap_or_else(|| {
            e.visual_threats
                .iter()
                .filter(|t| {
                    !is_flying(&t.r#type)
                        && t.distance
                            .is_some_and(|d| d <= s.number("hostile_query_distance"))
                })
                .count()
        })
}
pub(super) fn distance_order(a: &&VisualThreat, b: &&VisualThreat) -> Ordering {
    a.distance
        .unwrap_or(f64::INFINITY)
        .total_cmp(&b.distance.unwrap_or(f64::INFINITY))
}
pub(super) fn priority(a: &&VisualThreat, b: &&VisualThreat) -> Ordering {
    let key = |t: &VisualThreat| {
        let d = t.distance.unwrap_or(f64::INFINITY);
        let range = THREATS["effective_range"][norm(&t.r#type)]
            .as_f64()
            .unwrap_or(3.0);
        (
            d > 4.0,
            is_ranged(&t.r#type) || d > 4.5,
            d > range,
            d / range.max(0.1),
            !matches!(
                t.direction.horizontal,
                Some(H::Back | H::BackLeft | H::BackRight)
            ),
            !t.approaching,
        )
    };
    let a_key = key(a);
    let b_key = key(b);
    a_key
        .0
        .cmp(&b_key.0)
        .then(a_key.1.cmp(&b_key.1))
        .then(a_key.2.cmp(&b_key.2))
        .then(a_key.3.total_cmp(&b_key.3))
        .then(a_key.4.cmp(&b_key.4))
        .then(a_key.5.cmp(&b_key.5))
        .then(a.r#type.cmp(&b.r#type))
}
pub(super) fn call_name(e: &GameEvent, s: &Settings) -> String {
    e.meta
        .call_name
        .as_deref()
        .filter(|v| !v.trim().is_empty())
        .or_else(|| Some(s.text("default_call_name")).filter(|v| !v.trim().is_empty()))
        .or_else(|| e.player.name.as_deref().filter(|v| !v.trim().is_empty()))
        .unwrap_or("プレイヤー")
        .trim()
        .to_owned()
}
pub(super) fn choose(topic: &str, keys: &[&str], seed: &str) -> String {
    let choices = catalog::node(topic, keys)
        .as_array()
        .expect("catalog choices");
    choices[seed.chars().map(|c| c as usize).sum::<usize>() % choices.len()]
        .as_str()
        .expect("catalog choice")
        .to_owned()
}
pub(super) fn sequence(e: &GameEvent) -> String {
    e.sequence
        .filter(|v| *v != 0)
        .map(|v| v.to_string())
        .unwrap_or_default()
}
pub(super) fn date(e: &GameEvent) -> String {
    match &e.observed_at {
        EventTime::Aware(t) => format!(
            "{}{}{}",
            t.format("%Y-%m-%dT%H:%M:%S"),
            if t.timestamp_subsec_micros() == 0 {
                String::new()
            } else {
                format!(".{:06}", t.timestamp_subsec_micros())
            },
            t.format("%:z")
        ),
        EventTime::Naive(t) => format!(
            "{}{}",
            t.format("%Y-%m-%dT%H:%M:%S"),
            if t.and_utc().timestamp_subsec_micros() == 0 {
                String::new()
            } else {
                format!(".{:06}", t.and_utc().timestamp_subsec_micros())
            }
        ),
    }
}
pub(super) fn massive(e: &GameEvent, mode: Mode) -> String {
    if mode == Mode::SuppressedPanic {
        return catalog::text("combat", &["pressure", "hostile_massive_suppressed"]);
    }
    let seed = format!(
        "{}|{}|{}|{}",
        sequence(e),
        date(e),
        e.player
            .dimension
            .as_deref()
            .unwrap_or("")
            .trim()
            .to_lowercase(),
        norm(e.world.biome.as_deref().unwrap_or(""))
    );
    choose("combat", &["pressure", "hostile_massive_variants"], &seed)
}
pub(super) fn fallback(key: &str) -> String {
    FALLBACKS["combat"][key]
        .as_str()
        .expect("combat fallback")
        .to_owned()
}
pub(super) fn biome(e: &GameEvent) -> Option<(&'static str, &'static Value)> {
    let id = norm(e.world.biome.as_deref()?);
    BIOMES["groups"]
        .as_object()?
        .iter()
        .find_map(|(group, content)| content["biomes"].get(&id).map(|v| (group.as_str(), v)))
}
pub(super) fn biome_label(e: &GameEvent) -> String {
    biome(e)
        .and_then(|(_, b)| b["japanese"].as_str())
        .unwrap_or(e.world.biome.as_deref().unwrap_or("いまの場所"))
        .to_owned()
}
pub(super) fn time_phase(e: &GameEvent) -> Value {
    e.world
        .time_phase
        .as_ref()
        .map(|v| serde_json::to_value(v).expect("enum"))
        .unwrap_or(json!("unknown"))
}
pub(super) fn severity(k: &str) -> u8 {
    match k {
        "sculk_sensor" => 1,
        "sculk_shrieker" => 2,
        "warden_heartbeat" => 3,
        "warden_presence" => 4,
        "warden_sonic_boom" => 5,
        _ => 0,
    }
}
pub(super) fn ominous_context(e: &GameEvent, k: &str) -> bool {
    !matches!(k, "sculk_sensor" | "sculk_shrieker")
        || norm(e.world.biome.as_deref().unwrap_or("")) == "deep_dark"
        || e.visual_threats.iter().any(|t| norm(&t.r#type) == "warden")
        || e.auditory_threats
            .iter()
            .any(|t| norm(&t.label) == "warden")
}
pub(super) fn fresh_ominous(e: &GameEvent, s: &Settings) -> Option<String> {
    let k = norm(e.world.ominous_sound_kind.as_deref()?);
    (!k.is_empty()
        && e.world
            .ominous_sound_recent_ms
            .is_some_and(|n| n >= 0 && n as u64 <= s.ms("ominous_sound_reset_ms"))
        && ominous_context(e, &k))
    .then_some(k)
}

/// うしろ定型の互換選択専用。認証・暗号用途には使わない。
pub(super) fn sha1_first(input: &[u8]) -> u8 {
    let mut bytes = input.to_vec();
    let bit_len = (bytes.len() as u64) * 8;
    bytes.push(0x80);
    while bytes.len() % 64 != 56 {
        bytes.push(0);
    }
    bytes.extend(bit_len.to_be_bytes());
    let mut h = [
        0x67452301u32,
        0xefcdab89,
        0x98badcfe,
        0x10325476,
        0xc3d2e1f0,
    ];
    for block in bytes.as_chunks::<64>().0 {
        let mut w = [0u32; 80];
        for i in 0..16 {
            w[i] = u32::from_be_bytes(block[i * 4..i * 4 + 4].try_into().expect("four bytes"));
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = h;
        for (i, word) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | (!b & d), 0x5a827999u32),
                20..=39 => (b ^ c ^ d, 0x6ed9eba1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8f1bbcdc),
                _ => (b ^ c ^ d, 0xca62c1d6),
            };
            let t = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*word);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = t;
        }
        for (value, add) in h.iter_mut().zip([a, b, c, d, e]) {
            *value = value.wrapping_add(add);
        }
    }
    h[0].to_be_bytes()[0]
}
