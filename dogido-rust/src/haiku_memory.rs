//! Soft feedback and explicit recall. This module never edits a verse or turns
//! player advice into hard constraints; only the configured memory root is read.
use crate::haiku_record::{MemoryStore, SAVE_LOCK, append_line, locked_file};
use anyhow::Result;
use chrono::{DateTime, Duration, NaiveDateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    fs::{self, File},
    io::{BufRead, BufReader},
    path::Path,
};

fn clipped(s: &str, n: usize) -> String {
    s.trim().chars().take(n).collect()
}
fn date(v: &Value) -> Option<DateTime<Utc>> {
    let s = v.as_str()?.trim();
    DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|t| t.with_timezone(&Utc))
        .or_else(|| {
            NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f")
                .ok()
                .map(|t| t.and_utc())
        })
}
fn rows(path: &Path) -> Result<Vec<Value>> {
    let file = match File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(vec![]),
        Err(e) => return Err(e.into()),
    };
    let mut out = vec![];
    for line in BufReader::new(file).lines() {
        if let Ok(row) = serde_json::from_str::<Value>(&line?)
            && row.is_object()
        {
            out.push(row);
        }
    }
    Ok(out)
}

/// Whole raw utterance only: quotation, questions and negated requests fail.
pub fn clear_requested(raw: &str) -> bool {
    let mut s = raw.trim().trim_end_matches(['。', '！', '!']);
    if let Some(rest) = s.strip_prefix("うん").or_else(|| s.strip_prefix("はい")) {
        s = rest.trim_start_matches(['、', ',', ' ']);
    }
    if let Some(rest) = s.strip_prefix("もう") {
        s = rest.trim_start_matches(['、', ',', ' ']);
    }
    if [
        "前の注意やめて",
        "前の注意やめてください",
        "ゆるめて",
        "緩めて",
        "ゆるめてください",
        "緩めてください",
    ]
    .contains(&s)
    {
        return true;
    }
    if let Some(rest) = s.strip_prefix("縛らんで") {
        return ["", "ええ", "いい"].contains(&rest);
    }
    if let Some(rest) = s.strip_prefix("気にせんで") {
        return ["", "ええ", "いい"].iter().any(|p| {
            rest.strip_prefix(p)
                .is_some_and(|r| ["", "わ", "よ", "で"].contains(&r))
        });
    }
    if let Some(rest) = s
        .strip_prefix("気にしなくて")
        .or_else(|| s.strip_prefix("気にしんで"))
    {
        return ["ええ", "いい"].iter().any(|p| {
            rest.strip_prefix(p)
                .is_some_and(|r| ["", "わ", "よ", "で"].contains(&r))
        });
    }
    if let Some(rest) = s
        .strip_prefix("前の注意")
        .or_else(|| s.strip_prefix("注意"))
    {
        let rest = rest.strip_prefix('は').unwrap_or(rest);
        let rest = rest
            .strip_prefix("もう")
            .map_or(rest, |s| s.trim_start_matches(['、', ',', ' ']));
        return ["いらない", "いらん"].iter().any(|p| {
            rest.strip_prefix(p)
                .is_some_and(|r| ["", "わ", "よ", "で"].contains(&r))
        });
    }
    false
}

/// Avoid adding memory-only inputs to ordinary conversation history. Final
/// Japanese place/time interpretation is owned by recall_query.
pub fn memory_candidate(text: &str) -> bool {
    clear_requested(text)
        || text.contains('句')
        || text.contains("川柳")
        || text.contains("どこで詠んだ")
}

pub fn feedback_kind(step: &Value) -> Option<&'static str> {
    let action = step["action"].as_str()?;
    if action == "praise" {
        return Some("praise");
    }
    if ![
        "respond",
        "explain",
        "ask",
        "compare",
        "stage_player_edit",
        "propose_revision",
    ]
    .contains(&action)
    {
        return None;
    }
    let problems: HashSet<_> = step["findings"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|f| f["problem"].as_str())
        .collect();
    Some(
        if problems.contains("forced_compression") || problems.contains("meter") {
            "forced_compress"
        } else if ["unreadable", "unnatural_japanese", "reading"]
            .iter()
            .any(|s| problems.contains(s))
        {
            "unreadable"
        } else if problems.contains("off_scene") {
            "off_context"
        } else if action == "explain" && step["purpose"] == "understand_meaning" {
            "ask_meaning"
        } else {
            "other"
        },
    )
}

