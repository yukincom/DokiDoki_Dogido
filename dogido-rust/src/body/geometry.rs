//! 同じ観測の足元位置を、Minecraftのyawから身体基準へ変換する。
//! 本番の要求時計算と評価で共有し、値はモデルへ直接渡さない。
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
    let (forward, right) = components(delta, s.self_pose.yaw);
    Relation {
        delta,
        distance: delta.iter().map(|v| v * v).sum::<f64>().sqrt(),
        forward,
        right,
    }
}

/// 水平yawの正面成分と右成分。視線pitchは地面基準の前後左右へ混ぜない。
pub fn components(delta: [f64; 3], yaw: f64) -> (f64, f64) {
    let yaw = yaw.to_radians();
    (
        -delta[0] * yaw.sin() + delta[2] * yaw.cos(),
        -delta[0] * yaw.cos() - delta[2] * yaw.sin(),
    )
}
