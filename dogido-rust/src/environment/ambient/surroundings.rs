use super::*;
use crate::events::{VehicleState, VehicleStateActivity as Activity};
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
impl Ambient {
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
}
