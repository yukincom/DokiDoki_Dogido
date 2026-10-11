//! 所有馬snapshotとモデルの選択を照合する純粋な契約。
//! 名前の登録・表記ゆれの照合・操作実行は行わず、UUIDはコードの候補表から取り出す。
use anyhow::{Result, ensure};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Candidate {
    pub entity_id: String,
    pub custom_name: Option<String>,
    pub tamed: bool,
    pub owned_by_player: bool,
    pub alive: bool,
    pub dimension: String,
    pub has_passengers: bool,
    pub leashed: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Availability {
    Available,
    Unavailable,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub schema_version: u32,
    pub status: Availability,
    /// 同じ次元名でも別の接続・ワールドへ前の命令を持ち込まないための世代UUID。
    pub world_id: String,
    pub revision: u64,
    pub observed_at: DateTime<Utc>,
    pub player_uuid: String,
    pub dimension: String,
    #[serde(default)]
    pub recent_mount_id: Option<String>,
    pub candidates: Vec<Candidate>,
}
impl Snapshot {
    pub fn validate(&self) -> Result<()> {
        ensure!(self.schema_version == 1, "horse_schema_version");
        ensure!(self.revision > 0, "horse_revision");
        ensure!(
            uuid::Uuid::parse_str(&self.world_id).is_ok(),
            "horse_world_id"
        );
        ensure!(
            uuid::Uuid::parse_str(&self.player_uuid).is_ok(),
            "horse_player_uuid"
        );
        ensure!(
            !self.dimension.is_empty() && self.dimension.len() <= 128,
            "horse_dimension"
        );
        ensure!(self.candidates.len() <= 32, "horse_candidate_limit");
        ensure!(
            self.status == Availability::Available || self.candidates.is_empty(),
            "horse_unavailable_candidates"
        );
        let mut ids = BTreeSet::new();
        for horse in &self.candidates {
            ensure!(
                uuid::Uuid::parse_str(&horse.entity_id).is_ok() && ids.insert(&horse.entity_id),
                "horse_candidate_id"
            );
            ensure!(
                horse
                    .custom_name
                    .as_ref()
                    .is_none_or(|s| !s.trim().is_empty() && s.chars().count() <= 128),
                "horse_name"
            );
            ensure!(
                !horse.dimension.is_empty() && horse.dimension.len() <= 128,
                "horse_candidate_dimension"
            );
        }
        ensure!(
            self.recent_mount_id
                .as_ref()
                .is_none_or(|s| uuid::Uuid::parse_str(s).is_ok()),
            "horse_recent_mount_id"
        );
        Ok(())
    }
    /// ミリ秒単位の鮮度。将来時刻の許容は時計差1秒まで、過去は3秒まで。
    pub fn fresh(&self, now: DateTime<Utc>) -> bool {
        let age = (now - self.observed_at).num_milliseconds();
        (-1_000..=3_000).contains(&age)
    }
    pub fn eligible(&self, horse: &Candidate) -> bool {
        self.status == Availability::Available
            && horse.tamed
            && horse.owned_by_player
            && horse.alive
            && horse.dimension == self.dimension
            && !horse.has_passengers
            && !horse.leashed
    }
}

/// モデルへは短いIDを渡す。UUIDと名前は生成開始時の表で固定し、返却された名前を探し直さない。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Prepared {
    pub snapshot: Snapshot,
    pub original: String,
    pub candidates: Vec<Candidate>,
}
impl Prepared {
    pub fn new(snapshot: Snapshot, original: &str) -> Result<Self> {
        snapshot.validate()?;
        ensure!(!original.trim().is_empty(), "horse_empty_input");
        let mut candidates = snapshot.candidates.clone();
        candidates.sort_by(|a, b| a.entity_id.cmp(&b.entity_id));
        Ok(Self {
            snapshot,
            original: original.to_owned(),
            candidates,
        })
    }
    /// 形式と原文根拠を検査し、対象を最初のsnapshotへ結ぶ。
    /// 呼びかけの意味と表記ゆれはLLMが選び、名前や肯定操作語の完全一致を入口条件にしない。
    pub fn resolve(&self, choice: &Choice) -> Result<&Candidate> {
        validate_evidence(&self.original, &choice.evidence, choice.confidence)?;
        // 根拠の部分抜き出しで否定を落とさせない。明白な禁止だけを最後に拒否する。
        // 「呼んでくれる？」のような依頼は、疑問符だけを理由に棄却しない。
        let i = choice
            .candidate_id
            .strip_prefix('h')
            .and_then(|s| s.parse::<usize>().ok());
        let horse = i
            .filter(|i| *i > 0)
            .and_then(|i| self.candidates.get(i - 1))
            .ok_or_else(|| anyhow::anyhow!("horse_unknown_candidate"))?;
        ensure!(
            choice.candidate_id == format!("h{}", i.unwrap()),
            "horse_candidate_format"
        );
        ensure!(self.snapshot.eligible(horse), "horse_not_eligible");
        Ok(horse)
    }
    /// 毎snapshotでrevisionが増えても同じ馬なら使える。改名・乗車・別ワールド等は再依頼が必要。
    pub fn revalidate<'a>(
        &self,
        choice: &Choice,
        latest: &'a Snapshot,
        now: DateTime<Utc>,
    ) -> Result<&'a Candidate> {
        let selected = self.resolve(choice)?;
        latest.validate()?;
        ensure!(latest.fresh(now), "horse_stale_snapshot");
        ensure!(
            latest.world_id == self.snapshot.world_id
                && latest.player_uuid == self.snapshot.player_uuid
                && latest.dimension == self.snapshot.dimension
                && latest.revision >= self.snapshot.revision,
            "horse_snapshot_changed"
        );
        let horse = latest
            .candidates
            .iter()
            .find(|h| h.entity_id == selected.entity_id)
            .ok_or_else(|| anyhow::anyhow!("horse_no_longer_loaded"))?;
        ensure!(
            latest.eligible(horse) && horse == selected,
            "horse_target_changed"
        );
        Ok(horse)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Choice {
    pub candidate_id: String,
    pub evidence: String,
    pub confidence: f64,
}

pub fn validate_evidence(original: &str, evidence: &str, confidence: f64) -> Result<()> {
    ensure!(
        confidence.is_finite() && (0.9..=1.0).contains(&confidence),
        "horse_low_confidence"
    );
    ensure!(
        evidence == original.trim(),
        "horse_original_evidence_required"
    );
    ensure!(safe_evidence(evidence), "horse_contradictory_evidence");
    Ok(())
}

fn safe_evidence(text: &str) -> bool {
    let s = text.trim_end_matches(['。', '！', '!']);
    if [('「', '」'), ('『', '』'), ('"', '"')]
        .iter()
        .any(|(a, b)| s.starts_with(*a) && s.ends_with(*b))
    {
        return false;
    }
    ![
        "呼ばないで",
        "呼ばんといて",
        "呼ぶな",
        "呼ばなくていい",
        "呼ばなくてよい",
        "呼ぶ必要はない",
        "連れてこないで",
        "連れてこんといて",
        "連れてくるな",
        "移動させないで",
        "呼んでほしくない",
        "という意味ではない",
        "という意味じゃない",
        "とは言ってない",
        "って言ってない",
    ]
    .iter()
    .any(|v| text.contains(v))
}

#[cfg(test)]
mod tests;
