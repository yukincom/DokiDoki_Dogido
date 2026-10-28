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
