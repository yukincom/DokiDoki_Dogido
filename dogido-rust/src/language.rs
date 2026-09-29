//! 限定国語対話の一手。問い・聞き返しを保持し、検索・回答の順序と採否をコードで決める。
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
mod preparation;
mod validation;
pub mod web;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Focus {
    pub question: String,
    pub target: String,
    pub clarification: String,
    pub alternatives: Vec<String>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct State {
    pub focus: Focus,
    pub kanji_scope_confirmed: bool,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Phase {
    Start,
    InterpretationPrompt,
    Lookup,
    ReplyPrompt,
    Done,
}

pub struct Turn {
    phase: Phase,
    pub state: State,
    details: Value,
    interpretation: Value,
    lookup: Value,
    pub outcome: Value,
    web_available: bool,
}
impl Turn {
    pub fn new(input: &Value) -> Result<Self> {
        let state = serde_json::from_value(input["language_state"].clone())?;
        let mut turn = Self {
            phase: Phase::Start,
            state,
            details: json!({}),
            interpretation: Value::Null,
            lookup: Value::Null,
            outcome: Value::Null,
            web_available: input["web_available"] == true,
        };
        turn.details = json!({"current":{"turn_id":input["operation_id"], "text":input["text"], "source":input["source"]},
            "history":input["history"], "mode":if input["language_active"] == true {"language"} else {"normal"},
            "focus":turn.state.focus, "situation_history":input["event_digest"].as_str().unwrap_or("").chars().take(640).collect::<String>()});
        Ok(turn)
    }
    pub fn prompt_kind(&self) -> Result<(&'static str, u64)> {
        match self.phase {
            Phase::InterpretationPrompt => Ok(("language_dialogue_interpretation", 850)),
            Phase::ReplyPrompt => Ok(("language_dialogue_reply", 700)),
            _ => anyhow::bail!("unexpected language prompt"),
        }
    }
    pub fn request(&self, model: &str) -> Result<crate::types::GenerationRequest> {
        let (kind, max_tokens) = self.prompt_kind()?;
        let mut details = self.details.clone();
        if self.phase == Phase::ReplyPrompt {
            details["interpretation"] = self.interpretation.clone();
            details["facts"] = self.lookup["facts"].clone();
            details["search_status"] = self.lookup["status"].clone();
        }
        Ok(crate::types::GenerationRequest {
            schema_version: 1,
            kind: kind.into(),
            model: model.into(),
            messages: preparation::messages(kind, &details),
            temperature: 0.0,
            max_tokens,
            enable_thinking: false,
        })
    }
    pub fn generated(&mut self, generated: Value) -> Result<Value> {
        match self.phase {
            Phase::InterpretationPrompt => self.interpreted(&validation::interpretation(
                &generated,
                &self.details,
                &self.state,
            )),
            Phase::ReplyPrompt => Ok(self.reply(&validation::generated_reply(&generated))),
            _ => anyhow::bail!("unexpected language generation"),
        }
    }
    fn interpreted(&mut self, frame: &Value) -> Result<Value> {
        let i = &frame["payload"];
        if i.is_null() {
            if frame["invalid_normal_chat"] == true || frame["information_request"] == false {
                return Ok(self.handoff());
            }
            return Ok(self.done(
                "clarify",
                "ごめん、何のことを聞きたいか、もうちょっと教えてくれる？",
                vec![],
            ));
        }
        ensure!(i.is_object(), "invalid interpretation frame");
        self.interpretation = i.clone();
        if i["dialogue_act"] != "information_request"
            || i["relation"] == "end"
            || !matches!(i["topic"].as_str(), Some("language" | "unclear"))
        {
            return Ok(self.handoff());
        }
        self.state.focus = Focus {
            question: string(i, "question"),
            target: string(i, "target"),
            ..Focus::default()
        };
        self.state.kanji_scope_confirmed = false;
        if i["target_status"] == "ambiguous" || i["topic"] == "unclear" {
            let question = nonempty(i, "clarification", "どの言葉の、どんなことが知りたいん？");
            self.state.focus.clarification = question.clone();
            self.state.focus.alternatives = serde_json::from_value(i["alternatives"].clone())?;
            return Ok(self.done("clarify", &question, vec![]));
        }
        self.phase = Phase::Lookup;
        if frame["computed_fact"].is_object() {
            return self.looked_up(
                json!({"terms":[],"facts":[frame["computed_fact"]],"status":"computed","error":""}),
            );
        }
        Ok(json!({"command":"lookup","interpretation":i}))
    }
    pub fn advance(&mut self, frame: &Value) -> Result<Value> {
        match (self.phase, frame["stage"].as_str()) {
            (Phase::Start, Some("start")) => {
                self.phase = Phase::InterpretationPrompt;
                Ok(json!({"command":"generate"}))
            }
            (Phase::Lookup, Some("lookup")) => {
                ensure!(
                    frame.get("fixed_reply").is_none(),
                    "helper cannot supply language reply"
                );
                self.looked_up(frame["lookup"].clone())
            }
            _ => anyhow::bail!("unexpected language stage"),
        }
    }
    fn looked_up(&mut self, lookup: Value) -> Result<Value> {
        self.lookup = lookup;
        let facts = self.lookup["facts"]
            .as_array_mut()
            .ok_or_else(|| anyhow::anyhow!("missing language facts"))?;
        if let Some(fact) = preparation::comparison_fact(
            &self.interpretation,
            self.details["current"]["text"].as_str().unwrap_or(""),
        ) {
            facts.push(fact);
        }
        ensure!(
            facts.len() <= 11
                && facts
                    .iter()
                    .all(|f| f["id"].as_str().is_some_and(|s| !s.is_empty())),
            "invalid language facts"
        );
        // 明示Web要求は正本どおり回答生成を挟まず、同意確認へ進む。
        if self.web_available && self.interpretation["lookup_requested"] == true {
            return Ok(self.propose_web("explicit_request"));
        }
        if let Some(reply) = preparation::fixed_reply(&self.interpretation, facts) {
            return Ok(self.reply(&reply));
        }
        if facts.is_empty() {
            if self.web_available {
                return Ok(
                    self.propose_web(if self.interpretation["lookup_requested"] == true {
                        "explicit_request"
                    } else {
                        "no_local_facts"
                    }),
                );
            }
            return Ok(self.done("unsupported", "その言葉のことは、今の資料では確かめられへんかった。教科書や辞書で一緒に見てみよか。", vec![]));
        }
        self.phase = Phase::ReplyPrompt;
        Ok(json!({"command":"generate"}))
    }
    fn reply(&mut self, reply: &Value) -> Value {
        if self.web_available && self.interpretation["lookup_requested"] == true {
            return self.propose_web("explicit_request");
        }
        let facts = self.lookup["facts"].as_array().cloned().unwrap_or_default();
        let ids = reply["fact_ids"].as_array().cloned().unwrap_or_default();
        let valid = validation::reply_shape(reply)
            && (reply["status"] != "answer" || !ids.is_empty())
            && ids
                .iter()
                .all(|id| id.is_string() && facts.iter().any(|f| &f["id"] == id));
        if !valid {
            return self.done("unsupported","ごめんな、今の資料からはうまく説明できへんかった。一緒に教科書や資料集で確かめよか。",vec![]);
        }
        let status = string(reply, "status");
        self.state.kanji_scope_confirmed = self.interpretation["facet"] == "grade";
        let references = facts
            .iter()
            .filter(|f| ids.contains(&f["id"]))
            .flat_map(|f| f["sources"].as_array().into_iter().flatten())
            .cloned()
            .collect();
        if reply["missing_kind"] == "context" {
            let question = nonempty(
                reply,
                "clarification",
                "その言葉が出てくる文や、使う場面を教えてくれる？",
            );
            self.state.focus.clarification = question.clone();
            self.state.kanji_scope_confirmed = false;
            return self.done("clarify", &question, references);
        }
        if self.web_available {
            let reason = if self.interpretation["lookup_requested"] == true {
                "explicit_request"
            } else if matches!(status.as_str(), "partial" | "unsupported")
                && reply["missing_kind"] == "evidence"
                && !string(reply, "missing").trim().is_empty()
            {
                "uncertain_reply"
            } else {
                ""
            };
            if !reason.is_empty() {
                return self.propose_web(reason);
            }
        }
        self.done(&status, &string(reply, "text"), references)
    }
    fn propose_web(&mut self, reason: &str) -> Value {
        let facts = self.lookup["facts"].as_array().cloned().unwrap_or_default();
        let proposal = web::Proposal::from_interpretation(&self.interpretation, reason, &facts);
        let mut result = self.done("web_consent_requested", web::PERMISSION, vec![]);
        result["web_proposal"] = serde_json::to_value(proposal).unwrap();
        self.outcome = result.clone();
        result
    }
    fn handoff(&mut self) -> Value {
        self.state = State::default();
        self.done("host_chat", "", vec![])
    }
    fn done(&mut self, status: &str, text: &str, references: Vec<Value>) -> Value {
        self.phase = Phase::Done;
        self.outcome = json!({"command":"done","status":status,"text":text,
            "state":self.state,"references":references,"interpretation":self.interpretation});
        self.outcome.clone()
    }
    pub fn finished(&self) -> bool {
        self.phase == Phase::Done
    }
}
fn string(v: &Value, key: &str) -> String {
    v[key].as_str().unwrap_or("").to_owned()
}
fn nonempty(v: &Value, key: &str, fallback: &str) -> String {
    v[key]
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or(fallback)
        .to_owned()
}

#[cfg(test)]
mod web_tests {
    use super::*;
    #[test]
    fn explicit_web_request_with_local_facts_needs_no_reply_generation() {
        let mut turn =
            Turn::new(&json!({"language_state":State::default(),"web_available":true})).unwrap();
        turn.phase = Phase::Lookup;
        turn.interpretation = json!({"dialogue_act":"information_request","target_status":"explicit",
            "question":"狐についてウェブで調べて","target":"狐","facet":"meaning","search_terms":["狐"],"lookup_requested":true});
        let result = turn
            .looked_up(json!({"facts":[{"id":"local:fox","text_ja":"資料の狐"}],"status":"found"}))
            .unwrap();
        assert_eq!(result["command"], "done");
        assert_eq!(result["status"], "web_consent_requested");
        assert!(turn.prompt_kind().is_err());
    }
}
