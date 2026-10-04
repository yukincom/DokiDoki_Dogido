//! 入力元の共通型と、接続ごとのイベント重複・順序検査。
//! SequenceLedgerはDialogueが受理済みイベントを再処理しないために使う。
//! この層では入力の意味解釈、会話状態の変更、発話・保存は行わない。
use serde::{Deserialize, Serialize};
use std::collections::{HashSet, VecDeque};

const SEEN_LIMIT: usize = 2048;
const DEFERRED_LIMIT: usize = 8;

#[derive(Default)]
pub struct SequenceLedger {
    pub last_sequence: Option<i64>,
    sequences: VecDeque<i64>,
    sequence_set: HashSet<i64>,
    keys: VecDeque<String>,
    key_set: HashSet<String>,
}

#[derive(Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Admission {
    New,
    DuplicateKey,
    DuplicateSequence,
    StaleSequence,
}

impl SequenceLedger {
    /// 冪等キー、既受信sequence、古いsequenceの順に照合し、台帳を更新する。
    /// 読み取り検査ではないため、停止中・不明sessionなどの受付拒否は呼出前に済ませる。
    /// キーはsequenceより先に記録される。拒否されたsequenceに付いた新規キーも消費する。
    pub fn admit(&mut self, sequence: Option<i64>, key: Option<&str>) -> Admission {
        if let Some(key) = key.filter(|key| !key.is_empty()) {
            if self.key_set.contains(key) {
                return Admission::DuplicateKey;
            }
            if self.keys.len() == SEEN_LIMIT {
                self.key_set.remove(&self.keys.pop_front().unwrap());
            }
            self.keys.push_back(key.into());
            self.key_set.insert(key.into());
        }
        if let Some(sequence) = sequence {
            if self.sequence_set.contains(&sequence) {
                return Admission::DuplicateSequence;
            }
            if self.last_sequence.is_some_and(|last| sequence <= last) {
                return Admission::StaleSequence;
            }
            if self.sequences.len() == SEEN_LIMIT {
                self.sequence_set
                    .remove(&self.sequences.pop_front().unwrap());
            }
            self.sequences.push_back(sequence);
            self.sequence_set.insert(sequence);
            self.last_sequence = Some(sequence);
        }
        Admission::New
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InputSource {
    #[default]
    Text,
    Voice,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PendingInput {
    pub text: String,
    #[serde(default)]
    pub source: InputSource,
    #[serde(default)]
    pub display_text: String,
    #[serde(default)]
    pub turn_id: String,
    #[serde(default)]
    pub force_main_chat: bool,
    #[serde(default)]
    pub foreground_route: String,
}

impl PendingInput {
    fn normalized(mut self) -> Option<Self> {
        // 正規化済み本文の待ち列。STT文脈補正や意味解釈はここで行わない。
        self.text = self.text.trim().to_owned();
        if self.text.is_empty() {
            return None;
        }
        self.display_text = if self.display_text.is_empty() {
            self.text.clone()
        } else {
            self.display_text.trim().to_owned()
        };
        self.turn_id = self.turn_id.chars().take(180).collect();
        self.foreground_route = self.foreground_route.chars().take(40).collect();
        Some(self)
    }

    fn merge_route(&mut self, incoming: &Self) {
        if incoming.force_main_chat {
            self.turn_id.clone_from(&incoming.turn_id);
            self.force_main_chat = true;
            self.foreground_route = if incoming.foreground_route.is_empty() {
                "casual".into()
            } else {
                incoming.foreground_route.clone()
            };
        }
    }
}

#[derive(Default, Debug, Serialize)]
pub struct InputQueue {
    pending: Option<PendingInput>,
    deferred: VecDeque<PendingInput>,
}

#[derive(Clone, Copy)]
pub enum PendingPolicy {
    Preserve,
    Replace,
}

impl InputQueue {
    pub fn pending(&self) -> Option<&PendingInput> {
        self.pending.as_ref()
    }

    /// 知識質問/workshopを守るか一般入力を置換するかは、呼出側の既存policyが決める。
    pub fn push(&mut self, input: PendingInput, policy: PendingPolicy) -> bool {
        match policy {
            PendingPolicy::Preserve => self.enqueue(input),
            PendingPolicy::Replace => {
                let Some(input) = input.normalized() else {
                    return false;
                };
                self.pending = Some(input);
                true
            }
        }
    }

    pub fn enqueue(&mut self, input: PendingInput) -> bool {
        let Some(input) = input.normalized() else {
            return false;
        };
        let Some(pending) = &mut self.pending else {
            self.pending = Some(input);
            return true;
        };
        if pending.text == input.text {
            pending.merge_route(&input);
            return true;
        }
        if let Some(queued) = self.deferred.iter_mut().find(|old| old.text == input.text) {
            queued.merge_route(&input);
            return true;
        }
        if self.deferred.len() == DEFERRED_LIMIT {
            return false;
        }
        self.deferred.push_back(input);
        true
    }

    /// 同tickのadapter直接入力を先に扱い、同文の待機分だけを消す。
    pub fn remove_direct_duplicate(&mut self, text: &str) {
        let text = text.trim();
        if text.is_empty() {
            return;
        }
        if self
            .pending
            .as_ref()
            .is_some_and(|input| input.text == text)
        {
            self.pending = None;
        }
        self.deferred.retain(|input| input.text != text);
    }

    pub fn take_pending(&mut self) -> Option<PendingInput> {
        self.pending.take()
    }

    pub fn promote(&mut self) {
        if self.pending.is_none() {
            self.pending = self.deferred.pop_front();
        }
    }
}
