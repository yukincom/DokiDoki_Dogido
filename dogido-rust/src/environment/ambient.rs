//! Non-urgent environment decisions. No I/O, model calls, playback, or world actions.
mod catalog;
mod light;
mod places;
mod smell;
mod surroundings;
#[cfg(test)]
mod tests;

use crate::{
    combat::model::{LeafRequest, Mode, Scope, Settings, Speech, elapsed},
    events::{EventName, GameEvent},
};
pub(crate) use catalog::{biome_label, mob_label};
pub use light::{LightContext, LightPlanRequest};
use serde_json::{Value, json};
pub(crate) use smell::is_query as is_smell_query;
pub use surroundings::player_vehicle_fact;
/// 配送時に明示質問の返答だけを現在観測から作り直す。状態を進めない。
pub fn current_smell_reply(event: &GameEvent) -> Speech {
    smell::speech(event)
}

pub fn current_smell_query_reply(event: &GameEvent, text: &str) -> Speech {
    smell::query_reply(event, text)
}

/// マスキング後の各匂いとスメル値を会話へ渡す。位置・距離・個体IDは持たない。
/// 通常会話と環境反応で共有する投影。強度を落とすと「仄か／強烈」をモデルが選べなくなる。
/// ラベルはIDの読みやすい名前で、別の世界観測や個体の特定結果を追加するものではない。
pub(crate) fn smell_context(event: &GameEvent) -> Option<Value> {
    let observation = smell::observation(event)?;
    if observation.status != crate::events::SmellObservationStatus::Present {
        return None;
    }
    Some(smell_components_context(&observation.components))
}
fn smell_components_context(components: &[crate::events::SmellComponent]) -> Value {
    let mut components: Vec<_> = components.iter().collect();
    components.sort_by_key(|c| catalog::enum_text(&c.smell_id));
    json!({"components":components.into_iter().map(|component| {
        json!({"smell_id":component.smell_id,"label":smell::label(component.smell_id),
            "category":component.category,"valence":component.valence,"source_kind":component.source_kind,
            "effective_strength":component.effective_strength})
    }).collect::<Vec<_>>()})
}

/// 一度の雨上がり反応／明示質問だけに使う読み取り用の投影。
/// 受信した通常componentsや共有会話の現在観測を上書きしないため、完了後の復帰待ちは要らない。
fn rain_after_component(event: &GameEvent, window: u64) -> Option<&crate::events::SmellComponent> {
    let observation = event.smell_observation.as_ref()?;
    let notice = observation.rain_after_notice.as_ref()?;
    if !observation.rain_after_active
        || notice.window_id != window
        || observation.status == crate::events::SmellObservationStatus::Suppressed
    {
        return None;
    }
    // 検証済みGameEventは不変のまま、表示対象の成分だけを借りる。
    Some(&notice.component)
}
pub(crate) fn rain_after_context(event: &GameEvent, window: u64) -> Option<Value> {
    Some(smell_components_context(std::slice::from_ref(
        rain_after_component(event, window)?,
    )))
}

/// 強度・同種の代表源だけの変化を新しい種類の到着にせず、成分順序を正規化する。
/// これは通知の比較用コピーだけを縮める処理。実際のモデル入力には現在の値を残す。
/// 例: ゾンビ1→2だけなら再到着ではなく、花がマスクされて種類集合が変われば差分になる。
pub(crate) fn smell_change_signature(value: &Value) -> Value {
    let mut value = value.clone();
    if let Some(object) = value.as_object_mut()
        && let Some(rows) = object.get_mut("components").and_then(Value::as_array_mut)
    {
        for row in rows.iter_mut() {
            if let Some(row) = row.as_object_mut() {
                row.remove("effective_strength");
                row.remove("source_kind");
            }
        }
        rows.sort_by_key(|row| row["smell_id"].as_str().unwrap_or("").to_owned());
    }
    value
}

#[derive(Clone, Debug, Default)]
pub struct AmbientFocus {
    pub foreground: bool,
    pub casual_foreground: bool,
    pub last_player_input_at: Option<u64>,
    pub player_priority: bool,
    pub boss_presence: bool,
    pub ominous_presence: bool,
    pub submerged: bool,
    pub safe_zone_with_door: bool,
    pub light: LightContext,
    pub notice_ready: bool,
}
#[derive(Clone, Default)]
pub struct Ambient {
    smell: smell::Presence,
    rain_after: smell::RainAfter,
    places: places::Places,
    last_danger: Option<u64>,
    last_player_input: Option<u64>,
    dimension: Option<String>,
    inventory: Option<i64>,
    gain: Option<(i64, i64)>,
    last_light_comment: Option<u64>,
    light_serial: u64,
    light_pending: Option<LightPlanRequest>,
    light_ready: Option<LightPlanRequest>,
    last_eye: Option<u64>,
}

