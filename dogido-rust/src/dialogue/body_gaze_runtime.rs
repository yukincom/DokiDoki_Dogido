//! 実再生中の発話と現在観測から、見る対象の識別子だけを既存会話snapshotへ添える。
//! 座標取得・向きの計算・適用はMinecraft側。読取りで発話や観測の期限を更新しない。
use super::{Session, chat_context::CatalogLabels};
use crate::{body::senses::Senses, chat_observation, chat_validation, combat::model::Scope};
use serde_json::{Value, json};
use std::{collections::VecDeque, time::Instant};

/// 再生中の同一session行だけを対象にする。生成待ち・表示だけ・終了・失効時は解除を返す。
pub(super) fn snapshot(s: &Session, rows: &VecDeque<Value>) -> Value {
    let Some(row) = rows
        .iter()
        .rev()
        .find(|r| r["session_instance"] == s.instance_id && r["playback_status"] == "started")
    else {
        return Value::Null;
    };
    if s.foreground.game_paused || !s.delivery.voice_enabled {
        return Value::Null;
    }
    let Some(sample) = s.body_observation.accepted(Instant::now()) else {
        return Value::Null;
    };
    if row["combat_actions"].is_array() && row["speaking_action_index"].is_null() {
        return Value::Null;
    }
    let text = row["speaking_action_index"]
        .as_u64()
        .and_then(|index| row["combat_actions"].get(index as usize))
        .and_then(|action| action["text"].as_str())
        .unwrap_or_else(|| row["text"].as_str().unwrap_or(""));
    let mut targets = vec![];
    // 複数台詞を一つのturnへ束ねても、実際に開始した一件の対象だけを使う。
    if let Some(index) = row["speaking_action_index"].as_u64()
        && let Some(action) = s
            .warning
            .as_ref()
            .filter(|w| row["turn_id"] == w.turn)
            .and_then(|w| w.actions.get(index as usize))
    {
        match &action.scope {
            Scope::Visual(ids) => targets.extend(
                ids.iter()
                    .filter(|id| crate::body::contracts::canonical_uuid(id))
                    .map(|id| json!({"kind":"entity","id":id,"observer":"player"})),
            ),
            Scope::Auditory(ids) => targets.extend(
                ids.iter()
                    .map(|id| json!({"kind":"sound","id":id,"observer":"player"})),
            ),
            _ => {}
        }
    }
    if targets.is_empty()
        && let Senses::Observed(senses) = &sample.senses
    {
        targets = mentioned_targets(text, senses);
    }
    if targets.is_empty() {
        Value::Null
    } else {
        let turn = format!(
            "{}:{}",
            row["turn_id"].as_str().unwrap_or(""),
            row["speaking_action_index"].as_u64().unwrap_or(0)
        );
        json!({"turn_id":turn,"context":sample.context,"targets":targets.into_iter().take(16).collect::<Vec<_>>()})
    }
}

