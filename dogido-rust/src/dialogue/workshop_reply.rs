//! パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
//! 受付済みの川柳返答を、同じworker・生成結果・保存処理のまま戦闘後へ保留する。
//! 戦闘は再開可能なpause、手動停止や接続変更は既存cancelで終端する。音声は未完了の文から戻す。
use super::{Data, Dialogue, Session, bridge};
use anyhow::{Result, bail, ensure};
use serde_json::Value;
use std::{
    future::Future,
    sync::{Arc, Mutex},
};
use tokio::sync::{OwnedSemaphorePermit, Semaphore, watch};

/// 直列枠は保留中に警告へ譲る。worker自体は保持し、生成・保存の再実行を防ぐ。
pub(super) struct Permit {
    serial: Arc<Semaphore>,
    held: Mutex<Option<OwnedSemaphorePermit>>,
    yield_on_pause: bool,
}
impl Permit {
    pub fn new(serial: &Arc<Semaphore>) -> Self {
        Self {
            serial: serial.clone(),
            held: Mutex::new(None),
            yield_on_pause: true,
        }
    }
    /// 生成・保存の順序は保存完了まで固定する。警告は別の音声枠を使う。
    pub fn generation(serial: &Arc<Semaphore>) -> Self {
        Self {
            yield_on_pause: false,
            ..Self::new(serial)
        }
    }
    fn suspend(&self) {
        if self.yield_on_pause {
            self.release();
        }
    }
    pub fn release(&self) {
        self.held.lock().unwrap().take();
    }
    pub async fn acquire(&self) {
        if self.held.lock().unwrap().is_none() {
            let permit = self.serial.clone().acquire_owned().await.unwrap();
            *self.held.lock().unwrap() = Some(permit);
        }
    }
}

pub(super) struct State {
    turn: String,
    epoch: u64,
    workshop: String,
    pause: watch::Sender<bool>,
    retained: bool,
    display: Option<super::text_delivery::Kind>,
}
impl State {
    pub fn new(turn: &str, epoch: u64, workshop: &str) -> Self {
        Self {
            turn: turn.into(),
            epoch,
            workshop: workshop.into(),
            pause: watch::channel(false).0,
            retained: false,
            display: None,
        }
    }
    pub fn paused(&self) -> bool {
        *self.pause.borrow()
    }
}
pub(super) fn retained(s: &Session) -> bool {
    s.workshop_reply.as_ref().is_some_and(|r| r.retained)
}

pub(super) fn paused(s: &Session) -> bool {
    s.workshop_reply.as_ref().is_some_and(State::paused)
}

/// 元turnと接続の寿命へ固定した継続権。pauseでepochを変えず、永久取消のepochだけを検証する。
pub(super) struct Source<'a> {
    owner: &'a Dialogue,
    sid: String,
    instance: String,
    turn: String,
    epoch: u64,
    pause: watch::Receiver<bool>,
    cancel: watch::Receiver<bool>,
}
impl<'a> Source<'a> {
    pub fn new(owner: &'a Dialogue, input: &Value, cancel: &watch::Receiver<bool>) -> Option<Self> {
        let d = owner.data.lock().unwrap();
        let (sid, s) = d
            .sessions
            .iter()
            .find(|(_, s)| s.current_turn == input["operation_id"])?;
        let state = s.workshop_reply.as_ref()?;
        Some(Self {
            owner,
            sid: sid.clone(),
            instance: s.instance_id.clone(),
            turn: state.turn.clone(),
            epoch: state.epoch,
            pause: state.pause.subscribe(),
            cancel: cancel.clone(),
        })
    }
    fn current(&self) -> bool {
        let d = self.owner.data.lock().unwrap();
        !d.stopped
            && d.sessions.get(&self.sid).is_some_and(|s| {
                s.instance_id == self.instance
                    && (super::reply_output::current(s, &self.turn, self.epoch)
                        || (s.epoch == self.epoch
                            && s.current_turn == self.turn
                            && s.cancel.as_ref().is_some_and(|c| !*c.borrow())
                            && s.workshop_reply.as_ref().is_some_and(|r| {
                                r.turn == self.turn
                                    && s.haiku
                                        .workshop
                                        .as_ref()
                                        .is_some_and(|w| w.hud_id == r.workshop)
                            })))
            })
    }
    pub fn paused(&self) -> bool {
        *self.pause.borrow()
    }

