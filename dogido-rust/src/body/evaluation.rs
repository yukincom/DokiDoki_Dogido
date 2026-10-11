//! 位置理解の検査用正解。会話材料には使わず、実測とモデルの返答を評価するときだけ呼ぶ。
use super::contracts::Sample;

#[derive(Debug, PartialEq)]
pub struct Relation {
    pub delta: [f64; 3],
    pub distance: f64,
    pub forward: f64,
    pub right: f64,
}
/// 足元同士の差・距離はblock。身体yawだけで前後左右へ回し、playerカメラの向きには依存しない。
pub fn relation(s: &Sample) -> Relation {
    let a = s.self_pose.position;
    let b = s.player_pose.position;
    let delta = [b.x - a.x, b.y - a.y, b.z - a.z];
    let yaw = s.self_pose.yaw.to_radians();
    Relation {
        delta,
        distance: delta.iter().map(|v| v * v).sum::<f64>().sqrt(),
        forward: -delta[0] * yaw.sin() + delta[2] * yaw.cos(),
        right: -delta[0] * yaw.cos() - delta[2] * yaw.sin(),
    }
}
