use super::{SELECT_HOTBAR_CAPABILITY, intent, selection::*};
use crate::events::{
    AdapterCommandResult, AdapterCommandResultCommandType, AdapterCommandResultStatus, EventTime,
    GameEvent,
};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet, VecDeque};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InputSource {
    #[default]
    Text,
    Voice,
}
/// Raw text remains the authority. Interpreted text is accepted only on the voice path.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Input {
    pub raw_text: String,
    #[serde(default)]
    pub source: InputSource,
    #[serde(default)]
    pub interpreted_text: Option<String>,
    #[serde(default)]
    pub knowledge_query: bool,
    #[serde(default)]
    pub workshop_open: bool,
    #[serde(default)]
    pub workshop_combat_paused: bool,
}
#[derive(Clone, Debug, Serialize)]
pub struct IntentEvidence {
    pub source: String,
    pub evidence: String,
    pub confidence: f64,
}
#[derive(Clone, Debug, Serialize)]
pub struct Dispatch {
    pub intent: IntentEvidence,
    pub status: String,
    pub detail_code: String,
    pub command: Option<SelectHotbarCommand>,
    pub feedback: Option<String>,
}
/// Constructed only by submit. The private epoch prevents stale/replayed completions.
#[derive(Clone, Debug, Serialize)]
pub struct PreparedIntent {
    pub player_text: String,
    pub messages: Vec<intent::Message>,
    pub schema: Value,
    pub max_tokens: u32,
    pub temperature: f64,
    #[serde(skip)]
    epoch: u64,
    #[serde(skip)]
    owner: uuid::Uuid,
    #[serde(skip)]
    input: Input,
}
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", content = "value", rename_all = "snake_case")]
pub enum Submission {
    NotHandled,
    Handled(Dispatch),
    NeedsIntent(PreparedIntent),
}
#[derive(Clone, Debug, Serialize)]
pub struct ObservedResult {
    pub result: AdapterCommandResult,
    /// An unknown ID is acknowledged but is not evidence of an operation we issued.
    pub known_command: bool,
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct Results {
    pub acknowledged_ids: Vec<String>,
    pub observed: Vec<ObservedResult>,
    pub feedback: Option<String>,
}
#[derive(Clone, Debug)]
pub struct AssistState {
    capabilities: BTreeSet<String>,
    pending: BTreeMap<String, SelectHotbarCommand>,
    seen: BTreeSet<String>,
    seen_order: VecDeque<String>,
    last_issued_at: Option<DateTime<Utc>>,
    last_succeeded: bool,
    retired_results: Vec<ObservedResult>,
    epoch: u64,
    owner: uuid::Uuid,
    policy: RiskPolicy,
}
impl AssistState {
    pub fn new(capabilities: impl IntoIterator<Item = String>) -> Self {
        Self {
            capabilities: capabilities.into_iter().collect(),
            pending: BTreeMap::new(),
            seen: BTreeSet::new(),
            seen_order: VecDeque::new(),
            last_issued_at: None,
            last_succeeded: false,
            retired_results: Vec::new(),
            epoch: 0,
            owner: uuid::Uuid::new_v4(),
            policy: RiskPolicy::Auto,
        }
    }
    pub fn set_policy(&mut self, policy: RiskPolicy) {
        self.policy = policy;
    }
    /// Call on a superseding turn, session reset, or rejected/stale snapshot.
    pub fn invalidate_intent(&mut self) {
        self.epoch = self.epoch.wrapping_add(1);
    }
    pub fn submit(
        &mut self,
        input: Input,
        event: &GameEvent,
        now: DateTime<Utc>,
        has_existing_speech: bool,
    ) -> Submission {
        self.invalidate_intent();
        let expired = self.expire_pending(now);
        self.retired_results.extend(expired);
        let normalized = intent::normalize(&input.raw_text);
        if normalized.starts_with('/') {
            return Submission::NotHandled;
        }
        let repaired = if input.source == InputSource::Voice {
            input
                .interpreted_text
                .as_deref()
                .filter(|s| !s.trim().is_empty())
                .map(intent::normalize)
                .or_else(|| intent::interpret_voice_select_sword_request(&normalized))
        } else {
            None
        };
        let mut command_text = normalized.clone();
        let mut source = None;
        if intent::is_explicit_select_sword_request(&normalized) {
            source = Some("code");
        } else if let Some(text) = repaired
            .as_deref()
            .filter(|s| intent::is_explicit_voice_select_sword_request(s))
        {
            command_text = text.into();
            source = Some("code_voice_asr");
        }
        if input.knowledge_query
            || workshop_owns(&input, &command_text, source == Some("code_voice_asr"))
        {
            return Submission::NotHandled;
        }
        if let Some(source) = source {
            return Submission::Handled(self.issue(
                IntentEvidence {
                    source: source.into(),
                    evidence: command_text,
                    confidence: 1.0,
                },
                event,
                now,
                has_existing_speech,
            ));
        }
        let player_text = if input.source == InputSource::Voice {
            repaired.unwrap_or(normalized)
        } else {
            normalized
        };
        if player_text.is_empty()
            || !intent::mentions_sword_target(&player_text)
            || intent::is_bare_sword_target(&player_text)
        {
            return Submission::NotHandled;
        }
        Submission::NeedsIntent(PreparedIntent {
            messages: intent::messages(&player_text),
            schema: intent::schema(),
            player_text,
            max_tokens: 96,
            temperature: 0.0,
            epoch: self.epoch,
            owner: self.owner,
            input,
        })
    }
    /// The caller checks current snapshot freshness before completion. Model failure is NotHandled.
    /// Current ownership is checked again because generation runs outside the event worker.
    pub fn complete_intent(
        &mut self,
        prepared: PreparedIntent,
        payload: &Value,
        current_input: &Input,
        event: &GameEvent,
        now: DateTime<Utc>,
        has_existing_speech: bool,
    ) -> Submission {
        if prepared.epoch != self.epoch || prepared.owner != self.owner {
            return Submission::NotHandled;
        }
        self.invalidate_intent();
        if prepared.input.raw_text != current_input.raw_text
            || prepared.input.source != current_input.source
            || prepared.input.interpreted_text != current_input.interpreted_text
            || current_input.knowledge_query
            || workshop_owns(current_input, &prepared.player_text, false)
        {
            return Submission::NotHandled;
        }
        if !intent::contract_errors(payload).is_empty() {
            return Submission::NotHandled;
        }
        let Some(intent) = intent::validate_payload(payload, &prepared.player_text, 0.90) else {
            return Submission::NotHandled;
        };
        Submission::Handled(self.issue(intent, event, now, has_existing_speech))
    }
    fn issue(
        &mut self,
        intent: IntentEvidence,
        event: &GameEvent,
        now: DateTime<Utc>,
        has_speech: bool,
    ) -> Dispatch {
        let expired = self.expire_pending(now);
        self.retired_results.extend(expired);
        let mut out = Dispatch {
            intent,
            status: "unavailable".into(),
            detail_code: String::new(),
            command: None,
            feedback: None,
        };
        let say = |s: &str| (!has_speech).then(|| s.to_owned());
        if self.pending.values().any(|c| c.expires_at > now) {
            out.status = "pending".into();
            out.detail_code = "command_pending".into();
            out.feedback = say("いま持ち替え中やで！");
            return out;
        }
        if self.last_succeeded
            && self
                .last_issued_at
                .is_some_and(|t| now - t < Duration::seconds(3))
        {
            out.status = "cooldown".into();
            out.detail_code = "command_cooldown".into();
            out.feedback = say("もう持ち替えたで！");
            return out;
        }
        let selected = event
            .player
            .hotbar
            .as_ref()
            .and_then(|h| select_weapon_slot(&h.slots));
        match gate(
            self.policy,
            self.capabilities.contains(SELECT_HOTBAR_CAPABILITY),
            selected.as_ref(),
        ) {
            Gate::Denied => {
                out.status = "denied".into();
                out.detail_code = "policy_denied".into();
            }
            Gate::CapabilityMissing => out.detail_code = "capability_missing".into(),
            Gate::NoCandidate => out.detail_code = "no_candidate".into(),
            Gate::ConfirmRequired => {
                out.status = "confirm_required".into();
                out.detail_code = "confirmation_required".into();
            }
            Gate::Command => {
                let selection = selected.expect("gate established candidate");
                let command = selection.command(now);
                self.pending
                    .insert(command.command_id.clone(), command.clone());
                self.last_issued_at = Some(now);
                self.last_succeeded = false;
                out.status = "command".into();
                out.feedback = say(if selection.used_fallback {
                    "剣がない！ これでどうや！？"
                } else {
                    "剣やな、持ち替えるで！"
                });
                out.command = Some(command);
                return out;
            }
        }
        out.feedback = say(if out.detail_code == "capability_missing" {
            "今のアダプターでは、まだ持ち替え操作が使えへんで。"
        } else {
            "剣も代わりの武器も、ホットバーにないで！"
        });
        out
    }
    fn remember_result(&mut self, id: &str) -> bool {
        if self.seen.contains(id) {
            return true;
        }
        if self.seen_order.len() == 2048
            && let Some(old) = self.seen_order.pop_front()
        {
            self.seen.remove(&old);
        }
        self.seen.insert(id.into());
        self.seen_order.push_back(id.into());
        false
    }
    /// Invoke on every accepted event, including empty results, so expired commands are retired.
    pub fn observe_results(
        &mut self,
        incoming: &[AdapterCommandResult],
        now: DateTime<Utc>,
        has_existing_speech: bool,
    ) -> Results {
        let mut out = Results {
            observed: std::mem::take(&mut self.retired_results),
            ..Results::default()
        };
        for incoming in incoming {
            if !out.acknowledged_ids.contains(&incoming.command_id) {
                out.acknowledged_ids.push(incoming.command_id.clone());
            }
            if self.remember_result(&incoming.command_id) {
                continue;
            }
            let command = self.pending.remove(&incoming.command_id);
            let mut result = incoming.clone();
            if let Some(command) = &command {
                if result.status == AdapterCommandResultStatus::Succeeded {
                    let in_time = match result.executed_at {
                        EventTime::Aware(time) => {
                            time >= command.issued_at && time < command.expires_at
                        }
                        EventTime::Naive(_) => false,
                    };
                    if !in_time {
                        result.status = AdapterCommandResultStatus::Failed;
                        result.detail_code = "result_outside_validity_window".into();
                    } else if result.selected_slot != Some(command.slot)
                        || result.selected_item_id.as_deref()
                            != Some(command.expected_item_id.as_str())
                    {
                        result.status = AdapterCommandResultStatus::Failed;
                        result.detail_code = "result_mismatch".into();
                    }
                }
                self.last_succeeded = result.status == AdapterCommandResultStatus::Succeeded;
                if !self.last_succeeded {
                    self.last_issued_at = None;
                }
            }
            out.observed.push(ObservedResult {
                result,
                known_command: command.is_some(),
            });
        }
        out.observed.extend(self.expire_pending(now));
        if !has_existing_speech
            && let Some(result) = out
                .observed
                .iter()
                .filter(|o| o.known_command)
                .map(|o| &o.result)
                .find(|r| r.status != AdapterCommandResultStatus::Succeeded)
        {
            out.feedback = Some(
                if result.status == AdapterCommandResultStatus::Expired {
                    "持ち替えが間に合わへんかったわ。もう一回言うてな。"
                } else if ["expected_item_mismatch", "result_mismatch"]
                    .contains(&result.detail_code.as_str())
                {
                    "手元が変わったから、勝手に別の枠へは替えんかったで。"
                } else {
                    "うまく持ち替えられへんかったわ。手元を確認してな。"
                }
                .into(),
            );
        }
        out
    }
    fn expire_pending(&mut self, now: DateTime<Utc>) -> Vec<ObservedResult> {
        let mut out = Vec::new();
        let expired: Vec<_> = self
            .pending
            .values()
            .filter(|c| c.expires_at <= now)
            .map(|c| c.command_id.clone())
            .collect();
        for id in expired {
            self.pending.remove(&id);
            if self.remember_result(&id) {
                continue;
            }
            self.last_issued_at = None;
            self.last_succeeded = false;
            out.push(ObservedResult {
                known_command: true,
                result: AdapterCommandResult {
                    command_id: id,
                    command_type: AdapterCommandResultCommandType::SelectHotbar,
                    status: AdapterCommandResultStatus::Expired,
                    executed_at: EventTime::Aware(now.fixed_offset()),
                    selected_slot: None,
                    selected_item_id: None,
                    detail_code: "server_result_timeout".into(),
                    extra: BTreeMap::new(),
                },
            });
        }
        out
    }
    pub fn pending_commands(&self, now: DateTime<Utc>) -> Vec<SelectHotbarCommand> {
        let mut commands: Vec<_> = self
            .pending
            .values()
            .filter(|c| c.expires_at > now)
            .cloned()
            .collect();
        commands.sort_by(|a, b| (&a.issued_at, &a.command_id).cmp(&(&b.issued_at, &b.command_id)));
        commands
    }
}
fn workshop_owns(input: &Input, command: &str, voice_repaired: bool) -> bool {
    input.workshop_open
        && !input.workshop_combat_paused
        && !(voice_repaired && intent::is_unambiguous_voice_select_sword_request(command))
        && !intent::is_unambiguous_select_sword_request(command)
}