impl Ambient {
    /// Code-owned structure state: changed only by status snapshots or dimension resets.
    pub fn current_structure(&self) -> Option<&str> {
        self.places.current_structure()
    }

    pub fn update(&mut self, e: &GameEvent, now: u64, complete: bool, s: &Settings) {
        let dimension = e
            .player
            .dimension
            .as_deref()
            .unwrap_or("")
            .trim()
            .to_owned();
        if !dimension.is_empty() {
            if self.dimension.as_ref().is_some_and(|old| old != &dimension) {
                self.smell = smell::Presence::default();
                self.rain_after = smell::RainAfter::default();
                self.places.dimension_changed();
                self.last_danger = None;
                self.gain = None;
                self.light_pending = None;
                self.light_ready = None;
            }
            self.dimension = Some(dimension);
        }
        if e.meta
            .user_text
            .as_deref()
            .is_some_and(|t| !t.trim().is_empty())
        {
            self.note_player_input(now);
        }
        // Missing fields in partial audio/mob events are not absence observations.
        if complete || e.smell_observation.is_some() {
            self.smell.update(e);
            self.rain_after.update(e);
        }
        if !e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
            || e.combat
                .recent_damage_ms
                .is_some_and(|age| age <= s.ms("recent_damage_window_ms") as i64)
        {
            self.last_danger = Some(now);
        }
        if complete {
            self.places.update(e);
        }
        self.gain = None;
        if complete && e.event.name == EventName::StatusSnapshot {
            let current = light::count(e);
            if self.inventory.is_some_and(|n| n != current) {
                self.light_pending = None;
                self.light_ready = None;
            }
            if let Some(previous) = self.inventory.filter(|n| current > *n) {
                self.gain = Some((previous, current));
            }
            self.inventory = Some(current);
        }
    }
    pub fn note_player_input(&mut self, now: u64) {
        self.last_player_input = Some(now);
        self.light_pending = None;
        self.light_ready = None;
    }
    pub fn actions(
        &mut self,
        e: &GameEvent,
        now: u64,
        mode: Mode,
        busy: bool,
        focus: &AmbientFocus,
        s: &Settings,
    ) -> Vec<Speech> {
        let input_at = focus.last_player_input_at.or(self.last_player_input);
        if busy
            || focus.player_priority
            || !elapsed(now, input_at, s.ms("player_input_priority_cooldown_ms"))
            || matches!(mode, Mode::Panic | Mode::SuppressedPanic | Mode::Aftermath)
            || !e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
        {
            return vec![];
        }
        if focus.foreground && !focus.casual_foreground {
            return vec![];
        }
        if self.light_request(e, now, focus, s) {
            return vec![];
        }
        if let Some(speech) = self.eye_action(e, now, s) {
            return vec![speech];
        }
        if focus.boss_presence || focus.ominous_presence {
            return vec![];
        }
        if focus.casual_foreground
            && (input_at.is_none() || !elapsed(now, input_at, s.ms("conversation_ambient_mute_ms")))
        {
            return vec![];
        }
        // 雨上がりの「あっ」は非敵対ambient。Web・学習・会話中のmute等をすべて通った後だけ候補にする。
        // 呼出側の既存比較で暗所等の警告が勝てるよう、ゾンビ臭の優先経路には入れない。
        // 通常臭の検討済み状態と2分のゾンビ臭cooldownは、この一言では変更しない。
        if let Some(window) = self.rain_after.pending(e)
            && let Some(component) = rain_after_component(e, window)
        {
            let mut line = smell::speech_components(std::slice::from_ref(component));
            line.scope = Scope::Safe;
            line.rain_after_window = Some(window);
            return vec![line];
        }
        if focus.notice_ready {
            vec![super::notice::action()]
        } else {
            vec![]
        }
    }
    pub fn smell_query(&mut self, e: &GameEvent, text: &str, now: u64) -> Option<Speech> {
        if !smell::is_query(text) {
            return None;
        }
        if self.rain_after.pending(e).is_none() {
            self.smell.mark(e, now);
        }
        Some(self.smell_query_reply(e, text))
    }
    /// 配送直前にも使う。まだ一度も扱っていない雨上がりなら、その一件を回答へ結ぶ。
    pub fn smell_query_reply(&self, e: &GameEvent, text: &str) -> Speech {
        if let Some(window) = self.rain_after.pending(e)
            && let Some(component) = rain_after_component(e, window)
        {
            let mut reply = smell::query_components(std::slice::from_ref(component), text);
            reply.rain_after_window = Some(window);
            return reply;
        }
        smell::query_reply(e, text)
    }
    pub(crate) fn end_rain_after_attention(&mut self, window: u64) {
        self.rain_after.finish(window);
    }
    pub(crate) fn reconsider_smell_estimate(&mut self, e: &GameEvent) {
        self.smell.reconsider_estimate(e);
    }
    pub fn smell_answer(&mut self, e: &GameEvent, now: u64) -> Speech {
        if self.rain_after.pending(e).is_none() {
            self.smell.mark(e, now);
        }
        self.smell_query_reply(e, "")
    }
    pub fn take_light_plan(&mut self) -> Option<LightPlanRequest> {
        self.light_ready.take()
    }
}
fn leaf(kind: &'static str, text: String, details: Value, temperature: f64) -> Speech {
    let mut speech = Speech::new(kind, text);
    speech.scope = Scope::Safe;
    speech.leaf = Some(LeafRequest {
        kind: kind.to_owned(),
        details,
        temperature,
    });
    speech
}
fn common_details(e: &GameEvent, s: &Settings) -> Value {
    json!({"player_name":crate::combat::core::call_name(e,s),"biome":catalog::biome_label(e.world.biome.as_deref().unwrap_or("unknown")),"time_phase":e.world.time_phase})
}
/// Recheck immediately before and during rendering against the current environment notification.
/// The host separately verifies observation freshness, focus, and authoritative hostile lists.
pub fn still_applicable(speech: &Speech, e: &GameEvent) -> bool {
    if !super::reaction::still_applicable(speech, e) {
        return false;
    }
    // 天候leafのうち、実雷鳴への反応はDanger側の担当。
    // 暗所や雷の許可条件をambientの「敵なし」で上書きしない。
    if !matches!(
        speech.kind,
        "surroundings_notice" | "smell" | "light_source_gain" | "ender_eye_throw"
    ) || speech
        .leaf
        .as_ref()
        .is_some_and(|r| r.details["thunder_reaction"] == true)
    {
        return true;
    }
    if !e.visual_threats.is_empty() || !e.auditory_threats.is_empty() {
        return false;
    }
    let guard = speech.leaf.as_ref().map(|r| &r.details["__ambient_guard"]);
    if let Some(g) = guard
        && let Some(count) = g["light_source_count"].as_i64()
    {
        return light::count(e) == count;
    }
    match speech.kind {
        "smell" if super::reaction::is_model_reaction(speech) => true,
        "smell" => match speech.rain_after_window {
            Some(window) => rain_after_component(e, window).is_some_and(|component| {
                smell::components_still_applicable(speech, std::slice::from_ref(component))
            }),
            None => smell::still_applicable(speech, e),
        },
        _ => true,
    }
}
impl Ambient {
    /// 残ったゾンビ系の実体臭だけ、雷・現在入力の後、通常ambientより先に反応機会を設ける。
    /// 手持ちの腐った肉だけではこの優先経路へ入らず、匂い単独で戦闘modeにも変更しない。
    /// 視覚・聴覚の脅威が既にある時は、そちらの戦闘警告と重ねない。
    pub fn priority_smell(
        &mut self,
        e: &GameEvent,
        now: u64,
        mode: Mode,
        busy: bool,
        s: &Settings,
    ) -> Option<Speech> {
        use crate::events::{SmellObservationSmellId::Zombie, SmellObservationSourceKind::Entity};
        if busy
            || matches!(mode, Mode::Panic | Mode::SuppressedPanic | Mode::Aftermath)
            || !e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
        {
            return None;
        }
        let zombie = e.smell_observation.as_ref().is_some_and(|o| {
            o.components
                .iter()
                .any(|c| c.smell_id == Zombie && c.source_kind == Entity)
        });
        if zombie {
            self.smell.action(e, now, s)
        } else {
            None
        }
    }
}
