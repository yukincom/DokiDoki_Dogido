# スメルバトル仕様

状態: **2026-09-11 コード・Python/Java自動テスト済み。実Minecraft・実Qwen・実TTSは未確認。**

Fabric adapter が実際の近接源、hotbar、バイオーム、天候をコードで照合し、
その時点で優勢な匂いを一件の `smell_observation` に解決する。サーバーやLLMに
正確な位置・距離・個数を渡して「何が臭うか」を推測させない。

## 1. 計算

各候補は伝播力 `P` とプレイヤーからの3次元距離 `d` を持つ。hotbarと現在バイオームは
`d=0` とする。温度補正と雨上がり補正を足した伝播力を `P_eff` とし、

```text
P_eff = clamp(P + temperature_modifier + rain_after_modifier, 1, 12)
candidate iff d <= P_eff
distance_cost = max(0, ceil(d) - 1)
score = P_eff - distance_cost
```

で比較する。壁による減衰は行わない。同じ `smell_id` が複数あっても合算せず、
最も強い一件だけを残す。同点が同じ上位分類なら `花の匂い` / `食べ物の匂い` のように
分類までぼかし、快・不快が混ざれば valence も `mixed` にする。別分類の同点は
`いろんな匂いが混じって分からない` に固定する。

腐った肉とゾンビは同じ `decay` 分類でともに `P=8`。hotbarの腐った肉は距離0なので
通常は実ゾンビを覆い隠し、接触距離で同点でも `腐った匂い` までしか特定しない。

## 2. 温度・天候

Minecraftのtemperatureは摂氏ではないため、`temperature * 20` をこの機能だけの
作業上の目安として次の閉じた補正へ写す。バニラの位置依存 `isCold` が真なら、
高所補正を落とさないよう少なくとも0〜5℃帯相当へ寄せる。

| Minecraft temperature | 作業上の目安 | 補正 |
|---:|---:|---:|
| `>= 1.25` | 25℃以上 | `+2` |
| `0.75 .. <1.25` | 15℃以上25℃未満 | `0` |
| `>0.25 .. <0.75` | 5℃超15℃未満 | `-3` |
| `>-0.25 .. <=0.25` | -5℃超5℃以下 | `-5` |
| `<= -0.25` | -5℃以下 | `-7` |

冷えても、すでに有効な匂い源は `P_eff=1` を下限として、hotbar／同じ位置／隣接だけは
感じられる。火のついたcampfire／soul campfireで実際に焼かれている肉・魚だけは負の温度補正を
受けない。持っている調理済み食品は熱いと断定せず、通常補正を受ける。

- 現在地で雨・雪・雷が有効な間と、水中ではすべて `suppressed`
- 晴天の寒冷・積雪環境はhard offにせず、上の温度減衰を使う
- 現在地の雨または雨を伴う雷から晴れへ変わると180秒だけ雨上がり状態。雪から晴れは対象外
- 雨上がり中は植物・土系が `+1`。通常無臭の草・葉・土も `P=1` で候補にし、勝者になった植物・土は個別名でなく `雨上がりの匂い` に統合する
- 再び降水したときとdimension変更時に雨上がり状態を解除する。水中ではタイマーだけ進む

## 3. 対象

| 匂い | 実レジストリ／実状態 | P | 候補になる場所 |
|---|---|---:|---|
| ゾンビ | `zombie / zombie_villager / husk / drowned` のうち未視認・未聴取。基礎8、暖地では最大10ブロック | 8 | entity |
| 腐った肉 | `rotten_flesh` | 8 | hotbar・落下item |
| コンポスター | `composter` の `level > 0`。空は無臭 | 5 | block |
| 醸造台 | `brewing_stand` の瓶slotが一つ以上埋まっている | 5 | block |
| 沼 | `swamp / mangrove_swamp` | 3 | 現在バイオーム |
| 生肉 | `beef / chicken / mutton / porkchop / rabbit` | 3 | hotbar・落下item |
| 生魚 | `cod / salmon / tropical_fish / pufferfish` | 3 | hotbar・落下item |
| 焼いた肉・魚 | `cooked_beef` 等の実ID | 3 | hotbar・落下item |
| スープ類 | `mushroom_stew / rabbit_stew / beetroot_soup / suspicious_stew` | 3 | hotbar・落下item |
| 菓子・パン | `cookie / cake / bread` | 3 | hotbar・落下item。設置cake／candle cakeも対象 |
| イカ墨 | `ink_sac` | 3 | hotbarだけ |
| 焼いている肉・魚 | lit `campfire / soul_campfire` の実調理slot | 3 | block。寒さを無視 |

hotbarは選択中だけでなく9slot全部を使い、stack数は強さに足さない。container、他プレイヤーの
inventory、furnace／smokerの中身はクライアントから確実に観測できないため対象外。

花は設置block、対応するpotted block、hotbar itemを対象にする。基本は `P=1`、
`lilac` だけ `P=5`。

- pleasant: `lily_of_the_valley / lilac / peony / rose_bush / cactus_flower / flowering_azalea`
- mixed: `wither_rose`
- unpleasant: `allium / pitcher_plant / torchflower / open_eyeblossom`
- それ以外と `closed_eyeblossom` は無臭

1.21.11に実レジストリがない `sulfur_block` と `golden_dandelion` は架空IDを作らず保留する。
追加された実IDを確認できた版で、それぞれ `P=10` と `P=1` の候補へ加える。

## 4. serverへ渡す形と発話

```json
{
  "smell_observation": {
    "status": "present",
    "smell_id": "bread",
    "category": "food",
    "valence": "pleasant",
    "source_kind": "hotbar",
    "specificity": "source",
    "effective_strength": 3,
    "temperature_modifier": 0,
    "rain_after_active": false,
    "basis": "smell_policy_v1"
  }
}
```

- `status=none`: 対応済みadapterが調べて「匂いなし」。field自体がない旧adapterと区別する
- `status=suppressed`: 雨／雪／雷／水中で嗅げない。`suppression_reason` を付ける
- `status=present`: adapterで解決済みの一件だけ。方向・距離・個数・entity IDは送らない

serverは新しい同一結果を2回連続で受けたときだけ自発発話し、同じ優勢状態では一度だけ、
再出現や勝者変更にも全体2分クールダウンを掛ける。playerが現在の匂いを尋ねたときは
そのフレームの `none / suppressed / present / 旧adapter不明` を待たずにコード固定文で返す。
特定ゾンビだけは従来の警告優先度を保ち、それ以外は安全反応とplayer inputを押しのけないambientにする。
匂い単独でcombat、panic、alert、workshop pauseを開始せず、通常 `player_chat` モデルにも
観測を渡さない。モデルが補作した現在の嗅覚断言は従来どおり棄却する。

旧 `zombie_scent_clues` は移行互換として受信し、新fieldがない場合だけ従来のゾンビ匂いへ読み替える。
