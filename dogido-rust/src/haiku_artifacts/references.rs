//! 素材を処分する直前の参照照合。読めない領域や壊れたJSONを「参照なし」にしない。
//! 呼出側が本棚・保存のロックを保持する。ここでは修復・保存・削除しない。
use super::checked_dir;
use crate::bookshelf::research_archive::{self, ArchivedSnapshot};
use anyhow::{Context, Result, ensure};
use serde_json::Value;
use std::{
    fs::{self, File},
    io::{BufRead, BufReader, ErrorKind},
    path::Path,
};

fn mentions(value: &Value, needle: &str) -> bool {
    match value {
        Value::String(v) => v.contains(needle),
        Value::Array(v) => v.iter().any(|row| mentions(row, needle)),
        Value::Object(v) => v
            .iter()
            .any(|(key, row)| key.contains(needle) || mentions(row, needle)),
        _ => false,
    }
}

fn directory(path: &Path) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(_) => {
            checked_dir(path, false)?;
            Ok(true)
        }
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e.into()),
    }
}

fn open_optional(parent: &Path, name: &str) -> Result<Option<File>> {
    #[cfg(unix)]
    {
        use crate::bookshelf::research_archive::safe::Dir;
        let dir = Dir::open(parent)?;
        match dir.existing_file(name) {
            Ok(file) => Ok(Some(file)),
            Err(e)
                if e.downcast_ref::<std::io::Error>()
                    .is_some_and(|e| e.kind() == ErrorKind::NotFound) =>
            {
                Ok(None)
            }
            Err(e) => Err(e),
        }
    }
    #[cfg(not(unix))]
    {
        let path = parent.join(name);
        crate::poem_trash::regular(&path)?;
        match File::open(path) {
            Ok(file) => Ok(Some(file)),
            Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e.into()),
        }
    }
}

fn object(file: File) -> Result<Value> {
    let value: Value = serde_json::from_reader(BufReader::new(file))?;
    ensure!(value.is_object(), "artifact_reference_invalid_object");
    Ok(value)
}

fn poems(dir: &Path, needle: &str) -> Result<bool> {
    let long = dir.join("long_term");
    if !directory(&long)? {
        return Ok(false);
    }
    let mut found = false;
    for name in ["haiku_entries.jsonl", "haiku_revisions.jsonl"] {
        let Some(file) = open_optional(&long, name)? else {
            continue;
        };
        for line in BufReader::new(file).lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            let row: Value = serde_json::from_str(&line)
                .with_context(|| format!("invalid reference JSONL: {name}"))?;
            ensure!(row.is_object(), "artifact_reference_invalid_row");
            found |= mentions(&row, needle);
        }
    }
    if let Some(file) = open_optional(&long, "poem_trash.json")? {
        found |= mentions(&object(file)?, needle);
    }
    Ok(found)
}

