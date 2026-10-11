# スメルバトル仕様

状態: **2026-10-08 v2の方位計算・送受信・返答を撤去。各匂いの強度とマスキング、雨上がりの一度の気づきは維持する。2026-10-11に撤去後を含むRust単体512件成功、Fabric123件成功の記録とofflineビルドを確認。全体検査の残件は[再確認記録](2026-10-11-checkpoint-verification.md)を参照。撤去前の自動検証・実モデル8条件は過去の検証記録を参照。実Minecraft・実TTSは未確認。**

Fabric adapter が実際の近接源、hotbar、バイオーム、天候をコードで照合し、
その時点で感じ取れる匂いを `smell_observation.components` に解決する。サーバーやLLMに
方向・位置・距離・個数を渡して「何が臭うか」を推測させない。

2026-10-08: 共通プロンプトの `grounding` には未観測の匂いを語らない原則を置く。匂いの識別範囲と強さの扱いは同じプロンプトの `observed_smell` にまとめ、通常会話・環境反応・個別反応へ現在の匂い観測がある時だけ付ける。ambient目的文・周辺変化の状況文・匂い反応の注記にあった重複を除いた。共通化時の自動テストとビルドは確認済み。方位撤去後を含む現在コードの単体・Fabric確認は2026-10-11の[再確認記録](2026-10-11-checkpoint-verification.md)を参照。撤去前の実モデルの出力と全体検査の残件は[検証結果](2026-10-08-smell-v2-test-results.md)を参照。

## 1. 計算

各候補は伝播力 `P` とプレイヤーからの3次元距離 `d` を持つ。hotbarと現在バイオームは
`d=0` とする。温度補正と雨上がり補正を足した伝播力を `P_eff` とし、

```text
P_eff = clamp(P + temperature_modifier + rain_after_modifier, 1, 12)
candidate iff d <= P_eff
distance_cost = max(0, ceil(d) - 1)
score = P_eff - distance_cost
```

通常臭では `rain_after_modifier=0` とし、別に作る一度の雨上がり候補だけ `1` を使う。
この値で比較する。壁による減衰は行わない。同じ `smell_id` が複数あっても合算せず、
最も強い一件だけを残す。到達範囲外の候補は先に除き、その後に以下の規則を適用する。

- ゾンビ系（`zombie` と `rotten_flesh`）は、他の種類より1以上強ければその種類を隠す。
- それ以外は、他の種類より4以上強ければその種類を隠す。差3以内は共存する。
- 隠されなかった各種類のID・強度・快不快・発生源種別をそのまま保持する。
  同点でも `food` / `flower` / `mixed` などの親IDに潰さない。配列はID順。
- スメル値は1が仄かな匂い、8が強烈な匂いという尺度。単なる優勝点ではなく、
  補正後の各成分の `effective_strength` を通常会話と環境対話にも渡す。

| 距離・温度補正後の組合せ | 残る匂い |
|---|---|
| ゾンビ1・花1 | 両方 |
| ゾンビ2・花1 | ゾンビだけ |
| スープ3・ゾンビ1〜3 | 両方 |
| スープ3・ゾンビ4 | ゾンビだけ |
| ライラック4・花1 | 両方 |
| ライラック5・花1 | ライラックだけ |
| コンポスター3・花1 | 両方 |

コンポスターの基礎値は5から3へ変更した。花を守る個別例外は設けない。
腐った肉とゾンビはともに基礎値8。腐った肉8・ゾンビ7なら腐った肉だけ、8同士なら
各成分を残す。匂いの種類数を個体数に置き換えない。

### 方位計算の撤去（2026-10-08）

すべての匂いについて方位の計算・保持・送受信を撤去した。強度による例外は設けない。
視覚・聴覚の方向観測とは独立した変更で、匂いから発生源の方位を割り出さない。

距離はadapter内部で到達と強度の減衰を計算するために使う。block再走査の間も、
保存した座標と現在のプレイヤー位置から距離だけを更新する。serverやLLMへ方向・位置・
距離・個体IDを渡さず、同種の代表源の切替から頭数も作らない。
優先するゾンビ匂い反応では、未再生の古い強度・発生源属性に対する再判断を維持する。

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
- 雨上がりの180秒は**気づき候補を観測できる期限**。匂いを180秒間独占させる時間ではない
- 植物・土系の雨上がり候補に `+1`。通常無臭の草・葉・土は補正前 `P=0` とし、設置・手持ちの花も雨上がり候補へまとめる。最も強い一源を使う
- **2026-10-08、一度気づいたら慣れる:** 雨上がり一件を既存の匂い反応で扱い、出力完了後は同じ窓を扱い済みにする。モデルが沈黙を選んだ場合も一度の判断で終了。開始・生成成功だけでは終えない。失敗・取消もその一度の注意を終了して通常へ戻し、雨だけを再試行し続けない。配送結果は失敗・取消のまま記録する
- 通常の花・食品・ゾンビ等は、雨上がり補正なしの通常スメルバトルで常に保持する。一件の反応中だけモデルへの匂い入力を雨上がりへ絞り、出力後は180秒を待たず通常へ戻る。通常臭のクールダウンは消費しない
- 同じ窓で一時的に源が遠ざかった／水に入っただけでは再び驚かない。新しい雨上がり窓は別の一件として扱う。タイマーだけで未検知の匂いを作らず、戦闘・暗所・雷・プレイヤー入力等の既存優先順位と、Web・学習中の非敵対ambient抑止を維持する
- 再び降水したときとdimension変更時に雨上がり状態を解除する。水中ではタイマーだけ進む

## 3. 対象

