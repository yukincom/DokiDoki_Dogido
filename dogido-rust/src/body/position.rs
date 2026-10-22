//! 受理済みの同一sampleから双方の位置関係を計算し、現在観測と変化比較へ渡す。
//! 返却値は結果だけ。座標・角度・式・識別子をLLMの材料へ持ち込まない。
use super::{contracts::Sample, geometry};
use serde_json::{Value, json};

fn horizontal(dx: f64, dz: f64, forward: f64, right: f64) -> &'static str {
    if dx == 0.0 && dz == 0.0 {
        return "same_horizontal_position";
    }
    let angle = right.atan2(forward).to_degrees().rem_euclid(360.0);
    const DIRECTIONS: [&str; 8] = [
        "front",
        "front_right",
        "right",
        "back_right",
        "back",
        "back_left",
        "left",
        "front_left",
    ];
    DIRECTIONS[(((angle + 22.5) / 45.0).floor() as usize) % 8]
}
fn vertical(dy: f64) -> &'static str {
    if dy > 0.0 {
        "above"
    } else if dy < 0.0 {
        "below"
    } else {
        "same"
    }
}
pub fn unavailable() -> Value {
    json!({"position_status":"unavailable","relative_positions":[],"distance_blocks":null})
}
pub fn calculate(sample: &Sample) -> Value {
    let r = geometry::relation(sample);
    let inverse = [-r.delta[0], -r.delta[1], -r.delta[2]];
    let (forward, right) = geometry::components(inverse, sample.player_pose.yaw);
    json!({"position_status":"available", "relative_positions":[
        {"observer":"dogido", "target":"player", "horizontal":horizontal(r.delta[0], r.delta[2], r.forward, r.right), "vertical":vertical(r.delta[1])},
        {"observer":"player", "target":"dogido", "horizontal":horizontal(inverse[0], inverse[2], forward, right), "vertical":vertical(inverse[1])}
    ], "distance_blocks":(r.distance * 10.0).round() / 10.0})
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::body::contracts::{Observation, Position};
    fn sample() -> Sample {
        let o: Observation =
            serde_json::from_str(include_str!("../../fixtures/body-observation-v3.json")).unwrap();
        let Observation::Observed { sample, .. } = o else {
            panic!()
        };
        *sample
    }
    #[test]
    fn both_viewpoints_translate_rotate_and_keep_vertical_and_distance() {
        for base in [-1000.0, 0.0, 1000.0] {
            let mut s = sample();
            s.self_pose.position = Position {
                x: base,
                y: 64.0,
                z: base,
            };
            s.player_pose.position = Position {
                x: base,
                y: 64.0,
                z: base + 4.0,
            };
            s.self_pose.yaw = 0.0;
            s.self_pose.view_yaw = 90.0;
            s.player_pose.yaw = 90.0;
            let a = calculate(&s);
            assert_eq!(a["relative_positions"][0]["horizontal"], "front");
            assert_eq!(a["relative_positions"][1]["horizontal"], "right");
            assert_eq!(a["distance_blocks"], 4.0);
            s.self_pose.yaw = 90.0;
            assert_eq!(calculate(&s)["relative_positions"][0]["horizontal"], "left");
            s.player_pose.position = s.self_pose.position;
            s.player_pose.position.y += 3.0;
            let a = calculate(&s);
            assert_eq!(
                a["relative_positions"][0]["horizontal"],
                "same_horizontal_position"
            );
            assert_eq!(a["relative_positions"][0]["vertical"], "above");
            assert_eq!(a["relative_positions"][1]["vertical"], "below");
            assert_eq!(a["distance_blocks"], 3.0);
        }
    }
    #[test]
    fn eight_sectors_and_boundaries() {
        for (i, expected) in [
            "front",
            "front_right",
            "right",
            "back_right",
            "back",
            "back_left",
            "left",
            "front_left",
        ]
        .into_iter()
        .enumerate()
        {
            for offset in [-22.499, 0.0, 22.499] {
                let angle = (i as f64 * 45.0 + offset).to_radians();
                assert_eq!(horizontal(1.0, 1.0, angle.cos(), angle.sin()), expected);
            }
        }
        assert_eq!(horizontal(0.0, -0.0, 0.0, -0.0), "same_horizontal_position");
        assert_eq!(unavailable()["distance_blocks"], Value::Null);
    }
    #[test]
    fn recorded_experiment_results_match_world_geometry_at_multiple_translations() {
        let cases: Value =
            serde_json::from_str(include_str!("../../fixtures/body-position-results.json"))
                .unwrap();
        for row in cases["cases"].as_array().unwrap() {
            for base in [-12345.0, 0.0, 12345.0] {
                let mut s = sample();
                let p = &row["egocentric_input"]["player"];
                s.self_pose.position = Position {
                    x: base,
                    y: 64.0,
                    z: base,
                };
                s.self_pose.yaw = 0.0;
                s.player_pose.position = Position {
                    x: base - p["position"]["x"].as_f64().unwrap(),
                    y: 64.0 + p["position"]["y"].as_f64().unwrap(),
                    z: base + p["position"]["z"].as_f64().unwrap(),
                };
                s.player_pose.yaw = (p["yaw"].as_f64().unwrap() + 180.0).rem_euclid(360.0) - 180.0;
                assert_eq!(calculate(&s), row["expected"], "{} at {base}", row["id"]);
            }
        }
    }
}
