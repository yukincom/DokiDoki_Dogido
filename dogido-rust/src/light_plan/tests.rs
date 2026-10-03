use super::*;
use crate::types::GeneratedText;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

fn fixtures() -> Value {
    serde_json::from_str(include_str!("fixtures.json")).unwrap()
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
fn canonical_prompts_contracts_and_retry_messages() {
    let fixtures = fixtures();
    for row in fixtures["prompts"].as_array().unwrap() {
        assert_eq!(json!(messages(&row["details"], None)), row["messages"]);
    }
    for (index, row) in fixtures["contracts"].as_array().unwrap().iter().enumerate() {
        let errors = contract_errors(&row["payload"], &row["details"]);
        assert_eq!(
            json!(errors),
            row["errors"],
            "contract {index}: {}",
            row["payload"]
        );
        assert_eq!(
            json!(messages(&row["details"], Some((&errors, &row["payload"])))),
            row["retry_messages"],
            "retry {index}"
        );
    }
}

#[tokio::test]
async fn canonical_runtime_retry_and_abstention() {
    let fixtures = fixtures();
    for case in fixtures["runtime"].as_array().unwrap() {
        let (_sender, mut receiver) = watch::channel(false);
        let mut replies = case["replies"].as_array().unwrap().iter();
        let mut requests = Vec::new();
        let result = run_bounded(
            "configured-model",
            &case["details"],
            &case["fallback"],
            &mut receiver,
            DEADLINE,
            |request| {
                assert_eq!(request.model, "configured-model");
                assert_eq!(request.kind, KIND);
                assert!(!request.enable_thinking);
                requests.push(
                    json!({"messages":request.messages,"temperature":request.temperature,
                "max_tokens":request.max_tokens,"route":"chat"}),
                );
                std::future::ready(generated(replies.next().expect("at most canonical calls")))
            },
        )
        .await
        .unwrap();
        assert_eq!(
            result["payload"], case["expected_payload"],
            "{}",
            case["name"]
        );
        assert_eq!(json!(requests), case["requests"], "{}", case["name"]);
        assert_eq!(
            result["llm_reports"].as_array().unwrap().len(),
            requests.len()
        );
        assert!(requests.len() <= 2);
        // Compare the unchanged native event consumer with Python's domain decision.
        use crate::environment::ambient::{Ambient, AmbientFocus};
        use crate::{combat::model::Mode, events::GameEvent};
        let event = |inventory| {
            GameEvent::parse(json!({
            "schema_version":"2026-05-24", "adapter":"fixture", "sequence":1,
            "observed_at":"2026-09-25T00:00:00Z",
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "world":{"biome":"plains","time_phase":"day"}, "inventory":inventory
        })).unwrap()
        };
        let settings = crate::combat::model::Settings::default();
        let focus = AmbientFocus::default();
        let mut ambient = Ambient::default();
        ambient.update(&event(json!({})), 0, true, &settings);
        let current = event(json!({"torch":1}));
        ambient.update(&current, 1, true, &settings);
        ambient.actions(&current, 1, Mode::Normal, false, &focus, &settings);
        let request = ambient.take_light_plan().unwrap();
        assert_eq!(request.details, case["details"]);
        let spoken = ambient.resolve_light_plan(
            request.request_id,
            &result["payload"],
            &current,
            2,
            &focus,
            &settings,
        );
        assert_eq!(
            json!(spoken.is_some()),
            case["should_speak"],
            "consumer {}",
            case["name"]
        );
    }
}

#[tokio::test]
async fn cancellation_before_and_between_requests_stops_retry() {
    let f = fixtures();
    let case = &f["runtime"][8];
    for already_cancelled in [false, true] {
        let (sender, mut receiver) = watch::channel(already_cancelled);
        let count = AtomicUsize::new(0);
        let result = run_bounded(
            "model",
            &case["details"],
            &case["fallback"],
            &mut receiver,
            DEADLINE,
            |_| {
                count.fetch_add(1, Ordering::SeqCst);
                sender.send(true).unwrap();
                std::future::ready(generated(&case["replies"][0]))
            },
        )
        .await;
        assert_eq!(result.unwrap_err().to_string(), "cancelled");
        assert_eq!(
            count.load(Ordering::SeqCst),
            if already_cancelled { 0 } else { 1 }
        );
    }
    let (sender, mut receiver) = watch::channel(false);
    drop(sender);
    let result = run_bounded(
        "model",
        &case["details"],
        &case["fallback"],
        &mut receiver,
        DEADLINE,
        |_| async { panic!("orphaned request must not start") },
    )
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
    let f = fixtures();
    let case = &f["runtime"][0];
    for timeout in [false, true] {
        let (sender, mut receiver) = watch::channel(false);
        let (started_tx, started_rx) = tokio::sync::oneshot::channel();
        let mut started_tx = Some(started_tx);
        let drops = Arc::new(AtomicUsize::new(0));
        let calls = AtomicUsize::new(0);
        let runner = run_bounded(
            "model",
            &case["details"],
            &case["fallback"],
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