pub fn lesson(kind: &str) -> Option<Value> {
    let (axis, note) = match kind {
        "unreadable" | "ask_meaning" => (
            "readability",
            "読みやすさを少し意識する（かな連続・謎語は控えめに）",
        ),
        "forced_compress" => ("compress", "要素を少し絞って余白を残すとよい"),
        "off_context" => ("scene", "材料・場面から大きく外れない方がよい"),
        _ => return None,
    };
    Some(
        json!({"lesson_type":axis,"note":note,"prefer_materials":true,
        "forbidden_fragments":[],"polarity":"tighten","strength":0.3}),
    )
}

#[derive(Debug, Default, Clone, Deserialize, Serialize)]
pub struct RecallQuery {
    pub biome_id: Option<String>,
    #[serde(default)]
    pub biome_ids: Vec<String>,
    pub place_label: Option<String>,
    pub since: Option<DateTime<Utc>>,
    pub until: Option<DateTime<Utc>>,
    pub time_label: Option<String>,
}
fn biome(s: &str) -> &str {
    s.trim().strip_prefix("minecraft:").unwrap_or(s.trim())
}
impl RecallQuery {
    fn filtered(&self) -> bool {
        self.biome_id.is_some()
            || !self.biome_ids.is_empty()
            || self.since.is_some()
            || self.until.is_some()
    }
    fn matches(&self, row: &Value) -> bool {
        let allowed: HashSet<_> = self
            .biome_ids
            .iter()
            .chain(self.biome_id.iter())
            .map(|s| biome(s))
            .filter(|s| !s.is_empty())
            .collect();
        if !allowed.is_empty()
            && !allowed.contains(biome(row["world"]["biome"].as_str().unwrap_or("")))
        {
            return false;
        }
        if self.since.is_some() || self.until.is_some() {
            let Some(at) = date(&row["created_at"]) else {
                return false;
            };
            if self.since.is_some_and(|s| at < s) || self.until.is_some_and(|u| at >= u) {
                return false;
            }
        }
        true
    }
}

impl MemoryStore {
    /// Old root records plus migration session records, never evaluation logs,
    /// other checkout roots, or symbolic session directories.
    pub(crate) fn poem_rows(&self, filename: &str) -> Result<Vec<Value>> {
        let mut out = rows(&self.root().join("long_term").join(filename))?;
        let entries = match fs::read_dir(self.root().join("sessions")) {
            Ok(e) => Some(e),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Err(e.into()),
        };
        let mut paths = vec![];
        for entry in entries.into_iter().flatten() {
            let entry = entry?;
            if entry.file_type()?.is_dir() {
                paths.push(entry.path().join("long_term").join(filename));
            }
        }
        paths.sort();
        for path in paths {
            out.extend(rows(&path)?);
        }
        // Entry IDs from separate sessions can share second/sequence components.
        // Deduplicate copied records, never discard a different poem on ID alone.
        let mut records = HashSet::new();
        out.retain(|r| {
            r["id"]
                .as_str()
                .is_none_or(|_| records.insert(r.to_string()))
        });
        Ok(out)
    }

    pub fn active_lessons(&self, now: DateTime<Utc>) -> Result<Vec<Value>> {
        let entries = self.poem_rows("haiku_entries.jsonl")?;
        let lessons = rows(&self.root().join("long_term/haiku_lessons.jsonl"))?;
        Ok(select_lessons(&lessons, &entries, now))
    }

    pub fn loosen_lessons(&self, at: DateTime<Utc>) -> Result<()> {
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let row = json!({"id":format!("hlesson_{}",uuid::Uuid::new_v4().simple()),"created_at":at.to_rfc3339(),
            "lesson_type":"*","note":"","prefer_materials":false,"forbidden_fragments":[],
            "polarity":"loosen","strength":0.0,"from_entry_id":null,"from_critique_id":null});
        append_line(
            &mut locked_file(&self.root().join("long_term/haiku_lessons.jsonl"))?,
            &row,
        )
    }

    /// Validated feedback only; proposal requests deliberately never add lessons.
    /// This is auxiliary recording, independent of canonical/pending poem saves.
    pub fn save_feedback(
        &self,
        mut critique: Value,
        step: &Value,
        at: DateTime<Utc>,
    ) -> Result<()> {
        let Some(kind) = feedback_kind(step) else {
            return Ok(());
        };
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        critique["id"] = format!("hcrit_{}", uuid::Uuid::new_v4().simple()).into();
        critique["created_at"] = at.to_rfc3339().into();
        critique["kind"] = kind.into();
        critique["player_text"] =
            clipped(critique["player_text"].as_str().unwrap_or(""), 240).into();
        critique["surface_at_time"] =
            clipped(critique["surface_at_time"].as_str().unwrap_or(""), 200).into();
        append_line(
            &mut locked_file(&self.root().join("long_term/haiku_critiques.jsonl"))?,
            &critique,
        )?;
        if step["action"] != "propose_revision"
            && let Some(mut l) = lesson(kind)
        {
            l["id"] = format!("hlesson_{}", uuid::Uuid::new_v4().simple()).into();
            l["created_at"] = at.to_rfc3339().into();
            l["from_entry_id"] = critique["entry_id"].clone();
            l["from_critique_id"] = critique["id"].clone();
            append_line(
                &mut locked_file(&self.root().join("long_term/haiku_lessons.jsonl"))?,
                &l,
            )?;
        }
        Ok(())
    }

