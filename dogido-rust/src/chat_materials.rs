//! 通常対話の一回生成へ渡す観測・会話履歴・辞書材料を組み立てる。
//! Sessionのsnapshotを読むだけで、対象選択のモデル呼出しや返答の意味検査は行わない。
mod current;
mod descriptions;
mod individuals;
mod travel;
use crate::{
    chat_catalog, chat_hints,
    chat_observation::{Labels, Snapshot},
    chat_prompt,
    events::GameEvent,
    types::GenerationRequest,
};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

/// 現在の実観測に含まれる種類・個体のIDと呼称。履歴やモデル判断はここへ混ぜない。
#[derive(Debug, Clone, Serialize)]
struct Observation {
    entity_id: String,
    label: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub darkness_alert_threshold: f64,
    pub home_bed_prompt_distance: f64,
    pub default_call_name: String,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            darkness_alert_threshold: 0.72,
            home_bed_prompt_distance: 10.,
            default_call_name: String::new(),
        }
    }
}
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct PlayerInput {
    pub raw_text: String,
    pub semantic_text: String,
    pub asks_inventory: bool,
    pub asks_about_sound: bool,
    pub knowledge_query_present: bool,
}
impl From<&crate::input_context::Context> for PlayerInput {
    fn from(value: &crate::input_context::Context) -> Self {
        Self {
            raw_text: value.raw_text.clone(),
            semantic_text: value.interpreted_text.clone(),
            asks_inventory: value.asks_inventory,
            asks_about_sound: value.asks_about_sound,
            knowledge_query_present: value.knowledge_query.is_some(),
        }
    }
}
/// Owner must use its completed history reader. Neither model nor queued speech supplies these.
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct CompletedHistory {
    pub conversation_history: String,
    pub conversation_turns: Vec<Value>,
    pub event_digest: String,
}
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct WorkshopFields {
    pub haiku_workshop_open: String,
    pub haiku_workshop_text: String,
    pub haiku_workshop_materials: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Context {
    pub mode: String,
    pub current_structure: Option<String>,
    pub history: CompletedHistory,
    pub workshop_open: bool,
    pub workshop_details: Option<WorkshopFields>,
    pub world_context: Option<crate::conversation_observation::Context>,
}
impl Default for Context {
    fn default() -> Self {
        Self {
            mode: "normal".into(),
            current_structure: None,
            history: CompletedHistory::default(),
            workshop_open: false,
            workshop_details: None,
            world_context: None,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Fixed {
    pub text: String,
    pub reason: &'static str,
    pub announce_smell: bool,
}
#[derive(Debug)]
pub enum Prepared {
    Knowledge,
    Fixed(Fixed),
    Reply(Reply),
}
#[derive(Debug)]
pub struct Reply {
    pub details: Value,
}
impl Reply {
    /// 同じ観測・履歴から、話題判断と本文を一回で生成するリクエストを作る。
    pub fn request(&self, model: &str, max_tokens: u64) -> Result<GenerationRequest> {
        let mut details = self.details.clone();
        details["dialogue_choice"] = true.into();
        chat_prompt::Input {
            schema_version: 1,
            kind: "player_chat".into(),
            model: model.into(),
            details: chat_prompt::project_details(&details),
            temperature: 0.65,
            max_tokens,
            enable_thinking: false,
        }
        .into_request()
    }
}

/// 判断開始時のsnapshotから材料を一度用意する。返答対象の選択は本文生成に含める。
pub fn prepare(
    event: &GameEvent,
    settings: &Settings,
    input: &PlayerInput,
    context: &Context,
    snapshot: &Snapshot,
    _labels: &impl Labels,
) -> Result<Prepared> {
    if input.knowledge_query_present {
        return Ok(Prepared::Knowledge);
    }
    let user = chat_catalog::strip(&input.semantic_text);
    let focused = individuals::focus(event, snapshot, context, user);
    let (event, snapshot, context) = focused
        .as_ref()
        .map(|turn| (&turn.event, &turn.snapshot, &turn.context))
        .unwrap_or((event, snapshot, context));
    if crate::environment::ambient::is_smell_query(user) {
        return Ok(Prepared::Fixed(Fixed {
            text: crate::environment::ambient::current_smell_query_reply(event, user).text,
            reason: "current_smell",
            announce_smell: true,
        }));
    }
    let combat = event.combat.combat_active_hint == Some(true)
        || matches!(context.mode.as_str(), "panic" | "suppressed_panic");
    let visual = !event.visual_threats.is_empty();
    let dark = event
        .world
        .danger_darkness_score
        .is_some_and(|v| v >= settings.darkness_alert_threshold);
    let travel = travel::extract(user);
    let mut details = current::frame_details(
        event,
        settings,
        context,
        input,
        snapshot,
        &travel,
        (combat, visual, dark),
    )?;
    let effective_visual = current::unique(
        event
            .visual_threats
            .iter()
            .map(|v| v.r#type.as_str())
            .chain(snapshot.recent.visual_types.iter().map(String::as_str)),
    );
    let tactics = chat_hints::player_chat_tactics(
        chat_catalog::catalog(),
        &event.visual_threats,
        &effective_visual,
    )?;
    let sound = individuals::hearing(snapshot, user);
    let (mut hearing, mut named, mut sources) = if input.asks_about_sound {
        (
            if sound.ambiguous {
                "同じ名前の個体が複数おり、どの個体の音かは未確定。".into()
            } else {
                sound.summary
            },
            sound.named,
            sound.sources,
        )
    } else {
        (
            snapshot.hearing.summary.clone(),
            snapshot.hearing.named_mobs.clone(),
            snapshot.hearing.source_labels.clone(),
        )
    };
    if context.workshop_open && !input.asks_about_sound {
        hearing.clear();
        named.clear();
        sources.clear();
    }
    // 音の本文は共通world_context.hearingだけへ渡す。旧メモは内部診断に残す。
    let threat = current::threat(event, snapshot);
    let mut look = crate::world_catalog::catalog()
        .look_target_label(chat_catalog::catalog(), event.look_target.as_ref());
    if !look.is_empty()
        && let Some(name) = event
            .look_target
            .as_ref()
            .and_then(|target| target.identity.as_ref())
            .and_then(crate::mob_identity::name)
    {
        look = name.into();
    }
    let ids = current::unique(
        event
            .visual_threats
            .iter()
            .map(|v| v.r#type.as_str())
            .chain(snapshot.current.passive_types.iter().map(String::as_str))
            .chain(snapshot.current.hearing_types.iter().map(String::as_str)),
    );
    let mut observed = current::observed(event, &ids, context.current_structure.as_deref());
    observed.splice(0..0, individuals::current_observations(snapshot, user));
    let observation = if context.workshop_open {
        look.clear();
        observed.clear();
        current::observation(event, &threat, &[], &look, &[])
    } else {
        current::observation(
            event,
            &threat,
            &snapshot.current.passive_types,
            &look,
            &snapshot.recent.passive_sightings,
        )
    };
    details.as_object_mut().unwrap().extend(
        json!({
            "threat_summary":threat, "hearing_summary":hearing, "hearing_named_mobs":named,
            "hearing_source_labels":sources, "observation_summary":observation,
            "current_entity_context":serde_json::to_string(&observed)?, "look_target_label":look,
            "nearby_hostile_types":tactics.nearby_hostile_types, "mob_tactics_notes":tactics.notes,
            "safe_hints":tactics.safe_hints,
        })
        .as_object()
        .unwrap()
        .clone(),
    );
    if !context.workshop_open {
        let named = individuals::context(snapshot, user, &look, false);
        if !named.is_empty() {
            details["named_mob_context"] = named.into();
        }
        if let Some(description) = descriptions::named_mob(user) {
            details["named_entity_description_hints"] = description.into();
        }
    }
    Ok(Prepared::Reply(Reply { details }))
}

#[cfg(test)]
mod tests;
