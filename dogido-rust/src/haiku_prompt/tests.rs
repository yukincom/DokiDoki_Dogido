use super::*;
use serde_json::Map;
#[test]
fn unchanged_prepare_kinds_match_canonical_helper_messages() {
    let fixtures: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    for (index, row) in fixtures.as_array().unwrap().iter().enumerate() {
        let request: StructuredRequest = serde_json::from_value(row["request"].clone()).unwrap();
        if matches!(
            request.kind.as_str(),
            "haiku_line_grounding" | "haiku_draft" | "haiku_line_regeneration" | "haiku_scene"
        ) {
            continue;
        }
        let actual = messages(&request).unwrap();
        assert_eq!(
            json!(actual),
            row["messages"],
            "case {index} {} {}",
            row["name"],
            request.kind
        );
    }
}
#[test]
fn unknown_kind_is_rejected_without_io() {
    let request = StructuredRequest {
        kind: "player_chat".into(),
        details: Map::new(),
        route: "chat".into(),
        temperature: 0.0,
        max_tokens: Some(512),
        fallback_value: json!({}),
    };
    assert!(messages(&request).is_err());
}

// 見込み: 元材料と前置きを別欄に置けば、前置きや未選択の観測を材料へ混ぜずに渡せる。
// 合格: 初案と再生成の両方で選択材料だけが出て、方針は一度だけ別欄に出る。モデルは呼ばない。
#[test]
fn selected_material_pool_and_preface_have_separate_prompt_sections() {
    for kind in ["haiku_draft", "haiku_line_regeneration"] {
        let request=StructuredRequest {kind:kind.into(), details:json!({
            "source_atoms":[{"atom_id":"SECRET-ID","text":"未選択の材料"}],
            "nearby_blocks":["未選択の材料"],"feature_candidates":["未選択の材料"],
            "background_companions":[{"label":"未選択の材料"}],
            "available_poem_materials":[{"facts":[{"text":"選んだ雨粒","claim_class":"factual","claim_scopes":["observed_state"]}]}],
            "preface_direction":"雨粒を銀の粒に見立てた前置き",
            "failed_line_indices":[1],"current_lines":[{"line_index":0,"text":"くさちのひ","frozen":true}]
        }).as_object().unwrap().clone(),route:"haiku".into(),temperature:0.6,max_tokens:Some(192),fallback_value:json!({})};
        let text = messages(&request)
            .unwrap()
            .iter()
            .map(|m| m.content.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        let (ingredients, direction) = text.split_once("【材料の調理方針となる前置き】").unwrap();
        assert!(ingredients.contains("選んだ雨粒"));
        assert!(!ingredients.contains("雨粒を銀の粒に見立てた前置き"));
        assert!(direction.contains("雨粒を銀の粒に見立てた前置き"));
        assert_eq!(text.matches("雨粒を銀の粒に見立てた前置き").count(), 1);
        assert!(text.contains("全部"));
        for excluded in [
            "SECRET-ID",
            "未選択の材料",
            "atom_id",
            "固定行へ渡した材料は除いてある",
        ] {
            assert!(!text.contains(excluded), "{excluded}");
        }
    }
}

// 見込み: 既存の次段呼出しで、前置き使用材料と句用の部分集合を一度で選べる。
// 合格: 1〜3個は全件、4個以上は部分集合という契約と元資料だけを示す。意味理解は未検査。
#[test]
fn scene_request_selects_original_materials_without_a_three_material_minimum() {
    let request = StructuredRequest {
        kind: "haiku_scene".into(),
        details: json!({
            "irony":{"description":"雨粒と石の取り合わせ","elements":["雨粒","石"],"focus":["光"]},
            "preface_material_candidates":[{"material_id":"m01","facts":[{"text":"雨粒"}]}]
        })
        .as_object()
        .unwrap()
        .clone(),
        route: "chat".into(),
        temperature: 0.2,
        max_tokens: Some(192),
        fallback_value: json!({}),
    };
    let text = messages(&request).unwrap()[1].content.clone();
    for expected in [
        "preface_material_ids",
        "poem_material_ids",
        "1〜3個なら全部",
        "4個以上",
        "必ず3個に揃える必要はない",
        "m01",
    ] {
        assert!(text.contains(expected), "{expected}");
    }
    for obsolete in ["basis_atom_ids", "clauses", "最上位には必ず found"] {
        assert!(!text.contains(obsolete), "{obsolete}");
    }
}
#[test]
fn removed_evaluation_prompt_is_unavailable() {
    let request = StructuredRequest {
        kind: "haiku_line_grounding".into(),
        details: Map::new(),
        route: "chat".into(),
        temperature: 0.0,
        max_tokens: Some(512),
        fallback_value: json!({}),
    };
    assert!(messages(&request).is_err());
}

#[test]
fn workshop_revision_does_not_restore_removed_quality_gates_from_old_records() {
    let fixtures: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    let row = fixtures
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["request"]["kind"] == "haiku_workshop_revision")
        .unwrap();
    let mut request: StructuredRequest = serde_json::from_value(row["request"].clone()).unwrap();
    request.details.insert("edit_retry_feedback".into(), json!({
        "global_failure_reasons":["meter_too_long", "hard_forbidden_term", "duplicate_line", "meaning_not_retained"],
        "line_failures":[{"line_index":0,"failure_reasons":["unnatural_japanese"],"assessment_comment":"OLD-QUALITY-ASSESSMENT"}]
    }));
    let rendered = messages(&request)
        .unwrap()
        .iter()
        .map(|message| message.content.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    for stale in [
        "かなだけ",
        "各±1音",
        "0=五音",
        "meter_too_long",
        "hard_forbidden_term",
        "duplicate_line",
        "meaning_not_retained",
        "OLD-QUALITY-ASSESSMENT",
        "前の修正案が不合格",
    ] {
        assert!(!rendered.contains(stale), "{stale}");
    }
    assert!(rendered.contains("expected_text"));
    assert!(rendered.contains("固定行は一字も変えず"));
}

#[test]
fn scene_and_preface_requests_do_not_restore_removed_length_or_clause_limits() {
    let fixtures: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    for kind in ["haiku_irony", "haiku_scene"] {
        let row = fixtures
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["request"]["kind"] == kind)
            .unwrap();
        let request: StructuredRequest = serde_json::from_value(row["request"].clone()).unwrap();
        let rendered = messages(&request)
            .unwrap()
            .iter()
            .map(|message| message.content.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        for stale in ["12〜72字", "36字以内", "72字以内", "1〜3節"] {
            assert!(!rendered.contains(stale), "{kind}: {stale}");
        }
        assert!(rendered.contains("材料"));
    }
}
