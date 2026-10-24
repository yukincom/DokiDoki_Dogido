//! 本棚の閲覧元。設定済みsnapshotをSSDから検証して読み、未同期の本人編集だけ重ねる。
//! ローカル正本はゲームと編集のため保持する。GETでは同期・修復・保存しない。
use super::{research_archive as archive, store::{self, Store}};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

impl Store {
    pub(super) fn library_view(&self) -> Result<Value> {
        let data = self.read()?;
        let mut local: BTreeMap<String, Value> = store::poems(&self.memory, false)?.into_iter()
            .map(|p| (p["id"].as_str().unwrap().into(), p)).collect();
        let mut player = json!({"revision":data["revision"],"catalog":data["catalog"],"poems":data["poems"]});
        let mut photos = self.photo_snapshot(&data)?;
        let Some(config) = archive::load_config(&self.memory)? else {
            photos["researchStorage"]["displaySource"] = "local".into();
            return Ok(json!({"poems":sorted(local.into_values().collect()),"player":player,"photos":photos}));
        };
        let mut failures = Vec::new();
        let mut result = Vec::new();
        let mut archived = 0;
        let mut pending = 0;
        let deleted: BTreeSet<String> = store::poems(&self.memory, true)?.iter()
            .filter_map(|p| p["id"].as_str().map(Into::into)).collect();
        match archive::open(config.clone()) {
            Ok(archive) => {
                for (id, raw) in data["researchSnapshots"].as_object().into_iter().flatten() {
                    if deleted.contains(id) || raw["archive_id"] != config.archive_id { continue; }
                    let read = (|| -> Result<Value> {
                        let head: archive::ArchivedSnapshot = serde_json::from_value(raw.clone())?;
                        ensure!(head.poem_id == *id, "archive_snapshot_scope");
                        let record = archive.read_snapshot(&head)?;
                        ensure!(record["id"] == *id && record["original"].is_object()
                            && record["current"].is_object() && record["revisions"].is_array()
                            && record["dialogue"].is_array(), "archive_invalid_poem_record");
                        Ok(record)
                    })();
                    match read {
                        Ok(record) => {
                            let current = local.remove(id);
                            if record["deletedFromBookshelf"] == true && current.is_none() { continue; }
                            let mut poem = store::project_poem(id, &record["original"], &record["current"],
                                &record["revisions"], &record["materials"], &record["dialogue"], &record["recordStatus"]);
                            poem["storageSource"] = "archive".into();
                            poem["readOnly"] = current.is_none().into();
                            // 保存待ちの最新revisionや新しい対話を古いSSD snapshotで巻き戻さない。
                            if let Some(current) = current {
                                let new_dialogue = current["dialogue"].as_array().map_or(0, Vec::len)
                                    > record["dialogue"].as_array().map_or(0, Vec::len);
                                if current["original"] != record["original"] || current["current"] != record["current"]
                                    || current["history"] != record["revisions"] || new_dialogue
                                    || record["deletedFromBookshelf"] == true {
                                    poem = current;
                                    poem["storageSource"] = "local_pending".into();
                                    pending += 1;
                                }
                            }
                            if player["poems"].get(id).is_none() {
                                player["poems"][id] = record["player"].clone();
                            }
                            result.push(poem);
                            archived += 1;
                        }
                        Err(error) => failures.push(format!("{id}: {error:#}")),
                    }
                }
            }
            Err(error) => failures.push(format!("{error:#}")),
        }
        for (_, mut poem) in local {
            poem["storageSource"] = if failures.is_empty() {"local_pending"} else {"local_fallback"}.into();
            pending += 1;
            result.push(poem);
        }
        let status = &mut photos["researchStorage"];
        status.as_object_mut().context("invalid_research_status")?;
        status["displaySource"] = if !failures.is_empty() {"unavailable"}
            else if pending > 0 {"mixed"} else {"archive"}.into();
        status["readPoems"] = archived.into();
        status["pendingPoems"] = pending.into();
        status["readError"] = json!(failures.first());
        Ok(json!({"poems":sorted(result),"player":player,"photos":photos}))
    }
}

fn sorted(mut poems: Vec<Value>) -> Vec<Value> {
    poems.sort_by(|a, b| b["createdAt"].as_str().cmp(&a["createdAt"].as_str()));
    poems
}
