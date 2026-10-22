//! 保存済みの句と実送達済み対話を読むだけの研究用投影。
use crate::poem_trash as trash;
use anyhow::{Context, Result, ensure};
use chrono::{DateTime, FixedOffset, NaiveDate, TimeZone, Utc};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
};

pub(super) fn dialogue(dir: &Path) -> Result<BTreeMap<String, Vec<Value>>> {
    let mut links: BTreeMap<(String, String), BTreeSet<String>> = BTreeMap::new();
    for row in trash::raw_rows(dir, "haiku_workshop_turns.jsonl")? {
        for state in [&row, &row["state_before"], &row["state_after"]] {
            if let (Some(workshop), Some(entry)) =
                (state["workshop_id"].as_str(), state["entry_id"].as_str())
                && !workshop.is_empty()
                && !entry.is_empty()
            {
                links
                    .entry((
                        row["session_id"].as_str().unwrap_or("").into(),
                        workshop.into(),
                    ))
                    .or_default()
                    .insert(entry.into());
            }
        }
    }
    let folder = dir.join("short_term");
    match fs::symlink_metadata(&folder) {
        Ok(m) => ensure!(
            m.is_dir() && !m.file_type().is_symlink(),
            "unsafe_directory"
        ),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(e) => return Err(e.into()),
    }
    let path = folder.join("current_session.jsonl");
    trash::regular(&path)?;
    let body = match fs::read_to_string(path) {
        Ok(body) => body,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(e) => return Err(e.into()),
    };
    let mut result: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    let mut seen: BTreeMap<String, Value> = BTreeMap::new();
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        let row: Value = serde_json::from_str(line)?;
        if row["type"] != "dialogue_event" || row["kind"] != "workshop_dialogue" {
            continue;
        }
        let Some(workshop) = row["workshop_id"].as_str() else {
            continue;
        };
        let Some(entries) = links
            .get(&(
                row["session_id"].as_str().unwrap_or("").into(),
                workshop.into(),
            ))
            .filter(|entries| entries.len() == 1)
        else {
            continue;
        };
        let details = &row["details"];
        let (Some(player), Some(dogido)) = (
            details["player_text"].as_str(),
            details["dogido_text"].as_str(),
        ) else {
            continue;
        };
        // 同じ本文でも別turnは残す。時刻や本文の類似性では結び付けない。
        let value = json!({
            "time":row["time"], "workshopId":workshop, "turnId":details["input_turn_id"],
            "eventId":row["event_id"], "player":player, "dogido":dogido,
            "delivery":"confirmed"
        });
        let key = serde_json::to_string(&(
            row["session_instance"].clone(),
            workshop,
            details["input_turn_id"]
                .as_str()
                .or(row["event_id"].as_str())
                .context("missing_dialogue_identity")?,
        ))?;
        if let Some(old) = seen.get(&key) {
            ensure!(
                old["player"] == value["player"] && old["dogido"] == value["dogido"],
                "conflicting_dialogue"
            );
            continue;
        }
        seen.insert(key, value.clone());
        result
            .entry(entries.first().unwrap().clone())
            .or_default()
            .push(value);
    }
    for turns in result.values_mut() {
        turns.sort_by(|a, b| a["time"].as_str().cmp(&b["time"].as_str()));
    }
    Ok(result)
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Period {
    pub start_month: String,
    pub end_month: String,
}
impl Period {
    fn month(value: &str) -> Result<NaiveDate> {
        ensure!(
            value.len() == 7
                && value.as_bytes()[4] == b'-'
                && value
                    .bytes()
                    .enumerate()
                    .all(|(i, b)| i == 4 || b.is_ascii_digit()),
            "invalid_month"
        );
        let date = NaiveDate::parse_from_str(&format!("{value}-01"), "%Y-%m-%d")?;
        ensure!(("2000-01"..="9998-12").contains(&value), "invalid_month");
        Ok(date)
    }
    pub(super) fn bounds(&self) -> Result<(DateTime<FixedOffset>, DateTime<FixedOffset>)> {
        let start = Self::month(&self.start_month)?;
        let end = Self::month(&self.end_month)?;
        ensure!(start <= end, "invalid_period");
        let end = end
            .checked_add_months(chrono::Months::new(1))
            .context("invalid_month")?;
        let jst = FixedOffset::east_opt(9 * 3600).unwrap();
        Ok((
            jst.from_local_datetime(&start.and_hms_opt(0, 0, 0).unwrap())
                .unwrap(),
            jst.from_local_datetime(&end.and_hms_opt(0, 0, 0).unwrap())
                .unwrap(),
        ))
    }
}

pub(super) fn export(
    store: &super::store::Store,
    period: &Period,
    now: DateTime<Utc>,
) -> Result<Value> {
    let (start, end) = period.bounds()?;
    let player = store.player()?;
    let photos = store.photo_snapshot_now()?;
    let mut rows = vec![];
    for poem in super::store::poems(&store.memory, false)? {
        let at = DateTime::parse_from_rfc3339(
            poem["createdAt"].as_str().context("missing_created_at")?,
        )?;
        if at < start || at >= end {
            continue;
        }
        let id = poem["id"].as_str().unwrap();
        let note = &player["poems"][id];
        let slots = photos["placements"][format!("poems:{id}")]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let images: Vec<_> = slots.iter().filter_map(|id| photos["items"].as_array()?.iter().find(|p| p["id"] == *id)).map(|p| json!({
            "id":p["id"],"filename":p["filename"],"title":p["title"],"width":p["width"],"height":p["height"],
            "source":p["source"],"sha256":p["sha256"],"poemId":p["poemId"],"requestId":p["requestId"]
        })).collect();
        rows.push(json!({"id":id,"original":poem["original"],"current":poem["current"],
            "revisions":poem["history"],"materials":poem["materials"],"dialogue":poem["dialogue"],
            "recordStatus":poem["recordStatus"],"player":{"favorite":note["favorite"].as_bool().unwrap_or(false),"note":note["note"].as_str().unwrap_or("")},
            "photos":{"slots":slots,"references":images,"imageDataIncluded":false}}));
    }
    Ok(json!({"schemaVersion":1,"exportedAt":now.to_rfc3339(),
        "period":{"startMonth":period.start_month,"endMonth":period.end_month,"timezone":"Asia/Tokyo","basis":"original_created_at","startInclusive":start.to_rfc3339(),"endExclusive":end.to_rfc3339()},
        "historyScope":"all_saved_history_read_at_export","deletedPoemsIncluded":false,"poems":rows}))
}
