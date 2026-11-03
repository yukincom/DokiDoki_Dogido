//! 発句ごとの素材と保存結果。取消は生成の終了、保存待ちは完成した発句として区別する。
//! 自分の永続台帳で帰属・writer終了を証明できた素材だけを整理する。
mod references;
use crate::{haiku_record::{Emission, MemoryStore}, screenshot::Receipt};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs::{self, File, OpenOptions}, path::{Path, PathBuf}, sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}}};

pub const CAPABILITY: &str = "client.screenshot.lifecycle.v1";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum State { Active, Discard, SavePending, SaveFailed, Saved, Conflict }

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Request {
    request_id: String, token: String, response_id: Option<String>, removed: bool,
    #[serde(default)] removal: Option<Removal>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Removal { marker: Value, manifest_hash: String, hidden_hash: String, hidden_bytes: u64 }


#[derive(Clone, Debug, Serialize, Deserialize)]
struct Photo {
    command_id: String,
    context: crate::body::contracts::Context,
    issued_at: chrono::DateTime<chrono::Utc>,
    receipt: Option<Receipt>,
    #[serde(default)] failure: Option<Value>,
    registered: bool,
    decision_id: String,
    settled: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Record {
    schema_version: u32,
    archive_id: Option<String>,
    run_id: String,
    session_id: String,
    job_id: String,
    instance_id: String,
    player: String,
    state: State,
    emission: Option<Emission>,
    entry_id: Option<String>,
    #[serde(default)] entry_hash: Option<String>,
    photo: Option<Photo>,
    requests: Vec<Request>,
    error: Option<String>,
}

/// I/O処理は同期workerで呼ぶ。commandsは短いキャッシュ参照だけで観測のmutexを塞がない。
pub struct Manager {
    memory: PathBuf,
    capture: Option<PathBuf>,
    screenshots: PathBuf,
    run_id: String,
    cache: Mutex<Vec<Value>>,
    acknowledgements: Mutex<std::collections::HashMap<String,(String,Value)>>,
    photo_acknowledgements: Mutex<std::collections::HashMap<String,(String,Value)>>,
    tracked_photos: Mutex<std::collections::HashSet<String>>,
    unknown_photo_records: AtomicBool,
    #[cfg(test)] cleanup_hook: Mutex<Option<Box<dyn FnOnce(&Path)+Send>>>,
}

#[derive(Clone)]
pub struct Job { manager: Arc<Manager>, id: String, protected: Arc<AtomicBool> }

fn token(value: &str) -> bool {
    !value.is_empty() && value.len() <= 200
        && value.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
}
fn checked_dir(path: &Path, create: bool) -> Result<()> {
    ensure!(path.is_absolute(), "artifact_directory_not_absolute");
    crate::poem_trash::verify_primary_volume(path)?;
    let mut current = PathBuf::new();
    for component in path.components() {
        ensure!(!matches!(component, std::path::Component::ParentDir), "artifact_path_traversal");
        current.push(component);
        if create && !current.exists() { fs::create_dir(&current)?; }
        let meta = fs::symlink_metadata(&current)?;
        ensure!(meta.is_dir() && !meta.file_type().is_symlink(), "artifact_directory_changed");
    }
    Ok(())
}
fn read(path: &Path) -> Result<Vec<u8>> {
    let meta = fs::symlink_metadata(path)?;
    ensure!(meta.is_file() && !meta.file_type().is_symlink() && meta.len() < 16 * 1024 * 1024, "artifact_record_invalid");
    let mut options = OpenOptions::new(); options.read(true);
    #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.custom_flags(libc::O_NOFOLLOW); }
    Ok(std::io::read_to_string(options.open(path)?)?.into_bytes())
}

/// A successful archive must also wait until the producer has released this request.
pub(crate) fn completed_writer(root: &Path, token: &str, response: &str, manifest: &Value) -> Result<Option<File>> {
    uuid::Uuid::parse_str(token)?;
    ensure!(crate::haiku_artifacts::token(response), "invalid_artifact_response");
    checked_dir(root,false)?;
    #[cfg(unix)] {
        use crate::bookshelf::research_archive::safe::Dir;
        use std::os::fd::AsRawFd;
        let root=Dir::open(root)?;
        let requests=match root.child("_requests",false) {
            Ok(v)=>v,
            Err(e) if e.downcast_ref::<std::io::Error>().is_some_and(|e|e.kind()==std::io::ErrorKind::NotFound)=>return Ok(None),
            Err(e)=>return Err(e),
        };
        let Some(bytes)=requests.read(&format!("{token}.json"),1024*1024)? else {return Ok(None);};
        let marker:Value=serde_json::from_slice(&bytes)?;
        ensure!(marker["schema"]=="dogido.capture_writer.v1" && marker["capture_request_id"]==token
            && marker["response_id"]==response,"artifact_writer_identity_mismatch");
        if marker["writer_closed"]!=true {return Ok(None);}
        let lock=root.child(response,false)?.existing_file(".writer.lock")?;
        if unsafe {libc::flock(lock.as_raw_fd(),libc::LOCK_EX|libc::LOCK_NB)}!=0 {return Ok(None);}
        let after:Value=serde_json::from_slice(&requests.read(&format!("{token}.json"),1024*1024)?.context("artifact_writer_marker_missing")?)?;
        ensure!(marker==after,"artifact_writer_changed");
        for key in ["capture_request_id","response_id","writer_instance","status","writer_closed"] {
            ensure!(!marker[key].is_null() && marker[key]==manifest[key],"artifact_writer_manifest_conflict");
        }
        Ok(Some(lock))
    }
    #[cfg(not(unix))] {Ok(None)}
}

fn entry_hash(value: &Value) -> String {
    fn sorted(value: &Value) -> Value {
        match value {
            Value::Object(fields) => {
                let ordered:std::collections::BTreeMap<_,_>=fields.iter().map(|(k,v)|(k.clone(),sorted(v))).collect();
                Value::Object(ordered.into_iter().collect())
            },
            Value::Array(values)=>Value::Array(values.iter().map(sorted).collect()),
            _=>value.clone(),
        }
    }
    format!("{:x}",Sha256::digest(serde_json::to_vec(&sorted(value)).expect("JSON value serialization")))
}

/// Called under the bookshelf state lock. New registrations cannot revive a discarded capture.
pub(crate) fn validate_photo_target(memory: &Path, command: &str, poem: &str, canonical: Option<&Value>) -> Result<()> {
    let directory=memory.join("artifact-jobs");
    if !directory.try_exists()? {return Ok(());}
    checked_dir(&directory,false)?;
    let mut matched=None;
    let mut unknown=false;
    for entry in fs::read_dir(directory)? {
        let path=entry?.path();
        if path.file_name().is_some_and(|n|n.to_string_lossy().starts_with('.')) || path.extension().is_none_or(|e|e!="json") {continue;}
        let record:Record=match read(&path).and_then(|v|Ok(serde_json::from_slice(&v)?)) {
            Ok(record)=>record,
            Err(_)=>{unknown=true;continue;},
        };
        if record.photo.as_ref().is_some_and(|p|p.command_id==command) {
            ensure!(matched.is_none(),"artifact_photo_identity_conflict");
            matched=Some(record);
        }
    }
    if let Some(record)=matched {
        ensure!(record.state==State::Saved && record.entry_id.as_ref().is_some_and(|id|
            poem==format!("session:{}:{}",record.session_id,id)),"artifact_photo_target_uncommitted");
        let original=canonical.context("artifact_saved_entry_missing")?;
        ensure!(original["artifact_job_id"]==record.job_id
            && record.entry_hash.as_ref().is_some_and(|hash|*hash==entry_hash(original)),"artifact_photo_entry_conflict");
    } else {ensure!(!unknown,"artifact_photo_ownership_unknown");}
    Ok(())
}

impl Manager {
    pub fn new(memory: PathBuf, capture: Option<PathBuf>, screenshots: PathBuf) -> Arc<Self> {
        let manager=Arc::new(Self { memory, capture, screenshots, run_id: uuid::Uuid::new_v4().to_string(), cache: Mutex::new(vec![]), acknowledgements: Mutex::new(Default::default()), photo_acknowledgements: Mutex::new(Default::default()), tracked_photos: Mutex::new(Default::default()), unknown_photo_records:AtomicBool::new(false), #[cfg(test)] cleanup_hook: Mutex::new(None) });
        match manager.ids() {
            Ok(ids)=>for id in ids {
                match manager.load(&id) {
                    Ok(record)=>if let Some(p)=record.photo {manager.tracked_photos.lock().unwrap().insert(p.command_id);},
                    Err(_)=>manager.unknown_photo_records.store(true,Ordering::Release),
                }
            },
            Err(_)=>manager.unknown_photo_records.store(true,Ordering::Release),
        }
        manager
    }
    fn directory(&self) -> PathBuf { self.memory.join("artifact-jobs") }
    fn lock(&self, id: &str) -> Result<File> {
        ensure!(token(id), "invalid_artifact_job");
        checked_dir(&self.directory(), true)?;
        crate::haiku_record::locked_file(&self.directory().join(format!(".{id}.lock")))
    }
    fn archive_id(&self) -> Result<Option<String>> {
        let marker = self.memory.join(".dogido-research-archive.json");
        if !marker.try_exists()? { return Ok(None); }
        let value: Value = serde_json::from_slice(&read(&marker)?)?;
        Ok(Some(value["archive_id"].as_str().context("artifact_archive_id_missing")?.into()))
    }
    fn load(&self, id: &str) -> Result<Record> {
        ensure!(token(id), "invalid_artifact_job");
        let value: Record = serde_json::from_slice(&read(&self.directory().join(format!("{id}.json")))?)?;
        ensure!(value.schema_version == 1 && value.job_id == id && value.archive_id == self.archive_id()?, "artifact_job_identity_mismatch");
        Ok(value)
    }
    fn store(&self, value: &Record) -> Result<()> {
        ensure!(token(&value.job_id), "invalid_artifact_job");
        crate::poem_trash::atomic(&self.directory().join(format!("{}.json", value.job_id)), &serde_json::to_vec(value)?)
    }
    pub fn start(self: &Arc<Self>, sid: &str, id: &str, instance: &str, player: &str) -> Result<Job> {
        ensure!(token(sid) && token(id), "invalid_artifact_identity");
        let _lock = self.lock(id)?;
        let path = self.directory().join(format!("{id}.json"));
        ensure!(!path.try_exists()?, "artifact_job_reused");
        self.store(&Record { schema_version: 1, archive_id: self.archive_id()?, run_id: self.run_id.clone(),
            session_id: sid.into(), job_id: id.into(), instance_id: instance.into(), player: player.into(),
            state: State::Active, emission: None, entry_id: None, entry_hash: None, photo: None, requests: vec![], error: None })?;
        Ok(Job { manager: self.clone(), id: id.into(), protected: Arc::new(AtomicBool::new(false)) })
    }
    fn update<T>(&self, id: &str, f: impl FnOnce(&mut Record) -> Result<T>) -> Result<T> {
        let _lock = self.lock(id)?;
        let mut value = self.load(id)?; let result = f(&mut value)?; self.store(&value)?; Ok(result)
    }
    /// 送信先の接続が変わっても、同じownerの元撮影だけを決着する。
    pub fn commands(&self, sid: &str, owner: Option<&str>) -> Vec<Value> {
        let Ok(mut cache) = self.cache.lock() else { return vec![]; };
        let result:Vec<_>=cache.iter().filter(|v| v["owner_uuid"].as_str() == owner).map(|v| {
            let mut command = v.clone(); command["session_id"] = sid.into(); command
        }).take(8).collect();
        if !cache.is_empty() {let n=result.len()%cache.len();cache.rotate_left(n);}
        result
    }
    pub fn photo_tracked(&self, command: &str) -> bool {
        self.unknown_photo_records.load(Ordering::Acquire) || self.tracked_photos.lock().unwrap().contains(command)
    }
    pub fn photo_acknowledged(&self, owner: Option<&str>, result: &crate::events::AdapterCommandResult) -> bool {
        self.photo_acknowledgements.lock().unwrap().get(&result.command_id)
            .is_some_and(|(saved,value)|Some(saved.as_str())==owner && *value==json!(result))
    }
    pub fn disposition_acknowledged(&self, owner: Option<&str>, result: &crate::events::AdapterCommandResult) -> bool {
        self.acknowledgements.lock().unwrap().get(&result.command_id)
            .is_some_and(|(saved,value)|Some(saved.as_str())==owner && *value==json!(result))
    }
    fn ids(&self) -> Result<Vec<String>> {
        if !self.directory().try_exists()? { return Ok(vec![]); }
        checked_dir(&self.directory(), false)?;
        let mut out = vec![];
        for entry in fs::read_dir(self.directory())? {
            let path = entry?.path();
            if !path.file_name().is_some_and(|n|n.to_string_lossy().starts_with('.')) && path.extension().is_some_and(|e| e == "json") {
                let id = path.file_stem().and_then(|s| s.to_str()).context("invalid_artifact_job")?;
                ensure!(token(id), "invalid_artifact_job"); out.push(id.into());
            }
        }
        Ok(out)
    }
    /// 永続台帳を持つ素材だけを再照合する。ファイルの古さでは回収しない。
    pub fn reconcile(&self) -> Result<()> {
        let mut commands = vec![];
        for id in self.ids()? {
            let _lock=self.lock(&id)?;
            let mut record = match self.load(&id) {
                Ok(r)=>r,
                Err(error)=>{tracing::warn!(event="haiku_artifact_job_held",job_id=id,%error);continue;}
            };
            let before=serde_json::to_value(&record)?;
            if record.state==State::Active && record.run_id!=self.run_id {
                let root=self.memory.join("sessions").join(&record.session_id);
                match crate::poem_trash::raw_rows(&root,"haiku_entries.jsonl") {
                    Ok(rows)=>{
                        let candidates:Vec<_>=rows.iter().filter(|v|v["artifact_job_id"]==record.job_id).collect();
                        if candidates.len()==1 {
                            record.entry_id=Some(candidates[0]["id"].as_str().context("artifact_entry_id_missing")?.into());
                            record.entry_hash=Some(entry_hash(candidates[0]));record.state=State::Saved;record.emission=None;
                        } else if candidates.len()>1 {record.state=State::Conflict;record.error=Some("artifact_recovery_conflict".into());}
                    },
                    Err(error)=>{record.error=Some(error.to_string());}
                }
            }
            if matches!(record.state, State::SavePending | State::SaveFailed) {
                if let Err(error) = self.retry_save(&mut record) { record.state = if error.to_string().contains("conflict") {State::Conflict} else {State::SaveFailed}; record.error = Some(error.to_string()); }
            }
            if record.state == State::Discard {
                for index in 0..record.requests.len() {
                    if record.requests[index].removed { continue; }
                    match self.remove_capture(&mut record, index) {
                        Ok(true) => record.requests[index].removed = true,
                        Ok(false) => {record.error=Some("artifact_writer_pending_or_unsupported".into());},
                        Err(error) => record.error = Some(error.to_string()),
                    }
                }
            }
            if let Some(photo) = &record.photo {
                if !photo.settled && matches!(record.state, State::Discard | State::Saved) {
                    if record.state != State::Discard || !self.photo_referenced(&photo.command_id)? {
                        commands.push(json!({"type":"dispose_screenshot", "command_id":photo.decision_id,
                            "decision_id":photo.decision_id,"capture_command_id":photo.command_id,
                            "source_session_id":record.session_id,"owner_uuid":photo.context.owner_id,
                            "action":if record.state==State::Saved {"keep"} else {"discard"}}));
                    }
                }
                if record.state == State::Saved && !photo.registered && let Some(receipt) = photo.receipt.clone() {
                    let store = self.photo_store();
                    match store.register_capture(&photo.command_id, &Receipt { poem_id: format!("session:{}:{}", record.session_id, record.entry_id.as_deref().unwrap()), ..receipt }) {
                        Ok(()) => record.photo.as_mut().unwrap().registered = true,
                        Err(error) => record.error = Some(error.to_string()),
                    }
                }
            }
            if record.state==State::Discard && record.requests.iter().all(|r|r.removed)
                && record.photo.as_ref().is_none_or(|p|p.settled) {record.error=None;}
            if serde_json::to_value(&record)?!=before {
                if let Some(reason)=&record.error && before["error"].as_str()!=Some(reason) {
                    tracing::warn!(event="haiku_artifact_held",job_id=id,reason);
                }
                self.store(&record)?;
            }
        }
        *self.cache.lock().unwrap() = commands;
        Ok(())
    }
    fn photo_store(&self) -> crate::bookshelf::store::Store {
        crate::bookshelf::store::Store { memory: self.memory.clone(), capture_dir: self.capture.clone(),
            screenshots: self.screenshots.clone(), photos: self.memory.join("library-photos") }
    }
    fn retry_save(&self, record: &mut Record) -> Result<()> {
        let emission = record.emission.as_ref().context("artifact_pending_emission_missing")?;
        ensure!(emission.artifact_job_id.as_deref()==Some(record.job_id.as_str()),"artifact_emission_job_conflict");
        ensure!(record.entry_hash.as_ref().is_none_or(|h|*h==entry_hash(&emission.entry())),"artifact_pending_emission_conflict");
        let store = MemoryStore::new(self.memory.join("sessions").join(&record.session_id));
        // Strictly parse the existing original rows before retrying a possibly partial write.
        for row in crate::poem_trash::raw_rows(store.root(), "haiku_entries.jsonl")? {
            if row["id"] == emission.entry()["id"] { ensure!(row == emission.entry(), "haiku_entry_conflict"); }
        }
        let saved = store.save_agent_haiku(&record.session_id, &record.player, emission)?;
        ensure!(saved.entry == emission.entry(), "artifact_saved_entry_conflict");
        record.entry_id = Some(saved.entry_id); record.entry_hash=Some(entry_hash(&saved.entry)); record.emission=None; record.state = State::Saved; record.error = None;
        Ok(())
    }
    fn saved_reference(&self, needle: &str) -> Result<bool> {
        references::referenced(&self.memory,needle)
    }
    fn photo_referenced(&self, command: &str) -> Result<bool> { self.saved_reference(command) }

    fn remove_capture(&self, record: &mut Record, index: usize) -> Result<bool> {
        ensure!(record.state == State::Discard && record.emission.is_none(), "artifact_not_discardable");
        let Some(root) = &self.capture else { return Ok(false); };
        checked_dir(root, false)?;
        let request = &record.requests[index];
        uuid::Uuid::parse_str(&request.token)?;
        // Reference creation follows state -> poem order. Keep both locks until unlink finishes.
        let store = self.photo_store();
        let _state = store.write_lock()?;
        let _save = crate::haiku_record::SAVE_LOCK.lock().map_err(|_| anyhow::anyhow!("artifact_save_mutex_poisoned"))?;
        let mut poem_locks = vec![];
        for dir in crate::poem_book::directories(&self.memory)? { poem_locks.push(crate::poem_trash::lock(&dir)?); }
        #[cfg(unix)]
        {
            use crate::bookshelf::research_archive::safe::Dir;
            use std::os::fd::AsRawFd;
            let root_fd = Dir::open(root)?;
            let requests = match root_fd.child("_requests", false) {
                Ok(v) => v,
                Err(e) if e.downcast_ref::<std::io::Error>().is_some_and(|e|e.kind()==std::io::ErrorKind::NotFound) => return Ok(false),
                Err(e) => return Err(e),
            };
            let marker_name = format!("{}.json", request.token);
            let marker = requests.read(&marker_name, 1024*1024)?;
            let holding_name = format!(".discard-{}", request.token);
            if marker.is_none() {
                // A completed unlink can precede the final journal update after a crash.
                return Ok(request.removal.is_some() && !root.join(&holding_name).try_exists()?
                    && request.removal.as_ref().and_then(|v|v.marker["response_id"].as_str()).is_some_and(|r| !root.join(r).exists()));
            }
            let marker: Value = serde_json::from_slice(&marker.unwrap())?;
            ensure!(marker["schema"] == "dogido.capture_writer.v1" && marker["capture_request_id"] == request.token,
                "artifact_writer_identity_mismatch");
            if marker["writer_closed"] != true { return Ok(false); }
            let response = marker["response_id"].as_str().context("artifact_response_missing")?;
            ensure!(token(response) && request.response_id.as_deref().is_none_or(|r|r==response), "artifact_response_conflict");
            if self.saved_reference(response)? || self.saved_reference(&request.token)? { return Ok(false); }
            let resumed = root.join(&holding_name).try_exists()?;
            let original=root.join(response).try_exists()?;
            if !resumed && !original && let Some(intent)=&record.requests[index].removal {
                ensure!(intent.marker==marker,"artifact_cleanup_marker_changed");
                requests.unlink(&marker_name,false)?;
                return Ok(true);
            }
            ensure!(!(resumed && original), "artifact_cleanup_location_conflict");
            let name = if resumed { holding_name.as_str() } else { response };
            let folder = root_fd.child(name, false)?;
            let lease = match folder.existing_file(".writer.lock") {
                Ok(f) => Some(f),
                Err(e) if resumed && record.requests[index].removal.is_some()
                    && e.downcast_ref::<std::io::Error>().is_some_and(|e|e.kind()==std::io::ErrorKind::NotFound) => None,
                Err(e) => return Err(e),
            };
            if let Some(lock) = &lease && unsafe {libc::flock(lock.as_raw_fd(),libc::LOCK_EX|libc::LOCK_NB)}!=0 {return Ok(false);}
            ensure!(requests.read(&marker_name,1024*1024)?.as_ref().map(|b|serde_json::from_slice::<Value>(b)).transpose()?==Some(marker.clone()),"artifact_writer_changed");
            if record.requests[index].removal.is_none() {
                ensure!(!resumed,"artifact_cleanup_unowned_holding");
                let manifest_bytes=folder.read("manifest.json",16*1024*1024)?.context("artifact_manifest_missing")?;
                let manifest:Value=serde_json::from_slice(&manifest_bytes)?;
                for key in ["capture_request_id","response_id","writer_instance","status","writer_closed"] {
                    ensure!(manifest[key]==marker[key] && !marker[key].is_null(),"artifact_writer_manifest_conflict");
                }
                ensure!(manifest["schema"]=="dogido.internal_capture.v1" && manifest["writer_closed"]==true
                    && ["complete","partial","cancelled","failed"].contains(&manifest["status"].as_str().unwrap_or("")),"artifact_writer_not_finished");
                let mut hidden=folder.existing_file("hidden.f32")?;
                let mut digest=Sha256::new();std::io::copy(&mut hidden,&mut digest)?;
                let digest=format!("{:x}",digest.finalize());
                let bytes=hidden.metadata()?.len();
                ensure!(manifest["hidden_file"]=="hidden.f32" && manifest["hidden_sha256"]==digest && manifest["hidden_bytes"]==bytes,"artifact_capture_changed");
                record.requests[index].removal=Some(Removal {marker:marker.clone(),manifest_hash:format!("{:x}",Sha256::digest(&manifest_bytes)),hidden_hash:digest,hidden_bytes:bytes});
                self.store(record)?;
            }
            let intent=record.requests[index].removal.as_ref().unwrap();
            ensure!(intent.marker==marker,"artifact_cleanup_marker_changed");
            let path=root.join(name);
            checked_dir(&path,false)?;
            let observed=Dir::open(&path)?;
            ensure!(folder.same_directory(&observed)?,"artifact_cleanup_directory_changed");
            for e in fs::read_dir(&path)? {
                let n=e?.file_name();let n=n.to_str().context("artifact_unknown_file")?;
                ensure!(["hidden.f32","manifest.json",".writer.lock"].contains(&n),"artifact_unknown_file");
            }
            for (file,expected,size) in [("hidden.f32",intent.hidden_hash.as_str(),Some(intent.hidden_bytes)),("manifest.json",intent.manifest_hash.as_str(),None)] {
                match folder.existing_file(file) {
                    Ok(mut f) => {
                        let mut digest=Sha256::new();std::io::copy(&mut f,&mut digest)?;
                        ensure!(format!("{:x}",digest.finalize())==expected && size.is_none_or(|n|f.metadata().is_ok_and(|m|m.len()==n)),"artifact_cleanup_payload_changed");
                    },
                    Err(e) if resumed && e.downcast_ref::<std::io::Error>().is_some_and(|e|e.kind()==std::io::ErrorKind::NotFound)=>{},
                    Err(e)=>return Err(e),
                }
            }
            if !resumed {
                root_fd.rename_child(response,&holding_name)?;
                ensure!(folder.same_directory(&root_fd.child(&holding_name,false)?)?,"artifact_cleanup_directory_changed");
            }
            #[cfg(test)] if let Some(hook)=self.cleanup_hook.lock().unwrap().take() {hook(&root.join(&holding_name));}
            for name in folder.names()? {
                ensure!(["hidden.f32","manifest.json",".writer.lock"].contains(&name.as_str()),"artifact_unknown_file");
            }
            let mut owned=vec![];
            for (name,expected,size) in [("hidden.f32",Some(intent.hidden_hash.as_str()),Some(intent.hidden_bytes)),
                ("manifest.json",Some(intent.manifest_hash.as_str()),None),(".writer.lock",None,None)] {
                match folder.existing_file(name) {
                    Ok(mut file)=>{
                        if let Some(expected)=expected {
                            let mut digest=Sha256::new();std::io::copy(&mut file,&mut digest)?;
                            ensure!(format!("{:x}",digest.finalize())==expected && size.is_none_or(|n|file.metadata().is_ok_and(|m|m.len()==n)),"artifact_cleanup_payload_changed");
                        }
                        owned.push((name,file));
                    },
                    Err(e) if e.downcast_ref::<std::io::Error>().is_some_and(|e|e.kind()==std::io::ErrorKind::NotFound)=>{},
                    Err(e)=>return Err(e),
                }
            }
            for (name,file) in owned {folder.unlink_owned(name,&file)?;}
            root_fd.unlink(&holding_name,true)?;
            requests.unlink(&marker_name,false)?;
            Ok(true)
        }
        #[cfg(not(unix))] {Ok(false)}
    }
    fn photo_record_id(&self, command: &str, disposition: bool) -> Result<Option<String>> {
        let mut found=None;
        for id in self.ids()? {
            let record=match self.load(&id) {
                Ok(record)=>record,
                Err(error)=>{
                    self.unknown_photo_records.store(true,Ordering::Release);
                    tracing::warn!(event="haiku_artifact_job_held",job_id=id,%error);
                    continue;
                },
            };
            if record.photo.as_ref().is_some_and(|p|if disposition {p.decision_id==command} else {p.command_id==command}) {
                ensure!(found.is_none(),"artifact_photo_identity_conflict");found=Some(id);
            }
        }
        Ok(found)
    }
    /// 旧sessionへ返った結果も永続台帳で照合し、元の句の登録へだけ渡す。
    pub fn receive_photo(&self, owner: Option<&str>, result: &crate::events::AdapterCommandResult) -> Result<bool> {
        let Some(id)=self.photo_record_id(&result.command_id,false)? else {return Ok(false);};
        let _lock=self.lock(&id)?;
        let mut r=self.load(&id)?;
        let p=r.photo.as_mut().context("artifact_photo_missing")?;
        ensure!(p.command_id==result.command_id && owner==Some(p.context.owner_id.as_str()),"artifact_photo_owner_mismatch");
        ensure!(result.command_type==crate::events::AdapterCommandResultCommandType::CaptureScreenshot
            && result.extra.get("session_id")==Some(&json!(r.session_id))
            && result.extra.get("poem_id")==Some(&json!(format!("session:{}:capture:{}",r.session_id,r.job_id)))
            && result.executed_at>=p.issued_at && result.executed_at<=chrono::Utc::now(),"artifact_photo_identity_mismatch");
        if result.status != crate::events::AdapterCommandResultStatus::Succeeded {
            let failure=json!(result);
            ensure!(p.receipt.is_none() && p.failure.as_ref().is_none_or(|old|old==&failure),"artifact_photo_receipt_conflict");
            if p.failure.is_none() {p.failure=Some(failure);self.store(&r)?;}
            self.photo_acknowledgements.lock().unwrap().insert(result.command_id.clone(),(owner.unwrap().into(),json!(result)));return Ok(true);
        }
        ensure!(p.failure.is_none(),"artifact_photo_receipt_conflict");
        let receipt:Receipt=serde_json::from_value(json!(result.extra))?;
        ensure!(receipt.capture_view.as_ref().is_some_and(|v|v.matches(&p.context))
            && receipt.captured_at>=p.issued_at && receipt.captured_at<p.issued_at+chrono::Duration::seconds(10)
            && crate::screenshot::valid_filename(&receipt.filename) && receipt.filename.starts_with("dogido_")
            && result.executed_at>=receipt.captured_at && result.executed_at<p.issued_at+chrono::Duration::seconds(120)
            && receipt.sha256.len()==64 && receipt.sha256.bytes().all(|c|c.is_ascii_hexdigit()),"artifact_photo_identity_mismatch");
        ensure!(p.receipt.as_ref().is_none_or(|old|old==&receipt),"artifact_photo_receipt_conflict");
        if p.receipt.is_none() {p.receipt=Some(receipt);self.store(&r)?;}
        self.photo_acknowledgements.lock().unwrap().insert(result.command_id.clone(),(owner.unwrap().into(),json!(result)));Ok(true)
    }
    pub fn receive_disposition(&self, owner: Option<&str>, result: &crate::events::AdapterCommandResult) -> Result<bool> {
        let Some(id)=self.photo_record_id(&result.command_id,true)? else {return Ok(false);};
        let _lock=self.lock(&id)?;
        let mut r=self.load(&id)?;
        let p=r.photo.as_mut().context("artifact_photo_missing")?;
        let action=if r.state==State::Discard {"discard"} else if r.state==State::Saved {"keep"} else {return Ok(false);};
        ensure!(p.decision_id==result.command_id && result.command_type==crate::events::AdapterCommandResultCommandType::DisposeScreenshot
            && owner==Some(p.context.owner_id.as_str()) && result.extra.get("capture_command_id")==Some(&json!(p.command_id))
            && result.extra.get("decision_id")==Some(&json!(p.decision_id)) && result.extra.get("source_session_id")==Some(&json!(r.session_id))
            && result.extra.get("action")==Some(&json!(action)),"artifact_disposition_identity_mismatch");
        if result.status!=crate::events::AdapterCommandResultStatus::Succeeded {return Ok(false);}
        p.settled=true;self.store(&r)?;self.acknowledgements.lock().unwrap().insert(result.command_id.clone(),(owner.unwrap().into(),json!(result)));Ok(true)
    }

}

impl Job {
    pub fn request(&self, request_id: &str, token: &str) -> Result<()> {
        uuid::Uuid::parse_str(token)?;
        self.manager.update(&self.id, |r| {
            ensure!(r.state == State::Active && !r.requests.iter().any(|v| v.token == token), "artifact_request_state");
            r.requests.push(Request { request_id: request_id.into(), token: token.into(), response_id: None, removed: false, removal: None }); Ok(())
        })
    }
    pub fn response(&self, token: &str, response: &str) -> Result<()> {
        ensure!(crate::haiku_artifacts::token(response), "invalid_artifact_response");
        self.manager.update(&self.id, |r| {
            let row = r.requests.iter_mut().find(|v| v.token == token).context("artifact_request_missing")?;
            ensure!(row.response_id.as_deref().is_none_or(|v| v == response), "artifact_response_conflict");
            row.response_id = Some(response.into()); Ok(())
        })
    }
    pub fn photo(&self, context: &crate::body::contracts::Context, issued: chrono::DateTime<chrono::Utc>) -> Result<String> {
        let id=self.manager.update(&self.id, |r| {
            ensure!(r.state == State::Active, "artifact_photo_state");
            if let Some(p) = &r.photo { return Ok(p.command_id.clone()); }
            let command_id = format!("shot_{}", uuid::Uuid::new_v4());
            r.photo = Some(Photo { command_id: command_id.clone(), context: context.clone(), issued_at: issued,
                receipt: None, failure: None, registered: false, decision_id: uuid::Uuid::new_v4().to_string(), settled: false });
            Ok(command_id)
        })?;
        self.manager.tracked_photos.lock().unwrap().insert(id.clone());
        Ok(id)
    }
    pub fn save_pending(&self, emission: &Emission) -> Result<()> {
        self.protected.store(true, Ordering::Release);
        self.manager.update(&self.id, |r| {
            ensure!(r.state == State::Active && emission.artifact_job_id.as_deref()==Some(r.job_id.as_str()), "artifact_save_state");
            r.emission = Some(emission.clone()); r.entry_id = Some(emission.entry_id()); r.entry_hash=Some(entry_hash(&emission.entry())); r.state = State::SavePending; Ok(())
        })
    }
    pub fn saved(&self) -> Result<()> {
        let record=self.manager.load(&self.id)?;
        let root=self.manager.memory.join("sessions").join(&record.session_id);
        let rows=crate::poem_trash::raw_rows(&root,"haiku_entries.jsonl")?;
        let expected=rows.iter().find(|v|v["id"].as_str()==record.entry_id.as_deref()).context("artifact_saved_entry_missing")?;
        self.saved_entry(expected)
    }
    pub fn saved_entry(&self, expected:&Value)->Result<()> {
        self.manager.update(&self.id, |r| {
            ensure!(!matches!(r.state,State::Discard|State::Conflict),"artifact_save_state_conflict");
            ensure!(expected["artifact_job_id"]==r.job_id,"artifact_saved_job_mismatch");
            if let Some(hash)=&r.entry_hash {ensure!(entry_hash(expected)==*hash,"haiku_entry_conflict");}
            else {ensure!(self.protected.load(Ordering::Acquire),"artifact_pending_emission_missing");}
            let root=self.manager.memory.join("sessions").join(&r.session_id);
            let _guard=crate::poem_trash::lock(&root)?;
            let rows=crate::poem_trash::raw_rows(&root,"haiku_entries.jsonl")?;
            let matching:Vec<_>=rows.iter().filter(|v|v["id"]==expected["id"]).collect();
            ensure!(matching.len()==1 && matching[0]==expected,"haiku_entry_conflict");
            r.entry_id=Some(expected["id"].as_str().context("artifact_entry_id_missing")?.into());
            r.entry_hash=Some(entry_hash(expected));r.emission=None;r.state=State::Saved;r.error=None;Ok(())
        })
    }
    pub fn save_failed(&self, message: &str) -> Result<()> {
        self.manager.update(&self.id, |r| {
            if r.state == State::SavePending { r.state = State::SaveFailed; r.error = Some(message.into()); } Ok(())
        })
    }
    pub fn discard(&self) -> Result<()> {
        if self.protected.load(Ordering::Acquire) { return Ok(()); }
        self.manager.update(&self.id, |r| { if r.state == State::Active && r.emission.is_none() { r.state = State::Discard; } Ok(()) })
    }
}

#[cfg(test)]
mod tests;
