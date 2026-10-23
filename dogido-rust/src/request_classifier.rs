//! 採用済みModernBERTの依頼23分類を、既存Python Helper経由で受け取る。
//! 原文履歴の整形・応答検証・取消はRust、重みの検証と数値計算はPythonが担当する。
//! 本体の共通受付が結果を使い、実際の操作・会話状態と履歴の確定は各runtimeが所有する。
use crate::python_worker::Helper;
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{path::Path, time::Duration};
use tokio::sync::watch;

/// 学習時の整数ID順。確信度の閾値や別分類への読み替えは行わない。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Label {
    NotTarget,
    EnemyInformation,
    EquipSword,
    RecallMount,
    DogidoRecall,
    SenryuLineUpper,
    SenryuLineMiddle,
    SenryuLineLower,
    SenryuChangeUpper,
    SenryuChangeMiddle,
    SenryuChangeLower,
    LookDirection,
    SenryuEnd,
    LookRelativeRight,
    LookTarget,
    LookPlayer,
    LookRelativeFront,
    LookRelativeBack,
    LookRelativeLeft,
    LookRelativeFrontLeft,
    LookRelativeFrontRight,
    LookRelativeBackLeft,
    LookRelativeBackRight,
}
pub const LABEL_COUNT: usize = 23;
pub const LABELS: [Label; LABEL_COUNT] = [
    Label::NotTarget,
    Label::EnemyInformation,
    Label::EquipSword,
    Label::RecallMount,
    Label::DogidoRecall,
    Label::SenryuLineUpper,
    Label::SenryuLineMiddle,
    Label::SenryuLineLower,
    Label::SenryuChangeUpper,
    Label::SenryuChangeMiddle,
    Label::SenryuChangeLower,
    Label::LookDirection,
    Label::SenryuEnd,
    Label::LookRelativeRight,
    Label::LookTarget,
    Label::LookPlayer,
    Label::LookRelativeFront,
    Label::LookRelativeBack,
    Label::LookRelativeLeft,
    Label::LookRelativeFrontLeft,
    Label::LookRelativeFrontRight,
    Label::LookRelativeBackLeft,
    Label::LookRelativeBackRight,
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    User,
    Assistant,
}

/// 呼出元が選んだ古い順の発話。assistantは配送済みの原文だけを渡す。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Utterance {
    pub role: Role,
    pub text: String,
}

/// 現在発話は履歴に重複させない。全文の取得と履歴の選別は本体接続時の担当。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Input {
    pub text: String,
    pub context: Vec<Utterance>,
}

impl Input {
    /// dataset.model_exampleと同じ文字列を作る。空白や長文を正規化・短縮しない。
    pub fn render(&self, max_history_utterances: usize) -> Result<String> {
        ensure!(!self.text.trim().is_empty(), "empty current utterance");
        ensure!(
            self.context.len() <= max_history_utterances,
            "too many history utterances"
        );
        let history = self
            .context
            .iter()
            .map(|item| {
                let name = match item.role {
                    Role::User => "ユーザー",
                    Role::Assistant => "ドギド",
                };
                format!("{name}: {}", item.text)
            })
            .collect::<Vec<_>>()
            .join("\n");
        Ok(format!(
            "履歴:\n{}\n現在のユーザー発話:\n{}",
            if history.is_empty() {
                "（なし）"
            } else {
                &history
            },
            self.text
        ))
    }
}

#[derive(Debug, Deserialize)]
struct Manifest {
    schema_version: u32,
    path_base: String,
    evaluated_input: Limits,
}

/// 上限は採用記録の評価条件に従う。今回のモデルは現在発話のみ・128トークン。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Limits {
    pub max_history_utterances: usize,
    pub max_length: usize,
    pub truncation: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Prediction {
    pub label: Label,
    pub probabilities: [f64; LABEL_COUNT],
    pub token_count: usize,
    pub inference_ms: f64,
}

/// 原文と入力元に結びついた一回の結果。内部保留・再送も同じ結果を引き継ぐ。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Classification {
    pub text: String,
    pub source: String,
    pub manifest_sha256: String,
    pub prediction: Prediction,
}
impl Classification {
    pub fn matches(&self, text: &str, source: &str) -> bool {
        self.text == text && self.source == source
    }
}

/// 単一モデルを直列に呼ぶ所有ハンドル。失敗後は閉じ、呼出元が再作成を決める。
pub struct Classifier {
    helper: Option<Helper>,
    manifest_sha256: String,
    limits: Limits,
}

