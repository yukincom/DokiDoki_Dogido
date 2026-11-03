//! 前置き生成→元材料選択→三行生成→保存投影の接続を確認する。
//! モデル応答は固定し、材料の全件配送・選択境界・記録を検査する。句の意味理解は実モデルで別に見る。
use dogido_rust::haiku::{
    self, Backend, LineForm, StructuredRequest, TransformRequest,
    preparation::{Preparation, Reading, Start},
    source_atoms,
};
use serde_json::{Value, json};
use std::collections::{HashSet, VecDeque};

fn start() -> Start {
    serde_json::from_value(json!({
        "event":{"schema_version":"2026-05-24","adapter":"fixture",
            "observed_at":"2026-10-29T12:00:00+09:00","sequence":1,
            "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
            "player":{"held_item":"minecraft:obsidian","hotbar":{"selected_slot":0,"slots":[
                {"slot":0,"item_id":"minecraft:obsidian","count":1},
                {"slot":1,"item_id":"minecraft:dandelion","count":1},
                {"slot":2,"item_id":"minecraft:stick","count":2},
                {"slot":3,"item_id":"minecraft:bone","count":2}]}},
            "world":{"biome":"plains","sky_visible":true,"time_phase":"morning","weather":"clear"},
            "inventory":{"obsidian":1,"dandelion":1,"stick":2,"bone":2}},
        "runtime":{"current_structure":null,"player_name":"試験","inventory_order":["obsidian","dandelion","stick","bone"]},
        "settings":{},"dialogue_material":null
    })).unwrap()
}
fn prepare() -> (Preparation, Vec<Value>) {
    let (mut prep, _) = Preparation::capture(start()).unwrap();
    let next = prep
        .inspiration(&json!({"found":true,"kind":"contrast",
        "description":"黒曜石と花の色が目に浮かぶわ。", "elements":["黒曜石","タンポポ"],
        "focus":["色"],"confidence":0.9}))
        .unwrap();
    (
        prep,
        next.request.details["preface_material_candidates"]
            .as_array()
            .unwrap()
            .clone(),
    )
}
fn ids(candidates: &[Value], n: usize) -> Value {
    json!(
        candidates
            .iter()
            .take(n)
            .map(|g| g["material_id"].clone())
            .collect::<Vec<_>>()
    )
}

// 見込み: 前置きの1〜3材料は句用の絞込み結果が欠けても全件残り、前置き本文は別欄になる。
// 合格: 句用資料が選択数と一致し、未選択資料・派生前置きは入らない。意味は固定応答の範囲外。
#[test]
fn fewer_than_four_preface_materials_are_all_retained() {
    for count in 1..=3 {
        let (mut prep, candidates) = prepare();
        let out = prep
            .materials(&json!({"preface_material_ids":ids(&candidates,count),
            "poem_material_ids":ids(&candidates,1)}))
            .unwrap();
        let selected = &out.materials["preface_material_selection"];
        assert_eq!(selected["poem_materials"].as_array().unwrap().len(), count);
        assert_eq!(selected["preface_materials"], selected["poem_materials"]);
        assert_eq!(
            out.input.details["preface_direction"],
            "黒曜石と花の色が目に浮かぶわ。"
        );
        assert!(
            out.input
                .source_atoms
                .iter()
                .all(|a| a.basis_atom_ids.is_empty()
                    && !matches!(a.kind.as_str(), "preface_clause" | "poetic_interpretation"))
        );
        assert_eq!(
            out.input
                .source_atoms
                .iter()
                .map(|a| &a.source_ref)
                .collect::<HashSet<_>>()
                .len(),
            count
        );
    }
}

