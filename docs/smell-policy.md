# スメルバトル仕様

状態: **2026-10-01 勝者の粗い方角を追加。Python/Rust/Java自動テストと模擬HTTP確認済み。実Minecraft・実Qwen・実TTSは未確認。**

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

### 方向の観測

方向はスメルバトルで勝った実際の匂い源から、プレイヤーに対する方角を8方位へ丸める。
上下差が1ブロックを超えた場合は上／下も付け、真上・真下には水平方向を付けない。
プレイヤーが静止していても、接近するゾンビや勝者の切替を毎回の現在観測へ反映する。
移動履歴の蓄積や、静止した匂い源であることを条件にしない。

実位置と個体IDはadapter内部で勝者の方角を取得するために使い、serverへは粗い方向だけを渡す。
勝者の切替から頭数や「もう一体いる」という別の観測は作らない。block再走査の間も
プレイヤー移動に応じた強度を更新する。距離推定を新たに計算・保持せず、serverやLLMへ
距離情報を加えない。接近による強度変化だけでは同じ方角への発話を取り消さない。

hotbar・現在バイオーム・雨上がり全般・分類までの同点・混合・抑制中には推定を付けない。
方向推定のない観測も受け付ける。

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

- `status=none`: 対応済みadapterが調べて「匂いなし」。field自体がない観測未提供と区別する
- `status=suppressed`: 雨／雪／雷／水中で嗅げない。`suppression_reason` を付ける
- `status=present`: adapterで解決済みの一件だけ。正確な位置・距離・個数・entity IDは送らない
- 方角を取得できる場合は `direction_estimate: {cardinal: "east", basis: "source_bearing"}` も付ける。`vertical: "above" / "below"` は任意で、cardinalまたはverticalの一方以上が必要

serverは新しい同一結果を2回連続で受けたときだけ自発反応の候補にし、同じ優勢状態では一度だけ、
再出現や勝者変更にも全体2分クールダウンを掛ける。Rust本体では、この機会に会話モデルが
解決済みの匂い一件と再生完了した直近の会話から「話す／黙る」と内容を選ぶ。
無言でも検討機会を消費し、発話なしの反応として短期履歴へ残す。発声や再生済みの一往復には数えない。匂いの検出・発生源・優先度はモデルへ委ねない。
Pythonの既存自発反応と、生成不成立時のfallbackはコード固定文を使う。playerが現在の匂いを尋ねたときは
そのフレームの `none / suppressed / present / 観測未提供` を待たずにコード固定文で返す。
Rust本体は通常会話・環境対話と明示質問に粗い方向だけを渡し、目視位置と区別する。取得できない
方向への質問には「方向はまだ絞れてへん」と返す。方向を添えた固定応答は録音cueで置き換えない。
再生開始前に方角が変わった生成結果は取り消し、同じ匂いの新しい方角が2観測で安定したら
一度だけ直ちに再判断できる。再取消後は通常の2分間隔を守る。モデルの無言選択や再生済みの
発話は再判断しない。再生開始後の通常の方角変化は従来どおり途中停止の理由にしない。
特定ゾンビだけは従来の警告優先度を保ち、それ以外は安全反応とplayer inputを押しのけないambientにする。
匂い単独でcombat、panic、alert、workshop pauseを開始しない。Rust本体の通常 `player_chat` にも
解決済みの匂い一件を共通観測として渡し、会話中にその匂いを話題にできる。`present` の観測がない
現在の嗅覚断言は従来どおり棄却する。player報告やassistant履歴から嗅覚観測を作らない。
Python旧本体の通常雑談は従来どおり嗅覚観測を持たない。

Rust本体の川柳workshop中は、自動反応と匂い質問の固定応答を停止する。
「香りを〜に変えませんか」などの入力は句の相談へ渡し、匂い語だけでは横取りしない。
相談終了後は現在の匂い観測に基づく自発反応と、明示質問への固定応答へ戻る。

匂い観測は `smell_observation` で受信する。field省略は観測未提供として扱い、別の入力から匂いを推定しない。
