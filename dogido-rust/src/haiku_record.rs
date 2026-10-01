//! Completed automatic-haiku records, append-only memory, and initial workshop HUD.
//! No generation, kana conversion, editing, world actions, or implicit lifecycle
//! changes occur here. The caller supplies completion clocks and save ownership.
use anyhow::{Context, Result};
use chrono::{DateTime, SecondsFormat, Timelike, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::{
    collections::VecDeque,
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::Mutex,
    time::{Duration, Instant},
};

pub const DEFAULT_OPEN_TIMEOUT: Duration = Duration::from_secs(240);
pub const DEFAULT_IDLE_TIMEOUT: Duration = Duration::from_secs(120);
pub mod verse;
pub use verse::{LinePosition, ResolvedLines};
pub(crate) static SAVE_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct HaikuLine {
    pub line_id: String,
    pub line_index: usize,
    pub position: String,
    pub canonical_name: String,
    pub surface_text: String,
    pub reading_text: String,
    pub source_atom_ids: Vec<String>,
    pub source_atoms: Vec<Map<String, Value>>,
    pub provenance: String,
}

fn default_route() -> Option<String> {
    Some("haiku".into())
}

/// Pure helper output. `created_at` is deliberately not accepted from a helper.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct PreparedEmission {
    pub text: String,
    pub surface_text: Option<String>,
    pub reading_text: Option<String>,
    pub lines: Vec<HaikuLine>,
    #[serde(default)]
    pub materials: Map<String, Value>,
    pub interpretation: Option<String>,
    pub preface: Option<String>,
    pub biome: Option<String>,
    pub structure: Option<String>,
    pub time_phase: Option<String>,
    pub dimension: Option<String>,
    pub event_sequence: Option<i64>,
    #[serde(default = "default_route")]
    pub route: Option<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Emission {
    pub created_at: DateTime<Utc>,
    #[serde(flatten)]
    pub prepared: PreparedEmission,
}

impl PreparedEmission {
    /// Only canonical, already-resolved three-line records can be emitted.
    /// Match HaikuEmission.__post_init__: line records own display/readings and
    /// synchronize line_sources without inventing missing source evidence.
    pub fn complete(mut self, created_at: DateTime<Utc>) -> Result<Emission> {
        let resolved = ResolvedLines::try_from(self.lines.as_slice())?;
        self.surface_text = Some(resolved.surface());
        self.reading_text = Some(resolved.reading());
        let sources: Vec<_> = self.lines.iter().filter(|l| !l.source_atom_ids.is_empty()).map(|l| {
            json!({"line_index":l.line_index,"text":l.reading_text,"atom_ids":l.source_atom_ids,"sources":l.source_atoms})
        }).collect();
        if !sources.is_empty() {
            self.materials.insert("line_sources".into(), sources.into());
        }
        // Python datetime stores microseconds. Use that same precision for IDs,
        // serialized timestamps, and all comparisons to the original store.
        let created_at = created_at
            .with_nanosecond(created_at.nanosecond() / 1000 * 1000)
            .context("invalid completion timestamp")?;
        Ok(Emission {
            created_at,
            prepared: self,
        })
    }
}

impl Emission {
    pub fn entry_id(&self) -> String {
        let suffix = self.prepared.event_sequence.map_or_else(
            || format!("{:06}", self.created_at.timestamp_subsec_micros()),
            |n| n.to_string(),
        );
        format!("hk_{}_{suffix}", self.created_at.format("%Y%m%d_%H%M%S"))
    }
    pub fn created_at_json(&self) -> String {
        self.created_at.to_rfc3339_opts(
            if self.created_at.timestamp_subsec_micros() == 0 {
                SecondsFormat::Secs
            } else {
                SecondsFormat::Micros
            },
            false,
        )
    }
    pub fn entry(&self) -> Value {
        let e = &self.prepared;
        let surface = e.surface_text.as_deref().unwrap_or(&e.text).trim();
        let reading = e.reading_text.as_deref().unwrap_or(&e.text).trim();
        json!({
            "id":self.entry_id(),"created_at":self.created_at_json(),
            "author":"dogido","kind":"agent_haiku","text":surface,
            "surface_text":surface,"reading_text":reading,"lines":e.lines,
            "preface":e.preface,"interpretation":e.interpretation,
            "world":{"biome":e.biome,"structure":e.structure,"time_phase":e.time_phase,"dimension":e.dimension},
            "trigger":{"event_sequence":e.event_sequence,"route":e.route},
            "materials_snapshot":e.materials,
        })
    }
    pub fn short_term_entry(&self, session_id: &str) -> Value {
        let e = &self.prepared;
        json!({"type":"haiku_emitted","time":self.created_at_json(),"session_id":session_id,
            "sequence":e.event_sequence,"text":e.surface_text.as_deref().unwrap_or(&e.text),
            "surface_text":e.surface_text,"reading_text":e.reading_text,"lines":e.lines,
            "interpretation":e.interpretation,"biome":e.biome,"structure":e.structure})
    }
}

#[derive(Clone, Debug)]
pub struct MemoryStore {
    root: PathBuf,
}
#[derive(Debug, Serialize)]
pub struct SaveResult {
    pub entry_id: String,
    pub entry: Value,
    pub inserted: bool,
}
impl MemoryStore {
    /// The parent chooses an isolated Rust-migration root. Construction is pure.
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }
    pub fn entries_path(&self) -> PathBuf {
        self.root.join("long_term/haiku_entries.jsonl")
    }
    pub fn root(&self) -> &Path {
        &self.root
    }
    /// Like Python, this is separate from long-term deduplication. Call once at
    /// the parent-owned emission boundary, never for explicit resave requests.
    pub fn append_haiku_emission(&self, session_id: &str, emission: &Emission) -> Result<()> {
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let mut file = locked_file(&self.root.join("short_term/current_session.jsonl"))?;
        append_line(&mut file, &emission.short_term_entry(session_id))
    }
    pub fn save_agent_haiku(
        &self,
        session_id: &str,
        player_name: &str,
        emission: &Emission,
    ) -> Result<SaveResult> {
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let path = self.entries_path();
        let mut file = locked_file(&path)?;
        let entry_id = emission.entry_id();
        let entry = emission.entry();
        let mut inserted = true;
        for line in BufReader::new(&file).lines() {
            let line = line?;
            if let Ok(row) = serde_json::from_str::<Value>(&line)
                && row.get("id").and_then(Value::as_str) == Some(&entry_id)
            {
                inserted = false;
                break;
            }
        }
        if inserted {
            append_line(&mut file, &entry)?;
        }
        // File drop releases the OS lock on every success/error return.
        tracing::info!(
            event = "haiku_autosaved",
            session_id,
            player_name,
            entry_id,
            inserted
        );
        Ok(SaveResult {
            entry_id,
            entry,
            inserted,
        })
    }
}