/// 既存のカタログ名抽出を使い、現在の感覚に対応する識別子へ絞る。匂い・過去記憶は入力しない。
fn mentioned_targets(text: &str, senses: &crate::body::senses::Sample) -> Vec<Value> {
    let mentioned = chat_validation::mentioned(text);
    let named = |kind: &str| {
        crate::catalog_knowledge::mob(kind, None, false, "speech_gaze")
            .is_some_and(|entry| mentioned.contains(&entry.label))
    };
    let mut found: Vec<(usize, u8, f64, Value)> = vec![];
    let mut visual = |kind: &str, id: Option<&str>, distance: Option<f64>| {
        if named(kind)
            && let Some(id) = id.filter(|id| crate::body::contracts::canonical_uuid(id))
        {
            let label = crate::environment::ambient::mob_label(kind);
            found.push((
                text.find(&label).unwrap_or(usize::MAX),
                1,
                distance.unwrap_or(f64::MAX),
                json!({"kind":"entity","id":id,"observer":"body"}),
            ));
        }
    };
    for mob in &senses.visual_threats {
        visual(&mob.r#type, mob.entity_id.as_deref(), mob.distance);
    }
    for mob in &senses.passive_mobs {
        visual(
            &mob.r#type,
            mob.identity.as_ref().map(|i| i.entity_id.as_str()),
            mob.distance,
        );
    }
    for sound in &senses.auditory_threats {
        let kind = chat_observation::resolve_mob(
            &sound.label,
            sound.sound_event.as_deref(),
            &CatalogLabels,
        );
        if kind.as_deref().is_some_and(named)
            && let Some(id) = &sound.source_id
        {
            let label = crate::environment::ambient::mob_label(kind.as_deref().unwrap_or(""));
            found.push((
                text.find(&label).unwrap_or(usize::MAX),
                0,
                sound.heard_ago_ms.unwrap_or(i64::MAX) as f64,
                json!({"kind":"sound","id":id,"observer":"body"}),
            ));
        }
    }
    for sound in &senses.ambient_sounds {
        let kind = chat_observation::resolve_mob(
            &sound.r#type,
            sound.sound_event.as_deref(),
            &CatalogLabels,
        );
        let label = chat_observation::environment_label(&sound.r#type, &CatalogLabels);
        if (kind.as_deref().is_some_and(named)
            || label
                .as_ref()
                .is_some_and(|label| !label.is_empty() && text.contains(label)))
            && let Some(id) = &sound.source_id
        {
            let label = kind
                .as_deref()
                .map(crate::environment::ambient::mob_label)
                .or(label)
                .unwrap_or_default();
            found.push((
                text.find(&label).unwrap_or(usize::MAX),
                0,
                sound.heard_ago_ms.unwrap_or(i64::MAX) as f64,
                json!({"kind":"sound","id":id,"observer":"body"}),
            ));
        }
    }
    // 同じ種類の実音と姿が共にある場合は、実際に発音した位置を優先する。
    // 一般名だけで「見えている別個体が鳴いた」と結び付けない。複数の音は新しい順。
    found.sort_by(|a, b| {
        a.0.cmp(&b.0)
            .then(a.1.cmp(&b.1))
            .then_with(|| a.2.total_cmp(&b.2))
    });
    found.into_iter().map(|(_, _, _, target)| target).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        body::contracts::Observation,
        dialogue::{Dialogue, DialogueConfig},
    };
    const A: &str = "44444444-4444-4444-8444-444444444444";
    const B: &str = "55555555-5555-4555-8555-555555555555";
    fn observation() -> Observation {
        let mut value: Value =
            serde_json::from_str(include_str!("../../fixtures/body-observation-v3.json")).unwrap();
        value["sample"]["senses"]["visual_threats"] = json!([
            {"type":"zombie","entity_id":A,"distance":3},
            {"type":"zombie","entity_id":B,"distance":5}
        ]);
        value["sample"]["senses"]["ambient_sounds"] = json!([
            {"type":"cow","source_id":"heard-cow","sound_event":"entity.cow.ambient","heard_ago_ms":500},
            {"type":"block:water","source_id":"heard-water","heard_ago_ms":500}
        ]);
        serde_json::from_value(value).unwrap()
    }
    #[test]
    fn named_current_mobs_use_distance_order_and_sounds_keep_source_identity() {
        let Observation::Observed { sample, .. } = observation() else {
            panic!()
        };
        let Senses::Observed(senses) = &sample.senses else {
            panic!()
        };
        assert_eq!(mentioned_targets("ゾンビがおる！",senses),json!([
            {"kind":"entity","id":A,"observer":"body"},{"kind":"entity","id":B,"observer":"body"}
        ]).as_array().unwrap().clone());
        assert_eq!(
            mentioned_targets("ウシの声がするな", senses),
            vec![json!({"kind":"sound","id":"heard-cow","observer":"body"})]
        );
        assert_eq!(
            mentioned_targets("水の音がする", senses),
            vec![json!({"kind":"sound","id":"heard-water","observer":"body"})]
        );
        assert!(mentioned_targets("スケルトンがおる", senses).is_empty());
        assert!(mentioned_targets("なんか匂うな", senses).is_empty());
        assert!(mentioned_targets("村人ゾンビがおる", senses).is_empty());
    }
    #[tokio::test]
    async fn only_started_same_instance_speech_uses_fresh_body_and_existing_warning_target() {
        let mut config = DialogueConfig {
            audio_enabled: false,
            llm_enabled: false,
            ..Default::default()
        };
        config.haiku.enabled = false;
        config.haiku.memory_enabled = false;
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "test", true);
        let mut data = dialogue.data.lock().unwrap();
        let session = data.sessions.get_mut("s").unwrap();
        session.delivery.voice_enabled = true;
        let obs = observation();
        let observe = |session: &mut Session, now| {
            session.body_observation.observe(
                Some(&obs),
                Some("11111111-1111-4111-8111-111111111111"),
                Some("minecraft:overworld"),
                now,
                Some(0),
            )
        };
        observe(session, Instant::now());
        let mut rows = VecDeque::from([
            json!({"turn_id":"turn","session_instance":session.instance_id,"text":"ゾンビがおる！","playback_status":"started"}),
        ]);
        assert_eq!(snapshot(session, &rows)["targets"][0]["id"], A);
        for status in [
            "queued",
            "generating",
            "completed",
            "cancelled",
            "failed",
            "audio_disabled",
            "not_selected",
        ] {
            rows[0]["playback_status"] = status.into();
            assert!(snapshot(session, &rows).is_null(), "{status}");
        }
        rows[0]["playback_status"] = "started".into();
        rows[0]["session_instance"] = "old".into();
        assert!(snapshot(session, &rows).is_null());
        rows[0]["session_instance"] = session.instance_id.clone().into();
        let (cancel, _) = tokio::sync::watch::channel(false);
        session.warning = Some(super::super::warnings::Active {
            turn: "turn".into(),
            actions: vec![
                crate::combat::model::Speech::visual("hostile", "後ろ！", vec![B.into()]),
                crate::combat::model::Speech::visual("hostile", "前！", vec![A.into()]),
            ],
            cancel,
            started: true,
            input: None,
            protected_until: None,
        });
        rows[0]["speaking_action_index"] = 0.into();
        assert_eq!(
            snapshot(session, &rows)["targets"],
            json!([{"kind":"entity","id":B,"observer":"player"}])
        );
        rows[0]["speaking_action_index"] = 1.into();
        assert_eq!(
            snapshot(session, &rows)["targets"],
            json!([{"kind":"entity","id":A,"observer":"player"}])
        );
        session.foreground.game_paused = true;
        assert!(snapshot(session, &rows).is_null());
        session.foreground.game_paused = false;
        session.body_observation = Default::default();
        observe(session, Instant::now() - std::time::Duration::from_secs(4));
        assert!(snapshot(session, &rows).is_null());
    }
    #[test]
    fn visible_same_kind_cannot_replace_the_newest_recorded_sound() {
        let Observation::Observed { mut sample, .. } = observation() else {
            panic!()
        };
        let Senses::Observed(senses) = &mut sample.senses else {
            panic!()
        };
        senses.passive_mobs = serde_json::from_value(json!([
            {"type":"cow","distance":1,"identity":{"entity_id":A}}
        ]))
        .unwrap();
        senses.ambient_sounds.push(
            serde_json::from_value(json!({
                "type":"cow","source_id":"new-cow","heard_ago_ms":0
            }))
            .unwrap(),
        );
        let targets = mentioned_targets("ウシの声がするな", senses);
        assert_eq!(
            targets[0],
            json!({"kind":"sound","id":"new-cow","observer":"body"})
        );
        assert_eq!(
            targets[1],
            json!({"kind":"sound","id":"heard-cow","observer":"body"})
        );
        assert_eq!(
            targets[2],
            json!({"kind":"entity","id":A,"observer":"body"})
        );
    }
}
