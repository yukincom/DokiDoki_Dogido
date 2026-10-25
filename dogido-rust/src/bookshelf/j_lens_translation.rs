//! 本棚の明示操作だけで候補に訳を添える。元の候補・スコア・句を変更しない。
use super::{j_lens, store::Store};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs, time::Duration};

const PROMPT: &str = include_str!("../../../tools/dogido-library/j-lens-translation-prompt.txt");

#[derive(Clone, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Selection { pub phase: String, pub layer: usize }

fn hash(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }

pub(super) struct Prepared { key: String, source: Value, rows: Vec<Value> }

impl Store {
    pub(super) fn translation_source(&self, id: &str, index: usize, config: &j_lens::Config, selection: &Selection) -> Result<Prepared> {
        ensure!(matches!(selection.phase.as_str(), "before" | "during" | "after") && selection.layer < 30, "translation_selection_invalid");
        let readout = self.j_lens(id, index, config, false)?;
        let position = readout["positions"].as_array().unwrap().iter().find(|p| p["phase"] == selection.phase).context("translation_selection_invalid")?;
        let mut rows = position["layers"][selection.layer]["candidates"].as_array().context("translation_selection_invalid")?.clone();
        let mut words = config.clone(); words.worker = config.worker.with_file_name("j_lens_words.py");
        let input: Vec<_> = rows.iter().map(|r| r["piece"].clone()).collect();
        let copied: Vec<bool> = serde_json::from_slice(&j_lens::run_worker(&words, serde_json::to_vec(&input)?, Duration::from_secs(10))?)?;
        ensure!(copied.len() == rows.len(), "translation_dictionary_invalid");
        for (row, copy) in rows.iter_mut().zip(&copied) { row["copy_original"] = json!(copy); }
        let source = json!({"readout_sha256":hash(&serde_json::to_vec(&readout)?),"manifest_sha256":readout["manifest_sha256"],
            "response_id":readout["response_id"],"phase":selection.phase,"layer":selection.layer,"prompt_sha256":hash(PROMPT.as_bytes()),
            "copy_original":copied,"dictionary_worker_sha256":hash(&fs::read(&words.worker)?)});
        Ok(Prepared {key:hash(&serde_json::to_vec(&source)?),source,rows})
    }

    pub(super) fn translation_cache(&self, p: &Prepared) -> Result<Option<Value>> {
        let path = self.memory.join("bookshelf/j-lens-translations").join(format!("{}.json", p.key));
        for dir in [self.memory.join("bookshelf"), path.parent().unwrap().to_path_buf()] {
            if dir.exists() { ensure!(!fs::symlink_metadata(dir)?.file_type().is_symlink(), "unsafe_directory"); }
        }
        crate::poem_trash::regular(&path)?;
        if !path.exists() { return Ok(None); }
        ensure!(fs::metadata(&path)?.len() < 64 * 1024, "translation_cache_invalid");
        let envelope: Value = serde_json::from_slice(&fs::read(path)?)?;
        let value = &envelope["result"];
        ensure!(envelope["sha256"] == hash(&serde_json::to_vec(value)?) && value["source"] == p.source, "translation_cache_invalid");
        validate_rows(value, &p.rows)?;
        Ok(Some(value.clone()))
    }

    pub(super) fn save_translation(&self, p: &Prepared, value: &Value) -> Result<()> {
        validate_rows(value, &p.rows)?;
        let _lock = self.write_lock()?;
        let dir = self.memory.join("bookshelf/j-lens-translations");
        fs::create_dir_all(&dir)?;
        ensure!(!fs::symlink_metadata(&dir)?.file_type().is_symlink(), "unsafe_directory");
        let path = dir.join(format!("{}.json", p.key));
        crate::poem_trash::regular(&path)?;
        crate::poem_trash::atomic(&path, &serde_json::to_vec(&json!({"sha256":hash(&serde_json::to_vec(value)?),"result":value}))?)
    }
}