pub(crate) fn locked_file(path: &Path) -> Result<File> {
    fs::create_dir_all(path.parent().context("memory parent")?)?;
    let file = OpenOptions::new()
        .create(true)
        .read(true)
        .append(true)
        .open(path)?;
    // Lock the actual file as well as this process: independent Rust store
    // instances/processes cannot race between checking an ID and appending.
    // Existing Python does not participate in this advisory lock; never share
    // this migration root with a concurrent Python writer.
    file.lock().context("cannot lock haiku JSONL")?;
    Ok(file)
}

pub(crate) fn append_line(file: &mut File, entry: &Value) -> Result<()> {
    let mut bytes = Vec::new();
    if file.metadata()?.len() > 0 {
        file.seek(SeekFrom::End(-1))?;
        let mut last = [0];
        file.read_exact(&mut last)?;
        if last[0] != b'\n' {
            bytes.push(b'\n');
        }
    }
    serde_json::to_writer(&mut bytes, entry)?;
    bytes.push(b'\n');
    file.write_all(&bytes)?;
    file.flush()?;
    file.sync_all()?;
    Ok(())
}

#[derive(Clone, Debug)]
pub struct Workshop {
    pub current_lines: Vec<HaikuLine>,
    pub pending: Option<crate::workshop_edit::Pending>,
    pub revision_id: Option<String>,
    pub version: u64,
    pub emission: Emission,
    pub entry_id: Option<String>,
    pub hud_id: String,
    pub materials: Map<String, Value>,
    pub open: bool,
    pub close_reason: Option<String>,
    /// 一句専用。実再生済みの対だけを最大4往復保持する。
    pub dialogue: VecDeque<Value>,
    pub agent_steps: VecDeque<Value>,
    /// プレイヤーが会話で示した案。採用待ちの pending とは別に保持する。
    pub conversation_candidate: Option<crate::workshop_candidate::Candidate>,
    /// 一度示された相談・編集対象。短期対話の押し出しや返答不成立では消さない。
    pub discussion_target: Option<crate::workshop_target::Target>,
    pub drift_count: usize,
    pub followup: crate::workshop_followup::Stage,
    pub recovery: crate::workshop_combat::Recovery,
    pub provisional: Option<String>,
    completed: Instant,
    last_activity: Instant,
    paused_at: Option<Instant>,
    paused_total: Duration,
}
impl Workshop {
    pub fn is_open(&self) -> bool {
        self.open
    }
    pub fn emission(&self) -> &Emission {
        &self.emission
    }
    pub fn open(emission: Emission, entry_id: Option<String>, completed: Instant) -> Self {
        let e = &emission.prepared;
        let mut materials = e.materials.clone();
        for (key, value, visibility) in [
            ("interpretation", &e.interpretation, None),
            ("biome", &e.biome, Some("biome")),
            ("structure", &e.structure, None),
            ("time_phase", &e.time_phase, Some("sky")),
        ] {
            let visible = visibility.is_none_or(|key| {
                materials
                    .get("material_visibility")
                    .and_then(Value::as_object)
                    .and_then(|v| v.get(key))
                    != Some(&Value::Bool(false))
            });
            if visible
                && !materials.contains_key(key)
                && let Some(value) = value.as_ref().filter(|v| !v.is_empty())
            {
                materials.insert(key.into(), value.clone().into());
            }
        }
        Self {
            current_lines: emission.prepared.lines.clone(),
            pending: None,
            revision_id: None,
            version: 0,
            emission,
            entry_id,
            hud_id: format!("workshop_{}", uuid::Uuid::new_v4().simple()),
            materials,
            open: true,
            close_reason: None,
            dialogue: VecDeque::new(),
            agent_steps: VecDeque::new(),
            conversation_candidate: None,
            discussion_target: None,
            drift_count: 0,
            followup: crate::workshop_followup::Stage::Discussion,
            recovery: crate::workshop_combat::Recovery::default(),
            provisional: None,
            completed,
            last_activity: completed,
            paused_at: None,
            paused_total: Duration::ZERO,
        }
    }
    pub fn completed_at(&self) -> Instant {
        self.completed
    }
    pub fn combat_paused(&self) -> bool {
        self.paused_at.is_some()
    }
    pub fn close(&mut self, reason: impl Into<String>) {
        self.conversation_candidate = None;
        self.discussion_target = None;
        self.provisional = None;
        self.recovery = crate::workshop_combat::Recovery::default();
        self.followup = crate::workshop_followup::Stage::Discussion;
        self.open = false;
        self.close_reason = Some(reason.into());
        self.paused_at = None;
    }
    pub fn pause(&mut self, now: Instant) -> bool {
        if !self.open || self.combat_paused() {
            return false;
        }
        self.paused_at = Some(now.max(self.completed));
        self.provisional = None;
        self.recovery = crate::workshop_combat::Recovery::default();
        self.followup = crate::workshop_followup::Stage::Discussion;
        true
    }
    pub fn resume(&mut self, now: Instant) -> bool {
        if !self.open {
            return false;
        }
        let Some(started) = self.paused_at.take() else {
            return false;
        };
        self.paused_total += now.saturating_duration_since(started);
        self.record_activity(now);
        true
    }
    pub fn record_activity(&mut self, now: Instant) {
        self.last_activity = now.max(self.completed).max(self.last_activity);
    }
    /// Called by the worker/monitor only. Projection never expires a workshop.
    pub fn expire(&mut self, now: Instant, t_open: Duration, t_idle: Duration) -> bool {
        if !self.open || self.combat_paused() {
            return false;
        }
        if now
            .saturating_duration_since(self.completed)
            .saturating_sub(self.paused_total)
            >= t_open
        {
            self.close("timeout_open");
            true
        } else if now.saturating_duration_since(self.last_activity) >= t_idle {
            self.close("timeout_idle");
            true
        } else {
            false
        }
    }
}

