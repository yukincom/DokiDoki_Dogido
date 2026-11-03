//! 完成句の正本に移し終えた採取物のwriter管理ファイルだけを整理する。
//! 呼出側がSSDの正本・句・受領札と保全した数値・manifestを検証してから渡す。
use anyhow::{Result, ensure};
use serde_json::Value;
use std::path::Path;

/// trueは管理ファイルを回収した場合。保全済み数値や句の本文には触れない。
pub(crate) fn retire_archived_writer(
    input: &Path,
    token: &str,
    response: &str,
    archived_manifest: &Value,
) -> Result<bool> {
    ensure!(
        uuid::Uuid::parse_str(token)?.to_string() == token
            && !response.is_empty()
            && response.len() <= 160
            && response
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'),
        "archive_writer_invalid_id"
    );
    if archived_manifest["schema"] != "dogido.internal_capture.v1"
        || archived_manifest["backend"] != "llama.cpp"
        || archived_manifest["capture_request_id"] != token
        || archived_manifest["response_id"] != response
        || archived_manifest["writer_closed"] != true
        || !archived_manifest["writer_instance"]
            .as_str()
            .is_some_and(|s| !s.is_empty())
        || !matches!(
            archived_manifest["status"].as_str(),
            Some("complete" | "partial")
        )
    {
        return Ok(false);
    }
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    {
        unix::retire(input, token, response, archived_manifest)
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        let _ = input;
        Ok(false)
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
mod unix {
    use super::*;
    use anyhow::Context;
    use std::{
        ffi::{CStr, CString},
        fs::File,
        io::{self, Read},
        os::{
            fd::{AsRawFd, FromRawFd, IntoRawFd},
            unix::{ffi::OsStrExt, fs::MetadataExt},
        },
    };
    const LOCK: &str = ".writer.lock";

    // Mutations use directory descriptors pinned before opening either leaf. Renaming
    // or replacing a parent with a symlink cannot redirect an unlink into its target.
    fn child(parent: &File, name: &CStr, flags: i32) -> io::Result<File> {
        let fd = unsafe {
            libc::openat(
                parent.as_raw_fd(),
                name.as_ptr(),
                flags | libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(unsafe { File::from_raw_fd(fd) })
    }
    fn open_directory_path(path: &Path) -> Result<File> {
        use std::path::Component;
        ensure!(path.is_absolute(), "archive_writer_relative_root");
        let mut dir = File::open("/")?;
        for part in path.components() {
            match part {
                Component::RootDir => {}
                Component::Normal(name) => {
                    dir = child(&dir, &CString::new(name.as_bytes())?, libc::O_DIRECTORY)?
                }
                _ => anyhow::bail!("archive_writer_noncanonical_root"),
            }
        }
        Ok(dir)
    }
    // readdir works on the same directory inode as flock/unlinkat, even if its path is replaced.
    fn only_entry(folder: &File) -> io::Result<Option<Vec<u8>>> {
        let fd = folder.try_clone()?.into_raw_fd();
        let stream = unsafe { libc::fdopendir(fd) };
        if stream.is_null() {
            let error = io::Error::last_os_error();
            unsafe {
                libc::close(fd);
            }
            return Err(error);
        }
        let mut one = None;
        let result = loop {
            #[cfg(target_os = "macos")]
            unsafe {
                *libc::__error() = 0;
            }
            #[cfg(target_os = "linux")]
            unsafe {
                *libc::__errno_location() = 0;
            }
            let entry = unsafe { libc::readdir(stream) };
            if entry.is_null() {
                let err = io::Error::last_os_error();
                break if err.raw_os_error() == Some(0) {
                    Ok(one)
                } else {
                    Err(err)
                };
            }
            let name = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_bytes();
            if name == b"." || name == b".." {
                continue;
            }
            if one.is_some() {
                break Ok(None);
            }
            one = Some(name.to_vec());
        };
        unsafe {
            libc::closedir(stream);
        }
        result
    }
    fn same(left: &File, right: &File) -> io::Result<bool> {
        let l = left.metadata()?;
        let r = right.metadata()?;
        Ok((l.dev(), l.ino()) == (r.dev(), r.ino()))
    }
    fn unlink_owned(dir: &File, name: &CStr, expected: &File) -> Result<()> {
        let actual = child(dir, name, 0)?;
        ensure!(
            same(&actual, expected)? && actual.metadata()?.is_file(),
            "archive_writer_file_changed"
        );
        if unsafe { libc::unlinkat(dir.as_raw_fd(), name.as_ptr(), 0) } != 0 {
            return Err(io::Error::last_os_error().into());
        }
        Ok(())
    }
    fn rename_lock(folder: &File, to: &CStr) -> Result<()> {
        let fd = folder.as_raw_fd();
        #[cfg(target_os = "macos")]
        let rc = unsafe {
            libc::renameatx_np(
                fd,
                c".writer.lock".as_ptr(),
                fd,
                to.as_ptr(),
                libc::RENAME_EXCL,
            )
        };
        #[cfg(target_os = "linux")]
        let rc = unsafe {
            libc::renameat2(
                fd,
                c".writer.lock".as_ptr(),
                fd,
                to.as_ptr(),
                libc::RENAME_NOREPLACE,
            )
        };
        if rc != 0 {
            return Err(io::Error::last_os_error().into());
        }
        folder.sync_all()?;
        Ok(())
    }
    pub(super) fn retire(input: &Path, token: &str, response: &str, m: &Value) -> Result<bool> {
        let root = open_directory_path(input)?;
        let id = CString::new(response)?;
        let folder = child(&root, &id, libc::O_DIRECTORY)?;
        let index = child(&root, c"_requests", libc::O_DIRECTORY)?;
        let held = format!(".archived-writer-{token}.lock");
        let Some(name) = only_entry(&folder)? else {
            return Ok(false);
        };
        let recovery = name == held.as_bytes();
        if !recovery && name != LOCK.as_bytes() {
            return Ok(false);
        }
        let saved = CString::new(held)?;
        let lease = child(&folder, if recovery { &saved } else { c".writer.lock" }, 0)?;
        ensure!(
            lease.metadata()?.is_file() && lease.metadata()?.len() == 0,
            "archive_writer_bad_lock"
        );
        if unsafe { libc::flock(lease.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Ok(false);
        }
        ensure!(
            same(&folder, &child(&root, &id, libc::O_DIRECTORY)?)?
                && same(&index, &child(&root, c"_requests", libc::O_DIRECTORY)?)?,
            "archive_writer_directory_changed"
        );
        let marker = CString::new(format!("{token}.json"))?;
        let proof = match child(&index, &marker, 0) {
            Ok(file) => {
                ensure!(
                    file.metadata()?.is_file() && file.metadata()?.len() <= 8192,
                    "archive_writer_bad_marker"
                );
                let mut bytes = Vec::new();
                (&file).take(8193).read_to_end(&mut bytes)?;
                ensure!(bytes.len() <= 8192, "archive_writer_large_marker");
                let row: Value = serde_json::from_slice(&bytes)?;
                if row["schema"] != "dogido.capture_writer.v1"
                    || [
                        "capture_request_id",
                        "response_id",
                        "writer_instance",
                        "status",
                        "writer_closed",
                    ]
                    .iter()
                    .any(|k| row[*k] != m[*k])
                {
                    return Ok(false);
                }
                Some(file)
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound && recovery => None,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(e) => return Err(e.into()),
        };
        // Only a checked, closed writer can establish this token-specific recovery name.
        // After a crash it remains until the token marker removal is durable.
        if !recovery {
            rename_lock(&folder, &saved)?;
        }
        ensure!(
            same(&lease, &child(&folder, &saved, 0)?)?,
            "archive_writer_lock_changed"
        );
        if let Some(file) = proof {
            unlink_owned(&index, &marker, &file)?;
        }
        index.sync_all().context("archive_writer_index_sync")?;
        unlink_owned(&folder, &saved, &lease)?;
        folder.sync_all()?;
        Ok(true)
    }
}

#[cfg(all(test, any(target_os = "linux", target_os = "macos")))]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{
        fs,
        fs::File,
        os::unix::{fs::symlink, io::AsRawFd},
        path::PathBuf,
    };
    const TOKEN: &str = "2c338301-e0ca-4c2f-9a84-12b10bca5404";
    const ID: &str = "fixture-archived-response";

    struct Case {
        root: PathBuf,
        manifest: Value,
    }
    impl Case {
        fn new() -> Self {
            let root = std::env::temp_dir()
                .canonicalize()
                .unwrap()
                .join(format!("dogido-archived-writer-{}", uuid::Uuid::new_v4()));
            fs::create_dir(&root).unwrap();
            for d in ["_requests", ID] {
                fs::create_dir(root.join(d)).unwrap();
            }
            let manifest = json!({"schema":"dogido.internal_capture.v1","backend":"llama.cpp","capture_request_id":TOKEN,"response_id":ID,
                "writer_instance":"fixture-boot","writer_closed":true,"status":"partial"});
            let case = Self { root, manifest };
            File::create(case.lock()).unwrap();
            let mut marker = case.manifest.clone();
            marker["schema"] = "dogido.capture_writer.v1".into();
            fs::write(case.marker(), serde_json::to_vec(&marker).unwrap()).unwrap();
            case
        }
        fn source(&self) -> PathBuf {
            self.root.join(ID)
        }
        fn marker(&self) -> PathBuf {
            self.root.join("_requests").join(format!("{TOKEN}.json"))
        }
        fn lock(&self) -> PathBuf {
            self.source().join(".writer.lock")
        }
        fn holding(&self) -> PathBuf {
            self.source().join(format!(".archived-writer-{TOKEN}.lock"))
        }
        fn retire(&self) -> Result<bool> {
            retire_archived_writer(&self.root, TOKEN, ID, &self.manifest)
        }
    }
    impl Drop for Case {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    // 見込み: 正本確認後でも入力に数値・不明ファイルがあれば何も動かさない。
    // 合格: ファイルと札が残る。writerの排他中も保留し、解除後だけ管理2ファイルを回収。実SSDは使わない。
    #[test]
    fn archived_source_waits_for_empty_payload_and_writer_release() {
        let c = Case::new();
        for name in ["hidden.f32", "manifest.json", "other"] {
            let p = c.source().join(name);
            fs::write(&p, "retain").unwrap();
            assert!(!c.retire().unwrap());
            assert_eq!(fs::read(&p).unwrap(), b"retain");
            assert!(c.marker().is_file());
            fs::remove_file(p).unwrap();
        }
        let locked = File::open(c.lock()).unwrap();
        assert_eq!(
            unsafe { libc::flock(locked.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) },
            0
        );
        assert!(!c.retire().unwrap());
        assert!(c.lock().is_file());
        drop(locked);
        assert!(c.retire().unwrap());
        assert!(!c.marker().exists() && !c.lock().exists() && c.source().is_dir());
        assert!(!c.retire().unwrap());
    }

    // 見込み: マーカー撤去の前後で停止しても、同じtokenの一時lockからだけ回収を再開できる。
    // 合格: 一時フォルダの2停止点から完了し、別instance・旧式の証拠ではファイルを保持する。
    #[test]
    fn archived_source_resumes_without_guessing_a_missing_marker() {
        for already_removed in [false, true] {
            let c = Case::new();
            fs::rename(c.lock(), c.holding()).unwrap();
            if already_removed {
                fs::remove_file(c.marker()).unwrap();
            }
            assert!(c.retire().unwrap());
            assert!(!c.holding().exists() && !c.marker().exists());
        }
        let mut c = Case::new();
        c.manifest["writer_instance"] = "different".into();
        assert!(!c.retire().unwrap());
        assert!(c.marker().is_file());
        c.manifest["capture_request_id"] = Value::Null;
        assert!(!c.retire().unwrap());
        assert!(c.lock().is_file());
    }

    // 見込み: 応答フォルダ・札・lockがシンボリックリンクへ差し替われば、外の対象を開いて消去しない。
    // 合格: 一時フォルダの各差替えで失敗し、移し替えた外側の元ファイルと札が保持される。
    #[test]
    fn archived_source_does_not_follow_replaced_links() {
        for kind in ["marker", "lock", "source", "index"] {
            let c = Case::new();
            let target = match kind {
                "marker" => c.marker(),
                "lock" => c.lock(),
                "source" => c.source(),
                _ => c.root.join("_requests"),
            };
            let outside = c.root.join("outside");
            fs::rename(&target, &outside).unwrap();
            symlink(&outside, &target).unwrap();
            assert!(c.retire().is_err(), "{kind}");
            assert!(
                outside.exists() && c.marker().is_file() && c.lock().is_file(),
                "{kind}"
            );
        }
    }
}
