use super::*;
use serde::Serialize;
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LightContext {
    pub surroundings_reasonably_lit: bool,
    pub severe_darkness: bool,
    pub nearby_light_present: bool,
    pub dark_push_context_before: bool,
    pub dark_push_recovered: bool,
}
#[derive(Clone, Debug, Serialize)]
pub struct LightPlanRequest {
    pub request_id: u64,
    pub details: Value,
    pub fallback_payload: Value,
    #[serde(skip)]
    created_at: u64,
    #[serde(skip)]
    previous: i64,
    #[serde(skip)]
    current: i64,
    #[serde(skip)]
    context: LightContext,
}
pub(super) fn count(e: &GameEvent) -> i64 {
    ["torch", "soul_torch", "lantern", "soul_lantern"]
        .iter()
        .map(|key| e.inventory.get(*key).copied().unwrap_or(0))
        .sum()
}
fn band(n: i64) -> &'static str {
    if n <= 0 {
        "empty"
    } else if n < 8 {
        "low"
    } else if n < 32 {
        "enough"
    } else {
        "abundant"
    }
}
pub(super) fn allowed(
    previous: i64,
    current: i64,
    c: &LightContext,
    recent: bool,
) -> Vec<&'static str> {
    let mut out = vec!["stay_silent"];
    if current <= previous || recent {
        return out;
    }
    if c.dark_push_recovered && c.dark_push_context_before {
        out.push("relief_after_darkness");
        return out;
    }
    if current >= 32 && c.surroundings_reasonably_lit || c.severe_darkness && !c.dark_push_recovered
    {
        return out;
    }
    if previous <= 0 && current > 0 || current < 32 && !c.surroundings_reasonably_lit {
        out.push("acknowledge_supply_gain");
    }
    out
}
pub(super) fn facts(previous: i64, current: i64, c: &LightContext, recent: bool) -> Value {
    let mut rows = vec![
        ("light_source_gain_observed", "true".to_owned()),
        ("supply_before", band(previous).into()),
        ("supply_after", band(current).into()),
        (
            "surroundings_light",
            if c.surroundings_reasonably_lit {
                "reasonably_lit"
            } else {
                "not_reasonably_lit"
            }
            .into(),
        ),
        ("dangerous_darkness", c.severe_darkness.to_string()),
        ("nearby_light_present", c.nearby_light_present.to_string()),
        (
            "dark_push_context_before",
            c.dark_push_context_before.to_string(),
        ),
        ("dark_push_recovered", c.dark_push_recovered.to_string()),
        ("recent_light_comment", recent.to_string()),
    ];
    if previous <= 0 && current > 0 {
        rows.push(("first_light_supply", "true".into()));
    }
    Value::Array(
        rows.into_iter()
            .map(|(id, value)| json!({"basis_id":id,"value":value}))
            .collect(),
    )
}
impl Ambient {
    pub(super) fn light_request(
        &mut self,
        _e: &GameEvent,
        now: u64,
        focus: &AmbientFocus,
        s: &Settings,
    ) -> bool {
        let Some((previous, current)) = self.gain.take() else {
            return false;
        };
        let recent = !elapsed(
            now,
            self.last_light_comment,
            s.ms("darkness_llm_comment_cooldown_ms"),
        );
        let actions = allowed(previous, current, &focus.light, recent);
        if actions.len() == 1 {
            return false;
        }
        self.light_serial = self.light_serial.saturating_add(1);
        let request = LightPlanRequest {
            request_id: self.light_serial,
            details: json!({"allowed_actions":actions,"facts":facts(previous,current,&focus.light,recent)}),
            fallback_payload: json!({"action":"stay_silent","basis_ids":["light_source_gain_observed"],"confidence":0.0}),
            created_at: now,
            previous,
            current,
            context: focus.light.clone(),
        };
        self.light_pending = Some(request.clone());
        self.light_ready = Some(request);
        true
    }
    pub fn resolve_light_plan(
        &mut self,
        id: u64,
        payload: &Value,
        e: &GameEvent,
        now: u64,
        focus: &AmbientFocus,
        s: &Settings,
    ) -> Option<Speech> {
        if self
            .light_pending
            .as_ref()
            .is_none_or(|r| r.request_id != id)
        {
            return None;
        }
        let request = self.light_pending.take()?;
        if self
            .light_ready
            .as_ref()
            .is_some_and(|r| r.request_id == id)
        {
            self.light_ready = None;
        }
        if focus.foreground
            || focus.player_priority
            || focus.boss_presence
            || !e.visual_threats.is_empty()
            || !e.auditory_threats.is_empty()
            || count(e) != request.current
            || focus
                .last_player_input_at
                .or(self.last_player_input)
                .is_some_and(|at| at > request.created_at)
        {
            return None;
        }
        let status = payload["__dogido_status"].as_str().unwrap_or("accepted");
        if status != "accepted" {
            return None;
        }
        let action = payload["action"].as_str()?;
        if !request.details["allowed_actions"]
            .as_array()?
            .iter()
            .any(|v| v.as_str() == Some(action))
        {
            return None;
        }
        let recent = !elapsed(
            now,
            self.last_light_comment,
            s.ms("darkness_llm_comment_cooldown_ms"),
        );
        let mut current_context = focus.light.clone();
        current_context.dark_push_context_before = request.context.dark_push_context_before;
        current_context.dark_push_recovered =
            request.context.dark_push_recovered && !focus.light.severe_darkness;
        if !allowed(request.previous, request.current, &current_context, recent).contains(&action) {
            return None;
        }
        let basis = payload["basis_ids"].as_array()?;
        if !(1..=3).contains(&basis.len()) {
            return None;
        }
        let mut ids = vec![];
        for v in basis {
            let id = v.as_str()?.trim();
            if id.is_empty()
                || ids.contains(&id)
                || !request.details["facts"]
                    .as_array()?
                    .iter()
                    .any(|f| f["basis_id"].as_str() == Some(id))
            {
                return None;
            }
            ids.push(id);
        }
        if action == "relief_after_darkness" && !ids.contains(&"dark_push_recovered") {
            return None;
        }
        if action == "acknowledge_supply_gain"
            && !ids.iter().any(|id| {
                ["first_light_supply", "supply_before", "surroundings_light"].contains(id)
            })
        {
            return None;
        }
        let confidence = payload["confidence"].as_f64()?;
        if !confidence.is_finite()
            || !(0.0..=1.0).contains(&confidence)
            || action != "stay_silent" && confidence < 0.78
            || action == "stay_silent"
        {
            return None;
        }
        let name = crate::combat::core::call_name(e, s);
        let prefix = if name == "プレイヤー" {
            String::new()
        } else {
            format!("{name}、")
        };
        let key = if action == "relief_after_darkness" {
            "light_source_relief"
        } else {
            "light_source_gain"
        };
        let text = catalog::general("darkness", key).replace("{prefix}", &prefix);
        let mut details = common_details(e, s);
        details["comment_action"] = action.into();
        details["surroundings_light"] = if focus.light.surroundings_reasonably_lit {
            "reasonably_lit"
        } else {
            "not_reasonably_lit"
        }
        .into();
        details["__ambient_guard"] = json!({"light_source_count":request.current});
        self.last_light_comment = Some(now);
        Some(leaf("light_source_gain", text, details, 0.48))
    }
}