/// Read-only canonical/pending projection. The owning snapshot cache adds its
/// revision when publishing; projection never changes adoption or lifetimes.
pub fn project_workshop(
    workshop: Option<&Workshop>,
    session_id: &str,
    observed_sequence: Option<i64>,
    mode: &str,
    thinking: bool,
) -> Value {
    let active = workshop.filter(|w| w.open);
    let danger = active.is_some_and(|w| {
        w.combat_paused() || (w.provisional.is_none() && matches!(mode, "alert" | "panic"))
    });
    json!({"schema_version":1,"session_id":session_id,"observed_sequence":observed_sequence.unwrap_or(0),
        "workshop_id":active.map(|w|w.hud_id.as_str()),
        "state":if active.is_some() {if danger {"danger"} else {"open"}} else {"closed"},
        "character_state":if active.is_none() && mode=="normal" && thinking {"thinking"} else {"normal"},
        "canonical_lines":active.map(|w|w.current_lines.iter().map(|l|l.surface_text.trim()).collect::<Vec<_>>()).unwrap_or_default(),
        "pending_lines":active.and_then(|w|w.pending.as_ref()).map(|p|p.lines.iter().map(|l|l.surface_text.as_str()).collect::<Vec<_>>()).unwrap_or_default(),
        "editing":active.is_some_and(|w|w.pending.is_some()),"selected_line":active.and_then(|w|w.pending.as_ref()).and_then(|p| if p.generated_basis.as_ref().is_some_and(|b| b.target_indices.len()!=1) {None}else{Some(p.selected_line)}),"provisional_resume":active.is_some_and(|w|w.provisional.is_some())})
}

