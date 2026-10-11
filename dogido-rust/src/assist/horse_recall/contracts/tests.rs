//! 意図の原文と個体の現在性の境界を検査する。モデル精度は別の実モデル比較で測る。
use super::*;
use crate::assist::horse_recall::transport::{self, Transport};
use serde_json::json;

pub fn snapshot() -> Snapshot {
    serde_json::from_value(json!({"schema_version":1,"status":"available",
        "world_id":"10000000-0000-0000-0000-000000000001","revision":1,"observed_at":Utc::now(),
        "player_uuid":"20000000-0000-0000-0000-000000000001","dimension":"minecraft:overworld",
        "candidates":[{"entity_id":"30000000-0000-0000-0000-000000000001","custom_name":"buchi",
        "tamed":true,"owned_by_player":true,"alive":true,"dimension":"minecraft:overworld","has_passengers":false,"leashed":false}]})).unwrap()
}
fn choice(text: &str) -> Choice { Choice {candidate_id:"h1".into(),evidence:text.into(),confidence:0.99} }

#[test]
fn names_are_not_an_execution_gate_but_evidence_and_latest_identity_are() {
    for text in ["ぶちを呼んで", "ブチこっち連れてきて", "さっき乗った子をお願い", "buchiを呼んでくれる？", "「buchi」をここへ連れてきて"] {
        let p = Prepared::new(snapshot(), text).unwrap();
        assert!(p.resolve(&choice(text)).is_ok(),"{text}");
        let mut latest = p.snapshot.clone(); latest.revision+=1;
        assert!(p.revalidate(&choice(text), &latest, Utc::now()).is_ok());
        latest.candidates[0].custom_name=Some("another".into());
        assert!(p.revalidate(&choice(text), &latest, Utc::now()).is_err());
    }
    for text in ["ぶちを呼ばないで", "「ぶちを呼んで」", "buchiを呼んでという意味じゃない"] {
        let p=Prepared::new(snapshot(),text).unwrap();
        assert!(p.resolve(&choice(text)).is_err());
    }
    let p=Prepared::new(snapshot(),"ぶちを呼んでという意味ではない").unwrap();
    assert!(p.resolve(&choice("ぶちを呼んで")).is_err());
}
#[test]
fn stale_other_world_unsafe_and_unknown_candidates_fail_closed() {
    let p=Prepared::new(snapshot(),"ぶちを呼んで").unwrap(); let c=choice(&p.original);
    for mutate in [
        |s:&mut Snapshot| s.observed_at-=chrono::Duration::seconds(4),
        |s:&mut Snapshot| s.observed_at+=chrono::Duration::seconds(2),
        |s:&mut Snapshot| s.world_id="10000000-0000-0000-0000-000000000002".into(),
        |s:&mut Snapshot| s.player_uuid="20000000-0000-0000-0000-000000000002".into(),
        |s:&mut Snapshot| s.candidates[0].owned_by_player=false,
        |s:&mut Snapshot| s.candidates[0].alive=false,
        |s:&mut Snapshot| s.candidates[0].tamed=false,
        |s:&mut Snapshot| s.candidates[0].has_passengers=true,
        |s:&mut Snapshot| s.candidates[0].leashed=true,
        |s:&mut Snapshot| s.candidates.clear(),
    ] {let mut s=p.snapshot.clone();mutate(&mut s);assert!(p.revalidate(&c,&s,Utc::now()).is_err());}
    for id in ["h0","h2","h01","buchi"] {let mut c=c.clone();c.candidate_id=id.into();assert!(p.resolve(&c).is_err());}
    let mut s=snapshot();s.candidates.push(s.candidates[0].clone());assert!(s.validate().is_err());
    let mut c=c.clone();c.confidence=0.89;assert!(p.resolve(&c).is_err());
}
#[test]
fn transports_have_one_strict_action_and_do_not_convert_each_other() {
    let args=json!({"candidate_id":"h1","evidence":"ぶちを呼んで","confidence":0.99});
    let call=json!({"id":"c1","type":"function","function":{"name":"request_horse_recall","arguments":args.to_string()}});
    let native=json!({"tool_calls":[call.clone()],"content":null});
    let mut payload=args.clone();payload["action"]="recall_horse".into();
    let content=json!({"content":payload.to_string()});
    assert!(transport::decode(Transport::Native,&native,"tool_calls").unwrap().is_some());
    assert!(transport::decode(Transport::Json,&content,"stop").unwrap().is_some());
    assert!(transport::decode(Transport::Json,&native,"tool_calls").is_err());
    assert!(transport::decode(Transport::Native,&content,"stop").is_err());
    assert!(transport::decode(Transport::Native,&json!({"tool_calls":[call.clone(),call]}),"tool_calls").is_err());
    assert!(transport::decode(Transport::Json,&content,"length").is_err());
}
#[test]
fn both_requests_preserve_the_shared_personality_and_remove_conflicting_contract() {
    let p=Prepared::new(snapshot(),"ぶちを呼んで").unwrap();
    for arm in [Transport::Json,Transport::Native] {
        let base=crate::chat_prompt::Input {schema_version:1,kind:"player_chat".into(),model:"fixture".into(),details:json!({"dialogue_choice":true,"user_text":p.original}),temperature:0.65,max_tokens:192,enable_thinking:false}.into_request().unwrap();
        let (request,tools)=transport::request(base,&p,arm).unwrap();
        let s=&request.messages[0].content;
        assert!(s.contains(crate::companion_prompt::base()));
        assert!(!s.contains(crate::companion_prompt::choice_contract()));
        assert_eq!(tools.len(),usize::from(arm==Transport::Native));
        assert!(request.messages.last().unwrap().content.contains("buchi"));
    }
}
