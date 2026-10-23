//! 静止時の身体画像を既存game-eventsで受け取り、最新一枚と未考慮の変化を保持する。
//! 撮影・比較はFabric、発話機会・取消・身体世代の照合はRust。画像本文は履歴や保存JSONに入れない。
use super::{Dialogue, Session, bridge, chat_context};
use crate::{body::contracts::Context, chat_image::Attachment, events::{AdapterCommandResult, AdapterCommandResultStatus as Status}};
use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use std::{collections::VecDeque, sync::{Arc, Weak}, time::{Duration, Instant}};
use tokio::sync::watch;

pub(super) const CAPABILITY: &str = "client.body_view.on_stop.v1";
const IMAGE_FRESH_SECONDS: i64 = 30;
struct Pending { command: Value, context: Context, issued: DateTime<Utc>, expires: DateTime<Utc> }
#[derive(Clone)]
pub(super) struct Captured { pub image: Attachment, pub context: Context, pub capture_id: String, pub sequence: u64, change: Value }
#[derive(Default)]
pub(super) struct State {
    pub enabled: bool,
    context: Option<Context>,
    pending: Option<Pending>,
    latest: Option<Captured>,
    ready: bool,
    sequence: u64,
    done: VecDeque<String>,
}
impl State {
    fn finish(&mut self, id: &str) {
        if self.pending.as_ref().is_some_and(|p| p.command["command_id"] == id) { self.pending = None; }
        if !self.done.iter().any(|v| v == id) { self.done.push_back(id.into()); }
        while self.done.len() > 256 { self.done.pop_front(); }
    }
    /// 次の停止を予約する。10秒の命令更新は撮影周期ではなく、切断時に予約が残り続けないための期限。
    /// 身体・世界・接続が失効した画像を別の状態へ持ち越さず、停止番号で再撮影を抑える。
    pub fn arm(&mut self, sid: &str, context: Option<Context>, now: DateTime<Utc>) {
        if self.context != context || !self.enabled {
            if let Some(p) = self.pending.take() { self.finish(p.command["command_id"].as_str().unwrap_or("")); }
            self.latest = None; self.ready = false; self.sequence = 0; self.context = context.clone();
        }
        let Some(context) = context.filter(|_| self.enabled) else { return; };
        if self.pending.as_ref().is_some_and(|p| now >= p.expires) {
            let id = self.pending.as_ref().unwrap().command["command_id"].as_str().unwrap().to_owned(); self.finish(&id);
        }
        if self.pending.is_some() { return; }
        let expires = now + chrono::Duration::seconds(10);
        let command = json!({"type":"capture_body_view","command_id":format!("view_{}",uuid::Uuid::new_v4()),"session_id":sid,
            "issued_at":now,"expires_at":expires,"body_entity_uuid":context.body_id,"body_epoch":context.epoch,
            "owner_uuid":context.owner_id,"dimension":context.dimension,"after_motion_sequence":self.sequence});
        self.pending = Some(Pending {command,context,issued:now,expires});
    }
    pub fn snapshot(&self) -> Value {
        json!({"supported":self.enabled,"capture_trigger":"body_stopped","pending":self.pending.as_ref().map(|p| json!({
            "command_id":p.command["command_id"],"phase":"waiting_for_stop","issued_at":p.issued,"expires_at":p.expires})),
            "latest":self.latest.as_ref().map(|v| json!({"capture_id":v.capture_id,"motion_sequence":v.sequence,
                "captured_at":v.image.captured_at,"sha256":v.image.sha256,"change":v.change})),"notice_pending":self.ready})
    }
    pub fn owns(&self, id: &str) -> bool {
        self.pending.as_ref().is_some_and(|p| p.command["command_id"] == id) || self.done.iter().any(|v| v == id)
    }
    pub fn commands(&self, now: DateTime<Utc>) -> Vec<Value> {
        self.pending.as_ref().filter(|p| now < p.expires).map(|p| vec![p.command.clone()]).unwrap_or_default()
    }
    /// 一枚を検証して最新へ置換し、受信ACKを返す。生成中の一枚はSpeechが別に所有するため再送保持は不要。
    /// 同じ地点で4枚前と同等なら画像は更新するが、変化と新たなモデル判断は作らない。
    pub fn receive(&mut self, result: &AdapterCommandResult, now: DateTime<Utc>) -> bool {
        let Some(p) = self.pending.as_ref().filter(|p| p.command["command_id"] == result.command_id) else { return true; };
        let captured = (now < p.expires && result.status == Status::Succeeded).then(|| parse_image(result,p,now)).flatten();
        if let Some(mut captured) = captured.filter(|v| v.sequence > self.sequence) {
            let changed = captured.change["visual_change"] == true;
            captured.change["previous_sha256"] = json!(self.latest.as_ref().map(|v| &v.image.sha256));
            self.sequence = captured.sequence; self.ready = changed;
            tracing::info!(event="body_stop_image_received",command_id=result.command_id,capture_id=captured.capture_id,
                motion_sequence=captured.sequence,visual_change=changed,compared_to_four_before=%captured.change["compared_to_four_before"]);
            self.latest = Some(captured);
        } else { tracing::info!(event="body_stop_image_rejected", command_id=result.command_id, detail=result.detail_code); }
        self.finish(&result.command_id); true
    }
}
fn parse_image(result: &AdapterCommandResult, pending: &Pending, now: DateTime<Utc>) -> Option<Captured> {
    let v = &result.body_view.as_ref()?.0;
    if v["camera_restored"] != true || v["body_entity_uuid"] != pending.context.body_id
        || v["body_epoch"] != pending.context.epoch || v["owner_uuid"] != pending.context.owner_id
        || v["dimension"] != pending.context.dimension { return None; }
    let captured = DateTime::parse_from_rfc3339(v["captured_at"].as_str()?).ok()?.with_timezone(&Utc);
    if captured < pending.issued || captured > now || captured >= pending.expires { return None; }
    let capture_id = v["capture_id"].as_str()?.strip_prefix("view_")?;
    if uuid::Uuid::parse_str(capture_id).ok()?.to_string() != capture_id { return None; }
    let sequence = v["motion_sequence"].as_u64()?;
    if sequence == 0 || sequence > i64::MAX as u64 { return None; }
    let changed = v["visual_change"].as_bool()?;
    let compared = v["compared_to_four_before"].as_bool()?;
    if !changed && !compared { return None; }
    let image = Attachment { png_base64:v["png_base64"].as_str()?.into(), sha256:v["sha256"].as_str()?.into(),
        width:v["width"].as_u64()?.try_into().ok()?, height:v["height"].as_u64()?.try_into().ok()?,
        captured_at:v["captured_at"].as_str()?.into(), annotation_status:v["annotation_status"].as_str()?.into() };
    image.validate().ok()?;
    Some(Captured { image,context:pending.context.clone(),capture_id:format!("view_{capture_id}"),sequence,
        change:json!({"visual_change":changed,"compared_to_four_before":compared,"captured_at":captured}) })
}

