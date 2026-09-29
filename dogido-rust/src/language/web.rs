//! Web同意・実再生・一回だけの検索・読書期限。外部処理と状態変更を分離する。
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
pub mod model;

pub const PERMISSION: &str = "うーん。ちょっと俺にはわからへんな。この質問をウェブで調べるため、新しい検索ページを開いてもええか？";
pub const DEPARTURE: &str = "ほな一緒にいこか！";
pub const DECLINED: &str = "わかった。この質問では新しい検索はせえへんで。";
pub const AGAIN: &str = "この質問で新しい検索をしてもええか、教えてくれる？";
pub const UNAVAILABLE: &str =
    "今はページをうまく読めへんかった。教科書や資料集でも確かめてみよか。";
pub const RETURN: &str = "勉強も楽しいけど、そろそろ冒険にもどろか！";
pub const RESEARCH_TTL_MS: u64 = 1_800_000;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Proposal {
    pub question: String,
    pub target: String,
    pub facet: String,
    pub search_terms: Vec<String>,
    pub web_query: String,
    pub trigger_reason: String,
    pub known_urls: Vec<String>,
}
impl Proposal {
    /// 検証済みの解釈からだけ生成する。曖昧な対象はこの段階に渡さない。
    pub fn from_interpretation(i: &Value, reason: &str, facts: &[Value]) -> Option<Self> {
        if i["dialogue_act"] != "information_request" || i["target_status"] == "ambiguous" {
            return None;
        }
        Some(Self {
            question: i["question"].as_str()?.into(),
            target: i["target"].as_str()?.into(),
            facet: i["facet"].as_str()?.into(),
            search_terms: serde_json::from_value(i["search_terms"].clone()).ok()?,
            web_query: i["web_query"].as_str().unwrap_or("").into(),
            trigger_reason: reason.into(),
            known_urls: facts
                .iter()
                .flat_map(|f| f["sources"].as_array().into_iter().flatten())
                .filter_map(|s| s["url"].as_str().map(str::to_owned))
                .collect(),
        })
    }
}
/// WebResultは外部取得の記録。本文を公式事実や通常会話履歴へ昇格させない。
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchResult {
    #[serde(default)]
    pub query: String,
    pub status: String,
    #[serde(default)]
    pub pages: Vec<Value>,
    #[serde(default)]
    pub events: Vec<Value>,
    #[serde(default)]
    pub search_status: String,
    #[serde(default)]
    pub child_status: String,
    #[serde(default)]
    pub search_results: Vec<Value>,
    #[serde(default)]
    pub search_url: String,
    #[serde(default)]
    pub timing: Value,
}
impl SearchResult {
    pub fn valid(&self) -> bool {
        self.pages.len() <= 1
            && self.search_results.len() <= 5
            && self.pages.iter().all(|p| {
                p["id"]
                    .as_str()
                    .is_some_and(|s| !s.is_empty() && s.len() <= 160)
                    && p["text_ja"]
                        .as_str()
                        .is_some_and(|s| !s.trim().is_empty() && s.chars().count() <= 15000)
                    && p["use"] == "google_ai_overview"
                    && p["claim_status"] == "retrieved_ai_summary_not_verified_fact"
            })
            && self
                .search_results
                .iter()
                .all(|r| r["claim_status"] == "search_snippet_not_page_body")
            && match self.status.as_str() {
                "read" => !self.pages.is_empty(),
                "results_only" => !self.search_results.is_empty(),
                "page_opened" => self
                    .search_url
                    .starts_with("https://www.google.com/search?"),
                _ => false,
            }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Research {
    pub question: String,
    pub target: String,
    pub pages: Vec<Value>,
    pub phase: String,
    pub search_results: Vec<Value>,
    pub search_url: String,
}
#[derive(Clone, Debug, Default)]
pub struct State {
    pub proposal: Option<Proposal>,
    pub departure: Option<String>,
    pub searching: Option<String>,
    pub research: Option<Research>,
    pub last_activity: u64,
    pub last_topic: String,
}
impl State {
    pub fn active(&self) -> bool {
        self.proposal.is_some() || self.research.is_some() || self.searching.is_some()
    }
    pub fn propose(&mut self, p: Proposal, now: u64) {
        self.proposal = Some(p);
        self.departure = None;
        self.research = None;
        self.searching = None;
        self.last_activity = now;
    }
    pub fn ttl(&self, ordinary: u64) -> u64 {
        if self.research.is_some() {
            RESEARCH_TTL_MS
        } else {
            ordinary
        }
    }
    pub fn expire(&mut self, now: u64, ordinary: u64) -> bool {
        if self.active() && now.saturating_sub(self.last_activity) >= self.ttl(ordinary) {
            self.clear(true);
            true
        } else {
            false
        }
    }
    pub fn clear(&mut self, remember: bool) {
        if remember && let Some(r) = &self.research {
            self.last_topic = r.question.chars().take(160).collect();
        }
        self.proposal = None;
        self.departure = None;
        self.searching = None;
        self.research = None;
    }
    /// 戦闘開始は未消費の許可と検索中結果だけ失効。読書済み文脈を保持する。
    pub fn interrupt(&mut self) {
        self.proposal = None;
        self.departure = None;
        self.searching = None;
    }
    pub fn consent(&mut self, intent: &str, now: u64) -> Value {
        if self.proposal.is_none() {
            return json!({"status":"stale_consent","text":""});
        }
        self.last_activity = now;
        match intent {
            "accept" => {
                if self.departure.is_some() {
                    return json!({"status":"web_waiting_playback","text":""});
                }
                let id = format!("web-departure:{}", uuid::Uuid::new_v4().simple());
                self.departure = Some(id.clone());
                json!({"status":"web_waiting_playback","text":DEPARTURE,"web_departure":id})
            }
            "decline" => {
                self.clear(false);
                json!({"status":"web_declined","text":DECLINED})
            }
            "new_question" => {
                self.clear(false);
                json!({"status":"new_question","text":""})
            }
            _ => {
                self.departure = None;
                json!({"status":"web_consent_requested","text":AGAIN})
            }
        }
    }
    /// hostが同じsession epochと実際のplayer終了を確認した後だけ呼ぶ。
    pub fn playback(&mut self, id: &str, status: &str) -> Option<Proposal> {
        if !["completed", "failed", "cancelled"].contains(&status)
            || self.departure.as_deref() != Some(id)
        {
            return None;
        }
        self.departure = None;
        let p = self.proposal.take();
        if status == "completed" {
            self.searching = Some(id.into());
            p
        } else {
            None
        }
    }
    pub fn searched(&mut self, id: &str, p: &Proposal, result: &SearchResult, now: u64) -> Value {
        if self.searching.as_deref() != Some(id) {
            return json!({"status":"stale_search","text":""});
        }
        self.searching = None;
        if !result.valid() {
            return json!({"status":"web_unavailable","text":UNAVAILABLE});
        }
        let opened = result.child_status == "opened";
        let phase = if opened {
            "awaiting_report"
        } else {
            "reference_only"
        };
        self.research = Some(Research {
            question: p.question.clone(),
            target: p.target.clone(),
            pages: result.pages.clone(),
            phase: phase.into(),
            search_results: result.search_results.clone(),
            search_url: result.search_url.clone(),
        });
        self.last_activity = now;
        json!({"status":phase,"text":if opened { "" } else if !result.search_url.is_empty() { "検索の内容は受け取れたで。気になってるところ、一緒に見てみよか。" } else { "オレが確かめる資料は読めたけど、一人で読みやすいページはまだ見つけられてへんねん。教科書や先生にも聞いてみよか。" }})
    }
    pub fn snapshot(&self) -> Value {
        json!({"phase":if self.research.is_some() {"reading"} else if self.searching.is_some(){"searching"}else if self.departure.is_some(){"awaiting_playback"}else if self.proposal.is_some(){"awaiting_consent"}else{"none"},"topic":self.research.as_ref().map(|r| &r.question),"last_activity_ms":self.last_activity})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn proposal() -> Proposal {
        Proposal {
            question: "狐の語源".into(),
            target: "狐".into(),
            facet: "etymology".into(),
            search_terms: vec!["狐".into()],
            web_query: "狐の語源".into(),
            trigger_reason: "no_local_facts".into(),
            known_urls: vec![],
        }
    }
    #[test]
    fn matching_completion_consumes_once_and_failed_or_revoked_never_starts() {
        let mut s = State::default();
        s.propose(proposal(), 0);
        assert!(s.playback("invented", "completed").is_none());
        let r = s.consent("accept", 1);
        let id = r["web_departure"].as_str().unwrap();
        assert_eq!(s.consent("accept", 2)["text"], "");
        assert!(s.playback("wrong", "completed").is_none());
        assert!(s.playback(id, "started").is_none());
        assert!(s.playback(id, "completed").is_some());
        assert!(s.playback(id, "completed").is_none());
        for status in ["failed", "cancelled"] {
            s.propose(proposal(), 0);
            let r = s.consent("accept", 1);
            let id = r["web_departure"].as_str().unwrap();
            assert!(s.playback(id, status).is_none());
            assert!(s.playback(id, "completed").is_none());
        }
        s.propose(proposal(), 0);
        let r = s.consent("accept", 1);
        s.consent("uncertain", 2);
        assert!(
            s.playback(r["web_departure"].as_str().unwrap(), "completed")
                .is_none()
        );
    }
    #[test]
    fn reading_survives_interrupt_but_pending_grant_does_not_and_ttl_is_thirty_minutes() {
        let p = proposal();
        let mut s = State::default();
        s.propose(p.clone(), 0);
        let r = s.consent("accept", 1);
        let id = r["web_departure"].as_str().unwrap();
        s.playback(id, "completed");
        let result = SearchResult {
            status: "page_opened".into(),
            search_url: "https://www.google.com/search?q=fox".into(),
            child_status: "opened".into(),
            ..Default::default()
        };
        assert_eq!(s.searched(id, &p, &result, 2)["text"], "");
        s.interrupt();
        assert!(s.research.is_some());
        assert!(!s.expire(300_001, 300_000));
        assert!(s.expire(1_800_002, 300_000));
        assert_eq!(s.last_topic, "狐の語源");
        assert_eq!(
            s.searched(id, &p, &result, 1_800_003)["status"],
            "stale_search"
        );
        s.propose(p, 3);
        s.consent("accept", 4);
        s.interrupt();
        assert!(!s.active());
    }
}
