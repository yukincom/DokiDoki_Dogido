//! 共通受付の依頼分類を既存Helperで直列実行し、原入力・接続・世代へ結果を戻す。
//! 通常の分類待ちは新入力で取消。危険中の入力は既存reply_queueが寿命を所有する。
//! モデルは依頼の種類だけを返し、操作・候補選択・履歴確定は既存runtimeへ渡す。
//! 意図分類のBERT移行は完了。旧OSAI・chat分類fallbackの経路は撤去済みで、復活させない。
use super::{Data, Dialogue, DialogueConfig, Session, bridge, id};
use crate::request_classifier::{Classification, Classifier, Input};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::{sync::watch, task::JoinHandle};

/// model_root未指定だけが無効。指定後の読込・推論失敗では従来の依頼判定へ戻さない。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub model_root: Option<PathBuf>,
    pub python: Option<PathBuf>,
    pub load_timeout_ms: u64,
    pub timeout_ms: u64,
}
impl Default for Settings {
    fn default() -> Self {
        // serde(default)の再帰を避け、値だけを共有既定値から取得する。
        let values = crate::runtime_settings::defaults("request_classifier");
        Self {
            model_root: serde_json::from_value(values["model_root"].clone()).unwrap(),
            python: serde_json::from_value(values["python"].clone()).unwrap(),
            load_timeout_ms: values["load_timeout_ms"].as_u64().unwrap(),
            timeout_ms: values["timeout_ms"].as_u64().unwrap(),
        }
    }
}
impl Settings {
    pub fn enabled(&self) -> bool {
        self.model_root.is_some()
    }
    pub(super) fn validate(&self) -> Result<()> {
        ensure!(
            (1..=300_000).contains(&self.load_timeout_ms)
                && (1..=300_000).contains(&self.timeout_ms),
            "invalid request classifier timeout"
        );
        if let Some(root) = &self.model_root {
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

#[derive(Default)]
pub(super) struct State {
    pub pending: Option<Pending>,
    checked: Option<(u64, Classification)>,
}
pub(super) struct Pending {
    pub turn: String,
    pub text: String,
    pub source: String,
    generation: u64,
    instance: String,
    cancel: watch::Sender<bool>,
}
impl State {
    pub fn get(&self, generation: u64, text: &str, source: &str) -> Option<&Classification> {
        self.checked
            .as_ref()
            .filter(|(g, result)| *g == generation && result.matches(text, source))
            .map(|(_, result)| result)
    }
    pub fn restore(&mut self, generation: u64, result: Option<Classification>) {
        self.checked = result.map(|result| (generation, result));
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

impl Dialogue {
    /// 通常起動では待受開始前に一度読み込む。失敗は起動元へ返す。
    pub async fn prepare_request_classifier(&self) -> Result<()> {
        if !self.config.request_classifier.enabled() {
            return Ok(());
        }
        let (_owner, mut cancel) = watch::channel(false);
        let mut slot = self.request_classifier.lock().await;
        if slot.is_none() {
            *slot = Some(start(&self.config, &mut cancel).await?);
        }
        tracing::info!(
            event = "request_classifier_ready",
            manifest_sha256 = slot.as_ref().unwrap().manifest_sha256()
        );
        Ok(())
    }

    /// 現在発話だけを既存形式で分類する。履歴は操作依頼を再発行させないため渡さない。
    /// 一つの共有workerを使う。待機も取消可能で、失敗したworkerは回収して次回に再読込する。
    pub(super) async fn classify_request(
        &self,
        text: &str,
        source: &str,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Classification> {
        let input = Input {
            text: text.into(),
            context: vec![],
        };
        let mut slot = tokio::select! { biased;
            _ = bridge::cancelled(cancel) => anyhow::bail!("request classifier cancelled"),
            slot = self.request_classifier.lock() => slot,
        };
        if slot.is_none() {
            *slot = Some(start(&self.config, cancel).await?);
        }
        let classifier = slot.as_mut().unwrap();
        let manifest_sha256 = classifier.manifest_sha256().to_owned();
        let result = classifier
            .classify(
                &input,
                Duration::from_millis(self.config.request_classifier.timeout_ms),
                cancel,
            )
            .await;
        match result {
            Ok(prediction) => Ok(Classification {
                text: input.text,
                source: source.into(),
                manifest_sha256,
                prediction,
            }),
            Err(error) => {
                if let Some(classifier) = slot.take() {
                    let _ = classifier.close().await;
                }
                Err(error)
            }
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
        let instance = s.instance_id.clone();
        s.request_classification.pending = Some(Pending {
            turn: turn.clone(),
            text: text.into(),
            source: source.into(),
            generation,
            instance: instance.clone(),
            cancel,
        });
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(
            json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":sid,
            "category":"routing","text":"","player_input_text":text,"source":source,
            "created_at":chrono::Utc::now(),"playback_status":"routing"}),
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
            this.run_request_classification(sid, tid, generation, instance, text, source, rx)
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
        text: String,
        source: String,
        mut cancel: watch::Receiver<bool>,
    ) {
        let result = self.classify_request(&text, &source, &mut cancel).await;
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
                })
            {
                return;
            }
            s.request_classification.pending = None;
            if let Ok(classification) = &result {
                s.request_classification
                    .restore(generation, Some(classification.clone()));
            }
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                match &result {
                    Ok(classification) => {
                        row["playback_status"] = "not_selected".into();
                        row["request_classification"] = json!(classification);
                    }
                    Err(error) => {
                        row["playback_status"] = "failed".into();
                        row["resolution"] = "request_classification_failed".into();
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
            );
            let mut d = self.data.lock().unwrap();
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["forwarded_input"] = response;
            }
            d.revision += 1;
        }
    }
}

async fn start(config: &DialogueConfig, cancel: &mut watch::Receiver<bool>) -> Result<Classifier> {
    let settings = &config.request_classifier;
    let root = settings
        .model_root
        .as_ref()
        .context("request classifier disabled")?;
    Classifier::start(
        settings
            .python
            .as_ref()
            .context("request classifier Python missing")?,
        &config.helper.with_file_name("request_classifier_worker.py"),
        &root.join("adopted-model.json"),
        root,
        Duration::from_millis(settings.load_timeout_ms),
        cancel,
    )
    .await
}

#[cfg(test)]
mod tests;
