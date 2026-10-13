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
/// 姿勢の受理台帳から、その判断一回分の身体感覚を固定する。
pub(super) fn event(s: &Session, raw: &GameEvent) -> GameEvent {
    s.body_observation.sensory_event(raw, Instant::now())
}
pub(super) fn context(s: &Session) -> Option<Context> {
    let raw = s.environment_latest.as_ref().or(s.latest.as_ref())?;
    Some(context_for(s, &event(s, raw)))
}
/// 視認を先頭へ置き、同じ身体snapshotの姿勢・前回値を診断と会話へ残す。
/// 失効後は過去の環境欄を持ち越さず、プレイヤーの観測への代用もしない。
pub(super) fn context_for(s: &Session, event: &GameEvent) -> Context {
    let origin = event.body_origin().expect("accepted body sensory view");
    let c = if origin.available {
        s.conversation_observation.context().unwrap_or_default()
    } else {
        Context::default()
    };
    with_context(c, event)
}
/// 同じ身体材料を共有観測・周期通知にも一回だけ添える。
pub(super) fn with_context(mut c: Context, event: &GameEvent) -> Context {
    let origin = event.body_origin().expect("accepted body sensory view");
    let mut observations = serde_json::json!({
        "body_observation":origin.body,
        "observer":"dogido",
        "sensory_status":if origin.available {"observed"} else {"unavailable"},
    });
    if let Some(facts) = c.observations.as_object() {
        for (key, value) in facts {
            if !matches!(
                key.as_str(),
                "vision" | "observer" | "body_observation" | "sensory_status"
            ) {
                observations[key] = value.clone();
            }
        }
    }
    c.observations = observations;
    c
}

/// 生成へ渡した身体観測を発話の診断行へ固定する。現在値を後から読み直さず、本文や履歴へは加えない。
pub(super) fn attach_input(result: &mut serde_json::Value, details: &serde_json::Value) {
    if let Some(body) = details.pointer("/world_context/observations/body_observation") {
        result["body_observation_input"] = body.clone();
    }
}

#[cfg(test)]
mod tests;

/// 単体検査の手動Sessionへ、実経路と同じ受理済み身体fixtureを設定する。
#[cfg(test)]
pub(super) fn install_test_senses(s: &mut Session, event: &GameEvent, tick: u64) -> GameEvent {
    let raw = crate::body::tests::with_senses(serde_json::json!(event), tick);
    s.player_uuid = Some("11111111-1111-4111-8111-111111111111".into());
    observe(s, &raw);
    s.latest = Some(raw.clone());
    s.environment_latest = Some(raw.clone());
    self::event(s, &raw)
}
