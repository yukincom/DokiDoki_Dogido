use chrono::{Duration, TimeZone, Utc};
use dogido_rust::{
    haiku_memory::{RecallQuery, clear_requested, feedback_kind, lesson},
    haiku_record::MemoryStore,
};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        Self(std::env::temp_dir().join(format!("dogido-memory-{}", uuid::Uuid::new_v4())))
    }
    fn store(&self) -> MemoryStore {
        MemoryStore::new(&self.0)
    }
    fn write(&self, path: &str, rows: &[Value]) {
        let p = self.0.join(path);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(
            p,
            rows.iter()
                .map(|v| v.to_string() + "\n")
                .collect::<String>(),
        )
        .unwrap();
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        if self.0.exists() {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }
}
fn at() -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 9, 27, 12, 0, 0).unwrap()
}
fn read(path: &Path) -> Vec<Value> {
    fs::read_to_string(path)
        .unwrap()
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect()
}
fn l(axis: &str, age: i64) -> Value {
    json!({"lesson_type":axis,"note":"参考","created_at":(at()-Duration::days(age)).to_rfc3339(),"polarity":"tighten"})
}

#[test]
fn only_whole_explicit_clear_requests_change_lessons() {
    for s in [
        "気にせんで",
        "うん、もう気にせんでええよ！",
        "気にしなくていい",
        "前の注意はもういらんで",
        "縛らんで",
        "緩めてください",
    ] {
        assert!(clear_requested(s), "{s}");
    }
    for s in [
        "気にせんでいい？",
        "『気にせんで』と言った",
        "緩めてほしいわけじゃない",
        "緩めてくれたら",
        "気にせんでいいとは言ってない",
        "いい句",
        "そうなんだ",
    ] {
        assert!(!clear_requested(s), "{s}");
    }
}
#[test]
fn feedback_mapping_excludes_ack_adoption_and_praise_lessons() {
    assert_eq!(
        feedback_kind(
            &json!({"action":"respond","findings":[{"problem":"off_scene"},{"problem":"meter"}]})
        ),
        Some("forced_compress")
    );
    assert_eq!(
        feedback_kind(&json!({"action":"explain","purpose":"understand_meaning"})),
        Some("ask_meaning")
    );
    for action in [
        "inspect",
        "show_current",
        "acknowledge_meaning",
        "accept_pending",
        "reject_pending",
        "close_workshop",
        "unrelated",
    ] {
        assert!(
            feedback_kind(&json!({"action":action,"findings":[{"problem":"meter"}]})).is_none()
        );
    }
    assert!(lesson("praise").is_none());
    assert!(lesson("other").is_none());
    assert_eq!(
        lesson("unreadable").unwrap()["forbidden_fragments"],
        json!([])
    );
}
#[test]
fn reads_do_not_create_storage() {
    let f = Fixture::new();
    let s = f.store();
    assert!(s.active_lessons(at()).unwrap().is_empty());
    assert!(s.search_poems(&RecallQuery::default()).unwrap().is_empty());
    assert!(!f.0.exists());
}

