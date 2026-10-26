//! 冒険の接続寿命と保存を管理する。切断時の未要約分は終了処理へ引き継ぐ。
//! 正常停止でworkerを止めた後、未要約が10往復を超えるときだけ要約し、短い残りは原文で保存する。
//! 要約失敗時は既存あらすじを残し、未要約分の原文は永続化せず未収録を明記する。
use super::{Dialogue, Session, history::History};
use crate::journey::Record;

pub(super) struct Draft {
    record: Record,
    history: History,
}
/// 接続ごとに一度だけ取り出す。preview・テキスト相談室は冒険の保管対象にしない。
pub(super) fn take(s: &mut Session) -> Option<Draft> {
    let mut record = s.journey.take()?;
    if s.text_workshop {
        return None;
    }
    record.ended_at = Some(chrono::Utc::now());
    Some(Draft {
        record,
        history: s.history.clone(),
    })
}
impl Dialogue {
    pub(super) async fn persist_journey(&self, record: Record) {
        if !self.config.haiku.memory_enabled {
            return;
        }
        let root = self.config.haiku.memory_dir.clone();
        let id = record.id.clone();
        let result =
            tokio::task::spawn_blocking(move || crate::journey::save(&root, &record)).await;
        if !matches!(&result, Ok(Ok(()))) {
            tracing::error!(event = "journey_save_failed", journey_id = id, ?result);
        }
    }
    /// cancel_allと登録jobの終了後に呼ぶ。新入力が来ない固定snapshotだけを一度処理する。
    pub(super) async fn finish_journeys(&self) {
        let drafts = {
            let mut d = self.data.lock().unwrap();
            let mut drafts = std::mem::take(&mut d.closed_journeys);
            drafts.extend(d.sessions.values_mut().filter_map(take));
            drafts
        };
        if !self.config.haiku.memory_enabled {
            return;
        }
        if !drafts.is_empty() {
            tracing::info!(
                event = "journey_finishing",
                count = drafts.len(),
                message = "旅の記録をまとめて保存しています"
            );
        }
        for mut draft in drafts {
            if let Some(batch) = draft.history.final_compaction() {
                if batch.turns <= 10 {
                    draft.record.raw_tail = crate::journey::TranscriptLine::from_rows(&batch.rows);
                } else {
                    match self.summarize_history(&batch).await {
                        Ok(text) => {
                            draft.history.commit_compaction(&batch, &text);
                        }
                        Err(error) => {
                            draft.record.omitted_turns = batch.turns;
                            draft.record.unfinished = true;
                            tracing::warn!(event="journey_final_summary_failed", journey_id=draft.record.id, %error);
                        }
                    }
                }
            }
            // 各あらすじは生成した順のまま保存し、再要約を追加しない。
            draft.record.synopses = draft.history.synopses();
            self.persist_journey(draft.record).await;
        }
    }
}
