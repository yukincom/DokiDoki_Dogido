//! パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
//! 環境判断と既存の戦闘・会話音声の優先順位を結ぶ。
use super::{Data, Dialogue, Session, bridge, observation_fresh};
use crate::{
    combat::model::{Delivery, Speech},
    environment::{
        ambient::{AmbientFocus, LightContext, LightPlanRequest},
        danger,
    },
    events::{EventName, GameEvent},
};
use serde_json::json;
use std::sync::Arc;
use tokio::{sync::watch, task::JoinHandle};

pub(super) struct DeferredInput {
    pub text: String,
    pub source: String,
    pub previous_turn: Option<String>,
    pub generation: u64,
    pub instance: String,
    pub binding: Option<super::request_runtime::Binding>,
    pub classification: Option<crate::request_classifier::Classification>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum CombatPriority {
    None,
    Selected,
    FrontAmbush,
}

pub(super) fn dark_audio(a: &Speech) -> bool {
    matches!(
        a.kind,
        "dark_push_no_light" | "dark_push_breath" | "dark_push_stop"
    )
}

fn reserved_reply(s: &Session) -> bool {
    s.pending_warning.as_ref().is_some_and(|actions| {
        actions
            .iter()
            .any(|a| matches!(a.delivery, Delivery::Combat | Delivery::PlayerReply))
    })
}

/// 視認の全観測を、音通知の空配列で上書きしない。環境の通知は別に保持する。
pub(super) fn context(s: &Session, event: &GameEvent, complete: bool) -> GameEvent {
    let Some(prior) = s
        .environment_latest
        .as_ref()
        .filter(|_| !complete && observation_fresh(s))
    else {
        return event.clone();
    };
    let mut data = serde_json::to_value(prior).expect("validated event");
    let incoming = serde_json::to_value(event).expect("validated event");
    for field in ["event", "sequence", "observed_at", "meta"] {
        data[field] = incoming[field].clone();
    }
    if event.event.name == EventName::AmbientMobDetected || !event.passive_mobs.is_empty() {
        data["passive_mobs"] = incoming["passive_mobs"].clone();
        data["companion_candidates"] = json!(event.companion_candidates);
    }
    // 種類表は今回の観測だけ。部分通知に無い表を前の測定から作らない。
    data["nearby_composition"] = incoming["nearby_composition"].clone();
    if event.smell_observation.is_some() {
        data["smell_observation"] = incoming["smell_observation"].clone();
    }
    if !event.ambient_sounds.is_empty() {
        data["ambient_sounds"] = incoming["ambient_sounds"].clone();
    }
    // このFabricの音・mob通知は敵配列だけ部分観測で、world/playerは現在の全section。
    // 既知producerとsectionの実測項目を照合し、「現在なし」の任意項目だけを消す。
    let fabric_sections = event.adapter == "dogido-fabric-client"
        && event.schema_version == "2026-05-24"
        && matches!(
            event.event.name,
            EventName::HostileAudioDetected | EventName::AmbientMobDetected
        );
    let world_complete = fabric_sections
        && event.world.weather.is_some()
        && event.world.biome.is_some()
        && event.world.local_light.is_some()
        && event.world.sky_visible.is_some();
    let player_complete = fabric_sections
        && event.player.dimension.is_some()
        && event.player.held_item.is_some()
        && event.player.hotbar.is_some()
        && event.player.position.x.is_some()
        && event.player.position.y.is_some()
        && event.player.position.z.is_some();
    if fabric_sections && world_complete && player_complete {
        // These producer events sample both sections even when no mob/crosshair
        // target exists. Do not let an old target overwrite newer measurements.
        data["passive_mobs"] = incoming["passive_mobs"].clone();
        data["companion_candidates"] = json!(event.companion_candidates);
        data["look_target"] = incoming["look_target"].clone();
    }
    for section in ["world", "player"] {
        for (key, value) in incoming[section].as_object().unwrap() {
            let current_absence = section == "player" && player_complete && key == "vehicle"
                || section == "world"
                    && world_complete
                    && matches!(
                        key.as_str(),
                        "structure"
                            | "nearby_portal_type"
                            | "nearby_portal_distance"
                            | "nearby_portal_encounter"
                            | "visible_villager_count"
                            | "nearby_end_portal_frame_distance"
                            | "respawn_distance"
                            | "boss_omen_kind"
                            | "ominous_sound_kind"
                            | "ominous_sound_recent_ms"
                            | "rain_sound_recent_ms"
                            | "thunder_sound_recent_ms"
                            | "nearby_lightning_strike_recent_ms"
                            | "nearby_lightning_strike_distance"
                            | "ender_eye_launch_recent_ms"
                            | "time_of_day"
                            | "time_phase"
                            | "surface_y"
                            | "depth_below_surface"
                    );
            if !value.is_null() || current_absence {
                data[section][key] = value.clone();
            }
        }
    }
    if let Some(full) = &s.latest {
        data["visual_threats"] = json!(full.visual_threats);
        data["combat"] = json!(full.combat);
    }
    // 最新の音は独立した有効期間を持つ。環境の安全判定にも渡す。
    if let Some(audio) = &s.audio_latest {
        data["auditory_threats"] = json!(audio.auditory_threats);
    }
    GameEvent::parse(data).expect("merged validated observations")
}

/// 同じ判断で受理した身体観測を使い、発話機会の制約を計算する。
/// 呼出元がdata lock内で投影したeventを渡すため、危険判定と視点・時点が揃う。
fn focus(
    s: &Session,
    e: &GameEvent,
    now: u64,
    settings: &crate::combat::model::Settings,
) -> AmbientFocus {
    let (boss, ominous) = s.combat.environmental_presence(now, settings);
    let light = s.danger.light_context(e, settings);
    let casual = s.foreground.route == crate::foreground::Route::Casual
        && !crate::combat::model::elapsed(
            now,
            s.last_player_input,
            settings.ms("conversation_active_ttl_ms"),
        );
    AmbientFocus {
        foreground: s.foreground.route != crate::foreground::Route::None || s.haiku.foreground(),
        casual_foreground: casual && !s.haiku.foreground(),
        last_player_input_at: s.last_player_input,
        player_priority: s.cancel.is_some() || s.assist_pending.is_some(),
        boss_presence: boss,
        ominous_presence: ominous,
        notice_ready: s.conversation_observation.noticing_context().is_some(),
        submerged: e.world.is_submerged == Some(true),
        safe_zone_with_door: danger::safe_zone_with_door(e),
        light: LightContext {
            surroundings_reasonably_lit: light.reasonably_lit,
            severe_darkness: light.severe_darkness,
            nearby_light_present: light.nearby_light,
            dark_push_context_before: light.dark_push_before,
            dark_push_recovered: light.recovered,
        },
    }
}

/// 環境判断の全観測・変化・履歴を一箇所で組み立て、生成直前にも更新する。
/// 静止画像は同じ入力へ添える。周期通知の比較差分とrevisionは保持し、未提示の差分の消費を防ぐ。
pub(super) fn refresh_dialogue_context(s: &Session, actions: &mut [Speech]) -> bool {
    // 同一更新の全actionへ同じ履歴を渡す。次の更新では待機中に確定した発話・沈黙も取り込む。
    let mut recent = None;
    for action in actions {
        if let Some(raw) = s.environment_latest.as_ref().or(s.latest.as_ref()) {
            let event = super::body_runtime::event(s, raw);
            if !crate::environment::reaction::refresh_danger(action, &event) {
                return false;
            }
        }
        if !crate::reaction_leaf::is_model_reaction(action) {
            continue;
        }
        super::visual_runtime::attach_latest(s, action);
        let leaf = action.leaf.as_mut().unwrap();
        let kind = if leaf.kind == crate::environment::reaction::KIND {
            action.kind
        } else {
            leaf.kind.as_str()
        };
        let key = if leaf.kind == crate::environment::reaction::KIND {
            "reaction_context"
        } else {
            "conversation_context"
        };
        if !leaf.details[key].is_object() {
            leaf.details[key] = json!({});
        }
        // 定期比較の差分は画像添付の有無によらず固定する。現在の全観測は生成直前へ揃え、
        // 撮影後に変わった天候・匂い・モブ等を古い値に戻さない。画像自体は撮影時刻付きで固定する。
        let periodic_notice = action.kind == crate::environment::notice::KIND
            && leaf.details["visual_change_trigger"] != true;
        if !periodic_notice || !leaf.details[key]["world_context"].is_object() {
            let mut context = if periodic_notice {
                let Some(context) = s.conversation_observation.noticing_context() else {
                    return false;
                };
                context
            } else {
                super::body_runtime::context(s).unwrap_or_default()
            };
            if let Some(image) = &action.image
                && leaf.details["visual_change_trigger"] == true
            {
                let capture = leaf.details["visual_capture"].clone();
                context
                    .changes
                    .push(crate::conversation_observation::Change {
                        kind: "body_view".into(),
                        before: json!(image.scene_comparison.as_ref().and_then(|c| c.reference.as_ref())),
                        now: json!({"sha256":image.sha256,"captured_at":image.captured_at,
                        "motion_sequence":capture["motion_sequence"]}),
                        observed_at: image.captured_at.clone(),
                        revision: context.revision,
                    });
                leaf.details[key]["visual_capture"] = capture;
            }
            leaf.details[key]["world_context"] = json!(context.for_model().for_reaction(kind, &leaf.details));
        }
        if periodic_notice {
            // 比較した差分とrevisionは固定し、観測本文だけを生成直前へ揃える。
            // 古い遮蔽・接触と最新姿勢を混在させず、未提示の差分を考慮済みにすることも防ぐ。
            let current = super::body_runtime::context(s)
                .unwrap_or_default()
                .for_reaction(kind, &leaf.details);
            let context = &mut leaf.details[key]["world_context"];
            context["observations"] = if current.observations.is_object() {
                current.observations
            } else {
                json!({"body_observation":{"status":"unavailable","reason":"not_sampled"},
                    "observer":"dogido","sensory_status":"unavailable"})
            };
            for (key, value) in [
                (
                    "catalog_knowledge",
                    current.catalog_knowledge.map(|v| json!(v)),
                ),
                (
                    "villager_routines",
                    current.villager_routines.map(|v| json!(v)),
                ),
            ] {
                if let Some(value) = value {
                    context[key] = value;
                } else if let Some(context) = context.as_object_mut() {
                    context.remove(key);
                }
            }
        }
        if action.image.is_some() {
            leaf.details[key]["visual_capture"] = leaf.details["visual_capture"].clone();
        }
        if action.rain_after_window.is_some() {
            // この一件の匂いだけ雨上がりへ集中する。通常の会話snapshotは触らないので、
            // 出力が終われば次の観測を待たず花・食品等の現在の匂いを使える。
            let mut smell = leaf.details[key]["observations"]["smell"].clone();
            smell["status"] = "present".into();
            leaf.details[key]["world_context"]["observations"]["smell"] = smell;
            if let Some(changes) = leaf.details[key]["world_context"]["changes"].as_array_mut() {
                changes.retain(|change| change["kind"] != "smell");
            }
        }
        leaf.details[key]["recent"] = recent
            .get_or_insert_with(|| json!({"conversation":s.model_history()}))
            .clone();
    }
    true
}


impl Dialogue {
    pub(super) fn resume_deferred(self: &Arc<Self>, sid: &str) {
        let input = {
            let mut d = self.data.lock().unwrap();
            if d.stopped {
                return;
            }
            let Some(s) = d.sessions.get_mut(sid) else {
                return;
            };
            if s.warning.is_some() || s.pending_warning.is_some() {
                return;
            }
            let input = s.deferred_input.take().filter(|input| {
                super::fresh(s)
                    && input.generation == s.input_generation
                    && input.instance == s.instance_id
                    && input
                        .binding
                        .as_ref()
                        .is_none_or(|binding| binding.current(s))
            });
            if let Some(input) = &input {
                s.request_classification.restore(
                    input.generation,
                    input.binding.clone(),
                    input.classification.clone(),
                );
            }
            input
        };
        if let Some(input) = input {
            self.submit_recorded_guarded(
                Some(sid),
                &input.text,
                &input.source,
                true,
                Some((input.generation, input.previous_turn)),
                false,
                super::workshop_record::Admission {
                    forwarded: true,
                    private: false,
                },
                Some(&input.instance),
                None,
            );
        }
    }

