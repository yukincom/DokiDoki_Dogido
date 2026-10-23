//! 乗れる動物のsnapshotとモデルの選択を照合する純粋な契約。
//! 名前の登録・表記ゆれの照合・操作実行は行わず、UUIDはコードの候補表から取り出す。
use anyhow::{Result, ensure};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Candidate {
    pub entity_id: String,
    pub entity_type: MountKind,
    pub custom_name: Option<String>,
    pub tamed: bool,
    pub owner_uuid: Option<String>,
    pub adult: bool,
    pub equipped: bool,
    pub alive: bool,
    pub dimension: String,
    pub has_passengers: bool,
    pub has_vehicle: bool,
    pub leashed: bool,
}

/// Minecraft 1.21.11の乗れる動物だけ。通常のハスクや敵ガストを含めない。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MountKind {
    Horse,
    Donkey,
    Mule,
    SkeletonHorse,
    ZombieHorse,
    Camel,
    CamelHusk,
    Pig,
    Strider,
    HappyGhast,
    Nautilus,
    ZombieNautilus,
    Llama,
    TraderLlama,
}
impl MountKind {
    /// 実行できる種類の正本。依頼分類はこの範囲外の動物も受け、個体選択時に可否を伝える。
    pub const ALL: [Self; 14] = [
        Self::Horse,
        Self::Donkey,
        Self::Mule,
        Self::SkeletonHorse,
        Self::ZombieHorse,
        Self::Camel,
        Self::CamelHusk,
        Self::Pig,
        Self::Strider,
        Self::HappyGhast,
        Self::Nautilus,
        Self::ZombieNautilus,
        Self::Llama,
        Self::TraderLlama,
    ];
    /// バニラで所有者が付かない種類に限り、名札と乗用装備を呼び戻し資格にする。
    /// 所有者が実際に記録されている個体では、この条件で他人の所有を迂回しない。
    fn permits_named_equipment(self) -> bool {
        matches!(
            self,
            Self::SkeletonHorse
                | Self::Camel
                | Self::CamelHusk
                | Self::Pig
                | Self::Strider
                | Self::HappyGhast
        )
    }
    fn requires_taming(self) -> bool {
        !matches!(self, Self::Pig | Self::Strider | Self::HappyGhast)
    }
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
        ensure!(self.schema_version == 2, "horse_schema_version");
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
                horse
                    .owner_uuid
                    .as_ref()
                    .is_none_or(|id| uuid::Uuid::parse_str(id).is_ok()),
                "horse_owner_uuid"
            );
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
        self.unavailable_reason(horse).is_none()
    }
    /// 候補が呼べない根拠も同じ検査から返し、モデルが乗員や死亡などを補作しないようにする。
    pub fn unavailable_reason(&self, horse: &Candidate) -> Option<&'static str> {
        let authorized = match &horse.owner_uuid {
            Some(owner) => {
                horse.entity_type.requires_taming() && owner == &self.player_uuid && horse.tamed
            }
            None => {
                horse.entity_type.permits_named_equipment()
                    && horse.custom_name.is_some()
                    && horse.equipped
                    && (!horse.entity_type.requires_taming() || horse.tamed)
            }
        };
        if self.status != Availability::Available {
            Some("一覧を利用できない")
        } else if !authorized {
            // 不成立をまとめて渡すとモデルが原因を選んでしまうため、観測した条件を一件返す。
            Some(
                if horse
                    .owner_uuid
                    .as_ref()
                    .is_some_and(|owner| owner != &self.player_uuid)
                {
                    "所有者が本人ではない"
                } else if horse.entity_type.requires_taming() && !horse.tamed {
                    "飼い慣らされていない"
                } else if horse.owner_uuid.is_none() && horse.entity_type.permits_named_equipment()
                {
                    if horse.custom_name.is_none() {
                        "名札が付いていない"
                    } else if horse.entity_type == MountKind::HappyGhast {
                        "ハーネスを装備していない"
                    } else {
                        "鞍を装備していない"
                    }
                } else {
                    "本人の所有個体と確認できない"
                },
            )
        } else if !horse.alive {
            Some("生存を確認できない")
        } else if !horse.adult {
            Some("まだ成体ではない")
        } else if horse.dimension != self.dimension {
            Some("同じ次元にいない")
        } else if horse.has_passengers {
            Some("その個体に乗員がいる")
        } else if horse.has_vehicle {
            Some("その個体が別の乗り物に乗っている")
        } else if horse.leashed {
            Some("リードでつながれているか、他個体を牽引している")
        } else {
            None
        }
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
    /// BERTへ変更済み。旧判定へ戻さない。
    /// 分類結果と原文を束ね、個体選択後に同じ依頼の意味を語句で再判定しない。
    pub fn from_classified(
        snapshot: Snapshot,
        classification: &crate::request_classifier::Classification,
    ) -> Result<Self> {
        ensure!(
            classification.prediction.label == crate::request_classifier::Label::RecallMount,
            "horse_request_not_classified"
        );
        snapshot.validate()?;
        ensure!(!classification.text.trim().is_empty(), "horse_empty_input");
        let mut candidates = snapshot.candidates.clone();
        candidates.sort_by(|a, b| a.entity_id.cmp(&b.entity_id));
        Ok(Self {
            snapshot,
            original: classification.text.clone(),
            candidates,
        })
    }
    /// 形式と原文根拠を検査し、対象を最初のsnapshotへ結ぶ。
    /// 呼びかけの意味と表記ゆれはLLMが選び、名前や肯定操作語の完全一致を入口条件にしない。
    pub fn resolve(&self, choice: &Choice) -> Result<&Candidate> {
        validate_original_evidence(&self.original, &choice.evidence, choice.confidence)?;
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

/// confidenceは個体選択の確かさ。BERTの依頼確率とは別で、既存の選択条件を維持する。
fn validate_original_evidence(original: &str, evidence: &str, confidence: f64) -> Result<()> {
    ensure!(
        confidence.is_finite() && (0.9..=1.0).contains(&confidence),
        "horse_low_confidence"
    );
    ensure!(
        evidence == original.trim(),
        "horse_original_evidence_required"
    );
    Ok(())
}

#[cfg(test)]
mod tests;
