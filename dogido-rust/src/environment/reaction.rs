//! One model decision at a code-owned environmental reaction opportunity.
//! Observation, cooldowns, interruption, movement and persistence stay outside.
use crate::{
    combat::model::{Delivery, LeafRequest, Settings, Speech},
    events::GameEvent,
    types::{ChatMessage, GeneratedText, GenerationRequest, Role},
};
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};

pub(crate) const KIND: &str = "environment_reaction";
const SYSTEM: &str = "あんたはMinecraftの怖がりな相棒、ドギドや。一人称は『オレ』。親しみのある自然な関西弁で話してな。\n\
今は環境の変化に気づいた場面や。ここでひとこと話すか、黙って一緒におるか、自分で選んでな。\
話すんやったら、何に心が動いたかを選んで、感想・気遣い・問いかけなど自然な反応を返してな。長さも会話の流れに合わせてええし、毎回実況や注意をせんでもええで。\
直近の会話に自然につながるなら、つなげてええで。会話履歴は前に話したことで、今の世界を見た証拠にはせんといてな。\n\
observationsが今回観測できたことや。propertiesは種類や場所の一般的な性質で、今そこで起きた出来事とは分けて考えてな。\
観測してへん敵の存在・不在、位置・個数、動作、匂いの発生源を勝手に足さんといてな。\
友好的な動物に『触るな』と行動を禁止せんといてな。中立の動物には、優しく接しよか、くらいでええで。\
雷の驚き声は別に出るから、悲鳴を繰り返さんといてな。操作や保存を実行したことにもせんといてな。\n\
JSONを一つだけ返してな。話すときは {\"action\":\"speak\",\"speech\":\"自然なひとこと\"}、\
黙るときは {\"action\":\"silent\",\"speech\":\"\"} や。ほかのキーや説明文は付けんといてな。";

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
        "weather_transition"
            if action
                .leaf
                .as_ref()
                .is_some_and(|l| l.details["thunder_reaction"] == true) =>
        {
            "thunder"
        }
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
            properties["temperament"] = details["mob_temperament"].clone();
        }
        "thunder" => {
            let heard = event.world.thunder_sound_recent_ms.is_some_and(|age| {
                age >= 0 && age as u64 <= settings.ms("weather_sound_recent_ms")
            });
            observations = json!({"thunder_heard":heard,"nearby_lightning_observed":details["nearby_lightning"] == true});
            properties["note"] = "雷鳴だけやと、落ちた場所や距離は分からへんで。".into();
        }
        "weather" => {
            let sky = event.world.sky_visible == Some(true);
            observations = json!({"weather_before":details["weather_from"],"weather_now":details["weather_to"],"sky_visible":sky});
            let scene = details["scene"].as_str().unwrap_or("");
            observations["rain_heard"] = (scene == "rain_suspected").into();
            observations["thunder_heard"] = (scene == "thunder_suspected").into();
            if sky {
                let precipitation = precipitation_kind(event);
                observations["local_precipitation"] = precipitation.clone();
                guard["precipitation"] = precipitation;
            }
            guard["sky_visible"] = json!(event.world.sky_visible);
            properties["note"] = "weatherはゲームの天候区分や。実際の雨・雪はlocal_precipitationを見てな。空が見えへんときは、音から気づいたことまでにしといてな。天候が雷雨というだけで、雷鳴や落雷を見聞きしたことにはせんといてな。".into();
        }
        "smell" => {
            let Some(smell) = super::ambient::smell_context(event) else {
                return;
            };
            guard["smell"] = smell.clone();
            observations["smell"] = smell.clone();
            properties["note"] = "匂いの観測が一件あるで。specificityがcategoryやmixedやったら、特定の花・食べ物・モブの匂いやと決めんといてな。方向・距離・個数・姿は分からへんで。".into();
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
                "地上の昼夜と、日光の届かへん場所の明るさは別やで。昼でも暗い場所は気になるけど、それだけで今そこに敵がおるとは分からへんで。"
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

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Action {
    Speak,
    Silent,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Reply {
    action: Action,
    speech: String,
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
            .context("reaction context")?;
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
                    content: SYSTEM.into(),
                },
                ChatMessage {
                    role: Role::User,
                    content: serde_json::to_string(context)?,
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
        // Parse the whole response: never salvage a child object from cut-off JSON.
        let Ok(reply) = serde_json::from_str::<Reply>(generated.text.trim()) else {
            return (self.fallback.clone(), "invalid_contract");
        };
        match reply.action {
            Action::Silent if reply.speech.is_empty() => (String::new(), "silent"),
            Action::Speak => {
                let text = reply.speech.trim();
                if broken_output(text) {
                    return (self.fallback.clone(), "broken_output");
                }
                (text.to_owned(), "speak")
            }
            _ => (self.fallback.clone(), "invalid_contract"),
        }
    }
}

// Check transport/decoding damage and unmistakable loops, not vocabulary,
// length, dialect, language choice, or the meaning of the model's reaction.
fn broken_output(text: &str) -> bool {
    if text.is_empty()
        || text
            .chars()
            .any(|c| c == '\u{fffd}' || c.is_control() && !matches!(c, '\n' | '\r' | '\t'))
        || ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]
            .iter()
            .any(|p| text.contains(p))
    {
        return true;
    }
    let sentences: Vec<_> = text
        .split(['。', '！', '？', '\n'])
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();
    if sentences.len() >= 3 && sentences.iter().all(|s| *s == sentences[0]) {
        return true;
    }
    let chars: Vec<_> = text.chars().filter(|c| !c.is_whitespace()).collect();
    chars.len() >= 24
        && (1..=chars.len() / 4).any(|width| {
            chars.len().is_multiple_of(width)
                && chars
                    .iter()
                    .enumerate()
                    .all(|(i, c)| *c == chars[i % width])
        })
}

#[cfg(test)]
mod tests;
