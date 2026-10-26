//! 川柳と通常会話の履歴をつなぐ。戦闘判断・復帰判断・発話生成は既存の経路を使う。
use super::{Dialogue, Session, history::History};
use serde_json::{Value, json};

/// 通常履歴と短期ログで共有する、一件の出来事。
pub(super) struct StoryEvent<'a> {
    pub id: &'a str,
    pub workshop: Option<&'a str>,
    pub kind: &'a str,
    pub text: &'a str,
    pub details: Value,
}

/// 通常会話が既に持つ文脈を相談へ渡す。相談由来のeventはw.dialogueと重なるため戻さない。
pub(super) fn workshop_context(s: &Session) -> String {
    let history = s.workshop_context_rows();
    let ordinary: Vec<_> = history
        .iter()
        .filter(|row| {
            row["reaction"] != "story"
                && (row["role"] != "user"
                    || history.iter().any(|reply| {
                        reply["role"] == "assistant"
                            && reply["turn_id"]
                                == format!("{}:reply", row["turn_id"].as_str().unwrap_or(""))
                    }))
        })
        .cloned()
        .collect();
    std::iter::once(History::lines_for(&ordinary))
        .chain(s.combat_digest.iter().map(|note| format!("出来事: {note}")))
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}
impl Dialogue {
    /// 相談の出来事を通常履歴へ一度だけ接続する。通常会話の完了対や句の材料にはしない。
    pub(super) fn share_story(
        &self,
        sid: &str,
        s: &mut Session,
        event: StoryEvent<'_>,
    ) {
        let changed = if event.kind == "workshop_dialogue" {
            s.history.story_turn(event.id, event.text)
        } else { s.history.story(event.id, event.text) };
        if changed {
            self.append_story(sid, &s.instance_id, event);
        }
    }
    /// 既存の確定・配送境界から受け取った記録を、既存writerで短期ログへ追記するだけ。
    pub(super) fn append_story(
        &self,
        sid: &str,
        instance: &str,
        event: StoryEvent<'_>,
    ) {
        if !self.config.haiku.memory_enabled
            || sid.is_empty()
            || !sid
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
        {
            return;
        }
        self.workshop_records.append(
            self.config
                .haiku
                .memory_dir
                .join("sessions")
                .join(sid)
                .join("short_term/current_session.jsonl"),
            json!({"type":"dialogue_event","schema_version":"1","time":chrono::Utc::now(),
                "session_id":sid,"session_instance":instance,"event_id":event.id,
                "workshop_id":event.workshop,"kind":event.kind,"text":event.text,"details":event.details}),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dialogue::workshop_record::tests::fixture;
    #[tokio::test]
    async fn workshop_reads_existing_normal_context_once_and_never_reimports_its_own_story() {
        let f = fixture();
        {
            let mut data = f.d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            s.history.push("normal", "user", "この景色好きだよ");
            s.history.push("normal", "assistant", "一緒に眺めよか。");
            s.history.push("unanswered", "user", "届いていない入力");
            s.history.push("aftermath", "assistant", "まだ気になるわ。");
            s.combat_digest.push_back("ゾンビを倒した".into());
            f.d.share_story(
                "s",
                s,
                StoryEvent {
                    id: "workshop:1",
                    workshop: None,
                    kind: "workshop_dialogue",
                    text: "相談本文は専用履歴にある",
                    details: json!({}),
                },
            );
            let context = workshop_context(s);
            assert!(context.contains("プレイヤー: この景色好きだよ"));
            assert!(context.contains("ドギド: 一緒に眺めよか。"));
            assert!(context.contains("ドギド: まだ気になるわ。"));
            assert_eq!(context.matches("ゾンビを倒した").count(), 1);
            assert!(!context.contains("相談本文は専用履歴にある"));
            assert!(!context.contains("届いていない入力"));
        }
        f.finish().await;
    }
    #[tokio::test]
    async fn shared_reply_is_full_text_once_in_normal_history_and_short_log() {
        let f = fixture();
        let path =
            f.d.config
                .haiku
                .memory_dir
                .join("sessions/s/short_term/current_session.jsonl");
        let full = "返すつもりだった言葉。".repeat(120);
        {
            let mut data = f.d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            for _ in 0..2 {
                f.d.share_story(
                    "s",
                    s,
                    StoryEvent {
                        id: "unspoken:1",
                        workshop: None,
                        kind: "workshop_unspoken",
                        text: &full,
                        details: json!({}),
                    },
                );
            }
            assert_eq!(s.history.rows().len(), 1);
            assert_eq!(s.history.rows()[0]["text"], full);
            assert!(s.history.completed_pairs().is_empty());
        }
        f.d.workshop_records.flush().await;
        let saved = std::fs::read_to_string(path).unwrap();
        assert_eq!(saved.lines().count(), 1);
        let row: Value = serde_json::from_str(saved.trim()).unwrap();
        assert_eq!(row["text"], full);
        f.finish().await;
    }
}
