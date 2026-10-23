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
    assert_eq!(
        checked["step"]["analysis"]["line_reference"]["line_index"],
        0
    );
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
fn normalization_preserves_original_evidence_and_target_without_intent_veto() {
    for (key, value) in [
        ("evidence", json!("以前の相談案は「光かかして」。")),
        ("line_id", json!("line_1")),
        ("line_id", json!("line_4")),
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
    // 明示A/Bのない自由な言い方では、モデルが原文にない語を補うことは引き続き拒否する。
    let (view, mut args) = fixture();
    let raw = "くろいのところ、しろいがええかな";
    args["evidence"] = raw.into();
    args["replacement_text"] = "あおい".into();
    assert!(normalize_player_edit_tool(&view, raw, &args).is_err());
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
fn specified_phrases_survive_model_shortening_and_save_as_one_line_edit() {
    let view = reported_fixture();
    // 実際に失敗した文字入力・音声入力と、引用・新語中の助詞。
    for (raw, target, new_word, expected) in [
        (
            "ゆらぐひが を うつろいの に変えてください。",
            "ゆらぐひが",
            "うつろい",
            "うつろいの",
        ),
        (
            "揺らぐひがをうつろいのに変えてください",
            "ひが",
            "うつろい",
            "うつろいの",
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
            "うつろいの",
        ),
        (
            "「ゆらぐひが」を「うみをみる」に変えて",
            "ひが",
            "うみ",
            "うみをみる",
        ),
        (
            "ゆらぐひがをなみにのるにして",
            "ゆらぐひが",
            "なみ",
            "なみにのる",
        ),
    ] {
        let args = json!({"line_id":"line_1","expected_text":"ゆらぐひが",
            "target_fragment":target,"replacement_text":new_word,"evidence":raw,"confidence":0.95});
        let frame = normalize_player_edit_tool(&view, raw, &args).unwrap();
        assert_eq!(frame["proposal"]["replacement_text"], expected, "{raw}");
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
    assert!(normalize_player_edit_tool(&view, raw, &args).is_err());

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
fn explicit_arrow_uses_current_words_even_when_model_reuses_a_previous_candidate() {
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
        assert_eq!(frame["proposal"]["replacement_text"], "しろすなてらし");
        let Output::PlayerEdit(PlayerEditResult::Validated(result)) =
            engine_for(raw).project(&frame).unwrap()
        else {
            panic!("not an edit: {raw}")
        };
        assert!(result.failure_reasons.is_empty(), "{raw}: {result:?}");
        assert_eq!(
            result.text.as_deref(),
            Some("やさしいひが\nしろすなてらし\nうみのゆめ")
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
    assert_eq!(result.lines[1].reading_text, "しろすなをてらし");
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
    for (key, value) in [
        ("line_id", "line_1"),
        ("expected_text", "前の句"),
        ("evidence", "しろすなをてらし"),
    ] {
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
fn verbatim_deictic_target_preserves_only_the_matching_retained_line() {
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
fn direct_and_native_edits_keep_literal_punctuation_in_surface_and_reading_separate() {
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
        let direct = verbatim_player_edit(&view, raw, 1).unwrap().unwrap();
        let native =
            normalize_player_edit_tool(&view, raw, &white_sand_args(raw, expected)).unwrap();
        for frame in [direct, native] {
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
}