    pub(super) fn cancel_light(d: &mut Data, sid: &str) {
        if let Some(s) = d.sessions.get_mut(sid)
            && let Some(cancel) = s.light_cancel.take()
        {
            let _ = cancel.send(true);
        }
    }
    pub(super) fn queue_environment(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        mut actions: Vec<Speech>,
        urgent: bool,
    ) {
        if actions.iter().any(|a| a.kind == "dark_push_stop") {
            // 音を止める制御だけでは他の返事や戦闘警告を取り消さない。
            let s = &d.sessions[sid];
            if s.warning
                .as_ref()
                .is_some_and(|w| !w.finishing() && w.actions.iter().any(dark_audio))
            {
                Self::cancel_warning(d, sid, "darkness_recovered");
            } else if s
                .pending_warning
                .as_ref()
                .is_some_and(|a| a.iter().any(dark_audio))
            {
                let s = d.sessions.get_mut(sid).unwrap();
                s.pending_warning = None;
                s.pending_input = None;
            }
            actions.retain(|a| a.kind != "dark_push_stop");
        }
        if actions.is_empty() {
            return;
        }
        if super::workshop_focus::active(&d.sessions[sid])
            && !actions.iter().all(|a| a.kind == "player_hazard")
        {
            return;
        }
        if reserved_reply(&d.sessions[sid]) {
            return;
        }
        for a in &mut actions {
            a.delivery = if urgent {
                Delivery::UrgentEnvironment
            } else {
                Delivery::Ambient
            };
        }
        // 候補固有の材料を添えた後、共通runtimeで現在観測と履歴を一度更新する。
        let s = &d.sessions[sid];
        if let Some(event) = s.environment_latest.as_ref() {
            let event = super::body_runtime::event(s, event);
            for action in &mut actions {
                crate::environment::reaction::attach(action, &event, &self.config.combat);
                if !crate::environment::danger::player::player_origin(action)
                    && let Some(leaf) = action.leaf.as_mut()
                {
                    leaf.details["__body_context"] =
                        json!(event.body_origin().and_then(|o| o.context.as_ref()));
                }
            }
        }
        if !refresh_dialogue_context(s, &mut actions) {
            return;
        }
        if urgent {
            let s = &d.sessions[sid];
            if s.cancel.is_some()
                && s.deferred_input.is_none()
                && let Some(row) = d.rows.iter().rev().find(|r| r["turn_id"] == s.current_turn)
                && let Some(text) = row["player_input_text"].as_str()
            {
                let input = DeferredInput {
                    text: text.to_owned(),
                    source: row["source"].as_str().unwrap_or("text").to_owned(),
                    previous_turn: Some(s.current_turn.clone()),
                    generation: s.input_generation,
                    instance: s.instance_id.clone(),
                    binding: s
                        .request_classification
                        .binding(
                            s.input_generation,
                            text,
                            row["source"].as_str().unwrap_or("text"),
                        )
                        .cloned()
                        .or_else(|| super::request_runtime::Binding::capture(s)),
                    classification: super::request_runtime::classification(
                        s,
                        text,
                        row["source"].as_str().unwrap_or("text"),
                    )
                    .cloned(),
                };
                d.sessions.get_mut(sid).unwrap().deferred_input = Some(input);
            }
            Self::cancel_haiku(d, sid, "urgent_environment");
            super::reply_output::cancel(d, sid, "urgent_environment", false);
            Self::cancel_chat(d, sid, "urgent_environment");
            let combat = d.sessions[sid]
                .warning
                .as_ref()
                .is_some_and(|w| w.actions.iter().any(|a| a.delivery == Delivery::Combat));
            if !combat {
                Self::cancel_warning(d, sid, "urgent_environment");
            }
        }
        let s = d.sessions.get_mut(sid).unwrap();
        // 不急の環境音声は待ち列を奪わない。待機させるのは現在性を再照合できるurgentだけ。
        if !urgent
            && (s.haiku.foreground()
                || s.cancel.is_some()
                || s.warning.is_some()
                || s.pending_warning.is_some())
        {
            return;
        }
        s.pending_warning = Some(actions);
        s.pending_input = None;
        self.start_pending(d, jobs, sid);
    }
    pub(super) fn process_environment(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        now: u64,
        combat_priority: CombatPriority,
    ) {
        let speaking = super::reply_queue::playback_started(d, sid);
        let s = d.sessions.get_mut(sid).unwrap();
        // 被弾だけのpanicでも、本人の危険実測は扱う。戦闘判定や被弾原因は書き換えず、
        // 実際の敵・先行中の緊急警告・本人への予約済み応答がある間はこの機会を消費しない。
        let occupied = reserved_reply(s)
            || s.warning
                .as_ref()
                .map(|w| w.actions.as_slice())
                .into_iter()
                .chain(s.pending_warning.as_deref())
                .flatten()
                .any(|a| matches!(a.delivery, Delivery::Combat | Delivery::UrgentEnvironment));
        let mut projected = None;
        if !occupied
            && observation_fresh(s)
            && !s.foreground.game_paused
            && s.latest
                .as_ref()
                .is_none_or(|e| !danger::player::hostile_observed(e))
            && s.audio_latest
                .as_ref()
                .is_none_or(|e| !danger::player::hostile_observed(e))
            && let Some(raw) = s.environment_latest.as_ref()
        {
            let event = super::body_runtime::event(s, raw);
            if let Some(action) = s.danger.player_hazard(&event) {
                self.queue_environment(d, jobs, sid, vec![action], true);
                return;
            }
            projected = Some(event);
        }
        let s = d.sessions.get_mut(sid).unwrap();
        if super::workshop_focus::active(s) {
            // update済みの現在観測は保持し、発話候補やCDは消費しない。
            Self::cancel_light(d, sid);
            return;
        }
        // この区間ではdata lockを保持し、危険状態の更新も身体の受理台帳を変更しない。
        // 危険確認で既に投影していれば共有し、workshop等の早期終了では余分に構築しない。
        let Some(event) = projected.or_else(|| {
            s.environment_latest.as_ref().map(|raw| super::body_runtime::event(s, raw))
        }) else {
            return;
        };
        let (boss, ominous) = s.combat.environmental_presence(now, &self.config.combat);
        s.danger.set_presence(boss, ominous);
        let focus = focus(s, &event, now, &self.config.combat);
        if combat_priority != CombatPriority::None {
            let stop = if combat_priority == CombatPriority::FrontAmbush {
                s.danger.interrupt_for_threat(&event, &self.config.combat)
            } else {
                s.danger.combat_recovery(&event, now, &self.config.combat)
            };
            self.queue_environment(d, jobs, sid, stop, false);
            Self::cancel_light(d, sid);
            return;
        }
        // 受理済みの戦闘質問・操作応答は雷などの候補で上書きしない。
        // 候補を評価する前に戻り、まだ配送していない雷のCDも消費しない。
        if reserved_reply(s) {
            return;
        }
        let mut danger = s.danger.clone();
        // 川柳の生成と開始済みの発話を夕方警告で取り消さない。既存focus判定は
        // 雷・危険な熱源の後で働き、未発話の夕方警告は現在観測で後から再評価する。
        let urgent = danger.urgent(
            &event,
            now,
            s.mode,
            s.haiku.foreground() || speaking,
            &self.config.combat,
        );
        // Do not cut off an already selected thunder/evening warning on the
        // next snapshot, or consume the heat cooldown before it can be queued.
        if urgent.iter().any(|a| a.kind == "damaging_light")
            && s.warning
                .as_ref()
                .map(|w| w.actions.as_slice())
                .into_iter()
                .chain(s.pending_warning.as_deref())
                .flatten()
                .any(|a| a.delivery == Delivery::UrgentEnvironment)
        {
            return;
        }
        s.danger = danger;
        if !urgent.is_empty() {
            self.queue_environment(d, jobs, sid, urgent, true);
            return;
        }
        let s = d.sessions.get_mut(sid).unwrap();
        let busy = s.haiku.foreground()
            || !s.reply_queue.is_empty()
            || s.cancel.is_some()
            || s.warning.is_some()
            || s.pending_warning.is_some()
            || s.assist_pending.is_some()
            || jobs.len() >= 16
            || !crate::combat::model::elapsed(
                now,
                s.last_player_input,
                self.config.combat.ms("player_input_priority_cooldown_ms"),
            );
        if let Some(speech) =
            s.ambient
                .priority_smell(&event, now, s.mode, busy, &self.config.combat)
        {
            self.queue_environment(d, jobs, sid, vec![speech], false);
            return;
        }
        // 警告とambientを複製上で評価し、優先順を比較して採用した状態だけを本体へ反映する。
        // 落選した反応のクールダウン・入口フラグを消費しないので、次の安全な機会に話せる。
        let mut danger = s.danger.clone();
        let mut ambient = s.ambient.clone();
        let danger_actions = danger.actions(&event, now, s.mode, busy, false, &self.config.combat);
        let ambient_actions =
            ambient.actions(&event, now, s.mode, busy, &focus, &self.config.combat);
        let plan = ambient.take_light_plan();
        let danger_rank = priority(&danger_actions);
        let ambient_rank = if plan.is_some() {
            4
        } else {
            priority(&ambient_actions)
        };
        let (mut actions, request) = if danger_rank <= ambient_rank {
            s.danger = danger;
            if ambient_actions.is_empty() && plan.is_none() {
                s.ambient = ambient;
            }
            (danger_actions, None)
        } else {
            s.ambient = ambient;
            if danger_actions.is_empty() {
                s.danger = danger;
            }
            (ambient_actions, plan)
        };
        if actions.is_empty()
            && request.is_none()
            && !busy
            && self.config.llm_enabled
            && self.config.chat_image.stop_reactions_enabled
            && let Some(action) = super::visual_runtime::opportunity(s)
        {
            actions.push(action);
        }
        if actions.is_empty()
            && request.is_none()
            && !busy
            && self.config.llm_enabled
            && let Some(action) = super::body_interest_runtime::opportunity(s, now)
        {
            actions.push(action);
        }
        self.queue_environment(d, jobs, sid, actions, false);
        if let Some(request) = request {
            self.start_light_plan(d, jobs, sid, request);
        }
    }
    fn start_light_plan(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        mut request: LightPlanRequest,
    ) {
        request.details["context"]["recent"] = json!(d.sessions[sid].model_history());
        Self::cancel_light(d, sid);
        if jobs.len() >= 16 {
            return;
        }
        let (cancel, rx) = watch::channel(false);
        d.sessions.get_mut(sid).unwrap().light_cancel = Some(cancel);
        let this = self.clone();
        let sid = sid.to_owned();
        jobs.push(tokio::spawn(async move {
            this.run_light_plan(sid, request, rx).await;
        }));
    }
    async fn run_light_plan(
        self: Arc<Self>,
        sid: String,
        request: LightPlanRequest,
        mut cancel: watch::Receiver<bool>,
    ) {
        let input = json!({"op":"light_plan","model":self.config.model,"details":request.details,"fallback_payload":request.fallback_payload});
        let result = bridge::render(&self.config, &self.reading_dictionary, &self.llm, input, &mut cancel).await;
        let mut jobs = self.jobs.lock().unwrap();
        jobs.retain(|j| !j.is_finished());
        let mut d = self.data.lock().unwrap();
        if d.stopped || *cancel.borrow() {
            return;
        }
        let Some(s) = d.sessions.get_mut(&sid) else {
            return;
        };
        s.light_cancel = None;
        let now = self.clock.elapsed().as_millis() as u64;
        // モデル待機中に変わった観測をここで受理し、このlock内の照明判断とfocusで共有する。
        let Some(event) = s
            .environment_latest
            .as_ref()
            .filter(|_| observation_fresh(s))
        else {
            return;
        };
        let event = super::body_runtime::event(s, event);
        let focus = focus(s, &event, now, &self.config.combat);
        if s.haiku.foreground()
            || s.cancel.is_some()
            || s.warning.is_some()
            || s.pending_warning.is_some()
            || s.assist_pending.is_some()
        {
            return;
        }
        let result = result.unwrap_or_else(|_| json!({"payload":request.fallback_payload}));
        let payload = &result["payload"];
        if let Some(mut action) = s.ambient.resolve_light_plan(
            request.request_id,
            payload,
            &event,
            now,
            &focus,
            &self.config.combat,
        ) {
            if let Some(comment) = action.light_comment.as_mut() {
                comment.reports = result["llm_reports"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default();
            }
            self.queue_environment(&mut d, &mut jobs, &sid, vec![action], false);
        }
    }
}

fn priority(actions: &[Speech]) -> u8 {
    actions
        .iter()
        .map(|a| {
            // 雨上がりは一瞬の非敵対な気づき。通常のsmell（ゾンビ臭）の優先順位を借りない。
            if a.rain_after_window.is_some() {
                return 22;
            }
            match a.kind {
                "emergency_shelter_morning" | "emergency_shelter_relief" => 3,
                "submerged_darkness" => 5,
                "occluded_entry_with_light" | "occluded_entry_no_light" => 6,
                "dark_push_no_light" => 7,
                "dark_push_stop" | "dark_push_after_breath" | "dark_push_breath" => 8,
                "night_warning_surface" | "night_warning_cave" => 9,
                "thunder_reaction" => 10,
                "smell" => 11,
                "ender_eye_throw" => 12,
                "portal_appearance" => 13,
                "magma_block" => 15,
                "damaging_light" => 16,
                "emergency_shelter_presence" | "emergency_shelter_advice" => 17,
                "end_portal_frame" => 20,
                "surroundings_notice" => 22,
                "darkness_escape" | "darkness_advice" => 23,
                _ => 24,
            }
        })
        .min()
        .unwrap_or(u8::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dialogue::{DialogueConfig, empty_event};
    use std::time::Instant;

    #[test]
    fn one_refresh_shares_current_history_and_next_refresh_reads_new_delivery() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("shared-history", "試験", false);
        let mut data = dialogue.data.lock().unwrap();
        let s = data.sessions.get_mut("shared-history").unwrap();
        let raw = GameEvent::parse(empty_event("試験")).unwrap();
        let event = super::super::body_runtime::install_test_senses(s, &raw, 1);
        s.history.push("first", "user", "雨かな");
        s.history.silent("first");
        let mut thunder = Speech::new("thunder_reaction", "雷や");
        thunder.delivery = Delivery::Ambient;
        crate::environment::reaction::attach(&mut thunder, &event, &dialogue.config.combat);
        let mut portal = Speech::new("portal_appearance", "入口や");
        portal.leaf = Some(crate::combat::model::LeafRequest {
            kind: "portal_appearance".into(), temperature: 0.65, details: json!({}),
        });
        let mut actions = [thunder, portal, Speech::new("fixed", "気をつけて")];
        assert!(refresh_dialogue_context(s, &mut actions));
        let expected = json!({"conversation":s.model_history()});
        assert_eq!(expected["conversation"][1]["reaction"], "silent");
        assert_eq!(actions[0].leaf.as_ref().unwrap().details["reaction_context"]["recent"], expected);
        assert_eq!(actions[1].leaf.as_ref().unwrap().details["conversation_context"]["recent"], expected);
        assert!(actions[2].leaf.is_none());
        actions[0].leaf.as_mut().unwrap().details["reaction_context"]["recent"]["conversation"] = json!([]);
        assert_eq!(actions[1].leaf.as_ref().unwrap().details["conversation_context"]["recent"], expected);
        s.history.push("second", "user", "聞こえたね");
        s.history.push("second", "assistant", "雷の音やな");
        assert!(refresh_dialogue_context(s, &mut actions));
        let updated = json!({"conversation":s.model_history()});
        assert_ne!(updated, expected);
        assert_eq!(actions[0].leaf.as_ref().unwrap().details["reaction_context"]["recent"], updated);
        assert_eq!(actions[1].leaf.as_ref().unwrap().details["conversation_context"]["recent"], updated);
    }

    #[test]
    fn focus_uses_same_body_projection_without_losing_player_danger_or_next_update() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("shared-focus", "試験", false);
        let mut data = dialogue.data.lock().unwrap();
        let s = data.sessions.get_mut("shared-focus").unwrap();
        s.player_uuid = Some("11111111-1111-4111-8111-111111111111".into());
        for (tick, body_underwater) in [(1, false), (2, true)] {
            let mut wire = empty_event("試験");
            wire["world"] = json!({"is_submerged":true,"air_supply":20,"local_light":15});
            wire["player"]["environment"] = json!({"submerged_in_water":true});
            let mut wire = json!(crate::body::tests::with_senses(wire, tick));
            wire["body_observation"]["sample"]["senses"]["world"]["is_submerged"] = json!(body_underwater);
            let raw = GameEvent::parse(wire).unwrap();
            super::super::body_runtime::observe(s, &raw);
            s.environment_latest = Some(raw.clone());
            let event = super::super::body_runtime::event(s, &raw);
            assert!(event.body_origin().unwrap().available);
            assert!(danger::player::current(&event).contains(&danger::player::Kind::LowAir));
            assert_eq!(focus(s, &event, tick, &dialogue.config.combat).submerged, body_underwater);
        }
        s.body_observation = Default::default();
        let event = super::super::body_runtime::event(s, s.environment_latest.as_ref().unwrap());
        assert!(!event.body_origin().unwrap().available);
        assert!(!focus(s, &event, 3, &dialogue.config.combat).submerged);
    }

