//! models.pyの3つのafter-validator。構造化された観測の意味だけを検査する。
use super::{ensure, models::*};
use std::collections::HashSet;

impl HotbarState {
    pub(super) fn validate_semantics(&self) -> Result<(), String> {
        let unique: HashSet<_> = self.slots.iter().map(|slot| slot.slot).collect();
        ensure(
            unique.len() == self.slots.len(),
            "hotbar slot indices must be unique",
        )
    }
}
impl ZombieScentClue {
    pub(super) fn validate_semantics(&self) -> Result<(), String> {
        ensure(
            matches!(
                self.distance_band,
                DistanceBand::Touching | DistanceBand::VeryClose | DistanceBand::Close
            ),
            "zombie scent clue must be within the close distance band",
        )?;
        ensure(
            self.certainty == Certainty::Medium,
            "zombie scent clue certainty must be medium",
        )
    }
}

impl SmellObservation {
    pub(super) fn validate_semantics(&self) -> Result<(), String> {
        use SmellObservationCategory as Category;
        use SmellObservationSmellId as Id;
        use SmellObservationSourceKind as Source;
        use SmellObservationSpecificity as Specificity;
        use SmellObservationStatus as Status;
        use SmellObservationValence as Valence;
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
