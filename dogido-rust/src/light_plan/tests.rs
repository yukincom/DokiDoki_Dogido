use super::*;
use crate::types::GeneratedText;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

fn details() -> Value {
    json!({"allowed_actions":["stay_silent","acknowledge_supply_gain"],
        "facts":[{"basis_id":"first_light_supply","value":"true"}],
        "context":{"player_name":"ユキ","recent":[]}})
}
fn fallback() -> Value {
    json!({"action":"stay_silent","speech":""})
}
fn generated(reply: &Value) -> Result<GenerationReport> {
    if let Some(error) = reply["error"].as_str() {
        anyhow::bail!("{error}");
    }
    Ok(GenerationReport {
        schema_version: 1,
        provider: "mock".into(),
        kind: KIND.into(),
        requested_model: "configured-model".into(),
        response_model: "mock".into(),
        response_id: "mock".into(),
        elapsed_ms: 0,
        generated: GeneratedText {
            text: reply["text"].as_str().unwrap().into(),
            finish_reason: reply["finish_reason"].as_str().map(str::to_owned),
            completion_tokens: Some(10),
            prompt_tokens: Some(20),
        },
    })
}

#[test]
fn one_call_prompt_and_action_speech_contract() {
    let d = details();
    let m = messages(&d);
    assert_eq!(m.len(), 2);
    assert!(m[0].content.contains("照明器具が増えたけど"));
    assert_eq!(m[1].content, spaced_json(&d));
    for payload in [
        fallback(),
        json!({"action":"acknowledge_supply_gain","speech":"備えが増えたな。"}),
    ] {
        assert!(contract_errors(&payload, &d).is_empty());
    }
    for payload in [
        Value::Null,
        json!({"action":"relief_after_darkness","speech":"ほっとしたわ。"}),
        json!({"action":"acknowledge_supply_gain","speech":" "}),
        json!({"action":"stay_silent","speech":"話すで。"}),
        json!({"action":"acknowledge_supply_gain","speech":7}),
        json!({"action":"acknowledge_supply_gain","speech":"備えや。","confidence":1}),
    ] {
        assert!(!contract_errors(&payload, &d).is_empty(), "{payload}");
    }
}

#[tokio::test]
async fn every_result_finishes_after_exactly_one_generation() {
    for (reply, status, speech) in [
        (
            json!({"text":r#"{"action":"acknowledge_supply_gain","speech":"備えが増えたな。"}"#}),
            "accepted",
            "備えが増えたな。",
        ),
        (
            json!({"text":r#"{"action":"stay_silent","speech":""}"#}),
            "accepted",
            "",
        ),
        (json!({"text":"broken"}), "invalid_json", ""),
        (
            json!({"text":r#"{"action":"relief_after_darkness","speech":"ほっ。"}"#}),
            "schema_contract_error",
            "",
        ),
        (
            json!({"text":r#"{"action":"stay_silent","speech":""}"#, "finish_reason":"length"}),
            "output_truncated",
            "",
        ),
        (json!({"error":"offline"}), "generation_error", ""),
    ] {
        let (_sender, mut receiver) = watch::channel(false);
        let count = AtomicUsize::new(0);
        let result = run_bounded(
            "configured-model",
            &details(),
            &fallback(),
            &mut receiver,
            DEADLINE,
            |request| {
                count.fetch_add(1, Ordering::SeqCst);
                assert_eq!(request.model, "configured-model");
                assert_eq!(request.kind, KIND);
                assert!(!request.enable_thinking);
                // replyを消費するFnOnceで、同じ入力への再生成がないことも型で保証する。
                async move { generated(&reply) }
            },
        )
        .await
        .unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
        assert_eq!(result["llm_reports"].as_array().unwrap().len(), 1);
        assert_eq!(result["payload"]["__dogido_status"], status);
        assert_eq!(result["payload"]["speech"], speech);
    }
}

#[tokio::test]
async fn cancellation_before_and_during_generation_stops_delivery() {
    let d = details();
    let f = fallback();
    for already_cancelled in [false, true] {
        let (sender, mut receiver) = watch::channel(already_cancelled);
        let count = AtomicUsize::new(0);
        let result = run_bounded("model", &d, &f, &mut receiver, DEADLINE, |_| {
            count.fetch_add(1, Ordering::SeqCst);
            sender.send(true).unwrap();
            std::future::ready(generated(
                &json!({"text":r#"{"action":"stay_silent","speech":""}"#}),
            ))
        })
        .await;
        assert_eq!(result.unwrap_err().to_string(), "cancelled");
        assert_eq!(
            count.load(Ordering::SeqCst),
            if already_cancelled { 0 } else { 1 }
        );
    }
    let (sender, mut receiver) = watch::channel(false);
    drop(sender);
    let result = run_bounded("model", &d, &f, &mut receiver, DEADLINE, |_| async {
        panic!("orphaned request must not start")
    })
    .await;
    assert_eq!(result.unwrap_err().to_string(), "cancelled");
}

struct Dropped(Arc<AtomicUsize>);
impl Drop for Dropped {
    fn drop(&mut self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }
}
#[tokio::test]
async fn pending_generation_is_dropped_on_timeout_and_cancellation() {
    let d = details();
    let f = fallback();
    for timeout in [false, true] {
        let (sender, mut receiver) = watch::channel(false);
        let (started_tx, started_rx) = tokio::sync::oneshot::channel();
        let mut started_tx = Some(started_tx);
        let drops = Arc::new(AtomicUsize::new(0));
        let calls = AtomicUsize::new(0);
        let runner = run_bounded(
            "model",
            &d,
            &f,
            &mut receiver,
            if timeout {
                Duration::from_millis(30)
            } else {
                DEADLINE
            },
            |_| {
                calls.fetch_add(1, Ordering::SeqCst);
                let guard = Dropped(drops.clone());
                let tx = started_tx.take().unwrap();
                async move {
                    let _guard = guard;
                    tx.send(()).unwrap();
                    std::future::pending::<Result<GenerationReport>>().await
                }
            },
        );
        let control = async {
            started_rx.await.unwrap();
            if !timeout {
                sender.send(true).unwrap();
            }
        };
        let (result, _) = tokio::join!(runner, control);
        assert_eq!(
            result.unwrap_err().to_string(),
            if timeout {
                "light planner timed out"
            } else {
                "cancelled"
            }
        );
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        assert_eq!(drops.load(Ordering::SeqCst), 1);
    }
}
