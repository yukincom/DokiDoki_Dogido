//! HTTP登録のownerと生eventを身体状態へ配線し、既存world_contextに姿勢・視認の読取専用コピーを添える。
//! 接続のロック所有者はDialogue。状態・会話の生成やモデル呼出しを増やさない。
use super::{Dialogue, Session};
use crate::{
    conversation_observation::Context,
    events::{EventTime, GameEvent},
};
use std::time::Instant;

impl Dialogue {
    /// 登録直後に一度だけ結び付ける。名前しかない接続やpreviewは身体の座標を受理しない。
    pub fn bind_body_owner(&self, id: &str, owner: Option<&str>) {
        let mut d = self.data.lock().unwrap();
        if let Some(s) = d.sessions.get_mut(id)
            && s.player_uuid.is_none()
        {
            s.player_uuid = owner
                .filter(|v| crate::body::contracts::canonical_uuid(v))
                .map(str::to_owned);
        }
    }
}
pub(super) fn observe(s: &mut Session, event: &GameEvent) {
    let transport_age = match &event.observed_at {
        EventTime::Aware(at) => {
            let age = (chrono::Utc::now() - at.with_timezone(&chrono::Utc)).num_milliseconds();
            // 時計が少し進んだclientは0msとして扱い、1秒以上の未来時刻は現在値にしない。
            (age >= -1000).then_some(age.max(0) as u64)
        }
        _ => None,
    };
    s.body_observation.observe(
        event.body_observation.as_ref(),
        s.player_uuid.as_deref(),
        event.player.dimension.as_deref(),
        Instant::now(),
        transport_age,
    );
}
pub(super) fn context(s: &Session) -> Option<Context> {
    let mut context = s.conversation_observation.context();
    if let Some(body) = s.body_observation.project(Instant::now()) {
        let c = context.get_or_insert_with(Context::default);
        // このコピーにだけ主語を記す。従来の周囲データはplayer由来であり、身体の視覚へ昇格させない。
        c.observations["observer"] = serde_json::json!("player");
        c.observations["body_observation"] = body;
    }
    context
}

/// 生成へ渡した身体観測を発話の診断行へ固定する。現在値を後から読み直さず、本文や履歴へは加えない。
pub(super) fn attach_input(result: &mut serde_json::Value, details: &serde_json::Value) {
    if let Some(body) = details.pointer("/world_context/observations/body_observation") {
        result["body_observation_input"] = body.clone();
    }
}

#[cfg(test)]
mod tests;