    #[tokio::test]
    async fn player_hazard_waits_for_reply_and_preserves_workshop_and_pending_revision() {
        let f = super::super::workshop_record::tests::fixture();
        let mut wire = empty_event("試験");
        wire["player"]["environment"] = json!({"touching_lava":true,"on_fire":true});
        wire["combat"] = json!({"recent_damage_ms":0,"combat_active_hint":true});
        let raw = GameEvent::parse(wire).unwrap();
        let mut jobs = Vec::new();
        // 配送枠の飽和を使い、生成や音声を起動せず本物の待機列を検査する。
        for _ in 0..16 {
            jobs.push(tokio::spawn(std::future::pending::<()>()));
        }
        {
            let mut data = f.d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            let w = s.haiku.workshop.as_mut().unwrap();
            let mut proposed = w.current_lines.clone();
            proposed[0].surface_text = "さくらいろ".into();
            proposed[0].reading_text = "さくらいろ".into();
            proposed[0].provenance = "player_explicit".into();
            w.pending = Some(
                crate::workshop_edit::Pending::stage(&w.current_lines, &w.current_lines, proposed, 0)
                    .unwrap(),
            );
            let expected = (w.current_lines.clone(), w.version, json!(w.pending));
            s.preview = false;
            s.received = Some(Instant::now());
            s.latest = Some(raw.clone());
            s.environment_latest = Some(raw.clone());
            s.mode = crate::combat::model::Mode::Panic;
            let sensed = super::super::body_runtime::event(s, &raw);
            s.danger.update(&sensed, 0, true, &f.d.config.combat);
            let mut reply = Speech::new("combat_query", "先に受けた質問の返答");
            reply.delivery = Delivery::PlayerReply;
            s.pending_warning = Some(vec![reply]);
            f.d.process_environment(&mut data, &mut jobs, "s", 0, CombatPriority::Selected);
            assert_eq!(
                data.sessions["s"].pending_warning.as_ref().unwrap()[0].kind,
                "combat_query"
            );
            data.sessions.get_mut("s").unwrap().pending_warning = None;
            // 同じ実測のまま予約が空けば機会を取得できる。workshopを閉じたり案を採用しない。
            f.d.process_environment(&mut data, &mut jobs, "s", 1, CombatPriority::Selected);
            let s = data.sessions.get_mut("s").unwrap();
            let mut actions = s.pending_warning.clone().unwrap();
            assert_eq!(actions[0].kind, "player_hazard");
            assert_eq!(actions[0].delivery, Delivery::UrgentEnvironment);
            let w = s.haiku.workshop.as_ref().unwrap();
            assert_eq!(
                (w.current_lines.clone(), w.version, json!(w.pending)),
                expected
            );
            assert!(super::super::warnings::applicable(
                &actions[0],
                s,
                &f.d.config.warnings,
                &f.d.config.combat
            ));
            s.foreground.game_paused = true;
            assert!(!super::super::warnings::applicable(
                &actions[0],
                s,
                &f.d.config.warnings,
                &f.d.config.combat
            ));
            s.foreground.game_paused = false;
            s.received = Some(Instant::now() - std::time::Duration::from_secs(60));
            assert!(!super::super::warnings::applicable(
                &actions[0],
                s,
                &f.d.config.warnings,
                &f.d.config.combat
            ));
            s.received = Some(Instant::now());
            let mut current = json!(s.environment_latest.as_ref().unwrap());
            current["player"]["health"] = json!(8);
            s.environment_latest = Some(GameEvent::parse(current.clone()).unwrap());
            assert!(refresh_dialogue_context(s, &mut actions));
            assert_eq!(
                actions[0].leaf.as_ref().unwrap().details["reaction_context"]["observations"]["subject_states"]
                    ["player"]["vitals"]["health"],
                8.0
            );
            current["player"]["environment"]["touching_lava"] = json!(false);
            s.environment_latest = Some(GameEvent::parse(current).unwrap());
            assert!(!refresh_dialogue_context(s, &mut actions));
        }
        for job in jobs {
            job.abort();
            let _ = job.await;
        }
        f.finish().await;
    }

