use super::*;
use crate::events::{VehicleState, VehicleStateActivity as Activity};
pub(super) fn overworld(e: &GameEvent) -> bool {
    matches!(
        e.player.dimension.as_deref().unwrap_or(""),
        "" | "overworld" | "minecraft:overworld"
    )
}
pub(super) fn phase(e: &GameEvent) -> Option<&'static str> {
    use crate::events::TimePhase::*;
    if !overworld(e) {
        return None;
    }
    match e.world.time_phase {
        Some(Morning) => Some("morning"),
        Some(Day) => Some("day"),
        Some(Evening) => Some("evening"),
        Some(Night) => Some("night"),
        None => None,
    }
}
pub fn player_vehicle_fact(vehicle: Option<&VehicleState>) -> String {
    let Some(v) = vehicle else {
        return String::new();
    };
    let id = catalog::norm(&v.vehicle_id);
    let entry = catalog::mob(&id, None, false);
    let label = entry["label"]
        .as_str()
        .or_else(|| catalog::vehicle_item_label(&id))
        .unwrap_or(match id.as_str() {
            "boat" => "ボート",
            "chest_boat" => "チェスト付きボート",
            "raft" => "イカダ",
            "chest_raft" => "チェスト付きイカダ",
            "minecart" => "トロッコ",
            "chest_minecart" => "チェスト付きトロッコ",
            "command_block_minecart" => "コマンドブロック付きトロッコ",
            "furnace_minecart" => "かまど付きトロッコ",
            "hopper_minecart" => "ホッパー付きトロッコ",
            "spawner_minecart" => "スポナー付きトロッコ",
            "tnt_minecart" => "TNT付きトロッコ",
            "donkey" => "ロバ",
            "nautilus" => "オウムガイ",
            "zombie_nautilus" => "ゾンビオウムガイ",
            _ => "乗り物",
        });
    let activity = match v.activity {
        Activity::Riding => "乗っている",
        Activity::Moving => "乗って移動している",
        Activity::Running => "乗って走っている",
        Activity::Rowing => "乗って漕いでいる",
        Activity::Dashing => "乗ってダッシュしている",
    };
    format!("プレイヤーは{label}に{activity}")
}
fn cold(e: &GameEvent) -> bool {
    [
        "snowy_plains",
        "ice_spikes",
        "snowy_taiga",
        "snowy_slopes",
        "frozen_river",
        "snowy_beach",
        "frozen_ocean",
        "deep_frozen_ocean",
        "frozen_peaks",
        "jagged_peaks",
        "grove",
    ]
    .contains(
        &e.world
            .biome
            .as_deref()
            .unwrap_or("")
            .trim()
            .to_lowercase()
            .as_str(),
    )
}
fn dry(e: &GameEvent) -> bool {
    catalog::dry_biome(e.world.biome.as_deref().unwrap_or(""))
}
pub(super) fn weather_scene(from: &str, to: &str, cold: bool, dry: bool) -> Option<&'static str> {
    if to == "clear" && ["rain", "thunder"].contains(&from) {
        return Some("clear_after_bad_weather");
    }
    Some(match (to, cold, dry, from) {
        ("rain", _, true, "thunder") => "overcast_after_thunder",
        ("rain", _, true, _) => "overcast_started",
        ("thunder", _, true, "rain") => "dry_thunder_after_overcast",
        ("thunder", _, true, _) => "dry_thunder_started",
        ("rain", true, _, "thunder") => "snow_after_thunder",
        ("rain", true, _, _) => "snow_started",
        ("rain", _, _, "thunder") => "rain_after_thunder",
        ("rain", _, _, _) => "rain_started",
        ("thunder", true, _, "rain") => "blizzard_after_snow",
        ("thunder", true, _, _) => "blizzard_started",
        ("thunder", _, _, "rain") => "thunder_after_rain",
        ("thunder", _, _, _) => "thunder_started",
        _ => return None,
    })
}
impl Ambient {
    pub(super) fn weather_action(
        &mut self,
        e: &GameEvent,
        f: &AmbientFocus,
        s: &Settings,
    ) -> Option<Speech> {
        if f.boss_presence || f.ominous_presence {
            self.weather_pending = None;
            return None;
        }
        let (from, to) = self.weather_pending.take()?;
        let scene = if e.world.sky_visible == Some(true) {
            weather_scene(&from, &to, cold(e), dry(e))
        } else if to == "rain"
            && e.world
                .rain_sound_recent_ms
                .is_some_and(|age| age <= s.ms("weather_sound_recent_ms") as i64)
        {
            Some("rain_suspected")
        } else if to == "thunder"
            && e.world
                .thunder_sound_recent_ms
                .is_some_and(|age| age <= s.ms("weather_sound_recent_ms") as i64)
        {
            Some("thunder_suspected")
        } else {
            None
        }?;
        let mut details = common_details(e, s);
        details.as_object_mut()?.extend(json!({"scene":scene,"weather_from":from,"weather_to":to,"cold_biome":cold(e),"dry_biome":dry(e),"__ambient_guard":{"weather":to}}).as_object()?.clone());
        Some(leaf(
            "weather_transition",
            catalog::general("weather_transition", scene),
            details,
            0.66,
        ))
    }
    pub(super) fn eye_action(&mut self, e: &GameEvent, now: u64, s: &Settings) -> Option<Speech> {
        if !e
            .world
            .ender_eye_launch_recent_ms
            .is_some_and(|age| age <= s.ms("ender_eye_recent_ms") as i64)
            || !elapsed(now, self.last_eye, s.ms("ender_eye_comment_cooldown_ms"))
        {
            return None;
        }
        self.last_eye = Some(now);
        let lines = catalog::exploration()["ender_eye"]["throw"]["lines"].as_array()?;
        let seed = format!("ender_eye:{}", e.sequence.unwrap_or(0));
        let i = seed.chars().map(|c| c as usize).sum::<usize>() % lines.len();
        let mut details = common_details(e, s);
        details["reference_lines"] = Value::Array(lines.clone());
        Some(leaf(
            "ender_eye_throw",
            lines[i].as_str()?.into(),
            details,
            0.4,
        ))
    }
    pub(super) fn firefly_actions(
        &mut self,
        e: &GameEvent,
        f: &AmbientFocus,
    ) -> Option<Vec<Speech>> {
        if phase(e) != Some("night")
            || self.firefly_reacted
            || e.world.nearby_firefly_bush_count.unwrap_or(0) <= 0
            || f.submerged
            || f.safe_zone_with_door
        {
            return None;
        }
        self.firefly_reacted = true;
        let mut cue = Speech::new("firefly_cue", "ヒイ！");
        cue.cue_id = Some("suppressed_gasp");
        cue.scope = Scope::Safe;
        let mut text = Speech::new("firefly", "なんや。ほたるかいな……驚いて損したわ……。");
        text.scope = Scope::Safe;
        Some(vec![cue, text])
    }
}
