//! Read-only conversation memory. Observe accepted events once; reading never renews memory.
//! Current observations, recent memories, and permission to say a name are separate types.
//! Event timestamps own these ages; a monotonic playback clock must not be substituted.
use crate::events::{
    Direction, DistanceBand, EventName, EventTime, GameEvent, HorizontalDirection,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub struct Settings {
    pub player_chat_visual_retention_ms: i64,
    pub player_chat_hearing_retention_ms: i64,
    pub player_chat_name_correction_retention_ms: i64,
    pub weather_sound_recent_ms: i64,
    pub home_bed_prompt_distance: f64,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            player_chat_visual_retention_ms: 12000,
            player_chat_hearing_retention_ms: 20000,
            player_chat_name_correction_retention_ms: 10000,
            weather_sound_recent_ms: 4000,
            home_bed_prompt_distance: 10.0,
        }
    }
}
/// Immutable shared catalog adapter, not a second catalog/search implementation.
/// mob_label must return Some("") for a known entry with an empty label, None only
/// for an unknown ID. The fallback is the canonical MOB_LABELS, not a guessed name.
pub trait Labels {
    fn mob_label(&self, id: &str) -> Option<&str>;
    fn mob_fallback_label(&self, id: &str) -> Option<&str>;
    fn hostile_label(&self, id: &str) -> String;
    /// Match Python block_entry: final namespace component, strip/lower, then
    /// label-or-japanese from the existing immutable block catalog.
    fn block_label(&self, id: &str) -> Option<&str>;
}
/// Names selected by the existing combat outcome owner at its memory-update phase.
/// Only the synchronous code-owned combat update may construct these values;
/// never derive them from a chat/model reply, or resend them during snapshot reads.
/// Neither list asserts current presence. Legacy disappearances remain separately
/// marked; they are not promoted to an actual death or a victory claim here.
#[derive(Default, Clone, Debug, Deserialize, Serialize)]
pub struct NameOutcomeUpdate {
    #[serde(default)]
    pub confirmed_types: Vec<String>,
    #[serde(default)]
    pub legacy_disappeared_types: Vec<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct VisualMemo {
    pub mob_type: String,
    pub label_ja: String,
    pub direction: String,
    pub distance: Option<f64>,
    pub seen_at_us: i64,
    pub dedupe_key: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HearingMemo {
    pub kind: String,
    pub mob_type: Option<String>,
    pub label_ja: Option<String>,
    pub direction: String,
    pub distance_band: String,
    pub heard_at_us: i64,
    pub dedupe_key: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CurrentObservation {
    pub visual_types: Vec<String>,
    pub passive_types: Vec<String>,
    pub hearing_types: Vec<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PassiveSighting {
    pub mob_type: String,
    pub seconds_ago: i64,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RecentObservation {
    pub visual_memos: Vec<VisualMemo>,
    pub visual_types: Vec<String>,
    pub visual_summary: Option<String>,
    pub hearing_memos: Vec<HearingMemo>,
    pub passive_sightings: Vec<PassiveSighting>,
}
/// May include recent or dead entities. Never supply this to presence grounding.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct NameContext {
    pub types: Vec<String>,
}
/// Explicitly combines current sounds and bounded recent sounds, in that order.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HearingContext {
    pub types: Vec<String>,
    pub named_mobs: Vec<String>,
    pub source_labels: Vec<String>,
    pub summary: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DistanceSample {
    pub at_us: i64,
    pub distance: f64,
}
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HomeProgress {
    Unknown,
    AtHome,
    Approaching,
    Leaving,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HomeContext {
    pub progress: HomeProgress,
    pub samples: Vec<DistanceSample>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Snapshot {
    pub current: CurrentObservation,
    pub recent: RecentObservation,
    pub name_context: NameContext,
    pub hearing: HearingContext,
    pub home: HomeContext,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MixedTimeDomains;
impl std::fmt::Display for MixedTimeDomains {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("cannot mix offset-aware and offset-naive observation times")
    }
}
impl std::error::Error for MixedTimeDomains {}
#[derive(Clone, Debug)]
pub struct ChatObservationMemory {
    settings: Settings,
    time_aware: Option<bool>,
    current_dimension: Option<String>,
    dimension_changed_at: Option<i64>,
    visual: Vec<VisualMemo>,
    hearing: Vec<HearingMemo>,
    // Insertion order is meaningful for equal timestamps and name precedence.
    // Canonical passive memory retains one timestamp per type (read projection is capped at 4).
    passive: Vec<(String, i64)>,
    killed_names: Vec<(String, i64)>,
    home: Vec<DistanceSample>,
}
impl Default for ChatObservationMemory {
    fn default() -> Self {
        Self::new(Settings::default())
    }
}
impl ChatObservationMemory {
    pub fn new(settings: Settings) -> Self {
        Self {
            settings,
            time_aware: None,
            current_dimension: None,
            dimension_changed_at: None,
            visual: vec![],
            hearing: vec![],
            passive: vec![],
            killed_names: vec![],
            home: vec![],
        }
    }
    fn time(&self, event: &GameEvent) -> Result<(i64, bool), MixedTimeDomains> {
        let (us, aware) = match &event.observed_at {
            EventTime::Aware(t) => (t.timestamp_micros(), true),
            EventTime::Naive(t) => (t.and_utc().timestamp_micros(), false),
        };
        if self.time_aware.is_some_and(|previous| previous != aware) {
            Err(MixedTimeDomains)
        } else {
            Ok((us, aware))
        }
    }
    /// Call after admission/deduplication, once for full AND partial sound events.
    /// Passing a partial event does not synthesize a full snapshot or clear old memos.
    /// Outcome names must belong to this accepted event, after dimension handling.
    pub fn observe(
        &mut self,
        event: &GameEvent,
        outcomes: &NameOutcomeUpdate,
        labels: &impl Labels,
    ) -> Result<(), MixedTimeDomains> {
        let (now, aware) = self.time(event)?;
        self.time_aware = Some(aware);
        let dimension = event
            .player
            .dimension
            .as_deref()
            .map(|v| strip(v).to_lowercase())
            .filter(|v| !v.is_empty());
        if dimension != self.current_dimension {
            let previous = self.current_dimension.take();
            self.current_dimension = dimension.clone();
            self.home.clear();
            if dimension.is_some() && (previous.is_some() || !overworld(dimension.as_deref())) {
                self.dimension_changed_at = Some(now);
            }
            if previous.is_some() && dimension.is_some() {
                self.killed_names.clear();
            }
        }
        for mob in &event.passive_mobs {
            let id = strip(&mob.r#type).to_lowercase(); // intentionally retains namespace
            if !id.is_empty() {
                put_time(&mut self.passive, id, now);
            }
        }
        self.update_home(event, now);
        self.killed_names.retain(|(_, at)| {
            age(now, *at) <= self.settings.player_chat_name_correction_retention_ms
        });
        for raw in outcomes
            .confirmed_types
            .iter()
            .chain(&outcomes.legacy_disappeared_types)
        {
            let id = normalize(raw);
            if !id.is_empty() {
                put_time(&mut self.killed_names, id, now);
            }
        }
        self.hearing.retain(|memo| {
            age(now, memo.heard_at_us) <= self.settings.player_chat_hearing_retention_ms
        });
        for sound in sounds(event, labels) {
            let memo = HearingMemo {
                kind: sound.kind,
                mob_type: sound.mob_type,
                label_ja: sound.label,
                direction: sound.direction,
                distance_band: sound.band,
                heard_at_us: now,
                dedupe_key: sound.key,
            };
            put_hearing(&mut self.hearing, memo);
        }
        for (name, label, heard) in [
            ("thunder", "雷鳴", event.world.thunder_sound_recent_ms),
            ("rain", "雨", event.world.rain_sound_recent_ms),
        ] {
            let raw = format!("weather:{name}");
            if heard.is_some_and(|at| at <= self.settings.weather_sound_recent_ms)
                && !event
                    .ambient_sounds
                    .iter()
                    .any(|sound| strip(&sound.r#type).to_lowercase() == raw)
            {
                put_hearing(
                    &mut self.hearing,
                    HearingMemo {
                        kind: "environment".into(),
                        mob_type: None,
                        label_ja: Some(label.into()),
                        direction: "周囲".into(),
                        distance_band: String::new(),
                        heard_at_us: now,
                        dedupe_key: format!("environment:{raw}:周囲:"),
                    },
                );
            }
        }
        self.hearing
            .sort_by_key(|memo| std::cmp::Reverse(memo.heard_at_us));
        self.hearing.truncate(12);
        self.visual.retain(|memo| {
            age(now, memo.seen_at_us) <= self.settings.player_chat_visual_retention_ms
        });
        for threat in &event.visual_threats {
            let mob_type = normalize(&threat.r#type);
            if mob_type.is_empty() {
                continue;
            }
            let direction = direction(&threat.direction).to_owned();
            let key = match threat.entity_id.as_deref().filter(|s| !s.is_empty()) {
                Some(id) => format!("visual:{id}"),
                None => format!("visual:{mob_type}:{direction}"),
            };
            let memo = VisualMemo {
                label_ja: labels.hostile_label(&mob_type),
                mob_type,
                direction,
                distance: threat.distance,
                seen_at_us: now,
                dedupe_key: key,
            };
            if let Some(index) = self
                .visual
                .iter()
                .position(|v| v.dedupe_key == memo.dedupe_key)
            {
                self.visual[index] = memo;
            } else {
                self.visual.push(memo);
            }
        }
        self.visual
            .sort_by_key(|memo| std::cmp::Reverse(memo.seen_at_us));
        self.visual.truncate(12);
        // machine.process removes all consumed IDs even on a repeated outcome delivery.
        // Without IDs it removes matching types; with any IDs, type removal is disabled.
        if matches!(
            event.event.name,
            EventName::HostileDefeated | EventName::CreeperDetonated
        ) {
            let all = event.combat.hostile_outcomes.as_deref().unwrap_or_default();
            let ids: Vec<_> = all
                .iter()
                .filter_map(|o| o.entity_id.as_deref().filter(|v| !v.is_empty()))
                .map(strip)
                .collect();
            let types: Vec<_> = all.iter().map(|o| normalize(&o.r#type)).collect();
            self.visual.retain(|v| {
                !ids.contains(
                    &v.dedupe_key
                        .strip_prefix("visual:")
                        .unwrap_or(&v.dedupe_key),
                ) && !(ids.is_empty() && types.contains(&v.mob_type))
            });
            self.hearing.retain(|v| {
                !ids.contains(
                    &v.dedupe_key
                        .strip_prefix("hostile:")
                        .unwrap_or(&v.dedupe_key),
                ) && !(ids.is_empty() && v.mob_type.as_ref().is_some_and(|id| types.contains(id)))
            });
        }
        Ok(())
    }
    fn update_home(&mut self, event: &GameEvent, now: i64) {
        let Some(distance) = event.world.respawn_distance.filter(|_| {
            event.world.respawn_point_set.unwrap_or(false)
                && overworld(event.player.dimension.as_deref())
        }) else {
            self.home.clear();
            return;
        };
        if self
            .home
            .last()
            .is_some_and(|s| (now - s.at_us).max(0) as f64 / 1000.0 > 10000.0)
        {
            self.home.clear();
        }
        self.home
            .retain(|s| s.at_us >= now.saturating_sub(10_000_000));
        if let Some(last) = self.home.last_mut().filter(|s| s.at_us == now) {
            last.distance = distance;
        } else {
            self.home.push(DistanceSample {
                at_us: now,
                distance,
            });
        }
        if self.home.len() > 5 {
            self.home.drain(..self.home.len() - 5);
        }
    }
    fn home_progress(&self, event: &GameEvent) -> HomeProgress {
        let Some(distance) = event
            .world
            .respawn_distance
            .filter(|_| event.world.respawn_point_set.unwrap_or(false))
        else {
            return HomeProgress::Unknown;
        };
        if distance <= self.settings.home_bed_prompt_distance
            && event.world.nearby_bed_count.unwrap_or(0) > 0
        {
            return HomeProgress::AtHome;
        }
        if self.home.len() < 3 {
            return HomeProgress::Unknown;
        }
        let deltas: Vec<_> = self
            .home
            .windows(2)
            .map(|s| s[1].distance - s[0].distance)
            .collect();
        let net = self.home.last().unwrap().distance - self.home[0].distance;
        if deltas.iter().filter(|d| **d <= -0.25).count() >= 2 && net <= -1.0 {
            HomeProgress::Approaching
        } else if deltas.iter().filter(|d| **d >= 0.25).count() >= 2 && net >= 1.0 {
            HomeProgress::Leaving
        } else {
            HomeProgress::Unknown
        }
    }
    /// Projects at event.observed_at without changing/pruning/renewing any state.
    /// Current means this supplied frame, not the most recent complete world frame.
    pub fn snapshot(
        &self,
        event: &GameEvent,
        labels: &impl Labels,
    ) -> Result<Snapshot, MixedTimeDomains> {
        let (now, _) = self.time(event)?;
        let sounds = sounds(event, labels);
        let current = CurrentObservation {
            visual_types: unique(event.visual_threats.iter().map(|v| normalize(&v.r#type))),
            passive_types: unique(event.passive_mobs.iter().map(|v| normalize(&v.r#type))),
            hearing_types: unique(
                sounds
                    .iter()
                    .filter_map(|s| s.mob_type.clone())
                    .map(|s| normalize(&s)),
            ),
        };
        let visual: Vec<_> = self
            .visual
            .iter()
            .filter(|v| age(now, v.seen_at_us) <= self.settings.player_chat_visual_retention_ms)
            .cloned()
            .collect();
        let hearing: Vec<_> = self
            .hearing
            .iter()
            .filter(|v| age(now, v.heard_at_us) <= self.settings.player_chat_hearing_retention_ms)
            .cloned()
            .collect();
        let visual_types = unique(visual.iter().map(|v| v.mob_type.clone()));
        let visual_summary = visual.first().map(|v| {
            format!(
                "ついさっき 視認 {} が{}",
                nonempty(&v.label_ja).unwrap_or(&v.mob_type),
                nonempty(&v.direction).unwrap_or("近く")
            )
        });
        let current_passive: Vec<_> = event
            .passive_mobs
            .iter()
            .map(|v| v.r#type.strip_prefix("minecraft:").unwrap_or(&v.r#type))
            .collect();
        let mut sightings: Vec<_> = self.passive.iter().collect();
        sightings.sort_by_key(|(_, at)| std::cmp::Reverse(*at));
        let passive_sightings = sightings
            .into_iter()
            .filter_map(|(id, at)| {
                let elapsed = age(now, *at);
                (!current_passive.contains(&id.strip_prefix("minecraft:").unwrap_or(id))
                    && self.dimension_changed_at.is_none_or(|change| *at >= change)
                    && elapsed >= 0
                    && elapsed <= self.settings.player_chat_visual_retention_ms.max(60000))
                .then(|| PassiveSighting {
                    mob_type: id.clone(),
                    seconds_ago: elapsed / 1000,
                })
            })
            .take(4)
            .collect();
        let recent_for_name = |at: i64| {
            age(now, at) <= self.settings.player_chat_name_correction_retention_ms
                && self.dimension_changed_at.is_none_or(|change| at >= change)
        };
        let name_context = NameContext {
            types: unique(
                current
                    .visual_types
                    .iter()
                    .chain(&current.passive_types)
                    .chain(&current.hearing_types)
                    .cloned()
                    // Do not use the shorter visual/hearing read windows to prefilter name context.
                    .chain(
                        self.visual
                            .iter()
                            .filter(|v| recent_for_name(v.seen_at_us))
                            .map(|v| normalize(&v.mob_type)),
                    )
                    .chain(
                        self.passive
                            .iter()
                            .filter(|(_, at)| recent_for_name(*at))
                            .map(|(id, _)| normalize(id)),
                    )
                    .chain(
                        self.hearing
                            .iter()
                            .filter(|v| recent_for_name(v.heard_at_us))
                            .filter_map(|v| v.mob_type.as_deref())
                            .map(normalize),
                    )
                    .chain(
                        self.killed_names
                            .iter()
                            .filter(|(_, at)| recent_for_name(*at))
                            .map(|(id, _)| normalize(id)),
                    ),
            ),
        };
        let hearing_types = unique(
            current.hearing_types.iter().cloned().chain(
                hearing
                    .iter()
                    .filter_map(|v| v.mob_type.as_deref())
                    .map(normalize),
            ),
        );
        let named_mobs = unique(
            sounds
                .iter()
                .filter_map(|s| s.mob_label.as_deref())
                .map(|v| strip(v).to_owned())
                .chain(
                    hearing
                        .iter()
                        .filter(|v| v.kind != "environment")
                        .filter_map(|v| v.label_ja.as_deref())
                        .map(|v| strip(v).to_owned()),
                ),
        );
        let source_labels = unique(
            sounds
                .iter()
                .filter_map(|s| s.environment_label.as_deref())
                .map(|v| strip(v).to_owned())
                .chain(
                    hearing
                        .iter()
                        .filter(|v| v.kind == "environment")
                        .filter_map(|v| v.label_ja.as_deref())
                        .map(|v| strip(v).to_owned()),
                ),
        );
        let mut summary = Vec::<String>::new();
        let mut seen = Vec::<String>::new();
        for sound in sounds.iter().filter(|s| s.index < 4) {
            let key = format!(
                "{}:{}:{}:{}",
                sound.kind,
                sound.label.as_deref().unwrap_or(&sound.raw),
                sound.direction,
                sound.band
            );
            if seen.contains(&key) {
                continue;
            }
            let label = sound.label.as_deref();
            summary.push(line(
                label,
                &sound.kind,
                &sound.direction,
                &sound.band,
                false,
            ));
            seen.push(key);
        }
        for memo in &hearing {
            if summary.len() >= 6 {
                break;
            }
            let key = format!(
                "{}:{}:{}:{}",
                memo.kind,
                memo.label_ja
                    .as_deref()
                    .or(memo.mob_type.as_deref())
                    .unwrap_or("unknown"),
                memo.direction,
                memo.distance_band
            );
            if seen.contains(&key) {
                continue;
            }
            summary.push(line(
                memo.label_ja.as_deref(),
                &memo.kind,
                &memo.direction,
                &memo.distance_band,
                true,
            ));
            seen.push(key);
        }
        Ok(Snapshot {
            current,
            recent: RecentObservation {
                visual_memos: visual,
                visual_types,
                visual_summary,
                hearing_memos: hearing,
                passive_sightings,
            },
            name_context,
            hearing: HearingContext {
                types: hearing_types,
                named_mobs,
                source_labels,
                summary: summary.join("、"),
            },
            home: HomeContext {
                progress: self.home_progress(event),
                samples: self.home.clone(),
            },
        })
    }
}
fn strip(s: &str) -> &str {
    s.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}
fn normalize(s: &str) -> String {
    strip(s.strip_prefix("minecraft:").unwrap_or(s)).to_lowercase()
}
fn nonempty(s: &str) -> Option<&str> {
    (!s.is_empty()).then_some(s)
}
fn age(now: i64, at: i64) -> i64 {
    // Python int(timedelta.total_seconds() * 1000): negative ages truncate toward zero.
    (((now - at) as f64 / 1_000_000.0) * 1000.0) as i64
}
fn overworld(s: Option<&str>) -> bool {
    matches!(
        s.map(|v| strip(v).to_lowercase()).as_deref(),
        None | Some("" | "overworld" | "minecraft:overworld")
    )
}
fn unique(values: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut out = vec![];
    for value in values {
        if !value.is_empty() && !out.contains(&value) {
            out.push(value);
        }
    }
    out
}
fn put_time(values: &mut Vec<(String, i64)>, id: String, at: i64) {
    if let Some(row) = values.iter_mut().find(|(key, _)| *key == id) {
        row.1 = at;
    } else {
        values.push((id, at));
    }
}
fn put_hearing(values: &mut Vec<HearingMemo>, memo: HearingMemo) {
    if let Some(index) = values.iter().position(|v| v.dedupe_key == memo.dedupe_key) {
        values[index] = memo;
    } else {
        values.push(memo);
    }
}
fn direction(d: &Direction) -> &'static str {
    match d.horizontal {
        Some(HorizontalDirection::Front) => "前",
        Some(HorizontalDirection::FrontRight) => "右前",
        Some(HorizontalDirection::Right) => "右",
        Some(HorizontalDirection::BackRight) => "右後ろ",
        Some(HorizontalDirection::Back) => "後ろ",
        Some(HorizontalDirection::BackLeft) => "左後ろ",
        Some(HorizontalDirection::Left) => "左",
        Some(HorizontalDirection::FrontLeft) => "左前",
        None => "近く",
    }
}
fn band(d: Option<DistanceBand>) -> &'static str {
    match d {
        None => "",
        Some(DistanceBand::Touching) => "touching",
        Some(DistanceBand::VeryClose) => "very_close",
        Some(DistanceBand::Close) => "close",
        Some(DistanceBand::Mid) => "mid",
        Some(DistanceBand::Far) => "far",
    }
}
pub(crate) fn resolve_mob(raw: &str, sound: Option<&str>, labels: &impl Labels) -> Option<String> {
    let mut candidates = vec![normalize(raw)];
    if let Some(sound) = sound.filter(|s| !s.is_empty()) {
        candidates.extend(
            normalize(sound)
                .replace('/', ".")
                .split('.')
                .filter(|p| {
                    !matches!(
                        *p,
                        "" | "entity" | "minecraft" | "hostile" | "neutral" | "passive"
                    )
                })
                .map(str::to_owned),
        );
    }
    candidates
        .into_iter()
        .find(|v| !v.is_empty() && labels.mob_label(v).is_some())
}
pub(crate) fn mob_label(id: Option<&str>, labels: &impl Labels) -> Option<String> {
    let id = id?;
    labels
        .mob_label(id)
        .map(strip)
        .filter(|s| !s.is_empty())
        .or_else(|| labels.mob_fallback_label(id).filter(|s| !s.is_empty()))
        .map(str::to_owned)
}
fn environment_label(raw: &str, labels: &impl Labels) -> Option<String> {
    let raw = strip(raw).to_lowercase();
    let known = match raw.as_str() {
        "weather:rain" => Some("雨"),
        "weather:thunder" => Some("雷鳴"),
        "environment:cave" => Some("洞窟の環境音"),
        "environment:underwater" => Some("水中の環境音"),
        "environment:basalt_deltas" => Some("玄武岩デルタの環境音"),
        "environment:crimson_forest" => Some("真紅の森の環境音"),
        "environment:nether_wastes" => Some("ネザーの荒地の環境音"),
        "environment:soul_sand_valley" => Some("ソウルサンドの谷の環境音"),
        "environment:warped_forest" => Some("歪んだ森の環境音"),
        _ => None,
    };
    if let Some(known) = known {
        return Some(known.into());
    }
    let id = strip(raw.strip_prefix("block:")?);
    if let Some(label) = labels.block_label(id).map(strip).filter(|s| !s.is_empty()) {
        return Some(label.into());
    }
    match id {
        "fire" => Some("火"),
        "lava" => Some("溶岩"),
        "water" => Some("水"),
        "nether_portal" => Some("ネザーポータル"),
        "bubble_column" => Some("気泡柱"),
        "trial_spawner" => Some("トライアルスポナー"),
        "pointed_dripstone" => Some("鍾乳石"),
        "wooden_door" => Some("木のドア"),
        "wooden_trapdoor" => Some("木のトラップドア"),
        "wooden_button" => Some("木のボタン"),
        "wooden_pressure_plate" => Some("木の感圧板"),
        _ => None,
    }
    .map(str::to_owned)
}
struct Sound {
    index: usize,
    kind: String,
    raw: String,
    mob_type: Option<String>,
    label: Option<String>,
    mob_label: Option<String>,
    environment_label: Option<String>,
    direction: String,
    band: String,
    key: String,
}
fn sounds(event: &GameEvent, labels: &impl Labels) -> Vec<Sound> {
    let mut result = vec![];
    for (index, audio) in event.auditory_threats.iter().enumerate() {
        let mob_type = resolve_mob(&audio.label, audio.sound_event.as_deref(), labels);
        let label = mob_label(mob_type.as_deref(), labels);
        let direction = direction(&audio.direction).to_owned();
        let band = band(audio.distance_band).to_owned();
        let key = match audio.source_id.as_deref().filter(|v| !v.is_empty()) {
            Some(id) => format!("hostile:{id}"),
            None => format!(
                "hostile:{}:{direction}:{band}",
                mob_type.as_deref().unwrap_or(&audio.label)
            ),
        };
        result.push(Sound {
            index,
            kind: "hostile".into(),
            raw: audio.label.clone(),
            mob_type,
            label: label.clone(),
            mob_label: label,
            environment_label: None,
            direction,
            band,
            key,
        });
    }
    for (index, sound) in event.ambient_sounds.iter().enumerate() {
        let mob_type = resolve_mob(&sound.r#type, sound.sound_event.as_deref(), labels);
        let mob_label = mob_label(mob_type.as_deref(), labels);
        let environment_label = environment_label(&sound.r#type, labels);
        let kind = if environment_label.is_some() {
            "environment"
        } else {
            "ambient"
        }
        .to_owned();
        let direction = direction(&sound.direction).to_owned();
        let band = band(sound.distance_band).to_owned();
        let key = format!(
            "{kind}:{}:{direction}:{band}",
            mob_type.as_deref().unwrap_or(&sound.r#type)
        );
        result.push(Sound {
            index,
            kind,
            raw: sound.r#type.clone(),
            mob_type,
            label: environment_label.clone().or(mob_label.clone()),
            mob_label,
            environment_label,
            direction,
            band,
            key,
        });
    }
    result
}
fn line(label: Option<&str>, kind: &str, direction: &str, band: &str, recent: bool) -> String {
    let prefix = match label {
        Some(label) if kind == "ambient" => format!("{label}っぽい声"),
        Some(label) => format!("{label}の音"),
        None => "音（種別未確定）".into(),
    };
    strip(&format!(
        "{prefix} {direction} {band}{}",
        if recent { "（ついさっき）" } else { "" }
    ))
    .to_owned()
}
#[cfg(test)]
#[path = "chat_observation/tests.rs"]
mod tests;
