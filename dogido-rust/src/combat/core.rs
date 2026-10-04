//! ゲーム観測と単調時計の経過ミリ秒から、戦況mode・優先発話・会話可否を決める同期Engine。
//! 観測文脈の統合 → 被弾/音/戦闘結果の更新 → mode解決 → 優先発話選択、の順でDecisionを返す。
//! 呼び手がDecisionを配送し、実再生の成否を扱う。ここではネットワーク・音声I/Oを実行しない。
//!
//! Fabricの音・ambient通知は視認一覧を省きvisual_threats=[]を送ることがある。
//! completeは呼び手がイベント種別の送信仕様から決めるため、配列の空き具合とは別の情報。
//! 完全観測なら視認一式を置換し、部分通知なら10秒以内の直近完全観測へ音等を重ねる。
//! 撃破・爆散はoutcomesが明示の戦闘結果を照合し、視認の消失だけを撃破へ昇格させない。
use super::{
    auditory::{self, Auditory},
    catalog,
    model::{Mode, Scope, Settings, Speech, elapsed},
    outcomes::Outcomes,
    queries,
    specials::Specials,
};
use crate::{
    events::{EventName, GameEvent, HorizontalDirection as H},
    threats::{self, Policy, Warning},
};
use serde::Serialize;

/// 一回の判断で選んだ状態と配送要求。actionsは再生予定であり、実際に聞こえた証拠ではない。
#[derive(Clone, Debug, Serialize)]
pub struct Decision {
    pub mode: Mode,
    pub actions: Vec<Speech>,
    pub stop_audio: bool,
    pub chat_allowed: bool,
    pub dimension_changed: bool,
    pub input_handled: bool,
}
/// 一接続の脅威・被弾・警告間隔を保持する。完全観測と部分通知を分け、情報欠落を安全と誤認しない。
pub struct Engine {
    pub mode: Mode,
    pub shut_up_count: u32,
    policy: Policy,
    auditory: Auditory,
    specials: Specials,
    outcomes: Outcomes,
    /// 部分通知へ補う最後の完全観測。observe末尾でcompleteのときだけ更新する。
    latest_full: Option<GameEvent>,
    latest_full_at: Option<u64>,
    /// 部分通知で危険が増えたことを保持し、次の完全観測まで通常会話の早すぎる再開を止める。
    partial_danger: bool,
    last_damage: Option<(u64, u64)>,
    last_audio: Option<u64>,
    aftermath_until: Option<u64>,
    pending_safe_at: Option<u64>,
    previous_safe: bool,
    low_health_armed: bool,
    stalled_ids: Vec<String>,
    stalled_since: Option<u64>,
    stalled_spoken: Option<u64>,
    last_named: Option<(String, u64)>,
    dark_push_active: bool,
    dark_push_audio_active: bool,
    configured: bool,
}
impl Default for Engine {
    fn default() -> Self {
        Self {
            mode: Mode::Normal,
            shut_up_count: 0,
            policy: Policy::default(),
            auditory: Auditory::default(),
            specials: Specials::default(),
            outcomes: Outcomes::default(),
            latest_full: None,
            latest_full_at: None,
            partial_danger: false,
            last_damage: None,
            last_audio: None,
            aftermath_until: None,
            pending_safe_at: None,
            previous_safe: false,
            low_health_armed: true,
            stalled_ids: vec![],
            stalled_since: None,
            stalled_spoken: None,
            last_named: None,
            dark_push_active: false,
            dark_push_audio_active: false,
            configured: false,
        }
    }
}
impl Engine {
    pub fn environmental_presence(&self, now: u64, settings: &Settings) -> (bool, bool) {
        (
            self.specials.boss_presence(now, settings),
            self.specials.ominous_presence(now, settings),
        )
    }
    pub fn set_dark_push_active(&mut self, active: bool) {
        self.set_dark_push_context(active, active);
    }
    pub fn set_dark_push_context(&mut self, context: bool, audio_active: bool) {
        self.dark_push_active = context;
        self.dark_push_audio_active = audio_active;
    }
    pub fn take_notes(&mut self) -> Vec<String> {
        self.outcomes.take_notes()
    }
    /// 今回の明示戦闘結果から得た対象名の更新を取り出す。次のobserve前に一度だけ消費する。
    pub fn take_name_updates(&mut self) -> crate::chat_observation::NameOutcomeUpdate {
        self.outcomes.take_name_updates()
    }
    pub fn answer_query(
        &self,
        event: &GameEvent,
        text: &str,
        now: u64,
        s: &Settings,
    ) -> Option<Speech> {
        queries::answer(
            event,
            text,
            s,
            self.last_named
                .as_ref()
                .map(|(kind, at)| (kind.as_str(), *at)),
            now,
        )
    }
    /// 受信済みの観測と呼び手の時計から、優先警告・会話可否・音声停止を決める。
    /// completeは全視認を含むかを示す。falseなら直近の視認を補い、観測省略を敵の不在と誤認しない。
    #[allow(clippy::too_many_arguments)]
    pub fn observe(
        &mut self,
        event: &GameEvent,
        now: u64,
        complete: bool,
        busy: bool,
        s: &Settings,
        ws: &threats::Settings,
    ) -> Decision {
        let previous = self.mode;
        if !self.configured {
            self.outcomes = Outcomes::with_settings(s);
            self.configured = true;
        }
        let dimension_changed = self.specials.observe(event, now, complete, s);
        if dimension_changed {
            self.policy = Policy::default();
            self.auditory = Auditory::default();
            self.outcomes.reset_for_dimension(s);
            self.latest_full = None;
            self.latest_full_at = None;
            self.partial_danger = false;
            self.last_damage = None;
            self.last_audio = None;
            self.pending_safe_at = None;
            self.previous_safe = false;
            self.low_health_armed = true;
            self.stalled_ids.clear();
            self.stalled_since = None;
            self.stalled_spoken = None;
            self.last_named = None;
            self.dark_push_active = false;
            self.dark_push_audio_active = false;
        }
        let e = self.context(event, now, complete);
        let prior_audio = self.last_audio;
        if !auditory::unseen(&e).is_empty() {
            self.last_audio = Some(now);
        }
        self.auditory.observe(&e, now, busy, s);
        self.policy.begin_frame(now, ws);
        if let Some(age) = event.combat.recent_damage_ms {
            self.last_damage = Some((now, age as u64));
        }
        // 回復を実測したとき、または完全観測で体力が未提供に戻ったときに低体力警告を再待機にする。
        // 部分通知で体力が省かれただけでは再待機にせず、同じ低体力への警告連発を防ぐ。
        if event
            .player
            .health
            .is_some_and(|h| h > s.number("low_health_warning_threshold"))
            || complete && event.player.health.is_none()
        {
            self.low_health_armed = true;
        }
        let quiet = event.meta.user_text.as_deref().is_some_and(wants_quiet);
        if quiet {
            self.shut_up_count = self.shut_up_count.saturating_add(1);
        }
        if complete {
            self.partial_danger = false;
            self.update_stalled(&e, now);
        } else {
            self.partial_danger |= !event.visual_threats.is_empty()
                || !event.auditory_threats.is_empty()
                || event.combat.combat_active_hint == Some(true)
                || event
                    .combat
                    .recent_damage_ms
                    .is_some_and(|n| n <= s.ms("recent_damage_window_ms") as i64)
                || dark_alert(event, s);
        }
        // 撃破・爆散など今回の明示結果を先に処理し、通常の危険度警告より先に配送候補にする。
        let immediate = self.outcomes.observe(event, now);
        if complete && event.event.name == EventName::CombatEnded {
            self.pending_safe_at = self.outcomes.boss_defeat_confirmed(event).then_some(now);
        }
        if self
            .pending_safe_at
            .is_some_and(|at| now.saturating_sub(at) > s.ms("pending_safe_aftermath_window_ms"))
        {
            self.pending_safe_at = None;
        }
        let safe = safe_zone(&e);
        let pending = self.pending_safe_at.is_some()
            && e.visual_threats.is_empty()
            && (self.outcomes.has_boss_context() && self.outcomes.boss_defeat_confirmed(&e)
                || safe && !self.previous_safe && e.auditory_threats.is_empty());
        let clear = complete
            && Outcomes::clear_confirmed(event)
            && self.outcomes.boss_defeat_confirmed(event);
        let died = complete && event.event.name == EventName::PlayerDied;
        self.mode = if died || clear || pending {
            Mode::Aftermath
        } else {
            self.resolve_mode(&e, now, s)
        };
        if self.mode == Mode::Aftermath && previous != Mode::Aftermath {
            self.aftermath_until = Some(now.saturating_add(s.ms("aftermath_time_ms")));
            self.pending_safe_at = None;
        }
        if previous == Mode::Aftermath && self.mode == Mode::Normal {
            self.shut_up_count = 0;
        }
        let explicit = matches!(
            event.event.name,
            EventName::PlayerDied
                | EventName::HostileDefeated
                | EventName::CreeperDetonated
                | EventName::CombatEnded
        );
        let mut d = Decision {
            mode: self.mode,
            actions: vec![],
            stop_audio: false,
            chat_allowed: !matches!(self.mode, Mode::Panic | Mode::SuppressedPanic)
                && !self.partial_danger
                && !explicit
                && (complete
                    || self
                        .latest_full_at
                        .is_some_and(|at| now.saturating_sub(at) <= 10_000)),
            dimension_changed,
            input_handled: quiet,
        };
        if dimension_changed {
            d.stop_audio = true;
            d.chat_allowed = false;
        } else if let Some(speech) = immediate {
            d.actions.push(speech);
            d.stop_audio = true;
            d.chat_allowed = false;
        } else if self.mode == Mode::Aftermath
            && (clear || pending || previous != Mode::Aftermath)
            && !died
        {
            if let Some(speech) = self.outcomes.aftermath(&e, now) {
                d.actions.push(speech);
                d.stop_audio = clear;
            }
        } else if self.mode != Mode::Aftermath
            && (!explicit || matches!(event.event.name, EventName::CombatEnded))
        {
            let text = event.meta.user_text.as_deref().filter(|_| !quiet);
            for speech in self.select(
                &e,
                now,
                complete,
                busy,
                previous,
                prior_audio,
                text,
                s,
                ws,
                &mut d.input_handled,
            ) {
                self.deliver(&e, now, speech, &mut d);
            }
        }
        if self.dark_push_audio_active && blocks_environment(&e) {
            d.stop_audio = true;
            self.dark_push_audio_active = false;
        }
        if complete {
            self.policy.finish_frame(&e, now, ws);
            self.latest_full = Some(event.clone());
            self.latest_full_at = Some(now);
            self.previous_safe = safe;
        }
        d
    }
    /// 入力処理は観測回数・視認edgeを進めない。Noneなら通常会話の経路へ戻す。
    pub fn input(
        &mut self,
        event: &GameEvent,
        text: &str,
        now: u64,
        s: &Settings,
        ws: &threats::Settings,
    ) -> Option<Decision> {
        let previous = self.mode;
        let quiet = wants_quiet(text);
        let query = if quiet {
            None
        } else {
            queries::answer(
                event,
                text,
                s,
                self.last_named.as_ref().map(|(k, t)| (k.as_str(), *t)),
                now,
            )
        };
        if !quiet && query.is_none() {
            return None;
        }
        if quiet {
            self.shut_up_count = self.shut_up_count.saturating_add(1);
            self.mode = self.resolve_mode(event, now, s);
        }
        let mut d = Decision {
            mode: self.mode,
            actions: vec![],
            stop_audio: false,
            chat_allowed: false,
            dimension_changed: false,
            input_handled: true,
        };
        if let Some(speech) = query {
            self.deliver(event, now, speech, &mut d);
        } else if self.mode == Mode::SuppressedPanic && self.policy.cue_allowed(event, now, ws) {
            let (id, text) = if previous == Mode::SuppressedPanic {
                ("suppressed_breath", "ハァハァ……")
            } else {
                ("suppressed_gasp", "ひいっ！")
            };
            let mut speech = Speech::new("suppressed_panic", text);
            speech.cue_id = Some(id);
            self.deliver(event, now, speech, &mut d);
        }
        Some(d)
    }
    /// 新しい完全観測が届くまで、部分通知へ直近の視認・不足文脈を補う。
    /// 古い体力や「直前に聞こえた音」の経過時間は持ち越さず、10秒を越えた全体観測も使わない。
    fn context(&self, event: &GameEvent, now: u64, complete: bool) -> GameEvent {
        if complete
            || self
                .latest_full_at
                .is_none_or(|at| now.saturating_sub(at) > 10_000)
        {
            return event.clone();
        }
        let Some(full) = &self.latest_full else {
            return event.clone();
        };
        let mut v = serde_json::to_value(event).expect("serializable event");
        v["visual_threats"] = serde_json::to_value(&full.visual_threats).unwrap();
        for name in [
            "hostiles_within_7",
            "hostiles_within_10",
            "hostiles_within_scan_ground",
            "hostile_scan_distance",
        ] {
            v["combat"][name] = serde_json::to_value(&full.combat).unwrap()[name].clone();
        }
        for section in ["world", "player"] {
            let prior = serde_json::to_value(full).unwrap()[section].clone();
            for (key, value) in prior.as_object().unwrap() {
                if section == "player" && key == "health" {
                    continue;
                }
                if section == "world" && (key.ends_with("recent_ms") || key == "ominous_sound_kind")
                {
                    continue;
                }
                if v[section][key].is_null() {
                    v[section][key] = value.clone();
                }
            }
        }
        GameEvent::parse(v).expect("combining validated independent observations")
    }
    /// 現在脅威・被弾・警告抑止を設定閾値へ照合し、次の戦況modeを選ぶ。
    fn resolve_mode(&self, e: &GameEvent, now: u64, s: &Settings) -> Mode {
        let nearest = e
            .visual_threats
            .iter()
            .filter_map(|t| t.distance)
            .fold(f64::INFINITY, f64::min);
        let count = e.combat.hostiles_within_10.unwrap_or_else(|| {
            e.visual_threats
                .iter()
                .filter(|t| t.distance.is_some_and(|d| d <= 10.0))
                .count() as i64
        });
        let damage = self
            .last_damage
            .map(|(at, age)| age.saturating_add(now.saturating_sub(at)));
        let rear = e.visual_threats.iter().any(|t| {
            matches!(
                t.direction.horizontal,
                Some(H::Back | H::BackLeft | H::BackRight)
            ) && t
                .distance
                .is_some_and(|d| d <= s.number("rear_warning_distance"))
        });
        let panic = nearest <= s.number("panic_distance")
            || count >= 2
            || damage.is_some_and(|age| age <= s.ms("recent_damage_window_ms"))
            || rear;
        let active = e.combat.combat_active_hint == Some(true)
            || !e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
            || damage.is_some_and(|age| age <= s.ms("recent_damage_window_ms"));
        if panic {
            return if self.shut_up_count >= 3 && active {
                Mode::SuppressedPanic
            } else {
                Mode::Panic
            };
        }
        if !e.visual_threats.is_empty() || !e.auditory_threats.is_empty() || dark_alert(e, s) {
            return Mode::Alert;
        }
        if self.mode == Mode::Aftermath && self.aftermath_until.is_some_and(|until| now < until) {
            return Mode::Aftermath;
        }
        Mode::Normal
    }
    #[allow(clippy::too_many_arguments)]
    fn select(
        &mut self,
        e: &GameEvent,
        now: u64,
        complete: bool,
        busy: bool,
        previous: Mode,
        prior_audio: Option<u64>,
        text: Option<&str>,
        s: &Settings,
        ws: &threats::Settings,
        input_handled: &mut bool,
    ) -> Vec<Speech> {
        let mut actions = Vec::new();
        if let Some(sonic) = self.specials.sonic_boom(e, now, s) {
            self.policy.mark_cue(now);
            actions.push(sonic);
        }
        let skeleton = actions.is_empty()
            && complete
            && !busy
            && self.mode != Mode::SuppressedPanic
            && self.policy.cue_allowed(e, now, ws)
            && e.combat.recent_damage_ms.is_some_and(|n| n <= 1000)
            && elapsed(now, prior_audio, 30_000)
            && e.visual_threats.iter().any(|t| t.r#type == "skeleton")
            && !e.visual_threats.iter().any(|t| {
                matches!(
                    t.r#type.as_str(),
                    "warden" | "wither" | "ender_dragon" | "elder_guardian" | "ravager"
                )
            });
        let body = self.select_body(e, now, complete, busy, previous, text, s, ws, input_handled);
        let reserved = body.as_ref().is_some_and(|a| {
            matches!(
                a.kind,
                "creeper_fuse"
                    | "close_ambush"
                    | "ushiro_named"
                    | "ushiro_classic"
                    | "dark_push_forward"
                    | "flying_hostile"
                    | "boss_visual"
            )
        });
        if skeleton && !reserved {
            if body.as_ref().is_some_and(|a| a.kind == "low_health") {
                self.low_health_armed = true;
            }
            let mut scream = Speech::new("skeleton_damage_ambush", "きゃー！");
            scream.cue_id = Some("panic_scream_start");
            scream.interrupt = true;
            actions.push(scream);
            return actions;
        }
        if let Some(body) = body {
            if body.cue_id.is_none()
                && body.visual_plan.is_none()
                && self.mode != Mode::SuppressedPanic
                && self.policy.cue_allowed(e, now, ws)
                && !e.visual_threats.is_empty()
                && !e.visual_threats.iter().any(|t| {
                    matches!(
                        t.r#type.as_str(),
                        "warden" | "wither" | "ender_dragon" | "elder_guardian" | "ravager"
                    )
                })
            {
                let count = e.combat.hostiles_within_10.unwrap_or_else(|| {
                    e.visual_threats
                        .iter()
                        .filter(|t| t.distance.is_some_and(|d| d <= 10.0))
                        .count() as i64
                });
                let group = count >= 2
                    || !self.specials.mass_latched()
                        && threats::ground_count(e, ws) >= ws.hostile_mass_callout_threshold;
                if group || threats::highest(e).is_some_and(|t| threats::spotted_gasp(t, ws)) {
                    let mut cue =
                        Speech::new("combat_cue", if group { "ひいっ！" } else { "ハッ" });
                    cue.cue_id = Some("spot_hostile_gasp");
                    cue.scope = body.scope.clone();
                    actions.push(cue);
                }
            }
            actions.push(body);
        }
        actions
    }
    #[allow(clippy::too_many_arguments)]
    fn select_body(
        &mut self,
        e: &GameEvent,
        now: u64,
        complete: bool,
        busy: bool,
        previous: Mode,
        text: Option<&str>,
        s: &Settings,
        ws: &threats::Settings,
        input_handled: &mut bool,
    ) -> Option<Speech> {
        // 新規導火は呼吸ループや進行中本文より優先。cue-onlyで警告本文を消費しない。
        if complete && let Some(plan) = self.policy.fuse(e, now, ws) {
            return Some(from_warning(plan));
        }
        let cue = self.policy.cue_allowed(e, now, ws);
        if busy {
            if !complete {
                return None;
            }
            let mut urgent = self
                .specials
                .rear_ambush(e, now, cue, s)
                .or_else(|| {
                    self.specials
                        .front_ambush(e, now, self.dark_push_active, occluded(e), cue, s)
                })
                .or_else(|| self.policy.close_ambush(e, now, ws).map(from_warning));
            if urgent.is_none() && self.specials.has_fresh_boss(e) {
                urgent =
                    self.specials
                        .boss_visual(e, now, self.policy.common_cue_ready(now, ws), s);
            }
            if urgent.is_none() {
                urgent = self.specials.flying(e, now, cue, s);
            }
            if let Some(speech) = &mut urgent {
                speech.interrupt = true;
            }
            return urgent;
        }
        if self.mode == Mode::SuppressedPanic && cue {
            let (id, text) = if previous == Mode::SuppressedPanic {
                ("suppressed_breath", "ハァハァ……")
            } else {
                ("suppressed_gasp", "ひいっ！")
            };
            let mut speech = Speech::new("suppressed_panic", text);
            speech.cue_id = Some(id);
            return Some(speech);
        }
        if let Some(speech) = text.and_then(|text| {
            queries::answer(
                e,
                text,
                s,
                self.last_named.as_ref().map(|(k, t)| (k.as_str(), *t)),
                now,
            )
        }) {
            *input_handled = true;
            return Some(speech);
        }
        if let Some(speech) = self
            .specials
            .warden_tactic(e, now, s)
            .or_else(|| self.specials.dragon(e, now, s))
        {
            return Some(speech);
        }
        if complete {
            if let Some(speech) = self.specials.rear_ambush(e, now, cue, s).or_else(|| {
                self.specials
                    .front_ambush(e, now, self.dark_push_active, occluded(e), cue, s)
            }) {
                return Some(speech);
            }
            if self.mode != Mode::SuppressedPanic
                && let Some(plan) = self.policy.close_ambush(e, now, ws)
            {
                return Some(from_warning(plan));
            }
            if self.mode != Mode::SuppressedPanic
                && self.policy.has_close_ambush(e)
                && !matches!(
                    e.world.biome.as_deref(),
                    Some("deep_dark" | "minecraft:deep_dark")
                )
            {
                return None;
            }
            if let Some(speech) = self
                .specials
                .boss_visual(e, now, self.policy.common_cue_ready(now, ws), s)
                .or_else(|| self.specials.neutral_hostile(e, now, s))
                .or_else(|| self.specials.flying(e, now, cue, s))
            {
                return Some(speech);
            }
        }
        if self.low_health_armed
            && e.player
                .health
                .is_some_and(|h| h <= s.number("low_health_warning_threshold"))
            && (!e.visual_threats.is_empty()
                || !e.auditory_threats.is_empty()
                || threats::ground_count(e, ws) > 0)
        {
            self.low_health_armed = false;
            return Some(Speech::new(
                "low_health",
                format!("{}！体力やばいで！", call_name(e, s)),
            ));
        }
        if complete {
            if self.specials.mass_latched()
                && threats::ground_count(e, ws) >= s.ms("hostile_mass_callout_threshold") as usize
            {
                return None;
            }
            if let Some(speech) = self
                .specials
                .warp_mass(e, now, self.mode, s)
                .or_else(|| self.specials.daylight_rain(e, now, s))
                .or_else(|| self.specials.daylight_water(e, now, s))
                .or_else(|| self.specials.burning(e, now, s))
            {
                return Some(speech);
            }
            if !self.stalled_ids.is_empty()
                && self.stalled_since.is_some_and(|at| {
                    now.saturating_sub(at) >= s.ms("stalled_visual_comment_delay_ms")
                })
                && elapsed(
                    now,
                    self.stalled_spoken,
                    s.ms("stalled_visual_comment_cooldown_ms"),
                )
            {
                self.stalled_spoken = Some(now);
                let variant = if self.mode == Mode::SuppressedPanic {
                    "stalled_visual_suppressed"
                } else {
                    "stalled_visual"
                };
                return Some(Speech::visual(
                    "stalled_visual",
                    catalog::text("combat", &["pressure", variant]),
                    self.stalled_ids.clone(),
                ));
            }
            if let Some(plan) = self
                .policy
                .ordinary(e, now, ws, self.mode == Mode::SuppressedPanic)
            {
                return Some(from_warning(plan));
            }
        }
        if !matches!(
            e.player.dimension.as_deref().unwrap_or(""),
            "" | "overworld" | "minecraft:overworld"
        ) && (e.visual_threats.len() >= s.ms("other_realm_swarm_visual_threshold") as usize
            || !e.visual_threats.is_empty()
                && e.auditory_threats.len() >= s.ms("other_realm_audio_generic_threshold") as usize)
        {
            return None;
        }
        if let Some(speech) = self
            .auditory
            .select(e, now, occluded(e), s, &self.policy, ws)
        {
            return Some(speech);
        }
        self.specials
            .omen(e, now, s)
            .or_else(|| self.specials.ominous(e, now, s))
            .or_else(|| self.specials.mining_fatigue(e, now, s))
            .or_else(|| self.specials.overworld_return(e, now, s))
    }
    fn deliver(&mut self, e: &GameEvent, now: u64, mut speech: Speech, d: &mut Decision) {
        if speech.cue_id.is_some() {
            self.policy.mark_cue(now);
        }
        if let Scope::Auditory(ids) = &speech.scope {
            self.policy.mark_heard(ids, now);
        }
        if let Scope::Visual(ids) = &speech.scope {
            let single = if ids.len() == 1 {
                e.visual_threats
                    .iter()
                    .find(|t| threats::identity(t) == ids[0])
                    .map(|t| t.r#type.as_str())
            } else {
                None
            };
            if speech.kind == "daylight_water" {
                let survivors = e
                    .visual_threats
                    .iter()
                    .filter(|t| Specials::water_survivor(e, t))
                    .map(threats::identity)
                    .collect::<Vec<_>>();
                self.policy.mark_commented(&survivors, now);
                if e.visual_threats.len() >= 2 {
                    self.policy.mark_handled(&[], None, now);
                    self.last_named = None;
                }
            } else if matches!(
                speech.kind,
                "ushiro_named" | "ushiro_classic" | "stalled_visual" | "warp_mass_hostiles"
            ) {
                self.policy.mark_handled(&[], None, now);
                self.last_named = None;
            } else if speech.visual_plan.is_none()
                && !matches!(
                    speech.kind,
                    "daylight_rain" | "newly_burning_visual" | "combat_cue"
                )
            {
                self.policy.mark_handled(ids, single, now);
            }
            if let Some(kind) = single
                && !matches!(
                    speech.kind,
                    "daylight_water"
                        | "daylight_rain"
                        | "newly_burning_visual"
                        | "combat_cue"
                        | "ushiro_named"
                        | "ushiro_classic"
                        | "stalled_visual"
                        | "warp_mass_hostiles"
                )
            {
                self.last_named = Some((kind.to_owned(), now));
            }
        }
        if let Some(plan) = &speech.visual_plan {
            if !plan.hostile_type.is_empty() && !plan.text.is_empty() {
                self.last_named = Some((plan.hostile_type.clone(), now));
            }
            if plan.kind == "creeper_fuse" || plan.kind == "close_ambush" {
                speech.interrupt = true;
            }
        }
        if matches!(
            speech.kind,
            "ushiro_named"
                | "ushiro_classic"
                | "dark_push_forward"
                | "boss_visual"
                | "flying_hostile"
        ) && let Some(id) = speech.cue_id.take()
        {
            let cue_text = match id {
                "ushiro_scream" | "front_spawn_scream" => "ぎゃー！",
                "boss_reveal_scream" => {
                    if e.visual_threats.iter().any(|t| t.r#type == "warden") {
                        "ひいっ！"
                    } else {
                        "ぎゃー！"
                    }
                }
                _ => "ひいっ！",
            };
            let mut cue = Speech::new("combat_cue", cue_text);
            cue.cue_id = Some(id);
            cue.scope = speech.scope.clone();
            cue.interrupt = speech.interrupt;
            cue.protect_ms = speech.protect_ms;
            d.stop_audio |= cue.interrupt;
            d.actions.push(cue);
            speech.interrupt = false;
        }
        d.stop_audio |= speech.interrupt;
        d.actions.push(speech);
    }
    fn update_stalled(&mut self, e: &GameEvent, now: u64) {
        let mut ids = e
            .visual_threats
            .iter()
            .filter(|t| t.r#type != "ender_dragon")
            .map(threats::identity)
            .collect::<Vec<_>>();
        ids.sort();
        if ids != self.stalled_ids {
            self.stalled_since = (!ids.is_empty()).then_some(now);
            self.stalled_ids = ids;
        }
    }
}
fn from_warning(plan: Warning) -> Speech {
    let ids = if plan.group_counts.is_empty() {
        vec![plan.target.clone()]
    } else {
        vec![]
    };
    let mut speech = Speech::visual(plan.kind, plan.text.clone(), ids);
    speech.cue_id = plan.cue.as_ref().map(|c| c.id);
    speech.cue_sequence = plan.cue_sequence.clone();
    speech.protect_ms = if plan.hostile_type == "charged_creeper" {
        2500
    } else {
        0
    };
    speech.visual_plan = Some(plan);
    speech
}
pub fn wants_quiet(text: &str) -> bool {
    let folded: String = text
        .chars()
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .collect();
    ["うるさい", "静かにして", "黙れ"]
        .iter()
        .any(|word| folded.contains(word))
}
pub fn call_name(e: &GameEvent, s: &Settings) -> String {
    e.meta
        .call_name
        .as_deref()
        .filter(|n| !n.trim().is_empty())
        .or_else(|| {
            (!s.text("default_call_name").trim().is_empty()).then(|| s.text("default_call_name"))
        })
        .or(e.player.name.as_deref())
        .unwrap_or("プレイヤー")
        .trim()
        .to_owned()
}
pub fn occluded(e: &GameEvent) -> bool {
    occluded_with_cover(
        e,
        e.world.overhead_cover_type.as_deref().unwrap_or("unknown"),
    )
}
/// Shared geometry; callers retain their own canonical cover normalization.
pub(crate) fn occluded_with_cover(e: &GameEvent, cover: &str) -> bool {
    let w = &e.world;
    if w.is_submerged == Some(true) {
        return false;
    }
    let enclosure = w.enclosure_score.unwrap_or(0.0);
    let ceiling = w.ceiling_height.unwrap_or(0.0);
    let open = if w.sky_visible == Some(true) && ceiling >= 12.0 {
        true
    } else if cover == "foliage" {
        ceiling >= 3.0 || w.local_light.unwrap_or(15) >= 9 && enclosure < 0.9
    } else if w.sky_visible != Some(true) {
        ceiling >= 12.0 && enclosure < 0.12
            || matches!(cover, "foliage" | "fluid") && enclosure < 0.18
    } else {
        enclosure < 0.18
    };
    !open
}
fn safe_zone(e: &GameEvent) -> bool {
    let w = &e.world;
    if w.is_submerged == Some(true)
        || w.nearby_door_count.unwrap_or(0) <= 0
        || w.local_light.unwrap_or(0) < 8
    {
        return false;
    }
    let ceiling = w.ceiling_height.filter(|n| *n != 0.0).unwrap_or(24.0);
    let enclosure = w.enclosure_score.unwrap_or(0.0);
    match w.safe_zone_with_door {
        Some(false) => false,
        Some(true) => !(w.sky_visible == Some(true) && ceiling >= 8.0 && enclosure < 0.45),
        None => enclosure >= 0.18 || ceiling <= 5.0 || w.sky_visible != Some(true),
    }
}
fn dark_alert(e: &GameEvent, s: &Settings) -> bool {
    let w = &e.world;
    if w.is_submerged == Some(true) {
        return false;
    }
    if w.danger_darkness_score.unwrap_or(0.0) >= s.number("darkness_alert_threshold")
        && w.local_light
            .is_none_or(|n| n as f64 <= s.number("darkness_advice_light_threshold"))
    {
        return true;
    }
    if !occluded(e)
        || !w
            .local_light
            .map(|n| n as f64 <= s.number("occluded_entry_light_threshold"))
            .unwrap_or(
                w.danger_darkness_score.unwrap_or(0.0)
                    >= s.number("occluded_entry_darkness_threshold"),
            )
    {
        return false;
    }
    let lamp = w.nearby_light_source_count.unwrap_or(0) > 0
        && w.nearest_light_source_distance
            .is_none_or(|d| d <= s.number("lit_interior_safe_light_source_distance"));
    if lamp && w.local_light.unwrap_or(15) >= 4 || safe_zone(e) {
        return false;
    }
    let ceiling = w.ceiling_height.filter(|n| *n != 0.0).unwrap_or(24.0);
    let cover = w.overhead_cover_type.as_deref().unwrap_or("unknown");
    let solid = !matches!(cover, "foliage" | "fluid");
    let lit = w.sky_visible != Some(true)
        && (w.local_light.unwrap_or(15) as f64 >= s.number("lit_interior_safe_light_threshold")
            || lamp)
        && solid
        && ceiling <= s.number("lit_interior_safe_max_ceiling_height")
        && w.connected_dark_volume
            .is_some_and(|n| n as f64 <= s.number("lit_interior_safe_max_connected_volume"))
        && w.nearest_dark_spawn_distance
            .is_some_and(|n| n >= s.number("lit_interior_safe_min_spawn_distance"));
    if lit {
        return false;
    }
    let wall = w.cardinal_wall_count.unwrap_or(0);
    let openings = w.double_height_open_side_count.unwrap_or(0) > 0;
    let home = w.nearby_bed_count.unwrap_or(0) > 0
        && w.respawn_point_set == Some(true)
        && w.respawn_distance
            .is_some_and(|d| d <= s.number("home_bed_prompt_distance"));
    if !openings
        && wall >= 4
        && ceiling <= s.number("emergency_shelter_max_ceiling_height")
        && !home
    {
        return false;
    }
    let cramped = w.sky_visible != Some(true)
        && !openings
        && solid
        && ceiling <= s.number("cramped_dark_burrow_max_ceiling_height")
        && (w.enclosure_score.unwrap_or(0.0)
            >= s.number("cramped_dark_burrow_min_enclosure_score")
            || w.connected_dark_volume
                .is_some_and(|n| n as f64 <= s.number("cramped_dark_burrow_max_connected_volume"))
                && wall as f64 >= s.number("cramped_dark_burrow_min_wall_count"));
    !cramped
}
fn blocks_environment(e: &GameEvent) -> bool {
    e.visual_threats.iter().any(|t| {
        t.distance.is_some_and(|d| {
            d <= 6.0
                || t.approaching
                || matches!(
                    t.direction.horizontal,
                    Some(H::Back | H::BackLeft | H::BackRight)
                ) && d <= 3.0
                || matches!(
                    t.r#type.as_str(),
                    "skeleton" | "witch" | "blaze" | "ghast" | "pillager" | "drowned" | "evoker"
                ) && d
                    <= match t.r#type.as_str() {
                        "skeleton" => 17.5,
                        "witch" => 11.5,
                        "drowned" => 3.7,
                        _ => 7.5,
                    }
        })
    }) || e.auditory_threats.iter().any(|t| auditory::rank(t) <= 1)
}
