//! One model decision at a code-owned environmental reaction opportunity.
//! Observation, cooldowns, interruption, movement and persistence stay outside.
use crate::{
    combat::model::{Delivery, LeafRequest, Settings, Speech},
    events::GameEvent,
    types::{ChatMessage, GeneratedText, GenerationRequest, Role},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};

pub(crate) const KIND: &str = "environment_reaction";
fn normalized(value: Option<&str>) -> String {
    value
        .unwrap_or("")
        .trim()
        .trim_start_matches("minecraft:")
        .to_lowercase()
}

pub(crate) fn is_model_reaction(action: &Speech) -> bool {
    action.leaf.as_ref().is_some_and(|l| l.kind == KIND)
}

/// Decorate only the selected environmental candidate. Its original domain state
/// has consumed a consideration opportunity, not asserted successful playback.
pub(crate) fn attach(action: &mut Speech, event: &GameEvent, recent: &Value, settings: &Settings) {
    if !matches!(
        action.delivery,
        Delivery::Ambient | Delivery::UrgentEnvironment
    ) {
        return;
    }
    let topic = match action.kind {
        "surroundings_notice" => "surroundings",
        "thunder_reaction" => "thunder",
        "smell" => "smell",
        "occluded_entry_with_light" | "occluded_entry_no_light" => "place",
        _ => return,
    };
    let mut details = action
        .leaf
        .as_ref()
        .map(|l| l.details.clone())
        .unwrap_or_else(|| json!({}));
    let mut observations = json!({});
    let mut properties = json!({});
    let mut guard = json!({"dimension":normalized(event.player.dimension.as_deref())});
    match topic {
        "thunder" => {
            let heard = event.world.thunder_sound_recent_ms.is_some_and(|age| {
                age >= 0 && age as u64 <= settings.ms("weather_sound_recent_ms")
            });
            observations = json!({"thunder_heard":heard,"nearby_lightning_observed":details["nearby_lightning"] == true});
            properties["note"] = "雷鳴だけでは落雷地点や距離は未確認。".into();
        }
        "surroundings" => {}
        "smell" => {
            let Some(smell) = super::ambient::smell_context(event) else {
                return;
            };
            guard["smell"] = smell.clone();
            observations["smell"] = smell.clone();
            properties["note"] = "specificityは匂いの識別範囲。categoryは種類まで、mixedは混合。direction_estimateは最も強く匂う大まかな方角。姿・距離・個数は匂いからは未確認。".into();
            // The fixed smell cue would otherwise replace the generated words.
            // Explicit player questions bypass this function and retain the cue.
            action.cue_id = None;
        }
        "place" => {
            let biome = normalized(event.world.biome.as_deref());
            let projection = super::projection::project_environment(event);
            observations = json!({
                "location_biome":super::ambient::biome_label(&biome),
                "biome_scene_visible":projection.include_biome_context,
                "sky_visible":event.world.sky_visible,
                "time_phase":event.world.time_phase,
                "overhead_cover":event.world.overhead_cover_type,
                "entered_occluded_place":action.kind.starts_with("occluded_entry"),
            });
            guard["biome"] = biome.clone().into();
            guard["time_phase"] = json!(event.world.time_phase);
            guard["sky_visible"] = json!(event.world.sky_visible);
            guard["overhead_cover"] = json!(event.world.overhead_cover_type);
            guard["biome_scene_visible"] = json!(projection.include_biome_context);
            properties["note"] = "日光の届かへん場所へ入った実観測やで。所在地のバイオームだけでは地表の景色までは分からへんで。".into();
            action.text = "日が届かん場所は落ち着かんな。足元、気ぃつけよ。".into();
        }
        _ => unreachable!(),
    }
    details["__reaction_guard"] = guard;
    details["reaction_context"] =
        json!({"topic":topic,"observations":observations,"properties":properties,"recent":recent});
    action.leaf = Some(LeafRequest {
        kind: KIND.into(),
        details,
        temperature: 0.65,
    });
}

/// A new estimate of the same smell may replace an unplayed estimate once.
/// Absence, changed smell identity or other stale guards are not this case.
pub(crate) fn only_smell_spatial_changed(action: &Speech, event: &GameEvent) -> bool {
    let Some(leaf) = action
        .leaf
        .as_ref()
        .filter(|l| l.kind == KIND && action.kind == "smell")
    else {
        return false;
    };
    let Some(current) = super::ambient::smell_context(event) else {
        return false;
    };
    let Some(old) = leaf.details["__reaction_guard"].get("smell") else {
        return false;
    };
    let mut before = old.clone();
    let mut after = current.clone();
    for value in [&mut before, &mut after] {
        let Some(object) = value.as_object_mut() else {
            return false;
        };
        object.remove("direction_estimate");
    }
    if old == &current || before != after {
        return false;
    }
    let mut updated = action.clone();
    updated.leaf.as_mut().unwrap().details["__reaction_guard"]["smell"] = current;
    still_applicable(&updated, event)
}