impl Classifier {
    /// モデル保管先のルートと採用記録を明示して読み込む。一般起動設定は変更しない。
    pub async fn start(
        python: &Path,
        script: &Path,
        manifest: &Path,
        model_root: &Path,
        load_timeout: Duration,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Self> {
        let manifest = tokio::fs::canonicalize(manifest).await?;
        let model_root = tokio::fs::canonicalize(model_root).await?;
        let bytes = tokio::fs::read(&manifest).await?;
        let spec: Manifest = serde_json::from_slice(&bytes)?;
        ensure!(
            spec.schema_version == 1 && spec.path_base == "repository_root",
            "unsupported model manifest"
        );
        ensure!(
            spec.evaluated_input.max_length > 0
                && spec.evaluated_input.max_length <= 8192
                && spec.evaluated_input.max_history_utterances <= 10
                && !spec.evaluated_input.truncation,
            "unsupported input limits"
        );
        ensure!(!*cancel.borrow(), "request classifier cancelled");
        let mut client = Self {
            helper: Some(Helper::start(python, script)?),
            manifest_sha256: format!("{:x}", Sha256::digest(&bytes)),
            limits: spec.evaluated_input,
        };
        let result = async {
            let reply = client
                .exchange(
                    json!({"op":"load", "manifest_path":manifest, "model_root":model_root}),
                    load_timeout,
                    cancel,
                )
                .await?;
            ensure!(reply["ready"] == true, "classifier did not become ready");
            let labels: [Label; LABEL_COUNT] = serde_json::from_value(reply["labels"].clone())?;
            let limits: Limits = serde_json::from_value(reply["limits"].clone())?;
            ensure!(
                labels == LABELS && limits == client.limits,
                "classifier input or label contract mismatch"
            );
            Ok::<_, anyhow::Error>(())
        }
        .await;
        if let Err(error) = result {
            client.abort().await;
            return Err(error);
        }
        Ok(client)
    }

    /// 一件を整形して推論し、要求ID・モデル・確率・token上限を照合して返す。
    /// 取消や不正応答では子プロセスを回収し、遅れて届く旧応答を再利用しない。
    pub async fn classify(
        &mut self,
        input: &Input,
        timeout: Duration,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Prediction> {
        let text = input.render(self.limits.max_history_utterances)?;
        let result = async {
            let reply = self
                .exchange(json!({"op":"classify", "text":text}), timeout, cancel)
                .await?;
            let prediction: Prediction = serde_json::from_value(reply["prediction"].clone())
                .context("invalid classifier prediction")?;
            prediction.validate(self.limits.max_length)?;
            Ok(prediction)
        }
        .await;
        if result.is_err() {
            self.abort().await;
        }
        result
    }

    async fn exchange(
        &mut self,
        mut frame: Value,
        timeout: Duration,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Value> {
        let request_id = uuid::Uuid::new_v4().to_string();
        frame["schema_version"] = json!(1);
        frame["request_id"] = json!(request_id);
        frame["manifest_sha256"] = json!(self.manifest_sha256);
        let helper = self
            .helper
            .as_mut()
            .context("request classifier is closed")?;
        let result = tokio::select! {
            biased;
            () = cancelled(cancel) => Err(anyhow::anyhow!("request classifier cancelled")),
            result = helper.exchange_with_timeout(frame, timeout) => result,
        };
        let reply = result?;
        ensure!(
            reply["schema_version"] == 1
                && reply["request_id"] == request_id
                && reply["manifest_sha256"] == self.manifest_sha256,
            "classifier response identity mismatch"
        );
        Ok(reply)
    }

    async fn abort(&mut self) {
        if let Some(helper) = self.helper.take() {
            let _ = helper.finish(true).await;
        }
    }

    pub fn manifest_sha256(&self) -> &str {
        &self.manifest_sha256
    }

    /// EOFを送り正常終了を待つ。失敗後に呼んでも二重回収しない。
    pub async fn close(mut self) -> Result<()> {
        if let Some(helper) = self.helper.take() {
            helper.finish(false).await?;
        }
        Ok(())
    }
}

impl Prediction {
    fn validate(&self, max_length: usize) -> Result<()> {
        ensure!(
            self.token_count > 0 && self.token_count <= max_length,
            "invalid classifier token count"
        );
        ensure!(
            self.inference_ms.is_finite() && self.inference_ms >= 0.0,
            "invalid classifier timing"
        );
        ensure!(
            self.probabilities
                .iter()
                .all(|p| p.is_finite() && (0.0..=1.0).contains(p))
                && (self.probabilities.iter().sum::<f64>() - 1.0).abs() < 1e-5,
            "invalid classifier probabilities"
        );
        let mut best = 0;
        for i in 1..LABEL_COUNT {
            if self.probabilities[i] > self.probabilities[best] {
                best = i;
            }
        }
        ensure!(
            self.label == LABELS[best],
            "classifier label does not match probabilities"
        );
        Ok(())
    }
}

async fn cancelled(cancel: &mut watch::Receiver<bool>) {
    loop {
        if *cancel.borrow() || cancel.changed().await.is_err() {
            return;
        }
    }
}

#[cfg(test)]
#[path = "request_classifier/tests.rs"]
mod tests;
