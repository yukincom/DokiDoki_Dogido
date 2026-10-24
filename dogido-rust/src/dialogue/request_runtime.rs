//! 共通受付の依頼分類を既存Helperで直列実行し、原入力・接続・世代へ結果を戻す。
//! 通常の分類待ちは新入力で取消。危険中の入力は既存reply_queueが寿命を所有する。
//! モデルは依頼の種類だけを返し、操作・候補選択・履歴確定は既存runtimeへ渡す。
//! 意図分類のBERT移行は完了。旧OSAI・chat分類fallbackの経路は撤去済みで、復活させない。
use super::{Data, Dialogue, DialogueConfig, Session, bridge, id};
use crate::request_classifier::{Classification, Classifier, Input, Mode};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::{sync::watch, task::JoinHandle};

/// 二領域とも未指定なら分類なし。片側未準備・失敗でも別領域や旧判定へ戻さない。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub normal_model_root: Option<PathBuf>,
    pub senryu_model_root: Option<PathBuf>,
    pub python: Option<PathBuf>,
    pub load_timeout_ms: u64,
    pub timeout_ms: u64,
}
impl Default for Settings {
    fn default() -> Self {
        // serde(default)の再帰を避け、値だけを共有既定値から取得する。
        let values = crate::runtime_settings::defaults("request_classifier");
        Self {
            normal_model_root: serde_json::from_value(values["normal_model_root"].clone()).unwrap(),
            senryu_model_root: serde_json::from_value(values["senryu_model_root"].clone()).unwrap(),
            python: serde_json::from_value(values["python"].clone()).unwrap(),
            load_timeout_ms: values["load_timeout_ms"].as_u64().unwrap(),
            timeout_ms: values["timeout_ms"].as_u64().unwrap(),
        }
    }
}
impl Settings {
    pub fn enabled(&self) -> bool {
        self.normal_model_root.is_some() || self.senryu_model_root.is_some()
    }
    pub fn model_root(&self, mode: Mode) -> Option<&PathBuf> {
        match mode {
            Mode::Normal => self.normal_model_root.as_ref(),
            Mode::Senryu => self.senryu_model_root.as_ref(),
        }
    }
    pub(super) fn validate(&self) -> Result<()> {
        ensure!(
            (1..=300_000).contains(&self.load_timeout_ms)
                && (1..=300_000).contains(&self.timeout_ms),
            "invalid request classifier timeout"
        );
        for root in [&self.normal_model_root, &self.senryu_model_root]
            .into_iter()
            .flatten()
        {
            ensure!(
                root.join("adopted-model.json").is_file(),
                "request classifier manifest missing"
            );
            ensure!(
                self.python.as_ref().is_some_and(|p| p.is_file()),
                "request classifier Python missing"
            );
        }
        Ok(())
    }
}

/// 一回の入力が属する受付状態。句の版と中断の境界は別に照合する。
/// UIの一時非表示は含めず、同じ句へのpause→resumeも遅延結果には新しい境界とする。
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub(super) struct Binding {
    pub mode: Mode,
    instance: String,
    workshop: Option<WorkshopBinding>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
struct WorkshopBinding {
    id: String,
    version: u64,
    boundary: u64,
}
impl Binding {
    pub fn capture(s: &Session) -> Option<Self> {
        if !super::observation_fresh(s) {
            return None;
        }
        let senryu = super::workshop_combat_input::allowed(s)
            && s.haiku
                .workshop
                .as_ref()
                .is_some_and(|w| w.open && !w.combat_paused());
        Some(Self {
            mode: if senryu { Mode::Senryu } else { Mode::Normal },
            instance: s.instance_id.clone(),
            workshop: s.haiku.workshop.as_ref().map(|w| WorkshopBinding {
                id: w.hud_id.clone(),
                version: w.version,
                boundary: w.input_boundary,
            }),
        })
    }
    pub fn current(&self, s: &Session) -> bool {
        Self::capture(s).as_ref() == Some(self)
    }
    /// 分類完了済みの川柳入力だけを保留するための照合。別の句・版へは持ち越さない。
    pub fn same_open_workshop(&self, s: &Session) -> bool {
        self.mode == Mode::Senryu
            && self.instance == s.instance_id
            && self.workshop.as_ref().is_some_and(|target| {
                s.haiku
                    .workshop
                    .as_ref()
                    .is_some_and(|w| w.open && w.hud_id == target.id && w.version == target.version)
            })
    }
}

/// 録音期間の識別子。voice on/offの世代と同じ分類境界を読み取りだけで投影する。
/// 同じモードへ戻るABAや句の入替えでも、古いSTTを現在入力へ付け替えない。
pub(super) fn voice_generation(s: &Session) -> String {
    use sha2::{Digest, Sha256};
    let bytes = serde_json::to_vec(&Binding::capture(s)).expect("routing binding is serializable");
    format!("{}:{:x}", s.delivery.generation, Sha256::digest(bytes))
}

#[derive(Default)]
pub(super) struct State {
    pub pending: Option<Pending>,
    checked: Option<(u64, Binding, Classification)>,
}
pub(super) struct Pending {
    pub turn: String,
    pub text: String,
    pub source: String,
    generation: u64,
    instance: String,
    pub binding: Binding,
    cancel: watch::Sender<bool>,
}
impl State {
    pub fn get(&self, generation: u64, text: &str, source: &str) -> Option<&Classification> {
        self.checked
            .as_ref()
            .filter(|(g, _, result)| *g == generation && result.matches(text, source))
            .map(|(_, _, result)| result)
    }
    pub fn binding(&self, generation: u64, text: &str, source: &str) -> Option<&Binding> {
        self.checked
            .as_ref()
            .filter(|(g, _, result)| *g == generation && result.matches(text, source))
            .map(|(_, binding, _)| binding)
    }
    pub fn restore(
        &mut self,
        generation: u64,
        binding: Option<Binding>,
        result: Option<Classification>,
    ) {
        self.checked = binding
            .zip(result)
            .map(|(binding, result)| (generation, binding, result));
    }
}
pub(super) fn classification<'a>(
    s: &'a Session,
    text: &str,
    source: &str,
) -> Option<&'a Classification> {
    s.request_classification
        .get(s.input_generation, text, source)
}

