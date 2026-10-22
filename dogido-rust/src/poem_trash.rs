//! 句の削除は元記録を30日保持し、通常の想起・編集から即時に除外する。
//! 期限後の整理だけ正本を置換する。全writerが同じ固定ロックを使う。
use anyhow::{Context, Result, ensure};
use chrono::{DateTime, Duration, Utc};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::Write,
    path::Path,
};
pub const RETENTION_DAYS: i64 = 30;

pub(crate) fn regular(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(m) => ensure!(m.is_file() && !m.file_type().is_symlink(), "unsafe_file"),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e.into()),
    }
    Ok(())
}
pub(crate) fn lock(root: &Path) -> Result<File> {
    let dir = root.join("long_term");
    fs::create_dir_all(&dir)?;
    ensure!(
        !fs::symlink_metadata(&dir)?.file_type().is_symlink(),
        "unsafe_directory"
    );
    let path = dir.join(".poems.lock");
    regular(&path)?;
    crate::haiku_record::locked_file(&path)
}
pub(crate) fn read_json(path: &Path, default: Value) -> Result<Value> {
    regular(path)?;
    match fs::read(path) {
        Ok(b) => Ok(serde_json::from_slice(&b)?),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(default),
        Err(e) => Err(e.into()),
    }
}
pub(crate) fn atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    regular(path)?;
    let dir = path.parent().context("missing parent")?;
    fs::create_dir_all(dir)?;
    let tmp = dir.join(format!(".bookshelf-{}.tmp", uuid::Uuid::new_v4()));
    let result = (|| -> Result<()> {
        let mut f = OpenOptions::new().create_new(true).write(true).open(&tmp)?;
        f.write_all(bytes)?;
        f.sync_all()?;
        fs::rename(&tmp, path)?;
        File::open(dir)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}
pub(crate) fn raw_rows(root: &Path, filename: &str) -> Result<Vec<Value>> {
    let path = root.join("long_term").join(filename);
    regular(&path)?;
    let body = match fs::read_to_string(path) {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(vec![]),
        Err(e) => return Err(e.into()),
    };
    body.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| Ok(serde_json::from_str(l)?))
        .collect()
}
pub(crate) fn journal(root: &Path) -> Result<BTreeMap<String, Value>> {
    Ok(serde_json::from_value(read_json(
        &root.join("long_term/poem_trash.json"),
        json!({}),
    )?)?)
}
fn save(root: &Path, rows: &BTreeMap<String, Value>) -> Result<()> {
    atomic(
        &root.join("long_term/poem_trash.json"),
        &serde_json::to_vec(rows)?,
    )
}
pub fn is_deleted(root: &Path, id: &str) -> Result<bool> {
    Ok(journal(root)?.contains_key(id))
}
pub(crate) fn filter(root: &Path, filename: &str, mut rows: Vec<Value>) -> Result<Vec<Value>> {
    let deleted = journal(root)?;
    let key = if filename == "haiku_revisions.jsonl" {
        "haiku_id"
    } else {
        "id"
    };
    rows.retain(|r| r[key].as_str().is_none_or(|id| !deleted.contains_key(id)));
    Ok(rows)
}
pub fn source(root: &Path, dir: &Path) -> String {
    if root == dir {
        "root".into()
    } else {
        format!("session:{}", dir.file_name().unwrap().to_string_lossy())
    }
}
pub fn resolve(root: &Path, key: &str) -> Result<(std::path::PathBuf, String)> {
    for dir in crate::poem_book::directories(root)? {
        let prefix = format!("{}:", source(root, &dir));
        if let Some(id) = key.strip_prefix(&prefix) {
            ensure!(
                !id.is_empty() && !id.contains(['/', '\\', '\0']),
                "invalid_poem"
            );
            return Ok((dir, id.into()));
        }
    }
    anyhow::bail!("unknown_poem")
}
pub fn change(root: &Path, key: &str, restore: bool, now: DateTime<Utc>) -> Result<()> {
    let (dir, id) = resolve(root, key)?;
    let _lock = lock(&dir)?;
    let mut rows = journal(&dir)?;
    if restore {
        let row = rows.get(&id).context("not_in_trash")?;
        let expires =
            DateTime::parse_from_rfc3339(row["expiresAt"].as_str().context("trash_time")?)?;
        ensure!(now < expires && row["purged"] != true, "recovery_expired");
        rows.remove(&id);
    } else if let std::collections::btree_map::Entry::Vacant(slot) = rows.entry(id.clone()) {
        ensure!(
            raw_rows(&dir, "haiku_entries.jsonl")?
                .iter()
                .any(|r| r["id"] == id),
            "unknown_poem"
        );
        slot.insert(json!({"deletedAt":now.to_rfc3339(),"expiresAt":(now+Duration::days(RETENTION_DAYS)).to_rfc3339(),"purged":false}));
    }
    save(&dir, &rows)
}
pub fn purge(root: &Path, now: DateTime<Utc>) -> Result<()> {
    for dir in crate::poem_book::directories(root)? {
        // 未削除の保管場所にはロック等も作らない。
        if !dir.join("long_term/poem_trash.json").exists() {
            continue;
        }
        let _lock = lock(&dir)?;
        let mut rows = journal(&dir)?;
        let expired: Vec<_> = rows
            .iter()
            .filter_map(|(id, r)| {
                let at = DateTime::parse_from_rfc3339(r["expiresAt"].as_str()?).ok()?;
                (r["purged"] != true && now >= at).then_some(id.clone())
            })
            .collect();
        if expired.is_empty() {
            continue;
        }
        for (file, field) in [
            ("haiku_entries.jsonl", "id"),
            ("haiku_revisions.jsonl", "haiku_id"),
        ] {
            let path = dir.join("long_term").join(file);
            if !path.exists() {
                continue;
            }
            let mut bytes = vec![];
            for r in raw_rows(&dir, file)? {
                if r[field]
                    .as_str()
                    .is_some_and(|id| expired.iter().any(|x| x == id))
                {
                    continue;
                }
                serde_json::to_writer(&mut bytes, &r)?;
                bytes.push(b'\n');
            }
            atomic(&path, &bytes)?;
        }
        // 削除IDだけ保持して、遅延した保存の再送による復活を防ぐ。
        for id in expired {
            rows.get_mut(&id).unwrap()["purged"] = true.into();
        }
        save(&dir, &rows)?;
    }
    Ok(())
}