    /// Queuedの候補・終了処理は一度だけ通す。直前に戦闘へ戻った場合は未適用のまま待つ。
    pub async fn queue(&self, permit: &Permit, result: &Value) -> bool {
        loop {
            if self.ready(permit).await.is_err() {
                return false;
            }
            if self.owner.update(
                &self.sid,
                &self.turn,
                self.epoch,
                crate::playback::Status::Queued,
                Some(result),
            ) {
                return true;
            }
            if !self.paused() {
                return false;
            }
        }
    }
    /// 再開案内の配送と現在観測の安全確認が終わるまで、追加生成・操作・配送を進めない。
    pub async fn ready(&self, permit: &Permit) -> Result<()> {
        let mut pause = self.pause.clone();
        let mut cancel = self.cancel.clone();
        loop {
            ensure!(self.current(), "cancelled workshop continuation");
            if *pause.borrow_and_update() {
                permit.suspend();
                tokio::select! { biased;
                    _ = bridge::cancelled(&mut cancel) => bail!("cancelled"),
                    changed = pause.changed() => { ensure!(changed.is_ok(), "closed workshop continuation"); }
                }
                continue;
            }
            tokio::select! { biased;
                _ = bridge::cancelled(&mut cancel) => bail!("cancelled"),
                changed = pause.changed() => { ensure!(changed.is_ok(), "closed workshop continuation"); continue; },
                _ = permit.acquire() => {}
            }
            if !*pause.borrow() {
                ensure!(self.current(), "cancelled workshop continuation");
                return Ok(());
            }
        }
    }
    /// 保存開始前の照合と戦闘pauseが競合したときだけ、未実行の照合から待ち直す。
    pub async fn check<T>(&self, permit: &Permit, check: impl Fn() -> Result<T>) -> Result<T> {
        loop {
            self.ready(permit).await?;
            let result = check();
            if result.is_err() && self.paused() {
                continue;
            }
            return result;
        }
    }

    /// 開始済みの生成は受け取る。保留中は直列枠を離し、結果の適用前に同じ継続権で待つ。
    pub async fn during<T>(&self, permit: &Permit, future: impl Future<Output = T>) -> Result<T> {
        self.ready(permit).await?;
        let mut pause = self.pause.clone();
        let mut cancel = self.cancel.clone();
        tokio::pin!(future);
        loop {
            if *pause.borrow_and_update() {
                permit.suspend();
            }
            tokio::select! { biased;
                _ = bridge::cancelled(&mut cancel) => bail!("cancelled"),
                changed = pause.changed() => { ensure!(changed.is_ok(), "closed workshop continuation"); },
                result = &mut future => { self.ready(permit).await?; return Ok(result); }
            }
        }
    }
    /// 開始済みの保存は取消でも最後まで回収する。戦闘待ちの間は警告に枠を譲る。
    pub async fn transaction<T>(&self, permit: &Permit, future: impl Future<Output = T>) -> T {
        let mut pause = self.pause.clone();
        let mut cancel = self.cancel.clone();
        if *pause.borrow_and_update() {
            permit.suspend();
        }
        tokio::pin!(future);
        loop {
            tokio::select! { biased;
                _ = bridge::cancelled(&mut cancel) => { permit.suspend(); return future.await; },
                changed = pause.changed() => {
                    if changed.is_err() {
                        permit.suspend(); return future.await;
                    }
                    if *pause.borrow_and_update() { permit.suspend(); }
                },
                result = &mut future => return result,
            }
        }
    }

    /// 再生済み文の次から配送する。中断した文だけ文頭から読み直し、playerの回収後に枠を譲る。
    pub async fn speak(
        &self,
        permit: &Permit,
        config: &super::DialogueConfig,
        spoken: &str,
    ) -> Result<()> {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let completed = AtomicUsize::new(0);
        loop {
            self.ready(permit).await?;
            let mut pause = self.pause.clone();
            if *pause.borrow_and_update() {
                continue;
            }
            let mut cancel = self.cancel.clone();
            let (stop, mut audio_cancel) = watch::channel(false);
            let playback = self.owner.audio.speak_from(
                config,
                spoken,
                completed.load(Ordering::Relaxed),
                &mut audio_cancel,
                || {
                    self.owner.update(
                        &self.sid,
                        &self.turn,
                        self.epoch,
                        crate::playback::Status::Started,
                        None,
                    );
                },
                |count| {
                    completed.store(count, Ordering::Relaxed);
                },
            );
            tokio::pin!(playback);
            let result = tokio::select! { biased;
                _ = bridge::cancelled(&mut cancel) => {
                    let _ = stop.send(true); let _ = playback.await; bail!("cancelled");
                },
                changed = pause.changed() => {
                    let _ = stop.send(true); let _ = playback.await;
                    ensure!(changed.is_ok(), "closed workshop continuation");
                    permit.suspend(); continue;
                },
                result = &mut playback => result,
            };
            result?;
            self.ready(permit).await?;
            return Ok(());
        }
    }
}

