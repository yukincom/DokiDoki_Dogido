//! 発句開始時に一度だけ撮影し、同じ生成処理の保存済み句へ結び付ける台帳。
//! 生成中は結果を未ACKで待たせ、保存成功後だけ本棚へ渡す。取消画像は別の句へ流さない。
use crate::body::contracts::Context;
use crate::events::{
    AdapterCommandResult, AdapterCommandResultCommandType as Kind,
    AdapterCommandResultStatus as Status,
};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::VecDeque, path::PathBuf};

pub const CAPABILITY: &str = "client.screenshot.body_view.v1";
const START_SECONDS: i64 = 10;
const RESULT_SECONDS: i64 = 120;

/// 標準Minecraftディレクトリ。独自gameDirは明示設定だけで変更する。
pub fn default_directory() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        PathBuf::from(std::env::var_os("APPDATA").unwrap_or_default())
            .join(".minecraft/screenshots")
    }
    #[cfg(target_os = "macos")]
    {
        PathBuf::from(std::env::var_os("HOME").unwrap_or_default())
            .join("Library/Application Support/minecraft/screenshots")
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".minecraft/screenshots")
    }
}

pub fn valid_filename(name: &str) -> bool {
    // 過去のMinecraft写真も閲覧するため旧名を保つ。新規撮影のdogido_必須条件はreceiveで検査する。
    regex::Regex::new(r"^(?:dogido_)?\d{4}-\d{2}-\d{2}_\d{2}\.\d{2}\.\d{2}(?:_[2-9][0-9]*|_1[0-9]+)?\.png$")
        .unwrap()
        .is_match(name)
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub session_id: String,
    pub poem_id: String,
    pub filename: String,
    pub sha256: String,
    pub directory: PathBuf,
    pub captured_at: DateTime<Utc>,
    /// 旧写真には存在しない。新規要求では撮影した身体とカメラ復帰の一致をreceiveが必須にする。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capture_view: Option<CaptureView>,
}

/// 同一フレームを撮影した身体の根拠。adapterがカメラを復帰してから返し、Rustが要求元へ再照合する。
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CaptureView {
    pub viewpoint: String,
    pub body_entity_uuid: String,
    pub body_epoch: String,
    pub owner_uuid: String,
    pub dimension: String,
    pub camera_restored: bool,
}
impl CaptureView {
    fn matches(&self, context: &Context) -> bool {
        self.viewpoint == "dogido_client_render_eye"
            && self.camera_restored
            && self.body_entity_uuid == context.body_id
            && self.body_epoch == context.epoch
            && self.owner_uuid == context.owner_id
            && self.dimension == context.dimension
    }
}

#[derive(Clone)]
struct Pending {
    command: Value,
    context: Context,
    job: String,
    target: Option<String>,
    issued: DateTime<Utc>,
    retain_until: DateTime<Utc>,
    received: Option<Receipt>,
    registering: bool,
    retry_at: DateTime<Utc>,
}

#[derive(Default)]
pub struct State {
    pub enabled: bool,
    pending: Vec<Pending>,
    done: VecDeque<String>,
}

