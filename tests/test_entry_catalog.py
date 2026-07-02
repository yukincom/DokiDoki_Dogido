from __future__ import annotations

import unittest

from dogido_server.entry_catalog import (
    biome_entries,
    biome_labels,
    block_entry,
    block_labels,
    item_entry,
    item_labels,
    mob_entry,
    neutral_mob_labels,
    passive_mob_labels,
    threat_mob_labels,
)


class EntryCatalogTest(unittest.TestCase):
    def test_biome_entries_flatten_grouped_catalog(self) -> None:
        entries = biome_entries()
        self.assertEqual(len(entries), 65)
        self.assertEqual(entries["taiga"]["label"], "タイガ")
        self.assertEqual(entries["taiga"]["group_id"], "cold")
        self.assertEqual(entries["taiga"]["group_label"], "冷帯バイオーム")
        self.assertEqual(entries["cherry_grove"]["label"], "サクラの林")
        self.assertEqual(
            entries["snowy_plains"]["note"],
            "雪が降ると地上にある全ての葉が徐々に白く変化する。雷雨の場合、霜が降りたような見た目に変化する。",
        )
        self.assertEqual(
            entries["frozen_ocean"]["note"],
            "雪が降ると地上にある全ての葉が徐々に白く変化する。雷雨の場合、霜が降りたような見た目に変化する。",
        )

    def test_biome_labels_are_derived_from_grouped_catalog(self) -> None:
        labels = biome_labels()
        self.assertEqual(labels["nether_wastes"], "ネザーの荒地")
        self.assertEqual(labels["small_end_islands"], "小さなエンド島")

    def test_block_labels_are_loaded_from_split_catalog_directory(self) -> None:
        labels = block_labels()
        self.assertEqual(labels["birch_leaves"], "シラカバの葉")
        self.assertEqual(labels["oak_door"], "オークのドア")
        self.assertEqual(labels["cut_copper_stairs"], "切り込み入りの銅の階段")
        self.assertEqual(labels["gold_block"], "金ブロック")

    def test_item_labels_are_loaded_from_split_top_level_catalogs(self) -> None:
        labels = item_labels()
        self.assertEqual(labels["wooden_pickaxe"], "木のツルハシ")
        self.assertEqual(labels["golden_apple"], "金のリンゴ")
        self.assertEqual(labels["trial_key"], "試練の鍵")

    def test_item_entry_preserves_metadata_fields(self) -> None:
        entry = item_entry("test_block_start")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["label"], "テストブロック")
        self.assertEqual(entry["note"], "中華風の窓のようなデザインのブロック")
        self.assertEqual(entry["section"], "structure_and_test_blocks")
        self.assertEqual(entry["group_path"], [])

    def test_item_entry_keeps_special_notes(self) -> None:
        entry = item_entry("golden_pickaxe")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["label"], "金のツルハシ")
        self.assertEqual(entry["note"], "ピグリンが好きなもの。")

        axe = item_entry("wooden_axe")
        self.assertIsNotNone(axe)
        self.assertEqual(axe["note"], "銅の酸化を還元する。")

        gold_axe = item_entry("golden_axe")
        self.assertIsNotNone(gold_axe)
        self.assertEqual(gold_axe["note"], "銅の酸化を還元する。ピグリンが好きなもの。")

        egg = item_entry("egg")
        self.assertIsNotNone(egg)
        self.assertEqual(egg["note"], "温帯種のニワトリ産。")

        nautilus = item_entry("gold_nautilus_armor")
        self.assertIsNotNone(nautilus)
        self.assertEqual(nautilus["note"], "オウムガイ用の鎧。ピグリンが好きなもの。")

        wolf = item_entry("wolf_armor")
        self.assertIsNotNone(wolf)
        self.assertEqual(wolf["note"], "オオカミ用の鎧。アルマジロのウロコから作成。")

        ancient_debris = item_entry("ancient_debris")
        self.assertIsNotNone(ancient_debris)
        self.assertEqual(
            ancient_debris["note"],
            "ネザーに生成される珍しい鉱石。茶色で、ひび割れている。しかし、非常に高い爆発耐久値を持っており熱に強い。",
        )

        netherite_ingot = item_entry("netherite_ingot")
        self.assertIsNotNone(netherite_ingot)
        self.assertEqual(netherite_ingot["note"], "ネザーで産出する珍しい鉱石。真っ黒。ダイヤモンドより硬い。")

        netherite_helmet = item_entry("netherite_helmet")
        self.assertIsNotNone(netherite_helmet)
        self.assertEqual(
            netherite_helmet["note"],
            "ノックバックを軽減する効果。溶岩では消滅しないが、サボテンで消滅。",
        )

        netherite_pickaxe = item_entry("netherite_pickaxe")
        self.assertIsNotNone(netherite_pickaxe)
        self.assertEqual(
            netherite_pickaxe["note"],
            "ヘッドは黒い。ネザライト製のツルハシ。溶岩では消滅しないが、サボテンで消滅。",
        )

        glow_berries = item_entry("glow_berries")
        self.assertIsNotNone(glow_berries)
        self.assertEqual(
            glow_berries["note"],
            "洞窟のツタになる食用の実。光を放ち、洞窟を照らす。キツネの好物。",
        )

    def test_block_entry_preserves_special_notes(self) -> None:
        entry = block_entry("carved_pumpkin")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["label"], "くり抜かれたカボチャ")
        self.assertEqual(entry["note"], "目のところに穴が空いている。防御力はない。エンダーマン・クリーキングとの敵対を防ぐ。")

        button = block_entry("oak_button")
        self.assertIsNotNone(button)
        self.assertEqual(button["note"], "矢でも起動。")

        pressure_plate = block_entry("oak_pressure_plate")
        self.assertIsNotNone(pressure_plate)
        self.assertEqual(pressure_plate["note"], "アイテムでも「入」状態になる。")

        rail = block_entry("detector_rail")
        self.assertIsNotNone(rail)
        self.assertEqual(rail["note"], "トロッコにのみ反応する感圧装置。金ピカ")

        tripwire = block_entry("tripwire_hook")
        self.assertIsNotNone(tripwire)
        self.assertEqual(tripwire["note"], "糸を踏む・破壊で作動。ハサミで装置解除。2個セットで使用。水に流れる。溶岩流で消滅。")

        target = block_entry("target")
        self.assertIsNotNone(target)
        self.assertEqual(target["note"], "赤と白の縞々模様。投擲物が命中するとレッドストーン信号を発する。")

        trapped_chest = block_entry("trapped_chest")
        self.assertIsNotNone(trapped_chest)
        self.assertEqual(
            trapped_chest["note"],
            "音符ブロックを置くとベース音が鳴る。プレイヤーがチェストを開けたことを検出し、レッドストーン信号を発信。",
        )

        hopper = block_entry("hopper")
        self.assertIsNotNone(hopper)
        self.assertEqual(hopper["note"], "アイテムを吸い込み、送り込む。くわしくはwiki。")

        dispenser = block_entry("dispenser")
        self.assertIsNotNone(dispenser)
        self.assertEqual(dispenser["note"], "アイテムを射出する。アイテムによって作用が異なる。くわしくはwiki。")

        dropper = block_entry("dropper")
        self.assertIsNotNone(dropper)
        self.assertEqual(dropper["note"], "アイテムをドロップする。くわしくはwiki。")

        note_block = block_entry("note_block")
        self.assertIsNotNone(note_block)
        self.assertEqual(
            note_block["note"],
            "寄木細工のおしゃれな箱。台によって楽器が変わる。いろいろ試してみよう！",
        )

        daylight = block_entry("daylight_detector")
        self.assertIsNotNone(daylight)
        self.assertEqual(daylight["note"], "日光の照度に応じてレッドストーン信号を発する。")

        comparator = block_entry("redstone_comparator")
        self.assertIsNotNone(comparator)
        self.assertEqual(
            comparator["note"],
            "レッドストーン信号を比較、減算する。矢印が向いている方が前。前のトーチが消灯：比較モード。前のトーチが点灯：減算モード。容器の中身の量を信号化する。",
        )

        repeater = block_entry("redstone_repeater")
        self.assertIsNotNone(repeater)
        self.assertEqual(
            repeater["note"],
            "後ろから前にのみ信号を伝達。横からの信号で変更可能。詳細はwikiで確認。",
        )

        redstone_lamp = block_entry("redstone_lamp")
        self.assertIsNotNone(redstone_lamp)
        self.assertEqual(redstone_lamp["note"], "レッドストーン信号が通っているかを目で確認できるブロック。光ると綺麗。")

        rail = block_entry("rail")
        self.assertIsNotNone(rail)
        self.assertEqual(rail["note"], "トロッコの通り道。")

        powered_rail = block_entry("powered_rail")
        self.assertIsNotNone(powered_rail)
        self.assertEqual(powered_rail["note"], "トロッコの速度を増減させるレール。金ピカ。起動中は赤く光る")

        activator_rail = block_entry("activator_rail")
        self.assertIsNotNone(activator_rail)
        self.assertEqual(activator_rail["note"], "一部のトロッコに対して特定のアクションを起こすレール。くわしくはwiki。")

        lightning = block_entry("lightning_rod")
        self.assertIsNotNone(lightning)
        self.assertEqual(lightning["note"], "銅の酸化を還元する。落雷で炎発生。")

        sculk = block_entry("sculk_sensor")
        self.assertIsNotNone(sculk)
        self.assertEqual(
            sculk["note"],
            "何やらうねうねとした緑色の触手のようなものがついている。近くで動きがあるとコロロ〜と音を出す。",
        )

        glowstone = block_entry("glowstone")
        self.assertIsNotNone(glowstone)
        self.assertEqual(glowstone["note"], "眩しい光を放つ、ミステリアスな石。ネザーで産出")

        glow_lichen = block_entry("glow_lichen")
        self.assertIsNotNone(glow_lichen)
        self.assertEqual(glow_lichen["note"], "洞窟や渓谷に生える微かな光を放つ苔")

        piston = block_entry("piston")
        self.assertIsNotNone(piston)
        self.assertEqual(piston["note"], "レッドストーン信号を受信するとブロックやエンティティを押し出す。")

        sticky_piston = block_entry("sticky_piston")
        self.assertIsNotNone(sticky_piston)
        self.assertEqual(sticky_piston["note"], "ピストンに似ているが、押すのは勿論、引くこともできる。")

        bell = block_entry("bell")
        self.assertIsNotNone(bell)
        self.assertEqual(bell["note"], "金色。ピグリンが好きなもの。村の集会所。鳴らすと近くの襲撃者を発光させる。")

        crafter = block_entry("crafter")
        self.assertIsNotNone(crafter)
        self.assertEqual(
            crafter["note"],
            "作業台を鉄で加工した物。レッドストーン信号で自動クラフトする。くわしくはwiki。",
        )

        crimson = block_entry("crimson_sign")
        self.assertIsNotNone(crimson)
        self.assertEqual(crimson["note"], "巨大キノコの一部。ネザー。")

        amethyst_block = block_entry("amethyst_block")
        self.assertIsNotNone(amethyst_block)
        self.assertEqual(
            amethyst_block["note"],
            "紫色の綺麗なブロック。スカルクセンサーの信号を伝えることができる。",
        )

        amethyst_bud = block_entry("small_amethyst_bud")
        self.assertIsNotNone(amethyst_bud)
        self.assertEqual(amethyst_bud["note"], "わずかだが光を放つ。キラキラしている。")

        ancient_debris = block_entry("ancient_debris")
        self.assertIsNotNone(ancient_debris)
        self.assertEqual(
            ancient_debris["note"],
            "ネザーに生成される珍しい鉱石。茶色で、ひび割れている。しかし、非常に高い爆発耐久値を持っており熱に強い。",
        )

        azalea = block_entry("azalea")
        self.assertIsNotNone(azalea)
        self.assertEqual(azalea["note"], "ピンク色の可愛らしい花が咲く。")

        bamboo_shoot = block_entry("bamboo_shoot")
        self.assertIsNotNone(bamboo_shoot)
        self.assertEqual(bamboo_shoot["note"], "プレイヤーは食べることができない。")

        anvil = block_entry("anvil")
        self.assertIsNotNone(anvil)
        self.assertEqual(anvil["note"], "アイテムの修理と名称変更ができる。上から落ちてきたらかなり痛い。")

        barrel = block_entry("barrel")
        self.assertIsNotNone(barrel)
        self.assertEqual(barrel["note"], "アイテムを保管できる。重ねることができる。")

        beacon = block_entry("beacon")
        self.assertIsNotNone(beacon)
        self.assertEqual(
            beacon["note"],
            "光線を空に投射し近隣のプレイヤーにバフを与える。かなり光る。ピラミットパワー。詳しくはwiki。",
        )

        bee_nest = block_entry("bee_nest")
        self.assertIsNotNone(bee_nest)
        self.assertEqual(bee_nest["note"], "蜂たちの家。焚き火があれば、収穫時にミツバチが敵対しない。")

        beehive = block_entry("beehive")
        self.assertIsNotNone(beehive)
        self.assertEqual(beehive["note"], "焚き火があれば、収穫時にミツバチが敵対しない。")

        big_dripleaf = block_entry("big_dripleaf")
        self.assertIsNotNone(big_dripleaf)
        self.assertEqual(big_dripleaf["note"], "繁茂した洞窟。少しの間だけ、上に乗ることができる大きな葉っぱ。")

        blast_furnace = block_entry("blast_furnace")
        self.assertIsNotNone(blast_furnace)
        self.assertEqual(blast_furnace["note"], "鉱石と製錬可能な防具や道具の製錬に使用。")

        chest = block_entry("chest")
        self.assertIsNotNone(chest)
        self.assertEqual(chest["note"], "アイテムやその他のブロックを保管できる。ふたつ並べるとラージチェストになる。")

        shulker_box = block_entry("shulker_box")
        self.assertIsNotNone(shulker_box)
        self.assertEqual(
            shulker_box["note"],
            "保管ブロックの一種。他の保管ブロックと違い、壊しても中身をそのまま保持可能。",
        )

        bookshelf = block_entry("bookshelf")
        self.assertIsNotNone(bookshelf)
        self.assertEqual(bookshelf["note"], "エンチャントテーブルを強化。")

        chiseled_bookshelf = block_entry("chiseled_bookshelf")
        self.assertIsNotNone(chiseled_bookshelf)
        self.assertEqual(
            chiseled_bookshelf["note"],
            "本、本と羽根ペン、記入済みの本、エンチャントの本、知恵の本を収納することができる。",
        )

        brewing_stand = block_entry("brewing_stand")
        self.assertIsNotNone(brewing_stand)
        self.assertEqual(brewing_stand["note"], "ポーション類を醸造する道具。教会にもある")

        brown_mushroom = block_entry("brown_mushroom")
        self.assertIsNotNone(brown_mushroom)
        self.assertEqual(brown_mushroom["note"], "わずかに光る。")

        bubble_column = block_entry("bubble_column")
        self.assertIsNotNone(bubble_column)
        self.assertEqual(
            bubble_column["note"],
            "水中に発生する泡。流れがあり、ものを沈めたり持ち上げる。空気を補充できる。",
        )

        cactus = block_entry("cactus")
        self.assertIsNotNone(cactus)
        self.assertEqual(cactus["note"], "触れると痛い。プレイヤーと同じくらいの大きさ。太くて逞しい緑色の幹。")

        campfire = block_entry("campfire")
        self.assertIsNotNone(campfire)
        self.assertEqual(campfire["note"], "もくもくと煙を上げる焚き火。")

        soul_campfire = block_entry("soul_campfire")
        self.assertIsNotNone(soul_campfire)
        self.assertEqual(soul_campfire["note"], "もくもくと煙を上げる青い炎の焚き火。")

        cherry_leaves = block_entry("cherry_leaves")
        self.assertIsNotNone(cherry_leaves)
        self.assertEqual(cherry_leaves["note"], "ピンク色の花が咲いている。花びらが舞う。")

        deepslate = block_entry("deepslate")
        self.assertIsNotNone(deepslate)
        self.assertEqual(deepslate["note"], "通常の石の2倍、硬い。黒い岩。")

        chiseled_nether_bricks = block_entry("chiseled_nether_bricks")
        self.assertIsNotNone(chiseled_nether_bricks)
        self.assertEqual(
            chiseled_nether_bricks["note"],
            "不燃性でガストの火の玉でも破壊されない。ウィザースケルトンの模様が刻まれている。",
        )

        chiseled_polished_blackstone = block_entry("chiseled_polished_blackstone")
        self.assertIsNotNone(chiseled_polished_blackstone)
        self.assertEqual(chiseled_polished_blackstone["note"], "ピグリンの鼻のような模様が彫られている。")

        quartz_block = block_entry("block_of_quartz")
        self.assertIsNotNone(quartz_block)
        self.assertEqual(quartz_block["note"], "ネザークォーツから作られる白いブロック。爆発に弱い。")

        chiseled_quartz_block = block_entry("chiseled_quartz_block")
        self.assertIsNotNone(chiseled_quartz_block)
        self.assertEqual(chiseled_quartz_block["note"], "コンジットとオウムガイの模様が描かれている。")

        chiseled_red_sandstone = block_entry("chiseled_red_sandstone")
        self.assertIsNotNone(chiseled_red_sandstone)
        self.assertEqual(chiseled_red_sandstone["note"], "ウィザーのような模様が描かれている。")

        chiseled_resin_bricks = block_entry("chiseled_resin_bricks")
        self.assertIsNotNone(chiseled_resin_bricks)
        self.assertEqual(chiseled_resin_bricks["note"], "クリーキングのような模様が描かれている。")

        chiseled_sandstone = block_entry("chiseled_sandstone")
        self.assertIsNotNone(chiseled_sandstone)
        self.assertEqual(chiseled_sandstone["note"], "クリーパーのような模様が描かれている。")

        chiseled_stone_bricks = block_entry("chiseled_stone_bricks")
        self.assertIsNotNone(chiseled_stone_bricks)
        self.assertEqual(chiseled_stone_bricks["note"], "マトのような模様が描かれている。")

        chiseled_tuff = block_entry("chiseled_tuff")
        self.assertIsNotNone(chiseled_tuff)
        self.assertEqual(chiseled_tuff["note"], "ラーメンどんぶりのような模様が描かれている。")

        chiseled_tuff_bricks = block_entry("chiseled_tuff_bricks")
        self.assertIsNotNone(chiseled_tuff_bricks)
        self.assertEqual(chiseled_tuff_bricks["note"], "開いたジッパーのような模様が描かれている。")

        ochre_froglight = block_entry("ochre_froglight")
        self.assertIsNotNone(ochre_froglight)
        self.assertEqual(
            ochre_froglight["note"],
            "オレンジ色のカエルが小さいマグマキューブを捕食したときにドロップする。優しい光を放つ天然の光源。温帯種のカエルがドロップする。",
        )

    def test_mob_entry_preserves_runtime_notes(self) -> None:
        copper_golem = mob_entry("copper_golem")
        self.assertIsNotNone(copper_golem)
        self.assertEqual(copper_golem["note"], "銅のチェスト内のアイテムを仕分ける。")

        frog = mob_entry("frog")
        self.assertIsNotNone(frog)
        self.assertEqual(
            frog["note"],
            "温帯種オレンジ色、熱帯種白色、冷帯種緑色。ドロップリーフの上で遊ぶ。スライムボールが好物。",
        )

    def test_neutral_and_passive_mob_labels_are_available_for_ambient_use(self) -> None:
        neutral = neutral_mob_labels()
        ambient = passive_mob_labels()
        self.assertEqual(neutral["enderman"], "エンダーマン")
        self.assertEqual(ambient["enderman"], "エンダーマン")
        self.assertEqual(ambient["sheep"], "ヒツジ")

    def test_threat_mob_labels_include_hostile_and_neutral_threats(self) -> None:
        labels = threat_mob_labels()
        self.assertEqual(labels["creeper"], "クリーパー")
        self.assertEqual(labels["spider"], "スパイダー")
        self.assertEqual(labels["drowned"], "ドラウンド")


if __name__ == "__main__":
    unittest.main()
