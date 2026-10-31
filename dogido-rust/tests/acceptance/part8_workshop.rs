//! 見込み: 共通材料を保存・読戻ししても、相談の材料と行の対応、対象外不変を保てる。合格: 三行のIDと未検証記録が入力へ届き、指定語・保持案だけを一度保存、askは不変。生成・解釈は固定し、返答の意味は実モデルで別評価。
use super::*;
use crate::haiku_record::{Emission, MemoryStore, Workshop};
use crate::haiku::{self, Backend, LineForm, StructuredRequest, TransformRequest, SourceAtom, preparation::{Preparation, Reading, Start}};
use crate::workshop_editing::acceptance_part8_engine::{candidate_pending, native_pending};
use std::{fs, path::PathBuf, sync::Arc, time::Instant};

const ORIGINAL: [&str; 3] = ["さくらのは", "くろいおのへと", "あさのいろ"];

struct Fixture {
    dialogue: Arc<Dialogue>,
    root: PathBuf,
    entry_bytes: Vec<u8>,
    entry_id: String,
    expected_atoms: Vec<SourceAtom>,
    _cancel: watch::Sender<bool>,
    _receiver: watch::Receiver<bool>,
}
impl Fixture {
    async fn new() -> Self { Self::with_materials(2, false).await }
    async fn with_materials(count: usize, saved_text: bool) -> Self {
        let root =
            std::env::temp_dir().join(format!("dogido-acceptance-part8-{}", uuid::Uuid::new_v4()));
        let dialogue = Dialogue::new(DialogueConfig {
            audio_enabled: false,
            llm_enabled: false,
            haiku: HaikuSettings {
                enabled: false,
                memory_enabled: !saved_text,
                memory_dir: root.clone(),
                ..Default::default()
            },
            ..Default::default()
        })
        .unwrap();
        dialogue.register("s", "試験", true);
        let (emission, expected_atoms) = material_emission(count).await;
        let store = MemoryStore::new(root.join("sessions/s"));
        let saved = store.save_agent_haiku("s", "試験", &emission).unwrap();
        let entry_bytes = fs::read(store.entries_path()).unwrap();
        // 元のemissionを使い回さず、実JSONLを本棚の読戻し経路へ通す。
        let loaded = crate::poem_book::load(&root).unwrap();
        assert_eq!(loaded.len(), 1);
        let poem = loaded.into_values().next().unwrap();
        assert_eq!(poem.original, emission);
        assert_eq!(poem.original.entry_id(), saved.entry_id);
        if saved_text { dialogue.open_saved_text_workshop("s", poem.clone()).unwrap(); }
        let (cancel, receiver) = watch::channel(false);
        {
            let mut data = dialogue.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            s.cancel = Some(cancel.clone());
            if !saved_text {
                s.haiku.workshop = Some(Workshop::open(
                    poem.original, Some(saved.entry_id.clone()), Instant::now(),
                ));
            }
        }
        Self {
            dialogue,
            root,
            entry_bytes,
            entry_id: saved.entry_id,
            expected_atoms,
            _cancel: cancel,
            _receiver: receiver,
        }
    }
    fn view(&self, text: &str) -> Value {
        let mut data = self.dialogue.data.lock().unwrap();
        Dialogue::workshop_view(data.sessions.get_mut("s").unwrap(), text).unwrap()
    }
    fn revisions(&self) -> Vec<Value> {
        fs::read_to_string(self.root.join("sessions/s/long_term/haiku_revisions.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }
    fn assert_original_entry_preserved(&self) {
        assert_eq!(
            fs::read(self.root.join("sessions/s/long_term/haiku_entries.jsonl")).unwrap(),
            self.entry_bytes
        );
    }
    fn retain_candidate(&self) {
        let mut data = self.dialogue.data.lock().unwrap();
        let w = data
            .sessions
            .get_mut("s")
            .unwrap()
            .haiku
            .workshop
            .as_mut()
            .unwrap();
        w.conversation_candidate = workshop_candidate::Candidate::from_interpreted(
            workshop_candidate::Draft {
                proposal: workshop_candidate::Proposal {
                    line_index: Some(0),
                    target_fragment: "さくらのは".into(),
                    replacement_text: "さくらいろ".into(),
                },
                evidence: "相談中の本人の一案".into(),
                validation_codes: vec![],
            },
            &w.current_lines,
            w.version,
        );
    }
    async fn apply(&self, view: &Value, pending: Pending, action: &str) -> Result<Value> {
        let mut result = json!({"workshop_action":action,"workshop_id":view["workshop_id"],
            "workshop_version":view["version"],"workshop_proposed":pending});
        self.dialogue
            .apply_workshop_edit("s", 0, &mut result)
            .await?;
        Ok(result)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}
// 選択前の観測とモデル応答は固定。期待値は生成直前の元材料から保持し、生成結果から逆算しない。
async fn material_emission(count: usize) -> (Emission, Vec<SourceAtom>) {
    let start: Start = serde_json::from_value(json!({
        "event":{"schema_version":"2026-05-24","adapter":"part8-materials",
          "observed_at":"2026-10-29T12:00:00+09:00","sequence":1,
          "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
          "player":{"held_item":"minecraft:cherry_leaves"},
          "world":{"biome":"plains","sky_visible":true,"time_phase":"morning","weather":"clear"},
          "inventory":{"cherry_leaves":1,"netherite_axe":1,"obsidian":1,"dandelion":1}},
        "runtime":{"current_structure":null,"player_name":"試験","inventory_order":["cherry_leaves","netherite_axe","obsidian","dandelion"]},
        "settings":{},"dialogue_material":null
    })).unwrap();
    let (mut prep, _) = Preparation::capture(start).unwrap();
    let next = prep.inspiration(&json!({"found":true,"kind":"contrast",
        "description":"葉と斧の対比を眺めたんや。","elements":["サクラの葉","ネザライトの斧"],"focus":["色"],"confidence":0.9})).unwrap();
    let candidates = next.request.details["preface_material_candidates"].as_array().unwrap();
    assert!(candidates.len() >= count);
    let ids: Vec<_> = candidates.iter().take(count).map(|v|v["material_id"].clone()).collect();
    let selected = prep.materials(&json!({"preface_material_ids":ids,"poem_material_ids":ids})).unwrap();
    let expected_atoms = selected.input.source_atoms.clone();
    assert_eq!(expected_atoms.iter().map(|a| &a.source_ref).collect::<std::collections::HashSet<_>>().len(), count);
    let mut backend = FixedGeneration { calls: 0 };
    let result = haiku::generate(&mut backend, selected.input).await.unwrap();
    assert!(result.accepted, "{:?}", result.failure_reason);
    assert_eq!(backend.calls, 1);
    let emission = prep.emission(&result, &mut KanaReading).await.unwrap().complete(chrono::Utc::now()).unwrap();
    (emission, expected_atoms)
}
struct FixedGeneration { calls: usize }
impl Backend for FixedGeneration {
    async fn generate(&mut self, request: StructuredRequest) -> Result<Value> {
        self.calls += 1;
        assert_eq!(request.kind, "haiku_draft");
        Ok(json!({"lines":ORIGINAL}))
    }
    async fn transform(&mut self, request: TransformRequest) -> Result<LineForm> {
        Ok(LineForm { text: request.text.clone(), signature: request.text })
    }
}
struct KanaReading;
impl Reading for KanaReading {
    async fn hiraganize(&mut self, text: &str) -> Result<String> { Ok(text.into()) }
}

fn consultation(
    view: &Value,
    text: &str,
    classified: Option<ClassifiedWorkshop>,
) -> (Value, Value) {
    let allowed = allowed_workshop_actions(
        "decide",
        false,
        view["conversation_candidate"].is_object(),
        Stage::Discussion,
        classified,
        classified.is_some(),
    );
    let frame = json!({"workshop":view,"text":text,"phase":"decide","turn_steps":[],"observation":null,
        "allowed_actions":allowed});
    let details = workshop_projection::details_for(&frame).unwrap();
    let messages = prompt::prepare(&json!({"details":details}), None).unwrap();
    (details, messages)
}
fn labelled_pair(value: &Value, label: &str, text: &str) -> bool {
    match value {
        Value::Object(map) => {
            (map.values().any(|v| v == label) && map.values().any(|v| v == text))
                || map.values().any(|v| labelled_pair(v, label, text))
        }
        Value::Array(values) => values.iter().any(|v| labelled_pair(v, label, text)),
        Value::String(value) => value.lines().any(|line| {
            line.strip_prefix(label)
                .is_some_and(|tail| tail.trim_start_matches([' ', ':', '：']).trim() == text)
        }),
        _ => false,
    }
}

// 見込み: 保存句の行名と本文が対応すれば、誤認識を相談として読める。合格: 実保存読戻し後、上の句=さくらのは・中の句=くろいおのへと・下の句=あさのいろが最終入力に届く。生成は固定し、解釈の成否は別評価。
#[tokio::test]
async fn acceptance_part8_current_lines_have_upper_middle_lower_labels_in_model_input() {
    let f = Fixture::new().await;
    let (details, messages) = consultation(
        &f.view("髪の子の意味は？"),
        "髪の子の意味は？",
        Some(ClassifiedWorkshop::Line(0)),
    );
    let context = &details["workshop_context"];
    let wire = messages["messages"][1]["content"].as_str().unwrap();
    assert!(
        wire.contains(&crate::text_format::spaced_json(context)),
        "検査した共有文脈が実プロンプトへ届いていない"
    );
    let missing: Vec<_> = ["上の句", "中の句", "下の句"]
        .into_iter()
        .zip(ORIGINAL)
        .filter(|(label, text)| !labelled_pair(context, label, text))
        .collect();
    assert!(
        missing.is_empty(),
        "行ラベルと本文の組が欠けている: {missing:?}"
    );
}

// 見込み: 材料入りの保存句でも、保持した一案を選び直せる。合格: さくらのは→さくらいろの保持案が、分類済み具体変更の共有文脈と最終入力に完全一致で届く。案の選択は固定し、実モデルの補作は別評価。
#[tokio::test]
async fn acceptance_part8_classified_change_receives_current_candidate_without_new_words() {
    let f = Fixture::new().await;
    f.retain_candidate();
    let view = f.view("その案に変えて");
    let (details, messages) =
        consultation(&view, "その案に変えて", Some(ClassifiedWorkshop::Change(0)));
    assert!(view["conversation_candidate"].is_object());
    assert_eq!(
        details["workshop_context"]["conversation_candidate"], view["conversation_candidate"],
        "具体変更の入力から保持案が落ちた"
    );
    assert!(
        messages["messages"][1]["content"]
            .as_str()
            .unwrap()
            .contains(&crate::text_format::spaced_json(
                &view["conversation_candidate"]
            ))
    );
}

// 見込み: 共通材料があっても指定行だけを編集できる。合格: 各行に長い指定語を保存し、他行は材料・来歴まで不変、元entry不変、同じ句IDへ版+1・revision1件。低confidence・原文不一致の解釈を固定し、実受付から保存を検査。
#[tokio::test]
async fn acceptance_part8_interpreted_player_words_save_only_selected_line() {
    for target in 0..3 {
        let f = Fixture::new().await;
        let view = f.view("認識原文には置換語の表記が一致していない");
        let args = json!({"replacement_text":"ひかりがずっとさしている","confidence":0.01,"evidence":"文脈から解釈した指定語"});
        let pending = native_pending(
            &view,
            "認識原文には置換語の表記が一致していない",
            target,
            args,
        )
        .unwrap();
        let result = f.apply(&view, pending, "stage_player_edit").await.unwrap();
        assert_eq!(result["workshop_outcome"], "player_edit_saved");
        let after = f.view("現在句");
        for i in 0..3 {
            if i == target {
                assert_eq!(
                    after["current_lines"][i]["surface_text"],
                    "ひかりがずっとさしている"
                );
            } else {
                assert_eq!(after["current_lines"][i], view["current_lines"][i]);
            }
        }
        assert_eq!(after["version"], view["version"].as_u64().unwrap() + 1);
        assert!(after["pending"].is_null());
        let saved = f.revisions();
        assert_eq!(saved.len(), 1);
        assert_eq!(saved[0]["id"], result["workshop_revision_id"]);
        assert_eq!(saved[0]["haiku_id"], f.entry_id);
        assert_eq!(saved[0]["base_text"], ORIGINAL.join("\n"));
        assert_eq!(saved[0]["lines"], after["current_lines"]);
        f.assert_original_entry_preserved();
        f.dialogue.shutdown().await;
    }
}

// 見込み: 材料を共有しても相談と採用を分けられる。合格: 保持時は保存0件、採用後は上の句だけさくらいろ・版+1・同じ句IDへrevision1件。他行の材料と元entryは不変。案の解釈を固定し、採用と保存は本体。
#[tokio::test]
async fn acceptance_part8_selected_candidate_is_saved_without_reinventing_words() {
    let f = Fixture::new().await;
    f.retain_candidate();
    let view = f.view("その案でお願い");
    assert_eq!(view["current_lines"][0]["surface_text"], ORIGINAL[0]);
    assert!(f.revisions().is_empty());
    let pending = candidate_pending(&view).unwrap();
    let result = f
        .apply(&view, pending, "stage_conversation_candidate")
        .await
        .unwrap();
    assert_eq!(result["workshop_outcome"], "player_edit_saved");
    let after = f.view("現在句");
    assert_eq!(after["current_lines"][0]["surface_text"], "さくらいろ");
    for i in 1..3 {
        assert_eq!(after["current_lines"][i], view["current_lines"][i]);
    }
    assert!(after["conversation_candidate"].is_null());
    assert_eq!(after["version"], 1);
    assert_eq!(f.revisions().len(), 1);
    assert_eq!(f.revisions()[0]["haiku_id"], f.entry_id);
    f.assert_original_entry_preserved();
    f.dialogue.shutdown().await;
}

// 見込み: 置換語なしのaskを編集へ変換しなければ、共通材料から勝手に補作しない。合格: ask受付後も句・材料・版・pending・保持案・元entryは不変、revision0件。askの選択は固定し、本体の受付と保存を確認。
#[tokio::test]
async fn acceptance_part8_ask_for_missing_words_keeps_verse_version_and_files() {
    let f = Fixture::new().await;
    let text = "上の句は何を言いたいのか分かりにくいね";
    let view = f.view(text);
    let (details, _) = consultation(&view, text, Some(ClassifiedWorkshop::Change(0)));
    let decoded = response::decode(
        &json!({"workshop":view,"text":text,"payload":{
        "action":"ask","speech":"どの言葉に変えたい？"}}),
        &details,
    )
    .unwrap();
    assert_eq!(decoded["step"]["action"], "ask");
    let mut result = json!({"workshop_action":decoded["step"]["action"],"workshop_id":view["workshop_id"],
        "workshop_version":view["version"]});
    f.dialogue
        .apply_workshop_edit("s", 0, &mut result)
        .await
        .unwrap();
    let after = f.view(text);
    for key in [
        "current_lines",
        "version",
        "pending",
        "conversation_candidate",
        "materials",
    ] {
        assert_eq!(after[key], view[key], "{key}");
    }
    assert!(f.revisions().is_empty());
    f.assert_original_entry_preserved();
    f.dialogue.shutdown().await;
}

// 見込み: 材料が共通でも編集を別の句・版・相談へ付け替えない。合格: 元句・版・workshop ID不一致と取消の各条件でエラー、直前状態と元entryは不変、revision0件。解釈済み差分を固定し、実保存境界を確認。
#[tokio::test]
async fn acceptance_part8_stale_base_version_or_cancel_cannot_write_revision() {
    for mismatch in ["base", "version", "cancel", "workshop_id"] {
        let f = Fixture::new().await;
        let view = f.view("上の句を変えて");
        let pending = native_pending(
            &view,
            "上の句を変えて",
            0,
            json!({"replacement_text":"ひかりさす"}),
        )
        .unwrap();
        {
            let mut data = f.dialogue.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            let w = s.haiku.workshop.as_mut().unwrap();
            match mismatch {
                "base" => w.current_lines[1].surface_text = "別の現在句".into(),
                "version" => w.version += 1,
                "workshop_id" => w.hud_id = "different-workshop".into(),
                "cancel" => {
                    f._cancel.send(true).unwrap();
                }
                _ => unreachable!(),
            }
        }
        let before = f.view("検査前");
        assert!(
            f.apply(&view, pending, "stage_player_edit").await.is_err(),
            "{mismatch}"
        );
        let after = f.view("検査後");
        for key in ["current_lines", "version", "pending"] {
            assert_eq!(after[key], before[key], "{mismatch}: {key}");
        }
        assert!(f.revisions().is_empty());
        f.assert_original_entry_preserved();
        f.dialogue.shutdown().await;
    }
}

// 見込み: 自動再生成の固定行再掲許容をWSへ持ち込まなければ、指定外の行を守れる。合格: 上の句の編集へ下の句の改変を混ぜるとエラー、三行・版・元entry不変、revision0件。差分は固定し、実保存境界を確認。
#[tokio::test]
async fn acceptance_part8_extra_changed_line_cannot_cross_save_boundary() {
    let f = Fixture::new().await;
    let view = f.view("上の句を変えて");
    let mut pending = native_pending(
        &view,
        "上の句を変えて",
        0,
        json!({"replacement_text":"ひかりさす"}),
    )
    .unwrap();
    pending.lines[2].surface_text = "別行の改変".into();
    assert!(f.apply(&view, pending, "stage_player_edit").await.is_err());
    let after = f.view("現在句");
    assert_eq!(after["current_lines"], view["current_lines"]);
    assert_eq!(after["version"], view["version"]);
    assert!(f.revisions().is_empty());
    f.assert_original_entry_preserved();
    f.dialogue.shutdown().await;
}

fn assert_material_context(f: &Fixture, context: &Value) {
    let ids: Vec<_> = f.expected_atoms.iter().map(|a| a.atom_id.clone()).collect();
    assert!(!ids.is_empty());
    assert_eq!(context["source_attribution"], "provided_materials_unverified");
    assert_eq!(context["line_sources_for"], "current_verse");
    assert_eq!(context["saved_line_sources"].as_array().unwrap().len(), 3);
    for (index, text) in ORIGINAL.into_iter().enumerate() {
        let row = &context["saved_line_sources"][index];
        assert_eq!(row["line_index"], index);
        assert_eq!(row["text"], text);
        assert_eq!(row["atom_ids"], json!(ids));
        assert_eq!(row["provenance"], "generated_material_assignment");
    }
    let projected = context["source_atoms"].as_array().unwrap();
    // 要約は最大24 atom、行別sourcesは最大3件という既存表示契約。全IDの検査と分ける。
    assert!(f.expected_atoms.len() <= 24, "fixture must fit shared context without truncation");
    assert_eq!(projected.len(), f.expected_atoms.len());
    for atom in &f.expected_atoms {
        let row = projected.iter().find(|r| r["atom_id"] == atom.atom_id).unwrap();
        assert_eq!(row["text"], atom.text);
        assert_eq!(row["source_ref"], atom.source_ref);
        assert_eq!(row["claim_class"], atom.claim_class);
    }
}

struct FixedRevision { requests: Vec<StructuredRequest> }
impl Backend for FixedRevision {
    async fn generate(&mut self, request: StructuredRequest) -> Result<Value> {
        assert_eq!(request.kind, "haiku_workshop_revision");
        self.requests.push(request);
        Ok(json!({"lines":[{"line_index":0,"expected_text":ORIGINAL[0],"replacement_text":"さくらいろ"}]}))
    }
    async fn transform(&mut self, _: TransformRequest) -> Result<LineForm> {
        anyhow::bail!("WS must not normalize the proposed wording")
    }
}

// 見込み: 共通材料を実保存後も復元すれば、相談や新案生成で行ごとの資料が欠けない。合格: 2・4材料×通常/保存句で三行の全ID・未検証属性が最終入力まで一致、参照だけでは句・版・ファイル不変。生成応答は固定、保存と読戻しは本体。
#[tokio::test]
async fn acceptance_part8_saved_shared_materials_reach_consultation_and_revision_inputs() {
    for count in [2, 4] {
        for saved_text in [false, true] {
            let f = Fixture::with_materials(count, saved_text).await;
            let before = f.view("この句の材料は？");
            assert_eq!(before["entry_id"], f.entry_id);
            let ids: Vec<_> = f.expected_atoms.iter().map(|a| a.atom_id.clone()).collect();
            for row in before["current_lines"].as_array().unwrap() {
                assert_eq!(row["source_atom_ids"], json!(ids));
                assert_eq!(row["source_atoms"], json!(f.expected_atoms));
            }
            let (details, messages) = consultation(&before, "この句の材料は？", None);
            let context = &details["workshop_context"];
            assert_material_context(&f, context);
            assert!(messages["messages"][1]["content"].as_str().unwrap()
                .contains(&crate::text_format::spaced_json(context)));
            let revision = workshop_projection::revision_input(&json!({"workshop":before,
                "findings":[{"line_index":0,"problem":"preference","note":"別の案を相談"}],"max_tokens":192})).unwrap();
            for index in 0..3 { assert_eq!(revision["line_sources"][index.to_string()], json!(ids)); }
            assert_eq!(revision["basis"]["source_atoms"], json!(f.expected_atoms));
            assert_material_context(&f, &revision["basis"]["details"]["workshop_context"]);
            let mut backend = FixedRevision { requests: vec![] };
            let proposal = haiku::revision::generate(&mut backend, serde_json::from_value(revision).unwrap()).await.unwrap();
            assert!(proposal.accepted);
            assert_eq!(backend.requests.len(), 1);
            let request = &backend.requests[0];
            assert_material_context(&f, &request.details["workshop_context"]);
            let prompt = crate::haiku_prompt::messages(request).unwrap();
            assert!(prompt.iter().any(|m| m.content.contains(
                &crate::text_format::spaced_json(&request.details["workshop_context"]))));
            for index in 1..3 { assert_eq!(proposal.line_sources[index].atom_ids, ids); }
            assert_eq!(f.view("この句の材料は？"), before);
            assert!(f.revisions().is_empty());
            f.assert_original_entry_preserved();
            f.dialogue.shutdown().await;
        }
    }
}

// 見込み: 参照後も未検証の属性が残れば、記録の存在を実使用の証明に取り違えにくい。合格: 四材料の全IDを三行から参照し、続きの入力に元文脈と同じtool IDの結果が届く。句・材料・版・保存は不変。native応答は固定し、発話の意味は別評価。
#[tokio::test]
async fn acceptance_part8_inspection_preserves_provided_materials_without_mutation() {
    let f = Fixture::with_materials(4, true).await;
    let text = "各行で実際に使った材料まで確認できる？";
    let before = f.view(text);
    let call = crate::workshop::inspection_tool::Call::decode(json!({"role":"assistant","content":null,
        "tool_calls":[{"id":"material-inspection","type":"function","function":{
            "name":"inspect_verse","arguments":"{\"checks\":[\"source\"]}"}}]})).unwrap();
    let lines: Vec<HaikuLine> = serde_json::from_value(before["current_lines"].clone()).unwrap();
    let observed = workshop::inspect(&lines, &serde_json::from_value::<Vec<String>>(call.payload()["checks"].clone()).unwrap());
    let ids: Vec<_> = f.expected_atoms.iter().map(|a| &a.atom_id).collect();
    for row in observed["lines"].as_array().unwrap() {
        assert_eq!(row["source_status"], "recorded");
        assert_eq!(row["source_atom_ids"], json!(ids));
        assert_eq!(row["source_texts"], json!(f.expected_atoms.iter().take(3).map(|a| &a.text).collect::<Vec<_>>()));
    }
    let details = workshop_projection::details_for(&json!({"workshop":before,"text":text,
        "phase":"after_inspection","turn_steps":[],"observation":observed,
        "allowed_actions":workshop::allowed_actions("after_inspection",false,false)})).unwrap();
    assert_material_context(&f, &details["workshop_context"]);
    let mut request = prompt::prepare(&json!({"details":details}), None).unwrap();
    request["messages"].as_array_mut().unwrap().extend(call.result_messages(&observed));
    let tool = request["messages"].as_array().unwrap().last().unwrap();
    assert_eq!(tool["tool_call_id"], "material-inspection");
    assert_eq!(serde_json::from_str::<Value>(tool["content"].as_str().unwrap()).unwrap(), observed);
    assert!(request["messages"][1]["content"].as_str().unwrap().contains(
        &crate::text_format::spaced_json(&details["workshop_context"])));
    assert!(request.to_string().contains("実際に使った根拠の確認はしてへん"));
    assert_eq!(f.view(text), before);
    f.assert_original_entry_preserved();
    assert!(f.revisions().is_empty());
    f.dialogue.shutdown().await;
}