    #[test]
    fn rain_after_uses_ordinary_notice_priority_and_keeps_the_shared_normal_smell() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("rain", "試験", false);
        let mut value = empty_event("試験");
        value["smell_observation"] = json!({"status":"present","basis":"smell_policy_v2",
            "rain_after_active":true,"components":[{"smell_id":"bread","category":"food",
                "valence":"pleasant","source_kind":"hotbar","effective_strength":3}],
            "rain_after_notice":{"window_id":1,"component":{"smell_id":"rain_after",
                "category":"rain_after","valence":"pleasant","source_kind":"block","effective_strength":1}}});
        let event = GameEvent::parse(value).unwrap();
        let mut data = dialogue.data.lock().unwrap();
        let session = data.sessions.get_mut("rain").unwrap();
        session
            .ambient
            .update(&event, 0, true, &dialogue.config.combat);
        session.conversation_observation.observe(crate::conversation_observation::Context {
            observations:json!({"smell":{"status":"present","components":[{"smell_id":"bread"}]}}),
            ..Default::default()
        });
        let mut action = session
            .ambient
            .actions(
                &event,
                1,
                crate::combat::model::Mode::Normal,
                false,
                &AmbientFocus::default(),
                &dialogue.config.combat,
            )
            .remove(0);
        action.delivery = Delivery::Ambient;
        crate::environment::reaction::attach(
            &mut action,
            &event,
            &dialogue.config.combat,
        );
        assert_eq!(
            priority(&[action.clone()]),
            priority(&[crate::environment::notice::action()])
        );
        assert!(
            priority(&[Speech::new("dark_push_no_light", "暗い")]) < priority(&[action.clone()])
        );
        let mut actions = [action];
        assert!(refresh_dialogue_context(session, &mut actions));
        let context = &actions[0].leaf.as_ref().unwrap().details["reaction_context"];
        assert_eq!(
            context["world_context"]["observations"]["smell"]["components"][0]["smell_id"],
            "rain_after"
        );
        assert_eq!(
            session
                .conversation_observation
                .context()
                .unwrap()
                .observations["smell"]["components"][0]["smell_id"],
            "bread"
        );
        assert!(!context.to_string().contains("window_id"));
        assert!(!context.to_string().contains("rain_after_window"));
    }

