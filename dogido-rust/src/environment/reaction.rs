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
        "ambient" => "mob",
        "thunder_reaction" => "thunder",
        "weather_transition" => "weather",
        "smell" => "smell",
        "special_biome_entry"
        | "foliage_shade"
        | "occluded_entry_with_light"
        | "occluded_entry_no_light" => "place",
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
        "mob" => {
            // No all-mob count, poetic tags, example sentences or inferred daily
            // schedule is promoted to the selected animal's observed behaviour.
            for key in [
                "mob",
                "direction",
                "mob_temperament",
                "mob_caution_reason",
                "mob_profession",
                "mob_is_baby",
            ] {
                if let Some(value) = details.get(key).filter(|v| !v.is_null()) {
                    observations[key] = value.clone();
                }
            }
            properties["temperament"] = observations["mob_temperament"].clone();
        }
        "thunder" => {
            let heard = event.world.thunder_sound_recent_ms.is_some_and(|age| {
                age >= 0 && age as u64 <= settings.ms("weather_sound_recent_ms")
            });
            observations = json!({"thunder_heard":heard,"nearby_lightning_observed":details["nearby_lightning"] == true});
            properties["note"] = "雷鳴だけでは落雷地点や距離は未確認。".into();
        }
        "weather" => {
            let sky = event.world.sky_visible == Some(true);
            observations = json!({"weather_before":details["weather_from"],"weather_now":details["weather_to"],"sky_visible":sky});
            let scene = details["scene"].as_str().unwrap_or("");
            observations["rain_heard"] = (scene == "rain_suspected").into();
            observations["thunder_heard"] = (scene == "thunder_suspected").into();
            if sky {
                let precipitation = precipitation_kind(event);
                if details["weather_to"] == "rain" {
                    // The game weather enum covers both rain and snowfall; use
                    // the same current-altitude fact as the model observation.
                    let after_thunder = details["weather_from"] == "thunder";
                    details["scene"] = match (precipitation.as_str(), after_thunder) {
                        (Some("rain"), false) => "rain_started",
                        (Some("rain"), true) => "rain_after_thunder",
                        (Some("snow"), false) => "snow_started",
                        (Some("snow"), true) => "snow_after_thunder",
                        (Some("none"), false) => "overcast_started",
                        (Some("none"), true) => "overcast_after_thunder",
                        _ => "weather_changed",
                    }
                    .into();
                }
                observations["local_precipitation"] = precipitation.clone();
                guard["precipitation"] = precipitation;
            }
            guard["sky_visible"] = json!(event.world.sky_visible);
            properties["note"] = "weatherはゲームの天候区分。local_precipitationは現在地で確認した降水。空が見えない場合は音からの観測。雷雨区分だけでは雷鳴や落雷の観測根拠にならない。".into();
        }
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
                "entered_foliage_shade":action.kind == "foliage_shade",
            });
            guard["biome"] = biome.clone().into();
            guard["time_phase"] = json!(event.world.time_phase);
            guard["sky_visible"] = json!(event.world.sky_visible);
            guard["overhead_cover"] = json!(event.world.overhead_cover_type);
            guard["biome_scene_visible"] = json!(projection.include_biome_context);
            properties["note"] = if projection.cave_biome || action.kind.starts_with("occluded_entry") {
                "地上の昼夜と、日光の届かへん場所の明るさは別やで。昼でも暗い場所があるが、それだけでは現在の敵の在否は未確認。"
            } else if action.kind == "foliage_shade" || ["forest", "taiga", "jungle", "grove", "pale_garden"].iter().any(|s| biome.contains(s)) {
                "木陰には昼でも日光が届かへん場所があるで。所在地のバイオームだけやと、見える木々や今の敵の在否までは分からへんで。"
            } else {
                "所在地のバイオームは種類の情報や。biome_scene_visibleがfalseやったら、地表の景色はまだ見えてへんで。今そこに敵がおるかどうかは、別の観測が要るで。"
            }.into();
            // Old catalogue lines sometimes asserted enemies from a biome alone.
            // They remain comparison data, never the new request or its fallback.
            action.text = match action.kind {
                "foliage_shade" => "木陰は昼でも気になるな。気ぃつけて進もな。",
                "occluded_entry_with_light" | "occluded_entry_no_light" => {
                    "日が届かん場所は落ち着かんな。足元、気ぃつけよ。"
                }
                _ => "ここ、雰囲気が変わったな。ちょっと気になるわ。",
            }
            .into();
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

fn precipitation_kind(event: &GameEvent) -> Value {
    crate::world_catalog::catalog()
        .climate(event.world.biome.as_deref())
        .and_then(|climate| super::precipitation::from_event(event, &climate))
        .map(|p| json!(p.precipitation_kind))
        .unwrap_or_else(|_| json!("unknown"))
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
        || g.get("precipitation")
            .is_some_and(|p| *p != precipitation_kind(event))
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
            Some("weather") => Some("weather_transition"),
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
                Some("mob" | "thunder" | "weather" | "smell" | "place")
            ),
            "unknown reaction topic"
        );
        let request = GenerationRequest {
            schema_version: 1,
            kind: KIND.into(),
            model: model.into(),
            messages: vec![
                ChatMessage {
                    role: Role::System,
                    content: crate::companion_prompt::dialogue("normal"),
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
