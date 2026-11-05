//! 見込み: 親の現在辞書だけを配布し、子機では共有ファイルの状態にかかわらず編集を禁止する。
//! 合格と範囲: 一時保存先で公開内容・保存拒否・破損時の失敗を調べる。LAN、実音声、GUIは対象外。
use super::*;
use crate::{catalog_editor, reading_correction::Correction};
use chrono::Utc;
use std::{fs, io::Write};

struct Fixture {
    root: PathBuf,
    store: MemoryStore,
}
impl Fixture {
    fn new() -> Self {
        let root =
            std::env::temp_dir().join(format!("dogido-shared-reading-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        Self {
            store: MemoryStore::new(&root),
            root,
        }
    }
    fn save(&self, surface: &str, reading: &str) -> Value {
        self.store
            .save_reading_correction(
                &Correction {
                    surface: surface.into(),
                    reading: reading.into(),
                    wrong_reading: None,
                    explicit: true,
                },
                Some("biome:meadow"),
                Utc::now(),
                "private-session",
            )
            .unwrap()
    }
    fn replica(&self, value: Value) {
        fs::create_dir_all(self.store.shared_readings_path().parent().unwrap()).unwrap();
        fs::write(
            self.store.shared_readings_path(),
            serde_json::to_vec(&value).unwrap(),
        )
        .unwrap();
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}
fn row() -> Value {
    json!({"id":"corr_fixture","surface":"草地","reading":"くさち",
        "wrong_reading":"そうち","forbidden_readings":["そうち"],"source":"biome:meadow"})
}
fn replica(rows: Vec<Value>) -> Value {
    json!({"schema_version":1,"host_pin":"a".repeat(64),"corrections":rows})
}
fn edit() -> catalog_editor::ValidatedEdit {
    catalog_editor::Edit {
        surface: "草地".into(),
        reading: "くさち".into(),
        entry_id: None,
        wrong_reading: None,
        expected_id: None,
    }
    .validate()
    .unwrap()
}

// 見込み: 親の追記と取消を同じ読込処理で集計すれば、最新の辞書だけを私的な履歴なしで配布できる。
// 合格: 更新後の1行と公開6項目だけが残り、取消行・session・日時は出力せず元JSONLも不変。一時保存のみ。
#[test]
fn export_projects_latest_rows_without_audit_fields() {
    let fixture = Fixture::new();
    fixture.save("草地", "くさち");
    fixture.save("草地", "くさっぱら");
    let removed = fixture.save("石", "いし");
    fixture
        .store
        .remove_catalog_reading(
            &catalog_editor::Remove {
                surface: "石".into(),
                expected_id: removed["id"].as_str().unwrap().into(),
            }
            .validate()
            .unwrap(),
            Utc::now(),
        )
        .unwrap();
    let before = fs::read(fixture.store.corrections_path()).unwrap();
    let exported = fixture.store.export_readings().unwrap();
    assert_eq!(exported["schema_version"], 1);
    let rows = exported["corrections"].as_array().unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["surface"], "草地");
    assert_eq!(rows[0]["reading"], "くさっぱら");
    assert_eq!(
        rows[0]
            .as_object()
            .unwrap()
            .keys()
            .cloned()
            .collect::<BTreeSet<_>>(),
        [
            "id",
            "surface",
            "reading",
            "wrong_reading",
            "forbidden_readings",
            "source"
        ]
        .map(str::to_owned)
        .into()
    );
    assert!(!exported.to_string().contains("private-session"));
    assert_eq!(fs::read(fixture.store.corrections_path()).unwrap(), before);
    assert!(fixture.store.readings_writable());
}

// 見込み: 共有印を3つの保存入口で照合すれば、旧ローカル辞書が残っていても子機だけの編集は生じない。
// 合格: 共有行だけを読み、編集・取消・会話訂正は全てreading_read_only。空の共有辞書でも旧行へ戻らない。一時保存のみ。
#[test]
fn replica_owns_reads_and_all_write_paths_are_read_only() {
    let fixture = Fixture::new();
    let local = fixture.save("草地", "くさっぱら");
    let before = fs::read(fixture.store.corrections_path()).unwrap();
    fixture.replica(replica(vec![row()]));
    assert!(!fixture.store.readings_writable());
    assert_eq!(fixture.store.reading_corrections().unwrap(), vec![row()]);
    assert_eq!(
        fixture
            .store
            .edit_catalog_reading(&edit(), Utc::now())
            .unwrap_err()
            .to_string(),
        "reading_read_only"
    );
    let remove = catalog_editor::Remove {
        surface: "草地".into(),
        expected_id: local["id"].as_str().unwrap().into(),
    }
    .validate()
    .unwrap();
    assert_eq!(
        fixture
            .store
            .remove_catalog_reading(&remove, Utc::now())
            .unwrap_err()
            .to_string(),
        "reading_read_only"
    );
    assert_eq!(
        fixture
            .store
            .save_reading_correction(
                &Correction {
                    surface: "草地".into(),
                    reading: "くさち".into(),
                    wrong_reading: None,
                    explicit: true,
                },
                None,
                Utc::now(),
                "private-session"
            )
            .unwrap_err()
            .to_string(),
        "reading_read_only"
    );
    assert_eq!(
        fixture.store.export_readings().unwrap_err().to_string(),
        "reading_read_only"
    );
    fixture.replica(replica(vec![]));
    assert!(fixture.store.reading_corrections().unwrap().is_empty());
    assert_eq!(fs::read(fixture.store.corrections_path()).unwrap(), before);
}

// 見込み: 壊れた共有印も子機の印として扱えば、破損をきっかけに書込権限や旧読みが復活しない。
// 合格: 不正JSON・pin欠落・ディレクトリの各状態で読込失敗、編集拒否、元JSONL不変。一時ファイルの障害を模擬。
#[test]
fn malformed_or_unreadable_replica_never_falls_back() {
    let fixture = Fixture::new();
    fixture.save("草地", "くさっぱら");
    let before = fs::read(fixture.store.corrections_path()).unwrap();
    for value in [b"{".as_slice(), br#"{"schema_version":1,"corrections":[]}"#] {
        fs::write(fixture.store.shared_readings_path(), value).unwrap();
        assert!(!fixture.store.readings_writable());
        assert!(fixture.store.reading_corrections().is_err());
        assert_eq!(
            fixture
                .store
                .edit_catalog_reading(&edit(), Utc::now())
                .unwrap_err()
                .to_string(),
            "reading_read_only"
        );
    }
    fs::remove_file(fixture.store.shared_readings_path()).unwrap();
    fs::create_dir(fixture.store.shared_readings_path()).unwrap();
    assert!(!fixture.store.readings_writable());
    assert!(fixture.store.reading_corrections().is_err());
    assert_eq!(
        fixture
            .store
            .edit_catalog_reading(&edit(), Utc::now())
            .unwrap_err()
            .to_string(),
        "reading_read_only"
    );
    assert_eq!(fs::read(fixture.store.corrections_path()).unwrap(), before);
}

// 見込み: metadata失敗を未導入と見なさなければ、リンク切れや途中パスの不正でも子機の書込を解禁しない。
// 合格: 切れた共有リンクとNotADirectoryの両方で書込不可・読込失敗。実権限変更を伴わない一時パスの検査。
#[cfg(unix)]
#[test]
fn missing_symlink_target_and_metadata_error_are_read_only() {
    let fixture = Fixture::new();
    fs::create_dir(fixture.root.join("long_term")).unwrap();
    std::os::unix::fs::symlink(
        fixture.root.join("missing"),
        fixture.store.shared_readings_path(),
    )
    .unwrap();
    assert!(!fixture.store.readings_writable());
    assert!(fixture.store.reading_corrections().is_err());
    fs::remove_file(fixture.store.shared_readings_path()).unwrap();
    fs::remove_dir(fixture.root.join("long_term")).unwrap();
    fs::write(fixture.root.join("long_term"), b"not a directory").unwrap();
    assert!(!fixture.store.readings_writable());
    assert!(fixture.store.reading_corrections().is_err());
}

// 見込み: 配布時だけ履歴全体を厳密に読むことで、途中行や不正取消による古い版・空辞書の配布を避ける。
// 合格: 欠落rootと4種類の不正追記はexport失敗、通常読込は従来の有効行を維持し保存先も不変。一時JSONLのみ。
#[test]
fn export_requires_existing_parent_and_rejects_corrupt_history() {
    let fixture = Fixture::new();
    let missing = MemoryStore::new(fixture.root.join("missing"));
    assert!(missing.export_readings().is_err());
    assert!(!missing.root().exists());
    assert_eq!(
        fixture.store.export_readings().unwrap(),
        json!({"schema_version":1,"corrections":[]})
    );
    fixture.save("草地", "くさち");
    let before = fs::read(fixture.store.corrections_path()).unwrap();
    for invalid in [
        "{",
        r#"{"surface":"草地"}"#,
        r#"{"operation":"remove","surface":"草地"}"#,
        r#"{"operation":"replace","surface":"草地","reading":"くさち"}"#,
    ] {
        fs::write(fixture.store.corrections_path(), &before).unwrap();
        File::options()
            .append(true)
            .open(fixture.store.corrections_path())
            .unwrap()
            .write_all(invalid.as_bytes())
            .unwrap();
        assert!(fixture.store.export_readings().is_err(), "{invalid}");
    }
    fs::write(fixture.store.corrections_path(), &before).unwrap();
    File::options()
        .append(true)
        .open(fixture.store.corrections_path())
        .unwrap()
        .write_all(b"{")
        .unwrap();
    assert_eq!(
        fixture.store.reading_corrections().unwrap()[0]["reading"],
        "くさち"
    );
}

// 見込み: 共有外形と表記一意性を共通検証すれば、別schema・重複行・私的な追加項目を読みへ混入させない。
// 合格: 列挙した型・値・重複・未知項目を全て拒否し、正しい公開行は同じ内容で返す。メモリ内の形式検査のみ。
#[test]
fn snapshot_validates_schema_pin_unique_surfaces_and_known_shapes() {
    let good = replica(vec![row()]);
    assert_eq!(validate_reading_snapshot(&good).unwrap(), vec![row()]);
    let mut cases = vec![replica(vec![row(), row()])];
    for (key, value) in [
        ("schema_version", json!(2)),
        ("host_pin", json!("bad")),
        ("host_pin", Value::Null),
        ("session_id", json!("private")),
    ] {
        let mut invalid = good.clone();
        invalid[key] = value;
        cases.push(invalid);
    }
    for (key, value) in [
        ("surface", json!(" 草地")),
        ("reading", json!("クサチ")),
        ("wrong_reading", json!("くさち")),
        ("forbidden_readings", json!(["そうち", "そうち"])),
        ("source", json!(12)),
        ("id", Value::Null),
        ("created_at", json!("private")),
    ] {
        let mut invalid = good.clone();
        invalid["corrections"][0][key] = value;
        cases.push(invalid);
    }
    for invalid in cases {
        assert!(validate_reading_snapshot(&invalid).is_err(), "{invalid}");
    }
}

// 見込み: 入出力とも1 MiBで止めれば、共有ファイルの肥大化を子機の辞書処理へ持ち込まない。
// 合格: 大量の有効現行行のexportと、末尾空白だけで上限を超えた子機ファイルがサイズエラー。一時保存のみ。
#[test]
fn export_and_replica_enforce_byte_limit() {
    let fixture = Fixture::new();
    fs::create_dir(fixture.root.join("long_term")).unwrap();
    let mut file = File::create(fixture.store.corrections_path()).unwrap();
    for index in 0..2000 {
        let mut current = row();
        current["surface"] = json!(format!("草地{index}"));
        current["source"] = json!("x".repeat(500));
        writeln!(file, "{current}").unwrap();
    }
    drop(file);
    assert_eq!(
        fixture.store.export_readings().unwrap_err().to_string(),
        "reading_snapshot_too_large"
    );
    let mut bytes = serde_json::to_vec(&replica(vec![])).unwrap();
    bytes.resize(MAX_SHARED_READINGS_BYTES + 1, b' ');
    fs::write(fixture.store.shared_readings_path(), bytes).unwrap();
    assert_eq!(
        fixture.store.reading_corrections().unwrap_err().to_string(),
        "reading_snapshot_too_large"
    );
}
