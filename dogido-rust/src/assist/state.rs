//! 支援命令の配送、期限切れ、結果ACKと重複除外を所有する。
//! 対象選択は各支援ドメインへ渡し、剣と馬の実行結果・案内を発行時の種類で分ける。
use super::{SELECT_HOTBAR_CAPABILITY, horse_recall, intent, selection::*};
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
    horse_queue: horse_recall::queue::Queue,
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
            horse_queue: horse_recall::queue::Queue::default(),
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
    /// 接続時に合意したcapabilityだけを参照する。観測の鮮度と対象の照合は発行直前に行う。
    pub fn supports_horse_recall(&self) -> bool {
        self.capabilities.contains(horse_recall::CAPABILITY)
    }
    /// 同じ入力の生成完了と現在の会話所有権は呼出元が検査する。
    /// 発行済み命令は後続入力だけでは取り消さず、5秒の期限とFabricの実行前再検査に従う。
    pub fn enqueue_horse(
        &mut self,
        prepared: &horse_recall::contracts::Prepared,
        choice: &horse_recall::contracts::Choice,
        latest: &horse_recall::Snapshot,
        now: DateTime<Utc>,
    ) -> anyhow::Result<horse_recall::queue::Command> {
        anyhow::ensure!(self.supports_horse_recall(), "horse_capability_missing");
        let expired = self.expire_pending(now);
        self.retired_results.extend(expired);
        self.horse_queue.enqueue(prepared, choice, latest, now)
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
        // TODO(request-classifier): equip_sword 接続後は、この語句による即決と
        // confidence=1.0 の組み立てを分類結果へ置き換える。依頼性の二重判定を残さず、
        // issue 内のhotbar・capability・期限・実行検証は維持する。
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
        // TODO(request-classifier): 共通の分類入口へ移したら、剣の語を含む入力だけを
        // NeedsIntent へ送るこの絞り込みと、旧LLM用 PreparedIntent の組み立てを撤去する。
        // 空入力処理・原文と音声解釈の区別・入力世代の保持は移行先へ引き継ぐ。
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
        // TODO(request-classifier): 旧JSONの契約／依頼性検証は分類結果の受理へ置換する。
        // complete_intent 全体は削除せず、上の入力同一性・owner・epochの検証と
        // 下の issue への接続を、分類結果を受け取る経路にも引き継ぐ。
        if !intent::contract_errors(payload).is_empty() {
            return Submission::NotHandled;
        }
        let Some(intent) = intent::validate_payload(payload, &prepared.player_text, 0.90) else {
            return Submission::NotHandled;
        };
        Submission::Handled(self.issue(intent, event, now, has_existing_speech))
    }
    /// 共通受付で受理した剣依頼を実測検証へ渡す。確率は診断用で、追加の閾値判定はしない。
    pub fn submit_classified(
        &mut self, classification: &crate::request_classifier::Classification,
        event: &GameEvent, now: DateTime<Utc>, has_speech: bool,
    ) -> Submission {
        if classification.prediction.label != crate::request_classifier::Label::EquipSword {
            return Submission::NotHandled;
        }
        self.invalidate_intent();
        Submission::Handled(self.issue(IntentEvidence {
            source: "modernbert".into(), evidence: classification.text.clone(),
            confidence: classification.prediction.probabilities[2],
        }, event, now, has_speech))
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
                result.command_type = AdapterCommandResultCommandType::SelectHotbar;
                if incoming.command_type != AdapterCommandResultCommandType::SelectHotbar {
                    result.status = AdapterCommandResultStatus::Failed;
                    result.detail_code = "result_type_mismatch".into();
                } else if result.status == AdapterCommandResultStatus::Succeeded {
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
            let known_command = if command.is_some() {
                true
            } else if let Some(horse_result) = self.horse_queue.observe(incoming, now) {
                result = horse_result;
                true
            } else {
                false
            };
            out.observed.push(ObservedResult {
                result,
                known_command,
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
                if result.command_type == AdapterCommandResultCommandType::RecallHorse {
                    horse_recall::feedback::failure(&result.detail_code).1
                } else if result.status == AdapterCommandResultStatus::Expired {
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
                    body_view: None,
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
        if let Some(result) = self.horse_queue.expire(now)
            && !self.remember_result(&result.command_id)
        {
            out.push(ObservedResult {
                known_command: true,
                result,
            });
        }
        out
    }
    /// 両種類の有効な命令を同じHTTP応答へ載せる。ACKまでは同じIDのまま再配送する。
    pub fn commands(&self, now: DateTime<Utc>) -> Vec<Value> {
        let mut commands: Vec<_> = self
            .pending_commands(now)
            .into_iter()
            .map(|command| serde_json::to_value(command).expect("serializable hotbar command"))
            .collect();
        if let Some(command) = self.horse_queue.pending(now) {
            commands.push(serde_json::to_value(command).expect("serializable horse command"));
        }
        commands
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