/// These guards never use generated wording as observation identity.
pub(crate) fn still_applicable(action: &Speech, event: &GameEvent) -> bool {
    let Some(leaf) = action.leaf.as_ref().filter(|l| l.kind == KIND) else {
        return true;
    };
    let g = &leaf.details["__reaction_guard"];
    if g["dimension"]
        .as_str()
        .is_some_and(|d| !d.is_empty() && d != normalized(event.player.dimension.as_deref()))
    {
        return false;
    }
    if g.get("smell")
        .is_some_and(|s| Some(s.clone()) != super::ambient::smell_context(event))
    {
        return false;
    }
    if g["biome"]
        .as_str()
        .is_some_and(|b| b != normalized(event.world.biome.as_deref()))
        || g.get("time_phase")
            .is_some_and(|p| *p != json!(event.world.time_phase))
        || g.get("sky_visible")
            .is_some_and(|v| *v != json!(event.world.sky_visible))
        || g.get("overhead_cover")
            .is_some_and(|v| *v != json!(event.world.overhead_cover_type))
        || g.get("biome_scene_visible").is_some_and(|v| {
            *v != json!(super::projection::project_environment(event).include_biome_context)
        })
    {
        return false;
    }
    true
}

pub(crate) struct Prepared {
    pub request: GenerationRequest,
    fallback: String,
}
impl Prepared {
    pub(crate) fn new(input: &Value, model: &str) -> Result<Self> {
        ensure!(
            input["kind"] == KIND && input["model"] == model,
            "environment reaction route mismatch"
        );
        let context = input["details"]["reaction_context"]
            .as_object()
            .context("reaction context")?
            .clone();
        let mut context = context;
        crate::catalog_knowledge::separate(&mut context);
        crate::villager_routines::separate(&mut context);
        if let Some(kind) = match context.get("topic").and_then(Value::as_str) {
            Some("thunder") => Some("thunder_reaction"),
            _ => None,
        } {
            let shared = crate::reaction_leaf::context(kind, &input["details"])?;
            for key in ["event", "situation", "self_state"] {
                if let Some(value) = shared.get(key) {
                    context.insert(key.into(), value.clone());
                }
            }
        }
        ensure!(
            matches!(
                context.get("topic").and_then(Value::as_str),
                Some("surroundings" | "thunder" | "smell" | "place")
            ),
            "unknown reaction topic"
        );
        if context.get("topic").and_then(Value::as_str) == Some("surroundings") {
            let shared = crate::reaction_leaf::context("surroundings_notice", &input["details"])?;
            context.insert("situation".into(), shared["situation"].clone());
        }
        let request = GenerationRequest {
            schema_version: 1,
            kind: KIND.into(),
            model: model.into(),
            messages: vec![
                ChatMessage {
                    role: Role::System,
                    content: crate::companion_prompt::dialogue(
                        "normal",
                        crate::companion_prompt::DialoguePurpose::Ambient,
                    ),
                },
                ChatMessage {
                    role: Role::User,
                    content: serde_json::to_string(&context)?,
                },
            ],
            temperature: 0.65,
            max_tokens: input["max_tokens"]
                .as_u64()
                .context("reaction output budget")?,
            enable_thinking: false,
        };
        request.validate()?;
        Ok(Self {
            request,
            fallback: input["fallback_text"]
                .as_str()
                .context("reaction fallback")?
                .into(),
        })
    }
    pub(crate) fn finish(&self, generated: Option<&GeneratedText>) -> (String, &'static str) {
        let Some(generated) = generated else {
            return (self.fallback.clone(), "generation_error");
        };
        if generated.finish_reason.as_deref() == Some("length") {
            return (self.fallback.clone(), "truncated_output");
        }
        match crate::speech_choice::parse(&generated.text) {
            Ok(crate::speech_choice::Choice::Silent) => (String::new(), "silent"),
            Ok(crate::speech_choice::Choice::Speak(text)) => (text, "speak"),
            Err(reason) => (self.fallback.clone(), reason),
        }
    }
}

#[cfg(test)]
mod tests;
