//! Sessionから受け取った入力・観測・完了済み履歴を、通常会話の返答と読み上げ用テキストへ変換する。
//! 入口のroutingで宛先確認・記憶照会・正本知識・限定国語対話を分け、通常雑談は次の順で進む。
//! 入力に応じた材料準備 → bounded planner → 観測との照合 → 固定回答または発話leaf → 読み補正。
//! plannerは一件のread actionを選び、カタログ候補が現在の観測にあるかはafter_planで照合する。
//! 発話leafは採否検査と必要時一回の言い直しを経る。参照するsnapshotはこの判断中は固定する。
//! 戻り値は未配送の結果。Sessionが世代を確認して状態・履歴へ反映し、音声合成・実再生を担当する。
use super::{DialogueConfig, bridge, chat_context::CatalogLabels};
use crate::{
    chat_materials::{self, After, Before},
    foreground::Route,
    llm::RigLlm,
    planner,
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
/// select_route/hold_handoffはSession側で所有権と保留を照合する入口。
/// 読み補正に子プロセスが必要な場合は、その終了処理まで待ってから返る。
pub(super) async fn render(
    config: &DialogueConfig,
    llm: &RigLlm,
    input: &bridge::Input,
    cancel: &mut watch::Receiver<bool>,
    mut select_route: impl FnMut(Route) -> Result<()>,
    mut hold_handoff: impl FnMut(&Value) -> Result<bool>,
    budget: Duration,
) -> Result<Value> {
    live(cancel)?;
    let deadline = Instant::now() + budget;
    let mut native_cancel = cancel.clone();
    // 本文生成と資料読取りはfutureの破棄で止められるため、全体期限と取消を同じ待機で扱う。
    let mut result = tokio::select! { biased;
        _ = bridge::cancelled(cancel) => bail!("cancelled"),
        result = tokio::time::timeout_at(deadline, body(config,llm,input,&mut native_cancel,&mut select_route,&mut hold_handoff)) => result.context("chat generation timed out")??,
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

/// 固定routingを先に処理し、通常雑談だけをbefore_plan → planner → after_plan → leafへ進める。
async fn body(
    config: &DialogueConfig,
    llm: &RigLlm,
    input: &bridge::Input,
    cancel: &mut watch::Receiver<bool>,
    select_route: &mut impl FnMut(Route) -> Result<()>,
    hold_handoff: &mut impl FnMut(&Value) -> Result<bool>,
) -> Result<Value> {
    live(cancel)?;
    if let Some(text) = input["address_reply"].as_str() {
        return Ok(
            json!({"op":"result","text":text,"language_status":"address_confirmation_requested","llm_reports":[]}),
        );
    }
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
    if context.asks_haiku_recall
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
    if context.requests_sword
        || context.asks_save_last_haiku
        || context.revised_haiku_text.is_some()
        || context.player_haiku_text.is_some()
        || context.asks_hostile_count
        || context.asks_hostile_direction
        || context.asks_dragon_direction
    {
        return Ok(json!({"op":"result","unsupported":"unexpected_route","llm_reports":[]}));
    }
    if context.wants_quiet {
        return Ok(json!({"op":"result","text":"","repair":null,"llm_reports":[]}));
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
        select_route(Route::Learning)?;
        return Ok(
            json!({"op":"result","text":reply.text,"knowledge_status":reply.lookup_status,"references":reply.references,"llm_reports":[]}),
        );
    }
    let mut reports = vec![];
    let mut language = None;
    if config.language_enabled
        && input["host_chat_confirmed"] != true
        && input["workshop_fallback"] != true
        && input["workshop"].is_null()
        && !context.asks_inventory
        && !context.normalized_text.starts_with('/')
        && input["language_requested"] == true
    {
        select_route(Route::Learning)?;
        let mut turn = crate::language::Turn::new(input)?;
        drive_language(config, llm, &mut turn, &mut reports, cancel).await?;
        live(cancel)?;
        if turn.outcome["status"] == "host_chat" && hold_handoff(&turn.outcome)? {
            turn.outcome["status"] = "awaiting_address".into();
        }
        if turn.outcome["status"] != "host_chat" {
            let mut result =
                json!({"op":"result","text":turn.outcome["text"],"llm_reports":reports});
            language_fields(&mut result, &turn)?;
            return Ok(result);
        }
        language = Some(turn);
    }
    // Sessionがこのturn用に投影した観測・履歴をまとめて読む。
    // 判断途中で別時点の観測を拾うと照合対象が変わるため、snapshot欠落はエラーにする。
    let (event, snapshot) = input
        .snapshot
        .as_deref()
        .context("ordinary chat needs Session chat_native snapshot")?;
    let mut native = snapshot.clone();
    if input["workshop_fallback"] == true {
        native.context.workshop_open = false;
        native.context.workshop_details = None;
    }
    if language.is_none() {
        select_route(Route::Casual)?;
    }
    // 明示照会などの固定回答を先に確定し、LLMによる次手の選択が必要な入力だけPlanへ渡す。
    let before = chat_materials::before_plan(
        event,
        &native.settings,
        &chat_materials::PlayerInput::from(&context),
        &native.context,
        &native.snapshot,
        &CatalogLabels,
        config.llm_enabled.then_some(config.model.as_str()),
    )?;
    let mut result = match before {
        Before::Knowledge => bail!("knowledge routing already consumed"),
        Before::Fixed(fixed) => fixed_result(fixed),
        Before::Plan(prepared) => {
            let mut handoff = planner::handoff::Handoff::default();
            let plan = if let Some(request) = &prepared.planner.request {
                live(cancel)?;
                let report = planner::run(llm, request).await?;
                live(cancel)?;
                handoff.record_plan(request, &report.plan)?;
                let plan = report.plan.clone();
                reports.push(serde_json::to_value(report)?);
                plan
            } else {
                prepared.planner.fallback.clone()
            };
            // 選ばれたread actionを観測へ照合し、断定可能な固定文か生成用材料へ落とす。
            match chat_materials::after_plan(&prepared, &plan)? {
                After::Fixed(fixed) => fixed_result(fixed),
                After::Leaf(leaf) => {
                    if prepared.planner.request.is_some() {
                        let output = handoff.resolve(leaf.handoff_input.clone(), true)?;
                        ensure!(output == leaf.handoff, "native handoff projection changed");
                    }
                    let input = leaf.input(&config.model, config.max_tokens);
                    handoff.validate_leaf(&input.prompt.details)?;
                    handoff.validate_materials(
                        &input.prompt.details,
                        &input.validation,
                        native.context.workshop_open,
                    )?;
                    let mut turn =
                        crate::chat_validation::Turn::new(input, &config.model, config.max_tokens)?;
                    let reply = if config.llm_enabled {
                        super::chat_leaf_runtime::render(turn, llm, cancel).await?
                    } else {
                        // モデル無効時も同じ採否器を完走し、fallbackと最終安全検査の結果を受け取る。
                        live(cancel)?;
                        let _unused = turn.request()?;
                        ensure!(turn.complete(None)?, "disabled leaf must finish");
                        let outcome = turn.take_outcome()?;
                        super::chat_leaf_runtime::Run {
                            text: outcome.text,
                            final_text: outcome.final_text,
                            reports: vec![],
                            action: if outcome.status == "silent" {
                                "silent"
                            } else {
                                "speak"
                            },
                        }
                    };
                    tracing::info!(
                        event = "chat_final_safety",
                        changed = reply.text != reply.final_text
                    );
                    reports.extend(reply.reports);
                    handoff.validate_result(&json!(reply.final_text))?;
                    json!({"op":"result","text":reply.final_text,"repair":leaf.repair,
                        "dialogue_action":reply.action,"observation_revision":leaf.details["world_context"]["revision"]})
                }
            }
        }
    };
    result["llm_reports"] = json!(reports);
    if let Some(turn) = language {
        language_fields(&mut result, &turn)?;
    }
    Ok(result)
}
fn fixed_result(fixed: chat_materials::Fixed) -> Value {
    tracing::info!(event = "chat_fixed", reason = fixed.reason);
    json!({"op":"result","text":fixed.text,"repair":fixed.repair,"announce_smell":fixed.announce_smell})
}
fn language_fields(result: &mut Value, turn: &crate::language::Turn) -> Result<()> {
    ensure!(turn.finished(), "unfinished language turn");
    result["language_status"] = turn.outcome["status"].clone();
    result["language_state"] = serde_json::to_value(&turn.state)?;
    result["language_references"] = turn.outcome["references"].clone();
    if let Some(proposal) = turn.outcome.get("web_proposal") {
        result["web_proposal"] = proposal.clone();
    }
    Ok(())
}
/// 国語対話のTurnが返すgenerate/lookupだけを実行し、その結果を次の段階へ戻す。
/// 資料照会はローカルDB読取り、生成失敗はerror結果としてTurnへ渡し、doneまで状態機械を進める。
async fn drive_language(
    config: &DialogueConfig,
    llm: &RigLlm,
    turn: &mut crate::language::Turn,
    reports: &mut Vec<Value>,
    cancel: &watch::Receiver<bool>,
) -> Result<()> {
    let mut reply = turn.advance(&json!({"stage":"start"}))?;
    while matches!(reply["command"].as_str(), Some("generate" | "lookup")) {
        live(cancel)?;
        if reply["command"] == "lookup" {
            let interpretation = &reply["interpretation"];
            let request = crate::knowledge::retrieval::Request {
                terms: serde_json::from_value(interpretation["search_terms"].clone())?,
                facet: interpretation["facet"]
                    .as_str()
                    .context("lookup facet")?
                    .into(),
                target: interpretation["target"]
                    .as_str()
                    .context("lookup target")?
                    .into(),
            };
            let lookup = crate::knowledge::retrieval::search_async(
                crate::knowledge::retrieval::Paths::from_helper(&config.helper)?,
                request,
            )
            .await?;
            tracing::info!(event = "language_lookup", reader = "rust", status = ?lookup["status"]);
            reply = turn.advance(&json!({"stage":"lookup","lookup":lookup}))?;
        } else {
            let request = turn.request(&config.model)?;
            let generated = if !config.llm_enabled {
                json!({"text":"","finish_reason":"error"})
            } else {
                match llm.generate(&request).await {
                    Ok(report) => {
                        let report = serde_json::to_value(report)?;
                        let generated = report["generated"].clone();
                        reports.push(report);
                        generated
                    }
                    Err(error) => {
                        reports.push(json!({"kind":request.kind,"error":error.to_string()}));
                        json!({"text":"","finish_reason":"error"})
                    }
                }
            };
            reply = turn.generated(generated)?;
        }
    }
    live(cancel)?;
    ensure!(
        reply["command"] == "done" && turn.finished(),
        "unfinished language turn"
    );
    Ok(())
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
            |_| panic!("no handoff"),
            Duration::from_secs(1),
        )
        .await
        .unwrap();
        assert_eq!(quiet["text"], "");
        assert_eq!(quiet["spoken_text"], "");
        let mut address = input("ドギド");
        address["address_reply"] = "あの話、わし宛てでええんか？".into();
        let fixed = super::render(
            &c,
            &llm,
            &address,
            &mut rx,
            |_| panic!("no route"),
            |_| panic!("no handoff"),
            Duration::from_secs(1),
        )
        .await
        .unwrap();
        assert_eq!(fixed["text"], address["address_reply"]);
        assert_eq!(fixed["language_status"], "address_confirmation_requested");
        for text in ["剣に持ち替えて", "今の句を保存して"] {
            let reply = super::render(
                &c,
                &llm,
                &input(text),
                &mut rx,
                |_| Ok(()),
                |_| Ok(false),
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
                |_| Ok(false),
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
                |_| Ok(false),
                Duration::from_secs(1)
            )
            .await
            .unwrap_err()
            .to_string(),
            "cancelled"
        );
    }
    #[tokio::test]
    async fn disabled_model_and_language_use_native_fallback_without_transport() {
        let mut c = config();
        c.llm_enabled = false;
        c.language_enabled = false;
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
        frame["language_requested"] = true.into();
        let mut job = bridge::Input::native(
            frame,
            crate::events::GameEvent::parse(fixture["event"].clone()).unwrap(),
            serde_json::from_value(json!({"snapshot":fixture["snapshot"],"context":fixture["context"],"settings":fixture["settings"]})).unwrap(),
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
            |_| panic!("language disabled"),
            Duration::from_secs(1),
        )
        .await
        .unwrap();
        assert!(started.elapsed() < Duration::from_millis(500));
        assert!(!result["text"].as_str().unwrap().is_empty());
        assert_eq!(result["llm_reports"], json!([]));
        assert!(result.get("language_status").is_none());
    }
    #[test]
    fn native_projection_satisfies_existing_plan_and_material_boundary() {
        let mut checked = 0;
        for line in include_str!("../../fixtures/chat-materials.jsonl").lines() {
            let row: Value = serde_json::from_str(line).unwrap();
            let before = chat_materials::before_plan(
                &serde_json::from_value(row["event"].clone()).unwrap(),
                &serde_json::from_value(row["settings"].clone()).unwrap(),
                &serde_json::from_value(row["input"].clone()).unwrap(),
                &serde_json::from_value(row["context"].clone()).unwrap(),
                &serde_json::from_value(row["snapshot"].clone()).unwrap(),
                &CatalogLabels,
                Some("fixture"),
            )
            .unwrap();
            let Before::Plan(prepared) = before else {
                continue;
            };
            let plan = serde_json::from_value(row["plan"].clone()).unwrap();
            let After::Leaf(leaf) = chat_materials::after_plan(&prepared, &plan).unwrap() else {
                continue;
            };
            let Some(request) = &prepared.planner.request else {
                continue;
            };
            let mut handoff = planner::handoff::Handoff::default();
            handoff.record_plan(request, &plan).unwrap();
            assert_eq!(
                handoff.resolve(leaf.handoff_input.clone(), true).unwrap(),
                leaf.handoff
            );
            let input = leaf.input("fixture", 72);
            handoff.validate_leaf(&input.prompt.details).unwrap();
            handoff
                .validate_materials(
                    &input.prompt.details,
                    &input.validation,
                    row["context"]["workshop_open"] == true,
                )
                .unwrap();
            checked += 1;
        }
        assert!(checked >= 300, "{checked}");
    }
}