// 見込み: 4材料以上なら、前置きで使ったものの中から選んだ数だけを句へ渡す。
// 合格: 1・2・4個の部分集合をそのまま受け取り、3個への増減や未選択材料の追加をしない。
#[test]
fn four_or_more_can_select_a_subset_without_forcing_three() {
    for count in [1, 2, 4] {
        let (mut prep, candidates) = prepare();
        assert!(candidates.len() >= 4);
        let out = prep
            .materials(&json!({"preface_material_ids":ids(&candidates,4),
            "poem_material_ids":ids(&candidates,count)}))
            .unwrap();
        let selected = &out.materials["preface_material_selection"];
        assert_eq!(selected["preface_materials"].as_array().unwrap().len(), 4);
        assert_eq!(selected["poem_materials"].as_array().unwrap().len(), count);
        assert_eq!(
            out.input
                .source_atoms
                .iter()
                .map(|a| &a.source_ref)
                .collect::<HashSet<_>>()
                .len(),
            count
        );
    }
}

struct Fixed {
    replies: VecDeque<Value>,
    requests: Vec<StructuredRequest>,
}
impl Backend for Fixed {
    async fn generate(&mut self, r: StructuredRequest) -> anyhow::Result<Value> {
        self.requests.push(r);
        Ok(self
            .replies
            .pop_front()
            .expect("unexpected extra generation"))
    }
    async fn transform(&mut self, r: TransformRequest) -> anyhow::Result<LineForm> {
        Ok(LineForm {
            text: r.text.clone(),
            signature: r.text,
        })
    }
}
struct Kana;
impl Reading for Kana {
    async fn hiraganize(&mut self, s: &str) -> anyhow::Result<String> {
        Ok(s.into())
    }
}

// 見込み: 三行共通の提示資料は、生成と保存読戻しでも各行の記録が欠けない。
// 合格: 二材料で生成し、三行の同じID記録が保存後も残る。実使用済みとは記録しない。
#[tokio::test]
async fn shared_pool_survives_emission_and_saved_record_loading() {
    let (mut prep, candidates) = prepare();
    let out = prep
        .materials(&json!({"preface_material_ids":ids(&candidates,2)}))
        .unwrap();
    let mut backend = Fixed {
        replies: VecDeque::from([json!({"lines":["くろいいし","はなとそびえる","たんぽぽよ"]})]),
        requests: vec![],
    };
    let result = haiku::generate(&mut backend, out.input).await.unwrap();
    assert!(result.accepted);
    let emission = prep.emission(&result, &mut Kana).await.unwrap();
    let saved = serde_json::to_value(&emission).unwrap();
    let materials = &saved["materials"];
    assert_eq!(
        materials["source_attribution"],
        "provided_materials_unverified"
    );
    let atoms = source_atoms::source_atoms_from_materials(materials);
    let allowed = atoms.iter().map(|a| a.atom_id.clone()).collect();
    let lines = result.text.lines().map(str::to_owned).collect::<Vec<_>>();
    let restored = source_atoms::line_source_ids_from_materials(materials, &lines, &allowed);
    assert_eq!(restored.len(), 3);
    for i in 0..3 {
        assert_eq!(restored[&i], result.line_sources[i].atom_ids);
    }
    assert_eq!(backend.requests.len(), 1);
}

// 見込み: 未知ID・前置き選択外のID・空選択は、無関係な観測で埋めず既存失敗へ接続する。
// 合格: 生成用資料が空となり、追加モデル呼出しをせずinvalid_material_selectionを返す。
#[tokio::test]
async fn invalid_selection_does_not_restore_all_observations() {
    for which in 0..3 {
        let (mut prep, candidates) = prepare();
        let payload = match which {
            0 => json!({"preface_material_ids":["missing"]}),
            1 => {
                json!({"preface_material_ids":ids(&candidates,4),"poem_material_ids":[candidates[4]["material_id"]]})
            }
            _ => json!({"preface_material_ids":[]}),
        };
        let out = prep.materials(&payload).unwrap();
        assert!(out.input.source_atoms.is_empty());
        let mut backend = Fixed {
            replies: VecDeque::new(),
            requests: vec![],
        };
        let result = haiku::generate(&mut backend, out.input).await.unwrap();
        assert_eq!(
            result.failure_reason.as_deref(),
            Some("invalid_material_selection")
        );
        assert!(backend.requests.is_empty());
    }
}