impl State {
    /// 材料を固定した発句開始時に撮影する。wireのpoem_idはこの生成だけの予約キー。
    /// 受理済み身体contextを固定し、実際の正本IDは保存成功時にbindで一度だけ確定する。
    /// 開始期限を後ろへ動かさず、撮影結果が身体や生成を取り違えていないことをreceiveで確かめる。
    pub fn prepare(
        &mut self,
        sid: &str,
        job: &str,
        context: &Context,
        issued: DateTime<Utc>,
        generation_timeout_ms: u64,
    ) {
        self.expire(issued);
        if !self.enabled
            || self.pending.len() >= 8
            || context.dimension.is_empty()
            || self.pending.iter().any(|p| p.job == job)
        {
            return;
        }
        // 発句の既存timeoutまで待てるが、撮影自体の有効時間は10秒のまま。
        let timeout = std::time::Duration::from_millis(generation_timeout_ms)
            .saturating_add(std::time::Duration::from_secs(RESULT_SECONDS as u64));
        let retain_until = Duration::from_std(timeout).ok()
            .and_then(|d| issued.checked_add_signed(d)).unwrap_or(DateTime::<Utc>::MAX_UTC);
        self.pending.push(Pending {
            command: json!({"type":"capture_screenshot","command_id":format!("shot_{}",uuid::Uuid::new_v4()),
                "session_id":sid,"poem_id":format!("session:{sid}:capture:{job}"),"dimension":context.dimension,
                "body_entity_uuid":context.body_id,"body_epoch":context.epoch,"owner_uuid":context.owner_id,"issued_at":issued,
                "expires_at":issued+Duration::seconds(START_SECONDS)}),
            context: context.clone(), job: job.into(), target: None, issued, retain_until, received: None,
            registering: false, retry_at: issued,
        });
    }
    /// 前置き出力の前に、同じ生成の撮影開始が終わったかを確かめる。
    /// 期限後に届いたreceiptもcaptured_atを既存receiveが検査するので、保存待ち中の画像は失わない。
    pub fn capture_settled(&self, job: &str, now: DateTime<Utc>) -> bool {
        self.pending.iter().filter(|p| p.job == job).all(|p| p.received.is_some()
            || now >= p.issued + Duration::seconds(START_SECONDS))
    }
    /// 保存された同一生成の句だけを登録先にする。再bindで貼付け先を変えない。
    pub fn bind(&mut self, job: &str, sid: &str, poem: &str, now: DateTime<Utc>) {
        self.expire(now);
        if !poem.starts_with(&format!("session:{sid}:")) { return; }
        for p in self.pending.iter_mut().filter(|p| p.job == job && p.command["session_id"] == sid) {
            if p.target.is_none() {
                p.target = Some(poem.into());
                p.retain_until = now + Duration::seconds(RESULT_SECONDS);
            }
        }
    }
    /// 未保存の失敗・取消だけを終える。既に保存した句の撮影結果は継続して受け取る。
    pub fn discard_unbound(&mut self, job: &str) {
        let ids: Vec<_> = self.pending.iter().filter(|p| p.job == job && p.target.is_none())
            .filter_map(|p| p.command["command_id"].as_str().map(str::to_owned)).collect();
        for id in ids { self.finish(&id); }
    }
    fn finish(&mut self, id: &str) {
        self.pending.retain(|p| p.command["command_id"] != id);
        if !self.done.iter().any(|v| v == id) {
            self.done.push_back(id.into());
        }
        while self.done.len() > 512 {
            self.done.pop_front();
        }
    }
    fn expire(&mut self, now: DateTime<Utc>) {
        let expired: Vec<String> = self
            .pending
            .iter()
            .filter(|p| !p.registering && now >= p.retain_until)
            .map(|p| p.command["command_id"].as_str().unwrap().into())
            .collect();
        for id in expired {
            self.finish(&id);
        }
    }
    pub fn owns(&self, id: &str) -> bool {
        self.pending.iter().any(|p| p.command["command_id"] == id)
            || self.done.iter().any(|v| v == id)
    }
    pub fn commands(&mut self, now: DateTime<Utc>) -> Vec<Value> {
        self.expire(now);
        self.pending
            .iter()
            .filter(|p| p.received.is_none() && !p.registering && now < p.issued + Duration::seconds(START_SECONDS))
            .map(|p| p.command.clone())
            .collect()
    }
    /// 成功結果は永続化してからACKする。書込み中の再送で二重登録しない。
    pub fn receive(
        &mut self,
        result: &AdapterCommandResult,
        now: DateTime<Utc>,
    ) -> (bool, Option<Receipt>) {
        self.expire(now);
        let Some(p) = self
            .pending
            .iter_mut()
            .find(|p| p.command["command_id"] == result.command_id)
        else {
            return (true, None);
        };
        if p.registering {
            return (false, None);
        }
        if result.command_type != Kind::CaptureScreenshot {
            return (false, None);
        }
        // 失敗にも元のsessionと句を要求し、別接続の結果では終了しない。
        if result.extra.get("session_id") != p.command.get("session_id")
            || result.extra.get("poem_id") != p.command.get("poem_id")
        {
            return (false, None);
        }
        if result.status != Status::Succeeded {
            // 最初の成功画像を、矛盾する失敗再送で破棄しない。
            if p.received.is_some() { return (false, None); }
            self.finish(&result.command_id);
            return (true, None);
        }
        let mut receipt = serde_json::from_value::<Receipt>(json!(result.extra)).ok();
        if let Some(first) = &p.received && receipt.as_ref() != Some(first) {
            return (false, None);
        }
        let valid = receipt.as_ref().is_some_and(|r| valid_filename(&r.filename)
            && r.filename.starts_with("dogido_")
            && r.capture_view.as_ref().is_some_and(|view| view.matches(&p.context))
            && r.captured_at >= p.issued && r.captured_at < p.issued + Duration::seconds(START_SECONDS)
            && result.executed_at >= r.captured_at && result.executed_at <= now
            && result.executed_at < p.issued + Duration::seconds(RESULT_SECONDS));
        if !valid {
            if p.received.is_some() { return (false, None); }
            self.finish(&result.command_id);
            return (true, None);
        }
        p.received = receipt.clone();
        // adapterは最初の予約キーを返し続ける。本棚にはコードが確定した正本IDだけを渡す。
        let Some(target) = p.target.as_ref() else { return (false, None); };
        if now < p.retry_at {
            return (false, None);
        }
        receipt.as_mut().unwrap().poem_id = target.clone();
        p.registering = true;
        (false, receipt)
    }
    /// 永続化成功・終端エラーだけをACK可能にする。I/O失敗は同じ結果の再送を待つ。
    pub fn registered(&mut self, id: &str, terminal: bool, now: DateTime<Utc>) {
        if terminal {
            self.finish(id);
        } else if let Some(p) = self
            .pending
            .iter_mut()
            .find(|p| p.command["command_id"] == id)
        {
            p.registering = false;
            p.retry_at = now + Duration::seconds(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn context() -> Context {
        Context {
            owner_id: "11111111-1111-4111-8111-111111111111".into(),
            body_id: "22222222-2222-4222-8222-222222222222".into(),
            epoch: "33333333-3333-4333-8333-333333333333".into(),
            dimension: "minecraft:overworld".into(),
        }
    }
    fn pending() -> (State, Value, DateTime<Utc>) {
        let now = Utc::now();
        let mut state = State {
            enabled: true,
            ..Default::default()
        };
        state.prepare("sid", "job", &context(), now, 600_000);
        let c = state.commands(now).pop().unwrap();
        state.bind("job", "sid", "session:sid:hk_1", now);
        (state, c, now)
    }
    fn result(c: &Value, now: DateTime<Utc>) -> AdapterCommandResult {
        serde_json::from_value(json!({"command_id":c["command_id"],"command_type":"capture_screenshot",
            "status":"succeeded","executed_at":now,"session_id":c["session_id"],"poem_id":c["poem_id"],
            "filename":"dogido_2026-10-20_12.00.00.png","sha256":"test","directory":"/screenshots","captured_at":now,
            "capture_view":{"viewpoint":"dogido_client_render_eye","body_entity_uuid":c["body_entity_uuid"],
                "body_epoch":c["body_epoch"],"owner_uuid":c["owner_uuid"],"dimension":c["dimension"],"camera_restored":true}})).unwrap()
    }
    #[test]
    fn preface_waits_for_same_job_photo_or_capture_start_deadline() {
        let (mut s,c,now)=pending();
        assert!(!s.capture_settled("job",now));
        assert!(s.capture_settled("other",now));
        assert!(!s.capture_settled("job",now+Duration::seconds(START_SECONDS-1)));
        assert!(s.capture_settled("job",now+Duration::seconds(START_SECONDS)));
        s.receive(&result(&c,now),now);
        assert!(s.capture_settled("job",now));
    }
    #[test]
    fn ack_only_after_durable_save_and_retries_never_capture_twice() {
        let (mut s, c, now) = pending();
        let r = result(&c, now);
        assert_eq!(s.receive(&r, now).1.unwrap().poem_id, "session:sid:hk_1");
        assert!(s.commands(now).is_empty());
        assert_eq!(s.receive(&r, now), (false, None));
        s.registered(r.command_id.as_str(), false, now);
        assert_eq!(s.receive(&r, now), (false, None));
        assert!(s.receive(&r, now + Duration::seconds(3)).1.is_some());
        s.registered(&r.command_id, true, now);
        assert_eq!(s.receive(&r, now), (true, None));
    }
    #[test]
    fn capability_deadline_target_and_filename_are_closed() {
        let (mut s, c, now) = pending();
        let mut r = result(&c, now);
        r.extra.insert("poem_id".into(), json!("wrong"));
        assert_eq!(s.receive(&r, now), (false, None));
        assert!(s.commands(now + Duration::seconds(11)).is_empty());
        let mut off = State::default();
        off.prepare("s", "job", &context(), now, 600_000);
        assert!(off.commands(now).is_empty());
        assert!(!valid_filename("../2026-10-20_12.00.00.png"));
        assert!(valid_filename("2026-10-20_12.00.00_12.png"));
        assert!(valid_filename("dogido_2026-10-20_12.00.00_2.png"));
        assert!(valid_filename("dogido_2026-10-20_12.00.00_12.png"));
        assert!(!valid_filename("dogido_2026-10-20_12.00.00_1.png"));
        assert!(!valid_filename("../dogido_2026-10-20_12.00.00.png"));
        let (mut s, c, now) = pending();
        let r = result(&c, now + Duration::seconds(11));
        assert_eq!(s.receive(&r, now + Duration::seconds(11)), (true, None));
    }
    #[test]
    fn early_image_waits_for_its_saved_poem_even_after_a_long_generation() {
        let now = Utc::now();
        let mut s = State { enabled: true, ..Default::default() };
        s.prepare("sid", "first", &context(), now, 600_000);
        s.prepare("sid", "first", &context(), now, 600_000);
        let commands = s.commands(now);
        assert_eq!(commands.len(), 1);
        let c = &commands[0];
        let r = result(c, now);
        assert_eq!(s.receive(&r, now), (false, None));
        assert!(s.commands(now).is_empty());
        let later = now + Duration::seconds(180);
        assert_eq!(s.receive(&r, later), (false, None));
        s.bind("other_job", "sid", "session:sid:wrong", later);
        s.bind("first", "other_session", "session:other_session:wrong", later);
        assert_eq!(s.receive(&r, later), (false, None));
        s.bind("first", "sid", "session:sid:hk_saved", later);
        s.bind("first", "sid", "session:sid:wrong", later);
        let receipt = s.receive(&r, later).1.unwrap();
        assert_eq!(receipt.poem_id, "session:sid:hk_saved");
        assert_eq!(receipt.captured_at, now);
        assert_eq!(r.extra["poem_id"], c["poem_id"]);
    }
    #[test]
    fn cancelled_generation_does_not_attach_to_the_next_poem() {
        let now = Utc::now();
        let mut s = State { enabled: true, ..Default::default() };
        s.prepare("sid", "cancelled", &context(), now, 600_000);
        let r = result(&s.commands(now)[0], now);
        s.discard_unbound("cancelled");
        s.prepare("sid", "next", &context(), now, 600_000);
        s.bind("next", "sid", "session:sid:hk_next", now);
        assert_eq!(s.receive(&r, now), (true, None));
        assert_eq!(s.commands(now).len(), 1);
        let next = result(&s.commands(now)[0], now);
        // 完成句の読み上げ取消では、その保存済み句の写真を失わない。
        s.discard_unbound("next");
        assert_eq!(s.receive(&next, now).1.unwrap().poem_id, "session:sid:hk_next");
    }
    #[test]
    fn first_early_receipt_survives_conflicting_success_and_failure_replays() {
        let now = Utc::now();
        let mut s = State { enabled: true, ..Default::default() };
        s.prepare("sid", "job", &context(), now, 600_000);
        let original = result(&s.commands(now)[0], now);
        assert_eq!(s.receive(&original, now), (false, None));
        let mut changed = original.clone();
        changed.extra.insert("filename".into(), json!("dogido_2026-10-20_12.00.00_2.png"));
        assert_eq!(s.receive(&changed, now), (false, None));
        let mut failed = original.clone();
        failed.status = Status::Failed;
        assert_eq!(s.receive(&failed, now), (false, None));
        s.bind("job", "sid", "session:sid:hk_saved", now);
        assert_eq!(s.receive(&changed, now), (false, None));
        assert_eq!(s.receive(&failed, now), (false, None));
        assert_eq!(s.receive(&original, now).1.unwrap().filename, "dogido_2026-10-20_12.00.00.png");
    }
    #[test]
    fn unbound_result_expires_without_late_reshooting_or_binding() {
        let now = Utc::now();
        let mut s = State { enabled: true, ..Default::default() };
        s.prepare("sid", "job", &context(), now, 1_000);
        let r = result(&s.commands(now)[0], now);
        let late = now + Duration::seconds(122);
        s.bind("job", "sid", "session:sid:hk_late", late);
        assert_eq!(s.receive(&r, late), (true, None));
        assert!(s.commands(late).is_empty());
    }
    #[test]
    fn capture_command_and_receipt_keep_the_materials_body_context() {
        let (mut s, c, now) = pending();
        let expected = context();
        assert_eq!(c["body_entity_uuid"], expected.body_id);
        assert_eq!(c["body_epoch"], expected.epoch);
        assert_eq!(c["owner_uuid"], expected.owner_id);
        assert_eq!(c["dimension"], expected.dimension);
        let receipt = s.receive(&result(&c, now), now).1.unwrap();
        assert!(receipt.capture_view.unwrap().matches(&expected));
    }
    #[test]
    fn new_capture_rejects_other_body_view_and_unrestored_camera() {
        for (key, value) in [
            ("viewpoint", json!("player")),
            ("body_entity_uuid", json!("44444444-4444-4444-8444-444444444444")),
            ("body_epoch", json!("44444444-4444-4444-8444-444444444444")),
            ("owner_uuid", json!("44444444-4444-4444-8444-444444444444")),
            ("dimension", json!("minecraft:the_nether")),
            ("camera_restored", json!(false)),
        ] {
            let (mut s, c, now) = pending();
            let mut r = result(&c, now);
            r.extra.get_mut("capture_view").unwrap()[key] = value;
            assert_eq!(s.receive(&r, now), (true, None), "{key}");
            assert!(s.commands(now).is_empty());
        }
    }
    #[test]
    fn historical_receipts_remain_readable_but_do_not_satisfy_new_capture() {
        let (mut s, c, now) = pending();
        let mut r = result(&c, now);
        r.extra.remove("capture_view");
        let old: Receipt = serde_json::from_value(json!(r.extra)).unwrap();
        assert!(old.capture_view.is_none());
        assert!(json!(old).get("capture_view").is_none());
        assert_eq!(s.receive(&r, now), (true, None));

        let (mut s, c, now) = pending();
        let mut r = result(&c, now);
        r.extra.insert("filename".into(), json!("2026-10-20_12.00.00.png"));
        assert!(valid_filename(r.extra["filename"].as_str().unwrap()));
        assert_eq!(s.receive(&r, now), (true, None));
    }
}