/// 二モデルのハンドルを一つのMutexで所有する。読込・推論・取消回収も同じ排他区間。
#[derive(Default)]
pub(super) struct Models {
    normal: Option<Classifier>,
    senryu: Option<Classifier>,
}
impl Models {
    fn slot(&mut self, mode: Mode) -> &mut Option<Classifier> {
        match mode {
            Mode::Normal => &mut self.normal,
            Mode::Senryu => &mut self.senryu,
        }
    }
    pub async fn close(&mut self) {
        for slot in [&mut self.normal, &mut self.senryu] {
            if let Some(classifier) = slot.take() {
                let _ = classifier.close().await;
            }
        }
    }
}

impl Dialogue {
    /// 指定された側だけ待受開始前に読み込む。未準備の側を別モデルで補わない。
    pub async fn prepare_request_classifier(&self) -> Result<()> {
        let (_owner, mut cancel) = watch::channel(false);
        let mut models = self.request_classifier.lock().await;
        for mode in [Mode::Normal, Mode::Senryu] {
            if self.config.request_classifier.model_root(mode).is_none() {
                continue;
            }
            let slot = models.slot(mode);
            if slot.is_none() {
                *slot = Some(start(&self.config, mode, &mut cancel).await?);
            }
            tracing::info!(
                event = "request_classifier_ready",
                mode = mode.name(),
                manifest_sha256 = slot.as_ref().unwrap().manifest_sha256()
            );
        }
        Ok(())
    }

    /// 受付時の領域へ一度だけ送る。共有排他の待機中も対象を照合し、取消後は回収まで待つ。
    pub(super) async fn classify_request(
        &self,
        sid: &str,
        binding: &Binding,
        text: &str,
        source: &str,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Classification> {
        let input = Input {
            text: text.into(),
            context: vec![],
        };
        let mut models = tokio::select! { biased;
            _ = bridge::cancelled(cancel) => anyhow::bail!("request classifier cancelled"),
            _ = self.wait_classifier_binding_change(sid, binding) => anyhow::bail!("classifier binding changed"),
            models = self.request_classifier.lock() => models,
        };
        ensure!(
            self.classifier_binding_current(sid, binding),
            "classifier binding changed"
        );
        let result = {
            let slot = models.slot(binding.mode);
            // ロード中の取消にも既存Helperの回収を使い、終了前に次の領域を動かさない。
            let (stop, mut local_cancel) = watch::channel(false);
            let operation = async {
                if slot.is_none() {
                    *slot = Some(start(&self.config, binding.mode, &mut local_cancel).await?);
                }
                ensure!(
                    self.classifier_binding_current(sid, binding),
                    "classifier binding changed"
                );
                let classifier = slot.as_mut().unwrap();
                let manifest_sha256 = classifier.manifest_sha256().to_owned();
                let prediction = classifier
                    .classify(
                        &input,
                        Duration::from_millis(self.config.request_classifier.timeout_ms),
                        &mut local_cancel,
                    )
                    .await?;
                Ok(Classification {
                    mode: binding.mode,
                    text: input.text.clone(),
                    source: source.into(),
                    manifest_sha256,
                    prediction,
                })
            };
            tokio::pin!(operation);
            tokio::select! { biased;
                _ = bridge::cancelled(cancel) => {
                    let _ = stop.send(true); let _ = operation.await;
                    Err(anyhow::anyhow!("request classifier cancelled"))
                },
                _ = self.wait_classifier_binding_change(sid, binding) => {
                    let _ = stop.send(true); let _ = operation.await;
                    Err(anyhow::anyhow!("classifier binding changed"))
                },
                result = &mut operation => result,
            }
        };
        if result.is_err()
            && let Some(mut classifier) = models.slot(binding.mode).take()
        {
            classifier.abort().await;
        }
        result
    }

    fn classifier_binding_current(&self, sid: &str, binding: &Binding) -> bool {
        let d = self.data.lock().unwrap();
        !d.stopped && d.sessions.get(sid).is_some_and(|s| binding.current(s))
    }
    async fn wait_classifier_binding_change(&self, sid: &str, binding: &Binding) {
        // 分類待ちの間だけ既存の短いdataロックで確認する。常駐の別ルーターは作らない。
        loop {
            if !self.classifier_binding_current(sid, binding) {
                return;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }

    pub(super) fn cancel_request_classification(d: &mut Data, sid: &str) {
        let pending = d
            .sessions
            .get_mut(sid)
            .and_then(|s| s.request_classification.pending.take());
        if let Some(pending) = pending {
            let _ = pending.cancel.send(true);
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == pending.turn) {
                row["playback_status"] = "cancelled".into();
            }
            d.revision += 1;
        }
    }

    /// 受付の短い同期区間で現在の原文を固定し、既存jobsへ一件の分類を登録する。
    pub(super) fn start_request_classification(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        text: &str,
        source: &str,
    ) -> Value {
        Self::cancel_chat(d, sid, "new_player_input");
        let s = d.sessions.get_mut(sid).unwrap();
        let turn = id("request");
        let (cancel, rx) = watch::channel(false);
        let generation = s.input_generation;
        let Some(binding) = Binding::capture(s) else {
            return json!({"accepted":false,"reason":"fresh_safe_snapshot_required"});
        };
        let instance = s.instance_id.clone();
        s.request_classification.pending = Some(Pending {
            turn: turn.clone(),
            text: text.into(),
            source: source.into(),
            generation,
            instance: instance.clone(),
            binding: binding.clone(),
            cancel,
        });
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(
            json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":sid,
            "category":"routing","text":"","player_input_text":text,"source":source,
            "created_at":chrono::Utc::now(),"playback_status":"routing","classifier_mode":binding.mode}),
        );
        d.revision += 1;
        let (this, sid, tid, text, source) = (
            self.clone(),
            sid.to_owned(),
            turn.clone(),
            text.to_owned(),
            source.to_owned(),
        );
        jobs.push(tokio::spawn(async move {
            this.run_request_classification(
                sid, tid, generation, instance, binding, text, source, rx,
            )
            .await;
        }));
        json!({"accepted":true,"reason":"request_classification","turn_id":turn})
    }

