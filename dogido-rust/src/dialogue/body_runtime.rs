//! HTTP登録のownerと生eventを身体状態へ配線し、既存world_contextに姿勢・視認の読取専用コピーを添える。
//! 接続のロック所有者はDialogue。状態・会話の生成やモデル呼出しを増やさない。
use super::{Dialogue, Session};
use crate::{
    conversation_observation::Context,
    events::GameEvent,
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
    let age = (chrono::Utc::now() - event.observed_at.with_timezone(&chrono::Utc)).num_milliseconds();
    // 時計が少し進んだclientは0msとして扱い、1秒以上の未来時刻は現在値にしない。
    let transport_age = (age >= -1000).then_some(age.max(0) as u64);
    s.body_observation.observe(
        event.body_observation.as_ref(),
        s.player_uuid.as_deref(),
        event.player.dimension.as_deref(),
        Instant::now(),
        transport_age,
    );
    let now = Instant::now();
    s.body_autonomy.observe(event.body_autonomy.as_ref(),s.body_observation.accepted(now),now,transport_age);
}
/// 姿勢の受理台帳から、その判断一回分の身体感覚を固定する。
pub(super) fn event(s: &Session, raw: &GameEvent) -> GameEvent {
    let now = Instant::now();
    s.body_observation.sensory_event(raw, now, s.body_autonomy.stationary(now))
}
pub(super) fn context(s: &Session) -> Option<Context> {
    let raw = s.environment_latest.as_ref().or(s.latest.as_ref())?;
    Some(context_for(s, &event(s, raw)))
}
/// 視認・感覚と計算済みの位置関係を会話へ渡す。生姿勢は内部比較と診断にだけ残す。
/// 失効後は過去の環境欄を持ち越さず、プレイヤーの観測への代用もしない。
pub(super) fn context_for(s: &Session, event: &GameEvent) -> Context {
    // 停止通知直後の新鮮なsnapshotも、停止中の現在位置として使わない。
    if s.foreground.game_paused {
        return Context {
            observations: serde_json::json!({"body_observation":{"status":"unavailable","reason":"game_paused"},
                "observer":"dogido","sensory_status":"unavailable"}),
            ..Context::default()
        };
    }
    let origin = event.body_origin().expect("accepted body sensory view");
    let c = if origin.available {
        s.conversation_observation.context().unwrap_or_default()
    } else {
        Context::default()
    };
    let mut c = with_context(c, event).for_model();
    if let Some(reassurance) = s.body_autonomy.reassurance(Instant::now()) {
        c.observations["reassurance"] = reassurance_context(serde_json::json!(reassurance));
    }
    if let Some(attention) = s.body_autonomy.attention(Instant::now()) {
        c.observations["attention"] = attention;
    }
    let now = Instant::now();
    if let Some(current) = s.body_autonomy.current_body(now) {
        c.observations["current_body"] = current;
        let choices = s.body_autonomy.recent_choices(now);
        if choices.as_array().is_some_and(|choices| !choices.is_empty()) {
            c.observations["body_action_history"] = choices;
        }
    }
    if let Some(operation) = s.body_requests.model_operation(
        s.player_uuid.as_deref(), event.player.dimension.as_deref(),
        s.body_observation.accepted(now).map(|sample| &sample.context),
    ) {
        c.observations["body_operation"] = operation;
    }
    c
}
/// 共通観測には実測値と尺度のラベルだけを渡す。回復・視野の計算規則は身体コードに置く。
pub(super) fn reassurance_context(mut value: serde_json::Value) -> serde_json::Value {
    value["label"] = serde_json::json!("安心度");
    value["scale"] = serde_json::json!([0,100]);
    value
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
        if let Some(changes) = details.pointer("/world_context/changes").and_then(serde_json::Value::as_array) {
            result["body_position_changes_input"] = serde_json::json!(changes.iter()
                .filter(|change| change["kind"] == "body_position_relation").collect::<Vec<_>>());
        }
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
