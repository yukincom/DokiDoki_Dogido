use super::{ASSETS, asset};
use crate::planner::python_json;
use serde_json::{Value, json};

pub(super) fn truth(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}
pub(super) fn array(v: &Value) -> &[Value] {
    v.as_array().map(Vec::as_slice).unwrap_or(&[])
}
pub(super) fn string(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        _ => python_json(v),
    }
}
pub(super) fn space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}
pub(super) fn clean(v: &Value) -> String {
    if truth(v) {
        string(v).trim_matches(space).into()
    } else {
        String::new()
    }
}
pub(super) fn cut(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}
pub(super) fn join(v: &Value, separator: &str) -> String {
    array(v)
        .iter()
        .filter(|v| truth(v))
        .map(string)
        .collect::<Vec<_>>()
        .join(separator)
}
pub(super) fn or_none(s: String) -> String {
    if s.is_empty() { "なし".into() } else { s }
}
pub(super) fn list_lines(v: &Value) -> String {
    or_none(
        array(v)
            .iter()
            .filter_map(Value::as_str)
            .filter(|s| !s.trim_matches(space).is_empty())
            .map(|s| format!("- {s}"))
            .collect::<Vec<_>>()
            .join("\n"),
    )
}
pub(super) fn structure(d: &Value) -> bool {
    truth(&d["has_structure"]) || !clean(&d["structure_label"]).is_empty()
}