fn change_start(change: impl FnOnce(&mut Value)) -> Start {
    let mut value = serde_json::to_value(start()).unwrap();
    change(&mut value);
    serde_json::from_value(value).unwrap()
}

// 見込み: 手持ちの代用品を含め、hotbarにある品だけを候補へ配送する。
// 合格: 枠外の骨・腐った肉とair・個数0が候補にならず、全ての有効枠が残る。モデル判断は固定。
#[test]
fn every_nonempty_hotbar_item_is_available_without_inventory_fallback() {
    let start = change_start(|v| {
        v["event"]["player"]["held_item"] = json!("minecraft:iron_pickaxe");
        v["event"]["inventory"] = json!({"iron_pickaxe":1,"bone":64,"rotten_flesh":64});
        v["event"]["player"]["hotbar"] = json!({"selected_slot":0,"slots":[
            {"slot":0,"item_id":"minecraft:iron_pickaxe","count":1},
            {"slot":1,"item_id":"minecraft:torch","count":8},
            {"slot":2,"item_id":"minecraft:bread","count":3},
            {"slot":3,"item_id":"minecraft:apple","count":2},
            {"slot":4,"item_id":"minecraft:dandelion","count":1},
            {"slot":5,"item_id":"minecraft:air","count":1},
            {"slot":6,"item_id":"minecraft:bone","count":0},
            {"slot":7}]});
    });
    let (_, context) = Preparation::capture(start).unwrap();
    let details = context.request.unwrap().details;
    assert!(
        ["torch", "bread", "apple", "dandelion"]
            .contains(&details["poem_item_id"].as_str().unwrap())
    );
    let refs: HashSet<_> = details["source_atoms"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|a| a["source_ref"].as_str())
        .collect();
    for id in [
        "item:iron_pickaxe",
        "item:torch",
        "item:bread",
        "item:apple",
        "item:dandelion",
    ] {
        assert!(refs.contains(id), "{id}: {refs:?}");
    }
    for id in ["item:bone", "item:rotten_flesh", "item:air"] {
        assert!(!refs.contains(id));
    }
}

// 見込み: 欠測・空hotbar・空の手でも、inventoryを材料の補充元にしない。
// 合格: どの場合も持ち物候補・手持ち材料が空。景色の材料は残る。実機の欠測発生は範囲外。
#[test]
fn missing_and_empty_hotbar_never_restore_inventory_or_air() {
    for bar in [
        Value::Null,
        json!({"selected_slot":0,"slots":[]}),
        json!({"selected_slot":0,"slots":[{"slot":0,"item_id":"minecraft:air","count":1}]}),
    ] {
        let start = change_start(|v| {
            v["event"]["player"]["held_item"] = json!("minecraft:bone");
            v["event"]["player"]["hotbar"] = bar;
        });
        let (_, context) = Preparation::capture(start).unwrap();
        let details = context.request.unwrap().details;
        assert_eq!(details["held_item"], "");
        assert_eq!(details["inventory_items"], json!([]));
        assert!(
            details["source_atoms"]
                .as_array()
                .unwrap()
                .iter()
                .all(|a| !a["source_ref"].as_str().unwrap().starts_with("item:"))
        );
        assert!(
            !details["preface_material_candidates"]
                .as_array()
                .unwrap()
                .is_empty()
        );
    }
}

struct ObservationLabels;
impl dogido_rust::chat_observation::Labels for ObservationLabels {
    fn mob_label(&self, id: &str) -> Option<&str> {
        dogido_rust::chat_catalog::catalog()
            .mob_entry(id)?
            .get("label")?
            .as_str()
    }
    fn mob_fallback_label(&self, id: &str) -> Option<&str> {
        self.mob_label(id)
    }
    fn hostile_label(&self, id: &str) -> String {
        id.into()
    }
    fn block_label(&self, id: &str) -> Option<&str> {
        dogido_rust::world_catalog::catalog()
            .block_entry(id)?
            .get("label")?
            .as_str()
    }
}