    pub fn search_poems(&self, query: &RecallQuery) -> Result<Vec<Value>> {
        let mut candidates = self.poem_rows("haiku_revisions.jsonl")?.into_iter().map(|r|
            json!({"kind":"revision","id":r["id"],"created_at":r["created_at"],"original_text":r["original_text"],
                "revised_text":r["revised_text"],"comment":r["comment"],"world":r["world"]})).collect::<Vec<_>>();
        candidates.extend(self.poem_rows("haiku_entries.jsonl")?.into_iter().map(|r|
            json!({"kind":"entry","id":r["id"],"created_at":r["created_at"],"original_text":r["text"],
                "revised_text":null,"comment":r["interpretation"],"world":r["world"]})));
        candidates.retain(|r| query.matches(r));
        candidates.sort_by_key(|r| std::cmp::Reverse(date(&r["created_at"])));
        let mut seen = HashSet::new();
        candidates.retain(|r| {
            let text = r["original_text"].as_str().unwrap_or("").trim();
            text.is_empty() || seen.insert(text.to_owned())
        });
        candidates.truncate(3);
        Ok(candidates)
    }

    pub fn recall_reply(&self, q: &RecallQuery) -> Result<String> {
        let mut hits = self.search_poems(q)?;
        let place = q.place_label.as_deref().or(q.biome_id.as_deref());
        let time = q.time_label.as_deref();
        let mut prefix = match (time, place) {
            (Some(t), Some(p)) => format!("{t}の{p}あたりで覚えとる句やと…"),
            (Some(t), None) => format!("{t}の句やと…"),
            (None, Some(p)) => format!("{p}で覚えとる句やと…"),
            _ => "覚えとる句やと…".into(),
        };
        if hits.is_empty() && q.filtered() {
            hits = self.search_poems(&RecallQuery::default())?;
            prefix = if !hits.is_empty() && (place.is_some() || time.is_some()) {
                "ぴったりは無いけど、覚えとる句やと…"
            } else {
                "覚えとる句やと…"
            }
            .into();
        }
        if hits.is_empty() {
            return Ok("それに合う句、まだ覚えとらへんで。".into());
        }
        for h in hits.iter().take(2) {
            let at = h["created_at"]
                .as_str()
                .unwrap_or("")
                .chars()
                .take(10)
                .collect::<String>();
            let place = h["world"]["biome"]
                .as_str()
                .filter(|s| !s.is_empty())
                .unwrap_or("どこか");
            let original = h["original_text"]
                .as_str()
                .unwrap_or("")
                .replace('\n', " / ");
            let label = if at.is_empty() {
                place.to_owned()
            } else {
                format!("{at} {place}")
            };
            prefix.push_str(
                &match h["revised_text"].as_str().filter(|s| !s.is_empty()) {
                    Some(r) => format!(
                        " {label}: 元「{original}」直し「{}」",
                        r.replace('\n', " / ")
                    ),
                    None => format!(" {label}: 「{original}」"),
                },
            );
        }
        Ok(prefix)
    }
}

fn select_lessons(lessons: &[Value], entries: &[Value], now: DateTime<Utc>) -> Vec<Value> {
    let times: Vec<_> = entries
        .iter()
        .filter_map(|r| date(&r["created_at"]))
        .collect();
    let mut out = vec![];
    let mut seen = HashSet::new();
    let mut suppressed = HashSet::new();
    let mut all = false;
    for row in lessons.iter().rev().take(24) {
        let axis = row["lesson_type"]
            .as_str()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or("other");
        if row["polarity"]
            .as_str()
            .unwrap_or("tighten")
            .trim()
            .eq_ignore_ascii_case("loosen")
        {
            if axis == "*" {
                all = true;
            } else {
                suppressed.insert(axis);
            }
            continue;
        }
        if all
            || suppressed.contains(axis)
            || seen.contains(axis)
            || row["note"].as_str().is_none_or(|s| s.trim().is_empty())
        {
            continue;
        }
        if let Some(at) = date(&row["created_at"])
            && (at < now - Duration::days(14) || times.iter().filter(|t| **t > at).count() >= 6)
        {
            continue;
        }
        seen.insert(axis);
        out.push(row.clone());
        if out.len() == 3 {
            break;
        }
    }
    out
}
