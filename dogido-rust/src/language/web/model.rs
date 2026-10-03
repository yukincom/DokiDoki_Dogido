//! Web用の三つの限定生成。状態と採否はRust、外部SDKへモデル判断を渡さない。
use super::{PERMISSION, RETURN, Research, State};
use crate::{
    llm::RigLlm,
    types::{ChatMessage, GenerationRequest, Role},
};
use anyhow::Result;
use serde::{Deserialize, de::DeserializeOwned};
use serde_json::{Value, json};
use std::sync::LazyLock;

static PROMPTS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("prompts.json")).expect("checked web prompts")
});
fn nfkc(s: &str) -> String {
    icu_normalizer::ComposingNormalizer::new_nfkc()
        .normalize(s)
        .into_owned()
}
fn size(s: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&s.chars().count())
}
fn parse<T: DeserializeOwned>(generated: &Value) -> Option<T> {
    if matches!(
        generated["finish_reason"].as_str(),
        Some("length" | "max_tokens" | "MAX_TOKENS")
    ) {
        return None;
    }
    let mut object = crate::haiku_response::extract_object(generated["text"].as_str()?, true)?;
    object.remove("__dogido_status");
    serde_json::from_value(Value::Object(object)).ok()
}
pub fn representative(text: &str) -> Option<&'static str> {
    let word = nfkc(text);
    let word = word.trim().trim_end_matches(['。', '.', '!', '！']);
    if [
        "うん",
        "はい",
        "ええで",
        "ええよ",
        "いいよ",
        "お願い",
        "開いて",
    ]
    .contains(&word)
    {
        Some("accept")
    } else if [
        "いや",
        "いいえ",
        "やめとく",
        "やめて",
        "開かないで",
        "今はやめとく",
    ]
    .contains(&word)
    {
        Some("decline")
    } else {
        None
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Consent {
    intent: String,
    evidence: String,
    confidence: f64,
}
pub fn consent(generated: &Value, text: &str) -> String {
    let Some(c) = parse::<Consent>(generated) else {
        return "uncertain".into();
    };
    if !["accept", "decline", "uncertain", "new_question"].contains(&c.intent.as_str())
        || !c.confidence.is_finite()
        || !(0.85..=1.0).contains(&c.confidence)
        || !size(&c.evidence, 1, 500)
        || c.evidence.trim().is_empty()
        || !nfkc(text).contains(&nfkc(&c.evidence))
    {
        return "uncertain".into();
    }
    c.intent
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    intent: String,
    evidence: String,
}
fn intent(generated: &Value, text: &str) -> Option<String> {
    let c = parse::<Intent>(generated)?;
    ([
        "report",
        "discuss",
        "uncertain",
        "continue",
        "return",
        "new_question",
        "acknowledge",
        "other",
    ]
    .contains(&c.intent.as_str())
        && size(&c.evidence, 1, 500)
        && !c.evidence.trim().is_empty()
        && nfkc(text).contains(&nfkc(&c.evidence)))
    .then_some(c.intent)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Quote {
    page_id: String,
    quote: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Reading {
    perspective: String,
    quotes: Vec<Quote>,
}
fn compact(s: &str) -> String {
    s.chars()
        .filter(|c| !c.is_whitespace() && !('\u{1c}'..='\u{1f}').contains(c))
        .collect()
}
fn checked_reading(generated: &Value, research: &Research) -> Option<Reading> {
    let r = parse::<Reading>(generated)?;
    if !size(&r.perspective, 1, 240)
        || r.perspective.trim().is_empty()
        || r.quotes.is_empty()
        || r.quotes.len() > 2
    {
        return None;
    }
    r.quotes
        .iter()
        .all(|q| {
            size(&q.quote, 8, 240)
                && compact(&q.quote).chars().count() >= 8
                && research.pages.iter().any(|p| {
                    p["id"] == q.page_id
                        && p["text_ja"]
                            .as_str()
                            .is_some_and(|t| compact(t).contains(&compact(&q.quote)))
                })
        })
        .then_some(r)
}
pub fn request(kind: &str, model: &str, details: &Value) -> GenerationRequest {
    let mut background = details.as_object().expect("web details").clone();
    let current = background.shift_remove("current").unwrap_or(json!({}));
    GenerationRequest {
        schema_version: 1,
        kind: kind.into(),
        model: model.into(),
        temperature: 0.0,
        enable_thinking: false,
        max_tokens: match kind {
            "language_web_consent" => 300,
            "language_research_intent" => 350,
            _ => 750,
        },
        messages: vec![
            ChatMessage {
                role: Role::System,
                content: crate::companion_prompt::expand(PROMPTS[kind].as_str().expect("web kind")),
            },
            ChatMessage {
                role: Role::User,
                content: format!(
                    "{}\n以上は背景。今回応答する最新の発話はこちら：\n{}",
                    crate::planner::python_json(&Value::Object(background)),
                    crate::planner::python_json(&current)
                ),
            },
        ],
    }
}
async fn generate(llm: &RigLlm, model: &str, kind: &str, details: &Value) -> Value {
    match llm.generate(&request(kind, model, details)).await {
        Ok(report) => {
            tracing::info!(kind,elapsed_ms=report.elapsed_ms as u64,completion_tokens=?report.generated.completion_tokens,finish_reason=?report.generated.finish_reason);
            serde_json::to_value(report.generated).unwrap_or(Value::Null)
        }
        Err(error) => {
            tracing::warn!(kind,error=%error);
            Value::Null
        }
    }
}
fn done(status: &str, text: impl AsRef<str>) -> Value {
    json!({"status":status,"text":text.as_ref()})
}
fn close(state: &mut State, status: &str, text: &str) -> Value {
    state.clear(true);
    let mut r = done(status, text);
    r["return_context"] = json!({"researched_topic":state.last_topic});
    r
}
/// 同期で決まる読書操作。Noneだけがモデル分類を必要とする。
pub fn reading_preflight(state: &mut State, text: &str) -> Option<Value> {
    let context = state.research.as_mut()?;
    let word = nfkc(text);
    if ["ウェブ", "Web", "web", "検索", "調べもの", "ページ"]
        .iter()
        .any(|s| word.contains(s))
        && [
            "閉じて",
            "閉じても",
            "終わり",
            "やめて",
            "やめよ",
            "ここまで",
        ]
        .iter()
        .any(|s| word.contains(s))
    {
        return Some(close(
            state,
            "handoff",
            "わかった。調べものはいったんここまでにしよか。",
        ));
    }
    if context.phase != "confirming_topic_change" {
        return None;
    }
    let word = word
        .trim()
        .trim_end_matches(['。', '.', '!', '！', '?', '？']);
    if ["続き", "今の続き", "同じ話", "同じ質問"].contains(&word) || word.contains("続きや")
    {
        context.phase = "discussing".into();
        Some(done(
            "research_continue",
            "わかった、今の続きやな。気になるところ、聞かせてや。",
        ))
    } else if ["別の質問", "別の話", "違う質問", "新しい質問"].contains(&word) {
        Some(close(
            state,
            "handoff",
            "わかった。新しく聞きたいことを、もう一度聞かせてな。",
        ))
    } else if ["分かった", "わかった", "なるほど"]
        .iter()
        .any(|s| word.contains(s))
    {
        context.phase = "return_offered".into();
        Some(done("return_offered", RETURN))
    } else {
        Some(done(
            "research_topic_confirmation",
            format!(
                "いま見てる「{}」の続き？ それとも別の質問？",
                context.target
            ),
        ))
    }
}
fn uncertain(context: &Research) -> Value {
    done(
        "research_uncertain",
        if context.search_url.is_empty() {
            "そこまでは、オレには資料から確かめられへんかった。どのあたりでそう思ったん？"
        } else if context.pages.is_empty() && !context.search_results.is_empty() {
            "検索結果の紹介文までは受け取れたけど、リンク先の本文はまだ読めてへんねん。気になったところを教えてくれる？"
        } else if context.pages.is_empty() {
            "今の検索ページからは、説明の本文を受け取れてへんねん。調べるのはいったんここまでにしよか。"
        } else {
            "そのところは、今の概要だけやとまだ分からへんな。気になる説明を一緒に見てみよか。"
        },
    )
}
pub fn apply_intent(state: &mut State, generated: &Value, text: &str) -> Value {
    let Some(context) = state.research.as_mut() else {
        return done("stale_research", "");
    };
    let Some(intent) = intent(generated, text) else {
        return done("research_unclear", "ごめん、もうちょっと聞かせてくれる？");
    };
    match intent.as_str() {
        "new_question" => {
            context.phase = "confirming_topic_change".into();
            done(
                "research_topic_confirmation",
                format!(
                    "いま見てる「{}」の続き？ それとも別の質問？",
                    context.target
                ),
            )
        }
        "return" => close(state, "handoff", "よし、冒険にもどろか！"),
        "uncertain" if context.search_url.is_empty() => {
            context.phase = "return_offered".into();
            done(
                "return_offered",
                format!("うーん、そこは学校で先生に聞いてみるといいかもしれんなー。{RETURN}"),
            )
        }
        "continue" => {
            context.phase = "discussing".into();
            done(
                "research_continue",
                "ええで、もうちょっと考えてみよか。気になってること、聞かせてや。",
            )
        }
        "report" | "discuss" | "uncertain" => json!({"command":"reading"}),
        "acknowledge" if context.phase == "discussing" => {
            context.phase = "return_offered".into();
            done("return_offered", RETURN)
        }
        _ => done(
            "awaiting_report",
            "どうやった？ 分かったこと、オレにも教えてや。",
        ),
    }
}
pub fn apply_reading(state: &mut State, generated: &Value) -> Value {
    let Some(context) = state.research.as_mut() else {
        return done("stale_research", "");
    };
    context.phase = "discussing".into();
    if let Some(reading) = checked_reading(generated, context) {
        done("research_reflection", &reading.perspective)
    } else {
        uncertain(context)
    }
}
/// cloneしたStateに一手を適用する。hostが取消と同じepochを再検証してからcommitする。
/// new_questionだけNoneを返す。その場合も失効済みStateを先にcommitする。
pub async fn respond(
    llm: &RigLlm,
    model: &str,
    state: &mut State,
    details: &Value,
    now: u64,
) -> Result<Option<Value>> {
    let text = details["current"]["text"].as_str().unwrap_or("");
    state.last_activity = now;
    if let Some(p) = &state.proposal {
        let intent = if let Some(i) = representative(text) {
            i.into()
        } else {
            let d = json!({"current":details["current"],"question":p.question,"permission_prompt":PERMISSION,"phase":if state.departure.is_some(){"awaiting_playback"}else{"awaiting_consent"}});
            consent(
                &generate(llm, model, "language_web_consent", &d).await,
                text,
            )
        };
        let result = state.consent(&intent, now);
        return Ok((result["status"] != "new_question").then_some(result));
    }
    if state.research.is_none() {
        return Ok(None);
    }
    if let Some(result) = reading_preflight(state, text) {
        return Ok(Some(result));
    }
    let mut d = details.clone();
    let c = state.research.as_ref().unwrap();
    d["research"] = json!({"question":c.question,"target":c.target,"phase":c.phase});
    let mut result = apply_intent(
        state,
        &generate(llm, model, "language_research_intent", &d).await,
        text,
    );
    if result["command"] == "reading" {
        d["research"] = serde_json::to_value(state.research.as_ref().unwrap())?;
        result = apply_reading(
            state,
            &generate(llm, model, "language_research_reading", &d).await,
        );
    }
    Ok(Some(result))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn generated(v: Value) -> Value {
        json!({"text":v.to_string(),"finish_reason":"stop"})
    }
    #[test]
    fn consent_is_current_evidence_and_threshold_checked() {
        assert_eq!(representative("うん！"), Some("accept"));
        assert_eq!(representative("『うん』と言った"), None);
        for (e, c) in [("別の文", 0.99), ("開いて", 0.84), (" ", 1.0)] {
            assert_eq!(
                consent(
                    &generated(json!({"intent":"accept","evidence":e,"confidence":c})),
                    "開いてください"
                ),
                "uncertain"
            );
        }
        assert_eq!(
            consent(
                &generated(json!({"intent":"accept","evidence":"開いて","confidence":0.85})),
                "開いてください"
            ),
            "accept"
        );
        let mut r = generated(json!({"intent":"accept","evidence":"開いて","confidence":1.0}));
        r["finish_reason"] = "length".into();
        assert_eq!(consent(&r, "開いて"), "uncertain");
    }
    #[test]
    fn snippets_are_not_quotes_and_return_keeps_topic_only() {
        let mut s = State {
            research: Some(Research {
                question: "狐の語源".into(),
                target: "狐".into(),
                pages: vec![],
                phase: "awaiting_report".into(),
                search_results: vec![json!({"description":"紹介文には八文字以上の説明"})],
                search_url: "https://www.google.com/search?q=fox".into(),
            }),
            ..Default::default()
        };
        assert_eq!(
            apply_reading(
                &mut s,
                &generated(
                    json!({"perspective":"そういう意味やな。","quotes":[{"page_id":"snippet","quote":"紹介文には八文字以上"}]})
                )
            )["status"],
            "research_uncertain"
        );
        let r = apply_intent(
            &mut s,
            &generated(json!({"intent":"return","evidence":"戻る"})),
            "戻る",
        );
        assert_eq!(r["return_context"], json!({"researched_topic":"狐の語源"}));
        assert!(!s.active());
    }
    #[test]
    fn topic_change_needs_confirmation_and_never_searches() {
        let mut s = State {
            research: Some(Research {
                question: "狐の語源".into(),
                target: "狐".into(),
                pages: vec![],
                phase: "awaiting_report".into(),
                search_results: vec![],
                search_url: "https://www.google.com/search?q=fox".into(),
            }),
            ..Default::default()
        };
        assert_eq!(
            apply_intent(
                &mut s,
                &generated(json!({"intent":"new_question","evidence":"狸"})),
                "狸は？"
            )["status"],
            "research_topic_confirmation"
        );
        assert!(s.proposal.is_none());
        assert_eq!(
            reading_preflight(&mut s, "今の続き").unwrap()["status"],
            "research_continue"
        );
    }
}

#[cfg(test)]
mod parity {
    use super::*;
    #[test]
    fn research_decisions_match_canonical_python_cases() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("research-fixtures.json")).unwrap();
        assert_eq!(cases.len(), 47);
        for (index, case) in cases.iter().enumerate() {
            let mut state = State {
                research: Some(serde_json::from_value(case["context"].clone()).unwrap()),
                ..Default::default()
            };
            let text = case["text"].as_str().unwrap();
            let mut result = reading_preflight(&mut state, text).unwrap_or_else(|| {
                apply_intent(
                    &mut state,
                    &json!({"text":case["intent"].to_string(),"finish_reason":"stop"}),
                    text,
                )
            });
            if result["command"] == "reading" {
                result = apply_reading(
                    &mut state,
                    &json!({"text":case["reading"].to_string(),"finish_reason":"stop"}),
                );
            }
            assert_eq!(result["status"], case["expected"]["status"], "case {index}");
            assert_eq!(result["text"], case["expected"]["text"], "case {index}");
            if result["status"] != "handoff" {
                assert_eq!(
                    state.research.as_ref().unwrap().phase,
                    case["expected"]["phase"].as_str().unwrap(),
                    "case {index}"
                );
            }
        }
    }
}