    #[test]
    fn villager_routine_reaches_chat_and_both_reaction_routes_then_disappears() {
        let d = Dialogue::new(DialogueConfig::default()).unwrap();
        d.register("routine", "試験", false);
        let mut value = empty_event("試験");
        value["player"] = json!({"dimension":"minecraft:overworld"});
        value["world"] = json!({"time_of_day":11000,"sky_visible":false});
        value["passive_mobs"] = json!([{"type":"villager","is_baby":true}]);
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("routine").unwrap();
        for (time, expected) in [(11000, "play"), (12000, "sleep")] {
            value["world"]["time_of_day"] = time.into();
            let event = GameEvent::parse(value.clone()).unwrap();
            let event = super::super::body_runtime::install_test_senses(s, &event, time as u64);
            s.conversation_observation.observe(
                crate::conversation_observation::project(&event, None, 32.0, 7.0, 5000).unwrap(),
            );
            s.conversation_observation
                .sample_notice(time as u64 * 2, 1, true);
            // table差分のある時だけ通知を準備できる。
            let mut changed = s.conversation_observation.context().unwrap();
            changed.observations["structure"] = json!(format!("fixture-{time}"));
            s.conversation_observation.observe(changed);
            s.conversation_observation
                .sample_notice(time as u64 * 2 + 1, 1, true);
            for kind in ["surroundings_notice", "portal_appearance"] {
                let mut action = Speech::new(kind, "気になるな。");
                action.delivery = crate::combat::model::Delivery::Ambient;
                action.leaf = Some(crate::combat::model::LeafRequest {
                    kind: kind.into(),
                    temperature: 0.65,
                    details: json!({"mob":"子供","mob_is_baby":true,"__ambient_guard":{"mob_type":"villager"},"portal_type":"nether_portal","portal_encounter":"arrived","dimension":"minecraft:overworld"}),
                });
                if kind == "surroundings_notice" {
                    crate::environment::reaction::attach(
                        &mut action,
                        &event,
                        &crate::combat::model::Settings::default(),
                    );
                }
                refresh_dialogue_context(s, std::slice::from_mut(&mut action));
                let leaf = action.leaf.unwrap();
                let input = json!({"kind":leaf.kind,"details":leaf.details,"model":"test","max_tokens":72,"temperature":0.65,"fallback_text":action.text});
                let request = if kind == "surroundings_notice" {
                    crate::environment::reaction::Prepared::new(&input, "test")
                        .unwrap()
                        .request
                } else {
                    crate::reaction_leaf::Leaf::prepare(&input, "test", 72)
                        .unwrap()
                        .request
                };
                let body: serde_json::Value =
                    serde_json::from_str(&request.messages[1].content).unwrap();
                assert_eq!(
                    body["villager_routines"]["villagers"][0]["activity"],
                    expected
                );
                assert!(body["world_context"].get("villager_routines").is_none());
                if expected == "sleep" && kind != "surroundings_notice" {
                    assert_eq!(
                        body["world_context"]["changes"][0]["now"]["villagers"][0]["activity"],
                        "sleep"
                    );
                }
            }
            let input = json!({"user_text":"子供たち、元気だね。","world_context":s.conversation_observation.context(),"dialogue_choice":true});
            let original = input.clone();
            let messages = crate::chat_prompt::messages(&input).unwrap();
            assert!(
                messages[1]
                    .content
                    .contains("【観測範囲の村人の日課（時刻からの予定）】")
            );
            assert!(messages[1].content.contains(if expected == "sleep" {
                "休息・睡眠の時間"
            } else {
                "遊びの時間"
            }));
            assert_eq!(input, original);
        }
        value["passive_mobs"] = json!([]);
        let event = GameEvent::parse(value).unwrap();
        s.conversation_observation.observe(
            crate::conversation_observation::project(&event, None, 32.0, 7.0, 5000).unwrap(),
        );
        let messages=crate::chat_prompt::messages(&json!({"user_text":"戻ろう。","world_context":s.conversation_observation.context(),"dialogue_choice":true})).unwrap();
        assert!(!messages[1].content.contains("villager_routines"));
        assert!(!messages[1].content.contains("休息・睡眠の時間"));
    }

