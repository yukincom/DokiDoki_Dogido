//! Read-only projections of the configured memory store. Never creates files.
use serde_json::{Value, json};
use std::{fs, path::Path};

#[derive(Clone, Copy)]
pub enum View {
    Haiku,
    Profile,
    Summary,
}
impl View {
    pub fn disabled(self) -> Value {
        match self {
            Self::Haiku => json!([]),
            _ => json!({}),
        }
    }
}
fn document(path: &Path) -> Option<Value> {
    match fs::read(path) {
        Ok(bytes) => match serde_json::from_slice(&bytes) {
            Ok(value) => Some(value),
            Err(error) => {
                tracing::warn!(event="memory_document_load_failed", path=%path.display(), %error);
                None
            }
        },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => {
            tracing::warn!(event="memory_document_load_failed", path=%path.display(), %error);
            None
        }
    }
}
pub fn read(root: &Path, view: View) -> anyhow::Result<Value> {
    Ok(match view {
        View::Haiku => json!(crate::poem_book::rows(root)?),
        View::Profile => {
            let loaded = document(&root.join("long_term/player_profile.json"))
                .filter(Value::is_object)
                .unwrap_or(json!({}));
            let player = loaded
                .get("player_name")
                .filter(|v| !v.is_null() && **v != "")
                .cloned()
                .unwrap_or(json!("main_player"));
            let mut profile = json!({"player_name":player,"progress":{}});
            for (id, label) in [
                ("story/mine_diamond", "ダイヤモンド！"),
                ("story/enter_the_end", "おしまい？"),
                ("nether/root", "ネザー"),
                ("end/elytra", "空はどこまでも高く"),
            ] {
                profile["progress"][id] =
                    json!({"label":label,"unlocked":false,"first_unlocked_at":null});
                if let Some(extra) = loaded["progress"][id].as_object() {
                    profile["progress"][id]
                        .as_object_mut()
                        .unwrap()
                        .extend(extra.clone());
                }
            }
            for (key, value) in loaded.as_object().unwrap() {
                if key != "progress" {
                    profile[key] = value.clone();
                }
            }
            profile
        }
        View::Summary => match document(&root.join("short_term/rolling_summary.json")) {
            Some(value) if value.is_object() => value,
            Some(_) => json!({}),
            None => {
                json!({"updated_at":null,"startup_summary":"","open_topics":[],"recent_tone":""})
            }
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn missing_documents_have_python_defaults_and_never_create_directories() {
        let root =
            std::env::temp_dir().join(format!("dogido-memory-view-{}", uuid::Uuid::new_v4()));
        assert_eq!(read(&root, View::Haiku).unwrap(), json!([]));
        assert_eq!(
            read(&root, View::Profile).unwrap()["progress"]
                .as_object()
                .unwrap()
                .len(),
            4
        );
        assert_eq!(
            read(&root, View::Summary).unwrap()["open_topics"],
            json!([])
        );
        assert!(!root.exists());
    }
    #[test]
    fn existing_documents_preserve_order_extensions_and_progress_defaults() {
        let root =
            std::env::temp_dir().join(format!("dogido-memory-view-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(root.join("long_term")).unwrap();
        fs::create_dir_all(root.join("sessions/s1/long_term")).unwrap();
        fs::create_dir_all(root.join("short_term")).unwrap();
        fs::create_dir_all(root.join("eval")).unwrap();
        fs::write(
            root.join("long_term/haiku_entries.jsonl"),
            "{\"id\":\"a\",\"text\":\"句\"}\nBAD\n42\n",
        )
        .unwrap();
        fs::write(
            root.join("sessions/s1/long_term/haiku_entries.jsonl"),
            "{\"id\":\"b\"}\n",
        )
        .unwrap();
        fs::write(
            root.join("eval/episodes.jsonl"),
            "{\"id\":\"must-not-be-memory\"}\n",
        )
        .unwrap();
        fs::write(root.join("long_term/player_profile.json"), json!({"player_name":"試験", "extra":1, "progress":{"story/mine_diamond":{"unlocked":true}, "unknown":{"unlocked":true}}}).to_string()).unwrap();
        fs::write(
            root.join("short_term/rolling_summary.json"),
            json!({"startup_summary":"保存した要約","extra":true}).to_string(),
        )
        .unwrap();
        assert_eq!(
            read(&root, View::Haiku).unwrap(),
            json!([{"id":"a","text":"句"},{"id":"b"}])
        );
        let profile = read(&root, View::Profile).unwrap();
        assert_eq!(profile["player_name"], "試験");
        assert_eq!(profile["extra"], 1);
        assert_eq!(
            profile["progress"]["story/mine_diamond"]["label"],
            "ダイヤモンド！"
        );
        assert_eq!(profile["progress"]["story/mine_diamond"]["unlocked"], true);
        assert!(profile["progress"].get("unknown").is_none());
        assert_eq!(
            read(&root, View::Summary).unwrap(),
            json!({"startup_summary":"保存した要約","extra":true})
        );
        fs::write(root.join("short_term/rolling_summary.json"), "[]").unwrap();
        assert_eq!(read(&root, View::Summary).unwrap(), json!({}));
        fs::write(root.join("short_term/rolling_summary.json"), "BAD").unwrap();
        assert_eq!(read(&root, View::Summary).unwrap()["startup_summary"], "");
        fs::remove_dir_all(root).unwrap();
    }
}
