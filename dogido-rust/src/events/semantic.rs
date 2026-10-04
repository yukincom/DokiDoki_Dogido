//! 受信済みの型と数値範囲に続き、複数fieldの組合せが契約として成立するかを検査する。
//! 対象はhotbarのslot重複、匂いの方向の存在、匂いの状態・分類・発生源の対応。
//! modelsのValidateから呼ばれ、成功なら観測をそのまま通し、不整合なら理由を返す。
//! ゲーム座標や実在性の照合、観測の補完、戦闘・発話・保存の判断は各消費側の責務。
use super::{ensure, models::*};
use std::collections::HashSet;

/// 同じslotに複数のitem候補が入る曖昧なsnapshotを拒否する。
/// 番号の範囲と総枠数はmodels側で検査済みで、ここでは一意性だけを調べる。
impl HotbarState {
    pub(super) fn validate_semantics(&self) -> Result<(), String> {
        let unique: HashSet<_> = self.slots.iter().map(|slot| slot.slot).collect();
        ensure(
            unique.len() == self.slots.len(),
            "hotbar slot indices must be unique",
        )
    }
}
/// 方位または上下が少なくとも一つある方向観測だけを通す。
/// 空objectは方向を推定できたことにならないため、情報なしは親fieldのNoneで表す。
impl SmellDirectionEstimate {
    pub(super) fn validate_semantics(&self) -> Result<(), String> {
        ensure(
            self.cardinal.is_some() || self.vertical.is_some(),
            "smell direction estimate requires a direction",
        )
    }
}

/// 匂いのstatusに応じて必要fieldを揃え、特定度ごとのID・分類・快不快・発生源を照合する。
/// presentの整合性を確認できた後だけunwrapし、none/suppressedは理由の有無を検査して早期に返す。
impl SmellObservation {
    pub(super) fn validate_semantics(&self) -> Result<(), String> {
        use SmellObservationCategory as Category;
        use SmellObservationSmellId as Id;
        use SmellObservationSourceKind as Source;
        use SmellObservationSpecificity as Specificity;
        use SmellObservationStatus as Status;
        use SmellObservationValence as Valence;
        // 方向を語れるのは解決済みの単独外部源だけ。手持ち・バイオーム・雨上がりは方向へ投影しない。
        if self.direction_estimate.is_some() {
            ensure(
                self.status == Status::Present
                    && self.specificity == Some(Specificity::Source)
                    && matches!(
                        self.source_kind,
                        Some(Source::Block | Source::Entity | Source::DroppedItem)
                    )
                    && self.smell_id != Some(Id::RainAfter),
                "smell spatial estimate requires a resolved external source",
            )?;
        }
        let resolved = [
            self.smell_id.is_some(),
            self.category.is_some(),
            self.valence.is_some(),
            self.source_kind.is_some(),
            self.specificity.is_some(),
            self.effective_strength.is_some(),
        ];
        if self.status != Status::Present {
            ensure(
                !resolved.iter().any(|v| *v),
                "non-present smell observation cannot carry a resolved source",
            )?;
            return ensure(
                match self.status {
                    Status::Suppressed => self.suppression_reason.is_some(),
                    Status::None => self.suppression_reason.is_none(),
                    Status::Present => unreachable!(),
                },
                "smell suppression reason does not match status",
            );
        }
        ensure(
            resolved.iter().all(|v| *v),
            "present smell observation requires resolved smell fields",
        )?;
        ensure(
            self.suppression_reason.is_none(),
            "present smell observation cannot be suppressed",
        )?;
        let id = self.smell_id.unwrap();
        let category = self.category.unwrap();
        let valence = self.valence.unwrap();
        let source = self.source_kind.unwrap();
        // 同率候補はmixed/categoryの親形、単独候補はsourceの具体形として届く。
        // SmellPolicyの出力形を検査し、欠けた属性をRust側の推測で埋めない。
        match self.specificity.unwrap() {
            Specificity::Mixed => ensure(
                id == Id::Mixed
                    && category == Category::Mixed
                    && valence == Valence::Mixed
                    && source == Source::Mixed,
                "mixed smell observation requires the mixed parent shape",
            ),
            Specificity::Category => {
                let valid = match (id, category) {
                    (Id::Decay, Category::Decay) => valence == Valence::Unpleasant,
                    (Id::Food, Category::Food) | (Id::Flower, Category::Flower) => true,
                    _ => false,
                };
                ensure(
                    valid && source == Source::Mixed,
                    "category smell observation has an invalid semantic shape",
                )
            }
            Specificity::Source => {
                let item = &[Source::Hotbar, Source::DroppedItem][..];
                let flower = &[Source::Hotbar, Source::Block][..];
                let block = &[Source::Block][..];
                let expected = match id {
                    Id::Zombie => (Category::Decay, Valence::Unpleasant, &[Source::Entity][..]),
                    Id::RottenFlesh => (Category::Decay, Valence::Unpleasant, item),
                    Id::Composter => (Category::Compost, Valence::Unpleasant, block),
                    Id::BrewingStand => (Category::Brewing, Valence::Mixed, block),
                    Id::Swamp => (Category::Swamp, Valence::Mixed, &[Source::Biome][..]),
                    Id::RawMeat | Id::RawFish => (Category::Food, Valence::Unpleasant, item),
                    Id::CookedMeat | Id::CookedFish | Id::Soup | Id::Cookie | Id::Bread => {
                        (Category::Food, Valence::Pleasant, item)
                    }
                    Id::CookingMeat | Id::CookingFish => (Category::Food, Valence::Pleasant, block),
                    Id::Cake => (
                        Category::Food,
                        Valence::Pleasant,
                        &[Source::Hotbar, Source::DroppedItem, Source::Block][..],
                    ),
                    Id::InkSac => (Category::Ink, Valence::Unpleasant, &[Source::Hotbar][..]),
                    Id::LilyOfTheValley
                    | Id::Lilac
                    | Id::Peony
                    | Id::RoseBush
                    | Id::CactusFlower
                    | Id::FloweringAzalea => (Category::Flower, Valence::Pleasant, flower),
                    Id::WitherRose => (Category::Flower, Valence::Mixed, flower),
                    Id::Allium | Id::PitcherPlant | Id::Torchflower | Id::OpenEyeblossom => {
                        (Category::Flower, Valence::Unpleasant, flower)
                    }
                    Id::RainAfter => {
                        ensure(
                            self.rain_after_active,
                            "rain-after smell requires an active rain-after window",
                        )?;
                        (Category::RainAfter, Valence::Pleasant, block)
                    }
                    _ => return Err("source smell observation requires a concrete smell id".into()),
                };
                ensure(
                    category == expected.0 && valence == expected.1 && expected.2.contains(&source),
                    "source smell observation has an invalid semantic shape",
                )
            }
        }
    }
}

impl PassiveMobTemperament {
    pub(crate) fn as_str(self) -> &'static str {
        match self { Self::Passive => "passive", Self::Neutral => "neutral" }
    }
}
