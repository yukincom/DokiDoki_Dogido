//! 計算済みの位置関係と視認状態を、主語・基準を明記したモデル向けの事実へ変換する。
//! Context::for_modelのコピーだけを扱い、位置計算・内部比較・鮮度・状態更新は変更しない。
use serde_json::{Value, json};

/// 各本人の向きで計算済みの方向と足元の上下を表示する。相手視点の反転や座標計算はしない。
/// 距離は計算側が丸め前の実距離から切り捨てた、会話用の整数ブロック数を表示する。
pub(super) fn position(relation: &Value) -> Value {
    if relation["position_status"] != "available" {
        return json!({"position_status":"unavailable",
            "facts":["ドギドとプレイヤーの現在の位置関係は分からない。"]});
    }
    // 既にモデル用のコピーなら、その事実を重ねたり再解釈したりしない。
    if relation["facts"].is_array() {
        return relation.clone();
    }
    let mut facts = Vec::new();
    if let Some(rows) = relation["relative_positions"].as_array() {
        facts.extend(rows.iter().filter_map(relative_position));
    }
    if let Some(distance) = relation["distance_blocks_floor"].as_u64() {
        facts.push(format!(
            "ドギドとプレイヤーの足元間の直線距離は{distance}ブロック。"
        ));
    }
    json!({"position_status":"available","facts":facts})
}

fn relative_position(row: &Value) -> Option<String> {
    let (observer, target) = match (row["observer"].as_str()?, row["target"].as_str()?) {
        ("dogido", "player") => ("ドギド", "プレイヤー"),
        ("player", "dogido") => ("プレイヤー", "ドギド"),
        _ => return None,
    };
    let direction = match row["horizontal"].as_str()? {
        "front" => "前",
        "front_right" => "右前",
        "right" => "右",
        "back_right" => "右後ろ",
        "back" => "後ろ",
        "back_left" => "左後ろ",
        "left" => "左",
        "front_left" => "左前",
        "same_horizontal_position" => "同じ水平位置",
        _ => return None,
    };
    let horizontal = if direction == "同じ水平位置" {
        format!("{target}の足元は{observer}の足元と同じ水平位置にある。")
    } else {
        format!("{target}は{observer}の身体の向きを基準に{direction}にいる。")
    };
    let height = match row["vertical"].as_str()? {
        "above" => "より上",
        "same" => "と同じ高さ",
        "below" => "より下",
        _ => return None,
    };
    Some(format!(
        "{horizontal}{target}の足元は{observer}の足元{height}にある。"
    ))
}

/// 位置の取得可否とは独立に、ドギドからプレイヤーを視認できるかを表示する。
/// 視認理由と計測条件は受理済みの値を保持し、プレイヤーからの視認は推測しない。
pub(super) fn vision(vision: &Value) -> Value {
    let mut result = vision.clone();
    let Some(fields) = result.as_object_mut() else {
        return result;
    };
    let fact = match vision["player_visibility"].as_str() {
        Some("visible") => "ドギドからプレイヤーの姿が見えている。",
        Some("not_visible") => "ドギドからプレイヤーの姿は見えていない。",
        _ => "ドギドからプレイヤーの姿が見えるかは分からない。",
    };
    fields.insert("fact".into(), fact.into());
    result
}

/// 直近の移動を一行にする。内部座標は載せず、北=-Z、東=+XのMinecraft方位を使う。
pub(super) fn motion(kind: &str, before: &Value, now: &Value) -> Value {
    let who = if kind.starts_with("body_self_") {
        "ドギド"
    } else {
        "プレイヤー"
    };
    let delta = |key: &str| Some(now[key].as_f64()? - before[key].as_f64()?);
    let mut parts = Vec::new();
    if kind.ends_with("_position") {
        if let (Some(dx), Some(dy), Some(dz)) = (delta("x"), delta("y"), delta("z")) {
            let flat = dx.hypot(dz);
            if flat >= 0.1 {
                let i = ((dx.atan2(-dz).to_degrees().rem_euclid(360.) + 22.5) / 45.).floor()
                    as usize
                    % 8;
                parts.push(format!(
                    "{}へ約{flat:.1}ブロック",
                    ["北", "北東", "東", "南東", "南", "南西", "西", "北西"][i]
                ));
            }
            if dy.abs() >= 0.1 {
                parts.push(format!(
                    "{}へ約{:.1}ブロック",
                    if dy > 0. { "上" } else { "下" },
                    dy.abs()
                ));
            }
        }
    } else {
        let yaw = if who == "ドギド" {
            "view_yaw"
        } else {
            "yaw"
        };
        let pitch = if who == "ドギド" {
            "view_pitch"
        } else {
            "pitch"
        };
        if let Some(y) = delta(yaw) {
            let y = (y + 180.).rem_euclid(360.) - 180.;
            if y.abs() >= 1. {
                parts.push(format!(
                    "{}へ約{:.0}度",
                    if y > 0. { "右" } else { "左" },
                    y.abs()
                ));
            }
        }
        if let Some(p) = delta(pitch)
            && p.abs() >= 1.
        {
            parts.push(format!(
                "{}へ約{:.0}度",
                if p > 0. { "下" } else { "上" },
                p.abs()
            ));
        }
    }
    // 身体だけが旋回して眼の向きを保った場合、視線の変化とは言わない。
    if parts.is_empty()
        && kind == "body_self_orientation"
        && let Some(y) = delta("yaw")
    {
        let y = (y + 180.).rem_euclid(360.) - 180.;
        if y.abs() >= 1. {
            return json!({"fact":format!("ドギドが{}へ約{:.0}度、身体の向きを変えた。",if y>0. {"右"} else {"左"},y.abs())});
        }
    }
    if parts.is_empty() {
        return Value::Null;
    }
    let verb = if kind.ends_with("_position") {
        "動いた"
    } else {
        "視線を変えた"
    };
    json!({"fact":format!("{who}が{}{verb}。",parts.join("、"))})
}

#[cfg(test)]
mod motion_tests {
    use super::*;
    // 見込み: 軌跡全体ではなく測れた一歩だけを表す。合格: 北東1.4・上1と、±180度をまたいだ右2度を正しい主語で渡す。
    // 範囲: 投影の数値と向きの検査。実際の発話や描画は対象外。
    #[test]
    fn observed_steps_hide_coordinates_and_take_the_short_turn() {
        assert_eq!(
            motion(
                "body_player_position",
                &json!({"x":1,"y":64,"z":0}),
                &json!({"x":2,"y":65,"z":-1})
            )["fact"],
            "プレイヤーが北東へ約1.4ブロック、上へ約1.0ブロック動いた。"
        );
        assert_eq!(
            motion(
                "body_self_orientation",
                &json!({"view_yaw":179,"view_pitch":2}),
                &json!({"view_yaw":-179,"view_pitch":2})
            )["fact"],
            "ドギドが右へ約2度視線を変えた。"
        );
    }
}