/// 優先処理が空いた時に最新の変化だけを既存noticeへ渡す。古い画像を待ち列へ蓄積しない。
/// ここは画像と撮影情報だけを添える。全観測・変化表・履歴は環境runtimeが一緒に組み立てる。
pub(super) fn opportunity(s: &mut Session) -> Option<crate::combat::model::Speech> {
    if !s.visual.ready || s.preview || s.foreground.game_paused || s.foreground.combat_active
        || !matches!(s.foreground.route,crate::foreground::Route::None|crate::foreground::Route::Casual) { return None; }
    let captured = s.visual.latest.as_ref()?;
    let sample = s.body_observation.accepted(Instant::now())?;
    if sample.context != captured.context { return None; }
    let at = DateTime::parse_from_rfc3339(&captured.image.captured_at).ok()?.with_timezone(&Utc);
    if Utc::now()-at > chrono::Duration::seconds(IMAGE_FRESH_SECONDS) { s.visual.ready=false;return None; }
    let mut action = crate::environment::notice::action();
    action.image = Some(Arc::new(captured.image.clone()));
    let details = &mut action.leaf.as_mut().unwrap().details;
    details["visual_capture"] = json!({"capture_id":captured.capture_id,"captured_at":captured.image.captured_at,
        "motion_sequence":captured.sequence,"previous_sha256":captured.change["previous_sha256"]});
    action.text.clear(); // 画像の意味をコード固定文で代弁しない。
    s.visual.ready = false;
    Some(action)
}

/// 通常会話も最後に受信した一枚を参照する。会話ごとの新規撮影要求は発行しない。
#[derive(Clone)]
pub(super) struct Source { owner: Weak<Dialogue>, sid: String, instance: String, turn: String, epoch: u64 }
impl Source {
    pub fn new(owner: &Arc<Dialogue>, sid: &str, turn: &str, epoch: u64) -> Option<Self> {
        let data=owner.data.lock().unwrap();let s=data.sessions.get(sid)?;
        Some(Self {owner:Arc::downgrade(owner),sid:sid.into(),instance:s.instance_id.clone(),turn:turn.into(),epoch})
    }
    fn current(&self, s: &Session) -> bool { s.instance_id==self.instance && s.epoch==self.epoch && s.current_turn==self.turn }
    pub async fn capture(&self, cancel: &mut watch::Receiver<bool>) -> Option<Captured> {
        let owner=self.owner.upgrade()?;
        if !owner.config.chat_image.enabled { return None; }
        let deadline=tokio::time::Instant::now()+Duration::from_millis(owner.config.chat_image.wait_ms);
        loop {
            if *cancel.borrow() || cancel.has_changed().is_err() { return None; }
            {
                let data=owner.data.lock().unwrap();let s=data.sessions.get(&self.sid)?;
                if !self.current(s) || s.preview || !s.visual.enabled || s.foreground.game_paused { return None; }
                let context=&s.body_observation.accepted(Instant::now())?.context;
                if let Some(captured)=s.visual.latest.as_ref().filter(|v| &v.context==context) { return Some(captured.clone()); }
            }
            if tokio::time::Instant::now()>=deadline { return None; }
            tokio::select! {biased;_=bridge::cancelled(cancel)=>return None,_=tokio::time::sleep(Duration::from_millis(25))=>{}}
        }
    }
    /// 撮影を待った後の現在観測と配送済み履歴を再取得する。画像の撮影時刻は別材料として保持する。
    pub fn refresh(&self, input: &bridge::Input, captured: Option<&Context>) -> Option<(chat_context::Native, bool)> {
        let owner = self.owner.upgrade()?;
        let data = owner.data.lock().unwrap();
        let s = data.sessions.get(&self.sid)?;
        if !self.current(s) { return None; }
        let (_, previous) = input.snapshot.as_deref()?;
        let mut history = previous.context.history.clone();
        history.conversation_turns = s.history.refresh_before_input(&self.turn, &history.conversation_turns, input.retained_history_count);
        history.conversation_history = super::history::History::lines_for(&history.conversation_turns);
        let image_current = captured.is_some_and(|context| s.body_observation.accepted(Instant::now()).is_some_and(|sample| &sample.context == context));
        let native = chat_context::capture(s, s.latest.as_ref()?, &owner.config.combat, history, None).ok()?;
        Some((native, image_current))
    }
}
#[cfg(test)]
mod tests;
