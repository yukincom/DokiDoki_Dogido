use super::*;
use serde::Deserialize;

#[derive(Deserialize)]
struct Case {
    name: String,
    files: HashMap<String, String>,
    links: HashMap<String, String>,
    query: String,
    datasets: Vec<String>,
    types: Vec<String>,
    registries: Vec<String>,
    limit: usize,
    expected: Value,
    error: String,
}
fn cases() -> Vec<Case> {
    serde_json::from_str(include_str!("../../fixtures/minecraft-reader.json")).unwrap()
}
struct Temporary(PathBuf);
impl Temporary {
    fn new(case: &Case) -> Self {
        let root =
            std::env::temp_dir().join(format!("dogido-minecraft-reader-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        for (name, text) in &case.files {
            let path = root.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text).unwrap();
        }
        for (name, target) in &case.links {
            #[cfg(unix)]
            std::os::unix::fs::symlink(root.join(target), root.join(name)).unwrap();
            #[cfg(windows)]
            std::os::windows::fs::symlink_file(root.join(target), root.join(name)).unwrap();
        }
        Self(root)
    }
    fn open(&self, stop: Stop) -> Result<Minecraft> {
        Minecraft::open(
            &self.0.join("cache"),
            &self.0.join("source_lock.json"),
            stop,
        )
    }
    fn snapshot(&self) -> PathBuf {
        self.0.join("cache/mcjava-1.21.11-synthetic-n1")
    }
}
impl Drop for Temporary {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn strings(values: &[String]) -> Vec<&str> {
    values.iter().map(String::as_str).collect()
}

#[test]
fn synthetic_searches_and_rejections_match_fixture() {
    for case in cases() {
        let tmp = Temporary::new(&case);
        let result = tmp.open(Stop::default()).and_then(|reader| {
            reader.search(
                &case.query,
                &strings(&case.datasets),
                &strings(&case.types),
                &strings(&case.registries),
                case.limit,
            )
        });
        match result {
            Ok(records) => {
                assert!(
                    case.error.is_empty(),
                    "case {} should fail with {}",
                    case.name,
                    case.error
                );
                assert_eq!(
                    serde_json::to_value(records).unwrap(),
                    case.expected,
                    "case {}",
                    case.name
                );
            }
            Err(error) => {
                assert!(!case.error.is_empty(), "case {}: {error:#}", case.name);
                assert_eq!(
                    crate::knowledge::catalog::error_class(&error),
                    case.error,
                    "case {}: {error:#}",
                    case.name
                );
            }
        }
    }
}
fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn publish_manifest(tmp: &Temporary) {
    let path = tmp.snapshot().join("manifest.json");
    let mut manifest: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    for row in manifest["datasets"].as_array_mut().unwrap() {
        for (name, hash) in [("path", "sha256"), ("index_path", "index_sha256")] {
            row[hash] =
                sha(&fs::read(tmp.snapshot().join(row[name].as_str().unwrap())).unwrap()).into();
        }
    }
    let bytes = serde_json::to_vec(&manifest).unwrap();
    fs::write(path, &bytes).unwrap();
    let current_path = tmp.0.join("cache/current.json");
    let mut current: Value = serde_json::from_slice(&fs::read(&current_path).unwrap()).unwrap();
    current["manifest_sha256"] = sha(&bytes).into();
    fs::write(current_path, serde_json::to_vec(&current).unwrap()).unwrap();
}
#[test]
fn running_reader_rejects_atomic_artifact_replacement_and_fresh_open_reads_new_generation() {
    let case = cases().remove(0);
    for artifact in [
        "version_information.jsonl",
        "version_information.index.jsonl",
    ] {
        let tmp = Temporary::new(&case);
        let reader = tmp.open(Stop::default()).unwrap();
        let path = tmp.snapshot().join(artifact);
        let previous = fs::metadata(&path).unwrap();
        let bytes = fs::read_to_string(&path)
            .unwrap()
            .replace("配布版情報", "別の版情報");
        let replacement = path.with_extension("next");
        fs::write(&replacement, bytes).unwrap();
        File::options()
            .write(true)
            .open(&replacement)
            .unwrap()
            .set_times(fs::FileTimes::new().set_modified(previous.modified().unwrap()))
            .unwrap();
        fs::rename(replacement, &path).unwrap();
        assert_eq!(fs::metadata(&path).unwrap().len(), previous.len());
        assert!(reader.search("", &[], &[], &[], 20).is_err());
        assert!(tmp.open(Stop::default()).is_err());
        publish_manifest(&tmp);
        let rows = tmp
            .open(Stop::default())
            .unwrap()
            .search("", &[], &[], &[], 20)
            .unwrap();
        assert_eq!(rows.len(), 9);
        if artifact.ends_with("information.jsonl") {
            assert!(
                rows.iter()
                    .any(|r| r["id"] == "version.synthetic" && r["title_ja"] == "別の版情報")
            );
        }
    }
}
#[test]
fn cancelled_reader_stops_before_open_and_after_open() {
    let tmp = Temporary::new(&cases().remove(0));
    let stop = Stop::default();
    stop.cancel();
    assert!(tmp.open(stop).is_err());
    let stop = Stop::default();
    let reader = tmp.open(stop.clone()).unwrap();
    stop.cancel();
    assert!(reader.search("", &[], &[], &[], 20).is_err());
}
#[cfg(unix)]
#[test]
fn cache_root_and_snapshot_symlinks_are_rejected() {
    use std::os::unix::fs::symlink;
    let tmp = Temporary::new(&cases().remove(0));
    symlink(tmp.0.join("cache"), tmp.0.join("cache-link")).unwrap();
    assert!(
        Minecraft::open(
            &tmp.0.join("cache-link"),
            &tmp.0.join("source_lock.json"),
            Stop::default()
        )
        .is_err()
    );
    let original = tmp.snapshot();
    let moved = tmp.0.join("outside-snapshot");
    fs::rename(&original, &moved).unwrap();
    symlink(moved, original).unwrap();
    assert!(tmp.open(Stop::default()).is_err());
}