    #[allow(clippy::too_many_arguments)] // 既存の接続・入力世代・原文を結果の取り違え防止に照合する。
    async fn run_request_classification(
        self: Arc<Self>,
        sid: String,
        turn: String,
        generation: u64,
        instance: String,
        binding: Binding,
        text: String,
        source: String,
        mut cancel: watch::Receiver<bool>,
    ) {
        let mut result = self
            .classify_request(&sid, &binding, &text, &source, &mut cancel)
            .await;
        let forward = {
            let mut d = self.data.lock().unwrap();
            if d.stopped || *cancel.borrow() {
                return;
            }
            let Some(s) = d.sessions.get_mut(&sid) else {
                return;
            };
            if s.instance_id != instance
                || s.input_generation != generation
                || !s.request_classification.pending.as_ref().is_some_and(|p| {
                    p.turn == turn
                        && p.generation == generation
                        && p.instance == instance
                        && p.text == text
                        && p.source == source
                        && p.binding == binding
                })
            {
                return;
            }
            let stale = !binding.current(s);
            if stale {
                result = Err(anyhow::anyhow!("classifier binding changed"));
            }
            s.request_classification.pending = None;
            if let Ok(classification) = &result {
                s.request_classification.restore(
                    generation,
                    Some(binding.clone()),
                    Some(classification.clone()),
                );
            }
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                match &result {
                    Ok(classification) => {
                        row["playback_status"] = "not_selected".into();
                        row["request_classification"] = json!(classification);
                    }
                    Err(error) => {
                        row["playback_status"] = if stale { "cancelled" } else { "failed" }.into();
                        row["resolution"] = if stale {
                            "superseded_classifier_mode"
                        } else {
                            "request_classification_failed"
                        }
                        .into();
                        row["error"] = error.to_string().into();
                    }
                }
            }
            d.revision += 1;
            result.is_ok()
        };
        if forward {
            let response = self.submit_recorded_guarded(
                Some(&sid),
                &text,
                &source,
                false,
                Some((generation, None)),
                false,
                super::workshop_record::Admission {
                    forwarded: true,
                    private: false,
                },
                Some(&instance),
                None,
            );
            let mut d = self.data.lock().unwrap();
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["forwarded_input"] = response;
            }
            d.revision += 1;
        }
    }
}

async fn start(
    config: &DialogueConfig,
    mode: Mode,
    cancel: &mut watch::Receiver<bool>,
) -> Result<Classifier> {
    let settings = &config.request_classifier;
    let root = settings
        .model_root(mode)
        .with_context(|| format!("{} classifier model is not configured", mode.name()))?;
    Classifier::start(
        settings
            .python
            .as_ref()
            .context("request classifier Python missing")?,
        &config.helper.with_file_name("request_classifier_worker.py"),
        &root.join("adopted-model.json"),
        root,
        mode,
        Duration::from_millis(settings.load_timeout_ms),
        cancel,
    )
    .await
}

#[cfg(test)]
mod tests;
