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
    }
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

pub(super) fn focus(
    s: &Session,
    now: u64,
    settings: &crate::combat::model::Settings,
) -> AmbientFocus {
    let (boss, ominous) = s.combat.environmental_presence(now, settings);
    let e = s
        .environment_latest
        .as_ref()
        .or(s.latest.as_ref())
        .expect("environment observation");
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

/// Reuse one factual/history context when a queued decision is about to run.
pub(super) fn refresh_dialogue_context(s: &Session, actions: &mut [Speech]) {
    for action in actions {
        if !crate::reaction_leaf::is_model_reaction(action) {
            continue;
        }
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
        let context = s
            .conversation_observation
            .context()
            .unwrap_or_default()
            .for_reaction(kind, &leaf.details);
        leaf.details[key]["world_context"] = json!(context);
        leaf.details[key]["recent"] = json!({"conversation":s.history.rows()});
    }
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
            s.deferred_input
                .take()
                .filter(|_| super::fresh(s))
                .map(|v| (v, s.input_generation))
        };
        if let Some((input, generation)) = input {
            self.submit_inner(
                Some(sid),
                &input.text,
                &input.source,
                true,
                Some((generation, input.previous_turn)),
                false,
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
        if super::workshop_focus::active(&d.sessions[sid]) {
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
        // Player replies and environmental opportunities share the same history
        // (including chosen silence) and the same code-owned world projection.
        let s = &d.sessions[sid];
        let recent = json!({"conversation":s.history.rows()});
        if let Some(event) = s.environment_latest.as_ref() {
            for action in &mut actions {
                crate::environment::reaction::attach(action, event, &recent, &self.config.combat);
            }
        }
        refresh_dialogue_context(s, &mut actions);
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
                };
                d.sessions.get_mut(sid).unwrap().deferred_input = Some(input);
            }
            Self::cancel_haiku(d, sid, "urgent_environment");
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
        notification: &GameEvent,
        now: u64,
        combat_priority: CombatPriority,
    ) {
        let s = d.sessions.get_mut(sid).unwrap();
        if super::workshop_focus::active(s) {
            // update済みの現在観測は保持し、発話候補やCDは消費しない。
            Self::cancel_light(d, sid);
            return;
        }
        let Some(event) = s.environment_latest.clone() else {
            return;
        };
        let (boss, ominous) = s.combat.environmental_presence(now, &self.config.combat);
        s.danger.set_presence(boss, ominous);
        if danger::heard_thunder(notification, &self.config.combat) {
            s.ambient.clear_weather_transition();
        }
        let focus = focus(s, now, &self.config.combat);
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
        let urgent = danger.urgent(&event, now, s.mode, false, &self.config.combat);
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
            || !s.knowledge_queue.is_empty()
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
        // 落選候補のクールダウンや入口フラグを消費せず、Pythonの反応順を保つ。
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
        let (actions, request) = if danger_rank <= ambient_rank {
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
        request: LightPlanRequest,
    ) {
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
        let result = bridge::render(&self.config, &self.llm, input, &mut cancel).await;
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
        let Some(event) = s
            .environment_latest
            .clone()
            .filter(|_| observation_fresh(s))
        else {
            return;
        };
        let focus = focus(s, now, &self.config.combat);
        if s.haiku.foreground()
            || s.cancel.is_some()
            || s.warning.is_some()
            || s.pending_warning.is_some()
            || s.assist_pending.is_some()
        {
            return;
        }
        let payload = result
            .map(|r| r["payload"].clone())
            .unwrap_or(request.fallback_payload);
        if let Some(action) = s.ambient.resolve_light_plan(
            request.request_id,
            &payload,
            &event,
            now,
            &focus,
            &self.config.combat,
        ) {
            self.queue_environment(&mut d, &mut jobs, &sid, vec![action], false);
        }
    }
}

fn priority(actions: &[Speech]) -> u8 {
    actions
        .iter()
        .map(|a| match a.kind {
            "emergency_shelter_morning" | "emergency_shelter_relief" => 3,
            "submerged_darkness" => 5,
            "occluded_entry_with_light" | "occluded_entry_no_light" => 6,
            "dark_push_no_light" => 7,
            "dark_push_stop" | "dark_push_after_breath" | "dark_push_breath" => 8,
            "night_warning_surface" | "night_warning_cave" => 9,
            "weather_transition" | "thunder_reaction" => 10,
            "smell" => 11,
            "ender_eye_throw" => 12,
            "portal_appearance" => 13,
            "firefly" | "firefly_cue" => 14,
            "magma_block" => 15,
            "damaging_light" => 16,
            "emergency_shelter_presence" | "emergency_shelter_advice" => 17,
            "foliage_shade" => 18,
            "structure_entry" => 19,
            "end_portal_frame" => 20,
            "special_biome_entry" => 21,
            "ambient" => 22,
            "darkness_escape" | "darkness_advice" => 23,
            _ => 24,
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
            s.conversation_observation.observe(
                crate::conversation_observation::project(&event, None, 32.0, 7.0, 5000).unwrap(),
            );
            for kind in ["ambient", "portal_appearance"] {
                let mut action = Speech::new(kind, "気になるな。");
                action.delivery = crate::combat::model::Delivery::Ambient;
                action.leaf = Some(crate::combat::model::LeafRequest {
                    kind: kind.into(),
                    temperature: 0.65,
                    details: json!({"mob":"子供","mob_is_baby":true,"__ambient_guard":{"mob_type":"villager"},"portal_type":"nether_portal","portal_encounter":"arrived","dimension":"minecraft:overworld"}),
                });
                if kind == "ambient" {
                    crate::environment::reaction::attach(
                        &mut action,
                        &event,
                        &json!({}),
                        &crate::combat::model::Settings::default(),
                    );
                }
                refresh_dialogue_context(s, std::slice::from_mut(&mut action));
                let leaf = action.leaf.unwrap();
                let input = json!({"kind":leaf.kind,"details":leaf.details,"model":"test","max_tokens":72,"temperature":0.65,"fallback_text":action.text});
                let request = if kind == "ambient" {
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
                if expected == "sleep" {
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
        s.conversation_observation.observe(context);
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
            ("ambient", json!({"mob":"cow","mob_temperament":"passive"})),
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
            if kind == "ambient" {
                crate::environment::reaction::attach(
                    &mut action,
                    &event,
                    &json!({}),
                    &crate::combat::model::Settings::default(),
                );
            }
            refresh_dialogue_context(s, std::slice::from_mut(&mut action));
            let leaf = action.leaf.unwrap();
            let input = json!({"kind":leaf.kind,"details":leaf.details,"model":"test","temperature":0.65,"max_tokens":72,"fallback_text":action.text});
            let request = if kind == "ambient" {
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
        let gone = context(session, &GameEvent::parse(value).unwrap(), false);
        assert!(gone.passive_mobs.is_empty() && gone.look_target.is_none());
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
