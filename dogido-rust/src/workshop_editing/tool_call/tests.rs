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
fn native_line_reference_preserves_model_interpretation() {
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
    let checked = workshop_response::decode(&frame, &details).unwrap();
    assert_eq!(
        checked["step"]["analysis"]["line_reference"]["line_index"],
        0
    );
    for reference in ["前の上五", "下五"] {
        args["line_reference_evidence"] = reference.into();
        assert!(normalize_player_edit_tool(&view, raw, &args).is_ok());
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
fn normalization_keeps_interpreted_words_and_checks_the_current_target() {
    for (key, value) in [
        ("line_id", json!("line_1")),
        ("line_id", json!("line_4")),
        ("expected_text", Value::Null),
    ] {
        let (view, mut args) = fixture();
        args[key] = value;
        assert!(
            normalize_player_edit_tool(&view, RAW, &args).is_err(),
            "{key}: {args}"
        );
    }
    // 語形と原文の表記でモデルの置換語を拒否しない。
    let (view, mut args) = fixture();
    let raw = "くろいのところ、しろいがええかな";
    args["evidence"] = raw.into();
    args["replacement_text"] = "あおい".into();
    args["evidence"] = "黒から青への変更".into();
    assert!(normalize_player_edit_tool(&view, raw, &args).is_ok());
    for raw in [
        "くろいをしろいに変えていい？",
        "くろいをしろいに変えてと言われた",
        "『くろいをしろいに変えて』という例",
        "くろいをしろいに変えないで",
    ] {
        let (view, mut args) = fixture();
        args["evidence"] = if raw.starts_with('『') { RAW } else { raw }.into();
        assert!(
            normalize_player_edit_tool(&view, raw, &args).is_ok(),
            "{raw}"
        );
    }
}

#[test]
fn compare_against_pending_and_accept_player_wording() {
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
    let PlayerEditResult::Validated(result) = result else {
        panic!("missing edit");
    };
    assert!(result.failure_reasons.is_empty());
    assert_eq!(result.lines[1].reading_text, "あおおのへと");
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

fn reported_fixture() -> Value {
    let lines = Engine::default()
        .whole_verse("ゆらぐひが\nたましいのひが\nゆらゆらと", "generated")
        .unwrap();
    json!({"current_lines":lines,"emission":{"lines":lines},"materials":{},"followup":"discussion"})
}

#[test]
fn model_phrase_boundaries_are_preserved_through_the_edit_engine() {
    let view = reported_fixture();
    // 原文を再解析して、モデルの旧語や新語を延長・短縮しない。
    for (raw, target, new_word, expected) in [
        (
            "ゆらぐひが を うつろいの に変えてください。",
            "ゆらぐひが",
            "うつろい",
            "うつろい",
        ),
        (
            "揺らぐひがをうつろいのに変えてください",
            "ひが",
            "うつろい",
            "ゆらぐうつろい",
        ),
        (
            "では揺らぐひがをうつろいのに変えてください",
            "ゆらぐひが",
            "うつろいの",
            "うつろいの",
        ),
        (
            "じゃあ、「ゆらぐひが」を「うつろいの」にして",
            "ゆらぐひが",
            "うつろい",
            "うつろい",
        ),
        (
            "「ゆらぐひが」を「うみをみる」に変えて",
            "ひが",
            "うみ",
            "ゆらぐうみ",
        ),
        ("ゆらぐひがをなみにのるにして", "ゆらぐひが", "なみ", "なみ"),
    ] {
        let args = json!({"line_id":"line_1","expected_text":"ゆらぐひが",
            "target_fragment":target,"replacement_text":new_word,"evidence":raw,"confidence":0.95});
        let frame = normalize_player_edit_tool(&view, raw, &args).unwrap();
        assert_eq!(frame["proposal"]["replacement_text"], new_word, "{raw}");
        assert_eq!(frame["proposal"]["target_fragment"], target, "{raw}");
        // 辞書IPCは別途模擬通信で確認。単体ではこの入力に必要な読みだけを固定する。
        let mut engine = engine_for(raw);
        engine
            .readings
            .insert(raw.into(), raw.replace("揺", "ゆ").replace("変", "か"));
        let target = frame["proposal"]["target_fragment"].as_str().unwrap();
        engine.readings.insert(
            target.into(),
            target.replace("揺", "ゆ").replace(['「', '」', '、'], ""),
        );
        engine
            .readings
            .insert("揺らぐひが".into(), "ゆらぐひが".into());
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine.project(&frame).unwrap()
        else {
            panic!("not an edit: {raw}")
        };
        assert!(result.failure_reasons.is_empty(), "{raw}: {result:?}");
        assert_eq!(result.lines[0].surface_text, expected);
        assert_eq!(result.lines[0].reading_text, expected);
        assert_eq!(result.lines[1].reading_text, "たましいのひが");
        assert_eq!(result.lines[2].reading_text, "ゆらゆらと");
        let base: Vec<HaikuLine> = serde_json::from_value(view["current_lines"].clone()).unwrap();
        let pending = Pending::stage(&base, &base, result.lines, 0).unwrap();
        assert_eq!(pending.edits().len(), 1);
        assert_eq!(pending.edits()[0].replacement_text, expected);
        pending.validate(&base).unwrap();
    }
}

#[test]
fn raw_phrase_boundaries_preserve_target_and_do_not_classify_intent() {
    let view = reported_fixture();
    for raw in [
        "ゆらぐひがをうつろいのに変えないで",
        "ゆらぐひがをうつろいのに変えていい？",
        "『ゆらぐひがをうつろいのに変えて』と言われた",
        "ゆらぐひがをうつろいのに変えて、ゆらゆらとをあさのいろに変えて",
        "ゆらぐひがをうみをみるに変えて", // 引用のない複数の「を」は範囲を決めつけない。
    ] {
        let args = json!({"line_id":"line_1","expected_text":"ゆらぐひが",
            "target_fragment":"ひが","replacement_text":"う", "evidence":raw,"confidence":0.95});
        assert!(
            normalize_player_edit_tool(&view, raw, &args).is_ok(),
            "{raw}"
        );
    }
    let raw = "ゆらぐひがをうつろいのに変えて";
    let args = json!({"line_id":"line_2","expected_text":"たましいのひが",
        "target_fragment":"ひが","replacement_text":"うつろい","evidence":raw,"confidence":0.95});
    let frame = normalize_player_edit_tool(&view, raw, &args).unwrap();
    assert_eq!(frame["proposal"]["line_index"], 1);
    assert_eq!(frame["proposal"]["target_fragment"], "ひが");

    // 前置き等を含む原文Aが現在句に対応しなければ、モデルの有効な対象を保つ。
    for (raw, reference) in [
        ("あかいゆらぐひがをうつろいのに変えて", ""),
        ("「あかいゆらぐひが」を「うつろいの」に変えて", ""),
        ("「ではゆらぐひが」を「うつろいの」に変えて", ""),
        ("あかいゆらぐひがをうつろいのに変えて", "あかいゆらぐひが"),
        (
            "「あかいゆらぐひが」を「うつろいの」に変えて",
            "あかいゆらぐひが",
        ),
    ] {
        let args = json!({"line_id":"line_1","expected_text":"ゆらぐひが",
            "target_fragment":"ゆらぐひが","replacement_text":"うつろい","evidence":raw,"confidence":0.95,
            "line_reference_evidence":reference});
        let frame = normalize_player_edit_tool(&view, raw, &args).unwrap();
        let Output::PlayerEdit(result) = engine_for(raw).project(&frame).unwrap() else {
            panic!("not an edit")
        };
        assert!(result.failure_reasons().is_empty(), "{raw}: {result:?}");
    }
}

#[test]
fn a_phrase_starting_with_dewa_is_preserved_during_replacement() {
    let lines = Engine::default()
        .whole_verse("ではじめる\nたましいのひが\nゆらゆらと", "generated")
        .unwrap();
    let view = json!({"current_lines":lines,"emission":{"lines":lines},"materials":{},"followup":"discussion"});
    for raw in ["ではじめるをおわるに変えて", "では始めるをおわるに変えて"]
    {
        let args = json!({"line_id":"line_1","expected_text":"ではじめる",
            "target_fragment":"ではじめる","replacement_text":"おわる","evidence":raw,"confidence":0.95});
        let frame = normalize_player_edit_tool(&view, raw, &args).unwrap();
        let mut engine = engine_for(raw);
        engine
            .readings
            .insert(raw.into(), raw.replace("始", "じ").replace("変", "か"));
        engine
            .readings
            .insert("では始める".into(), "ではじめる".into());
        let Output::PlayerEdit(result) = engine.project(&frame).unwrap() else {
            panic!("not an edit")
        };
        let PlayerEditResult::Validated(result) = result else {
            panic!("missing edit");
        };
        assert!(result.failure_reasons.is_empty(), "{raw}: {result:?}");
        assert_eq!(result.lines[0].reading_text, "おわる");
    }
}

#[test]
fn explicit_line_reference_keeps_existing_partial_replacements() {
    for raw in [
        "中七のくろいをしろいに変えて",
        "中七の「くろい」を「しろい」に変えて",
    ] {
        let (view, mut args) = fixture();
        args["evidence"] = raw.into();
        let frame = normalize_player_edit_tool(&view, raw, &args).unwrap();
        assert_eq!(frame["proposal"]["target_fragment"], "くろい");
        let mut engine = engine_for(raw);
        engine.readings.insert(
            raw.into(),
            raw.replace("中七", "なかしち").replace("変", "か"),
        );
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine.project(&frame).unwrap()
        else {
            panic!("not an edit")
        };
        assert_eq!(
            result.text.as_deref(),
            Some("さくらのは\nしろいおのへと\nあさのいろ")
        );
    }
    let raw = "冒頭の五音の「さくら」を「よもぎ」にして";
    let (view, mut args) = fixture();
    args["line_id"] = "line_1".into();
    args["expected_text"] = "さくらのは".into();
    args["target_fragment"] = "さくら".into();
    args["replacement_text"] = "よもぎ".into();
    args["evidence"] = raw.into();
    args["line_reference_evidence"] = "冒頭の五音".into();
    let frame = normalize_player_edit_tool(&view, raw, &args).unwrap();
    assert_eq!(frame["proposal"]["target_fragment"], "さくら");
    let mut engine = engine_for(raw);
    engine
        .readings
        .insert(raw.into(), raw.replace("冒頭の五音", "ぼうとうのごおん"));
    engine.readings.insert(
        "冒頭の五音の「さくら".into(),
        "ぼうとうのごおんのさくら".into(),
    );
    let Output::PlayerEdit(PlayerEditResult::Validated(result)) = engine.project(&frame).unwrap()
    else {
        panic!("not an edit")
    };
    assert_eq!(result.lines[0].reading_text, "よもぎのは");
}

fn white_sand_fixture() -> Value {
    let lines = Engine::default()
        .whole_verse("やさしいひが\nしろいしをてらし\nうみのゆめ", "generated")
        .unwrap();
    json!({"current_lines":lines,"emission":{"lines":lines},"materials":{},"followup":"discussion"})
}

fn white_sand_args(raw: &str, replacement: &str) -> Value {
    json!({"line_id":"line_2","expected_text":"しろいしをてらし",
        "target_fragment":"しろいしをてらし","replacement_text":replacement,"evidence":raw,"confidence":0.95})
}

#[test]
fn native_edit_keeps_model_words_across_arrow_and_quoted_inputs() {
    let mut view = white_sand_fixture();
    view["conversation_candidate"] = json!({"proposal":{
        "line_index":1,"target_fragment":"しろいしをてらし","replacement_text":"しろすなをてらし"},
        "evidence":"しろいしをてらし -> しろすなをてらし","validation_codes":["meter_not_exact"]});
    for raw in [
        "しろいしをてらし -> しろすなてらし",
        "しろいしをてらし→しろすなてらし",
        "「しろいしをてらし」→「しろすなてらし」",
        "「しろいしをてらし」を「しろすなてらし」に変えて",
    ] {
        let frame =
            normalize_player_edit_tool(&view, raw, &white_sand_args(raw, "しろすなをてらし"))
                .unwrap();
        assert_eq!(frame["proposal"]["replacement_text"], "しろすなをてらし");
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine_for(raw).project(&frame).unwrap()
        else {
            panic!("not an edit: {raw}")
        };
        assert!(result.failure_reasons.is_empty(), "{raw}: {result:?}");
        assert_eq!(
            result.text.as_deref(),
            Some("やさしいひが\nしろすなをてらし\nうみのゆめ")
        );
        assert_eq!(result.edits.len(), 1);
    }
    let raw = "しろいしをてらし -> しろすなをてらし";
    let frame =
        normalize_player_edit_tool(&view, raw, &white_sand_args(raw, "しろすなてらし")).unwrap();
    let Output::PlayerEdit(result) = engine_for(raw).project(&frame).unwrap() else {
        panic!("not an edit")
    };
    let PlayerEditResult::Validated(result) = result else {
        panic!("missing edit");
    };
    assert!(result.failure_reasons.is_empty());
    assert_eq!(result.lines[1].reading_text, "しろすなてらし");
}

#[test]
fn arrow_arguments_are_completed_without_reclassifying_the_model_action() {
    let view = white_sand_fixture();
    for raw in [
        "しろいしをてらし -> しろすなてらし？",
        "しろいしをてらし -> しろすなてらしにしないで",
        "しろいしをてらし -> しろさではない",
        "しろいしをてらし -> しろさはやめて",
        "もし しろいしをてらし -> しろすなてらしなら",
        "しろいしをてらし -> さくらいろなら",
        "しろいしをてらし -> さくらいろかな。",
        "しろいしをてらし -> しろすなてらしと言われた",
        "「しろいしをてらし -> しろすなてらし」",
        "『「しろいしをてらし」→「しろすなてらし」』",
        "「しろいしをてらし」 -> 「しろすなてらし",
        "しろいしをてらし -> しろすなてらし -> しろいしをてらし",
        "しろいしをてらし -> しろすなてらし、うみのゆめをあさのいろに変えて",
    ] {
        let args = white_sand_args(raw, "しろすなてらし");
        match normalize_player_edit_tool(&view, raw, &args) {
            Err(_) => {}
            Ok(frame) => {
                let Output::PlayerEdit(result) = engine_for(raw).project(&frame).unwrap() else {
                    panic!("not an edit: {raw}")
                };
                let PlayerEditResult::Validated(result) = result else {
                    panic!("missing edit");
                };
                assert!(result.failure_reasons.is_empty(), "{raw}: {result:?}");
                assert_eq!(
                    result.lines[0],
                    serde_json::from_value::<HaikuLine>(view["current_lines"][0].clone()).unwrap()
                );
                assert_eq!(
                    result.lines[2],
                    serde_json::from_value::<HaikuLine>(view["current_lines"][2].clone()).unwrap()
                );
            }
        }
    }
    let raw = "しろいしをてらし -> しろすなてらし";
    for (key, value) in [("line_id", "line_1"), ("expected_text", "前の句")] {
        let mut args = white_sand_args(raw, "しろすなをてらし");
        args[key] = value.into();
        assert!(
            normalize_player_edit_tool(&view, raw, &args).is_err(),
            "{key}"
        );
    }
    // 引用した新語の否定表現は操作の否定にしない。語句自体は7音のまま検査する。
    for (raw, expected) in [
        ("しろいしをてらし → 「しろさではない」", "しろさではない"),
        ("しろいしをてらし → \"しろさではない\"", "しろさではない"),
        ("しろいしをてらし → 「さくらいろなら」", "さくらいろなら"),
        ("しろいしをてらし → 「さくらいろかな」", "さくらいろかな"),
    ] {
        let frame =
            normalize_player_edit_tool(&view, raw, &white_sand_args(raw, expected)).unwrap();
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine_for(raw).project(&frame).unwrap()
        else {
            panic!("not an edit")
        };
        assert!(result.failure_reasons.is_empty(), "{raw}: {result:?}");
        assert_eq!(result.lines[1].reading_text, expected);
    }
}

#[test]
fn interpreted_target_preserves_only_the_matching_current_line() {
    for (target, retained, selected, succeeds) in [
        ("それ", Some(2), 2, true),
        ("「それ」", Some(2), 2, true),
        ("それ", None, 2, true),
        ("それ", Some(0), 2, true),
    ] {
        let (mut view, mut args) = fixture();
        if let Some(index) = retained {
            view["discussion_target"] = json!({"line_index":index,
                "line_id":format!("line_{}",index+1),"fragment":""});
        }
        let raw = format!("{target}を『あさひかる』にして");
        args["line_id"] = format!("line_{}", selected + 1).into();
        args["expected_text"] = "あさのいろ".into();
        args["target_fragment"] = "".into();
        args["replacement_text"] = "あさひかる".into();
        args["evidence"] = raw.clone().into();
        let frame = normalize_player_edit_tool(&view, &raw, &args).unwrap();
        let engine = engine_for(&raw);
        let Output::PlayerEdit(result) = engine.project(&frame).unwrap() else {
            panic!("not edit")
        };
        let text = match result {
            PlayerEditResult::Validated(r) => r.text,
            _ => None,
        };
        assert_eq!(text.is_some(), succeeds, "{raw} retained={retained:?}");
        if succeeds {
            assert_eq!(
                text.as_deref(),
                Some("さくらのは\nくろいおのへと\nあさひかる")
            );
        }
    }
}

#[test]
fn model_selected_edit_with_tomoshibi_needs_no_word_exemption() {
    let mut lines = fixture().0["current_lines"].clone();
    for (i, phrase) in ["いしのそこ", "ゆれるともしのひ", "だいじょうぶや"]
        .iter()
        .enumerate()
    {
        lines[i]["surface_text"] = (*phrase).into();
        lines[i]["reading_text"] = (*phrase).into();
    }
    let view = json!({"current_lines":lines,"emission":{"lines":lines},"materials":{}});
    for raw in [
        "ゆれるともしのひ->ゆれるともしび",
        "ゆれるともしのひをゆれるともしびに変更してください",
        "ゆれるともしのひをゆれるともしびにへんこうして",
        "ゆれるともしのひをゆれるともしびにかえようぜ",
        "中句をゆれるともしびに変更してください",
    ] {
        let args = json!({"line_id":"line_2", "expected_text":"ゆれるともしのひ",
            "target_fragment":if raw.starts_with("中句") {""} else {"ゆれるともしのひ"},
            "line_reference_evidence":if raw.starts_with("中句") {"中句"} else {""},
            "replacement_text":"ゆれるともしび", "evidence":raw,"confidence":1.0});
        let frame = normalize_player_edit_tool(&view, raw, &args)
            .unwrap_or_else(|error| panic!("{raw}: {error}"));
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine_for(raw).project(&frame).unwrap()
        else {
            panic!("not an edit: {raw}")
        };
        assert!(result.failure_reasons.is_empty(), "{raw}: {result:?}");
        assert_eq!(result.lines[1].reading_text, "ゆれるともしび");
        assert_eq!(result.edits.len(), 1);
    }
    for raw in [
        "もし、ゆれるともしのひをゆれるともしびに変更してください",
        "もしよければゆれるともしのひをゆれるともしびに変更してください",
        "もし今日の夕方までに予定している作業が全部きちんと終わった場合は、ゆれるともしのひをゆれるともしびに変更してください",
        "ゆれるともしのひをゆれるともしびに変更しないで",
        "ゆれるともしのひはゆれるともしびのほうがいいんじゃない？",
        "「ゆれるともしのひをゆれるともしびに変更してください」",
        "ゆれるともしのひをゆれるともしびに変更してと言われた",
    ] {
        let args = json!({"line_id":"line_2", "expected_text":"ゆれるともしのひ",
            "target_fragment":"ゆれるともしのひ", "replacement_text":"ゆれるともしび",
            "evidence":raw.rsplit('、').next().unwrap(),"confidence":1.0});
        assert!(
            normalize_player_edit_tool(&view, raw, &args).is_ok(),
            "{raw}"
        );
    }
}

#[test]
fn native_edits_keep_punctuation_in_surface_and_reading_separate() {
    let view = white_sand_fixture();
    let base: Vec<HaikuLine> = serde_json::from_value(view["current_lines"].clone()).unwrap();
    for (raw, expected) in [
        ("しろいしをてらし → さくらいろかな。", "さくらいろかな。"),
        (
            "しろいしをてらし → 「さくらいろかな。」",
            "さくらいろかな。",
        ),
        (
            "しろいしをてらし -> 『さくらいろかな？』",
            "さくらいろかな？",
        ),
    ] {
        let frame =
            normalize_player_edit_tool(&view, raw, &white_sand_args(raw, expected)).unwrap();
        assert_eq!(frame["proposal"]["replacement_text"], expected, "{raw}");
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine_for(raw).project(&frame).unwrap()
        else {
            panic!("not an edit: {raw}")
        };
        assert!(result.failure_reasons.is_empty(), "{raw}: {result:?}");
        assert_eq!(result.lines[1].surface_text, expected, "{raw}");
        assert_eq!(result.lines[1].reading_text, "さくらいろかな", "{raw}");
        assert_eq!(result.lines[0], base[0]);
        assert_eq!(result.lines[2], base[2]);
        assert_eq!(result.edits.len(), 1);
        // 保存差分は既存の確定読み契約を維持し、表示表記は行正本で保持する。
        assert_eq!(result.edits[0].replacement_text, "さくらいろかな");
        let pending = Pending::stage(&base, &base, result.lines, 1).unwrap();
        assert_eq!(pending.lines[1].surface_text, expected);
        assert_eq!(pending.lines[1].reading_text, "さくらいろかな");
        assert_eq!(pending.edits(), result.edits);
        pending.validate(&base).unwrap();
    }
}

#[test]
fn bert_edit_uses_interpreted_words_and_keeps_cas_and_other_lines() {
    use crate::workshop::tools::Call;
    let mut current = fixture().0;
    current["current_lines"][0]["surface_text"] = "たいまつのひ".into();
    current["current_lines"][0]["reading_text"] = "たいまつのひ".into();
    current["emission"]["lines"] = current["current_lines"].clone();
    let base: Vec<crate::haiku_record::HaikuLine> =
        serde_json::from_value(current["current_lines"].clone()).unwrap();
    for (raw, evidence) in [
        ("紙の句を松松がにしましょう", "松松がにしましょう"),
        ("紙の句を松松がにしましょう", "上五を松明へ変更"),
        ("上を端末に変えて", "松明に変えて"),
        ("一番上はあの明かりの名前で", ""),
    ] {
        let mut message = json!({"role":"assistant","content":null,"tool_calls":[{
            "id":"edit-1","type":"function","function":{"name":"stage_player_edit","arguments":""}}]});
        message["tool_calls"][0]["function"]["arguments"] = json!({
            "target_fragment":"たいまつのひ", "replacement_text":"松明",
            "evidence":evidence, "confidence":1.0})
        .to_string()
        .into();
        let call = Call::decode_for_target(message.clone(), &current, raw, Some(0)).unwrap();
        assert!(Call::decode_for_target(message.clone(), &current, raw, None).is_err());
        let frame = json!({"workshop":current,"text":raw,"phase":"decide",
            "allowed_actions":["stage_player_edit"],"payload":call.payload()});
        let details = crate::workshop_projection::details_for(&frame).unwrap();
        let checked = crate::workshop_response::decode(&frame, &details).unwrap();
        let mut step = checked["step"].clone();
        assert_eq!(step["action"], "stage_player_edit", "{raw}: {checked}");
        call.retain_classified_target(&mut step);
        let frame = json!({"op":"player_edit", "workshop":current,"text":raw,
            "proposal":step["analysis"]["line_proposal"]});
        let engine = Engine {
            readings: [("松明".into(), "たいまつ".into())].into(),
        };
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine.project(&frame).unwrap()
        else {
            panic!("not an edit: {raw}")
        };
        assert!(result.failure_reasons.is_empty(), "{raw}: {result:?}");
        assert_eq!(result.lines[0].surface_text, "松明");
        assert_eq!(result.lines[0].reading_text, "たいまつ");
        assert_eq!(result.lines[1..], base[1..]);
        let pending = Pending::stage(&base, &base, result.lines, 0).unwrap();
        pending.validate(&base).unwrap();
        let mut stale = base.clone();
        stale[0].surface_text = "既に変わった句".into();
        assert!(pending.validate(&stale).is_err());
        // 完成行の適用先はBERTが所有する。旧語から別行へ移し替えない。
        let Call::PlayerEdit { frame, .. } =
            Call::decode_for_target(message, &current, raw, Some(1)).unwrap()
        else {
            panic!("not an edit")
        };
        assert_eq!(frame["proposal"]["line_index"], 1);
        assert_eq!(frame["proposal"]["target_fragment"], "");
    }
}

// 見込み: 完成行なら末尾の削除を部分一致で取り違えない。
// 合格: あさひさして→あさひさし、部分変更、下句の削除が一行差分となり、他二行・原本は不変。旧語が付いても使わない。固定tool応答から実Engine/CASまでを確認し、モデル解釈は対象外。
#[test]
fn classified_complete_line_replaces_the_entire_selected_line() {
    use crate::workshop::tools::Call;
    let lines = Engine::default()
        .whole_verse("あさひさして\nくろいおのへと\nあさのいろ", "generated")
        .unwrap();
    let view = json!({"current_lines":lines,"emission":{"lines":lines},"materials":{},"followup":"discussion"});
    for (target, replacement) in [(0, "あさひさし"), (1, "しろいおのへと"), (2, "あさのい")]
    {
        let message = json!({"tool_calls":[{"id":"complete-line","type":"function","function":{
            "name":"stage_player_edit","arguments":json!({
                "replacement_text":replacement,"target_fragment":replacement
            }).to_string()}}]});
        let Call::PlayerEdit { frame, .. } = Call::decode_for_target(
            message,
            &view,
            "本人の指定を解釈した変更後の一行",
            Some(target),
        )
        .unwrap() else {
            panic!("not an edit")
        };
        assert_eq!(frame["proposal"]["target_fragment"], "");
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            Engine::default().project(&frame).unwrap()
        else {
            panic!("not an edit")
        };
        assert!(
            result.failure_reasons.is_empty(),
            "{:?}",
            result.failure_reasons
        );
        assert_eq!(result.lines[target].surface_text, replacement);
        assert_eq!(result.lines[target].reading_text, replacement);
        for i in 0..3 {
            if i != target {
                assert_eq!(result.lines[i], lines[i]);
            }
        }
        assert_eq!(result.edits.len(), 1);
        assert_eq!(result.edits[0].line_index, target);
        let pending = Pending::stage(&lines, &lines, result.lines, target).unwrap();
        pending.validate(&lines).unwrap();
        let mut stale = lines.clone();
        stale[target].surface_text = "べつのく".into();
        assert!(pending.validate(&stale).is_err());
        assert_eq!(view["current_lines"], json!(lines));
    }
}

// 見込み: 完成行の同値指定は実編集にならない。
// 合格: どの行も同じ本文ならno_change・差分0・結果本文なし、入力の行記録も不変。固定tool応答と実Engineの比較だけを検査し、保存はHTTP試験で確認。
#[test]
fn classified_identical_complete_line_is_no_change() {
    use crate::workshop::tools::Call;
    let (view, _) = fixture();
    for target in 0..3 {
        let replacement = &view["current_lines"][target]["surface_text"];
        let message = json!({"tool_calls":[{"id":"same-line","type":"function","function":{
            "name":"stage_player_edit","arguments":json!({"replacement_text":replacement}).to_string()}}]});
        let Call::PlayerEdit { frame, .. } =
            Call::decode_for_target(message, &view, "同じ句を指定", Some(target)).unwrap()
        else {
            panic!("not an edit")
        };
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            Engine::default().project(&frame).unwrap()
        else {
            panic!("not an edit")
        };
        assert_eq!(result.failure_reasons, ["no_change"]);
        assert!(result.edits.is_empty());
        assert!(result.text.is_none());
        assert_eq!(view, fixture().0);
    }
}

// 見込み: 既知の行ラベルを本文から取り出せば、モデルを再呼出しせず編集できる。
// 合格: 実例と全行のラベル形式を受理し、対象本文だけ変更。他二行・原snapshotは不変。固定tool出力と実Engineの試験。
#[test]
fn labelled_replacement_is_cooked_into_body_without_rejecting_the_edit() {
    let (view, _) = fixture();
    let before = view.clone();
    for (index, label) in ["上の句", "中の句", "下の句"].iter().enumerate() {
        for replacement in [
            format!("{label}: ともしびに"),
            format!("{label}：ともしびに"),
            format!("line_{}=ともしびに", index + 1),
            format!("「{label}: ともしびに」"),
            format!("{label}:\nともしびに"),
        ] {
            let mut args = json!({"replacement_text":replacement});
            fill_classified_edit_target(&view, index, &mut args).unwrap();
            let frame =
                normalize_player_edit_tool(&view, "ともしびとに変えましょう", &args).unwrap();
            let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
                Engine::default().project(&frame).unwrap()
            else {
                panic!("not an edit")
            };
            assert!(result.failure_reasons.is_empty(), "{replacement}");
            assert_eq!(result.lines[index].surface_text, "ともしびに");
            for i in 0..3 {
                if i != index {
                    assert_eq!(
                        result.lines[i].surface_text,
                        view["current_lines"][i]["surface_text"]
                    );
                }
            }
        }
    }
    assert_eq!(view, before);
}

// 見込み: 三行を返した場合も既存の対象外不変・CASを保ったまま見出しを外せる。
// 合格: 下句だけ抽出し、別行も変えた出力と古い元句は従来どおり実行不可。固定tool引数の検査。
#[test]
fn labelled_full_verse_keeps_target_and_version_checks() {
    let (view, _) = fixture();
    let mut args = json!({"replacement_text":"上の句: さくらのは\n中の句: くろいおのへと\n下の句: ともしびに"});
    fill_classified_edit_target(&view, 2, &mut args).unwrap();
    let frame = normalize_player_edit_tool(&view, "下の句を変えて", &args).unwrap();
    assert_eq!(frame["proposal"]["replacement_text"], "ともしびに");
    args["expected_text"] = "古い句".into();
    assert!(normalize_player_edit_tool(&view, "下の句を変えて", &args).is_err());
    fill_classified_edit_target(&view, 2, &mut args).unwrap();
    args["replacement_text"] =
        "上の句: さくらいろ\n中の句: くろいおのへと\n下の句: ともしびに".into();
    assert!(normalize_player_edit_tool(&view, "下の句を変えて", &args).is_err());
}

// 見込み: 見出しの区切りがない通常の表現や本文途中の行名には触れない。
// 合格: 原文一致を要求せず、モデルの語句・句読点・本文内の行名を保持。固定引数の正規化のみ。
#[test]
fn label_cooking_preserves_plain_words_and_inner_labels() {
    let (view, _) = fixture();
    for text in [
        "ともしびと",
        "ともしびに",
        "下の句を詠む",
        "ああ、下の句: ともしびに",
        "下の句:",
    ] {
        let mut args = json!({"replacement_text":text});
        fill_classified_edit_target(&view, 2, &mut args).unwrap();
        let frame = normalize_player_edit_tool(&view, "別の認識原文", &args).unwrap();
        assert_eq!(frame["proposal"]["replacement_text"], text);
    }
}

// 見込み: 既存のline_ID付き三行形式を、先頭行だけの本文として誤って扱わない。
// 合格: 上句を選んだ場合も三行を比較し、変更した上句だけ抽出。他行改変は拒否。固定tool出力の境界試験。
#[test]
fn legacy_labelled_whole_verse_is_not_collapsed_into_first_line() {
    let (view, _) = fixture();
    let mut args =
        json!({"replacement_text":"line_1=ともしびに, line_2=くろいおのへと, line_3=あさのいろ。"});
    fill_classified_edit_target(&view, 0, &mut args).unwrap();
    let frame = normalize_player_edit_tool(&view, "上の句を変えて", &args).unwrap();
    assert_eq!(frame["proposal"]["replacement_text"], "ともしびに");
    args["replacement_text"] = "line_1=ともしびに, line_2=別の行, line_3=あさのいろ。".into();
    assert!(normalize_player_edit_tool(&view, "上の句を変えて", &args).is_err());
}