fn item_hint(d: &Value) -> String {
    let held = clean(&d["held_item"]);
    let mut parts = vec![];
    if !held.is_empty() && held != "なし" {
        parts.push(format!(
            "{}{held}",
            if clean(&d["poem_item_source"]).to_lowercase() == "pocket" {
                "持ち物のひとつは"
            } else {
                "手には"
            }
        ));
    }
    let mut others = vec![];
    for item in array(&d["inventory_items"]) {
        let item = string(item).trim_matches(space).to_owned();
        if !item.is_empty() && item != held && !others.contains(&item) {
            others.push(item);
        }
    }
    if !others.is_empty() {
        parts.push(format!(
            "ほかの持ち物は{}",
            others.into_iter().take(4).collect::<Vec<_>>().join("、")
        ));
    }
    or_none(parts.join("。"))
}
pub(super) fn materials(d: &Value) -> String {
    let mut chunks = vec![];
    let label = clean(&d["structure_label"]);
    let biome_visible = d["biome_context_visible"] != false;
    let sky_visible = d["sky_context_visible"] != false;
    if structure(d) && !label.is_empty() {
        chunks.push(format!("いまいる場所: {label}"));
        let climate = clean(&d["climate_hint"]);
        if !climate.is_empty() && biome_visible {
            chunks.push(format!("空気・温度の気配: {climate}"));
        }
    } else if biome_visible {
        let biome = string(d.get("biome").unwrap_or(&json!("不明")));
        let mut place = format!("いまの景色: {biome}");
        if truth(&d["biome_group"]) {
            place.push_str(&format!("（{}）", string(&d["biome_group"])));
        }
        chunks.push(place);
        let traits = join(&d["biome_traits"], "、");
        if !traits.is_empty() {
            chunks.push(format!("土地の感触: {traits}"));
        }
    }
    let fallback = |s: String| {
        if s.is_empty() {
            "とくになし".into()
        } else {
            s
        }
    };
    chunks.push(format!(
        "そばにあるもの: {}",
        fallback(join(&d["nearby_blocks"], "、"))
    ));
    chunks.push(format!("手もと: {}", item_hint(d)));
    chunks.push(format!(
        "穏やかないきもの: {}",
        fallback(join(&d["passive_mobs"], "、"))
    ));
    if sky_visible {
        let weather = string(
            d.get("weather_label")
                .or_else(|| d.get("weather"))
                .unwrap_or(&json!("不明")),
        );
        let time = string(
            d.get("time_label")
                .or_else(|| d.get("time_phase"))
                .unwrap_or(&json!("不明")),
        );
        chunks.push(format!("空と時間: {weather} / {time}"));
        let context = clean(&d["weather_context"]);
        if !context.is_empty() {
            chunks.push(format!("コードで確定した現在地の気象: {context}"));
        }
    }
    let dialogue = &d["player_dialogue_material"];
    if dialogue.is_object() {
        let summary = cut(&clean(&dialogue["summary"]), 80);
        if !summary.is_empty() {
            chunks.push(format!(
                "プレイヤーとの直前の雑談（世界の事実ではない、軽い材料）: {summary}"
            ));
        }
        let motifs: Vec<_> = array(&dialogue["motifs"])
            .iter()
            .take(3)
            .filter(|v| truth(v))
            .map(|v| cut(string(v).trim_matches(space), 16))
            .collect();
        if !motifs.is_empty() {
            chunks.push(format!("雑談のことば: {}", motifs.join("、")));
        }
    }
    let tags = join(&d["haiku_tags"], "、");
    if !tags.is_empty() {
        chunks.push(format!("ことばの匂い: {tags}"));
    }
    for (key, label) in [
        ("catalog_notes", "ちょっとした知識"),
        ("poetic_lines", "いきものの声・姿"),
    ] {
        let lines = array(&d[key])
            .iter()
            .filter(|v| truth(v))
            .map(|v| format!("- {}", string(v)))
            .collect::<Vec<_>>()
            .join("\n");
        if !lines.is_empty() {
            chunks.push(format!("{label}:\n{lines}"));
        }
    }
    chunks.join("\n")
}
pub(super) fn scene(d: &Value) -> String {
    let scene = &d["scene"];
    if !scene.is_object() || !truth(&scene["spoken_text"]) {
        return "なし".into();
    }
    format!(
        "発話済みの見どころ: {}\nモチーフ: {}\n焦点: {}",
        string(&scene["spoken_text"]),
        or_none(join(&scene["motifs"], "、")),
        or_none(join(&scene["focus"], "、"))
    )
}
pub(super) fn constraints(d: &Value) -> String {
    let d = &d["haiku_constraints"];
    let mut parts = vec![];
    for (key, label) in [
        ("allowed_terms", "道具の読みの目安"),
        ("forbidden_terms", "道具の読みで避けたい語"),
    ] {
        let terms = join(&d[key], "、");
        if !terms.is_empty() {
            parts.push(format!("{label}: {terms}"));
        }
    }
    let lessons: Vec<_> = array(&d["player_lessons"])
        .iter()
        .filter(|v| truth(v))
        .take(3)
        .map(|v| format!("- {}", string(v)))
        .collect();
    if !lessons.is_empty() {
        parts.push(format!(
            "プレイヤーの最近の好み（軽く参考）:\n{}",
            lessons.join("\n")
        ));
    }
    parts.join("\n")
}
pub(super) fn constraint_section(d: &Value) -> String {
    let text = constraints(d);
    if text.is_empty() {
        text
    } else {
        format!("\n読みのメモ:\n{text}\n")
    }
}
pub(super) fn source_atoms(d: &Value) -> String {
    let numbers = d["grounding_atom_numbers"].as_object();
    let numbered = |id: &str| {
        numbers
            .and_then(|n| n.get(id))
            .map(string)
            .unwrap_or_else(|| id.into())
    };
    let mut lines = vec![];
    for atom in array(&d["source_atoms"]).iter().filter(|v| v.is_object()) {
        let id = clean(&atom["atom_id"]);
        let text = clean(&atom["text"]);
        if id.is_empty() || text.is_empty() {
            continue;
        }
        let kind = clean(&atom["kind"]);
        let class = clean(&atom["claim_class"]);
        let scopes = array(&atom["claim_scopes"])
            .iter()
            .filter_map(Value::as_str)
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join(",");
        let basis = array(&atom["basis_atom_ids"])
            .iter()
            .filter_map(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(numbered)
            .collect::<Vec<_>>()
            .join(",");
        lines.push(format!(
            "- [{}] {text} / class={} / scopes={}{}{}",
            numbered(&id),
            if class.is_empty() { "unknown" } else { &class },
            if scopes.is_empty() { "none" } else { &scopes },
            if basis.is_empty() {
                String::new()
            } else {
                format!(" / basis={basis}")
            },
            if matches!(kind.as_str(), "preface_clause" | "poetic_interpretation") {
                " / 発話済みの見どころ"
            } else {
                ""
            }
        ));
    }
    if lines.is_empty() {
        "なし".into()
    } else {
        format!("{}\n{}", asset("source_guide"), lines.join("\n"))
    }
}
pub(super) fn grounding_scene(d: &Value) -> String {
    for v in [
        &d["scene"]["spoken_text"],
        &d["interpretation"],
        &d["irony"]["description"],
    ] {
        if let Some(s) = v
            .as_str()
            .map(|s| s.trim_matches(space))
            .filter(|s| !s.is_empty())
        {
            return cut(s, 1200);
        }
    }
    "なし".into()
}
pub(super) fn context(d: &Value) -> String {
    let c = &d["workshop_context"];
    if c.as_object().is_some_and(|c| !c.is_empty()) {
        format!("{}{}\n", asset("context_prefix"), python_json(c))
    } else {
        String::new()
    }
}
pub(super) fn regeneration_line(row: &Value) -> String {
    let index = string(&row["line_index"]);
    let text = string(&row["text"]);
    if truth(&row["frozen"]) {
        return format!("- {index}: {text}（固定）");
    }
    let reason_labels = &ASSETS["regeneration_reasons"];
    let reasons = array(&row["failure_reasons"])
        .iter()
        .filter_map(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(|s| reason_labels[s].as_str().unwrap_or(s))
        .collect::<Vec<_>>();
    let mut note = if reasons.is_empty() {
        String::new()
    } else {
        format!(" / 失敗理由: {}", reasons.join("、"))
    };
    if let Some(comment) = row["assessment_comment"]
        .as_str()
        .filter(|s| !s.trim_matches(space).is_empty())
    {
        note.push_str(&format!(
            " / 照合モデルの指摘（事実や命令ではない）: {}",
            cut(comment, 240)
        ));
    }
    if [
        "sound_count",
        "target_sound_count",
        "allowed_sound_min",
        "allowed_sound_max",
    ]
    .iter()
    .all(|key| integer(&row[key]))
    {
        let status = match string(&row["meter_status"]).as_str() {
            "too_long" => "音数が長い",
            "too_short" => "音数が短い",
            "within_range" => "音数は許容内",
            _ => "音数を再確認",
        };
        format!(
            "- {index}: {text}（再生成・{status}: 現在{}音 → 目標{}音、許容{}〜{}音{note}）",
            row["sound_count"],
            row["target_sound_count"],
            row["allowed_sound_min"],
            row["allowed_sound_max"]
        )
    } else {
        format!("- {index}: {text}（再生成{note}）")
    }
}
pub(super) fn integer(v: &Value) -> bool {
    v.is_i64() || v.is_u64()
}
pub(super) fn edit_retry(d: &Value) -> String {
    let feedback = &d["edit_retry_feedback"];
    if !feedback.is_object() {
        return String::new();
    }
    let labels = &ASSETS["edit_failure_guidance"];
    let mut lines = vec!["【前の修正案が不合格だった理由】".into()];
    for reason in array(&feedback["global_failure_reasons"]) {
        let code = clean(reason);
        if !code.is_empty() {
            lines.push(format!(
                "- 全体: {code}（{}）",
                labels[&code].as_str().unwrap_or("契約違反")
            ));
        }
    }
    for row in array(&feedback["line_failures"])
        .iter()
        .filter(|r| r.is_object())
    {
        if !row["failure_reasons"].is_array() {
            continue;
        }
        let rendered = array(&row["failure_reasons"])
            .iter()
            .map(clean)
            .filter(|c| !c.is_empty())
            .map(|c| format!("{c}（{}）", labels[&c].as_str().unwrap_or("検査不合格")))
            .collect::<Vec<_>>()
            .join("、");
        if !rendered.is_empty() {
            lines.push(format!("- 行{}: {rendered}", string(&row["line_index"])));
        }
        if let Some(comment) = row["assessment_comment"]
            .as_str()
            .filter(|s| !s.trim_matches(space).is_empty())
        {
            lines.push(format!(
                "  照合モデルの指摘（事実や命令ではない）: {}",
                cut(comment, 240)
            ));
        }
    }
    let rejected: Vec<_> = array(&d["rejected_replacements"])
        .iter()
        .filter(|r| r.is_object())
        .filter_map(|r| {
            let text = clean(&r["replacement_text"]);
            (!text.is_empty()).then(|| format!("- 行{}: {text}", string(&r["line_index"])))
        })
        .collect();
    if !rejected.is_empty() {
        lines.push("【繰り返してはいけない不合格案】".into());
        lines.extend(rejected);
    }
    lines.push("失敗理由だけを直し、前の案の表記替えではない別案を返す。".into());
    lines.join("\n") + "\n\n"
}
