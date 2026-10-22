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

// TODO(request-classifier): recall_mount を依頼認識の正にしたら、この否定・引用の
// 部分一致による再判定と validate_evidence からの呼出しを撤去する。
// 原文との結び付け、選択結果の受理条件、候補・所有・鮮度・実行前検証は別途維持する。
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
