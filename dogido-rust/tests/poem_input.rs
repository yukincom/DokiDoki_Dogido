use dogido_rust::{
    events::GameEvent,
    haiku_record::{HaikuLine, MemoryStore, PreparedEmission},
    poem_input::{Input, WholeRevision, parse, validate_lines},
};
use serde_json::{Value, json};
use std::{fs, path::PathBuf};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        Self(std::env::temp_dir().join(format!("dogido-poem-{}", uuid::Uuid::new_v4())))
    }
    fn store(&self) -> MemoryStore {
        MemoryStore::new(&self.0)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        if self.0.exists() {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }
}
fn lines(source: &str) -> Vec<HaikuLine> {
    ["さくらいろ","くろいおのもつ","あさひかる"].iter().enumerate().map(|(i,s)|serde_json::from_value(json!({
        "line_id":format!("line_{}",i+1),"line_index":i,"position":(["upper","middle","lower"][i]),"canonical_name":(["上五","中七","下五"][i]),
        "surface_text":s,"reading_text":s,"provenance":source,"source_atom_ids":[],"source_atoms":[]})).unwrap()).collect()
}
fn revision() -> WholeRevision {
    let at = "2026-09-27T12:00:00Z".parse().unwrap();
    let prepared:PreparedEmission=serde_json::from_value(json!({"text":"元の句","lines":lines("generated"),"surface_text":null,"reading_text":null,"materials":{},"interpretation":null,"preface":null,"biome":"plains","structure":null,"time_phase":"day","dimension":"minecraft:overworld","event_sequence":42})).unwrap();
    let original = prepared.complete(at).unwrap();
    WholeRevision {
        id: "rev_one".into(),
        at,
        base: original.prepared.lines.clone(),
        original,
        parent: None,
        text: "さくらいろ\nくろいおのもつ\nあさひかる".into(),
        source: "formal".into(),
        lines: lines("formal"),
    }
}
#[test]
fn literal_archive_and_three_line_natural_requests_are_distinct() {
    for prefix in ["直し:", "句直し：", "川柳直し:"] {
        assert_eq!(
            parse(&format!("{prefix}言葉のまま保存"), false),
            Some(Input::Revision {
                text: "言葉のまま保存".into(),
                source: "formal".into()
            })
        );
    }
    for sep in ["/", "／", "|", "｜", "\n", "　"] {
        let raw = format!("こう直して: さくらいろ{sep}くろいおのもつ{sep}あさひかる");
        assert!(
            matches!(parse(&raw,true),Some(Input::Revision{source,..}) if source=="conversational")
        );
        assert!(parse(&raw, false).is_none());
    }
    assert!(matches!(
        parse("こう直して、さくらいろ/くろいおのもつ/あさひかる", true),
        Some(Input::Revision { .. })
    ));
    assert!(parse("こう直して あさひかる", true).is_none());
    assert_eq!(
        parse("川柳保存: 私の句", false),
        Some(Input::Player {
            text: "私の句".into()
        })
    );
}
#[test]
fn invalid_archive_never_silently_truncates_or_falls_through() {
    for raw in [
        "直し:",
        "直し: //",
        "川柳保存：",
        "直し:上/中/下/第四行",
        "川柳:上\n中\n下\n余り",
    ] {
        assert_eq!(parse(raw, true), Some(Input::Invalid), "{raw}");
    }
}
#[test]
fn questions_negations_quotes_and_conditionals_do_not_authorize_saving() {
    for s in [
        "今の句を保存していい？",
        "今の句を保存しないで",
        "今の句を保存してと言った",
        "今の句を保存してくれたら",
        "『直し:上/中/下』と言った",
        "たとえば直し:上/中/下",
        "こうしたら:上/中/下",
        "こう直してと言われた/中/下",
        "こう直してほしくない/中/下",
    ] {
        assert!(parse(s, true).is_none(), "{s}");
    }
    for s in [
        "今の句を保存して",
        "さっきの川柳を保存してね！",
        "いまの句保存",
    ] {
        assert_eq!(parse(s, true), Some(Input::SaveLast), "{s}");
    }
}
#[test]
fn canonical_helper_output_cannot_change_supplied_text_or_add_sources() {
    let r = revision();
    validate_lines(&r.text, &r.source, &r.lines).unwrap();
    validate_lines("未解決の表記", "formal", &[]).unwrap();
    let mut wrong = r.lines.clone();
    wrong[0].surface_text = "別の句".into();
    assert!(validate_lines(&r.text, &r.source, &wrong).is_err());
    let mut wrong = r.lines.clone();
    wrong[0].source_atom_ids.push("invented".into());
    assert!(validate_lines(&r.text, &r.source, &wrong).is_err());
    let mut wrong = r.lines.clone();
    wrong[0].reading_text = "漢字".into();
    assert!(validate_lines(&r.text, &r.source, &wrong).is_err());
}
#[test]
fn player_turns_sharing_an_observation_have_distinct_entries_and_retry_is_idempotent() {
    let f = Fixture::new();
    let s = f.store();
    let e=GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-09-27T00:00:00Z","sequence":42,"event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"player":{"dimension":"minecraft:overworld","position":{"x":0,"z":0}},"world":{"biome":"plains","time_phase":"day"}})).unwrap();
    assert!(s.save_authored_poem("one", &e, "私の句").unwrap());
    assert!(!s.save_authored_poem("one", &e, "私の句").unwrap());
    assert!(s.save_authored_poem("one", &e, "別の句").is_err());
    assert!(s.save_authored_poem("two", &e, "別の句").unwrap());
    let rows: Vec<Value> = fs::read_to_string(s.entries_path())
        .unwrap()
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0]["author"], "player");
    assert_eq!(rows[0]["world"]["biome"], "plains");
}
#[test]
fn full_revision_follows_saved_canonical_parent_and_deduplicates_same_operation() {
    let f = Fixture::new();
    let s = f.store();
    let mut r = revision();
    s.save_whole_revision(&r).unwrap();
    s.save_whole_revision(&r).unwrap();
    let first = r.id.clone();
    r.id = "rev_two".into();
    r.parent = Some(first);
    r.base = r.lines.clone();
    s.save_whole_revision(&r).unwrap();
    r.id = "rev_three".into();
    r.base[0].reading_text = "ちがうく".into();
    assert!(s.save_whole_revision(&r).is_err());
    assert_eq!(
        fs::read_to_string(f.0.join("long_term/haiku_revisions.jsonl"))
            .unwrap()
            .lines()
            .count(),
        2
    );
}
#[test]
fn unresolved_archive_preserves_literal_text_and_no_canonical_reading_is_invented() {
    let f = Fixture::new();
    let s = f.store();
    let mut r = revision();
    r.text = "まだ読みの分からない私の句".into();
    r.lines.clear();
    s.save_whole_revision(&r).unwrap();
    let saved = r.record();
    assert_eq!(saved["revised_text"], r.text);
    assert!(saved["lines"].is_null());
    r.text = "変わった内容".into();
    assert!(s.save_whole_revision(&r).is_err());
}