    #[test]
    fn authored_catalogue_survives_session_refresh_and_real_model_request_builders() {
        let d = Dialogue::new(DialogueConfig::default()).unwrap();
        d.register("catalogue", "試験", false);
        let mut value = empty_event("試験");
        value["player"] = json!({"dimension":"minecraft:the_nether"});
        value["world"] =
            json!({"nearby_portal_type":"nether_portal","ominous_sound_kind":"sculk_shrieker"});
        value["passive_mobs"] = json!([{"type":"cow"}]);
        let event = GameEvent::parse(value).unwrap();
        let context =
            crate::conversation_observation::project(&event, None, 32.0, 7.0, 5000).unwrap();
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("catalogue").unwrap();
        let event = super::super::body_runtime::install_test_senses(s, &event, 1);
        s.conversation_observation.observe(context);
        s.conversation_observation.sample_notice(0, 1, true);
        let mut changed = s.conversation_observation.context().unwrap();
        changed.observations["structure"] = json!("fixture-changed");
        s.conversation_observation.observe(changed);
        s.conversation_observation.sample_notice(1, 1, true);
        let original = s.conversation_observation.context().unwrap();
        for (kind, details) in [
            (
                "portal_appearance",
                json!({"portal_type":"nether_portal","dimension":"minecraft:the_nether","portal_encounter":"arrived"}),
            ),
            (
                "deep_dark_ominous_sound",
                json!({"ominous_kind":"sculk_shrieker","ominous_stage":2}),
            ),
            (
                "surroundings_notice",
                json!({"mob":"cow","mob_temperament":"passive"}),
            ),
            ("daylight_water", json!({"hostiles":["スケルトン"]})),
        ] {
            let mut action = Speech::new(kind, "気になるな。");
            action.delivery = crate::combat::model::Delivery::Ambient;
            action.leaf = Some(crate::combat::model::LeafRequest {
                kind: if kind == "daylight_water" {
                    "daylight_water_skeleton"
                } else {
                    kind
                }
                .into(),
                details,
                temperature: 0.65,
            });
            if kind == "surroundings_notice" {
                crate::environment::reaction::attach(
                    &mut action,
                    &event,
                    &crate::combat::model::Settings::default(),
                );
            }
            refresh_dialogue_context(s, std::slice::from_mut(&mut action));
            let leaf = action.leaf.unwrap();
            let input = json!({"kind":leaf.kind,"details":leaf.details,"model":"test","temperature":0.65,"max_tokens":72,"fallback_text":action.text});
            let request = if kind == "surroundings_notice" {
                crate::environment::reaction::Prepared::new(&input, "test")
                    .unwrap()
                    .request
            } else {
                crate::reaction_leaf::Leaf::prepare(&input, "test", 72)
                    .unwrap()
                    .request
            };
            let model_context: serde_json::Value =
                serde_json::from_str(&request.messages[1].content).unwrap();
            assert!(
                model_context["world_context"]
                    .get("catalog_knowledge")
                    .is_none()
            );
            let rows = model_context["catalog_knowledge"]["entries"]
                .as_array()
                .unwrap();
            let shrieker = rows.iter().find(|r| r["id"] == "sculk_shrieker").unwrap();
            assert_eq!(
                shrieker["general"]["note"],
                "角のような飾りがついている。スカルクセンサーが反応を受信し、叫び声をあげ、ウォーデンを喚ぶ。"
            );
            assert_eq!(shrieker["basis"], json!(["heard"]));
            assert!(!rows.iter().any(|r| r["id"] == "warden"));
            let cow = rows.iter().find(|r| r["id"] == "cow").unwrap();
            assert_eq!(
                cow["general"]["poetic"],
                crate::chat_catalog::catalog().all_mob_entries()["cow"]["poetic"]
            );
            if kind == "daylight_water" {
                assert_eq!(
                    rows.iter().find(|r| r["id"] == "skeleton").unwrap()["basis"],
                    json!(["reaction_target"])
                );
            }
            if kind == "portal_appearance" {
                assert_eq!(
                    model_context["properties"]["portal_role"],
                    "オーバーワールドへの帰り道（出口）。"
                );
                assert_eq!(
                    rows.iter().find(|r| r["id"] == "nether_portal").unwrap()["general"]["note"],
                    "ネザーへ誘う不思議なポータル。紫色の光が渦巻く"
                );
            }
            assert_eq!(s.conversation_observation.context().unwrap(), original);
        }
        let input = json!({"user_text":"今の音、気になるな。","world_context":original,"dialogue_choice":true});
        let before = input.clone();
        let messages = crate::chat_prompt::messages(&input).unwrap();
        let text = &messages[1].content;
        let (_, knowledge) = text
            .split_once("【対象のカタログ情報（一般的特徴・表現材料）】")
            .unwrap();
        assert!(knowledge.contains("角のような飾りがついている。"));
        assert_eq!(input, before);
    }

