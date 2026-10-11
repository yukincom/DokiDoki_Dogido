use super::*;
use crate::workshop_edit::Pending;
use crate::workshop_editing::{Engine, Output, PlayerEditResult};

const RAW: &str = "くろいをしろいに変えて";
const WHOLE: &str = "line_1=さくらのは, line_2=くろいおのへと, line_3=あさのいろ。";

fn fixture() -> (Value, Value) {
    let lines = Engine::default()
        .whole_verse("さくらのは\nくろいおのへと\nあさのいろ", "generated")
        .unwrap();
    (
        json!({"current_lines":lines,"emission":{"lines":lines},"materials":{},"followup":"discussion"}),
        json!({"line_id":"line_2","expected_text":WHOLE,"target_fragment":"くろい",
         "replacement_text":"しろい","evidence":RAW,"confidence":1.0}),
    )
}

#[test]
fn native_line_reference_keeps_new_player_wording_and_rejects_fabricated_reference() {
    let (view, mut args) = fixture();
    let raw = "冒頭の五音をさくらいろにして";
    args["line_id"] = "line_1".into();
    args["expected_text"] = "さくらのは".into();
    args["target_fragment"] = "".into();
    args["replacement_text"] = "さくらいろ".into();
    args["evidence"] = raw.into();
    args["line_reference_evidence"] = "冒頭の五音".into();
    let frame = normalize_player_edit_tool(&view, raw, &args).unwrap();
    let details = workshop_projection::details_for(&frame).unwrap();
    let checked = workshop_validation::validate(&frame, &details).unwrap();
    assert_eq!(checked["step"]["analysis"]["line_reference"]["line_index"], 0);
    for reference in ["前の上五", "下五"] {
        args["line_reference_evidence"] = reference.into();
        assert!(normalize_player_edit_tool(&view, raw, &args).is_err());
    }
}

fn engine_for(raw: &str) -> Engine {
    Engine {
        readings: [(raw.into(), raw.replace("変", "か"))].into(),
    }
}

#[test]
fn full_expected_verse_uses_existing_edit_and_cas() {
    for expected in [
        WHOLE,
        "さくらのは\nくろいおのへと\nあさのいろ",
        "くろいおのへと",
    ] {
        let (view, mut args) = fixture();
        args["expected_text"] = expected.into();
        let frame = normalize_player_edit_tool(&view, RAW, &args).unwrap();
        assert_eq!(frame["expected_text"], "くろいおのへと");
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine_for(RAW).project(&frame).unwrap()
        else {
            panic!("not an edit")
        };
        assert!(result.failure_reasons.is_empty());
        assert_eq!(
            result.text.as_deref(),
            Some("さくらのは\nしろいおのへと\nあさのいろ")
        );
        assert_eq!(result.edits.len(), 1);
        assert_eq!(result.edits[0].line_index, 1);
        assert_eq!(result.edits[0].expected_text, "くろいおのへと");
        let base: Vec<HaikuLine> = serde_json::from_value(view["current_lines"].clone()).unwrap();
        let pending = Pending::stage(&base, &base, result.lines, 1).unwrap();
        assert_eq!(pending.edits(), result.edits);
        let mut changed = base.clone();
        changed[0].surface_text = "さくらいろ".into();
        assert!(pending.validate(&changed).is_err());
        assert_eq!(
            view,
            fixture().0,
            "normalization must not mutate the snapshot"
        );
    }
}

#[test]
fn stale_or_malformed_whole_verse_never_drops_unselected_lines() {
    for expected in [
        "line_1=さくらいろ, line_2=くろいおのへと, line_3=あさのいろ。",
        "line_2=くろいおのへと, line_1=さくらのは, line_3=あさのいろ。",
        "line_1=さくらのは, line_2=くろいおのへと, line_2=あさのいろ。",
        "line_1=さくらのは, line_2=くろいおのへと",
        "line_1=さくらのは, line_2=くろいおのへと, line_3=あさのいろ。説明",
        "さくらいろ\nくろいおのへと\nあさのいろ",
        "",
        "さくらのは",
    ] {
        let (view, mut args) = fixture();
        args["expected_text"] = expected.into();
        assert!(
            normalize_player_edit_tool(&view, RAW, &args).is_err(),
            "{expected}"
        );
    }
}