pub(super) fn referenced(memory: &Path, needle: &str) -> Result<bool> {
    ensure!(!needle.is_empty(), "artifact_reference_empty_id");
    checked_dir(memory, false)?;
    let mut found = poems(memory, needle)?;
    let sessions = memory.join("sessions");
    if directory(&sessions)? {
        for entry in fs::read_dir(&sessions)? {
            let entry = entry?;
            let kind = entry.file_type()?;
            ensure!(!kind.is_symlink(), "artifact_reference_linked_session");
            if kind.is_dir() {
                checked_dir(&entry.path(), false)?;
                found |= poems(&entry.path(), needle)?;
            }
        }
    }

    let marker = open_optional(memory, ".dogido-research-archive.json")?
        .map(object)
        .transpose()?;
    let shelf = memory.join("bookshelf");
    if !directory(&shelf)? {
        ensure!(marker.is_none(), "artifact_reference_shelf_missing");
        return Ok(found);
    }
    let state = open_optional(&shelf, "state.json")?
        .map(object)
        .transpose()?;
    let heads = state
        .as_ref()
        .and_then(|v| v.get("researchSnapshots"))
        .map(|v| {
            v.as_object()
                .context("artifact_reference_invalid_snapshot_index")
        })
        .transpose()?;
    if let Some(state) = &state {
        for key in [
            "catalog",
            "poems",
            "photoTitles",
            "placements",
            "photoTrash",
            "minecraftPhotos",
            "researchOriginals",
            "playerPhotos",
            "journeys",
        ] {
            ensure!(
                state.get(key).is_none_or(Value::is_object),
                "artifact_reference_invalid_shelf_table: {key}"
            );
        }
        found |= mentions(state, needle);
    }

    let archive = research_archive::load(memory)?;
    if let Some(marker) = marker {
        let current = archive
            .as_ref()
            .context("artifact_reference_config_missing")?;
        ensure!(
            current.config().root == memory
                && marker["archive_id"].as_str() == Some(current.config().archive_id.as_str()),
            "artifact_reference_archive_changed"
        );
    }
    if let Some(heads) = heads.filter(|h| !h.is_empty()) {
        for (id, row) in heads {
            let head: ArchivedSnapshot = serde_json::from_value(row.clone())?;
            ensure!(head.poem_id == *id, "artifact_reference_snapshot_owner");
            let record = archive
                .as_ref()
                .context("artifact_reference_archive_unavailable")?
                .read_snapshot(&head)?;
            ensure!(
                record["id"].as_str() == Some(id.as_str()),
                "artifact_reference_snapshot_record_owner"
            );
            found |= mentions(&record, needle);
        }
    } else if let Some(archive) = archive {
        // A new empty shelf may not have an index yet. Once a year directory exists,
        // losing its head index is not evidence that those archived poems have no references.
        for entry in fs::read_dir(&archive.config().root)? {
            let name = entry?.file_name();
            ensure!(
                !name
                    .to_str()
                    .is_some_and(|s| s.len() == 4 && s.bytes().all(|c| c.is_ascii_digit())),
                "artifact_reference_snapshot_index_missing"
            );
        }
    }
    Ok(found)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{os::unix::fs::symlink, path::PathBuf};
    struct Fixture {
        root: PathBuf,
        memory: PathBuf,
    }
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir()
                .canonicalize()
                .unwrap()
                .join(format!("dogido-reference-test-{}", uuid::Uuid::new_v4()));
            let memory = root.join("memory");
            fs::create_dir_all(memory.join("bookshelf")).unwrap();
            fs::create_dir_all(memory.join("sessions/one/long_term")).unwrap();
            Self { root, memory }
        }
        fn state(&self) -> PathBuf {
            self.memory.join("bookshelf/state.json")
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    // 見込み: 一行でも破損、無効UTF-8、途中の台帳なら参照なしと断定しない。
    // 合格: 先にIDが見つかっていてもErr、手動ごみ箱に有効なIDを含む場合はtrue。仮保管庫だけを読む。
    #[test]
    fn undecodable_state_and_truncated_rows_hold_the_candidate() {
        let f = Fixture::new();
        let rows = f.memory.join("sessions/one/long_term/haiku_entries.jsonl");
        fs::write(&rows, b"{\"note\":\"token-1\"}\n").unwrap();
        fs::write(f.state(), b"{\"photoTrash\":").unwrap();
        assert!(referenced(&f.memory, "token-1").is_err());
        fs::write(f.state(), b"{\"photoTrash\":{\"user-token-1\":{}}}").unwrap();
        fs::write(&rows, b"\xff\n").unwrap();
        assert!(referenced(&f.memory, "token-1").is_err());
        fs::write(&rows, b"{\"ok\":true}\n{\"partial\":").unwrap();
        assert!(referenced(&f.memory, "unreferenced").is_err());
        fs::write(&rows, b"").unwrap();
        assert!(referenced(&f.memory, "token-1").unwrap());
        fs::remove_dir_all(f.memory.join("bookshelf")).unwrap();
        fs::write(
            f.memory.join(".dogido-research-archive.json"),
            br#"{"archive_id":"unavailable"}"#,
        )
        .unwrap();
        assert!(
            referenced(&f.memory, "missing").is_err(),
            "marked archive lost its bookshelf"
        );
    }

    // 見込み: 台帳のhash付きheadが本体JSONLにない句を指していても、その保存済み本文を参照に数える。
    // 合格: 退避先パス内のIDを検出し、無関係IDはfalse。headの内容破損はErr。仮SSDで実保全形式を検証する。
    #[test]
    fn follows_and_validates_archive_only_references() {
        let f = Fixture::new();
        let root = f.root.join("archive");
        fs::create_dir(&root).unwrap();
        let config =
            research_archive::Config::new(root.clone(), uuid::Uuid::new_v4().to_string(), None)
                .unwrap();
        let archive = research_archive::configure(&f.memory, &config).unwrap();
        let id = "root:fixture-poem";
        let poem = archive
            .for_poem(id, "2026-10-27T01:02:03Z".parse().unwrap())
            .unwrap();
        let head=poem.snapshot(&json!({"id":id,"original":{"internal_capture":{"file":"chatcmpl-fixture/hidden.f32"}}})).unwrap();
        fs::write(
            f.state(),
            serde_json::to_vec(&json!({"researchSnapshots":{(id):head}})).unwrap(),
        )
        .unwrap();
        assert!(referenced(&f.memory, "chatcmpl-fixture").unwrap());
        assert!(!referenced(&f.memory, "chatcmpl-different").unwrap());
        fs::write(root.join(&head.relative_path), b"{\"other\":true}").unwrap();
        assert!(referenced(&f.memory, "chatcmpl-different").is_err());
    }

    // 見込み: 外部へ向くsessionや原本は列挙を打ち切るが、Finderの付随ファイルは原本として解釈しない。
    // 合格: 通常のAppleDoubleだけならfalse、session子・sessions親のリンクはそれぞれErr。仮の外部ディレクトリで検証。
    #[test]
    fn sessions_and_source_links_hold_but_finder_metadata_is_ignored() {
        let f = Fixture::new();
        let sessions = f.memory.join("sessions");
        fs::write(sessions.join("._one"), b"\0binary").unwrap();
        fs::write(
            sessions.join("one/long_term/._haiku_entries.jsonl"),
            b"\0binary",
        )
        .unwrap();
        assert!(!referenced(&f.memory, "missing").unwrap());
        let other = f.root.join("other");
        fs::create_dir(&other).unwrap();
        let link = sessions.join("elsewhere");
        symlink(&other, &link).unwrap();
        assert!(referenced(&f.memory, "missing").is_err());
        fs::remove_file(&link).unwrap();
        let leaf = sessions.join("one/long_term/haiku_entries.jsonl");
        symlink(other.join("missing"), &leaf).unwrap();
        assert!(referenced(&f.memory, "missing").is_err());
        fs::remove_file(leaf).unwrap();
        fs::rename(&sessions, f.root.join("old_sessions")).unwrap();
        symlink(other, sessions).unwrap();
        assert!(referenced(&f.memory, "missing").is_err());
    }
}
