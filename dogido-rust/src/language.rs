//! 限定国語対話の一手。問い・聞き返しを保持し、検索・回答の順序と採否をコードで決める。
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

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
    Interpretation,
    Lookup,
    ReplyPrompt,
    Reply,
    Done,
}

pub struct Turn {
    phase: Phase,
    pub state: State,
    details: Value,
    interpretation: Value,
    lookup: Value,
    pub outcome: Value,
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
    pub fn generated(&mut self, generated: Value) -> Result<Value> {
        let command = match self.phase {
            Phase::InterpretationPrompt => {
                self.phase = Phase::Interpretation;
                "interpretation"
            }
            Phase::ReplyPrompt => {
                self.phase = Phase::Reply;
                "reply"
            }
            _ => anyhow::bail!("unexpected language generation"),
        };
        Ok(
            json!({"command":command,"generated":generated,"details":self.details,"state":self.state}),
        )
    }
    pub fn advance(&mut self, frame: &Value) -> Result<Value> {
        match (self.phase, frame["stage"].as_str()) {
            (Phase::Start, Some("start")) => {
                self.phase = Phase::InterpretationPrompt;
                Ok(
                    json!({"command":"prompt","kind":"language_dialogue_interpretation","details":self.details}),
                )
            }
            (Phase::Interpretation, Some("interpretation")) => {
                let i = &frame["payload"];
                if i.is_null() {
                    if frame["invalid_normal_chat"] == true || frame["information_request"] == false
                    {
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
                    let question =
                        nonempty(i, "clarification", "どの言葉の、どんなことが知りたいん？");
                    self.state.focus.clarification = question.clone();
                    self.state.focus.alternatives =
                        serde_json::from_value(i["alternatives"].clone())?;
                    return Ok(self.done("clarify", &question, vec![]));
                }
                self.phase = Phase::Lookup;
                Ok(
                    json!({"command":"lookup","interpretation":i,"computed_fact":frame["computed_fact"],"text":self.details["current"]["text"]}),
                )
            }
            (Phase::Lookup, Some("lookup")) => {
                self.lookup = frame["lookup"].clone();
                let facts = self.lookup["facts"]
                    .as_array()
                    .ok_or_else(|| anyhow::anyhow!("missing language facts"))?;
                ensure!(
                    facts.len() <= 11
                        && facts
                            .iter()
                            .all(|f| f["id"].as_str().is_some_and(|s| !s.is_empty())),
                    "invalid language facts"
                );
                if frame["fixed_reply"].is_object() {
                    return Ok(self.reply(&frame["fixed_reply"]));
                }
                if facts.is_empty() {
                    return Ok(self.done("unsupported", "その言葉のことは、今の資料では確かめられへんかった。教科書や辞書で一緒に見てみよか。", vec![]));
                }
                self.phase = Phase::ReplyPrompt;
                let mut details = self.details.clone();
                details["interpretation"] = self.interpretation.clone();
                details["facts"] = self.lookup["facts"].clone();
                details["search_status"] = self.lookup["status"].clone();
                Ok(json!({"command":"prompt","kind":"language_dialogue_reply","details":details}))
            }
            (Phase::Reply, Some("reply")) => Ok(self.reply(&frame["payload"])),
            _ => anyhow::bail!("unexpected language stage"),
        }
    }
    fn reply(&mut self, reply: &Value) -> Value {
        let facts = self.lookup["facts"].as_array().cloned().unwrap_or_default();
        let ids = reply["fact_ids"].as_array().cloned().unwrap_or_default();
        let valid = reply.is_object()
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
        self.done(&status, &string(reply, "text"), references)
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
