//! 保存済み内部表現の読出し。生成モデル・句の正本へは書き込まない。
use super::store::Store;
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs, io::{Read, Seek, SeekFrom, Write}, path::PathBuf, process::{Command, Stdio}, time::{Duration, Instant}};

#[derive(Clone, serde::Deserialize)]
pub struct Config {
    pub python: PathBuf,
    pub worker: PathBuf,
    pub assets: PathBuf,
}

fn hash(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }

fn positions(m: &Value) -> Result<Vec<Value>> {
    let [n, layers, width]: [u64; 3] = serde_json::from_value(m["shape"].clone())?;
    ensure!(n > 0 && layers == 30 && width == 2816, "j_lens_unsupported_capture");
    let mut selected = vec![("before", 0), ("during", (n - 1) / 2)];
    if m["status"] == "complete" { selected.push(("after", n - 1)); }
    selected.into_iter().map(|(phase, position)| {
        let row = m["positions"].get(position as usize).context("capture_position_missing")?;
        ensure!(row["generation_index"] == position && row["predicted_token_id"].as_u64().is_some(),
            "capture_token_position_mismatch");
        Ok(json!({"phase":phase,"position":position,"input_position":row["input_position"],
            "predicted_token_id":row["predicted_token_id"]}))
    }).collect()
}

fn validate(value: &Value, header: &Value, processor: &str, binding: &str) -> Result<()> {
    ensure!(value["schema"] == "dogido.j_lens.v1" && value["status"] == "exploratory_transfer"
        && value["processor_sha256"] == processor && value["binding_sha256"] == binding,
        "j_lens_cache_mismatch");
    for key in ["response_id", "manifest_sha256", "hidden_sha256", "model_sha256", "shape", "boundary", "backend"] {
        ensure!(value[key] == header[key], "j_lens_cache_mismatch");
    }
    let rows = value["positions"].as_array().context("j_lens_rows_missing")?;
    let expected = header["positions"].as_array().unwrap();
    ensure!(rows.len() == expected.len(), "j_lens_positions_mismatch");
    for (row, expected) in rows.iter().zip(expected) {
        for key in ["phase", "position", "input_position", "predicted_token_id"] {
            ensure!(row[key] == expected[key], "j_lens_positions_mismatch");
        }
        let layers = row["layers"].as_array().context("j_lens_layers_missing")?;
        ensure!(layers.len() == 30, "j_lens_layers_mismatch");
        for (index, layer) in layers.iter().enumerate() {
            ensure!(layer["layer"] == index, "j_lens_layers_mismatch");
            let candidates = layer["candidates"].as_array().context("j_lens_candidates_missing")?;
            ensure!(candidates.len() == 16 && candidates.iter().all(|v|
                v["id"].as_u64().is_some_and(|id| id < 262144) && v["piece"].as_str().is_some()
                && v["score"].as_f64().is_some_and(f64::is_finite)
                && v["logprob"].as_f64().is_some_and(f64::is_finite)), "j_lens_candidates_invalid");
        }
    }
    Ok(())
}

pub(super) fn run_worker(config: &Config, input: Vec<u8>, timeout: Duration) -> Result<Vec<u8>> {
    let start = Instant::now();
    let mut child = Command::new(&config.python).arg(&config.worker).arg("--config").arg(&config.assets)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()?;
    let stdout = child.stdout.take().unwrap();
    let reader = std::thread::spawn(move || {
        let mut output = Vec::new(); stdout.take(2_000_001).read_to_end(&mut output).map(|_| output)
    });
    let mut stdin = child.stdin.take().unwrap();
    let writer = std::thread::spawn(move || stdin.write_all(&input));
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if start.elapsed() <= timeout => std::thread::sleep(Duration::from_millis(25)),
            result => {
                let _ = child.kill(); let _ = child.wait();
                let _ = writer.join(); let _ = reader.join();
                if let Err(error) = result { return Err(error.into()); }
                anyhow::bail!("j_lens_timeout");
            }
        }
    };
    let written = writer.join().map_err(|_| anyhow::anyhow!("j_lens_writer_failed"))?;
    let output = reader.join().map_err(|_| anyhow::anyhow!("j_lens_reader_failed"))??;
    ensure!(status.success() && written.is_ok() && output.len() <= 2_000_000, "j_lens_worker_failed");
    Ok(output)
}