fn panda_start() -> Start {
    use dogido_rust::chat_observation::{ChatObservationMemory, NameOutcomeUpdate};
    let mut start = change_start(|v| {
        v["event"]["world"]["biome"] = json!("bamboo_jungle");
        v["event"]["nearby_composition"] = json!({"blocks":["bamboo"],"mobs":[],"mobs_truncated":false,"blocks_truncated":false});
        v["completed_turns"] = json!([{"turn_id":"panda-turn","player_text":"パンダさんかわいいね", "dogido_text":"竹を食べる姿がええなあ"}]);
    });
    let mut seen = serde_json::to_value(&start.event).unwrap();
    seen["observed_at"] = json!("2026-10-29T11:59:40+09:00");
    seen["passive_mobs"] = json!([{"type":"panda"}]);
    let seen = serde_json::from_value(seen).unwrap();
    let mut memory = ChatObservationMemory::default();
    memory.observe(&seen, &NameOutcomeUpdate::default(), &ObservationLabels);
    let snapshot = memory.snapshot(&start.event, &ObservationLabels);
    start.recent_observation_atoms =
        haiku::preparation::observations::recent_atoms(&start.event, &snapshot);
    start
}

// 見込み: 20秒前のパンダと会話のパンダを、現在地の竹林と同じ候補一覧へ別出典で渡せる。
// 合格: 両段階の一覧が一致し、過去の経過時間・会話turnが残る。現在視認のパンダは補作しない。
#[test]
fn bamboo_and_panda_sources_reach_both_preface_stages() {
    let (mut prep, context) = Preparation::capture(panda_start()).unwrap();
    let request = context.request.unwrap();
    let atoms = request.details["source_atoms"].as_array().unwrap();
    assert!(
        atoms
            .iter()
            .any(|a| a["source_ref"] == "biome:bamboo_jungle")
    );
    assert!(atoms.iter().any(|a| a["source_ref"] == "block:bamboo"));
    let recent = atoms
        .iter()
        .find(|a| a["observation_role"] == "recent_visual")
        .unwrap();
    assert_eq!(recent["text"], "さっき見たもん（20秒前に見たパンダ）");
    assert!(
        atoms
            .iter()
            .any(|a| a["source_ref"] == "dialogue:panda-turn:mob:panda"
                && a["claim_scopes"] == json!(["player_reported_context"]))
    );
    assert!(!atoms.iter().any(|a| a["source_ref"] == "mob:panda"));
    let next = prep
        .inspiration(&json!({"found":true,"description":"竹林にさっきのパンダの姿が浮かぶわ。"}))
        .unwrap();
    assert_eq!(
        request.details["preface_material_candidates"],
        next.request.details["preface_material_candidates"]
    );
    let rendered = dogido_rust::haiku_prompt::messages(&request).unwrap();
    assert!(rendered[1].content.contains("20秒前に見たパンダ"));
    assert!(rendered[1].content.contains("配送済みの会話でパンダ"));
}

// 見込み: 視認記録の既存期限を保ち、会話だけのモブを目に見える存在へ変えない。
// 合格: 61秒経過では過去視認候補が消えるが、配送済み会話由来のパンダは残る。模擬観測の範囲。
#[test]
fn expired_panda_sighting_does_not_turn_dialogue_into_presence() {
    use dogido_rust::chat_observation::{ChatObservationMemory, NameOutcomeUpdate};
    let mut start = panda_start();
    let mut seen = serde_json::to_value(&start.event).unwrap();
    seen["observed_at"] = json!("2026-10-29T11:58:59+09:00");
    seen["passive_mobs"] = json!([{"type":"panda"}]);
    let mut memory = ChatObservationMemory::default();
    memory.observe(
        &serde_json::from_value(seen).unwrap(),
        &NameOutcomeUpdate::default(),
        &ObservationLabels,
    );
    start.recent_observation_atoms = haiku::preparation::observations::recent_atoms(
        &start.event,
        &memory.snapshot(&start.event, &ObservationLabels),
    );
    assert!(start.recent_observation_atoms.is_empty());
    let (_, context) = Preparation::capture(start).unwrap();
    let atoms = &context.request.unwrap().details["source_atoms"];
    assert!(
        !atoms
            .as_array()
            .unwrap()
            .iter()
            .any(|a| a["observation_role"] == "recent_visual")
    );
    assert!(
        atoms
            .as_array()
            .unwrap()
            .iter()
            .any(|a| a["source_ref"] == "dialogue:panda-turn:mob:panda")
    );
}