#[test]
fn normalization_preserves_original_evidence_target_and_confidence_checks() {
    for (key, value) in [
        ("evidence", json!("以前の相談案は「光かかして」。")),
        ("replacement_text", json!("あおい")),
        ("line_id", json!("line_1")),
        ("line_id", json!("line_4")),
        ("confidence", json!(0.84)),
        ("confidence", json!(1.1)),
        ("expected_text", Value::Null),
        ("extra", json!(true)),
    ] {
        let (view, mut args) = fixture();
        args[key] = value;
        assert!(
            normalize_player_edit_tool(&view, RAW, &args).is_err(),
            "{key}: {args}"
        );
    }
    for raw in [
        "くろいをしろいに変えていい？",
        "くろいをしろいに変えてと言われた",
        "『くろいをしろいに変えて』という例",
        "くろいをしろいに変えないで",
    ] {
        let (view, mut args) = fixture();
        args["evidence"] = if raw.starts_with('『') { RAW } else { raw }.into();
        assert!(
            normalize_player_edit_tool(&view, raw, &args).is_err(),
            "{raw}"
        );
    }
}

#[test]
fn compare_against_pending_and_keep_existing_meter_checks() {
    let (mut view, mut args) = fixture();
    let pending = Engine::default()
        .whole_verse("さくらいろ\nくろいおのへと\nあさのいろ", "generated")
        .unwrap();
    view["pending"] = json!({"lines":pending});
    assert!(normalize_player_edit_tool(&view, RAW, &args).is_err());
    args["expected_text"] = "さくらいろ\nくろいおのへと\nあさのいろ".into();
    assert!(normalize_player_edit_tool(&view, RAW, &args).is_ok());

    let (view, mut args) = fixture();
    args["replacement_text"] = "あお".into();
    args["evidence"] = "くろいをあおに変えて".into();
    let frame = normalize_player_edit_tool(&view, "くろいをあおに変えて", &args).unwrap();
    let Output::PlayerEdit(result) = engine_for("くろいをあおに変えて").project(&frame).unwrap()
    else {
        panic!("wrong output")
    };
    assert!(
        result
            .failure_reasons()
            .iter()
            .any(|r| r == "meter_not_exact")
    );
}

#[test]
fn full_replacement_extracts_one_changed_line_but_not_extra_changes() {
    let (view, mut args) = fixture();
    let raw = "中七をしろいおのへとに変えて";
    args["replacement_text"] = "さくらのは\nしろいおのへと\nあさのいろ".into();
    args["evidence"] = raw.into();
    let frame = normalize_player_edit_tool(&view, raw, &args).unwrap();
    assert_eq!(frame["proposal"]["replacement_text"], "しろいおのへと");
    let mut engine = engine_for(raw);
    engine
        .readings
        .insert(raw.into(), "なかしちをしろいおのへとにかえて".into());
    engine.readings.insert("中七".into(), "なかしち".into());
    let Output::PlayerEdit(PlayerEditResult::Validated(result)) = engine.project(&frame).unwrap()
    else {
        panic!("not an edit")
    };
    assert!(result.failure_reasons.is_empty());
    assert_eq!(result.edits.len(), 1);
    assert_eq!(result.edits[0].line_index, 1);
    for replacement in [
        "さくらいろ\nしろいおのへと\nあさのいろ",
        "さくらいろ\nくろいおのへと\nあさのいろ",
        "さくらのは\nくろいおのへと\nあさのいろ",
    ] {
        args["replacement_text"] = replacement.into();
        assert!(
            normalize_player_edit_tool(&view, raw, &args).is_err(),
            "{replacement}"
        );
    }
}
