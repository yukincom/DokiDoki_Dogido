//! 実際に届いた音だけを共通観測へ投影する。発音者の由来と音自体の確度を分ける。
use crate::{chat_observation, events::{GameEvent, AmbientSound, AuditoryThreat}};
use chrono::{DateTime, Duration, FixedOffset};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::HashSet;

fn owner(event: &GameEvent) -> Option<&str> {
    event.body_origin().and_then(|o| o.context.as_ref()).map(|c| c.owner_id.as_str())
}
fn description(label: Option<&str>, kind: &str, sound: Option<&str>, named: bool) -> String {
    // 発音者の由来は専用欄が所有する。音種の説明に別の判定を残さない。
    chat_observation::sound_description(label, kind, sound, named)
        .trim_end_matches("（発音者未確定）").to_owned()
}
pub(crate) fn ambient(event: &GameEvent, sound: &AmbientSound) -> Value {
    let name = sound.identity.as_ref()
        .filter(|identity| sound.source_id.as_deref() == Some(identity.entity_id.as_str()))
        .and_then(crate::mob_identity::name);
    let labels = &chat_observation::CatalogLabels;
    let label = chat_observation::environment_label(&sound.r#type, labels).or_else(|| {
        let kind = chat_observation::resolve_mob(&sound.r#type, sound.sound_event.as_deref(), labels);
        chat_observation::mob_label(kind.as_deref(), labels)
    });
    let attribution = attribution(event.observed_at, sound.heard_ago_ms, owner(event),
        sound.source_id.as_deref(), sound.sound_event.as_deref(), sound.extra.get("source_context"));
    json!({"description":description(name.or(label.as_deref()), "ambient", sound.sound_event.as_deref(), name.is_some()),
        "sound_event":sound.sound_event,"direction":sound.direction,"distance_band":sound.distance_band,
        "certainty":sound.certainty,"heard_ago_ms":sound.heard_ago_ms,
        "emitter":attribution.0,"emitter_attribution":attribution.1,
        "source_survey_status":sound.extra.get("source_context").map(|v| &v["survey"]["status"]),"name":name})
}
pub(crate) fn threat(event: &GameEvent, sound: &AuditoryThreat) -> Value {
    let label = sound.spoken_name_allowed.then(|| crate::environment::ambient::mob_label(&sound.label));
    let attribution = attribution(event.observed_at, sound.heard_ago_ms, owner(event),
        sound.source_id.as_deref(), sound.sound_event.as_deref(), sound.extra.get("source_context"));
    json!({"description":description(label.as_deref(), "hostile", sound.sound_event.as_deref(), false),
        "sound_event":sound.sound_event,"direction":sound.direction,"distance_band":sound.distance_band,
        "certainty":sound.certainty,"spoken_name_allowed":sound.spoken_name_allowed,
        "heard_ago_ms":sound.heard_ago_ms,"emitter":attribution.0,"emitter_attribution":attribution.1,
        "source_survey_status":sound.extra.get("source_context").map(|v| &v["survey"]["status"])})
}
pub(crate) fn project(event: &GameEvent, weather_age_ms: u64) -> Value {
    let origin = event.body_origin();
    if let Some(origin) = origin.filter(|o| !o.available) {
        return json!({"status":"unavailable","reason":origin.body["reason"],"sounds":[]});
    }
    let mut seen = HashSet::new();
    let mut sounds = Vec::new();
    for sound in &event.ambient_sounds {
        if seen.insert(json!(["ambient", sound]).to_string()) { sounds.push(ambient(event, sound)); }
    }
    for sound in &event.auditory_threats {
        if seen.insert(json!(["threat", sound]).to_string()) { sounds.push(threat(event, sound)); }
    }
    let recent = |age: Option<i64>| age.is_some_and(|v| v >= 0 && v as u64 <= weather_age_ms);
    json!({"status":"observed","observer":if origin.is_some() {"dogido"} else {"player"},
        "observed_at":event.observed_at,
        "basis":origin.map(|o| &o.body["current"]["senses"]["hearing_basis"]),
        "truncated":origin.is_some_and(|o| o.body["current"]["senses"]["hearing_truncated"] == true),
        "rain_heard":recent(event.world.rain_sound_recent_ms),
        "thunder_heard":recent(event.world.thunder_sound_recent_ms),"sounds":sounds})
}