// 見込み: 選択した竹林と過去のパンダだけが初案・再生成・保存へ同じ出典で渡る。
// 合格: 模擬モデルが初案で音数を外しても、再生成で材料・方針が保持され、保存行出典とhotbar記録が一致。
#[tokio::test]
async fn selected_panda_sources_and_hotbar_snapshot_survive_regeneration_and_save() {
    let initial = panda_start();
    let hotbar = serde_json::to_value(&initial.event.player.hotbar).unwrap();
    let (mut prep, context) = Preparation::capture(initial).unwrap();
    let request = context.request.unwrap();
    let candidates = request.details["preface_material_candidates"]
        .as_array()
        .unwrap();
    let offered_atoms = request.details["source_atoms"].clone();
    let recent_text = "さっき見たもん（20秒前に見たパンダ）";
    let chosen: Vec<_> = candidates
        .iter()
        .filter(|g| {
            g["facts"]
                .as_array()
                .unwrap()
                .iter()
                .any(|a| a["text"] == "竹林" || a["text"] == recent_text)
        })
        .map(|g| g["material_id"].clone())
        .collect();
    assert_eq!(chosen.len(), 2);
    prep.inspiration(&json!({"found":true,"description":"竹林にさっきのパンダの姿が浮かぶわ。"}))
        .unwrap();
    let out = prep
        .materials(&json!({"preface_material_ids":chosen}))
        .unwrap();
    assert!(out.input.source_atoms.iter().any(|a| a.text == recent_text));
    assert!(
        !out.input
            .source_atoms
            .iter()
            .any(|a| a.source_ref.starts_with("dialogue:"))
    );
    let mut backend = Fixed {
        replies: VecDeque::from([
            json!({"lines":["たけのかげ","さっきのパンダのすがたがまだ","おもいだす"]}),
            json!({"lines":[{"line_index":1,"text":"パンダのすがた"}]}),
        ]),
        requests: vec![],
    };
    let result = haiku::generate(&mut backend, out.input).await.unwrap();
    assert!(result.accepted, "{result:?}");
    assert_eq!(backend.requests.len(), 2);
    assert_eq!(
        backend.requests[0].details["available_poem_materials"],
        backend.requests[1].details["available_poem_materials"]
    );
    assert_eq!(
        backend.requests[0].details["preface_direction"],
        backend.requests[1].details["preface_direction"]
    );
    let prepared = prep.emission(&result, &mut Kana).await.unwrap();
    let emission = prepared.complete(chrono::Utc::now()).unwrap();
    let root = std::env::temp_dir().join(format!("dogido-a-materials-{}", uuid::Uuid::new_v4()));
    let store = dogido_rust::haiku_record::MemoryStore::new(root.clone());
    store.save_agent_haiku("a-test", "試験", &emission).unwrap();
    let saved: Value =
        serde_json::from_str(&std::fs::read_to_string(store.entries_path()).unwrap()).unwrap();
    assert_eq!(
        saved["materials_snapshot"]["material_input_snapshot"]["hotbar"],
        hotbar
    );
    assert_eq!(
        saved["materials_snapshot"]["material_input_snapshot"]["offered_source_atoms"],
        offered_atoms
    );
    let selected_refs: HashSet<_> = out.materials["source_atoms"]
        .as_array()
        .unwrap()
        .iter()
        .map(|a| a["source_ref"].as_str().unwrap())
        .collect();
    assert!(
        selected_refs
            .iter()
            .any(|r| r.starts_with("recent_visual:"))
    );
    assert!(selected_refs.contains("biome:bamboo_jungle"));
    assert_eq!(
        saved["materials_snapshot"]["source_atoms"],
        out.materials["source_atoms"]
    );
    for (index, source) in result.line_sources.iter().enumerate() {
        assert_eq!(
            saved["materials_snapshot"]["line_sources"][index]["atom_ids"],
            json!(source.atom_ids)
        );
    }
    assert_eq!(
        saved["materials_snapshot"]["source_attribution"],
        "provided_materials_unverified"
    );
    std::fs::remove_dir_all(root).unwrap();
}