fn validate_rows(value: &Value, rows: &[Value]) -> Result<()> {
    let translations = value["translations"].as_array().context("translation_output_invalid")?;
    ensure!(value["schema"] == "dogido.j_lens_translation.v1" && translations.len() == rows.len(), "translation_output_invalid");
    for (t, row) in translations.iter().zip(rows) {
        ensure!(t["id"] == row["id"] && t["piece"] == row["piece"] && t["text"].as_str().is_some_and(|s| s.chars().count() <= 120), "translation_output_invalid");
        if row["copy_original"] == true { ensure!(t["text"] == row["piece"], "translation_original_changed"); }
    }
    Ok(())
}

pub(super) async fn translate(p: &Prepared, game_port: u16) -> Result<Value> {
    let input: Vec<Value> = p.rows.iter().filter(|r| r["copy_original"] != true)
        .map(|r| json!({"id":r["id"],"piece":r["piece"]})).collect();
    let mut translated = BTreeMap::new();
    let mut model = Value::Null;
    if !input.is_empty() {
        // 同じモデルを使う本体が動いている間は生成要求を送らない。起動・停止はしない。
        if tokio::time::timeout(Duration::from_millis(500), tokio::net::TcpStream::connect(("127.0.0.1", game_port))).await.is_ok_and(|r| r.is_ok()) {
            anyhow::bail!("translation_game_running");
        }
        let client = reqwest::Client::builder().timeout(Duration::from_secs(90)).no_proxy().build()?;
        let models: Value = client.get("http://127.0.0.1:8080/v1/models").send().await?.error_for_status()?.json().await?;
        let name = models["data"][0]["id"].as_str().context("translation_model_missing")?;
        let body = json!({"model":name,"stream":false,"temperature":0,"max_tokens":1600,
            "response_format":{"type":"json_object"},"messages":[{"role":"system","content":PROMPT},
                {"role":"user","content":serde_json::to_string(&input)?}]});
        let response: Value = client.post("http://127.0.0.1:8080/v1/chat/completions").json(&body).send().await?.error_for_status()?.json().await?;
        let content = response["choices"][0]["message"]["content"].as_str().context("translation_output_invalid")?;
        let value: Value = serde_json::from_str(content).context("translation_output_invalid")?;
        let entries = value["translations"].as_array().context("translation_output_invalid")?;
        ensure!(entries.len() == input.len(), "translation_output_invalid");
        for row in entries {
            let id = row["id"].as_u64().context("translation_output_invalid")?;
            let text = row["text"].as_str().context("translation_output_invalid")?;
            ensure!(!text.trim().is_empty() && text.chars().count() <= 120 && !text.contains(['\n','\r'])
                && input.iter().any(|r| r["id"] == id) && translated.insert(id, text.to_string()).is_none(), "translation_output_invalid");
        }
        model = json!({"api_id":name,"response_model":response["model"],"response_id":response["id"],"endpoint":"http://127.0.0.1:8080"});
    }
    let rows: Vec<Value> = p.rows.iter().map(|r| {
        let piece = r["piece"].as_str().unwrap();
        let copied = r["copy_original"] == true;
        json!({"id":r["id"],"piece":piece,"text":if copied {piece} else {translated[&r["id"].as_u64().unwrap()].as_str()},"method":if copied {"original"}else{"model_translation"}})
    }).collect();
    Ok(json!({"schema":"dogido.j_lens_translation.v1","source":p.source,"model":model,
        "created_at":chrono::Utc::now().to_rfc3339(),"translations":rows}))
}

pub(super) fn originals(p: &Prepared) -> Value {
    json!({"status":"not_prepared","translations":p.rows.iter().filter(|r| r["copy_original"] == true)
        .map(|r| json!({"id":r["id"],"piece":r["piece"],"text":r["piece"],"method":"original"})).collect::<Vec<_>>()})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn running_game_blocks_model_request_and_originals_need_no_model() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let mut p = Prepared {key:"test".into(),source:json!({}),rows:vec![json!({"id":1,"piece":"宝","copy_original":true})]};
        let result = translate(&p,listener.local_addr().unwrap().port()).await.unwrap();
        assert_eq!(result["translations"][0]["text"], "宝"); assert!(result["model"].is_null());
        p.rows.push(json!({"id":2,"piece":"treasure","copy_original":false}));
        assert_eq!(translate(&p,listener.local_addr().unwrap().port()).await.unwrap_err().to_string(), "translation_game_running");
        assert_eq!(originals(&p)["translations"].as_array().unwrap().len(),1);
    }
}
