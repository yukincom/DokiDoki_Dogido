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
            "player":{"held_item":"minecraft:obsidian"},
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