impl Dialogue {
    /// 戦闘だけを保留へ変える。全文配送済み・別turn・閉じた接続は既存の取消へ渡す。
    pub(super) fn pause_workshop_reply(&self, d: &mut Data, sid: &str) -> bool {
        let Some(s) = d.sessions.get_mut(sid) else {
            return false;
        };
        let mut current_held = false;
        for state in s.workshop_reply.iter_mut().chain(
            s.reply_outputs
                .iter_mut()
                .filter_map(|output| output.workshop.as_mut()),
        ) {
            let Some(row) = d
                .rows
                .iter_mut()
                .find(|r| r["session_id"] == sid && r["turn_id"] == state.turn)
            else {
                continue;
            };
            if row["delivery_confirmed"] == true {
                continue;
            }
            current_held |= state.turn == s.current_turn && state.epoch == s.epoch;
            if state.paused() {
                continue;
            }
            state.display = s.delivery.suspend_reply(&state.turn);
            row["display_id"] = Value::Null;
            state.retained = true;
            state.pause.send_replace(true);
            row["reply_delivery"] = "held_combat".into();
            row["playback_status"] = "waiting_for_warning".into();
            d.revision += 1;
        }
        current_held
    }
    /// 現在の入力とは別に配送中の川柳も、同じworkerの未完了文から再開する。
    pub(super) fn resume_workshop_reply(&self, d: &mut Data, sid: &str) {
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        if !super::workshop_combat_runtime::clear_for_resume(s)
            || s.warning.is_some()
            || s.pending_warning.is_some()
        {
            return;
        }
        let mut displays = Vec::new();
        for (state, detached) in s
            .workshop_reply
            .iter_mut()
            .map(|state| (state, false))
            .chain(
                s.reply_outputs
                    .iter_mut()
                    .filter_map(|output| output.workshop.as_mut())
                    .map(|state| (state, true)),
            )
        {
            if !state.paused()
                || (!detached
                    && !s
                        .haiku
                        .workshop
                        .as_ref()
                        .is_some_and(|w| w.hud_id == state.workshop && !w.combat_paused()))
            {
                continue;
            }
            state.pause.send_replace(false);
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == state.turn) {
                row["reply_delivery"] = "resumed_combat".into();
                row["playback_status"] = if row["text"].as_str().is_some_and(|s| !s.is_empty()) {
                    "queued"
                } else {
                    "generating"
                }
                .into();
            }
            if let Some(kind) = state.display.take() {
                displays.push((state.turn.clone(), kind));
            }
            d.revision += 1;
        }
        for (turn, kind) in displays {
            Self::publish_text(d, sid, &turn, kind);
        }
    }
}

#[cfg(test)]
mod permit_tests {
    use super::*;
    // 見込み: 取消後も開始済み保存を回収し、その前に次の生成・保存を通さない。
    // 合格: 保存gateが閉じている間は生成枠を取得不可。終了後は取得可。実ディスクは対象外。
    #[tokio::test]
    async fn cancelled_save_keeps_generation_order_until_collected() {
        let owner = Dialogue::new(super::super::DialogueConfig::default()).unwrap();
        let (cancel_tx, cancel) = watch::channel(false);
        let (_pause_tx, pause) = watch::channel(false);
        let source = Source {
            owner: &owner,
            sid: "test".into(),
            instance: "test".into(),
            turn: "test".into(),
            epoch: 0,
            pause,
            cancel,
        };
        let serial = Arc::new(Semaphore::new(1));
        let permit = Permit::generation(&serial);
        permit.acquire().await;
        let release = tokio::sync::Notify::new();
        let mut save = Box::pin(source.transaction(&permit, release.notified()));
        cancel_tx.send(true).unwrap();
        tokio::select! {
            _ = &mut save => panic!("save completed before its gate opened"),
            _ = tokio::time::sleep(std::time::Duration::from_millis(20)) => {}
        }
        assert!(
            serial.try_acquire().is_err(),
            "next save must not overtake the active save"
        );
        release.notify_one();
        save.await;
        permit.release();
        assert!(serial.try_acquire().is_ok());
        // 音声枠はこれまでどおり戦闘pauseで譲れる。
        let audio = Permit::new(&serial);
        audio.acquire().await;
        audio.suspend();
        assert!(serial.try_acquire().is_ok());
        owner.shutdown().await;
    }
}
