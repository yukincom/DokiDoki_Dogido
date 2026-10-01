//! 戦闘判断の入出力。I/O・発声・履歴・世界操作は実行層が所有する。
use crate::events::GameEvent;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    #[default]
    Normal,
    Alert,
    Panic,
    SuppressedPanic,
    Aftermath,
}

#[derive(Clone, Debug, Default, Serialize)]
#[serde(tag = "kind", content = "targets", rename_all = "snake_case")]
pub enum Scope {
    /// 起きた出来事への反応。後続の空visualで過去の事実を消さない。
    #[default]
    Event,
    /// 現在視認中の個体を主語にした発話。
    Visual(Vec<String>),
    /// 現在の敵音を主語にした発話。visual更新と独立して扱う。
    Auditory(Vec<String>),
    /// 安全確認後の安堵。新しい脅威で取り消す。
    Safe,
    /// 同じ一句・版について、安全な間だけ再開確認を配送する。
    Workshop {
        id: String,
        version: u64,
    },
    WorkshopReply {
        id: String,
        version: u64,
    },
}

#[derive(Clone, Debug, Serialize)]
pub struct LeafRequest {
    pub kind: String,
    pub details: Value,
    pub temperature: f64,
}

/// 音声の実行層だけが付ける優先区分。判断の比較データには混ぜない。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Delivery {
    #[default]
    Combat,
    Ambient,
    UrgentEnvironment,
    PlayerReply,
}

#[derive(Clone, Debug, Serialize)]
pub struct Speech {
    #[serde(skip)]
    pub delivery: Delivery,
    pub kind: &'static str,
    pub text: String,
    pub cue_id: Option<&'static str>,
    pub cue_sequence: Vec<String>,
    pub interrupt: bool,
    pub protect_ms: u64,
    pub scope: Scope,
    /// 既存単体・群れ警告の配送中再照合をそのまま使う。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub visual_plan: Option<crate::threats::Warning>,
    /// コードが選んだ出来事の言い回しだけを生成する。textが失敗時の正本fallback。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub leaf: Option<LeafRequest>,
}
impl Speech {
    pub fn new(kind: &'static str, text: impl Into<String>) -> Self {
        Self {
            delivery: Delivery::Combat,
            kind,
            text: text.into(),
            cue_id: None,
            cue_sequence: vec![],
            interrupt: false,
            protect_ms: 0,
            scope: Scope::Event,
            visual_plan: None,
            leaf: None,
        }
    }
    pub fn visual(kind: &'static str, text: impl Into<String>, ids: Vec<String>) -> Self {
        Self {
            scope: Scope::Visual(ids),
            ..Self::new(kind, text)
        }
    }
}

/// Python既定値から抽出した閉じた設定。起動時にキー・型を検査する。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(transparent)]
pub struct Settings(pub Map<String, Value>);
impl Default for Settings {
    fn default() -> Self {
        let mut values: Map<String, Value> =
            serde_json::from_str(include_str!("defaults.json")).expect("checked combat defaults");
        for file in [
            include_str!("../environment/danger_defaults.json"),
            include_str!("../environment/ambient_defaults.json"),
        ] {
            values.extend(
                serde_json::from_str::<Map<String, Value>>(file)
                    .expect("checked environment defaults"),
            );
        }
        Self(values)
    }
}
impl Settings {
    pub fn merged(overrides: &Map<String, Value>) -> anyhow::Result<Self> {
        let mut result = Self::default();
        for (key, value) in overrides {
            let expected = result
                .0
                .get(key)
                .ok_or_else(|| anyhow::anyhow!("unknown combat setting: {key}"))?;
            anyhow::ensure!(
                expected.is_string() && value.is_string()
                    || expected.is_boolean() && value.is_boolean()
                    || expected.is_number()
                        && value.as_f64().is_some_and(|n| n.is_finite() && n >= 0.0),
                "invalid combat setting: {key}"
            );
            result.0.insert(key.clone(), value.clone());
        }
        Ok(result)
    }
    pub fn number(&self, key: &str) -> f64 {
        self.0[key]
            .as_f64()
            .expect("validated numeric combat setting")
    }
    pub fn ms(&self, key: &str) -> u64 {
        self.number(key) as u64
    }
    pub fn text(&self, key: &str) -> &str {
        self.0[key].as_str().expect("validated text combat setting")
    }
}

pub fn elapsed(now: u64, at: Option<u64>, window: u64) -> bool {
    at.is_none_or(|at| now.saturating_sub(at) >= window)
}
pub fn label(kind: &str) -> String {
    let catalog: &Value = crate::combat::catalog::labels();
    catalog
        .get(kind.strip_prefix("minecraft:").unwrap_or(kind))
        .and_then(Value::as_str)
        .unwrap_or(kind)
        .to_owned()
}
pub fn visual_ids(e: &GameEvent) -> Vec<String> {
    e.visual_threats
        .iter()
        .map(crate::threats::identity)
        .collect()
}
