//! 意図の原文と個体の現在性の境界を検査する。モデル精度は別の実モデル比較で測る。
use super::*;
use crate::assist::horse_recall::intent;
use serde_json::json;

pub fn snapshot() -> Snapshot {
    serde_json::from_value(json!({"schema_version":1,"status":"available",
        "world_id":"10000000-0000-0000-0000-000000000001","revision":1,"observed_at":Utc::now(),
        "player_uuid":"20000000-0000-0000-0000-000000000001","dimension":"minecraft:overworld",
        "candidates":[{"entity_id":"30000000-0000-0000-0000-000000000001","custom_name":"buchi",
        "tamed":true,"owned_by_player":true,"alive":true,"dimension":"minecraft:overworld","has_passengers":false,"leashed":false}]})).unwrap()
}
fn choice(text: &str) -> Choice {
    Choice {
        candidate_id: "h1".into(),
        evidence: text.into(),
        confidence: 0.99,
    }
}

#[test]
fn names_are_not_an_execution_gate_but_evidence_and_latest_identity_are() {
    for text in [
        "ぶちを呼んで",
        "ブチこっち連れてきて",
        "さっき乗った子をお願い",
        "buchiを呼んでくれる？",
        "「buchi」をここへ連れてきて",
    ] {
        let p = Prepared::new(snapshot(), text).unwrap();
        assert!(p.resolve(&choice(text)).is_ok(), "{text}");
        let mut latest = p.snapshot.clone();
        latest.revision += 1;
        assert!(p.revalidate(&choice(text), &latest, Utc::now()).is_ok());
        latest.candidates[0].custom_name = Some("another".into());
        assert!(p.revalidate(&choice(text), &latest, Utc::now()).is_err());
    }
    for text in [
        "ぶちを呼ばないで",
        "「ぶちを呼んで」",
        "buchiを呼んでという意味じゃない",
    ] {
        let p = Prepared::new(snapshot(), text).unwrap();
        assert!(p.resolve(&choice(text)).is_err());
    }
    let p = Prepared::new(snapshot(), "ぶちを呼んでという意味ではない").unwrap();
    assert!(p.resolve(&choice("ぶちを呼んで")).is_err());
}
#[test]
fn stale_other_world_unsafe_and_unknown_candidates_fail_closed() {
    let p = Prepared::new(snapshot(), "ぶちを呼んで").unwrap();
    let c = choice(&p.original);
    for mutate in [
        |s: &mut Snapshot| s.observed_at -= chrono::Duration::seconds(4),
        |s: &mut Snapshot| s.observed_at += chrono::Duration::seconds(2),
        |s: &mut Snapshot| s.world_id = "10000000-0000-0000-0000-000000000002".into(),
        |s: &mut Snapshot| s.player_uuid = "20000000-0000-0000-0000-000000000002".into(),
        |s: &mut Snapshot| s.candidates[0].owned_by_player = false,
        |s: &mut Snapshot| s.candidates[0].alive = false,
        |s: &mut Snapshot| s.candidates[0].tamed = false,
        |s: &mut Snapshot| s.candidates[0].has_passengers = true,
        |s: &mut Snapshot| s.candidates[0].leashed = true,
        |s: &mut Snapshot| s.candidates.clear(),
    ] {
        let mut s = p.snapshot.clone();
        mutate(&mut s);
        assert!(p.revalidate(&c, &s, Utc::now()).is_err());
    }
    for id in ["h0", "h2", "h01", "buchi"] {
        let mut c = c.clone();
        c.candidate_id = id.into();
        assert!(p.resolve(&c).is_err());
    }
    let mut s = snapshot();
    s.candidates.push(s.candidates[0].clone());
    assert!(s.validate().is_err());
    let mut c = c.clone();
    c.confidence = 0.89;
    assert!(p.resolve(&c).is_err());
}
#[test]
fn native_stage_contracts_reject_truncation_multiple_calls_and_wrong_stage() {
    use intent::{Decision, Stage};
    let args = json!({"candidate_id":"h1","evidence":"ぶちを呼んで","confidence":0.99});
    let call = json!({"id":"c1","type":"function","function":{"name":"select_horse","arguments":args.to_string()}});
    let native = json!({"tool_calls":[call.clone()],"content":null});
    assert!(matches!(
        intent::decode(Stage::Selection, &native, "tool_calls").unwrap(),
        Decision::Select(_)
    ));
    assert!(intent::decode(Stage::Intent, &native, "tool_calls").is_err());
    assert!(intent::decode(Stage::Selection, &native, "length").is_err());
    assert!(
        intent::decode(
            Stage::Selection,
            &json!({"tool_calls":[call.clone(),call]}),
            "tool_calls"
        )
        .is_err()
    );
    let body=json!({"action":"select_horse","candidate_id":"h1","evidence":"ぶちを呼んで","confidence":0.99}).to_string();
    assert!(matches!(
        intent::decode(Stage::Selection, &json!({"content":body}), "stop").unwrap(),
        Decision::Conversation
    ));
    assert_eq!(intent::conversation(&body).status, "rejected");
    let intent = intent::Intent {
        evidence: "ぶちを呼んで".into(),
        confidence: 0.99,
    };
    assert!(intent.validate("ぶちを呼んで").is_ok());
    assert!(intent.validate("ぶちを呼んでという意味じゃない").is_err());
}
#[test]
fn first_stage_preserves_personality_without_candidates_then_selection_exposes_them() {
    use intent::Stage;
    let mut snapshot = snapshot();
    snapshot.candidates[0].custom_name = Some("candidate_sentinel_not_in_request".into());
    let p = Prepared::new(snapshot, "さっきの馬を呼んで").unwrap();
    for stage in [Stage::Intent, Stage::Selection] {
        let base = crate::chat_prompt::Input {
            schema_version: 1,
            kind: "player_chat".into(),
            model: "fixture".into(),
            details: json!({"dialogue_choice":true,"user_text":p.original}),
            temperature: 0.65,
            max_tokens: 192,
            enable_thinking: false,
        }
        .into_request()
        .unwrap();
        let (request, tools) = intent::request(base, &p.original, Some(&p), stage).unwrap();
        let system = &request.messages[0].content;
        assert!(system.contains(crate::companion_prompt::base()));
        assert!(!system.contains(crate::companion_prompt::choice_contract()));
        let serialized = serde_json::to_string(&request.messages).unwrap();
        assert_eq!(
            serialized.contains("candidate_sentinel_not_in_request"),
            stage == Stage::Selection
        );
        assert!(!serialized.contains(&p.snapshot.world_id));
        assert!(!serialized.contains(&p.candidates[0].entity_id));
        assert_eq!(tools.len(), 1);
        assert_eq!(
            tools[0]["function"]["name"],
            if stage == Stage::Intent {
                "list_horses"
            } else {
                "select_horse"
            }
        );
    }
}