// 見込み: 旧落下物を含む観測でも、生成材料には現在の視認と周辺の種類だけを加える。
// 合格: パンダ・敵・周辺観測を別の由来で含み、落下物と枠外の骨を除く。固定入力と資料JSONを確認する。
#[test]
fn current_mobs_keep_their_observation_source_and_legacy_drops_are_ignored() {
    let start = change_start(|v| {
        v["event"]["player"]["held_item"] = json!("minecraft:torch");
        v["event"]["player"]["hotbar"] =
            json!({"selected_slot":0,"slots":[{"slot":0,"item_id":"minecraft:torch","count":1}]});
        v["event"]["inventory"] = json!({"bone":64,"rotten_flesh":64});
        v["event"]["passive_mobs"] = json!([{"type":"panda"}]);
        v["event"]["visual_threats"] = json!([{"type":"creeper"}]);
        v["event"]["nearby_composition"] = json!({"mobs":[{"id":"cow","quantity":1}],"blocks":[],"mobs_truncated":false,"blocks_truncated":false});
        v["event"]["dropped_items"] = json!([{"name":"bone","count":1,"distance":2}]);
    });
    let (_, context) = Preparation::capture(start).unwrap();
    let request = context.request.unwrap();
    let atoms = request.details["source_atoms"].as_array().unwrap();
    assert!(
        atoms
            .iter()
            .any(|a| a["source_ref"] == "mob:panda" && a["observation_role"] == "passive_mob")
    );
    assert!(
        atoms
            .iter()
            .any(|a| a["observation_role"] == "current_visual"
                && a["text"].as_str().unwrap().contains("クリーパー"))
    );
    assert!(
        atoms
            .iter()
            .any(|a| a["observation_role"] == "current_surroundings")
    );
    assert!(
        !atoms.iter().any(|a| a["source_ref"] == "item:bone" || a["observation_role"] == "dropped_item")
    );
    assert!(!atoms.iter().any(|a| a["source_ref"] == "item:rotten_flesh"));
    assert_eq!(
        request.details["inventory_items"].as_array().unwrap().len(),
        1
    );
}

// 見込み: 配送済み直近3往復からのみ会話のモブを取り、古い話題や未配送入力を候補へ戻さない。
// 合格: 4往復前のパンダ・未配送のウシを除き、直近assistant発話のヒツジは会話由来で残る。
#[test]
fn mob_topics_respect_completed_dialogue_window() {
    let start = change_start(|v| {
        v["completed_turns"] = json!([
            {"turn_id":"old","player_text":"パンダがかわいい","dogido_text":"そうやな"},
            {"turn_id":"t1","player_text":"こんにちは","dogido_text":"ヒツジの話をしてたな"},
            {"turn_id":"t2","player_text":"晴れたね","dogido_text":"ええ天気や"},
            {"turn_id":"t3","player_text":"歩こう","dogido_text":"行こか"},
            {"turn_id":"pending","player_text":"ウシもいたね"}]);
    });
    let (_, context) = Preparation::capture(start).unwrap();
    let request = context.request.unwrap();
    let atoms = request.details["source_atoms"].as_array().unwrap();
    let mob_topics: Vec<_> = atoms
        .iter()
        .filter(|a| a["field_path"] == "mob_topic")
        .collect();
    assert!(
        mob_topics
            .iter()
            .any(|a| a["source_ref"] == "dialogue:t1:mob:sheep")
    );
    assert!(
        !mob_topics
            .iter()
            .any(|a| a["text"].as_str().unwrap().contains("パンダ")
                || a["source_ref"].as_str().unwrap().contains("pending"))
    );
}
