//! 受信済みの型と数値範囲に続き、複数fieldの組合せが契約として成立するかを検査する。
//! 対象はhotbarのslot重複、匂いの状態・分類・発生源の対応。
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
/// presentだけが一種類以上を持ち、同じsmell_idを重複させない。
impl SmellObservation {
    pub(super) fn validate_semantics(&self) -> Result<(), String> {
        use SmellObservationStatus as Status;
        // presentと非空配列は必ず対になる。「匂いありだが成分なし」も、
        // 「匂いなし／抑止中なのに成分あり」も、発話処理へ入る前に拒否する。
        ensure(
            (self.status == Status::Present) == !self.components.is_empty(),
            "smell components do not match status",
        )?;
        ensure(
            (self.status == Status::Suppressed) == self.suppression_reason.is_some(),
            "smell suppression reason does not match status",
        )?;
        // 同種の集約はFabricの責務。重複をここで勝手に足す／選ぶと計算結果が変わるため拒否する。
        let mut ids = HashSet::new();
        for component in &self.components {
            ensure(
                ids.insert(component.smell_id),
                "smell component ids must be unique",
            )?;
            // 雨上がりは持続する通常臭へ混ぜず、一度の気づき専用fieldだけで受ける。
            ensure(
                component.smell_id != SmellObservationSmellId::RainAfter,
                "rain-after smell belongs in rain_after_notice",
            )?;
            component.validate_semantics(false)?;
        }
        if let Some(notice) = &self.rain_after_notice {
            ensure(
                self.rain_after_active
                    && self.status != Status::Suppressed
                    && notice.component.smell_id == SmellObservationSmellId::RainAfter,
                "rain-after notice requires an active unsuppressed rain-after source",
            )?;
            notice.component.validate_semantics(true)?;
        }
        Ok(())
    }
}
impl SmellComponent {
    fn validate_semantics(&self, rain_after_active: bool) -> Result<(), String> {
        use SmellObservationCategory as Category;
        use SmellObservationSmellId as Id;
        use SmellObservationSourceKind as Source;
        use SmellObservationValence as Valence;
        let id = self.smell_id;
        let category = self.category;
        let valence = self.valence;
        let source = self.source_kind;
        let item = &[Source::Hotbar, Source::DroppedItem][..];
        let flower = &[Source::Hotbar, Source::Block][..];
        let block = &[Source::Block][..];
        // 成分ごとの許される分類・快不快・発生源を照合する。
        // この表は受信契約の検査であり、到達範囲やマスキングをRustで再計算する処理ではない。
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
                    rain_after_active,
                    "rain-after smell requires an active rain-after window",
                )?;
                // 手持ちの花も雨上がり候補。blockに偽装せず、実際の採取元を受け取る。
                (Category::RainAfter, Valence::Pleasant, flower)
            }
        };
        ensure(
            category == expected.0 && valence == expected.1 && expected.2.contains(&source),
            "smell component has an invalid semantic shape",
        )
    }
}