    #[test]
    fn partial_fabric_refreshes_crosshair_and_passive_environment_together() {
        let d = Dialogue::new(DialogueConfig::default()).unwrap();
        d.register("s", "試験", false);
        let mut value = empty_event("試験");
        value["adapter"] = "dogido-fabric-client".into();
        value["world"] =
            json!({"weather":"rain","biome":"plains","local_light":15,"sky_visible":true});
        value["player"] = json!({"dimension":"minecraft:overworld","held_item":"minecraft:air",
            "position":{"x":0,"y":64,"z":0},"hotbar":{"selected_slot":0,"slots":[]}});
        value["passive_mobs"] = json!([{"type":"salmon","identity":{"entity_id":"fish"},
            "environment":{"touching_water":true,"on_ground":false}}]);
        value["companion_candidates"] = json!([{"type":"cat", "identity":{
            "entity_id":"cat-1", "custom_name":"クロちゃん", "tamed":true}}]);
        value["look_target"] = json!({"kind":"entity","name":"salmon","identity":{"entity_id":"fish"},
            "environment":{"touching_water":true,"on_ground":false}});
        let full = GameEvent::parse(value.clone()).unwrap();
        let mut data = d.data.lock().unwrap();
        let session = data.sessions.get_mut("s").unwrap();
        session.received = Some(Instant::now());
        session.latest = Some(full.clone());
        session.environment_latest = Some(full.clone());
        session.conversation_observation.observe(
            crate::conversation_observation::project(&full, None, 24.0, 7.0, 10000).unwrap(),
        );
        value["event"]["name"] = "ambient_mob_detected".into();
        value["passive_mobs"][0]["environment"] = json!({"touching_water":false,"on_ground":true});
        value.as_object_mut().unwrap().remove("look_target");
        let partial = GameEvent::parse(value.clone()).unwrap();
        let merged = context(session, &partial, false);
        assert!(merged.look_target.is_none());
        assert_eq!(
            merged.companion_candidates[0]
                .identity
                .as_ref()
                .unwrap()
                .custom_name
                .as_deref(),
            Some("クロちゃん")
        );
        let projected =
            crate::conversation_observation::project(&merged, None, 24.0, 7.0, 10000).unwrap();
        assert_eq!(
            projected.observations["mob_states"][0]["medium"],
            "水に触れず地面にいる"
        );
        session.conversation_observation.observe(projected);
        assert!(
            session
                .conversation_observation
                .context()
                .unwrap()
                .changes
                .iter()
                .any(|c| c.kind == "mob_environment")
        );
        session.environment_latest = Some(merged);
        value["event"]["name"] = "hostile_audio_detected".into();
        value["passive_mobs"] = json!([]);
        value
            .as_object_mut()
            .unwrap()
            .remove("companion_candidates");
        let gone = context(session, &GameEvent::parse(value).unwrap(), false);
        assert!(gone.passive_mobs.is_empty() && gone.look_target.is_none());
        assert!(gone.companion_candidates.is_empty());
        session.conversation_observation.observe(
            crate::conversation_observation::project(&gone, None, 24.0, 7.0, 10000).unwrap(),
        );
        assert!(
            !session
                .conversation_observation
                .context()
                .unwrap()
                .changes
                .iter()
                .any(|c| c.kind == "mob_environment")
        );
    }