#[test]
fn session_local_id_collision_does_not_erase_a_different_poem() {
    let f = Fixture::new();
    for (sid, text) in [("one", "first"), ("two", "second")] {
        f.write(
            &format!("sessions/{sid}/long_term/haiku_entries.jsonl"),
            &[json!({"id":"same_second_sequence","created_at":at(),"text":text})],
        );
    }
    assert_eq!(
        f.store()
            .search_poems(&RecallQuery::default())
            .unwrap()
            .len(),
        2
    );
}
#[test]
fn latest_axis_limit_and_age_match_existing_memory() {
    let f = Fixture::new();
    f.write(
        "long_term/haiku_lessons.jsonl",
        &[
            l("old", 15),
            l("a", 4),
            l("b", 3),
            l("a", 2),
            l("c", 1),
            l("d", 0),
        ],
    );
    let rows = f.store().active_lessons(at()).unwrap();
    assert_eq!(
        rows.iter()
            .map(|v| v["lesson_type"].as_str().unwrap())
            .collect::<Vec<_>>(),
        vec!["d", "c", "a"]
    );
    f.write(
        "long_term/haiku_lessons.jsonl",
        &[l("old", 15), l("edge", 14)],
    );
    assert_eq!(f.store().active_lessons(at()).unwrap().len(), 1);
}
#[test]
fn emission_ttl_counts_sessions_once_and_excludes_revisions() {
    let f = Fixture::new();
    f.write("long_term/haiku_lessons.jsonl", &[l("a", 1)]);
    let entries = (0..5)
        .map(|i| json!({"id":format!("e{i}"),"created_at":at()}))
        .collect::<Vec<_>>();
    f.write("sessions/one/long_term/haiku_entries.jsonl", &entries);
    f.write("sessions/two/long_term/haiku_entries.jsonl", &entries); // duplicate IDs
    f.write(
        "long_term/haiku_revisions.jsonl",
        &[json!({"created_at":at()})],
    );
    assert_eq!(f.store().active_lessons(at()).unwrap().len(), 1);
    f.write(
        "sessions/three/long_term/haiku_entries.jsonl",
        &[json!({"id":"six","created_at":at()})],
    );
    assert!(f.store().active_lessons(at()).unwrap().is_empty());
}
#[test]
fn loosen_suppresses_older_advice_but_allows_new_feedback() {
    let f = Fixture::new();
    f.write(
        "long_term/haiku_lessons.jsonl",
        &[
            l("a", 1),
            l("b", 1),
            json!({"lesson_type":"a","polarity":"loosen","created_at":"old"}),
        ],
    );
    assert_eq!(
        f.store().active_lessons(at()).unwrap()[0]["lesson_type"],
        "b"
    );
    f.store().loosen_lessons(at()).unwrap();
    assert!(f.store().active_lessons(at()).unwrap().is_empty());
    f.store()
        .save_feedback(
            json!({"player_text":"読みにくい","surface_at_time":"くさちのひ"}),
            &json!({"action":"respond","findings":[{"problem":"unreadable"}]}),
            at(),
        )
        .unwrap();
    assert_eq!(
        f.store().active_lessons(at()).unwrap()[0]["lesson_type"],
        "readability"
    );
}
#[test]
fn praise_and_repair_preserve_existing_lessons() {
    let f = Fixture::new();
    f.write("long_term/haiku_lessons.jsonl", &[l("scene", 0)]);
    for action in ["praise", "propose_revision"] {
        f.store().save_feedback(json!({"entry_id":"original","player_text":"あ".repeat(300),"surface_at_time":"pending"}),&json!({"action":action,"findings":[{"problem":"meter"}]}),at()).unwrap();
    }
    assert_eq!(read(&f.0.join("long_term/haiku_lessons.jsonl")).len(), 1);
    let rows = read(&f.0.join("long_term/haiku_critiques.jsonl"));
    assert_eq!(rows.len(), 2);
    assert_eq!(
        rows[0]["player_text"].as_str().unwrap().chars().count(),
        240
    );
    assert_eq!(rows[0]["surface_at_time"], "pending");
    assert_eq!(rows[0]["kind"], "praise");
}
#[test]
fn recall_combines_root_sessions_and_revisions_with_time_and_place_filters() {
    let f = Fixture::new();
    f.write("long_term/haiku_entries.jsonl",&[json!({"id":"a","text":"old","created_at":"2026-09-25T00:00:00Z","world":{"biome":"plains"}})]);
    f.write("sessions/new/long_term/haiku_entries.jsonl",&[json!({"id":"b","text":"new","created_at":"2026-09-26T00:00:00","world":{"biome":"minecraft:desert"}})]);
    f.write("sessions/old/long_term/haiku_revisions.jsonl",&[json!({"id":"r","original_text":"old","revised_text":"fixed","created_at":"2026-09-27T00:00:00Z","world":{"biome":"plains"}})]);
    let rows = f.store().search_poems(&RecallQuery::default()).unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0]["kind"], "revision");
    assert_eq!(rows[1]["original_text"], "new");
    let q = RecallQuery {
        biome_ids: vec!["minecraft:desert".into()],
        since: Some(Utc.with_ymd_and_hms(2026, 9, 26, 0, 0, 0).unwrap()),
        until: Some(Utc.with_ymd_and_hms(2026, 9, 27, 0, 0, 0).unwrap()),
        ..Default::default()
    };
    assert_eq!(f.store().search_poems(&q).unwrap().len(), 1);
    let q = RecallQuery {
        until: q.since,
        ..q
    };
    assert!(f.store().search_poems(&q).unwrap().is_empty());
    let text = f
        .store()
        .recall_reply(&RecallQuery {
            biome_id: Some("snowy_plains".into()),
            place_label: Some("雪原".into()),
            ..Default::default()
        })
        .unwrap();
    assert!(text.starts_with("ぴったりは無いけど"));
    assert!(text.contains("元「old」直し「fixed」"));
}
#[test]
fn malformed_rows_are_skipped_but_io_failures_are_reported() {
    let f = Fixture::new();
    f.write("long_term/haiku_entries.jsonl", &[]);
    fs::write(
        f.0.join("long_term/haiku_entries.jsonl"),
        "broken\n[]\n{\"text\":\"valid\"}\n",
    )
    .unwrap();
    assert_eq!(
        f.store()
            .search_poems(&RecallQuery::default())
            .unwrap()
            .len(),
        1
    );
    fs::create_dir(f.0.join("long_term/haiku_lessons.jsonl")).unwrap();
    assert!(f.store().active_lessons(at()).is_err());
}
