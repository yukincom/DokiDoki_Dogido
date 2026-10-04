//! 接続ごとの観測・会話・川柳・限定操作をまとめ、判断結果を表示と音声へ配送する本体。
//! observeがゲーム観測を更新し、submitが入力を受理してturnと読み取り用snapshotを作る。
//! 通常turnはserial待ち → snapshot更新 → routing/生成 → 結果の再照合 → 保存/配送へ進む。
//! assistant履歴は実再生完了後に確定し、テキスト相談室では表示確認を使う。生成だけでは確定しない。
//!
//! Sessionと表示台帳はdataのmutexで保護し、各runtimeが短い同期区間で照合・更新する。
//! job登録で両方を取る順序はjobs → data。通常turnのserial待ちはdataを解放して行う。
//! epochは返答処理の世代、input_generationは入力とその内部再送の世代を識別する。
//! 取消通知で処理を止めるとともに、完了時にも世代を再照合して古い結果の混入を防ぐ。
mod address_runtime;
mod web_adapter;
mod web_runtime;
pub use web_runtime::Settings as WebSettings;
mod assist_runtime;
mod audio;
mod bridge;
mod chat_context;
mod chat_leaf_runtime;
mod chat_runtime;
mod combat_runtime;
mod environment_runtime;
mod episode_runtime;
mod foreground_runtime;
mod haiku_runtime;
mod history;
mod knowledge_display;
mod knowledge_queue;
mod language_runtime;
mod memory_runtime;
mod monitor;
mod poem_runtime;
mod reaction_runtime;
mod reading_runtime;
mod tts_runtime;
mod update;
pub use haiku_runtime::Settings as HaikuSettings;
mod combat_classifier;
mod sentences;
mod voice_input_runtime;
mod warnings;
mod workshop_combat_input;
mod workshop_combat_runtime;
mod workshop_edits;
mod workshop_focus;
mod workshop_record;
mod workshop_runtime;
mod workshop_text;

