//! A frozen ordinary turn's pure materials, split before and after the bounded planner.
//! No Session mutation, model run, history writes, clock reads, or playback success claims.
use crate::text_format::{self, ContainerFormat::QuotedRepr};
mod current;
mod descriptions;
mod individuals;
mod travel;
use crate::{
    chat_catalog, chat_hints, chat_names,
    chat_observation::{Labels, Snapshot},
    chat_prompt, chat_topics, chat_validation,
    events::GameEvent,
    planner::{self, Action, Plan, handoff},
};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

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
    pub repair: Option<planner::repair::Repair>,
}
#[derive(Debug)]
pub enum Before {
    Knowledge,
    Fixed(Fixed),
    Plan(Box<Prepared>),
}
#[derive(Debug)]
pub struct Prepared {
    pub planner: planner::prepare::PreparedInput,
    pub planner_input: planner::prepare::Input,
    event: GameEvent,
    snapshot: Snapshot,
    context: Context,
    input: PlayerInput,
    base_details: Value,
    observed_ids: Vec<String>,
    observed_entities: Vec<handoff::Observation>,
    effective_visual: Vec<String>,
    passive: Vec<String>,
    recent_passive: Vec<crate::chat_observation::PassiveSighting>,
    look: String,
    audio_fallback: Option<String>,
    fallback: String,
    tactics: chat_hints::ChatTactics,
}
/// Supply the same current event and read-only temporal snapshot to this one turn.
/// A Knowledge/Fixed result means no planner or leaf generation should be called.
pub fn before_plan(
    event: &GameEvent,
    settings: &Settings,
    input: &PlayerInput,
    context: &Context,
    snapshot: &Snapshot,
    labels: &impl Labels,
    model: Option<&str>,
) -> Result<Before> {
    if input.knowledge_query_present {
        return Ok(Before::Knowledge);
    }
    let user = chat_catalog::strip(&input.semantic_text);
    if crate::environment::ambient::is_smell_query(user) {
        return Ok(Before::Fixed(Fixed {
            text: crate::environment::ambient::current_smell_query_reply(event, user).text,
            reason: "current_smell",
            announce_smell: true,
            repair: None,
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
    let base_details = current::frame_details(
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
        &snapshot.recent.visual_types,
    )?;
    let fallback = if let Some(text) = &tactics.safe_fallback {
        text.clone()
    } else if travel.action == "return_home" && !combat && tactics.nearby_hostile_types.is_empty() {
        if current::evening(event) {
            "せやな、暗なる前に帰ろか。気いつけてな。".into()
        } else {
            "せやな、気いつけて帰ろか。".into()
        }
    } else {
        crate::environment::ambient::general_text("chat", "reply")
    };
    let observed_ids = current::unique(
        event
            .visual_threats
            .iter()
            .map(|v| v.r#type.as_str())
            .chain(snapshot.current.passive_types.iter().map(String::as_str))
            .chain(snapshot.current.hearing_types.iter().map(String::as_str)),
    );
    let mut observed_entities =
        current::observed(event, &observed_ids, context.current_structure.as_deref());
    observed_entities.splice(0..0, individuals::current_observations(snapshot));
    let mut look = if crate::chat_world::wants_look_answer(user) {
        crate::world_catalog::catalog()
            .look_target_label(chat_catalog::catalog(), event.look_target.as_ref())
    } else {
        String::new()
    };
    if !look.is_empty()
        && let Some(identity) = event
            .look_target
            .as_ref()
            .and_then(|target| target.identity.as_ref())
        && let Some(name) = crate::mob_identity::name(identity)
    {
        look = name.into();
    }
    let audio_fallback = current::audio_fallback(event, labels);
    let visual_summary = current::threat(event, snapshot, audio_fallback.as_deref(), None);
    let (passive, recent_passive) = if travel.action == "return_home" {
        (vec![], vec![])
    } else {
        (
            snapshot.current.passive_types.clone(),
            snapshot.recent.passive_sightings.clone(),
        )
    };
    let mut summary =
        current::observation(event, &visual_summary, "", &passive, &look, &recent_passive);
    let individual_summary = individuals::context(snapshot, user, &look, true);
    if !individual_summary.is_empty() {
        summary.push_str(&format!(" / 個体の名前と種類: {individual_summary}"));
    }
    let sound_answer = individuals::hearing(snapshot, user);
    let planner_input = planner::prepare::Input {
        user_text: user.into(),
        conversation_turns: json!(context.history.conversation_turns),
        observation_summary: summary,
        observed_entities: observed_entities
            .iter()
            .map(|r| json!(r).as_object().unwrap().clone())
            .collect(),
        look_target_label: look.clone(),
        look_target: event
            .look_target
            .as_ref()
            .filter(|_| !look.is_empty() && !context.workshop_open)
            .map(|target| handoff::Observation {
                entity_id: target
                    .identity
                    .as_ref()
                    .filter(|i| crate::mob_identity::name(i).is_some())
                    .map(|i| format!("individual:{}", i.entity_id))
                    .unwrap_or_else(|| chat_catalog::normalized_observation_id(&target.name)),
                label: look.clone(),
            }),
        hearing_summary: if input.asks_about_sound {
            sound_answer.summary
        } else {
            snapshot.hearing.summary.clone()
        },
        inventory_question: input.asks_inventory,
        sound_question: input.asks_about_sound,
        raw_user_text: Some(input.raw_text.clone()),
        repair_enabled: !context.workshop_open,
    };
    let planner = planner::prepare::prepare(model, &planner_input);
    Ok(Before::Plan(Box::new(Prepared {
        planner,
        planner_input,
        event: event.clone(),
        snapshot: snapshot.clone(),
        context: context.clone(),
        input: input.clone(),
        base_details,
        observed_ids,
        observed_entities,
        effective_visual,
        passive,
        recent_passive,
        look,
        audio_fallback,
        fallback,
        tactics,
    })))
}
#[derive(Debug)]
pub enum After {
    Fixed(Fixed),
    Leaf(Box<Leaf>),
}
#[derive(Debug)]
pub struct Leaf {
    pub details: Value,
    pub fallback_text: String,
    pub repair: Option<planner::repair::Repair>,
    pub handoff_input: handoff::Input,
    pub handoff: handoff::Output,
}
impl Leaf {
    /// Run the existing bounded Turn/runner with this payload, never a second retry loop.
    pub fn input(&self, model: &str, max_tokens: u64) -> chat_validation::Input {
        let mut details = self.details.clone();
        details["dialogue_choice"] = true.into();
        chat_validation::Input {
            prompt: chat_prompt::Input {
                schema_version: 1,
                kind: "player_chat".into(),
                model: model.into(),
                details: chat_prompt::project_details(&details),
                temperature: 0.65,
                max_tokens,
                enable_thinking: false,
            },
            validation: chat_validation::project_details(&self.details),
            fallback_text: self.fallback_text.clone(),
        }
    }
}
/// Plan must already have passed the existing planner parser/evidence contract (or be its fallback).
/// The caller retains cancellation/epoch and Handoff lifecycle ownership. No model calls occur here.
pub fn after_plan(turn: &Prepared, plan: &Plan) -> Result<After> {
    if let Some(repair) = &plan.repair
        && plan.action == Action::ClarifyRepair
    {
        return Ok(After::Fixed(Fixed {
            text: repair.fallback(),
            reason: "clarify_repair",
            announce_smell: false,
            repair: Some(repair.clone()),
        }));
    }
    let event = &turn.event;
    let snapshot = &turn.snapshot;
    let user = chat_catalog::strip(&turn.input.semantic_text);
    let use_hearing = turn.input.asks_about_sound
        || matches!(
            plan.action,
            Action::CheckEntityPresence | Action::CorrectPreviousReply
        );
    let sound_answer = individuals::hearing(snapshot, user);
    if turn.input.asks_about_sound && sound_answer.ambiguous {
        return Ok(After::Fixed(Fixed {
            text: "同じ名前の子が複数おるから、どの子の声かは分からへんわ。".into(),
            reason: "ambiguous_named_sound",
            announce_smell: false,
            repair: plan.repair.clone(),
        }));
    }
    let hearing = if turn.input.asks_about_sound {
        sound_answer.summary
    } else if use_hearing {
        snapshot.hearing.summary.clone()
    } else {
        String::new()
    };
    let named = if turn.input.asks_about_sound {
        sound_answer.named
    } else if use_hearing {
        snapshot.hearing.named_mobs.clone()
    } else {
        vec![]
    };
    let sources = if turn.input.asks_about_sound {
        sound_answer.sources
    } else if use_hearing {
        snapshot.hearing.source_labels.clone()
    } else {
        vec![]
    };
    let threat = current::threat(
        event,
        snapshot,
        turn.audio_fallback.as_deref(),
        use_hearing.then_some(hearing.as_str()),
    );
    let observation = current::observation(
        event,
        &threat,
        &hearing,
        &turn.passive,
        &turn.look,
        &turn.recent_passive,
    );
    let names = chat_names::Input {
        topics: vec![],
        visual_types: turn.effective_visual.clone(),
        passive_types: current::unique(
            snapshot
                .current
                .passive_types
                .iter()
                .map(String::as_str)
                .chain(
                    snapshot
                        .recent
                        .passive_sightings
                        .iter()
                        .map(|r| r.mob_type.as_str()),
                ),
        ),
        hearing_named_mobs: named.iter().chain(sources.iter()).cloned().collect(),
        recent_mob_types: snapshot
            .name_context
            .types
            .iter()
            .cloned()
            .chain(
                crate::mob_identity::mentioned(&snapshot.named_mobs, user)
                    .iter()
                    .map(|r| r.mob_type.clone()),
            )
            .collect(),
        current_entity_labels: turn
            .observed_entities
            .iter()
            .map(|r| r.label.clone())
            .collect(),
        user_text: user.into(),
        history: turn
            .context
            .history
            .conversation_turns
            .iter()
            .filter_map(|r| {
                Some(chat_names::HistoryRow {
                    role: r.get("role")?.as_str()?.into(),
                    text: r
                        .get("text")
                        .filter(|v| chat_catalog::truth(v))
                        .map(|value| text_format::value_text(value, QuotedRepr))
                        .unwrap_or_default(),
                })
            })
            .collect(),
        look_label: turn.look.clone(),
    };
    let input = handoff::Input {
        schema_version: 1,
        source: "current_observation".into(),
        plan: plan.clone(),
        topic_hits: vec![],
        observed_entities: turn.observed_entities.clone(),
        individual_names: snapshot
            .named_mobs
            .iter()
            .map(|row| handoff::Observation {
                entity_id: format!("individual:{}", row.entity_id),
                label: row.custom_name.clone(),
            })
            .collect(),
        look_target: turn.planner_input.look_target.clone(),
        native_catalog: true,
        topic_policy: Some(chat_topics::Input {
            has_visual_threats: !event.visual_threats.is_empty()
                || !snapshot.recent.visual_types.is_empty(),
            topic_hits: vec![],
            threat_summary: threat.clone(),
            user_text: if plan.entity_query.is_empty() {
                user.into()
            } else {
                plan.entity_query.clone()
            },
            observed_ids: turn.observed_ids.clone(),
        }),
        name_context: Some(names),
    };
    let handoff = handoff::project(input.clone())?;
    let topics = handoff.topics.as_ref().expect("native topics");
    let catalog = handoff.catalog.as_ref().expect("native catalog");
    let names = handoff.names.as_ref().expect("native names");
    let selected: Vec<_> = topics
        .topic_for_identify_indices
        .iter()
        .map(|i| catalog[*i].clone())
        .collect();
    let label = crate::world_catalog::catalog().biome_label(event.world.biome.as_deref());
    let plausibility = chat_hints::player_chat_plausibility(
        chat_catalog::catalog(),
        topics.policy.reply_stance.as_str(),
        &selected,
        event.world.biome.as_deref(),
        Some(&label),
    );
    if turn.input.asks_about_sound && hearing.is_empty() && named.is_empty() && sources.is_empty() {
        return Ok(After::Fixed(Fixed {
            text: crate::environment::ambient::general_text("chat", "no_hearing_evidence"),
            reason: "no_hearing_evidence",
            announce_smell: false,
            repair: plan.repair.clone(),
        }));
    }
    let mut d = turn.base_details.clone();
    if !turn.context.workshop_open {
        let context = individuals::context(snapshot, user, &turn.look, false);
        if !context.is_empty() {
            d["named_mob_context"] = json!(context);
        }
    }
    let g = &handoff.grounding;
    let generated = json!({
        "threat_summary": threat,
        "hearing_summary": hearing,
        "hearing_named_mobs": named,
        "hearing_source_labels": sources,
        "observation_summary": observation,
        "player_chat_plan_action": plan.action,
        "player_chat_plan_focus": plan.focus,
        "player_chat_plan_source": plan.source,
        "conversation_repair": plan.repair.as_ref().map(|r|json!({"target_turn_id":r.target_turn_id,
        "target_quote": r.target_quote,
        "replacement_quote": r.replacement_quote})),
        "player_chat_plan_evidence": plan.evidence,
        "entity_query": g.query,
        "entity_grounding_status": g.status,
        "entity_candidate_ids": g.candidate_ids,
        "entity_candidate_labels": g.candidate_labels,
        "entity_observed_ids": g.observed_ids,
        "entity_observed_labels": g.observed_labels,
        "catalog_topic_hints": handoff.catalog_topic_hints.as_deref().unwrap_or(""),
        "catalog_topic_ids": selected.iter().map(|r|r.entry_id.clone()).collect::<Vec<_>>(),
        "reply_stance": topics.policy.reply_stance,
        "reply_policy": topics.policy.reply_policy,
        "allowed_speech_labels": names.allowed_speech_labels,
        "speech_whitelist_enforce": true,
        "speech_name_corrections": names.speech_name_corrections,
        "identify_skeleton": topics.identify_skeleton.as_deref().unwrap_or(""),
        "plausibility_hints": plausibility.hints,
        "look_target_label": turn.look,
        "look_target_kind": event.look_target.as_ref().filter(|_|!turn.look.is_empty()).map(|r|r.kind.as_str()).unwrap_or(""),
        "look_target_name": event.look_target.as_ref().filter(|_|!turn.look.is_empty()).map(|r|r.name.as_str()).unwrap_or(""),
        "nearby_hostile_types": turn.tactics.nearby_hostile_types,
        "mob_tactics_notes": turn.tactics.notes,
        "forbidden_advice": turn.tactics.forbidden_advice,
        "safe_hints": turn.tactics.safe_hints
    });
    d.as_object_mut()
        .unwrap()
        .extend(generated.as_object().unwrap().clone());
    if !turn.context.workshop_open
        && let Some(hints) = descriptions::named_mob(user)
    {
        d["named_entity_description_hints"] = json!(hints);
    }
    if !turn.context.workshop_open
        && plan.action == Action::IdentifyEntity
        && g.status == "observed"
        && let [label] = g.observed_labels.as_slice()
        && !turn.look.is_empty()
        && label == &turn.look
    {
        d["required_identification_label"] = json!(label);
    }
    if turn.context.workshop_open {
        for key in [
            "look_target_label",
            "look_target_kind",
            "look_target_name",
            "catalog_topic_hints",
            "plausibility_hints",
            "identify_skeleton",
        ] {
            d[key] = json!("")
        }
        d["catalog_topic_ids"] = json!([]);
        d["reply_stance"] = json!("none");
        d["reply_policy"] = json!(chat_prompt::reply_policy_line("none"));
        d["speech_whitelist_enforce"] = json!(false);
        d["allowed_speech_labels"] = json!([]);
        if !turn.input.asks_about_sound {
            d["hearing_summary"] = json!("");
            d["hearing_named_mobs"] = json!([]);
            d["hearing_source_labels"] = json!([])
        }
        if !turn.input.asks_inventory {
            d["inventory_summary"] = json!("");
            d["held_item_label"] = json!("")
        }
        d["observation_summary"] = json!(current::observation(
            event,
            &threat,
            d["hearing_summary"].as_str().unwrap_or(""),
            &[],
            "",
            &[]
        ));
    }
    if !handoff.fixed_reply.is_empty() {
        return Ok(After::Fixed(Fixed {
            text: handoff.fixed_reply.clone(),
            reason: "grounded_reply",
            announce_smell: false,
            repair: plan.repair.clone(),
        }));
    }
    let fallback = plan
        .repair
        .as_ref()
        .map(|r| r.fallback())
        .unwrap_or_else(|| {
            d["required_identification_label"]
                .as_str()
                .map(|label| format!("それは{label}やで。"))
                .unwrap_or_else(|| turn.fallback.clone())
        });
    ensure!(d.is_object(), "chat details lost shape");
    Ok(After::Leaf(Box::new(Leaf {
        details: d,
        fallback_text: fallback,
        repair: plan.repair.clone(),
        handoff_input: input,
        handoff,
    })))
}

#[cfg(test)]
mod tests;