| 匂い | 実レジストリ／実状態 | P | 候補になる場所 |
|---|---|---:|---|
| ゾンビ系 | `zombie / zombie_villager / husk / drowned / zombified_piglin` の生きた実体。基礎8、暖地では最大10ブロック | 8 | entity |
| 腐った肉 | `rotten_flesh` | 8 | hotbar・落下item |
| コンポスター | `composter` の `level > 0`。空は無臭 | 3 | block |
| 醸造台 | `brewing_stand` の瓶slotが一つ以上埋まっている | 5 | block |
| 沼 | `swamp / mangrove_swamp` | 3 | 現在バイオーム |
| 生肉 | `beef / chicken / mutton / porkchop / rabbit` | 3 | hotbar・落下item |
| 生魚 | `cod / salmon / tropical_fish / pufferfish` | 3 | hotbar・落下item |
| 焼いた肉・魚 | `cooked_beef` 等の実ID | 3 | hotbar・落下item |
| スープ類 | `mushroom_stew / rabbit_stew / beetroot_soup / suspicious_stew` | 3 | hotbar・落下item |
| 菓子・パン | `cookie / cake / bread` | 3 | hotbar・落下item。設置cake／candle cakeも対象 |
| イカ墨 | `ink_sac` | 3 | hotbarだけ |
| 焼いている肉・魚 | lit `campfire / soul_campfire` の実調理slot | 3 | block。寒さを無視 |

ゾンビ系の収集は敵対状態・視認・聴取とは独立する。中立状態のゾンビピグリンも対象。
チキンジョッキーは騎乗中のゾンビ実体として扱い、架空のentity IDは作らない。
嗅覚だけで種別の内訳や敵対状態を推定せず、`zombie` という同一の匂いにまとめる。

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
    "components": [
      {"smell_id": "composter", "category": "compost", "valence": "unpleasant",
       "source_kind": "block", "effective_strength": 3},
      {"smell_id": "lily_of_the_valley", "category": "flower", "valence": "pleasant",
       "source_kind": "hotbar", "effective_strength": 1}
    ],
    "temperature_modifier": 0,
    "rain_after_active": false,
    "basis": "smell_policy_v2"
  }
}
```

- `basis=smell_policy_v2` と `components` は必須。旧flat形式・旧basis・新旧併記を拒否する。
  adapterとRustサーバーは両方更新する。旧形式への変換やfallbackは設けない。
- `status=none`: 対応済みadapterが調べて「匂いなし」。`components=[]`。
  field自体がない観測未提供と区別する。
- `status=suppressed`: 雨／雪／雷／水中で嗅げない。`components=[]` と `suppression_reason`。
- `status=present`: 非空の成分一覧。各成分の `smell_id / category / valence / source_kind / effective_strength`
  は必須。IDの重複、不整合、1〜12外の強度、未知fieldを入口で拒否する。
- `direction_estimate` は削除済み。外部源・手持ち・雨上がりを問わず、objectでもnullでも未知fieldとして拒否する。
- `components` と `status` は通常臭に対応する。`rain_after` を通常成分へ混ぜず、
  任意の `rain_after_notice: {window_id, component}` に分ける。通常臭が `none` でもこの候補は持てる。
  `rain_after_active=true` かつ抑止外だけ許可し、成分は `rain_after / pleasant / blockまたはhotbar`。
  手持ちの花も受信できる。他の匂いID・0の窓ID・通常成分内の `rain_after` は拒否する。
- 窓IDはコードが出力完了を照合するためだけに使い、LLMへ渡さない。明示匂い質問でその一件を
  答え終えた場合も扱い済みにする。古い窓・古いturnの完了で新しい雨上がりを消費しない。
- 方向・位置・距離・個数・entity IDは送らない。

雨上がりの一度の気づきを除く通常の自発匂い反応は[周辺の気づきテーブル](2026-10-08-surroundings-notice.md)へ統合した。既定3分ごとの新しい全snapshotを前回の周期表と比較する。モデルは当回の共通観測と対話履歴から、コメント済みかも踏まえて話す／黙るを選ぶ。比較基準は発話結果にかかわらず周期ごとに更新し、古い差分を持ち越さない。成分順序・匂いの強度・同種の代表源だけの変化では再到着にしない。当回のモデル入力には現在の強度も残す。

特定ゾンビの優先反応だけは既存の2観測・2分間隔を維持する。未再生の強度・発生源属性変更は、安定した新観測へ一度だけ再判断し、再取消後は通常間隔を守る。周期の気づき発話は当回の表を固定し、生成・音声合成待ち中の通常の種類や匂い強度の変化で取消さない。

現在の匂いへの明示質問は、現在の `none / suppressed / present / 観測未提供` からコード固定文で返す。presentでは各匂いと強さを答え、旧単独臭の録音cueへ戻さない。固定文の強度表現は1「ほのか」、2〜3「匂いがする」、4〜7「はっきり」、8以上「強烈」。方向への問いは「匂いだけでは方向までは分からへんわ」、距離への問いは「距離までは分からへんわ」と答える。匂い単独ではcombat、panic、alert、workshop pauseを開始しない。

通常会話にも解決済みの匂いを共通観測として渡す。観測の扱いは共有プロンプトで伝え、通常対話の嗅覚断言を生成後の単語検査で棄却しない。player報告、assistant履歴、視覚のブロック一覧から嗅覚観測を作らない。ベリー・はちみつの新しい検出源は追加していない。

Rust本体の川柳workshop中は、自動反応と匂い質問の固定応答を停止する。
「香りを〜に変えませんか」などの入力は句の相談へ渡し、匂い語だけでは横取りしない。
相談終了後は現在の匂い観測に基づく自発反応と、明示質問への固定応答へ戻る。

匂い観測は `smell_observation` で受信する。field省略は観測未提供として扱い、別の入力から匂いを推定しない。