impl Store {
    pub(super) fn j_lens(&self, id: &str, index: usize, config: &Config, build: bool) -> Result<Value> {
        // Resolve the existing poem/request/archive route first. Never trust paths from a manifest.
        self.read_j_lens(config, build, |kind| self.internal_capture_file(id, index, kind))
    }

    pub(super) fn read_j_lens(&self, config: &Config, build: bool,
        open_capture: impl Fn(&str) -> Result<super::research_archive::CaptureFile>) -> Result<Value> {
        let mut file = open_capture("manifest")?;
        let mut bytes = Vec::new(); file.file.read_to_end(&mut bytes)?;
        let m = file.manifest;
        ensure!(m["backend"] == "llama.cpp" && m["boundary"] == "decoder_layer_output_pre_final_norm",
            "j_lens_unsupported_capture");
        let selected = positions(&m)?;
        let processor = hash(&fs::read(&config.worker)?);
        let assets = fs::read(&config.assets)?;
        let settings: Value = serde_json::from_slice(&assets)?;
        let binding = hash(&fs::read(settings["binding"].as_str().context("j_lens_binding_missing")?)?);
        let header = json!({"response_id":m["response_id"],"manifest_sha256":hash(&bytes),
            "hidden_sha256":m["hidden_sha256"],"model_sha256":m["identity"]["model"]["sha256"],
            "shape":m["shape"],"boundary":m["boundary"],"backend":m["backend"],"positions":selected});
        let key = hash(&serde_json::to_vec(&json!([header, processor, binding, hash(&assets)]))?);
        let parent = self.memory.join("bookshelf");
        if parent.exists() { ensure!(!fs::symlink_metadata(&parent)?.file_type().is_symlink(), "unsafe_directory"); }
        let dir = parent.join("j-lens");
        if dir.exists() { ensure!(!fs::symlink_metadata(&dir)?.file_type().is_symlink(), "unsafe_directory"); }
        let path = dir.join(format!("{key}.json"));
        crate::poem_trash::regular(&path)?;
        if path.exists() {
            ensure!(fs::metadata(&path)?.len() <= 2_000_000, "j_lens_cache_too_large");
            let envelope: Value = serde_json::from_slice(&fs::read(&path)?)?;
            let value = &envelope["result"];
            ensure!(envelope["sha256"] == hash(&serde_json::to_vec(value)?), "j_lens_cache_changed");
            validate(value, &header, &processor, &binding)?;
            return Ok(value.clone());
        }
        ensure!(build, "j_lens_not_prepared");
        let mut captured = open_capture("hidden")?;
        ensure!(captured.manifest == m, "capture_manifest_changed");
        let mut input = serde_json::to_vec(&header)?; input.push(b'\n');
        for position in &selected {
            let offset = position["position"].as_u64().unwrap() * 30 * 2816 * 4;
            captured.file.seek(SeekFrom::Start(offset))?;
            let start = input.len(); input.resize(start + 30 * 2816 * 4, 0);
            captured.file.read_exact(&mut input[start..])?;
        }
        let output = run_worker(config, input, Duration::from_secs(120))?;
        let value: Value = serde_json::from_slice(&output)?;
        validate(&value, &header, &processor, &binding)?;
        let _lock = self.write_lock()?;
        fs::create_dir_all(&dir)?;
        ensure!(!fs::symlink_metadata(&dir)?.file_type().is_symlink(), "unsafe_directory");
        let envelope = json!({"sha256":hash(&serde_json::to_vec(&value)?),"result":value});
        crate::poem_trash::atomic(&path, &serde_json::to_vec(&envelope)?)?;
        Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn worker_that_never_reads_stdin_still_times_out() {
        let root = std::env::temp_dir().join(format!("dogido-readout-timeout-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        let worker = root.join("sleep.py"); fs::write(&worker, "import time\ntime.sleep(5)\n").unwrap();
        let config = Config {python:"python3".into(),worker,assets:root.join("unused.json")};
        let started = Instant::now();
        let result = run_worker(&config, vec![0u8; 1_100_000], Duration::from_millis(100));
        assert_eq!(result.unwrap_err().to_string(), "j_lens_timeout");
        assert!(started.elapsed() < Duration::from_secs(3));
        fs::remove_dir_all(root).unwrap();
    }
}