    #[test]
    fn fabric_partial_clears_absent_environment_without_clearing_visuals_or_freshness() {
        let d = Dialogue::new(DialogueConfig::default()).unwrap();
        d.register("s", "試験", false);
        let mut value = empty_event("試験");
        value["adapter"] = "dogido-fabric-client".into();
        value["world"] = json!({"weather":"clear","biome":"plains","local_light":15,"sky_visible":true,
            "structure":"village","nearby_portal_type":"nether_portal","nearby_portal_distance":2,
            "nearby_portal_encounter":"appeared","visible_villager_count":10,
            "nearby_end_portal_frame_distance":3,"thunder_sound_recent_ms":0});
        value["player"] = json!({"dimension":"minecraft:overworld","held_item":"minecraft:air",
            "position":{"x":0,"y":64,"z":0},"hotbar":{"selected_slot":0,"slots":[]},
            "vehicle":{"vehicle_id":"minecraft:boat","activity":"riding"}});
        value["visual_threats"] = json!([{"type":"zombie","entity_id":"z","distance":12}]);
        let full = GameEvent::parse(value.clone()).unwrap();
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        s.received = Some(Instant::now());
        let received = s.received;
        s.latest = Some(full.clone());
        s.environment_latest = Some(full);
        value["event"]["name"] = "hostile_audio_detected".into();
        value["event"]["source_kind"] = "auditory".into();
        value["visual_threats"] = json!([]);
        for key in [
            "structure",
            "nearby_portal_type",
            "nearby_portal_distance",
            "nearby_portal_encounter",
            "visible_villager_count",
            "nearby_end_portal_frame_distance",
            "thunder_sound_recent_ms",
        ] {
            value["world"].as_object_mut().unwrap().remove(key);
        }
        value["player"].as_object_mut().unwrap().remove("vehicle");
        let partial = GameEvent::parse(value).unwrap();
        assert!(!crate::dialogue::complete_observation(&partial));
        let merged = context(s, &partial, false);
        assert!(merged.world.structure.is_none() && merged.world.nearby_portal_type.is_none());
        assert!(merged.world.nearby_portal_encounter.is_none());
        assert!(merged.world.visible_villager_count.is_none());
        assert!(
            merged.world.nearby_portal_distance.is_none()
                && merged.world.nearby_end_portal_frame_distance.is_none()
        );
        assert!(merged.player.vehicle.is_none() && merged.world.thunder_sound_recent_ms.is_none());
        assert_eq!(merged.visual_threats[0].entity_id.as_deref(), Some("z"));
        assert_eq!(s.received, received);

        let mut minimal = empty_event("試験");
        minimal["adapter"] = "dogido-fabric-client".into();
        minimal["event"]["name"] = "hostile_audio_detected".into();
        let kept = context(s, &GameEvent::parse(minimal).unwrap(), false);
        assert!(kept.world.structure.is_some() && kept.player.vehicle.is_some());
    }
}

#[cfg(test)]
mod noticing_tests {
    use super::*;
    use crate::conversation_observation::Context;
    #[test]
    fn queued_notice_refreshes_rain_cover_and_subjects_together_but_keeps_offered_revision() {
        let dialogue = Dialogue::new(super::super::DialogueConfig::default()).unwrap();
        dialogue.register("rain-refresh", "試験", false);
        let mut data = dialogue.data.lock().unwrap();
        let s = data.sessions.get_mut("rain-refresh").unwrap();
        let observe = |s: &mut Session, tick: u64, covered: bool| {
            let event = crate::body::tests::with_senses(
                json!({
                    "schema_version":"2026-05-24","adapter":"test","observed_at":chrono::Utc::now(),
                    "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
                    "player":{"dimension":"minecraft:overworld","position":{"y":64},
                        "environment":{"touching_water":false,"touching_rain":false,"touching_water_or_rain":false}},
                    "world":{"biome":"forest","weather":"rain","sky_visible":true,"overhead_cover_type":"none"}
                }),
                tick,
            );
            let mut wire = json!(event);
            let senses = &mut wire["body_observation"]["sample"]["senses"];
            senses["world"]["sky_visible"] = json!(!covered);
            senses["world"]["overhead_cover_type"] = json!(if covered { "foliage" } else { "none" });
            senses["environment"] = json!({"touching_water":false,"touching_rain":!covered,
                "submerged_in_water":false,"touching_water_or_rain":!covered});
            let raw = GameEvent::parse(wire).unwrap();
            s.player_uuid = Some("11111111-1111-4111-8111-111111111111".into());
            super::super::body_runtime::observe(s, &raw);
            s.latest = Some(raw.clone());
            s.environment_latest = Some(raw.clone());
            let event = super::super::body_runtime::event(s, &raw);
            s.conversation_observation.observe(
                crate::conversation_observation::project(&event, None, 32., 7., 5000).unwrap(),
            );
        };
        observe(s, 1, false);
        s.conversation_observation.sample_notice(0, 180000, true);
        observe(s, 2, true);
        s.conversation_observation
            .sample_notice(180000, 180000, true);
        let mut actions = vec![crate::environment::notice::action()];
        assert!(refresh_dialogue_context(s, &mut actions));
        let offered =
            actions[0].leaf.as_ref().unwrap().details["reaction_context"]["world_context"].clone();
        assert_eq!(
            offered["observations"]["subject_states"]["dogido"]["contact"]["touching_rain"],
            false
        );
        s.conversation_observation.finish_notice_opportunity();
        observe(s, 3, false);
        let latest_revision = s.conversation_observation.context().unwrap().revision;
        assert!(latest_revision > offered["revision"].as_u64().unwrap());
        assert!(refresh_dialogue_context(s, &mut actions));
        let input = &actions[0].leaf.as_ref().unwrap().details["reaction_context"]["world_context"];
        assert_eq!(input["revision"], offered["revision"]);
        assert_eq!(input["changes"], offered["changes"]);
        assert_eq!(input["observations"]["overhead_cover"], "none");
        assert_eq!(
            input["observations"]["weather"]["precipitation_kind"],
            "rain"
        );
        assert_eq!(
            input["observations"]["subject_states"]["dogido"]["contact"]["touching_rain"],
            true
        );
        s.conversation_observation
            .considered(offered["revision"].as_u64().unwrap());
        assert!(
            s.conversation_observation
                .context()
                .unwrap()
                .changes
                .iter()
                .any(|c| c.revision > offered["revision"].as_u64().unwrap())
        );
        for paused in [false, true] {
            s.foreground.game_paused = paused;
            if !paused {
                s.body_observation = Default::default();
            }
            assert!(refresh_dialogue_context(s, &mut actions));
            let input = &actions[0].leaf.as_ref().unwrap().details["reaction_context"]["world_context"];
            assert_eq!(input["observations"]["sensory_status"], "unavailable");
            assert!(input["observations"].get("subject_states").is_none());
            assert!(input["observations"].get("overhead_cover").is_none());
            assert_eq!(input["revision"], offered["revision"]);
        }
    }

    #[test]
    fn queued_notice_keeps_periodic_table_after_opportunity_and_movement() {
        let dialogue = Dialogue::new(super::super::DialogueConfig::default()).unwrap();
        dialogue.register("notice", "試験", false);
        let mut data = dialogue.data.lock().unwrap();
        let s = data.sessions.get_mut("notice").unwrap();
        let frame = |mobs| Context {
            observations: json!({"dimension":"overworld","nearby_mob_types":mobs}),
            ..Default::default()
        };
        s.conversation_observation.observe(frame(json!([])));
        s.conversation_observation.sample_notice(0, 180000, true);
        s.conversation_observation.observe(frame(json!(["cow"])));
        s.conversation_observation
            .sample_notice(180000, 180000, true);
        let mut actions = vec![crate::environment::notice::action()];
        assert!(refresh_dialogue_context(s, &mut actions));
        let offered =
            actions[0].leaf.as_ref().unwrap().details["reaction_context"]["world_context"].clone();
        s.conversation_observation.finish_notice_opportunity();
        s.conversation_observation.observe(frame(json!(["sheep"])));
        assert!(refresh_dialogue_context(s, &mut actions));
        assert_eq!(
            actions[0].leaf.as_ref().unwrap().details["reaction_context"]["world_context"],
            offered
        );
        assert!(!refresh_dialogue_context(
            s,
            &mut [crate::environment::notice::action()]
        ));
    }
}