use crate::{
    events::{EventName, GameEvent, SourceKind},
    ingress::{Admission, SequenceLedger},
    llm::RigLlm,
    playback::Status as PlaybackStatus,
};
use anyhow::Result;
use serde_json::{Value, json};
use std::{
    collections::{HashMap, VecDeque},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::sync::{Semaphore, watch};

/// 会話・発句・音声の接続先と予算。newで検査し、各workerへ読み取り用に共有する。
#[derive(Clone)]
pub struct DialogueConfig {
    pub python: PathBuf,
    /// UniDic token補助のパス。同じディレクトリのOS AI・Web等のSDKアダプターもここを基準に探す。
    pub helper: PathBuf,
    pub model: String,
    pub base_url: String,
    pub max_tokens: u64,
    pub timeout_ms: u64,
    pub reading_engine: String,
    pub voicevox_url: String,
    pub speaker: u32,
    pub speed: f64,
    pub haiku_speed: f64,
    pub pitch: f64,
    pub volume: f64,
    pub output_sampling_rate: Option<u32>,
    pub audio_dir: PathBuf,
    pub player: PathBuf,
    pub audio_enabled: bool,
    pub llm_enabled: bool,
    pub language_enabled: bool,
    pub web: WebSettings,
    pub warnings: crate::threats::Settings,
    pub combat: crate::combat::model::Settings,
    pub haiku: HaikuSettings,
}
impl Default for DialogueConfig {
    fn default() -> Self {
        Self {
            python: "python3".into(),
            helper: PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("scripts/haiku_tokens.py"),
            model: "default_model".into(),
            base_url: "http://127.0.0.1:8080/v1".into(),
            max_tokens: 72,
            timeout_ms: 20_000,
            reading_engine: "auto".into(),
            voicevox_url: "http://127.0.0.1:50021".into(),
            speaker: 21,
            speed: 0.88,
            haiku_speed: 0.80,
            pitch: 0.0,
            volume: 1.0,
            output_sampling_rate: None,
            audio_dir: PathBuf::from(".dogido_tmp/rust-dialogue"),
            player: "/usr/bin/afplay".into(),
            audio_enabled: true,
            llm_enabled: true,
            language_enabled: true,
            web: WebSettings::default(),
            warnings: crate::threats::Settings::default(),
            combat: crate::combat::model::Settings::default(),
            haiku: HaikuSettings::default(),
        }
    }
}
/// 一接続の可変状態。全フィールドはDialogue.dataのmutexで保護する。
/// 観測入口と各runtimeが同期区間で更新し、生成workerには必要な値のsnapshotを渡す。
/// 非同期結果はepochや対象IDを再照合してから反映する。型内に独立したロックは持たない。
struct Session {
    /// 接続時のプレイヤー表示名。registerで設定する。
    name: String,
    /// ゲーム観測なしの対話試験か。trueなら通常の観測鮮度条件を免除する。
    preview: bool,
    /// workshop_textが設定する、音声を伴わない共同編集試験の入口。
    text_workshop: bool,
    /// テキスト試験で選択した保存句。workshop_textが読み込み・参照する。
    text_poem: Option<crate::poem_book::SavedPoem>,
    /// テキスト試験への返却結果とturn ID・epoch。要求と対応づけて一度だけ取り出す。
    text_reply: Option<(String, u64, Value)>,
    /// テキスト試験に表示する直近要求。workshop_textが生成段階を記録する。
    text_last_request: Option<Value>,
    /// observeが全視認を含むイベントだけから更新する、通常会話と戦況の観測正本。
    latest: Option<GameEvent>,
    /// latestの受信時刻。受信時点で古い／時差不明ならNone。以後の鮮度はobservation_freshで測る。
    received: Option<Instant>,
    /// 完全観測または新しい聴覚通知。warnings等が最新の音による中断根拠を読む。
    audio_latest: Option<GameEvent>,
    /// audio_latestの受信時刻。聴覚通知の鮮度を全体観測とは別に測る。
    audio_received: Option<Instant>,
    /// combat_runtimeが戦況から更新する会話可否。入口では観測鮮度も合わせて調べる。
    chat_allowed: bool,
    /// combat_runtimeが反映する現在のnormal/alert/panic等の状態。
    mode: crate::combat::model::Mode,
    /// observeが更新する受信済みsequence・冪等キー。再送で同じ判断を繰り返すことを防ぐ。
    sequences: SequenceLedger,
    /// turnに結びついた短期履歴。assistant側は実再生完了、テキスト相談室では表示確認で確定する。
    history: history::History,
    /// 観測された名前と最近の対象の記録。chat_contextが会話用snapshotへ投影する。
    chat_observation: crate::chat_observation::ChatObservationMemory,
    /// 発句開始に添える現在構造物・プレイヤー名・所持品順。chat_contextが観測入口で更新する。
    haiku_context: crate::haiku::preparation::RuntimeSnapshot,
    /// voice_input_runtimeが叫声と後続状況を対応づける一時保留。
    pending_vocalization: Option<voice_input_runtime::Pending>,
    /// haiku_runtimeが持つ生成状態・現在句・workshopの正本。発句時計はforegroundが所有する。
    haiku: haiku_runtime::State,
    /// 戦闘の出来事とWeb復帰時の話題を保持する短い文脈。combat_runtime／web_runtimeが更新する。
    combat_digest: VecDeque<String>,
    /// 敵の同一性・接近・被弾を追跡し、workshop暫定再開の安全条件に使う。
    stable_threat: crate::workshop_combat_input::StableThreat,
    /// 戦闘中入力の限定分類を待つ要求。workshop_combat_inputが世代付きで管理する。
    combat_input: Option<workshop_combat_input::Pending>,
    /// 通常turnの結果を反映できる世代。新turn・取消後の古い生成や再生通知を識別する。
    epoch: u64,
    /// 現在の通常turn ID。表示行・生成結果・再生通知を同じ入力へ結ぶ。
    current_turn: String,
    /// updateが反映する現在turnの生成・配送・再生状態。
    status: PlaybackStatus,
    /// 通常turnへの取消通知口。待機・生成・再生が同じ通知を監視する。
    cancel: Option<watch::Sender<bool>>,
    /// 観測・被弾・警告間隔から戦況と優先発話を決める同期Engine。
    combat: crate::combat::core::Engine,
    /// 許可能力・発行中command・実行結果と重複防止情報。現在slotはイベント観測と照合する。
    assist: crate::assist::AssistState,
    /// assist_runtimeが限定意図抽出の完了を待つ要求。
    assist_pending: Option<assist_runtime::Pending>,
    /// environment_runtimeが扱う暗さ・夕方・天候などの警告状態。
    danger: crate::environment::danger::Danger,
    /// environment_runtimeが扱う周辺反応・匂い・照明増加の状態と間隔。
    ambient: crate::environment::ambient::Ambient,
    /// 部分通知へ必要な既存文脈を補った環境判断用観測。
    environment_latest: Option<GameEvent>,
    /// 会話へ渡す場所・天候等の投影と版。観測入口が更新する。
    conversation_observation: crate::conversation_observation::State,
    /// 最後に扱ったplayer入力のDialogue時計上の経過ミリ秒。発話の優先制御に使う。
    last_player_input: Option<u64>,
    /// casual/learning/web/workshop等、現在どの会話を優先するかの状態。
    foreground: crate::foreground::State,
    /// 限定国語対話の対象・確認段階。language_runtimeが検証済み結果を反映する。
    language: crate::language::State,
    /// Web調査の同意・実再生待ち・読書期限・専用アダプターの状態。
    web: web_runtime::State,
    /// 高優先発話の後へ回す知識質問。knowledge_queueが先着順・安全復帰時の再送・取消を管理する。
    knowledge_queue: VecDeque<knowledge_queue::Pending>,
    /// 先に分類済みの知識質問。内部再送を同じ入力世代へ結ぶ。
    knowledge_checked: Option<knowledge_queue::Checked>,
    /// 学習から通常会話へ戻す際の宛先確認待ち一件。address_runtimeが所有する。
    address: Option<crate::address::Pending>,
    /// 宛先待ちの自由文を先に分類した結果。元turnと入力世代が一致するときだけ使う。
    address_checked: Option<address_runtime::Checked>,
    /// 会話の実再生完了またはテキスト表示確認時の経過ミリ秒。話題の新しさの判定に使う。
    last_completed_conversation: Option<u64>,
    /// 新規入力や割込みで進める世代。内部再送が後の入力を追い越すことを防ぐ。
    input_generation: u64,
    /// 受理時の世代と記録制限。内部再送にも同じ制限を引き継ぐ。
    record_private_generation: Option<(u64, bool)>,
    /// 進行中の照明コメント判定への取消通知口。発話の寿命は警告配送側が扱う。
    light_cancel: Option<watch::Sender<bool>>,
    /// 雷・夕方などの優先発話後へ回す入力。environment_runtimeが再送する。
    deferred_input: Option<environment_runtime::DeferredInput>,
    /// 再生中の優先警告と取消情報。warningsが音声の寿命を管理する。
    warning: Option<warnings::Active>,
    /// 優先警告をまだ開始できない間の配送候補。次の観測で再評価する。
    pending_warning: Option<Vec<crate::combat::model::Speech>>,
    /// 保留した警告・戦況質問に対応する元入力。候補と一緒に消費する。
    pending_input: Option<String>,
}
/// data mutexでまとめて保護する接続群と表示台帳。revisionは表示更新の検知に使う。
#[derive(Default)]
struct Data {
    sessions: HashMap<String, Session>,
    rows: VecDeque<Value>,
    revision: u64,
    stopped: bool,
    text_prompts: Option<Value>,
    text_prompt_settings: Option<Value>,
    text_prompt_version: u64,
}
/// 共有設定・各I/O実装・接続状態・job寿命を所有する本体ハンドル。
pub struct Dialogue {
    config: DialogueConfig,
    workshop_records: workshop_record::Recorder,
    llm: RigLlm,
    audio: audio::Audio,
    haiku_routes: haiku_runtime::Routes,
    combat_classifier: combat_classifier::Classifier,
    episodes: Option<Arc<crate::episode_log::Recorder>>,
    /// 状態の短い照合・更新区間だけで保持し、ネットワークや音声の完了待ちへ持ち越さない。
    data: Mutex<Data>,
    /// 全接続に共通の通常turn実行枠一つ。生成から保存・音声配送までの競合を抑える。
    serial: Semaphore,
    /// shutdownが完了待ちする登録済みjob。dataと同時取得する入口はjobsを先に取る。
    jobs: Mutex<Vec<tokio::task::JoinHandle<()>>>,
    /// 入力期限・発句時計・クールダウンに共通の、起動からの単調時計。
    clock: Instant,
}
fn id(prefix: &str) -> String {
    format!("{prefix}_{}", uuid::Uuid::new_v4().simple())
}
/// Fabricが現在の視認一式を添えるイベントを区別する。
/// status・接近・死亡/爆散・戦闘終了なら視認正本を更新し、音・ambient通知なら既存を保持する。
/// 後者のvisual_threats=[]は観測省略なので、敵の消失や観測鮮度の延長に使わない。
fn complete_observation(event: &GameEvent) -> bool {
    match event.event.name {
        EventName::StatusSnapshot
        | EventName::ThreatApproaching
        | EventName::PlayerDied
        | EventName::HostileDefeated
        | EventName::CreeperDetonated
        | EventName::CombatEnded => true,
        EventName::HostileAudioDetected | EventName::AmbientMobDetected => false,
    }
}
fn recent_observation(event: &GameEvent) -> bool {
    match &event.observed_at {
        crate::events::EventTime::Aware(at) => {
            (chrono::Utc::now() - at.with_timezone(&chrono::Utc))
                .num_milliseconds()
                .abs()
                <= 10_000
        }
        crate::events::EventTime::Naive(_) => false,
    }
}
fn observation_fresh(session: &Session) -> bool {
    session.preview
        || session
            .received
            .is_some_and(|at| at.elapsed() <= Duration::from_secs(10))
            && session.latest.as_ref().is_some_and(recent_observation)
}
fn fresh(session: &Session) -> bool {
    observation_fresh(session) && session.chat_allowed
}
fn empty_event(name: &str) -> Value {
    json!({"schema_version":"2026-05-24","adapter":"rust-conversation-preview","observed_at":chrono::Utc::now(),
    "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"player":{"name":name}})
}

impl Dialogue {
    pub fn llm_enabled(&self) -> bool {
        self.config.llm_enabled
    }

    /// 設定を検査して各I/Oの所有者を構築する。会話処理はregister後のobserve/submitから始まる。
    pub fn new(mut config: DialogueConfig) -> Result<Arc<Self>> {
        config.warnings.validate()?;
        config.combat = crate::combat::model::Settings::merged(&config.combat.0)?;
        anyhow::ensure!(config.helper.is_file(), "dictionary helper missing");
        anyhow::ensure!(
            config.max_tokens > 0 && config.max_tokens <= 512,
            "invalid reply token budget"
        );
        anyhow::ensure!(
            config.speed.is_finite()
                && config.speed > 0.0
                && config.haiku_speed.is_finite()
                && config.haiku_speed > 0.0
                && config.volume.is_finite()
                && config.volume >= 0.0
                && config.pitch.is_finite(),
            "invalid voice settings"
        );
        let key = std::env::var("DOGIDO_LLM_API_KEY").ok();
        Ok(Arc::new(Self {
            llm: RigLlm::new(
                &config.base_url,
                key.as_deref(),
                Duration::from_millis(config.timeout_ms),
            )?
            .with_enabled(config.llm_enabled),
            audio: audio::Audio::new()?,
            haiku_routes: haiku_runtime::Routes::new(&config)?,
            combat_classifier: combat_classifier::Classifier::default(),
            episodes: if config.haiku.memory_enabled {
                match crate::episode_log::Recorder::new(config.haiku.memory_dir.clone()) {
                    Ok(recorder) => Some(Arc::new(recorder)),
                    Err(error) => {
                        tracing::warn!(event="episode_writer_start_failed", %error);
                        None
                    }
                }
            } else {
                None
            },
            workshop_records: workshop_record::Recorder::new(config.haiku.memory_enabled),
            config,
            data: Mutex::new(Data::default()),
            serial: Semaphore::new(1),
            jobs: Mutex::new(vec![]),
            clock: Instant::now(),
        }))
    }
    /// 接続IDへ空の履歴・観測・判断器を登録し、workshop記録の差分比較状態を初期化する。
    pub fn register(&self, session_id: &str, name: &str, preview: bool) {
        let mut d = self.data.lock().unwrap();
        d.sessions.insert(
            session_id.into(),
            Session {
                name: name.into(),
                preview,
                text_workshop: false,
                text_poem: None,
                text_reply: None,
                text_last_request: None,
                latest: None,
                received: None,
                audio_latest: None,
                audio_received: None,
                chat_allowed: true,
                mode: crate::combat::model::Mode::Normal,
                sequences: SequenceLedger::default(),
                history: history::History::default(),
                chat_observation: chat_context::memory(&self.config.combat),
                haiku_context: crate::haiku::preparation::RuntimeSnapshot::default(),
                pending_vocalization: None,
                haiku: haiku_runtime::State::default(),
                combat_digest: VecDeque::new(),
                stable_threat: crate::workshop_combat_input::StableThreat::default(),
                combat_input: None,
                epoch: 0,
                current_turn: String::new(),
                status: PlaybackStatus::Ready,
                cancel: None,
                combat: crate::combat::core::Engine::default(),
                assist: crate::assist::AssistState::new([]),
                assist_pending: None,
                danger: crate::environment::danger::Danger::default(),
                ambient: crate::environment::ambient::Ambient::default(),
                environment_latest: None,
                conversation_observation: crate::conversation_observation::State::default(),
                last_player_input: None,
                foreground: crate::foreground::State::default(),
                language: crate::language::State::default(),
                web: web_runtime::State::default(),
                knowledge_queue: VecDeque::new(),
                knowledge_checked: None,
                address: None,
                address_checked: None,
                last_completed_conversation: None,
                input_generation: 0,
                record_private_generation: None,
                light_cancel: None,
                deferred_input: None,
                warning: None,
                pending_warning: None,
                pending_input: None,
            },
        );
        d.revision += 1;
        self.workshop_records.register(session_id);
    }
    /// 接続の各workerへ取消を通知して状態を外し、開いていたworkshopの終了を記録する。
    pub fn close(&self, session_id: &str) {
        let before = self.workshop_record_state(session_id);
        let mut d = self.data.lock().unwrap();
        self.cancel_knowledge_queue(&mut d, session_id, "session_closed");
        Self::cancel_haiku(&mut d, session_id, "session_closed");
        Self::cancel_chat(&mut d, session_id, "session_closed");
        Self::cancel_combat_input(&mut d, session_id);
        Self::cancel_warning(&mut d, session_id, "session_closed");
        Self::cancel_assist(&mut d, session_id);
        Self::cancel_light(&mut d, session_id);
        d.sessions.remove(session_id);
        d.revision += 1;
        drop(d);
        self.record_workshop(
            session_id,
            "lifecycle",
            &before,
            &Value::Null,
            &workshop_record::Input::default(),
            &json!({"reason":"session_closed"}),
        );
        self.workshop_records.forget(session_id);
    }
    pub fn only_session(&self) -> Option<String> {
        let d = self.data.lock().unwrap();
        if d.sessions.len() == 1 {
            d.sessions.keys().next().cloned()
        } else {
            None
        }
    }
    /// 一件のイベントを受理し、戦況・環境・発句と必要なplayer入力を処理する。
    /// 戻り値は受理・判断・配送予定の結果。音声の実再生結果は各workerから後で届く。
    pub fn observe(
        self: &Arc<Self>,
        session_id: &str,
        event: GameEvent,
        key: Option<&str>,
    ) -> Value {
        let before = self.workshop_record_state(session_id);
        let text = event.meta.user_text.clone().unwrap_or_default();
        let private = self
            .data
            .lock()
            .unwrap()
            .sessions
            .get(session_id)
            .is_some_and(|s| s.web.state.research.is_some());
        let mut result = self.observe_inner(session_id, event, key, private);
        let direct_input = result
            .as_object_mut()
            .and_then(|r| r.remove("_workshop_direct_input"))
            == Some(Value::Bool(true));
        let after = self.workshop_record_state(session_id);
        if result["deduplicated"] != true {
            if direct_input && !text.is_empty() {
                self.record_workshop(
                    session_id,
                    "input_admission",
                    &before,
                    &after,
                    &workshop_record::Input {
                        raw: text.clone(),
                        private,
                        ..Default::default()
                    },
                    &result["player_input"],
                );
            } else if before != after {
                self.record_workshop(
                    session_id,
                    "lifecycle",
                    &before,
                    &after,
                    &workshop_record::Input::default(),
                    &json!({"reason":"game_event"}),
                );
            }
        }
        result
    }
    fn observe_inner(
        self: &Arc<Self>,
        session_id: &str,
        event: GameEvent,
        key: Option<&str>,
        private: bool,
    ) -> Value {
        let sequence = event.sequence;
        let recent = recent_observation(&event);
        let text = event.meta.user_text.clone().unwrap_or_default();
        // submitと同じjobs → dataの順で、停止判定からjob登録までを保護する。
        // shutdownはdataで停止を確定して解放した後、jobsを回収するため、
        // 受理されたjobは回収対象に入り、停止後の新規受付はここで断られる。
        let mut jobs = self.jobs.lock().unwrap();
        jobs.retain(|j| !j.is_finished());
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return json!({"accepted":false,"reason":"server_stopping"});
        }
        let Some(s) = d.sessions.get_mut(session_id) else {
            return json!({"accepted":false,"reason":"unknown_session_id"});
        };
        let duplicate = s.sequences.admit(sequence, key) != Admission::New;
        let results = s.assist.observe_results(
            &event.command_results,
            chrono::Utc::now(),
            s.warning.is_some() || s.pending_warning.is_some() || s.cancel.is_some(),
        );
        for observed in &results.observed {
            tracing::info!(event="assist_result",session_id=session_id,result=%json!(observed));
        }
        let episode_before = (!duplicate && self.episodes.is_some())
            .then(|| episode_runtime::Before::capture(&d, session_id));
        let mut input_handled = false;
        let mut input_generation = None;
        if !duplicate {
            let now = self.clock.elapsed().as_millis() as u64;
            let complete = complete_observation(&event);
            let s = d.sessions.get_mut(session_id).unwrap();
            if complete {
                s.latest = Some(event.clone());
                s.received = recent.then(Instant::now);
            }
            if complete && recent {
                s.foreground
                    .set_game_paused(now, event.world.game_paused.unwrap_or(false));
            }
            if complete || recent && event.event.source_kind == SourceKind::Auditory {
                s.audio_latest = Some(event.clone());
                s.audio_received = recent.then(Instant::now);
            }
            if complete {
                if recent {
                    s.stable_threat.observe(
                        &event,
                        now,
                        self.config.warnings.recent_damage_window_ms,
                    );
                } else {
                    s.stable_threat.reset();
                }
            } else if recent && warnings::interruption_reason(&event).is_some() {
                s.stable_threat.reset();
            }
            if recent {
                s.environment_latest = Some(environment_runtime::context(s, &event, complete));
                let (boss, ominous) = s.combat.environmental_presence(now, &self.config.combat);
                s.danger.set_presence(boss, ominous);
                s.danger.update(&event, now, complete, &self.config.combat);
                s.ambient.update(&event, now, complete, &self.config.combat);
                if let Some(current) = &s.environment_latest {
                    match crate::conversation_observation::project(
                        current,
                        s.ambient.current_structure(),
                        self.config.combat.number("home_bed_prompt_distance"),
                        self.config.combat.number("darkness_advice_light_threshold"),
                        self.config.combat.ms("weather_sound_recent_ms"),
                    ) {
                        Ok(context) => s.conversation_observation.observe(context),
                        Err(error) => {
                            tracing::warn!(event="conversation_observation_rejected",session_id,%error)
                        }
                    }
                }
                if complete {
                    chat_context::update_haiku(s, &event, &self.config.combat);
                }
                s.combat.set_dark_push_context(
                    s.danger.dark_push_active(),
                    s.warning
                        .as_ref()
                        .is_some_and(|w| w.actions.iter().any(environment_runtime::dark_audio))
                        || s.pending_warning
                            .as_ref()
                            .is_some_and(|a| a.iter().any(environment_runtime::dark_audio)),
                );
            }
            if recent
                && warnings::interruption_reason(&event).is_some()
                && !workshop_focus::owns_input(&d.sessions[session_id])
            {
                Self::cancel_haiku(&mut d, session_id, "current_threat");
                Self::cancel_address(&mut d, session_id, "attention_interrupted");
            }
            if recent && !text.trim().is_empty() {
                Self::cancel_combat_input(&mut d, session_id);
                Self::cancel_haiku(&mut d, session_id, "new_player_input");
                Self::cancel_assist(&mut d, session_id);
                Self::cancel_light(&mut d, session_id);
                let s = d.sessions.get_mut(session_id).unwrap();
                s.input_generation = s.input_generation.wrapping_add(1);
                input_generation = Some(s.input_generation);
                s.record_private_generation = Some((s.input_generation, private));
                s.last_player_input = Some(now);
                s.ambient.note_player_input(now);
            }
            self.tick_workshop(&mut d, session_id);
            self.tick_foreground(&mut d, session_id);
            if recent {
                // Clear a superseded deep-dark reaction before busy is computed below.
                self.preempt_ominous_reaction(&mut d, session_id, &event);
            }
            self.refresh_combat_audio(&mut d, session_id);
            if recent {
                let s = d.sessions.get_mut(session_id).unwrap();
                let busy = s.warning.is_some() || s.pending_warning.is_some() || jobs.len() >= 16;
                let workshop_event = (!text.trim().is_empty() && workshop_focus::quiet(s))
                    .then(|| event.without_player_input());
                let decision = s.combat.observe(
                    workshop_event.as_ref().unwrap_or(&event),
                    now,
                    complete,
                    busy,
                    &self.config.combat,
                    &self.config.warnings,
                );
                let names = s.combat.take_name_updates();
                if let Err(error) =
                    s.chat_observation
                        .observe(&event, &names, &chat_context::CatalogLabels)
                {
                    tracing::warn!(event="chat_observation_rejected", session_id, %error);
                }
                let conversation_threat = warnings::interruption_reason(&event).is_some()
                    && !workshop_focus::owns_input(s);
                if event.event.name == EventName::PlayerDied
                    || decision.dimension_changed
                    || (event.event.name == EventName::CombatEnded && !conversation_threat)
                {
                    s.foreground.finish_combat();
                    s.history.end_danger(
                        self.config
                            .combat
                            .ms("conversation_post_danger_player_turns"),
                    );
                }
                // 暗所だけでもmodeはalertになる。敵等の根拠なしに
                // combat_ended待ちへ入ると、明るくなっても発句が止まる。
                if event.event.name != EventName::PlayerDied && conversation_threat {
                    s.history.begin_danger();
                    s.foreground.start_combat(
                        now,
                        self.config.combat.ms("conversation_suspended_player_turns"),
                    );
                }
                input_handled = decision.input_handled;
                let combat_priority = if decision
                    .actions
                    .iter()
                    .any(|a| a.kind == "dark_push_forward")
                {
                    environment_runtime::CombatPriority::FrontAmbush
                } else if !decision.actions.is_empty() || !decision.chat_allowed {
                    environment_runtime::CombatPriority::Selected
                } else {
                    environment_runtime::CombatPriority::None
                };
                if decision.dimension_changed {
                    Self::cancel_combat_input(&mut d, session_id);
                    d.sessions
                        .get_mut(session_id)
                        .unwrap()
                        .stable_threat
                        .reset();
                    Self::cancel_assist(&mut d, session_id);
                    let s = d.sessions.get_mut(session_id).unwrap();
                    s.input_generation = s.input_generation.wrapping_add(1);
                    s.deferred_input = None;
                }
                if event.event.name == EventName::PlayerDied || decision.dimension_changed {
                    self.cancel_knowledge_queue(&mut d, session_id, "world_context_changed");
                    Self::cancel_address(&mut d, session_id, "attention_interrupted");
                }
                self.apply_combat_decision(
                    &mut d,
                    &mut jobs,
                    session_id,
                    decision,
                    input_handled.then_some(text.as_str()),
                    warnings::interruption_reason(&event),
                );
                self.process_environment(
                    &mut d,
                    &mut jobs,
                    session_id,
                    &event,
                    now,
                    combat_priority,
                );
                let s = d.sessions.get_mut(session_id).unwrap();
                s.danger.finish_frame(s.mode);
            } else {
                let s = d.sessions.get_mut(session_id).unwrap();
                if let Err(error) = s.chat_observation.observe(
                    &event,
                    &Default::default(),
                    &chat_context::CatalogLabels,
                ) {
                    tracing::warn!(event="chat_observation_rejected", session_id, %error);
                }
            }
            self.resolve_vocalization(d.sessions.get_mut(session_id).unwrap(), Some(&event));
            self.start_pending(&mut d, &mut jobs, session_id);
            self.tick_workshop(&mut d, session_id);
            if recent && complete && text.trim().is_empty() {
                self.start_workshop_recovery(&mut d, &mut jobs, session_id);
            }
            d.revision += 1;
        }
        if let Some(feedback) = results.feedback.clone() {
            self.queue_fixed_reply(&mut d, &mut jobs, session_id, feedback, None);
        }
        let mut episode_snapshot = episode_before.map(|mut before| {
            let actions = before.actions(&d, session_id);
            let s = &d.sessions[session_id];
            (before, json!(s.mode), s.foreground.combat_active, actions)
        });
        drop(d);
        drop(jobs);
        if !duplicate && recent && complete_observation(&event) && text.trim().is_empty() {
            self.resume_knowledge_input(session_id);
        }
        if !duplicate
            && recent
            && event.event.name == EventName::StatusSnapshot
            && text.trim().is_empty()
        {
            self.try_start_haiku(session_id, false);
        }
        let input = if input_handled {
            json!({"accepted":true,"reason":"combat_input"})
        } else if !duplicate && !text.trim().is_empty() {
            self.submit_recorded(
                Some(session_id),
                &text,
                "text",
                false,
                input_generation.map(|g| (g, None)),
                false,
                workshop_record::Admission {
                    forwarded: false,
                    private,
                },
            )
        } else {
            Value::Null
        };
        let commands = {
            let d = self.data.lock().unwrap();
            if let Some((before, _, _, actions)) = episode_snapshot.as_mut() {
                actions.extend(before.input_actions(&d, session_id, &text));
            }
            d.sessions
                .get(session_id)
                .map(|s| s.assist.pending_commands(chrono::Utc::now()))
                .unwrap_or_default()
        };
        let event_id = id("evt");
        let recorded_at = chrono::Utc::now();
        if let Some((before, mode_after, combat_active, actions)) = episode_snapshot {
            // ACKs may repeat. Only newly consumed real receipts establish execution evidence.
            let command_results = results
                .observed
                .iter()
                .filter(|o| {
                    event
                        .command_results
                        .iter()
                        .any(|r| r.command_id == o.result.command_id)
                })
                .map(|o| json!(o.result))
                .collect();
            self.record_episode(crate::episode_log::Record {
                event: event.clone(),
                event_id: event_id.clone(),
                session_id: session_id.into(),
                recorded_at: recorded_at.to_rfc3339(),
                state_before: before.state,
                mode_after,
                combat_active,
                actions,
                adapter_commands: commands.iter().map(|c| json!(c)).collect(),
                command_results,
            });
        }
        json!({"accepted":true,"event_id":event_id,"session_id":session_id,"sequence":sequence,"deduplicated":duplicate,
            "state":null,"outputs":null,"commands":commands,"acknowledged_command_ids":results.acknowledged_ids,"server_time":recorded_at,"phase":"dialogue","player_input":input,"_workshop_direct_input":input_handled})
    }
    /// 明示入力を対象接続へ送り、受理・保留・重複・拒否をすぐ返す。
    /// 通常turnの生成や再生は登録したjobで進み、ここでのacceptedは再生成功を意味しない。
    pub fn submit(self: &Arc<Self>, selected: Option<&str>, text: &str, source: &str) -> Value {
        self.submit_recorded(
            selected,
            text,
            source,
            false,
            None,
            false,
            workshop_record::Admission::default(),
        )
    }
    fn submit_inner(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
    ) -> Value {
        self.submit_recorded(
            selected,
            text,
            source,
            skip_assist,
            expected_generation,
            combat_classified,
            workshop_record::Admission {
                forwarded: true,
                private: false,
            },
        )
    }
    #[allow(clippy::too_many_arguments)] // The last value is recording metadata, not routing authority.
    fn submit_recorded(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
        origin: workshop_record::Admission,
    ) -> Value {
        let sid = selected.map(str::to_owned).or_else(|| self.only_session());
        let (before, private) = {
            let d = self.data.lock().unwrap();
            let s = sid.as_deref().and_then(|sid| d.sessions.get(sid));
            (
                workshop_record::state(s.and_then(|s| s.haiku.workshop.as_ref())),
                origin.private
                    || s.is_some_and(|s| {
                        s.web.state.research.is_some()
                            || origin.forwarded
                                && s.record_private_generation.is_some_and(|(g, private)| {
                                    private
                                        && expected_generation
                                            .as_ref()
                                            .is_some_and(|(expected, _)| *expected == g)
                                })
                    }),
            )
        };
        let result = self.submit_impl(
            sid.as_deref(),
            text,
            source,
            skip_assist,
            expected_generation,
            combat_classified,
            private,
        );
        if let Some(sid) = sid {
            let (after, mut recorded_input) = {
                let d = self.data.lock().unwrap();
                let row = d
                    .rows
                    .iter()
                    .find(|r| r["session_id"] == sid && r["turn_id"] == result["turn_id"]);
                let mut input = workshop_record::Input {
                    raw: text.into(),
                    private,
                    ..Default::default()
                };
                if let Some(row) = row {
                    input.private |= row["workshop_record_private"] == true;
                    input.epoch = row["epoch"].as_u64();
                    if row["player_input_text"] == text {
                        input.semantic = row["interpreted_player_input_text"]
                            .as_str()
                            .map(str::to_owned);
                    }
                }
                (
                    workshop_record::state(
                        d.sessions.get(&sid).and_then(|s| s.haiku.workshop.as_ref()),
                    ),
                    input,
                )
            };
            // Early rejection/classification has no semantic interpretation yet.
            if result["deduplicated"] == true {
                recorded_input.private = true;
            }
            self.record_workshop(
                &sid,
                if origin.forwarded {
                    "forwarded_admission"
                } else {
                    "input_admission"
                },
                &before,
                &after,
                &recorded_input,
                &result,
            );
        }
        result
    }
    #[allow(clippy::too_many_arguments)]
    fn submit_impl(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
        private: bool,
    ) -> Value {
        if text.trim().is_empty() {
            return json!({"accepted":false,"reason":"empty_text"});
        }
        if text.chars().count() > 1000 {
            return json!({"accepted":false,"reason":"text_too_long"});
        }
        let session_id = selected.map(str::to_owned).or_else(|| self.only_session());
        let Some(session_id) = session_id else {
            return json!({"accepted":false,"reason":"select_one_session"});
        };
        let mut jobs = self.jobs.lock().unwrap();
        jobs.retain(|j| !j.is_finished());
        let vocalization =
            source.trim().eq_ignore_ascii_case("voice") && crate::vocalization::is_pure(text);
        if jobs.len() >= 16 && !vocalization {
            let mut d = self.data.lock().unwrap();
            self.tick_address(&mut d, &session_id);
            if d.sessions
                .get(&session_id)
                .and_then(|s| s.address.as_ref())
                .is_some_and(|p| p.input(text) == crate::address::Action::Accept)
            {
                Self::cancel_address(&mut d, &session_id, "host_chat_queue_full");
            }
            return json!({"accepted":false,"reason":"input_queue_full"});
        }
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return json!({"accepted":false,"reason":"server_stopping"});
        }
        let Some(s) = d.sessions.get_mut(&session_id) else {
            return json!({"accepted":false,"reason":"unknown_session_id"});
        };
        if expected_generation
            .as_ref()
            .is_some_and(|(g, _)| *g != s.input_generation)
        {
            return json!({"accepted":false,"reason":"superseded_input"});
        }
        if vocalization {
            return self.accept_vocalization(&mut d, &session_id, text);
        }
        self.resolve_vocalization(s, None);
        if !observation_fresh(s) {
            return json!({"accepted":false,"reason":"fresh_safe_snapshot_required"});
        }
        if let Some(p) = s.knowledge_queue.iter().find(|p| p.request.text == text) {
            return json!({"accepted":true,"deduplicated":true,"queued":true,
                "turn_id":p.request.turn,"session_id":session_id});
        }
        let queued_knowledge = s
            .knowledge_checked
            .as_ref()
            .filter(|c| {
                expected_generation
                    .as_ref()
                    .is_some_and(|(g, _)| c.generation == *g)
            })
            .and_then(|c| c.original.clone());
        if let Some(p) = s.combat_input.as_ref().filter(|p| p.text == text) {
            return json!({"accepted":true,"deduplicated":true,"turn_id":p.turn,"session_id":session_id});
        }
        if let Some(p) = s
            .assist_pending
            .as_ref()
            .filter(|p| p.input.raw_text == text)
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":p.turn,"session_id":session_id});
        }
        if s.deferred_input.as_ref().is_some_and(|p| p.text == text) {
            return json!({"accepted":true,"deduplicated":true,"queued":true,"session_id":session_id});
        }
        s.deferred_input = None;
        if let Some(w) = s
            .warning
            .as_ref()
            .filter(|w| w.input.as_deref() == Some(text))
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":w.turn,"session_id":session_id});
        }
        if s.pending_input.as_deref() == Some(text) && s.pending_warning.is_some() {
            return json!({"accepted":true,"deduplicated":true,"queued":true,"session_id":session_id});
        }
        if expected_generation.is_none() {
            s.input_generation = s.input_generation.wrapping_add(1);
        }
        s.record_private_generation = Some((s.input_generation, private));
        Self::cancel_combat_input(&mut d, &session_id);
        Self::cancel_haiku(&mut d, &session_id, "new_player_input");
        Self::cancel_assist(&mut d, &session_id);
        Self::cancel_light(&mut d, &session_id);
        let now = self.clock.elapsed().as_millis() as u64;
        let s = d.sessions.get_mut(&session_id).unwrap();
        s.last_player_input = Some(now);
        s.ambient.note_player_input(now);
        if s.warning.as_ref().is_some_and(|w| {
            w.actions
                .iter()
                .all(|a| a.delivery == crate::combat::model::Delivery::Ambient)
        }) {
            Self::cancel_warning(&mut d, &session_id, "new_player_input");
        }
        if !skip_assist
            && !workshop_focus::quiet(&d.sessions[&session_id])
            && let Some(result) = self.assist_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if let Some(event) = s.environment_latest.as_ref().or(s.latest.as_ref())
            && s.chat_allowed
            && !workshop_focus::active(s)
            && let Some(mut speech) = s.ambient.smell_query(event, text, now)
        {
            speech.delivery = crate::combat::model::Delivery::PlayerReply;
            s.pending_warning = Some(vec![speech]);
            s.pending_input = Some(text.to_owned());
            Self::cancel_chat(&mut d, &session_id, "smell_query");
            self.start_pending(&mut d, &mut jobs, &session_id);
            return json!({"accepted":true,"session_id":session_id,"reason":"smell_query"});
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if !workshop_focus::quiet(s)
            && let Some(event) = s.latest.clone()
        {
            let decision = s.combat.input(
                &event,
                text,
                self.clock.elapsed().as_millis() as u64,
                &self.config.combat,
                &self.config.warnings,
            );
            if let Some(decision) = decision {
                Self::cancel_chat(&mut d, &session_id, "combat_input");
                self.apply_combat_decision(
                    &mut d,
                    &mut jobs,
                    &session_id,
                    decision,
                    Some(text),
                    None,
                );
                self.start_pending(&mut d, &mut jobs, &session_id);
                let s = &d.sessions[&session_id];
                return json!({"accepted":true,"session_id":session_id,"reason":"combat_input",
                    "turn_id":if s.pending_warning.is_some(){None}else{s.warning.as_ref().map(|w|w.turn.clone())},"queued":s.pending_warning.is_some(),"state":s.mode});
            }
        }
        if let Some(result) =
            self.queue_knowledge_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if s.warning
            .as_ref()
            .is_some_and(|w| w.actions.iter().any(|a| a.kind == "workshop_resume"))
        {
            Self::cancel_warning(&mut d, &session_id, "new_player_input");
        }
        if !combat_classified
            && let Some(result) =
                self.classify_paused_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if !workshop_combat_input::allowed(s) || s.warning.is_some() {
            if fresh(s)
                && s.warning.as_ref().is_some_and(|w| {
                    w.actions
                        .iter()
                        .all(|a| a.delivery == crate::combat::model::Delivery::UrgentEnvironment)
                })
            {
                s.deferred_input = Some(environment_runtime::DeferredInput {
                    text: text.to_owned(),
                    source: source.to_owned(),
                    previous_turn: None,
                });
                return json!({"accepted":true,"queued":true,"session_id":session_id,"reason":"after_environment_warning"});
            }
            return json!({"accepted":false,"reason":"fresh_safe_snapshot_required"});
        }
        self.tick_workshop(&mut d, &session_id);
        if let Some(result) = self.check_address_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let (address_reply, replay) = match self.address_input(&mut d, &session_id, text, source) {
            address_runtime::Input::Pass => (None, None),
            address_runtime::Input::Repair(reply) => (Some(reply), None),
            address_runtime::Input::Replay(original) => (None, Some(original)),
            address_runtime::Input::Consumed(result) => return result,
        };
        let host_chat_confirmed = replay.is_some();
        let replay = replay.or(queued_knowledge);
        let text = replay.as_ref().map_or(text, |p| p.text.as_str());
        let source = replay.as_ref().map_or(source, |p| p.source.as_str());
        let private = private || replay.as_ref().is_some_and(|p| p.record_private);
        let s = d.sessions.get_mut(&session_id).unwrap();
        let active_turn = s.current_turn.clone();
        // 同じ進行中入力の二重配送を、割り込みやLLM再呼出しにしない。
        if let Some(last) = d
            .rows
            .iter()
            .rev()
            .find(|r| r["turn_id"] == active_turn && r["player_input_text"] == text)
            && matches!(
                last["playback_status"].as_str(),
                Some("generating" | "queued" | "started")
            )
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":last["turn_id"]});
        }
        Self::cancel_chat(&mut d, &session_id, "new_player_input");
        self.tick_foreground(&mut d, &session_id);
        let text_prompt = d.text_prompts.clone();
        let text_prompt_version = d.text_prompt_version;
        let s = d.sessions.get_mut(&session_id).unwrap();
        let workshop = Self::workshop_view(s, text);
        let (interpreted_text, asr_corrections) =
            crate::contextual_asr::for_workshop(text, source, &json!(workshop));
        if !asr_corrections.is_empty() {
            tracing::info!(event="asr_fix_conversation",session_id,original=text,
                interpreted=interpreted_text,applied=?asr_corrections);
        }
        let poem_input = crate::poem_input::parse(text, workshop.is_some());
        let poem_reference = s
            .haiku
            .workshop
            .as_ref()
            .map(|w| json!({"id":w.hud_id,"version":w.version,"open":w.open}));
        let event = s
            .latest
            .as_ref()
            .cloned()
            .unwrap_or_else(|| GameEvent::parse(empty_event(&s.name)).expect("preview event"));
        if let Some((_, Some(previous))) = expected_generation {
            s.history.replace_unanswered(&previous);
        }
        let resume_context = s.foreground.resume_prompt(text);
        let event_digest = s
            .history
            .situation_lines()
            .iter()
            .map(|n| format!("- {n}"))
            .chain(s.combat_digest.iter().map(|n| format!("- {n}")))
            .chain((!resume_context.is_empty()).then_some(resume_context))
            .collect::<Vec<_>>()
            .join("\n");
        let history = s.history.rows();
        let conversation_history = s.history.lines();
        let chat_native = match chat_context::capture(
            s,
            &event,
            &self.config.combat,
            crate::chat_materials::CompletedHistory {
                conversation_history: conversation_history.clone(),
                conversation_turns: history.clone(),
                event_digest: event_digest.clone(),
            },
            workshop.as_ref(),
        ) {
            Ok(native) => native,
            Err(error) => {
                tracing::warn!(event="chat_snapshot_rejected", session_id, %error);
                return json!({"accepted":false,"reason":"chat_context_unavailable"});
            }
        };
        if (poem_input.is_some() || crate::haiku_memory::clear_requested(text))
            && let Some(w) = s.haiku.workshop.as_mut()
        {
            w.followup = crate::workshop_followup::Stage::Discussion;
        }
        let audit_before = workshop_record::state(s.haiku.workshop.as_ref());
        s.epoch += 1;
        let epoch = s.epoch;
        let turn = replay
            .as_ref()
            .map_or_else(|| id("turn"), |p| p.turn.clone());
        if address_reply.is_some()
            && let Some(p) = s.address.as_mut()
        {
            p.repair = Some((turn.clone(), false));
        }
        if let Some(checked) = s.knowledge_checked.as_ref()
            && checked.original.as_ref().is_some_and(|r| r.turn == turn)
            && let Some(audit) = &checked.audit
        {
            audit.transferred();
        }
        s.current_turn = turn.clone();
        s.status = PlaybackStatus::Generating;
        let (language_active, language_state) = language_runtime::context(s, text);
        let input = json!({"audit_before":audit_before,"audit_private":private,"model":self.config.model,"max_tokens":self.config.max_tokens,"reading_engine":self.config.reading_engine,"workshop":workshop,
            "poem_input":poem_input,"poem_reference":poem_reference,"operation_id":turn,
            "source":source,"language_active":language_active,"language_state":language_state,
            "address_reply":address_reply,"host_chat_confirmed":host_chat_confirmed,
            "input_at_ms":now,"previous_activity_ms":s.foreground.last_player_at.max(s.last_completed_conversation),
            "text":text,"interpreted_text":interpreted_text,"history":history,"conversation_history":conversation_history,
            "event_digest":event_digest,"event":event});
        let mut input = input;
        if s.text_workshop {
            input["text_workshop_prompt"] = text_prompt.unwrap_or(Value::Null);
            input["text_prompt_version"] = text_prompt_version.into();
        }
        let mut input = bridge::Input::native(input, event, chat_native);
        input.retained_history_count = s.history.retained_count();
        if workshop.is_none()
            && s.web.state.research.is_none()
            && poem_input.is_none()
            && !crate::haiku_memory::memory_candidate(text)
        {
            s.history.push(&turn, "user", text);
        }
        let (cancel, rx) = watch::channel(false);
        s.cancel = Some(cancel);
        if replay.is_none() && d.rows.len() == 200 {
            d.rows.pop_front();
        }
        let mut new_row = json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":session_id,"category":"speech","text":"",
            "created_at":chrono::Utc::now(),"input_at_ms":now,"epoch":epoch,"reference_ids":[],"output_mode":"both","player_input_text":text,"source":source,"playback_status":"generating",
            "interpreted_player_input_text":interpreted_text,"asr_corrections":asr_corrections,
            "workshop_id":workshop.as_ref().map(|w| &w["workshop_id"]),"workshop_fixed_close":false,"workshop_state_before":audit_before,"workshop_record_private":private});
        if let Some(original) = &replay {
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["playback_status"] = "generating".into();
                row["epoch"] = epoch.into();
                row["interpreted_player_input_text"] =
                    new_row["interpreted_player_input_text"].clone();
                row["asr_corrections"] = new_row["asr_corrections"].clone();
                row["workshop_state_before"] = new_row["workshop_state_before"].clone();
                row["workshop_record_private"] = new_row["workshop_record_private"].clone();
                if host_chat_confirmed {
                    row["language_status"] = "host_chat".into();
                    row["conversation_route"] = "casual".into();
                } else {
                    row["category"] = "speech".into();
                    row["workshop_id"] = new_row["workshop_id"].clone();
                    row["workshop_fixed_close"] = new_row["workshop_fixed_close"].clone();
                    row["resolution"] = "knowledge_resumed".into();
                }
                row["input_at_ms"] = original.input_at.into();
                row["resumed_at"] = chrono::Utc::now().to_rfc3339().into();
            } else {
                // 長い戦闘で表示履歴200件から落ちても、保留質問のIDを復元する。
                if d.rows.len() == 200 {
                    d.rows.pop_front();
                }
                new_row["input_at_ms"] = original.input_at.into();
                new_row["resumed_at"] = chrono::Utc::now().to_rfc3339().into();
                d.rows.push_back(new_row);
            }
        } else {
            d.rows.push_back(new_row);
        }
        d.revision += 1;
        drop(d);
        tracing::info!(
            event = "dialogue_input",
            session_id,
            turn_id = turn,
            text,
            source
        );
        let this = self.clone();
        let sid = session_id.clone();
        let tid = turn.clone();
        let job = tokio::spawn(async move {
            this.run_turn(sid, tid, epoch, input, rx).await;
        });
        jobs.push(job);
        json!({"accepted":true,"session_id":session_id,"turn_id":turn})
    }
    /// 接続の進行中処理を取り消し、保留入力と世代を更新して遅れた結果の反映を止める。
    pub fn interrupt(&self, session_id: &str) {
        let mut d = self.data.lock().unwrap();
        self.cancel_knowledge_queue(&mut d, session_id, "manual_interrupt");
        Self::cancel_haiku(&mut d, session_id, "manual_interrupt");
        Self::cancel_chat(&mut d, session_id, "manual_interrupt");
        Self::cancel_combat_input(&mut d, session_id);
        Self::cancel_warning(&mut d, session_id, "manual_interrupt");
        Self::cancel_assist(&mut d, session_id);
        Self::cancel_light(&mut d, session_id);
        if let Some(s) = d.sessions.get_mut(session_id) {
            s.pending_vocalization = None;
            if !s.foreground.combat_active {
                s.history.end_danger(
                    self.config
                        .combat
                        .ms("conversation_post_danger_player_turns"),
                );
            }
            s.deferred_input = None;
            s.input_generation = s.input_generation.wrapping_add(1);
            if let Some(w) = s.haiku.workshop.as_mut() {
                w.followup = crate::workshop_followup::Stage::Discussion;
            }
        }
        d.revision += 1;
    }
    async fn run_turn(
        self: Arc<Self>,
        sid: String,
        turn: String,
        epoch: u64,
        mut input: bridge::Input,
        cancel: watch::Receiver<bool>,
    ) {
        let before = input
            .as_object_mut()
            .and_then(|o| o.remove("audit_before"))
            .unwrap_or(Value::Null);
        let private = input
            .as_object_mut()
            .and_then(|o| o.remove("audit_private"))
            == Some(Value::Bool(true));
        let attempt = workshop_record::Attempt::new(
            before,
            workshop_record::Input {
                raw: input["text"].as_str().unwrap_or("").into(),
                semantic: input["interpreted_text"].as_str().map(str::to_owned),
                private,
                epoch: Some(epoch),
            },
        );
        self.clone()
            .run_turn_body(sid.clone(), turn.clone(), epoch, input, cancel)
            .await;
        self.record_workshop_turn(&sid, &turn, &attempt);
    }
    /// 通常turnを直列枠内で実行する。待ち時間中の観測・保存済み句を取り込み直し、
    /// routing/生成 → 状態再照合・保存 → 読み上げ → 実再生結果の反映、まで所有する。
    /// dataは必要な照合区間ごとに取り直すため、この間もゲーム観測を受理できる。
    async fn run_turn_body(
        self: Arc<Self>,
        sid: String,
        turn: String,
        epoch: u64,
        mut input: bridge::Input,
        mut cancel: watch::Receiver<bool>,
    ) {
        let started = Instant::now();
        let permit = tokio::select! { _=bridge::cancelled(&mut cancel)=>{self.update(&sid,&turn,epoch,PlaybackStatus::Cancelled,None);return;}, p=self.serial.acquire()=>p.unwrap() };
        // Pick up rain/smell changes and reactions completed while waiting for
        // the preceding turn. The resulting snapshot is fixed for this decision.
        {
            let d = self.data.lock().unwrap();
            if let Some(s) = d.sessions.get(&sid).filter(|s| s.epoch == epoch)
                && let Some((event, previous)) = input.snapshot.as_deref()
            {
                let event = s.latest.as_ref().unwrap_or(event);
                let mut history = previous.context.history.clone();
                history.conversation_turns = s.history.refresh_before_input(
                    &turn,
                    &history.conversation_turns,
                    input.retained_history_count,
                );
                history.conversation_history =
                    history::History::lines_for(&history.conversation_turns);
                let workshop = input["workshop"].is_object().then_some(&input["workshop"]);
                if let Ok(native) =
                    chat_context::capture(s, event, &self.config.combat, history, workshop)
                {
                    let event = event.clone();
                    input["history"] = json!(native.context.history.conversation_turns);
                    input["conversation_turns"] = input["history"].clone();
                    input["conversation_history"] =
                        native.context.history.conversation_history.clone().into();
                    input.snapshot = Some(std::sync::Arc::new((event, native)));
                }
            }
        }
        // An earlier authorized save may have completed while this turn waited.
        // Rebuild the read-only context before planning against that new version.
        if input["workshop"].is_object() && input["poem_input"].is_null() {
            let mut d = self.data.lock().unwrap();
            if let Some(s) = d.sessions.get_mut(&sid)
                && s.epoch == epoch
                && s.haiku.workshop.as_ref().is_some_and(|w| {
                    w.open
                        && input["workshop"]["workshop_id"] == w.hud_id
                        && input["workshop"]["version"] != w.version
                })
            {
                let text = input["text"].as_str().unwrap_or("").to_owned();
                if let Some(view) = Self::workshop_view(s, &text) {
                    input["workshop"] = view;
                }
            }
        }
        // 観測更新が途切れた場合も、古い場所の返答・音声を続けない。
        let owner = self.clone();
        let monitor_sid = sid.clone();
        let mut monitor_cancel = cancel.clone();
        let workshop_id = input["workshop"]["workshop_id"].as_str().map(str::to_owned);
        let provisional_target = input["workshop"]["provisional"].as_str().map(str::to_owned);
        let monitor = monitor::Monitor::spawn(async move {
            loop {
                tokio::select! {
                    _=bridge::cancelled(&mut monitor_cancel)=>break,
                    _=tokio::time::sleep(Duration::from_millis(200))=>{
                        let mut d=owner.data.lock().unwrap();
                        owner.tick_workshop(&mut d, &monitor_sid);
                        let stale = d.sessions.get(&monitor_sid).is_some_and(|s| {
                            // 閉じる返答の配送中も、開始時と同じ安定した敵なら最後まで話せる。
                            let same_threat = provisional_target.as_ref().is_some_and(|key| {
                                workshop_combat_input::ready(s, owner.clock.elapsed().as_millis() as u64, &owner.config).as_ref() == Some(key)
                            });
                            let workshop_allowed = workshop_id.is_some()
                                && (workshop_focus::quiet_observation(s)
                                    || workshop_combat_input::provisional(s) || same_threat);
                            s.epoch == epoch && !(fresh(s) || workshop_allowed)
                        });
                        if stale {Self::cancel_chat(&mut d,&monitor_sid,"stale_observation");break;}
                        let expired = workshop_id.as_ref().is_some_and(|wid| d.sessions.get(&monitor_sid)
                            .filter(|s| s.epoch==epoch).is_some_and(|s| s.haiku.workshop.as_ref()
                                .is_none_or(|w| w.hud_id != *wid || (!w.is_open() && w.close_reason.as_deref()!=Some("explicit_close")))));
                        if expired {Self::cancel_chat(&mut d,&monitor_sid,"workshop_changed");break;}
                    }
                }
            }
        });
        let mut completed = false;
        self.prepare_web(&sid, epoch, &mut input, &cancel).await;
        let web_result = self.web_turn(&sid, epoch, &input, &mut cancel).await;
        let result = if let Err(error) = web_result {
            Err(error)
        } else if let Some(result) = web_result.unwrap() {
            Ok(result)
        } else if input["address_reply"].is_string() {
            match self.select_foreground(&sid, &turn, epoch, crate::foreground::Route::Learning) {
                Ok(()) => bridge::render(&self.config, &self.llm, input.clone(), &mut cancel).await,
                Err(error) => Err(error),
            }
        } else if input["poem_input"].is_object() {
            self.save_poem_input(&sid, epoch, &input, &mut cancel).await
        } else if crate::haiku_memory::clear_requested(input["text"].as_str().unwrap_or("")) {
            self.clear_lessons(&sid, epoch, &input).await
        } else if input["workshop"].is_object() {
            match self.render_workshop(&input, &mut cancel).await {
                Ok(workshop) if workshop["workshop_action"] == "unrelated" => {
                    let safe = self
                        .data
                        .lock()
                        .unwrap()
                        .sessions
                        .get(&sid)
                        .is_some_and(fresh);
                    if !safe {
                        Err(anyhow::anyhow!("combat_workshop_only"))
                    } else {
                        let mut ordinary = input.clone();
                        // 対話plannerには過去の通常会話と現在入力だけを渡す。
                        ordinary["workshop"] = Value::Null;
                        ordinary["workshop_fallback"] = true.into();
                        match bridge::render(&self.config, &self.llm, ordinary, &mut cancel).await {
                            Ok(mut result) => {
                                let mut reports = workshop["llm_reports"]
                                    .as_array()
                                    .cloned()
                                    .unwrap_or_default();
                                reports.extend(
                                    result["llm_reports"]
                                        .as_array()
                                        .cloned()
                                        .unwrap_or_default(),
                                );
                                for key in [
                                    "workshop_id",
                                    "workshop_action",
                                    "workshop_steps",
                                    "workshop_reason",
                                ] {
                                    result[key] = workshop[key].clone();
                                }
                                result["llm_reports"] = json!(reports);
                                Ok(result)
                            }
                            Err(error) => Err(error),
                        }
                    }
                }
                result => result,
            }
        } else {
            bridge::render_with_route(
                &self.config,
                &self.llm,
                input.clone(),
                &mut cancel,
                |route| self.select_foreground(&sid, &turn, epoch, route),
                |outcome| self.hold_language_handoff(&sid, &turn, epoch, &input, outcome),
            )
            .await
        };
        let result = match result {
            Ok(r) if r["memory_query"].is_object() => {
                self.recall_poems(&sid, epoch, &input, &r["memory_query"], &mut cancel)
                    .await
            }
            r => r,
        };
        match result {
            Ok(mut result) => {
                if input["host_chat_confirmed"] == true {
                    result["language_status"] = "host_chat".into();
                    result["language_state"] = json!(crate::language::State::default());
                }
                self.apply_language_result(&sid, &turn, epoch, &result);
                self.record_feedback(&sid, epoch, &input, &mut result).await;
                if let Err(error) = self.apply_workshop_edit(&sid, epoch, &mut result).await {
                    result["text"] =
                        "句が変わったか、編集を続けられん状態になったわ。もう一度確認してな。"
                            .into();
                    result["spoken_text"] = result["text"].clone();
                    result["workshop_reason"] = error.to_string().into();
                    result["workshop_action"] = "fallback".into();
                }
                if let Some(outcome) = result.get("workshop_outcome").cloned()
                    && let Some(last) = result
                        .get_mut("workshop_steps")
                        .and_then(Value::as_array_mut)
                        .and_then(|s| s.last_mut())
                {
                    last["outcome"] = outcome;
                }
                if let Some(unsupported) = result.get("unsupported").cloned() {
                    result["error"] = unsupported;
                    self.update(
                        &sid,
                        &turn,
                        epoch,
                        PlaybackStatus::Unsupported,
                        Some(&result),
                    );
                } else if result["language_status"] == "awaiting_address" {
                    self.update(
                        &sid,
                        &turn,
                        epoch,
                        PlaybackStatus::NotSelected,
                        Some(&result),
                    );
                } else if result["text"].as_str().is_none_or(|s| s.is_empty()) {
                    self.update(&sid, &turn, epoch, PlaybackStatus::Quiet, Some(&result));
                } else if self.update(&sid, &turn, epoch, PlaybackStatus::Queued, Some(&result)) {
                    tracing::info!(
                        event = "dialogue_reply",
                        session_id = sid,
                        turn_id = turn,
                        text = result["text"].as_str().unwrap_or(""),
                        generation_ms = started.elapsed().as_millis() as u64
                    );
                    let playback = self
                        .audio
                        .speak(
                            &self.config,
                            result["spoken_text"].as_str().unwrap_or(""),
                            &mut cancel,
                            || {
                                self.update(&sid, &turn, epoch, PlaybackStatus::Started, None);
                            },
                        )
                        .await;
                    let status = match playback {
                        Ok(()) => PlaybackStatus::Completed,
                        Err(error) => {
                            result["error"] = error.to_string().into();
                            if *cancel.borrow() {
                                PlaybackStatus::Cancelled
                            } else if !self.config.audio_enabled {
                                result.as_object_mut().unwrap().remove("error");
                                PlaybackStatus::AudioDisabled
                            } else {
                                PlaybackStatus::Failed
                            }
                        }
                    };
                    // Web開始は音声プロセスの実終了と同じepochの確認後だけ。
                    self.web_playback(&sid, epoch, &result, status);
                    completed = self.update(&sid, &turn, epoch, status, Some(&result))
                        && status == PlaybackStatus::Completed
                        && result["workshop_action"].is_null()
                        && result["knowledge_status"].is_null()
                        && result["memory_action"].is_null();
                }
            }
            Err(error) => {
                self.update(
                    &sid,
                    &turn,
                    epoch,
                    if *cancel.borrow() {
                        PlaybackStatus::Cancelled
                    } else {
                        PlaybackStatus::Failed
                    },
                    Some(&json!({"error":error.to_string()})),
                );
            }
        }
        monitor.finish().await;
        drop(permit);
        if completed {
            self.try_start_haiku(&sid, true);
        }
        tracing::info!(
            event = "dialogue_finished",
            session_id = sid,
            turn_id = turn,
            total_ms = started.elapsed().as_millis() as u64
        );
    }
    pub fn snapshot(&self, selected: Option<&str>) -> Value {
        let d = self.data.lock().unwrap();
        let rows = d
            .rows
            .iter()
            .filter(|r| selected.is_none_or(|id| r["session_id"] == id))
            .collect::<Vec<_>>();
        json!({"revision":d.revision,"phase":"dialogue","audio_enabled":self.config.audio_enabled,"llm_enabled":self.config.llm_enabled,
            "sessions":d.sessions.iter().map(|(id,s)|json!({"session_id":id,"name":s.name,"status":s.status,"observation_mode":if s.preview{"none"}else{"minecraft"},"history":s.history.rows(),"history_retention":s.history.retention_status(),"foreground":s.foreground.snapshot(self.clock.elapsed().as_millis() as u64),"web":s.web.state.snapshot(),"workshop_history":s.haiku.workshop.as_ref().map(|w| &w.dialogue),"workshop_followup":s.haiku.workshop.as_ref().map(|w| w.followup),"workshop_discussion_target":s.haiku.workshop.as_ref().and_then(|w| w.discussion_target.as_ref()),"state":s.mode,"chat_allowed":fresh(s)})).collect::<Vec<_>>(),
            "utterances":rows,"references":knowledge_display::collect(&rows)})
    }
    pub fn cancel_all(&self) {
        {
            let mut d = self.data.lock().unwrap();
            d.stopped = true;
            let ids = d.sessions.keys().cloned().collect::<Vec<_>>();
            for sid in ids {
                self.cancel_knowledge_queue(&mut d, &sid, "server_shutdown");
                Self::cancel_haiku(&mut d, &sid, "server_shutdown");
                Self::cancel_chat(&mut d, &sid, "server_shutdown");
                Self::cancel_combat_input(&mut d, &sid);
                Self::cancel_warning(&mut d, &sid, "server_shutdown");
                Self::cancel_assist(&mut d, &sid);
                Self::cancel_light(&mut d, &sid);
            }
        }
    }
    /// 新規受付を停止して各処理を取り消し、登録済みjobの終了を待って記録を閉じる。
    /// cancel_allでdataを解放してからjobsを取得し、どちらのmutexもawaitへ持ち越さない。
    pub async fn shutdown(&self) {
        self.cancel_all();
        let jobs = std::mem::take(&mut *self.jobs.lock().unwrap());
        for job in jobs {
            let _ = job.await;
        }
        let open_workshops: Vec<_> = {
            let d = self.data.lock().unwrap();
            d.sessions
                .iter()
                .filter_map(|(sid, s)| {
                    s.haiku
                        .workshop
                        .as_ref()
                        .filter(|w| w.open)
                        .map(|w| (sid.clone(), workshop_record::state(Some(w))))
                })
                .collect()
        };
        for (sid, before) in open_workshops {
            self.record_workshop(
                &sid,
                "lifecycle",
                &before,
                &Value::Null,
                &workshop_record::Input::default(),
                &json!({"reason":"server_shutdown"}),
            );
        }
        self.workshop_records.flush().await;
        self.workshop_records.forget_all();
        self.combat_classifier.close().await;
        if let Some(recorder) = &self.episodes {
            let recorder = recorder.clone();
            let _ = tokio::task::spawn_blocking(move || recorder.close()).await;
        }
        tracing::info!(
            event = "dialogue_stopped",
            message = "会話補助・音声の終了を確認しました"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn only_fresh_complete_nonduplicate_observations_change_game_pause() {
        let mut config = DialogueConfig {
            audio_enabled: false,
            llm_enabled: false,
            ..Default::default()
        };
        config.haiku.enabled = false;
        let d = Dialogue::new(config).unwrap();
        d.register("pause", "試験", false);
        let frame = |seq, paused: Option<bool>, partial: bool, stale: bool| {
            let mut e = empty_event("試験");
            e["sequence"] = json!(seq);
            e["observed_at"] =
                json!(chrono::Utc::now() - chrono::Duration::seconds(if stale { 30 } else { 0 }));
            e["world"]["time_of_day"] = json!(13713);
            e["player"]["dimension"] = json!("minecraft:the_nether");
            if let Some(p) = paused {
                e["world"]["game_paused"] = json!(p);
            }
            if partial {
                e["event"]["name"] = json!("ambient_mob_detected");
                e["event"]["source_kind"] = json!("auditory");
            }
            GameEvent::parse(e).unwrap()
        };
        let paused = || {
            d.data.lock().unwrap().sessions["pause"]
                .foreground
                .game_paused
        };
        d.observe("pause", frame(1, Some(true), false, false), None);
        assert!(paused());
        for e in [
            frame(2, Some(false), true, false),
            frame(3, Some(false), false, true),
            frame(1, Some(false), false, false),
        ] {
            d.observe("pause", e, None);
            assert!(
                paused(),
                "partial, stale and duplicate observations cannot unpause"
            );
        }
        d.observe("pause", frame(4, None, false, false), None);
        assert!(
            !paused(),
            "legacy complete snapshots keep the former behavior"
        );
        d.observe("pause", frame(5, Some(true), false, false), None);
        assert!(paused());
        d.observe("pause", frame(6, Some(false), false, false), None);
        assert!(!paused(), "fixed world time and Nether do not imply pause");
        d.shutdown().await;
    }

    #[tokio::test]
    async fn knowledge_history_requires_playback_and_never_becomes_poem_material() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("knowledge-session", "試験", true);
        let result = json!({"text":"定型的な言葉やで。", "knowledge_status":"found", "references":[{
            "source_id":"src.example", "title_ja":"資料", "citation_label_ja":"文部科学省",
            "locator":"1頁", "url":"https://example.org/reference", "source_kind":"organization_authored_or_issued"}]});
        {
            let mut data = dialogue.data.lock().unwrap();
            for (sid, turn) in [("knowledge-session", "first"), ("other-session", "other")] {
                data.rows
                    .push_back(json!({"session_id":sid, "turn_id":turn,"utterance_id":turn,
                    "player_input_text":"枕詞って何？", "created_at":"2026-09-27T00:00:00Z"}));
            }
        }
        assert!(dialogue.update(
            "knowledge-session",
            "first",
            0,
            PlaybackStatus::Queued,
            Some(&result)
        ));
        assert!(dialogue.update(
            "knowledge-session",
            "first",
            0,
            PlaybackStatus::Failed,
            Some(&result)
        ));
        assert!(
            dialogue.data.lock().unwrap().sessions["knowledge-session"]
                .history
                .completed_pairs()
                .is_empty()
        );
        assert!(dialogue.update(
            "knowledge-session",
            "first",
            0,
            PlaybackStatus::Completed,
            Some(&result)
        ));
        {
            let data = dialogue.data.lock().unwrap();
            let session = &data.sessions["knowledge-session"];
            assert_eq!(session.history.completed_pairs().len(), 1);
            assert!(session.haiku.material_turns.is_empty());
        }
        let view = dialogue.snapshot(Some("knowledge-session"));
        assert_eq!(view["references"][0]["utterance_ids"], json!(["first"]));
        assert_eq!(view["utterances"][0]["category"], "knowledge");
        assert_eq!(
            view["utterances"][0]["reference_ids"][0],
            view["references"][0]["reference_id"]
        );
        assert_eq!(
            dialogue.snapshot(Some("other-session"))["references"],
            json!([])
        );
        dialogue.data.lock().unwrap().rows.pop_front();
        assert_eq!(dialogue.snapshot(None)["references"], json!([]));
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn pending_group_uses_latest_composition_after_queue_saturation() {
        let dialogue = Dialogue::new(DialogueConfig {
            audio_enabled: false,
            ..DialogueConfig::default()
        })
        .unwrap();
        dialogue.register("s", "試験", false);
        let event = |sequence, types: &[&str]| {
            GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture",
                "observed_at":chrono::Utc::now(),"sequence":sequence,
                "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
                "visual_threats":types.iter().enumerate().map(|(i,t)| json!({"type":t,"entity_id":format!("{t}{i}"),
                    "distance":8.2,"direction":{"horizontal":"front"}})).collect::<Vec<_>>()
            })).unwrap()
        };
        {
            let mut d = dialogue.data.lock().unwrap();
            let plan = crate::threats::Policy::default()
                .observe(
                    &event(1, &["zombie"; 3]),
                    0,
                    false,
                    &dialogue.config.warnings,
                )
                .unwrap();
            Dialogue::queue_actions(&mut d, "s", &[warnings::from_warning(plan)], None);
            // 発声前の待機中は、最新の個体数・構成へ更新する。
        }
        for _ in 0..16 {
            dialogue
                .jobs
                .lock()
                .unwrap()
                .push(tokio::spawn(std::future::pending()));
        }
        dialogue.observe("s", event(2, &["zombie"; 2]), None);
        dialogue.observe("s", event(3, &["zombie", "skeleton"]), None);
        let received = dialogue.data.lock().unwrap().sessions["s"].received;
        let mut partial = empty_event("試験");
        partial["sequence"] = 4.into();
        partial["event"]["name"] = "hostile_audio_detected".into();
        partial["event"]["source_kind"] = "auditory".into();
        partial["auditory_threats"] = json!([{"label":"zombie"}]);
        dialogue.observe("s", GameEvent::parse(partial).unwrap(), None);
        {
            let d = dialogue.data.lock().unwrap();
            assert_eq!(d.sessions["s"].received, received);
            assert!(!d.sessions["s"].chat_allowed);
            let pending = d.sessions["s"].pending_warning.as_ref().unwrap()[0]
                .visual_plan
                .as_ref()
                .unwrap();
            assert_eq!(pending.text, "スケルトン1体、ゾンビ1体おるで。");
            assert!(pending.cue.is_some());
        }
        let jobs = std::mem::take(&mut *dialogue.jobs.lock().unwrap());
        for job in jobs {
            job.abort();
            let _ = job.await;
        }
        dialogue.observe("s", event(5, &["zombie"; 2]), None);
        let view = dialogue.snapshot(None);
        let rows = view["utterances"].as_array().unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["playback_status"], "cancelled");
        assert_eq!(rows[1]["text"], "ひいっ！ ゾンビ2体おるで。");
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn pending_warning_tracks_repeated_direction_changes_while_jobs_are_full() {
        for (kind, fuse) in [("zombie", false), ("creeper", true)] {
            let dialogue = Dialogue::new(DialogueConfig {
                audio_enabled: false,
                ..DialogueConfig::default()
            })
            .unwrap();
            dialogue.register("s", "試験", false);
            let event = |sequence, direction| {
                GameEvent::parse(json!({
                "schema_version":"2026-05-24","adapter":"fixture","observed_at":chrono::Utc::now(),"sequence":sequence,
                "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
                "visual_threats":[{"type":kind,"entity_id":"z1","distance":8.2,"direction":{"horizontal":direction},"fuse_active":fuse}]
            })).unwrap()
            };
            {
                let mut d = dialogue.data.lock().unwrap();
                let plan = crate::threats::Policy::default()
                    .observe(&event(1, "front"), 0, false, &dialogue.config.warnings)
                    .unwrap();
                Dialogue::queue_actions(&mut d, "s", &[warnings::from_warning(plan)], None);
            }
            // 実モデル・playerを使わず、配送枠だけ埋める。
            for _ in 0..16 {
                dialogue
                    .jobs
                    .lock()
                    .unwrap()
                    .push(tokio::spawn(std::future::pending()));
            }
            dialogue.observe("s", event(2, "right"), None);
            dialogue.observe("s", event(3, "left"), None);
            {
                let d = dialogue.data.lock().unwrap();
                let pending = d.sessions["s"].pending_warning.as_ref().unwrap()[0]
                    .visual_plan
                    .as_ref()
                    .unwrap();
                assert_eq!(
                    pending.horizontal,
                    Some(crate::events::HorizontalDirection::Left)
                );
                assert!(pending.text.contains("左"));
            }
            let jobs = std::mem::take(&mut *dialogue.jobs.lock().unwrap());
            for job in jobs {
                job.abort();
                let _ = job.await;
            }
            dialogue.observe("s", event(4, "back_left"), None);
            let view = dialogue.snapshot(None);
            let rows = view["utterances"].as_array().unwrap();
            assert_eq!(rows.len(), 2);
            assert_eq!(rows[0]["playback_status"], "cancelled");
            assert_eq!(rows[1]["warning"]["horizontal"], "back_left");
            assert!(rows[1]["text"].as_str().unwrap().contains("左後ろ"));
            dialogue.shutdown().await;
        }
    }

    #[tokio::test]
    async fn late_playback_completion_cannot_commit_after_global_shutdown() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("test-session", "試験", true);
        {
            let mut data = dialogue.data.lock().unwrap();
            data.sessions.get_mut("test-session").unwrap().history.push(
                "test-turn",
                "user",
                "こんにちは",
            );
        }
        dialogue.cancel_all();
        // SIGINTとplayer成功終了が同時に届く場合を、完了通知を遅らせて再現。
        assert!(!dialogue.update(
            "test-session",
            "test-turn",
            0,
            PlaybackStatus::Completed,
            Some(&json!({"text":"こんにちは。"}))
        ));
        let snapshot = dialogue.snapshot(None);
        assert_eq!(
            snapshot["sessions"][0]["history"].as_array().unwrap().len(),
            1
        );
        assert_eq!(snapshot["sessions"][0]["history"][0]["role"], "user");
        dialogue.shutdown().await;
    }
    #[test]
    fn only_explicit_current_silence_records_reaction_without_spoken_material() {
        for (status, action, epoch, expect_silent) in [
            (PlaybackStatus::Quiet, "silent", 0, true),
            (PlaybackStatus::Failed, "silent", 0, false),
            (PlaybackStatus::Cancelled, "silent", 0, false),
            (PlaybackStatus::Quiet, "speak", 0, false),
            (PlaybackStatus::Quiet, "silent", 9, false),
        ] {
            let d = Dialogue::new(DialogueConfig::default()).unwrap();
            d.register("s", "試験", true);
            d.data.lock().unwrap().rows.push_back(
                json!({"session_id":"s","turn_id":"t","epoch":0,"player_input_text":"そうなんだ"}),
            );
            d.update(
                "s",
                "t",
                epoch,
                status,
                Some(&json!({"text":"","dialogue_action":action,"observation_revision":0})),
            );
            let data = d.data.lock().unwrap();
            let h = &data.sessions["s"].history;
            assert_eq!(
                h.rows().iter().any(|r| r["reaction"] == "silent"),
                expect_silent
            );
            assert!(h.completed_pairs().is_empty());
        }
    }
}
