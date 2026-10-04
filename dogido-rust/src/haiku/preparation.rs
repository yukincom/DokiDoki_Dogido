//! 一回の発句について、受け取った観測と読み・会話材料を固定し、生成前後の段階をつなぐ。
//! 通常はcaptureで見どころ抽出要求を作り、inspirationで前置きとscene要求、materialsで三行生成入力を返す。
//! 生成器の採用結果をemissionで表記・読み・行出典を持つ発句記録へ変換する。LLM無効時は固定句の経路を使う。
//! モデル呼出し、前置きの配送完了、発句時刻と保存は呼出側が担当し、この値はその一件分の準備状態を持つ。
use super::{
    GroundedHaikuResult, Input, SourceAtom, StructuredRequest,
    context::{Context as HaikuContext, Irony, Scene, scene_for_spoken_irony},
    materials::{self as selection, ReadingCorrection, ReadingSnapshot, RuntimeRead},
    source_atoms::*,
};
use crate::text_format::{self, ContainerFormat::QuotedRepr};
use crate::{
    chat_catalog::{strip, truth},
    events::GameEvent,
    haiku_record::{HaikuLine, PreparedEmission},
};
use anyhow::{Context as _, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::future::Future;
pub mod fallback;
mod materials;
pub use materials::dialogue_material;
/// 観測イベントだけでは復元できない呼出側の文脈。所持品の順序は受信JSONの順序を保持する。
/// 現在構造物とプレイヤー名も同じ発句の準備へ渡し、途中のsession更新を直接読みに行かない。
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RuntimeSnapshot {
    pub current_structure: Option<String>,
    pub inventory_order: Vec<String>,
    pub player_name: String,
}
/// 見どころ抽出と三行生成に渡す予算・生成方式。再生成回数等はcapture時に検査する。
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub llm_enabled: bool,
    pub structured_max_tokens: u64,
    pub grounding_max_tokens: u64,
    pub generation_strategy: String,
    pub max_regeneration_rounds: i64,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            llm_enabled: true,
            structured_max_tokens: 192,
            grounding_max_tokens: 512,
            generation_strategy: "three_slot".into(),
            max_regeneration_rounds: 6,
        }
    }
}
/// 発句開始時の入力一式。eventとruntimeに、読み訂正・有効なlesson・配送済み会話を添える。
/// dialogue_materialを省略した場合はcompleted_turnsから短い会話材料を作る。明示した空値は材料なしを表す。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Start {
    pub event: GameEvent,
    pub runtime: RuntimeSnapshot,
    pub settings: Settings,
    #[serde(default)]
    pub reading_corrections: Vec<Value>,
    #[serde(default)]
    pub lessons: Vec<Value>,
    #[serde(default)]
    pub completed_turns: Vec<Value>,
    pub dialogue_material: Option<Value>,
}
/// captureの結果。通常は見どころ抽出request、モデル無効時はfixed_textを呼び手へ返す。
#[derive(Clone, Debug, Serialize)]
pub struct ContextOutput {
    pub request: Option<StructuredRequest>,
    pub fallback_text: String,
    pub fixed_text: Option<String>,
}
/// 見どころの説明、配送する前置き文、次のscene要求を束ねた結果。
#[derive(Clone, Debug, Serialize)]
pub struct InspirationOutput {
    pub text: String,
    pub spoken_text: String,
    pub request: StructuredRequest,
}
/// 三行生成器へのInputと、採用時の記録へ残す材料・解釈のsnapshot。
#[derive(Clone, Debug, Serialize)]
pub struct MaterialsOutput {
    pub input: Input,
    pub materials: Map<String, Value>,
    pub interpretation: Option<String>,
    pub interpretation_origin: Option<String>,
}
/// 通常はContext→Inspiration→Materials→Emitted。モデル無効時はFixed→Emittedへ進む。
#[derive(Debug, PartialEq, Eq)]
enum Stage {
    Context,
    Fixed,
    Inspiration,
    Materials,
    Emitted,
}
/// 見どころ、生成材料、発句記録へ順に進む一件の状態。各公開操作はstageを照合する。
/// 同じモデル応答の二重適用や、材料準備前の発句確定を段階違反として返す。
pub struct Preparation {
    stage: Stage,
    event: GameEvent,
    settings: Settings,
    context: HaikuContext,
    readings: ReadingSnapshot,
    lessons: Vec<Value>,
    dialogue: Value,
    irony: Irony,
    interpretation: Option<String>,
    atoms: Vec<SourceAtom>,
    materials: Map<String, Value>,
    fixed_text: Option<String>,
    background_companions: Vec<CatalogSourceSnapshot>,
}
fn take(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}
fn value_text(v: Option<&Value>) -> String {
    v.filter(|v| truth(v))
        .map(|value| text_format::value_text(value, QuotedRepr))
        .unwrap_or_default()
}
fn normalized(s: Option<&str>) -> Option<String> {
    let s = strip(s.unwrap_or("")).to_lowercase();
    let s = s.rsplit(':').next().unwrap_or("");
    (!s.is_empty()).then(|| s.into())
}
/// 保存済みの読み訂正を表記ごとの参照表へまとめる。表記・読み・禁止読みの外形不正はErrになる。
/// 同じ表記の読みは後の行で更新し、過去の誤読候補は重複を除いて蓄積する。
pub fn reading_snapshot(rows: &[Value]) -> Result<ReadingSnapshot> {
    let mut snapshot = ReadingSnapshot::default();
    for row in rows {
        let surface = strip(
            row["surface"]
                .as_str()
                .context("reading surface must be a string")?,
        );
        let reading = strip(
            row["reading"]
                .as_str()
                .context("reading must be a string")?,
        );
        let mut forbidden = if row["forbidden_readings"].is_null() {
            vec![]
        } else {
            row["forbidden_readings"]
                .as_array()
                .context("forbidden readings must be a list")?
                .clone()
        };
        if let Some(wrong) = row.get("wrong_reading").filter(|v| truth(v))
            && !forbidden.contains(wrong)
        {
            forbidden.push(wrong.clone());
        }
        let entry = snapshot
            .by_surface
            .entry(surface.into())
            .or_insert_with(ReadingCorrection::default);
        entry.reading = reading.into();
        for wrong in forbidden.into_iter().filter(truth) {
            let wrong = text_format::value_text(&wrong, QuotedRepr);
            if !entry.forbidden_readings.contains(&wrong) {
                entry.forbidden_readings.push(wrong);
            }
        }
    }
    Ok(snapshot)
}
/// 見どころの説明を短い前置きへ整え、見つからなければ「なんか浮かんできたわ。」を返す。
/// 既に「浮かぶ／思いつく」を含む説明には同じ結びを重ねず、句読点だけを補う。
pub fn compose_inspiration_speech(found: bool, description: &str) -> String {
    let s = if found { strip(description) } else { "" };
    if s.is_empty() {
        return "なんか浮かんできたわ。".into();
    }
    if ["浮か", "おもいつ", "思いつ"].iter().any(|m| s.contains(m)) {
        if s.ends_with(['。', '！', '？', '!', '?']) {
            s.into()
        } else {
            format!("{s}。")
        }
    } else {
        format!(
            "{}、なんか浮かんできたわ。",
            s.trim_end_matches(['。', '！', '？', '!', '?'])
        )
    }
}
impl Preparation {
    /// 設定を検査し、観測・所持品・読み訂正・会話材料からこの発句のContextを作る。
    /// LLM有効ならhaiku_irony要求、無効なら観測に対応した固定句を返す。ここではモデルを呼ばない。
    pub fn capture(start: Start) -> Result<(Self, ContextOutput)> {
        ensure!(
            start.settings.grounding_max_tokens >= 1
                && (0..=8).contains(&start.settings.max_regeneration_rounds)
                && ["whole_poem", "three_slot", "one_plus_two", "two_plus_one"]
                    .contains(&start.settings.generation_strategy.as_str()),
            "invalid haiku preparation settings"
        );
        let readings = reading_snapshot(&start.reading_corrections)?;
        let dialogue = match &start.dialogue_material {
            Some(v) if !truth(v) => json!({}),
            Some(v) => {
                ensure!(v.is_object(), "dialogue_material must be an object");
                v.clone()
            }
            None => dialogue_material(&start.completed_turns)?,
        };
        // 会話中に話題となった同行者だけを主材料の観測へ昇格する。要約・motifに加え、
        // 材料を自動作成する経路では配送済みturnの本人発話も使い、残る同行者から背景材料を一体分選ぶ。
        let mut topics = dialogue
            .get("summary")
            .and_then(Value::as_str)
            .into_iter()
            .map(str::to_owned)
            .chain(
                dialogue["motifs"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_owned),
            )
            .collect::<Vec<_>>();
        if start.dialogue_material.is_none() {
            topics.extend(
                materials::completed_player_turns(&start.completed_turns)?
                    .into_iter()
                    .map(|(_, text)| text),
            );
        }
        let primary_event = super::companions::primary_event(&start.event, &topics);
        let background_companions =
            super::companions::background(&start.event, &primary_event, &readings);
        let world = crate::world_catalog::catalog();
        let context = selection::capture(
            &primary_event,
            RuntimeRead {
                current_structure: start.runtime.current_structure.as_deref(),
                player_name: &start.runtime.player_name,
                inventory_order: &start.runtime.inventory_order,
            },
            world,
            crate::chat_catalog::catalog(),
            world,
            &readings,
        )?;
        let mut job = Self {
            stage: Stage::Context,
            event: primary_event,
            settings: start.settings,
            context,
            readings,
            lessons: start.lessons,
            dialogue,
            irony: Irony::default(),
            interpretation: None,
            atoms: vec![],
            materials: Map::new(),
            fixed_text: None,
            background_companions,
        };
        let request = if job.settings.llm_enabled {
            Some(job.request("haiku_irony", job.context.irony_details()))
        } else {
            job.fixed_text = Some(fallback::fixed_text(&job.event));
            job.seed(Irony::default(), None);
            job.stage = Stage::Fixed;
            None
        };
        let output = ContextOutput {
            request,
            fallback_text: fallback::failed_text(),
            fixed_text: job.fixed_text.clone(),
        };
        Ok((job, output))
    }
    fn request(&self, kind: &str, details: Map<String, Value>) -> StructuredRequest {
        StructuredRequest {
            kind: kind.into(),
            details,
            route: "chat".into(),
            temperature: if kind == "haiku_irony" { 0.15 } else { 0.2 },
            max_tokens: Some(self.settings.structured_max_tokens),
            fallback_value: json!({"found":false}),
        }
    }
    /// scene応答前の材料を初期化する。観測出典と本人に帰属する会話出典を併合し、
    /// 見どころ・前置き・道具と読みの制約を、後の発句記録にも渡せるmaterialsへ保存する。
    fn seed(&mut self, irony: Irony, spoken: Option<&str>) {
        self.interpretation = (irony.found && !strip(&irony.description).is_empty())
            .then(|| strip(&irony.description).into());
        self.irony = irony;
        self.atoms = merge_source_atoms(&[
            self.context.source_atoms.clone(),
            materials::conversation_atoms(&self.dialogue),
        ]);
        self.materials = materials::seed(
            &self.event,
            &self.context,
            &self.irony,
            &Scene::default(),
            self.interpretation.as_deref(),
            &self.atoms,
            spoken,
        );
        materials::attach_conversation(&mut self.materials, &self.dialogue);
        if let Some(constraints) = selection::constraint_details(
            &self.event,
            &Scene::default(),
            crate::world_catalog::catalog(),
            &self.readings,
            &self.lessons,
        ) {
            self.materials
                .insert("haiku_constraints".into(), constraints);
        }
    }
    /// Context段階の見どころ応答を読み、前置きと次のhaiku_scene要求を返す。
    /// 応答を解釈できなければ見どころなしで続ける。返したspoken_textの配送は呼出側の仕事になる。
    pub fn inspiration(&mut self, payload: &Value) -> Result<InspirationOutput> {
        ensure!(
            self.stage == Stage::Context,
            "invalid haiku preparation stage"
        );
        let irony = Irony::from_mapping(payload).unwrap_or_default();
        let spoken = compose_inspiration_speech(irony.found, &irony.description);
        self.seed(irony, Some(&spoken));
        self.stage = Stage::Inspiration;
        Ok(InspirationOutput {
            text: if self.irony.found {
                strip(&self.irony.description).into()
            } else {
                String::new()
            },
            spoken_text: spoken,
            request: self.request("haiku_scene", self.context.scene_details(Some(&self.irony))),
        })
    }
    /// 前置きの後のscene応答を、生成器用Inputと保存用materialsへまとめる。
    /// sceneが不成立でも一次観測の出典は残し、会話・前置き由来・背景同行者の出典を区別して併合する。
    /// 前置きを配送してから呼ぶ順序は呼出側が守る。この関数は保存した前置き文との対応を確認する。
    pub fn materials(&mut self, payload: &Value) -> Result<MaterialsOutput> {
        ensure!(
            self.stage == Stage::Inspiration,
            "invalid haiku preparation stage"
        );
        let mut scene =
            Scene::from_mapping(payload, &self.context.source_atoms).unwrap_or_default();
        let spoken = value_text(self.materials.get("preface_spoken"));
        let spoken = strip(&spoken);
        let core = strip(&self.irony.description);
        let was_spoken = self.irony.found
            && !core.is_empty()
            && spoken
                .trim_end_matches(['。', '！', '？', '!', '?'])
                .contains(core.trim_end_matches(['。', '！', '？', '!', '?']));
        if was_spoken {
            scene = scene_for_spoken_irony(&self.irony, &scene, &self.context.source_atoms);
        }
        // 説明が実際の前置き文に含まれる場合だけ、その説明に由来する節を出典候補へ加える。
        // 対応しないsceneの説明を「既に共有した見どころ」として三行の根拠へ混ぜないため。
        let clauses = if was_spoken {
            scene.clauses.as_slice()
        } else {
            &[]
        };
        self.atoms = merge_source_atoms(&[
            self.context.source_atoms.clone(),
            atoms_from_preface_clauses(clauses),
            atom_from_poetic_interpretation(clauses)
                .into_iter()
                .collect(),
            materials::conversation_atoms(&self.dialogue),
            atoms_from_catalog_sources(&self.background_companions, 0, 1),
        ]);
        self.materials = materials::seed(
            &self.event,
            &self.context,
            &self.irony,
            &scene,
            self.interpretation.as_deref(),
            &self.atoms,
            (!spoken.is_empty()).then_some(spoken),
        );
        let origin = if was_spoken {
            "spoken_preface"
        } else {
            "generated_unspoken"
        };
        self.materials
            .insert("interpretation_origin".into(), origin.into());
        materials::attach_conversation(&mut self.materials, &self.dialogue);
        let constraints = selection::constraint_details(
            &self.event,
            &scene,
            crate::world_catalog::catalog(),
            &self.readings,
            &self.lessons,
        );
        if let Some(c) = &constraints {
            self.materials.insert("haiku_constraints".into(), c.clone());
        }
        let mut details = self.context.prompt_details(Some(&self.irony), Some(&scene));
        if !self.background_companions.is_empty() {
            let companions = json!(
                self.background_companions
                    .iter()
                    .map(CatalogSourceSnapshot::to_dict)
                    .collect::<Vec<_>>()
            );
            details.insert("background_companions".into(), companions.clone());
            self.materials
                .insert("background_companions".into(), companions);
        }
        if truth(&self.dialogue) {
            details.insert("player_dialogue_material".into(), self.dialogue.clone());
        }
        details.insert("haiku_constraints".into(), constraints.into());
        self.stage = Stage::Materials;
        Ok(MaterialsOutput {
            input: Input {
                details,
                source_atoms: self.atoms.clone(),
                fallback_text: fallback::failed_text(),
                max_tokens: Some(self.settings.structured_max_tokens),
                grounding_max_tokens: self.settings.grounding_max_tokens,
                generation_strategy: self.settings.generation_strategy.clone(),
                max_regeneration_rounds: self.settings.max_regeneration_rounds,
                llm_enabled: self.settings.llm_enabled,
            },
            materials: self.materials.clone(),
            interpretation: self.interpretation.clone(),
            interpretation_origin: Some(origin.into()),
        })
    }
    /// 採用された三行と出典を、表示表記・確定読み・安定した行IDを持つPreparedEmissionへ変換する。
    /// 固定句経路では準備した句との一致も検査する。時刻の付与、句の公開、保存はこの戻り値を使う呼出側で行う。
    pub async fn emission<R: Reading>(
        &mut self,
        result: &GroundedHaikuResult,
        reader: &mut R,
    ) -> Result<PreparedEmission> {
        ensure!(
            matches!(self.stage, Stage::Materials | Stage::Fixed),
            "invalid haiku preparation stage"
        );
        ensure!(
            result.accepted && !fallback::failed(&result.text),
            "only accepted poem text may become an emission"
        );
        if self.stage == Stage::Fixed {
            ensure!(
                Some(&result.text) == self.fixed_text.as_ref(),
                "disabled LLM emission must use prepared fixed text"
            );
        }
        let stripped = fallback::strip_preface(&result.text);
        let mut mats = self.materials.clone();
        if self.stage == Stage::Materials {
            mats.insert("line_sources".into(), json!(result.line_sources));
            mats.insert(
                "generation_strategy".into(),
                result.generation_strategy.clone().into(),
            );
            mats.insert(
                "regeneration_rounds".into(),
                result.regeneration_rounds.into(),
            );
            mats.insert(
                "prompt_variant".into(),
                result.prompt_variant.clone().into(),
            );
        }
        materials::attach_links(&mut mats, stripped);
        let surfaces = split_verse(stripped);
        let mut lines = vec![];
        if surfaces.len() == 3 {
            for (i, surface) in surfaces.iter().enumerate() {
                let raw = reader.hiraganize(surface).await?;
                let reading: String = super::lexical::hiragana(&raw)
                    .chars()
                    .filter(|c| !crate::knowledge::query::space(*c))
                    .collect();
                if reading.is_empty()
                    || !reading
                        .chars()
                        .all(|c| ('ぁ'..='ゖ').contains(&c) || c == 'ー')
                {
                    lines.clear();
                    break;
                }
                let source = if self.stage == Stage::Materials {
                    result.line_sources.iter().find(|s| s.line_index == i)
                } else {
                    None
                };
                let (id, position, name) = crate::haiku_record::LinePosition::ALL[i].metadata();
                lines.push(HaikuLine {
                    line_id: id.into(),
                    line_index: i,
                    position: position.into(),
                    canonical_name: name.into(),
                    surface_text: surface.clone(),
                    reading_text: reading,
                    source_atom_ids: source
                        .map(|s| {
                            s.atom_ids
                                .iter()
                                .map(|s| strip(s))
                                .filter(|s| !s.is_empty())
                                .map(str::to_owned)
                                .collect()
                        })
                        .unwrap_or_default(),
                    source_atoms: source
                        .map(|s| {
                            s.sources
                                .iter()
                                .map(|s| {
                                    serde_json::to_value(s)
                                        .unwrap()
                                        .as_object()
                                        .unwrap()
                                        .clone()
                                })
                                .collect()
                        })
                        .unwrap_or_default(),
                    provenance: "generated".into(),
                });
            }
        }
        // 読みを三行とも確定できた場合だけ行記録を正本として使う。一行でも未解決なら
        // linesを空にしたまま元の句本文を表示・読みの両方へ返し、不完全な行対応を作らない。
        let surface_text = if lines.len() == 3 {
            crate::haiku_record::verse::surface(&lines)
        } else {
            stripped.into()
        };
        let reading_text = if lines.len() == 3 {
            crate::haiku_record::verse::reading(&lines)
        } else {
            stripped.into()
        };
        let sources:Vec<_>=lines.iter().filter(|l|!l.source_atom_ids.is_empty()).map(|l|json!({"line_index":l.line_index,"text":l.reading_text,"atom_ids":l.source_atom_ids,"sources":l.source_atoms})).collect();
        if !sources.is_empty() {
            mats.insert("line_sources".into(), sources.into());
        }
        self.stage = Stage::Emitted;
        Ok(PreparedEmission {
            text: stripped.into(),
            surface_text: Some(surface_text),
            reading_text: Some(reading_text),
            lines,
            materials: mats,
            interpretation: self.interpretation.clone(),
            preface: Some("ここで一句。".into()),
            biome: normalized(self.event.world.biome.as_deref()),
            structure: normalized(self.event.world.structure.as_deref()),
            time_phase: serde_json::to_value(self.event.world.time_phase)?
                .as_str()
                .map(str::to_owned),
            dimension: self.event.player.dimension.clone(),
            event_sequence: self.event.sequence,
            route: Some("haiku".into()),
        })
    }
}
/// 改行で句を分割する。一行表記の場合だけ、空白でちょうど三語に分かれれば三行として扱う。
/// ここでは音数から境界を推測せず、三行になったかの判断は利用側へ返す。
pub fn split_verse(text: &str) -> Vec<String> {
    let s = strip(text);
    let mut lines: Vec<_> = s
        .split([
            '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
            '\u{2029}',
        ])
        .map(strip)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect();
    if lines.len() == 1 {
        let words: Vec<_> = s
            .split(crate::knowledge::query::space)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect();
        if words.len() == 3 {
            lines = words;
        }
    }
    lines
}
/// 発句の表示表記から読みを得る境界。生成や採否を委ねず、読み文字列または処理エラーを返す。
pub trait Reading {
    fn hiraganize(&mut self, surface: &str) -> impl Future<Output = Result<String>> + Send;
}
// 漢字がある行だけ、既に所有する辞書補助へtokenを問い合わせる。辞書応答が
// tokenを返さなければ原文を使い、emission側のかな検査へ渡す。通信・応答の検査失敗はErrを返す。
impl Reading for crate::python_worker::Helper {
    async fn hiraganize(&mut self, surface: &str) -> Result<String> {
        if !crate::tts_reading::has_kanji(surface) {
            return Ok(surface.into());
        }
        let id = uuid::Uuid::new_v4().to_string();
        let response = self
            .exchange(json!({"op":"tts_tokens","schema_version":1,"request_id":id,"text":surface}))
            .await?;
        Ok(crate::tts_reading::tokens::decode_tokens(response, &id)?
            .as_deref()
            .map(crate::tts_reading::tokens::neutral)
            .unwrap_or_else(|| surface.into()))
    }
}
