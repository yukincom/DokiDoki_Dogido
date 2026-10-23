//! Sessionの現在観測と確定済み短期履歴から、通常対話を一回だけ生成する。
//! 話題選択を本文生成へまとめ、コードは形式補完と明らかな出力破損だけを判定する。
//! 詩の基本知識・Minecraft知識は既存DBへ。国語専用対話へは分岐せず、履歴確定はSessionが所有する。
use super::{DialogueConfig, bridge, chat_context::CatalogLabels};
use crate::{
    chat_materials::{self, Prepared},
    foreground::Route,
    llm::RigLlm,
};
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::time::Duration;
use tokio::{sync::watch, time::Instant};

fn live(cancel: &watch::Receiver<bool>) -> Result<()> {
    ensure!(
        !*cancel.borrow() && cancel.has_changed().is_ok(),
        "cancelled"
    );
    Ok(())
}

/// 返答処理全体をbudget内で実行し、表示用textと必要時spoken_textを返す。
/// select_routeはSession側で所有権と保留を照合する入口。
/// 読み補正に子プロセスが必要な場合は、その終了処理まで待ってから返る。
pub(super) async fn render(
    config: &DialogueConfig,
    llm: &RigLlm,
    input: &bridge::Input,
    cancel: &mut watch::Receiver<bool>,
    mut select_route: impl FnMut(Route) -> Result<()>,
    budget: Duration,
) -> Result<Value> {
    live(cancel)?;
    let deadline = Instant::now() + budget;
    let mut native_cancel = cancel.clone();
    // 本文生成と資料読取りはfutureの破棄で止められるため、全体期限と取消を同じ待機で扱う。
    let mut result = tokio::select! { biased;
        _ = bridge::cancelled(cancel) => bail!("cancelled"),
        result = tokio::time::timeout_at(deadline, body(config,llm,input,&mut native_cancel,&mut select_route)) => result.context("chat generation timed out")??,
    };
    live(cancel)?;
    ensure!(Instant::now() < deadline, "chat timed out");
    if let Some(text) = result
        .get("text")
        .filter(|_| result["dialogue_action"] != "silent")
    {
        let text = text.as_str().context("native chat result text")?;
        // 読み補正は自身で取消・期限を監視し、所有する子プロセスを終了してから戻る。
        // 外側でfutureを先に破棄すると終了待ちを失うため、readの完了を直接待つ。
        let reading_deadline = deadline.min(Instant::now() + Duration::from_secs(35));
        result["spoken_text"] = super::tts_runtime::read(config, text, cancel, reading_deadline)
            .await?
            .into();
    }
    live(cancel)?;
    tracing::info!(event = "chat_worker_finished", implementation = "rust");
    Ok(result)
}