#[derive(Deserialize)]
struct Point { x: f64, y: f64, z: f64 }
impl Point {
    fn valid(&self) -> bool { self.x.is_finite() && self.y.is_finite() && self.z.is_finite() }
    fn distance(&self, other: &Self) -> f64 {
        ((self.x-other.x).powi(2)+(self.y-other.y).powi(2)+(self.z-other.z).powi(2)).sqrt()
    }
}
#[derive(Deserialize)]
struct SourceContext {
    heard_at: DateTime<FixedOffset>,
    source_position: Point,
    player: Player,
    survey: Survey,
}
#[derive(Deserialize)]
struct Player { id: String, position: Point, activity: String, observed_at: DateTime<FixedOffset> }
#[derive(Deserialize)]
struct Survey {
    status: String, scope: String, observed_at: DateTime<FixedOffset>,
    receiver_position: Point, radius_blocks: f64, other_emitters: Vec<Value>,
}

/// UUIDは直接の同定、pos/played等は追跡キー。観測の時刻と同時点の根拠だけで推定する。
fn attribution(observed_at: DateTime<FixedOffset>, age: Option<i64>, owner: Option<&str>,
    source: Option<&str>, sound_event: Option<&str>, context: Option<&Value>) -> (Option<&'static str>, &'static str) {
    let unknown = (None, "unknown");
    let Some(owner) = owner.and_then(|s| uuid::Uuid::parse_str(s).ok()) else { return unknown; };
    if let Some(source) = source.and_then(|s| uuid::Uuid::parse_str(s).ok()) {
        return (Some(if source == owner {"プレイヤー"} else {"本人以外"}), "confirmed");
    }
    let Some(context) = context.and_then(|v| serde_json::from_value::<SourceContext>(v.clone()).ok()) else { return unknown; };
    let Some(age) = age.filter(|age| *age >= 0) else { return unknown; };
    let Some(heard_at) = observed_at.checked_sub_signed(Duration::milliseconds(age)) else { return unknown; };
    let c = context;
    // 同一発音の時計丸め・配送の補正差を100msまで許す。現在の別観測へ付け替えない。
    let concurrent = |time: DateTime<FixedOffset>| (time-heard_at).num_milliseconds().abs() <= 100;
    let kind = sound_event.unwrap_or("").strip_prefix("minecraft:").unwrap_or(sound_event.unwrap_or(""));
    let max_distance = if kind.starts_with("block.") && kind.ends_with(".step") && c.player.activity == "walking" {
        2.0
    } else if kind.starts_with("block.") && kind.ends_with(".break") && c.player.activity == "breaking" {
        6.0
    } else { return unknown; };
    if uuid::Uuid::parse_str(&c.player.id).ok() != Some(owner)
        || !concurrent(c.heard_at) || !concurrent(c.player.observed_at) || !concurrent(c.survey.observed_at)
        || c.survey.status != "complete" || c.survey.scope != "hearing_range" || !c.survey.other_emitters.is_empty()
        || !c.source_position.valid() || !c.player.position.valid() || !c.survey.receiver_position.valid()
        || !c.survey.radius_blocks.is_finite() || !(0.0..=64.0).contains(&c.survey.radius_blocks)
        || c.survey.radius_blocks == 0.0
        || c.source_position.distance(&c.player.position) > max_distance
        || c.survey.receiver_position.distance(&c.source_position) > c.survey.radius_blocks
        || c.survey.receiver_position.distance(&c.player.position) > c.survey.radius_blocks {
        return unknown;
    }
    (Some("プレイヤー"), "inferred")
}

/// 元の聴取時刻を使い、現在の音と既存保持の音を重ねない。旧メモにない確度は補わない。
pub(crate) fn append_recent(hearing: &mut Value, event: &GameEvent, snapshot: &crate::chat_observation::Snapshot, labels: &impl crate::chat_observation::Labels) {
    if hearing["status"] != "observed" { return; }
    let now = hearing["observed_at"].as_str().and_then(|s| DateTime::parse_from_rfc3339(s).ok()).unwrap_or(event.observed_at);
    let Some(rows) = hearing["sounds"].as_array_mut() else { return; };
    for memo in chat_observation::retained_hearing(event, snapshot, labels) {
        // 雨・雷は同じ観測内のweather欄から既に渡している。
        if memo.sound_event.is_none() && memo.dedupe_key.starts_with("environment:weather:") { continue; }
        let mut fact = memo.observation.clone().unwrap_or_else(|| json!({
            "description":"音（種別未確定）","sound_event":memo.sound_event,
            "direction":memo.direction,"distance_band":memo.distance_band,
            "certainty":null,"emitter":null,"emitter_attribution":"unknown"}));
        fact["heard_ago_ms"] = json!((now.timestamp_micros().saturating_sub(memo.heard_at_us)/1000).max(0));
        fact["basis"] = json!("retained_sound");
        rows.push(fact);
    }
}