#[cfg(test)]
mod tests {
    use super::*;
    const LINE_META: [(&str, &str, &str); 3] = [
        ("line_1", "upper", "上五"),
        ("line_2", "middle", "中七"),
        ("line_3", "lower", "下五"),
    ];

    use std::sync::Arc;

    fn prepared() -> PreparedEmission {
        let lines: Vec<_> = LINE_META.iter().enumerate().map(|(i,(id,pos,name))| json!({
            "line_id":id,"line_index":i,"position":pos,"canonical_name":name,
            "surface_text":(["草地の日","黒き剣の","影の寒さ"][i]),
            "reading_text":(["くさちのひ","くろきつるぎの","かげのさむさ"][i]),
            "source_atom_ids":[format!("observation:{i}")],
            "source_atoms":[{"atom_id":format!("observation:{i}"),"text":"材料","extra":{"kept":true}}],
            "provenance":"generated"
        })).collect();
        serde_json::from_value(json!({"text":"くさちのひ くろきつるぎの かげのさむさ",
            "lines":lines,"surface_text":"old","reading_text":"old","materials":{},
            "biome":"plains","structure":null,"time_phase":"day","dimension":"minecraft:overworld",
            "event_sequence":42,"preface":"ここで一句。","interpretation":"草地の剣"}))
        .unwrap()
    }
    fn emission() -> Emission {
        prepared()
            .complete("2026-09-26T12:34:56.123456789Z".parse().unwrap())
            .unwrap()
    }
    struct Scratch(PathBuf);
    impl Scratch {
        fn new() -> Self {
            Self(std::env::temp_dir().join(format!("dogido-haiku-record-{}", uuid::Uuid::new_v4())))
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    #[test]
    fn completion_syncs_canonical_records_without_replacing_materials() {
        let mut p = prepared();
        p.materials
            .insert("unrelated".into(), json!({"keep":[true,"意味"]}));
        let e = p.complete(Utc::now()).unwrap();
        assert_eq!(
            e.prepared.surface_text.as_deref(),
            Some("草地の日\n黒き剣の\n影の寒さ")
        );
        assert_eq!(
            e.prepared.materials["line_sources"][0]["sources"][0]["extra"]["kept"],
            true
        );
        assert_eq!(
            e.prepared.materials["unrelated"],
            json!({"keep":[true,"意味"]})
        );
        assert_eq!(e.entry()["author"], "dogido");
        assert_eq!(e.entry()["trigger"]["route"], "haiku");
        assert!(e.entry().get("player_name").is_none());
        assert_eq!(e.short_term_entry("s1")["session_id"], "s1");
    }
    #[test]
    fn helper_cannot_supply_time_or_malformed_line_identity() {
        let mut raw = serde_json::to_value(prepared()).unwrap();
        raw["created_at"] = json!("2026-01-01T00:00:00Z");
        assert!(serde_json::from_value::<PreparedEmission>(raw).is_err());
        for damage in [
            "count",
            "index",
            "id",
            "position",
            "name",
            "surface",
            "reading",
            "provenance",
            "source",
        ] {
            let mut p = prepared();
            match damage {
                "count" => {
                    p.lines.pop();
                }
                "index" => p.lines[0].line_index = 1,
                "id" => p.lines[0].line_id = "line_2".into(),
                "position" => p.lines[0].position = "middle".into(),
                "name" => p.lines[0].canonical_name = "中七".into(),
                "surface" => p.lines[0].surface_text = "a\nb".into(),
                "reading" => p.lines[0].reading_text = "".into(),
                "provenance" => p.lines[0].provenance = " ".into(),
                _ => p.lines[0].source_atom_ids.push("".into()),
            }
            assert!(p.complete(Utc::now()).is_err(), "{damage}");
        }
    }
    #[test]
    fn completion_time_and_python_compatible_ids_use_microseconds() {
        let e = emission();
        assert_eq!(e.created_at_json(), "2026-09-26T12:34:56.123456+00:00");
        assert_eq!(e.entry_id(), "hk_20260926_123456_42");
        let mut p = prepared();
        p.event_sequence = None;
        let e = p.complete("2026-09-26T12:34:56Z".parse().unwrap()).unwrap();
        assert_eq!(e.entry_id(), "hk_20260926_123456_000000");
        assert_eq!(e.created_at_json(), "2026-09-26T12:34:56+00:00");
    }
    #[test]
    fn store_is_append_only_deduplicates_and_preserves_prior_bytes() {
        let dir = Scratch::new();
        let store = MemoryStore::new(&dir.0);
        assert!(!dir.0.exists());
        let path = store.entries_path();
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let before = b"{\"id\":\"old\",\"text\":\"prior\"}\ninvalid prior row";
        fs::write(&path, before).unwrap();
        assert!(
            store
                .save_agent_haiku("s", "p", &emission())
                .unwrap()
                .inserted
        );
        let saved = fs::read(&path).unwrap();
        assert!(saved.starts_with(before));
        assert!(
            !store
                .save_agent_haiku("s", "p", &emission())
                .unwrap()
                .inserted
        );
        assert_eq!(saved, fs::read(&path).unwrap());
        assert_eq!(String::from_utf8(saved).unwrap().lines().count(), 3);
        store.append_haiku_emission("s", &emission()).unwrap();
        let short: Value = serde_json::from_str(
            &fs::read_to_string(dir.0.join("short_term/current_session.jsonl")).unwrap(),
        )
        .unwrap();
        assert_eq!(short, emission().short_term_entry("s"));
    }
    #[test]
    fn independent_store_instances_do_not_race_duplicate_id() {
        let dir = Scratch::new();
        let e = Arc::new(emission());
        let jobs: Vec<_> = (0..12)
            .map(|_| {
                let store = MemoryStore::new(&dir.0);
                let e = e.clone();
                std::thread::spawn(move || store.save_agent_haiku("s", "p", &e).unwrap().inserted)
            })
            .collect();
        let inserted = jobs
            .into_iter()
            .map(|j| usize::from(j.join().unwrap()))
            .sum::<usize>();
        assert_eq!(inserted, 1);
        assert_eq!(
            fs::read_to_string(MemoryStore::new(&dir.0).entries_path())
                .unwrap()
                .lines()
                .count(),
            1
        );
    }
    #[test]
    fn timeouts_start_at_completion_and_projection_is_read_only() {
        let start = Instant::now();
        let mut w = Workshop::open(emission(), Some("hk".into()), start);
        let before = project_workshop(Some(&w), "s", Some(5), "normal", true);
        assert_eq!(before["character_state"], "normal");
        assert_eq!(before["canonical_lines"][0], "草地の日");
        assert_eq!(
            before,
            project_workshop(Some(&w), "s", Some(5), "normal", true)
        );
        assert!(!w.expire(
            start + Duration::from_secs(119),
            DEFAULT_OPEN_TIMEOUT,
            DEFAULT_IDLE_TIMEOUT
        ));
        assert!(w.expire(
            start + Duration::from_secs(120),
            DEFAULT_OPEN_TIMEOUT,
            DEFAULT_IDLE_TIMEOUT
        ));
        assert_eq!(w.close_reason.as_deref(), Some("timeout_idle"));
        assert_eq!(
            project_workshop(Some(&w), "s", None, "normal", true)["state"],
            "closed"
        );
    }
    #[test]
    fn current_player_idea_keeps_target_and_failures_across_history_and_pause() {
        let now = Instant::now();
        let mut w = Workshop::open(emission(), None, now);
        let draft = crate::workshop_candidate::Draft {
            proposal: crate::workshop_candidate::Proposal {
                line_index: Some(0),
                target_fragment: "くさち".into(),
                replacement_text: "はる".into(),
            },
            evidence: "くさちをはるにするのはどう？".into(),
            validation_codes: vec!["meter_not_exact".into()],
        };
        w.conversation_candidate = crate::workshop_candidate::Candidate::from_player(
            draft,
            &w.current_lines,
            w.version,
            "くさちをはるにするのはどう？",
        );
        assert!(w.conversation_candidate.as_ref().unwrap().is_current(
            &w.current_lines,
            w.version,
            false
        ));
        w.dialogue.clear();
        w.pause(now);
        w.resume(now);
        let c = w.conversation_candidate.as_ref().unwrap();
        assert_eq!(c.view()["proposal"]["target_fragment"], "くさち");
        assert_eq!(c.view()["validation_codes"][0], "meter_not_exact");
        assert!(!c.is_current(&w.current_lines, w.version + 1, false));
        assert!(!c.is_current(&w.current_lines, w.version, true));
        w.close("explicit_close");
        assert!(w.conversation_candidate.is_none());
        assert!(!w.resume(now));
    }

    #[test]
    fn pause_freezes_timeout_and_resume_excludes_combat_duration() {
        let start = Instant::now();
        let mut w = Workshop::open(emission(), None, start);
        assert!(w.pause(start + Duration::from_secs(30)));
        assert!(!w.pause(start + Duration::from_secs(40)));
        assert!(!w.expire(
            start + Duration::from_secs(1000),
            DEFAULT_OPEN_TIMEOUT,
            DEFAULT_IDLE_TIMEOUT
        ));
        assert_eq!(
            project_workshop(Some(&w), "s", None, "normal", false)["state"],
            "danger"
        );
        assert!(w.resume(start + Duration::from_secs(1000)));
        assert!(!w.resume(start + Duration::from_secs(1001)));
        w.record_activity(start + Duration::from_secs(1100));
        assert!(!w.expire(
            start + Duration::from_secs(1209),
            DEFAULT_OPEN_TIMEOUT,
            DEFAULT_IDLE_TIMEOUT
        ));
        assert!(w.expire(
            start + Duration::from_secs(1210),
            DEFAULT_OPEN_TIMEOUT,
            DEFAULT_IDLE_TIMEOUT
        ));
        assert_eq!(w.close_reason.as_deref(), Some("timeout_open"));
        assert_eq!(
            w.emission.created_at_json(),
            "2026-09-26T12:34:56.123456+00:00"
        );
    }
    #[test]
    fn visibility_and_closed_thinking_follow_initial_projection_contract() {
        let mut p = prepared();
        p.materials = json!({"material_visibility":{"biome":false,"sky":false}})
            .as_object()
            .unwrap()
            .clone();
        let mut w = Workshop::open(p.complete(Utc::now()).unwrap(), None, Instant::now());
        assert!(!w.materials.contains_key("biome"));
        assert!(!w.materials.contains_key("time_phase"));
        assert!(w.materials.contains_key("interpretation"));
        assert_eq!(
            project_workshop(Some(&w), "s", None, "alert", false)["state"],
            "danger"
        );
        w.close("next_haiku");
        assert!(!w.pause(Instant::now()));
        assert_eq!(
            project_workshop(Some(&w), "s", None, "normal", true)["character_state"],
            "thinking"
        );
        assert_eq!(
            project_workshop(None, "s", None, "panic", true)["character_state"],
            "normal"
        );
    }
}
