//! 川柳の構造化要求に入ったdetailsを、モデルへ渡す日本語の材料・出典・検査結果の文章へ整形する。
//! materialsは観測と会話、sceneは共有済みの見どころ、constraintsは読みと好み、source_atomsは行の根拠を表す。
//! 再生成と共同編集には不合格理由や既存文脈を別ブロックで付け、親moduleがテンプレートと組み合わせる。
//! この層の入力は準備・検査側の結果であり、通信、音数測定、採否の判定はここでは行わない。
use super::{ASSETS, asset};
use crate::text_format::spaced_json;
use crate::text_format::{self, ContainerFormat::SpacedJson};
use serde_json::{Value, json};

pub(super) use crate::compat::json_truthy as truth;
pub(super) fn array(v: &Value) -> &[Value] {
    v.as_array().map(Vec::as_slice).unwrap_or(&[])
}
pub(super) use crate::compat::is_dogido_whitespace as space;
pub(super) fn clean(v: &Value) -> String {
    if truth(v) {
        text_format::value_text(v, SpacedJson)
            .trim_matches(space)
            .into()
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
        .map(|value| text_format::value_text(value, SpacedJson))
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

/// 選択品が実際の手持ちか所持品から選んだ一品かを文中に残し、ほかの所持品を重複なく最大4件添える。
/// pocketから選んだ品を「手に持っている」と書かないため、poem_item_sourceを使って主語を分ける。
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
        let item = text_format::value_text(item, SpacedJson)
            .trim_matches(space)
            .to_owned();
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
/// 場所→周辺物→手もと→Mob→空→会話→語彙・カタログの順に、共有材料の説明を組む。
/// 構造物名があれば一般のバイオーム説明より優先し、biome/sky_context_visibleがfalseの欄は省く。
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
        let biome = text_format::value_text(d.get("biome").unwrap_or(&json!("不明")), SpacedJson);
        let mut place = format!("いまの景色: {biome}");
        if truth(&d["biome_group"]) {
            place.push_str(&format!(
                "（{}）",
                text_format::value_text(&d["biome_group"], SpacedJson)
            ));
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
        let weather = text_format::value_text(
            d.get("weather_label")
                .or_else(|| d.get("weather"))
                .unwrap_or(&json!("不明")),
            SpacedJson,
        );
        let time = text_format::value_text(
            d.get("time_label")
                .or_else(|| d.get("time_phase"))
                .unwrap_or(&json!("不明")),
            SpacedJson,
        );
        chunks.push(format!("空と時間: {weather} / {time}"));
        let context = clean(&d["weather_context"]);
        if !context.is_empty() {
            chunks.push(format!("コードで確定した現在地の気象: {context}"));
        }
    }
    // 直前の雑談は80字の要約と最大3語に留め、プレイヤーとの会話に由来する軽い材料と明示する。
    // 観測した周辺物の列とは分けて、本人の話を現在世界の在否証拠へ変えないようにする。
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
            .map(|v| {
                cut(
                    text_format::value_text(v, SpacedJson).trim_matches(space),
                    16,
                )
            })
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
            .map(|v| format!("- {}", text_format::value_text(v, SpacedJson)))
            .collect::<Vec<_>>()
            .join("\n");
        if !lines.is_empty() {
            chunks.push(format!("{label}:\n{lines}"));
        }
    }
    chunks.join("\n")
}
/// sceneに保持された発話文・motif・焦点を表示する。発話文がないsceneは「なし」とする。
/// 本当に配送済みかの確定は呼出側が行い、ここでは渡された記録の文章化に徹する。
pub(super) fn scene(d: &Value) -> String {
    let scene = &d["scene"];
    if !scene.is_object() || !truth(&scene["spoken_text"]) {
        return "なし".into();
    }
    format!(
        "発話済みの見どころ: {}\nモチーフ: {}\n焦点: {}",
        text_format::value_text(&scene["spoken_text"], SpacedJson),
        or_none(join(&scene["motifs"], "、")),
        or_none(join(&scene["focus"], "、"))
    )
}
/// 道具等の許容読み・避ける語と、最大3件のプレイヤーの好みを別の説明へ整える。
/// 参考lessonを読みの禁止語へ昇格させず、生成器の検査と同じ欄の区別をモデルにも伝える。
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
        .map(|v| format!("- {}", text_format::value_text(v, SpacedJson)))
        .collect();
    if !lessons.is_empty() {
        parts.push(format!(
            "プレイヤーの最近の好み（軽く参考）:\n{}",
            lessons.join("\n")
        ));
    }
    parts.join("\n")
}
/// 制約があるときだけ見出しと改行を付ける。空の「読みのメモ」をテンプレートへ残さない。
pub(super) fn constraint_section(d: &Value) -> String {
    let text = constraints(d);
    if text.is_empty() {
        text
    } else {
        format!("\n読みのメモ:\n{text}\n")
    }
}
/// 出典atomを、本文・主張種別・許容範囲・基底出典が追える一覧へ変える。
/// grounding_atom_numbersがある場合はatom_idとbasisを同じ番号表へ置換し、照合応答の参照先を揃える。
/// IDか本文が欠けた項目は省き、使える出典がなければ「なし」を返す。
pub(super) fn source_atoms(d: &Value) -> String {
    let numbers = d["grounding_atom_numbers"].as_object();
    let numbered = |id: &str| {
        numbers
            .and_then(|n| n.get(id))
            .map(|value| text_format::value_text(value, SpacedJson))
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
/// 行の根拠照合へ渡す見どころは、sceneの発話文→interpretation→ironyの説明の順で最初の非空値を使う。
/// 最大1200字に切り、いずれもなければ「なし」を返す。
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
/// 呼出側が作ったworkshop_contextを、空でない場合だけJSONのまま共有文脈欄へ載せる。
pub(super) fn context(d: &Value) -> String {
    let c = &d["workshop_context"];
    if c.as_object().is_some_and(|c| !c.is_empty()) {
        format!("{}{}\n", asset("context_prefix"), spaced_json(c))
    } else {
        String::new()
    }
}
/// 一行分の再生成指示を、固定行または不合格理由付きの書き直し対象として表示する。
/// 四つの音数値が整数で揃う場合だけ数値付きで説明し、ここで再計測や欠損値の補完はしない。
pub(super) fn regeneration_line(row: &Value) -> String {
    let index = text_format::value_text(&row["line_index"], SpacedJson);
    let text = text_format::value_text(&row["text"], SpacedJson);
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
        let status = match text_format::value_text(&row["meter_status"], SpacedJson).as_str() {
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
/// 前回編集の全体・行別の失敗理由、照合モデルの指摘、繰り返せない案を次の編集要求へまとめる。
/// モデルの指摘は事実や命令とは区別して最大240字で載せ、未知の理由コードもコード自体は残す。
/// edit_retry_feedbackがobjectでなければ、再試行用の説明は付けない。
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
            lines.push(format!(
                "- 行{}: {rendered}",
                text_format::value_text(&row["line_index"], SpacedJson)
            ));
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
            (!text.is_empty()).then(|| {
                format!(
                    "- 行{}: {text}",
                    text_format::value_text(&r["line_index"], SpacedJson)
                )
            })
        })
        .collect();
    if !rejected.is_empty() {
        lines.push("【繰り返してはいけない不合格案】".into());
        lines.extend(rejected);
    }
    lines.push("失敗理由だけを直し、前の案の表記替えではない別案を返す。".into());
    lines.join("\n") + "\n\n"
}
