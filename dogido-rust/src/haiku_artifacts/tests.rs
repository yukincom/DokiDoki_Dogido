//! 見込み: 発句・保存・writer終了を別々に進めても生成素材の帰属は変わらない。
//! 合格: 完成句・保留素材は同じバイトで残り、失敗側の専有物だけが消える。
//! 範囲: Rustの永続台帳、正本、実ファイル・OSロック。採取器の出力は模擬。

use super::*;
use crate::haiku_record::PreparedEmission;

const SID: &str = "ses_fixture";
const DATA: &[u8] = &[0, 0, 128, 63, 0, 0, 0, 64];

struct Fixture {
    dir: PathBuf,
    manager: Arc<Manager>,
}
impl Fixture {
    fn new() -> Self {
        // macOSの /var のリンクを避け、個人のSSDや実ゲームを使わない。
        let temp = std::env::temp_dir().canonicalize().unwrap();
        let dir = temp.join(format!("dogido-artifact-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&dir).unwrap();
        fs::create_dir_all(dir.join("captures/_requests")).unwrap();
        let manager = Self::manager(&dir);
        Self { dir, manager }
    }

    fn manager(dir: &Path) -> Arc<Manager> {
        Manager::new(
            dir.join("memory"),
            Some(dir.join("captures")),
            dir.join("screenshots"),
        )
    }

    fn job(&self, id: &str) -> Job {
        self.manager
            .start(SID, id, "fixture-instance", "試験")
            .unwrap()
    }

    fn entries(&self) -> PathBuf {
        self.dir
            .join("memory/sessions")
            .join(SID)
            .join("long_term/haiku_entries.jsonl")
    }

    fn bundle(&self, job: &Job, response: &str) -> Bundle {
        let token = uuid::Uuid::new_v4().to_string();
        let request = format!("{}:request:1", job.id);
        job.request(&request, &token).unwrap();
        let root = self.dir.join("captures");
        let dir = root.join(response);
        fs::create_dir(&dir).unwrap();
        let lease = dir.join(".writer.lock");
        fs::write(&lease, []).unwrap();
        fs::write(dir.join("hidden.f32"), DATA).unwrap();
        let marker = json!({
            "schema":"dogido.capture_writer.v1", "capture_request_id":token,
            "response_id":response, "writer_instance":"fixture-writer",
            "status":"complete", "writer_closed":true,
        });
        let mut manifest = marker.clone();
        manifest["schema"] = json!("dogido.internal_capture.v1");
        manifest["backend"] = json!("llama.cpp");
        manifest["hidden_file"] = json!("hidden.f32");
        manifest["hidden_bytes"] = json!(DATA.len());
        manifest["hidden_sha256"] = json!(format!("{:x}", Sha256::digest(DATA)));
        let marker_path = root.join("_requests").join(format!("{token}.json"));
        fs::write(&marker_path, serde_json::to_vec(&marker).unwrap()).unwrap();
        fs::write(
            dir.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        Bundle {
            dir,
            marker: marker_path,
            lease,
            token,
            request,
            response: response.to_owned(),
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.dir).unwrap();
    }
}

struct Bundle {
    dir: PathBuf,
    marker: PathBuf,
    lease: PathBuf,
    token: String,
    request: String,
    response: String,
}
impl Bundle {
    fn intact(&self) {
        assert_eq!(fs::read(self.dir.join("hidden.f32")).unwrap(), DATA);
        assert!(self.dir.join("manifest.json").is_file());
    }
    fn marker_closed(&self, closed: bool) {
        let mut marker: Value = serde_json::from_slice(&fs::read(&self.marker).unwrap()).unwrap();
        marker["writer_closed"] = json!(closed);
        fs::write(&self.marker, serde_json::to_vec(&marker).unwrap()).unwrap();
    }
}
fn emission(job: &Job, data: &Bundle) -> Emission {
    let rows: Vec<Value> = [("upper","上五","くさちのひ"),("middle","中七","くろきつるぎの"),("lower","下五","かげのさむさ")].iter().enumerate().map(|(i,(pos,name,text))|json!({
        "line_id":format!("line_{}",i+1),"line_index":i, "position":pos, "canonical_name":name,
        "surface_text":text, "reading_text":text,"source_atom_ids":[],"source_atoms":[],"provenance":"generated"
    })).collect();
    let prepared: PreparedEmission = serde_json::from_value(json!({
        "text":"くさちのひ くろきつるぎの かげのさむさ", "lines":rows, "materials":{},
        "biome":"plains", "event_sequence":1
    }))
    .unwrap();
    let mut e = prepared
        .complete("2026-11-04T00:01:00.123456Z".parse().unwrap())
        .unwrap();
    e.artifact_job_id=Some(job.id.clone());
    e.internal_capture=Some(serde_json::from_value(json!({
        "schema_version":1, "session_id":SID, "job_id":job.id,
        "token_scope":"raw_generation_before_domain_parse_and_reading_normalization",
        "requests":[{"request_id":data.request,"capture_request_id":data.token,"request_index":1,
            "kind":"haiku_draft","route":"haiku","requested_model":"mock","request_sha256":"fixture",
            "status":"complete","response_id":data.response}]
    })).unwrap());
    e
}
fn persisted(m: &Manager, j: &Job) -> Record {
    m.load(&j.id).unwrap()
}

#[test]
#[cfg(unix)]
// 見込み: 取消でHTTP応答が失われても要求トークンが応答先を示す。writerの終了札だけでは消さない。
// 合格: 札未終了と実lock中は保持。再起動後、解放した旧要求だけ消え同一内容の次ジョブは不変。範囲: 模擬札＋実OSロック。
fn cancelled_before_response_waits_for_closed_writer_and_only_drops_its_bundle() {
    let f = Fixture::new();
    let a = f.job("cancelled");
    let old = f.bundle(&a, "old_response");
    let b = f.job("next");
    let next = f.bundle(&b, "next_response");
    b.response(&next.token, &next.response).unwrap();
    f.manager.reconcile().unwrap();
    old.intact();
    next.intact();
    a.discard().unwrap();
    old.marker_closed(false);
    let reopened = Fixture::manager(&f.dir);
    reopened.reconcile().unwrap();
    old.intact();
    assert!(!persisted(&reopened, &a).requests[0].removed);
    let lock = File::open(&old.lease).unwrap();
    lock.lock().unwrap();
    old.marker_closed(true);
    reopened.reconcile().unwrap();
    old.intact();
    next.intact();
    drop(lock);
    reopened.reconcile().unwrap();
    reopened.reconcile().unwrap();
    assert!(!old.dir.exists() && !old.marker.exists());
    assert!(persisted(&reopened, &a).requests[0].removed);
    next.intact();
    assert!(next.marker.exists());
    assert_eq!(persisted(&reopened, &b).state, State::Active);
}

#[test]
// 見込み: 完成記録の保存失敗は生成不成立と区別する。音声側の取消でも元の句と素材を復元できる。
// 合格: 実書込み障害中はpending/failedの同じ句と数値を保持。復旧後は同じID・内容の正本1行だけ。範囲: 仮保管庫と台帳再読。
fn failed_save_recovers_same_emission_and_capture_once() {
    let f = Fixture::new();
    let job = f.job("save");
    let data = f.bundle(&job, "save_capture");
    job.response(&data.token, &data.response).unwrap();
    let e = emission(&job, &data);
    let entries = f.entries();
    // 実ファイルの位置にディレクトリを置き、OSの保存エラーを発生させる。
    fs::create_dir_all(&entries).unwrap();
    job.save_pending(&e).unwrap();
    job.discard().unwrap();
    let pending = persisted(&f.manager, &job);
    assert_eq!(pending.state, State::SavePending);
    assert_eq!(pending.emission.as_ref().unwrap().entry(), e.entry());
    data.intact();
    job.save_failed("fixture write failure").unwrap();
    let restarted = Fixture::manager(&f.dir);
    restarted.reconcile().unwrap();
    assert_eq!(persisted(&restarted, &job).state, State::SaveFailed);
    assert!(entries.is_dir());
    data.intact();
    let failed = persisted(&restarted, &job);
    assert_eq!(failed.emission.as_ref().unwrap().entry(), e.entry());
    fs::remove_dir(&entries).unwrap();
    restarted.reconcile().unwrap();
    assert_eq!(persisted(&restarted, &job).state, State::Saved);
    let raw = fs::read_to_string(&entries).unwrap();
    assert_eq!(raw.lines().count(), 1);
    assert_eq!(
        serde_json::from_str::<Value>(raw.trim()).unwrap(),
        e.entry()
    );
    job.discard().unwrap();
    Fixture::manager(&f.dir).reconcile().unwrap();
    assert_eq!(fs::read_to_string(entries).unwrap(), raw);
    data.intact();
}

#[test]
// 見込み: 保存後の応答が失われても、同一原記録の再送は同じ句へ一度だけ結び付く。
// 合格: 実正本1行のバイトと生成素材は不変、jobはSaved。範囲: 再起動を模した新managerと既存保存処理。
fn matching_preinserted_emission_is_one_saved_row() {
    let f = Fixture::new();
    let j = f.job("replay");
    let data = f.bundle(&j, "replay_response");
    j.response(&data.token, &data.response).unwrap();
    let e = emission(&j, &data);
    j.save_pending(&e).unwrap();
    let st = MemoryStore::new(f.dir.join("memory/sessions").join(SID));
    assert!(st.save_agent_haiku(SID, "試験", &e).unwrap().inserted);
    let before = fs::read(f.entries()).unwrap();
    j.saved().unwrap();
    let m = Fixture::manager(&f.dir);
    m.reconcile().unwrap();
    m.reconcile().unwrap();
    assert_eq!(persisted(&m, &j).state, State::Saved);
    assert_eq!(fs::read(f.entries()).unwrap(), before);
    data.intact();
}

#[test]
// 見込み: 本文・句IDが一致しても別jobの原記録なら、今回の素材を成功扱いしない。
// 合格: 実正本バイト不変、衝突を保留して素材を保持。再起動でも上書き・削除なし。範囲: 実保存のinserted=falseと照合。
fn preinserted_same_id_and_text_with_another_job_stays_in_conflict() {
    let f = Fixture::new();
    let j = f.job("wanted");
    let data = f.bundle(&j, "wanted_response");
    j.response(&data.token, &data.response).unwrap();
    let requested = emission(&j, &data);
    let mut existing = requested.clone();
    let foreign = existing.internal_capture.as_mut().unwrap();
    foreign.job_id = "another".into();
    foreign.requests[0].response_id = Some("another_response".into());
    assert_eq!(existing.entry_id(), requested.entry_id());
    assert_ne!(existing.entry(), requested.entry());
    MemoryStore::new(f.dir.join("memory/sessions").join(SID))
        .save_agent_haiku(SID, "試験", &existing)
        .unwrap();
    let before = fs::read(f.entries()).unwrap();
    j.save_pending(&requested).unwrap();
    let m = Fixture::manager(&f.dir);
    m.reconcile().unwrap();
    j.discard().unwrap();
    m.reconcile().unwrap();
    let held = persisted(&m, &j);
    assert_eq!(held.state, State::Conflict);
    assert!(held.error.is_some());
    data.intact();
    assert_eq!(fs::read(f.entries()).unwrap(), before);
}

#[test]
// 見込み: 取消でも他の有効な参照、差替え、帰属札のない旧素材は処分できない。
// 合格: 3要求すべて元の内容と位置に残り、removedはfalse。範囲: 保存参照と実ファイルのハッシュ、旧採取器の固定例。
fn canonical_reference_changed_bytes_and_legacy_writer_stay_in_place() {
    let f = Fixture::new();
    let refs = f.job("referenced");
    let linked = f.bundle(&refs, "referenced_response");
    let changed = f.job("replaced");
    let replaced = f.bundle(&changed, "changed_response");
    let legacy = f.job("legacy");
    let old = f.bundle(&legacy, "old_format");
    for (j, b) in [(&refs, &linked), (&changed, &replaced), (&legacy, &old)] {
        j.response(&b.token, &b.response).unwrap();
        j.discard().unwrap();
    }
    // もう一つの保存句・修正履歴の参照は、元jobの成否と独立に守る。
    let holder = f.job("kept_poem");
    let e = emission(&holder, &linked);
    MemoryStore::new(f.dir.join("memory/sessions").join(SID))
        .save_agent_haiku(SID, "試験", &e)
        .unwrap();
    let other = *b"replaced";
    fs::write(replaced.dir.join("hidden.f32"), other).unwrap();
    fs::remove_file(&old.marker).unwrap();
    f.manager.reconcile().unwrap();
    linked.intact();
    old.intact();
    assert_eq!(fs::read(replaced.dir.join("hidden.f32")).unwrap(), other);
    for j in [refs, changed, legacy] {
        assert!(!persisted(&f.manager, &j).requests[0].removed);
    }
}

#[cfg(unix)]
#[test]
// 見込み: 処分の永続予約があれば、どこで停止しても同じ要求だけを処分し終える。
// 合格: 退避直後、部分unlink、退避先消去後、札消去後の4状態すべてでremoved=true、別要求の数値不変。範囲: OS上に中断状態を再現し、新しいmanagerで再開。
fn cleanup_resumes_from_each_durable_unlink_boundary() {
    for boundary in [
        "renamed",
        "partially_unlinked",
        "folder_gone",
        "marker_gone",
    ] {
        let f = Fixture::new();
        let old_job = f.job("old");
        let old = f.bundle(&old_job, "old_response");
        let next_job = f.job("next");
        let next = f.bundle(&next_job, "next_response");
        old_job.discard().unwrap();
        // 退避前検証で意図を本物のreconcileに記録させ、fixture側で正確な中断位置を再現する。
        let unknown = old.dir.join("not-owned");
        fs::write(&unknown, b"keep").unwrap();
        f.manager.reconcile().unwrap();
        assert!(
            persisted(&f.manager, &old_job).requests[0]
                .removal
                .is_some()
        );
        assert_eq!(fs::read(&unknown).unwrap(), b"keep");
        fs::remove_file(&unknown).unwrap();
        let holding = f
            .dir
            .join("captures")
            .join(format!(".discard-{}", old.token));
        fs::rename(&old.dir, &holding).unwrap();
        if boundary != "renamed" {
            fs::remove_file(holding.join("hidden.f32")).unwrap();
        }
        if matches!(boundary, "folder_gone" | "marker_gone") {
            fs::remove_file(holding.join("manifest.json")).unwrap();
            fs::remove_file(holding.join(".writer.lock")).unwrap();
            fs::remove_dir(&holding).unwrap();
        }
        if boundary == "marker_gone" {
            fs::remove_file(&old.marker).unwrap();
        }
        let resumed = Fixture::manager(&f.dir);
        resumed.reconcile().unwrap();
        resumed.reconcile().unwrap();
        let record = persisted(&resumed, &old_job);
        assert!(record.requests[0].removed, "{boundary}: {:?}", record.error);
        assert!(
            !old.dir.exists() && !holding.exists() && !old.marker.exists(),
            "{boundary}"
        );
        next.intact();
        assert!(next.marker.is_file());
    }
}

const PHOTO_OWNER: &str = "11111111-1111-4111-8111-111111111111";

fn photo_context() -> crate::body::contracts::Context {
    crate::body::contracts::Context {
        owner_id: PHOTO_OWNER.into(),
        body_id: "22222222-2222-4222-8222-222222222222".into(),
        epoch: "33333333-3333-4333-8333-333333333333".into(),
        dimension: "minecraft:overworld".into(),
    }
}
fn disposition_result(command: &Value) -> crate::events::AdapterCommandResult {
    serde_json::from_value(
        json!({"command_id":command["decision_id"],"command_type":"dispose_screenshot",
        "status":"succeeded","executed_at":chrono::Utc::now(), "action":command["action"],
        "capture_command_id":command["capture_command_id"],"decision_id":command["decision_id"],
        "source_session_id":command["source_session_id"]}),
    )
    .unwrap()
}

#[test]
// 見込み: 再接続で変えられるのは配送先sessionだけ。処分対象とowner・決定IDは変わらない。
// 合格: 他owner・不一致結果の4種は未決着。無関係な破損台帳があっても正当な再送だけ決着する。範囲: Rust台帳・模擬結果。
fn disposition_requires_original_owner_capture_and_decision_after_reconnect() {
    let f = Fixture::new();
    let j = f.job("cancel_photo");
    let shot = j.photo(&photo_context(), chrono::Utc::now()).unwrap();
    j.discard().unwrap();
    let m = Fixture::manager(&f.dir);
    m.reconcile().unwrap();
    assert!(m.commands("new_sid", None).is_empty());
    assert!(m.commands("new_sid", Some("other-owner")).is_empty());
    let commands = m.commands("new_sid", Some(PHOTO_OWNER));
    assert_eq!(commands.len(), 1);
    let c = &commands[0];
    assert_eq!(c["type"], "dispose_screenshot");
    assert_eq!(c["action"], "discard");
    assert_eq!(c["capture_command_id"], shot);
    assert_eq!(c["source_session_id"], SID);
    assert_eq!(c["session_id"], "new_sid");
    assert_eq!(c["owner_uuid"], PHOTO_OWNER);
    let decision = c["decision_id"].as_str().unwrap();
    assert_eq!(c["command_id"], decision);
    fs::write(m.directory().join("broken_other.json"),b"{broken").unwrap();
    let ok = disposition_result(c);
    assert!(m.receive_disposition(Some("other-owner"), &ok).is_err());
    for key in [
        "capture_command_id",
        "decision_id",
        "source_session_id",
        "action",
    ] {
        let mut wrong = ok.clone();
        wrong.extra.insert(key.into(), json!("other"));
        assert!(m.receive_disposition(Some(PHOTO_OWNER), &wrong).is_err());
    }
    assert!(!persisted(&m, &j).photo.unwrap().settled);
    assert!(!m.disposition_acknowledged(Some(PHOTO_OWNER),&ok));
    assert!(m.receive_disposition(Some(PHOTO_OWNER), &ok).unwrap());
    assert!(m.receive_disposition(Some(PHOTO_OWNER), &ok).unwrap());
    assert!(m.disposition_acknowledged(Some(PHOTO_OWNER),&ok));
    let restarted = Fixture::manager(&f.dir);
    restarted.reconcile().unwrap();
    assert!(persisted(&restarted, &j).photo.unwrap().settled);
    assert!(restarted.commands("new_sid", Some(PHOTO_OWNER)).is_empty());
}

#[tokio::test]
// 見込み: 本体の観測からは、撮影の旧capabilityだけで処分命令を受け取れない。
// 合格: 同一ownerの同じ保留命令が新capabilityで届き、旧版へ戻すと消え、再追加で同じIDが届く。範囲: 一時保存先・Rustの本物の登録/observe、実Minecraftなし。
async fn observe_requires_lifecycle_capability_to_dispatch_disposition() {
    use crate::dialogue::{Dialogue, DialogueConfig};
    let f = Fixture::new();
    let old = f.job("pending_photo");
    let shot = old.photo(&photo_context(), chrono::Utc::now()).unwrap();
    old.discard().unwrap();
    let mut cfg = DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        reading_engine: "off".into(),
        audio_dir: f.dir.join("audio"),
        ..Default::default()
    };
    cfg.haiku.enabled = false;
    cfg.haiku.memory_enabled = true;
    cfg.haiku.memory_dir = f.dir.join("memory");
    cfg.haiku.capture_dir = Some(f.dir.join("captures"));
    cfg.haiku.screenshot_dir = Some(f.dir.join("screenshots"));
    let d = Dialogue::new(cfg).unwrap();
    d.register("new_sid", "試験", true);
    d.bind_body_owner("new_sid", Some(PHOTO_OWNER));
    let mut seq = 0;
    let mut observe = || {
        seq += 1;
        let input = json!({"schema_version":"2026-05-24","adapter":"rust-conversation-preview","observed_at":chrono::Utc::now(),"sequence":seq,
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"name":"試験"}});
        let event = crate::events::GameEvent::parse(input).unwrap();
        let output = d.observe("new_sid", event, None);
        assert_eq!(output["accepted"], true);
        output["commands"]
            .as_array()
            .unwrap()
            .iter()
            .find(|c| c["type"] == "dispose_screenshot")
            .cloned()
    };
    let old_cap = crate::screenshot::CAPABILITY.to_owned();
    d.set_execution_capabilities("new_sid", &[old_cap.clone(), CAPABILITY.into()]);
    let issued = tokio::time::timeout(std::time::Duration::from_secs(6), async {
        loop {
            if let Some(c) = observe() {
                break c;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
    })
    .await
    .expect("lifecycle command was not delivered through observation");
    assert_eq!(issued["capture_command_id"], shot);
    d.set_execution_capabilities("new_sid", std::slice::from_ref(&old_cap));
    assert!(observe().is_none());
    d.set_execution_capabilities("new_sid", &[old_cap, CAPABILITY.into()]);
    assert_eq!(observe().unwrap()["command_id"], issued["command_id"]);
    d.shutdown().await;
}

#[test]
// 見込み: 保存後に旧撮影の結果が到着しても元の正本IDへ一度だけ貼られる。新ジョブの撮影へ付け替えない。
// 合格: 別台帳が破損しても元句へ1枚だけ登録されPNG一致、再起動・再送で不変。範囲: 模擬結果、実Rust保存・本棚登録。
fn late_photo_binds_once_to_the_saved_canonical_entry_after_restart() {
    let f = Fixture::new();
    let j = f.job("saved_photo");
    let bundle = f.bundle(&j, "photo_response");
    let at = chrono::Utc::now();
    let ctx = photo_context();
    let command = j.photo(&ctx, at).unwrap();
    j.response(&bundle.token, &bundle.response).unwrap();
    let e = emission(&j, &bundle);
    j.save_pending(&e).unwrap();
    MemoryStore::new(f.dir.join("memory/sessions").join(SID))
        .save_agent_haiku(SID,"試験",&e).unwrap();
    j.saved().unwrap();
    let saved = persisted(&f.manager, &j);
    assert!(saved.emission.is_none());
    assert!(saved.entry_hash.is_some());
    let next = f.job("next_photo");
    assert_ne!(next.photo(&ctx, at).unwrap(), command);
    let screens = f.dir.join("screenshots");
    fs::create_dir_all(&screens).unwrap();
    let name = "dogido_2026-11-04_01.00.00.png";
    let bytes = b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR\0\0\0\x02\0\0\0\x03";
    fs::write(screens.join(name), bytes).unwrap();
    let digest = format!("{:x}", Sha256::digest(bytes));
    let result:crate::events::AdapterCommandResult=serde_json::from_value(json!({
        "command_id":command,"command_type":"capture_screenshot","status":"succeeded","executed_at":at,
        "session_id":SID,"poem_id":format!("session:{SID}:capture:{}",j.id),
        "filename":name,"directory":screens,"sha256":digest,"captured_at":at,
        "capture_view":{"viewpoint":"dogido_client_render_eye","body_entity_uuid":ctx.body_id,"body_epoch":ctx.epoch,
            "owner_uuid":PHOTO_OWNER,"dimension":ctx.dimension,"camera_restored":true}
    })).unwrap();
    fs::write(f.manager.directory().join("broken_other.json"),b"{broken").unwrap();
    let m = Fixture::manager(&f.dir);
    m.reconcile().unwrap();
    assert!(persisted(&m, &j).photo.unwrap().receipt.is_none());
    let mut wrong = result.clone();
    wrong.extra.insert(
        "poem_id".into(),
        json!(format!("session:{SID}:capture:{}", next.id)),
    );
    assert!(m.receive_photo(Some(PHOTO_OWNER), &wrong).is_err());
    assert!(m.receive_photo(Some(PHOTO_OWNER), &result).unwrap());
    m.reconcile().unwrap();
    let record = persisted(&m, &j);
    assert!(record.photo.unwrap().registered, "{:?}", record.error);
    assert!(persisted(&m, &next).photo.unwrap().receipt.is_none());
    let state_path = f.dir.join("memory/bookshelf/state.json");
    let state = fs::read(&state_path).unwrap();
    let shelf: Value = serde_json::from_slice(&state).unwrap();
    let key = format!("minecraft-{command}");
    let target = format!("session:{SID}:{}", e.entry_id());
    assert_eq!(shelf["minecraftPhotos"].as_object().unwrap().len(), 1);
    assert_eq!(shelf["minecraftPhotos"][&key]["poemId"], target);
    assert_eq!(shelf["minecraftPhotos"][&key]["sha256"], digest);
    assert_eq!(
        shelf["placements"][format!("poems:{target}")],
        json!([key, null, null])
    );
    assert_eq!(m.photo_store().image_bytes(&key).unwrap(), bytes);
    let again = Fixture::manager(&f.dir);
    assert!(again.receive_photo(Some(PHOTO_OWNER), &result).unwrap());
    again.reconcile().unwrap();
    assert_eq!(fs::read(state_path).unwrap(), state);
}


#[test]
// 見込み: 撮影失敗の配送完了も成功画像と別に永続化すれば、未ACK結果の再送を終了できる。
// 合格: failed/rejected/expiredの正当な結果だけACKでき、別owner・別session・後着成功はACK不可。再起動後も同じ失敗だけACK。範囲: 実台帳・模擬失敗、無関係な破損台帳。
fn terminal_photo_failures_acknowledge_only_the_original_identity_after_restart() {
    let f=Fixture::new();
    for (index,status) in ["failed","rejected","expired"].into_iter().enumerate() {
        let j=f.job(&format!("failed_photo_{index}"));
        let at=chrono::Utc::now()-chrono::Duration::seconds(1);
        let shot=j.photo(&photo_context(),at).unwrap();
        let result:crate::events::AdapterCommandResult=serde_json::from_value(json!({
            "command_id":shot,"command_type":"capture_screenshot","status":status,"executed_at":chrono::Utc::now(),
            "session_id":SID,"poem_id":format!("session:{SID}:capture:{}",j.id),"detail":"fixture_failure"
        })).unwrap();
        fs::write(f.manager.directory().join("broken_other.json"),b"{broken").unwrap();
        assert!(f.manager.receive_photo(Some("another-owner"),&result).is_err());
        let mut wrong=result.clone();wrong.extra.insert("session_id".into(),json!("other_session"));
        assert!(f.manager.receive_photo(Some(PHOTO_OWNER),&wrong).is_err());
        assert!(!f.manager.photo_acknowledged(Some(PHOTO_OWNER),&result));
        assert!(f.manager.receive_photo(Some(PHOTO_OWNER),&result).unwrap());
        assert!(f.manager.photo_acknowledged(Some(PHOTO_OWNER),&result));
        let record=persisted(&f.manager,&j);let photo=record.photo.unwrap();
        assert!(photo.receipt.is_none());assert_eq!(photo.failure,Some(json!(result)));
        let again=Fixture::manager(&f.dir);
        assert!(again.receive_photo(Some(PHOTO_OWNER),&result).unwrap());
        assert!(again.photo_acknowledged(Some(PHOTO_OWNER),&result));
        wrong=result.clone();wrong.extra.insert("detail".into(),json!("different_failure"));
        assert!(again.receive_photo(Some(PHOTO_OWNER),&wrong).is_err());
        assert!(!again.photo_acknowledged(Some(PHOTO_OWNER),&wrong));
        wrong=result.clone();wrong.status=crate::events::AdapterCommandResultStatus::Succeeded;
        assert!(!again.photo_acknowledged(Some(PHOTO_OWNER),&wrong),"未受理の成功へ以前の失敗ACKを転用しない");
        assert!(again.receive_photo(Some(PHOTO_OWNER),&wrong).is_err());
        assert!(!again.photo_acknowledged(Some(PHOTO_OWNER),&wrong));
        assert!(!again.photo_acknowledged(Some("another-owner"),&result));
    }
}

#[test]
// 見込み: 保存時のdigestを句ロック内で再照合すれば、同じIDの別原記録・重複行へ遅延画像を貼らない。
// 合格: 元記録と一致するreceiptでも差替え句には未登録、PNGと新しい原記録は不変で衝突を記録。範囲: 実保存・本棚登録・模擬撮影。
fn late_photo_holds_when_canonical_is_changed_or_duplicated_before_registration() {
    for duplicate in [false,true] {
    let f=Fixture::new();let j=f.job("changed_canonical_photo");
    let bundle=f.bundle(&j,"changed_canonical_response");
    let at=chrono::Utc::now();let ctx=photo_context();let shot=j.photo(&ctx,at).unwrap();
    j.response(&bundle.token,&bundle.response).unwrap();
    let e=emission(&j,&bundle);j.save_pending(&e).unwrap();
    MemoryStore::new(f.dir.join("memory/sessions").join(SID)).save_agent_haiku(SID,"試験",&e).unwrap();
    j.saved().unwrap();
    let mut changed=e.entry();changed["text"]=json!("same ID, a different original");
    let canonical=f.entries();
    let content=if duplicate {format!("{}\n{}\n",e.entry(),changed)} else {format!("{}\n",changed)};
    fs::write(&canonical,content).unwrap();let original=fs::read(&canonical).unwrap();
    let screens=f.dir.join("screenshots");fs::create_dir_all(&screens).unwrap();
    let name="dogido_2026-11-04_02.00.00.png";
    let bytes=b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR\0\0\0\x02\0\0\0\x03";
    fs::write(screens.join(name),bytes).unwrap();
    let result:crate::events::AdapterCommandResult=serde_json::from_value(json!({
        "command_id":shot,"command_type":"capture_screenshot","status":"succeeded","executed_at":at,
        "session_id":SID,"poem_id":format!("session:{SID}:capture:{}",j.id),
        "filename":name,"directory":screens,"sha256":format!("{:x}",Sha256::digest(bytes)),"captured_at":at,
        "capture_view":{"viewpoint":"dogido_client_render_eye","body_entity_uuid":ctx.body_id,"body_epoch":ctx.epoch,
        "owner_uuid":PHOTO_OWNER,"dimension":ctx.dimension,"camera_restored":true}
    })).unwrap();
    assert!(f.manager.receive_photo(Some(PHOTO_OWNER),&result).unwrap());f.manager.reconcile().unwrap();
    let record=persisted(&f.manager,&j);assert!(!record.photo.unwrap().registered);
    assert_eq!(record.error.as_deref(),Some(if duplicate {"capture_target_conflict"} else {"artifact_photo_entry_conflict"}));
    assert!(!f.dir.join("memory/bookshelf/state.json").exists(),"衝突した画像の登録は本棚を新規保存しない");
    assert_eq!(fs::read(canonical).unwrap(),original);assert_eq!(fs::read(screens.join(name)).unwrap(),bytes);
    }
}