/// 固定routingを先に処理し、通常雑談だけを一回生成へ進める。
async fn body(
    config: &DialogueConfig,
    llm: &RigLlm,
    input: &bridge::Input,
    cancel: &mut watch::Receiver<bool>,
    select_route: &mut impl FnMut(Route) -> Result<()>,
) -> Result<Value> {
    live(cancel)?;
    let mut context = input.context.clone().context("native prepared input")?;
    let raw = input["text"].as_str().context("native current input")?;
    // 操作・保存・知識照会のroutingは原文由来の解析を使う。
    // STTの文脈補正は別のinterpreted_textとして会話の意味理解へ渡し、原文を保存判断の正として残す。
    context.raw_text = raw.into();
    context.interpreted_text = input["interpreted_text"]
        .as_str()
        .filter(|v| !v.is_empty())
        .unwrap_or(raw)
        .into();
    // 一句の相談中は過去句を検索しない。戦闘pauseと通常会話への寄り道も、
    // 受付時にコードが確定したworkshopのopen状態を引き継ぐ。
    if context.asks_haiku_recall
        && input["poem_reference"]["open"] != true
        && input["workshop_fallback"] != true
        && !(context.asks_save_last_haiku
            || context.revised_haiku_text.is_some()
            || context.player_haiku_text.is_some())
    {
        let query = crate::recall_query::parse(
            raw,
            input["reading_corrections"]
                .as_array()
                .context("recall reading snapshot")?,
            chrono::Utc::now(),
        )
        .context("recall request missing from current input")?;
        return Ok(
            json!({"op":"result","memory_query_requested":true,"memory_query":query,"llm_reports":[]}),
        );
    }
    // 剣・モブのフラグは共通分類で置換済み。専用経路から通常会話へ誤流入した場合を扱う。
    // 完成句の保存・三行編集は23分類とは別の既存経路なので、この分岐を残す。
    if context.requests_sword
        || context.asks_save_last_haiku
        || context.revised_haiku_text.is_some()
        || context.player_haiku_text.is_some()
        || context.asks_mob_information
    {
        return Ok(json!({"op":"result","unsupported":"unexpected_route","llm_reports":[]}));
    }
    if context.wants_quiet {
        return Ok(json!({"op":"result","text":"","llm_reports":[]}));
    }
    if let Some(query) = context.knowledge_query.clone() {
        if input["workshop_fallback"] == true {
            return Ok(json!({"op":"result","unsupported":"unexpected_route","llm_reports":[]}));
        }
        ensure!(
            input["workshop"].is_null(),
            "knowledge must be outside workshop"
        );
        let lookup = crate::knowledge::provider::lookup_async(
            crate::knowledge::provider::Paths::from_helper(&config.helper)?,
            query,
        )
        .await?;
        live(cancel)?;
        let reply = crate::knowledge::render(&lookup);
        select_route(Route::Knowledge)?;
        return Ok(
            json!({"op":"result","text":reply.text,"knowledge_status":reply.lookup_status,"references":reply.references,"llm_reports":[]}),
        );
    }
    // 国語学習の専用対話は本体の対象外。詩の基本知識は上の正本DB経路で扱う。
    let mut reports: Vec<Value> = vec![];
    // Sessionがこのturn用に投影した観測・履歴をまとめて読む。
    // 判断途中で別時点の観測を拾うと照合対象が変わるため、snapshot欠落はエラーにする。
    let (_event, snapshot) = input
        .snapshot
        .as_deref()
        .context("ordinary chat needs Session chat_native snapshot")?;
    let mut native = snapshot.clone();
    if input["workshop_fallback"] == true {
        native.context.workshop_open = false;
        native.context.workshop_details = None;
    }
    select_route(Route::Casual)?;
    let prepared = chat_materials::prepare(
        native
            .sensory_event
            .as_ref()
            .context("accepted body sensory view")?,
        &native.settings,
        &chat_materials::PlayerInput::from(&context),
        &native.context,
        &native.snapshot,
        &CatalogLabels,
    )?;
    let mut result = match prepared {
        Prepared::Knowledge => bail!("knowledge routing already consumed"),
        Prepared::Fixed(fixed) => fixed_result(fixed),
        Prepared::Reply(mut reply) => {
            if config.llm_enabled
                && input["workshop_fallback"] != true
                && let Some(horse_recall) = input.horse_recall.as_ref()
            {
                let request = reply.request(&config.model, config.max_tokens)?;
                // BERTへ変更済み。旧判定へ戻さない。captureが受理した候補選択だけを実行する。
                let mut result =
                    super::horse_recall_runtime::render(request, llm, horse_recall, cancel).await?;
                super::body_runtime::attach_input(&mut result, &reply.details);
                result["observation_revision"] = reply.details["world_context"]["revision"].clone();
                return Ok(result);
            }
            // 通常の本文生成だけが一枚を要求する。知識・川柳・操作・workshopには画像待機を足さない。
            let mut image = None;
            if config.llm_enabled
                && config.chat_image.enabled
                && input["workshop_fallback"] != true
                && input["workshop"].is_null()
                && let Some(source) = &input.visual
            {
                image = source.capture(cancel).await;
                if image.is_none() {
                    tracing::info!(
                        event = "chat_image_unavailable",
                        reason = "unavailable_or_timeout"
                    );
                }
                live(cancel)?;
                if let Some((current, image_current)) =
                    source.refresh(input, image.as_ref().map(|capture| &capture.context))
                {
                    if !image_current {
                        image = None;
                    }
                    native = current;
                    // 待機中の現在値で材料を再構成する。これは純粋な投影で、モデル呼出しを増やさない。
                    match chat_materials::prepare(
                        native
                            .sensory_event
                            .as_ref()
                            .context("accepted body sensory view")?,
                        &native.settings,
                        &chat_materials::PlayerInput::from(&context),
                        &native.context,
                        &native.snapshot,
                        &CatalogLabels,
                    )? {
                        Prepared::Reply(current) => reply = current,
                        Prepared::Fixed(fixed) => return Ok(fixed_result(fixed)),
                        Prepared::Knowledge => bail!("knowledge routing already consumed"),
                    }
                } else {
                    image = None;
                }
            }
            let output = if config.llm_enabled {
                let request = reply.request(&config.model, config.max_tokens)?;
                let run = super::chat_leaf_runtime::render_image(
                    request,
                    llm,
                    cancel,
                    image.as_ref().map(|capture| &capture.image),
                )
                .await?;
                reports.extend(run.reports);
                run.output
            } else {
                live(cancel)?;
                crate::chat_validation::finish(None)
            };
            let mut result = json!({"op":"result","text":output.text,
                "dialogue_action":output.action,"dialogue_outcome":output.status,
                "dialogue_reason":output.reason,
                "observation_revision":reply.details["world_context"]["revision"]});
            super::body_runtime::attach_input(&mut result, &reply.details);
            result
        }
    };
    result["llm_reports"] = json!(reports);
    Ok(result)
}
fn fixed_result(fixed: chat_materials::Fixed) -> Value {
    tracing::info!(event = "chat_fixed", reason = fixed.reason);
    json!({"op":"result","text":fixed.text,"announce_smell":fixed.announce_smell})
}
#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> DialogueConfig {
        let mut c = DialogueConfig {
            python: "/missing/native-chat-python".into(),
            helper: "/missing/native-chat-helper.py".into(),
            reading_engine: "off".into(),
            ..Default::default()
        };
        c.haiku.memory_enabled = false;
        c
    }
    fn input(text: &str) -> bridge::Input {
        let prepared = crate::player_text::prepare(text);
        let context = crate::input_context::Context::from_prepared(
            &prepared,
            &[],
            chrono::Local::now().fixed_offset(),
        );
        let mut input: bridge::Input = json!({"text":text,"reading_corrections":[]}).into();
        input.context = Some(context);
        input
    }
    #[tokio::test]
    async fn fixed_routes_need_neither_python_nor_model_nor_snapshot() {
        let c = config();
        let llm = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_secs(1)).unwrap();
        let (_owner, mut rx) = watch::channel(false);
        let quiet = super::render(
            &c,
            &llm,
            &input("静かにして"),
            &mut rx,
            |_| panic!("no route"),
            Duration::from_secs(1),
        )
        .await
        .unwrap();
        assert_eq!(quiet["text"], "");
        assert_eq!(quiet["spoken_text"], "");
        for text in ["剣に持ち替えて", "今の句を保存して"] {
            let mut current = input(text);
            if text == "剣に持ち替えて" {
                let classification = crate::request_classifier::Classification {
                    text: text.into(),
                    source: "text".into(),
                    manifest_sha256: "fixture".into(),
                    prediction: crate::request_classifier::Prediction {
                        label: crate::request_classifier::Label::EquipSword,
                        probabilities: std::array::from_fn(|i| if i == 2 { 1.0 } else { 0.0 }),
                        token_count: 8,
                        inference_ms: 0.1,
                    },
                };
                current
                    .context
                    .as_mut()
                    .unwrap()
                    .apply_request_classification(&classification);
            }
            let reply = super::render(
                &c,
                &llm,
                &current,
                &mut rx,
                |_| Ok(()),
                Duration::from_secs(1),
            )
            .await
            .unwrap();
            assert!(reply["unsupported"].is_string(), "{text}: {reply}");
            assert!(reply.get("spoken_text").is_none());
        }
    }
    #[tokio::test]
    async fn missing_snapshot_and_cancelled_or_closed_owner_fail_closed() {
        let c = config();
        let llm = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_secs(1)).unwrap();
        let (owner, mut rx) = watch::channel(false);
        let missing = bridge::render(&c, &llm, input("こんにちは"), &mut rx)
            .await
            .unwrap_err();
        assert!(missing.to_string().contains("chat_native"), "{missing}");
        owner.send(true).unwrap();
        assert_eq!(
            super::render(
                &c,
                &llm,
                &input("静かにして"),
                &mut rx,
                |_| Ok(()),
                Duration::from_secs(1)
            )
            .await
            .unwrap_err()
            .to_string(),
            "cancelled"
        );
        let (owner, mut rx) = watch::channel(false);
        drop(owner);
        assert_eq!(
            super::render(
                &c,
                &llm,
                &input("静かにして"),
                &mut rx,
                |_| Ok(()),
                Duration::from_secs(1)
            )
            .await
            .unwrap_err()
            .to_string(),
            "cancelled"
        );
    }
    #[tokio::test]
    async fn disabled_model_is_quiet_without_transport() {
        let mut c = config();
        c.llm_enabled = false;
        let llm = RigLlm::new("http://127.0.0.1:9/v1", None, Duration::from_secs(1)).unwrap();
        let fixture: Value = serde_json::from_str(
            include_str!("../../fixtures/chat-materials.jsonl")
                .lines()
                .next()
                .unwrap(),
        )
        .unwrap();
        let mut frame = json!({"text":"こんにちは","reading_corrections":[]});
        frame["event"] = fixture["event"].clone();
        let mut native: crate::dialogue::chat_context::Native = serde_json::from_value(json!({"snapshot":fixture["snapshot"],"context":fixture["context"],"settings":fixture["settings"]})).unwrap();
        native.sensory_event =
            Some(crate::events::GameEvent::parse(fixture["event"].clone()).unwrap());
        let mut job = bridge::Input::native(
            frame,
            crate::events::GameEvent::parse(fixture["event"].clone()).unwrap(),
            native,
        );
        job.context = Some(crate::input_context::Context::from_prepared(
            &crate::player_text::prepare("こんにちは"),
            &[],
            chrono::Local::now().fixed_offset(),
        ));
        // The renderer consumes the frozen typed observation, not a reparsed
        // legacy JSON frame. No chat_native JSON field is required in production.
        job["event"] = Value::Null;
        assert!(job.get("chat_native").is_none());
        let (_owner, mut rx) = watch::channel(false);
        let started = Instant::now();
        let result = super::render(
            &c,
            &llm,
            &job,
            &mut rx,
            |route| {
                assert_eq!(route, Route::Casual);
                Ok(())
            },
            Duration::from_secs(1),
        )
        .await
        .unwrap();
        assert!(started.elapsed() < Duration::from_millis(500));
        assert_eq!(result["text"], "");
        assert_eq!(result["dialogue_action"], "silent");
        assert_eq!(result["dialogue_outcome"], "generation_error");
        assert!(result.get("spoken_text").is_none());
        assert_eq!(result["llm_reports"], json!([]));
    }
}
