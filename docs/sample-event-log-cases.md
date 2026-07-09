# サンプルイベントログ収集ケース

この文書は、ドギド実装の初期段階で集めたいイベントログのケース一覧です。

目的は、実プレイ中に何を記録すれば実装と評価に使えるかを先に決めることです。

イベント JSON の形は [イベントスキーマ](event-schema.md) を参照します。
受信方法は [受信 API 仕様](adapter-api.md) を参照します。

## 1. 方針

- まずは会話中に出てきた題材だけを対象にする
- 1 ケースにつき 1 つの「面白い判断」を検証できるようにする
- 完璧なログより、再現性のある短いログを優先する
- 実プレイで再現しにくいものは、後で手書き fixture にしてよい

## 2. 収集時に最低限ほしい情報

各ケースで最低限ほしいのは以下。

- `observed_at`
- `sequence`
- `event`
- `player`
- `world`
- `visual_threats`
- `auditory_threats`
- `inventory`
- `combat`

ケースによって以下もほしい。

- `nearby_resources`
- `passive_mobs`
- `neutral_mobs`
- `hostile_mobs`
- `meta.death_cause`
- 討伐回数（エンダードラゴン等ボスのみ）

## 3. 収集優先度

### 最優先

- 後方クリーパー　✅
- 複数敵接近✅
- 音だけで脅威を感じるケース✅
- 巨大洞窟の暗所危険✅
- `うるさい` 抑制✅
- エンティティのjson化　✅6/9完了
- neutral_mobsの敵対化状態遷移管理

### 次点

- 松明材料あり✅
- ベッドあり　❌廃止
- 昼 mob 雑談　
- 死亡イベント✅
- 戦闘終了後の余韻
- エンダードラゴン、ウォーデン等ボス級mobへの特殊メッセージ（ウォーデンvsアイアンゴーレムのみ実装。他、TNT・エンドクリスタル爆殺メッセージまだうまくいかない
- プレイヤーからの音声入力・雑談
- プレイヤーがドギドの句を評価するシステム

## 4. ケース一覧

## Case 01: 後方クリーパー接近

### 目的

- `後ろ！クリーパー！` 系の高優先視認警告を確認する

### 状況

- プレイヤーの後方 `8` マス以内にクリーパー
- 視認済み
- 接近中

### 期待する主イベント

- `threat_approaching`

### 期待する反応

- `panic` または強い `alert`
- 高優先 `callout`
- 必要なら `panic_cue`

### 最低限ほしい項目

- `visual_threats[0].type = creeper`
- `visual_threats[0].direction.horizontal = back`
- `visual_threats[0].distance <= 8`
- `visual_threats[0].approaching = true`

## Case 02: 複数敵が近い

### 目的

- ユーザー会話より周辺脅威を優先する判断を確認する

### 状況

- `10` マス以内に敵が 2 体以上
- 例: ゾンビ + スケルトン

### 期待する主イベント

- `threat_approaching`

### 期待する反応

- `panic`
- 絶叫または短い敵数コールアウト
- 通常会話の抑止

### 最低限ほしい項目

- `combat.hostiles_within_10 >= 2`
- `visual_threats.length >= 2`

## Case 03: 音だけで敵の気配

### 目的

- 音由来脅威を方向中心で扱えるか確認する

### 状況

- 近距離で敵っぽい鳴き声や気配がする
- 見えていない
- 格子窓や壁越しの可能性あり

### 期待する主イベント

- `hostile_audio_detected`

### 期待する反応

- `左からなんか声する`
- 具体名断定を避ける

### 最低限ほしい項目

- `auditory_threats[0].label = hostile_presence` または `hostile_voice_like`
- `auditory_threats[0].direction`
- `auditory_threats[0].spoken_name_allowed = false`

## Case 04: さっき見た敵が壁向こうに消えた

### 目的

- 視認記憶を使った控えめ推定を確認する

### 状況

- 直前に敵を見ていた
- いまは見えないが、壁や地形の向こうに消えた
- 音が続いていてもよい

### 期待する主イベント

- `hostile_audio_detected` または `status_snapshot`

### 期待する反応

- `さっきのウィッチ、向こうにまだおるかも`

### 最低限ほしい項目

- `combat.recent_hostile_visual_ms`
- `auditory_threats`
- 内部的には `last_confirmed_hostiles` 更新に使える情報

## Case 05: 巨大洞窟で足元だけ明るい

### 目的

- `local_light` が高めでも `danger_darkness_score` で危険判定できるか確認する

### 状況

- グロウベリー等で足元は明るい
- だが暗闇ゾーンとつながっている
- 敵が押し寄せる可能性が高い

### 期待する主イベント

- `status_snapshot`（暗所の本流）
- （互換）`danger_darkness_changed` でも server は受理してよいが、本番 adapter の主経路ではない

### 期待する反応

- `暗い` ではなく `危ない` 寄りの助言
- 松明や撤退の提案
- 必要なら `dark_push` 多段（息・あと押し）や shelter 判定へ接続

### 最低限ほしい項目

- `world.local_light`
- `world.connected_dark_volume`
- `world.nearest_dark_spawn_distance`
- `world.danger_darkness_score`

## Case 06: 暗いが松明を持っている

### 目的

- 単純な助言フローの確認

### 状況

- 周囲が危険
- インベントリに松明あり

### 期待する主イベント

- `status_snapshot`（暗所スコア + inventory 同梱）

### 期待する反応

- `そろそろ松明をなあ？`

### 最低限ほしい項目

- `inventory.torch > 0`
- `world.danger_darkness_score`

## Case 07: 暗いが松明はない、石炭と棒はある

### 目的

- 材料から松明提案へ進むか確認する

### 状況

- 松明なし
- 石炭あり
- 棒あり

### 期待する主イベント

- `status_snapshot`（暗所スコア + inventory 同梱）

### 期待する反応

- `石炭あるやん！これで松明作ろうや！`

### 最低限ほしい項目

- `inventory.torch = 0`
- `inventory.coal > 0`
- `inventory.stick > 0`

## Case 08: 暗い、ベッドは持っている

### 目的

- 照明ではなくベッド提案へ切り替わるか確認する

### 状況

- 松明材料が足りない
- ベッドあり

### 期待する主イベント

- `status_snapshot`（暗所スコア + inventory 同梱）

### 期待する反応

- `ベッド持ってるやん！もう寝よ！`

### 最低限ほしい項目

- `inventory.bed > 0`
- `inventory.torch = 0`

## Case 09: 暗い、材料不足、周辺資源あり

### 目的

- `nearby_resources` を使った助言を確認する

### 状況

- 松明なし
- ベッドなし
- 近くに `coal_ore`, `oak_log` 等あり

### 期待する主イベント

- `status_snapshot`（`nearby_resources` 同梱）
- （互換）`resource_option_found` は主経路にしない

### 期待する反応

- `石あるな。じゃあ木を切ってー`

### 最低限ほしい項目

- `nearby_resources`
- `inventory`
- `world.danger_darkness_score`

## Case 10: もうすぐ朝

### 目的

- 時刻に応じた励ましを確認する

### 状況

- 夜明け前
- まだ危険圏かもしれない

### 期待する主イベント

- `status_snapshot`（`world.time_phase` 継続更新）
- （互換）`time_phase_changed` は主経路にしない

### 期待する反応

- `もうすぐ朝や！踏ん張れ！`

### 最低限ほしい項目

- `world.time_of_day`
- `world.time_phase`

## Case 11: ゾンビが燃えて喜ぶ

### 目的

- 特殊リアクションの種として使えるか確認する

### 状況

- 夜明け後または日光下
- ゾンビが燃えている

### 期待する主イベント

- 初期段階では専用イベントなしでもよい
- `status_snapshot` や後日の `hostile_state_changed` 候補

### 期待する反応

- `おらあ！我らの勝利や！`

### 最低限ほしい項目

- まずはメモ段階
- 後で観測方法を調査

## Case 12: 水に浸かって燃えないゾンビ

### 目的

- 変則リアクションの種を残す

### 状況

- 日中
- ゾンビが水中または濡れ状態で燃えない

### 期待する主イベント

- 初期段階では専用イベントなしでもよい

### 期待する反応

- `素直に燃えてくれ・・・！`

### 最低限ほしい項目

- まずはメモ段階
- どこまで client 側で安定検知できるか要調査

## Case 13: モンスター方向に向かってブロックを壊す

### 目的

- `マジで行くん！？` 系の動揺リアクションを確認する

### 状況

- プレイヤーがブロック破壊中
- その先方向に敵がいる、または敵音がする

### 期待する主イベント

- `threat_detected` または将来の `block_break_toward_hostile`

### 期待する反応

- `マジで行くん！？`

### 最低限ほしい項目

- ブロック破壊方向
- 最近の敵方向

## Case 14: 戦闘終了直後の余韻

### 目的

- `aftermath` へ自然に入れるか確認する

### 状況

- 直前まで交戦
- いまは `10` マス以内に敵なし
- 数秒被弾なし

### 期待する主イベント

- `combat_ended`

### 期待する反応

- `あー・・・こわかったぁ・・・`

### 最低限ほしい項目

- `combat.recent_damage_ms`
- `combat.recent_hostile_visual_ms`
- `combat.recent_hostile_audio_ms`
- `combat.hostiles_within_10 = 0`

## Case 15: `うるさい` 3回で抑制

### 目的

- `suppressed_panic` への遷移確認

### 状況

- 交戦中
- ユーザーが `うるさい` 系を 3 回以上言う

### 期待する主イベント

- Minecraft イベントではなく会話入力側イベント
- ただし戦闘ログとセットで収集したい

### 期待する反応

- 絶叫が `ハァハァ`, `ひっ・・・` に変わる
- 続く場合は `後ろ！`, `あと 2 体！`

### 最低限ほしい項目

- 戦闘中イベント列
- `shut_up_count`
- 状態遷移ログ

## Case 16: 昼のうさぎ雑談

### 目的

- 平和時間帯の雑談を確認する

### 状況

- 昼
- 近くにうさぎ
- 敵脅威なし

### 期待する主イベント

- `ambient_mob_detected`　-> ambient_mobという表現はマイクラにはないこれも要整理対象

### 期待する反応

- `うさぎおるやん！かわい〜〜！`

### 最低限ほしい項目

- `passive_mobs`（旧 `peaceful_mobs` から改名済み。旧名も受信互換あり）
- `world.time_phase = day`
- 敵脅威が空

## Case 17: モンスターにやられて死亡

### 目的

- 責めすぎない死亡フォローを確認する

### 状況

- 敵由来で死亡

### 期待する主イベント

- `player_died`

### 期待する反応

- `次は照明がっつりつかお！`

### 最低限ほしい項目

- `meta.death_cause`
- 直前の戦闘情報

## Case 18: 落下事故で死亡

### 目的

- 事故死の励ましを確認する

### 状況

- 崖や高所から落下

### 期待する主イベント

- `player_died`

### 期待する反応

- `あー痛かったな。まあゲームやから！`

### 最低限ほしい項目

- `meta.death_cause = fall` 系

## Case 19: 浅瀬スケルトンを弓で対処できる

### 目的

- 水辺遠隔敵の高優先警告と、`弓あり` 分岐を確認する

### 状況

- 日中
- スケルトンが浅瀬にいる
- プレイヤーは `bow` または `crossbow` を使える
- 射線が通っている

### 期待する主イベント

- `status_snapshot`
- または将来の `hostile_state_changed`

### 期待する反応

- `遠くから撃てるなら倒せるで！`
- `足が取られるから近づかんほうがええ！`

### 最低限ほしい項目

- `player.held_item`
- `inventory`
- `visual_threats[].type = skeleton`
- `visual_threats[].in_water = true`
- `visual_threats[].distance`

## Case 20: 完全水没した遠隔敵は 1 回だけ警告

### 目的

- 完全水没した遠隔敵を `脅威ゼロではないが圧は低い` 扱いにできるか確認する

### 状況

- 遠隔敵が完全に水中
- 状態はしばらく変わらない

### 期待する主イベント

- `status_snapshot`

### 期待する反応

- `あっ[モンスター名]が…まー、射程が落ちるし、いることだけ覚えとこか・・・`
- その後、状態が変わらない限り `5 分` 抑制

### 最低限ほしい項目

- `visual_threats[].type`
- `visual_threats[].in_water = true`
- 将来的には `submerged_state = submerged`

## Case 21: 燃える近接敵が水と燃焼を往復する

### 目的

- `燃えた / 水に浸かった / 忙しいやっちゃな` の集約ルールを確認する

### 状況

- 日中
- 近接敵が燃える
- 水に入って消火する
- 再び燃える
- これを 2 回以上繰り返す

### 期待する主イベント

- `status_snapshot`
- または将来の `hostile_state_changed`

### 期待する反応

- 最初は `燃えとる`
- 次に `水に浸かった`
- 2 周以上したら `忙しいやっちゃな`
- その後 `5 分` 抑制

### 最低限ほしい項目

- `visual_threats[].on_fire`
- `visual_threats[].in_water`
- `visual_threats[].distance`

## Case 22: 粉雪の中の敵が見えた瞬間に悲鳴

### 目的

- 粉雪の透視を避けつつ、reveal の驚きを出せるか確認する

### 状況

- 敵が粉雪に隠れている間は見えない
- プレイヤーが粉雪を払う、または自分で踏み込む
- その瞬間に敵が確認できる

### 期待する主イベント

- `threat_detected`
- または将来の `hostile_revealed`

### 期待する反応

- `ぎゃー！`

### 最低限ほしい項目

- 将来的には `visual_threats[].obscured_by_powder_snow`
- reveal 後の `visual_threats[].distance`
- reveal 後の `visual_threats[].direction`

## 5. まず対応したい敵

マイクラ歴が浅くても、初期実装では以下から始めれば十分です。

- クリーパー
- ゾンビ
- スケルトン
- スパイダー
- ウィッチ

理由は、会話中に出てきた題材と、脅威リアクションが作りやすい相手だからです。

## 6. 後で増やしたい敵

初期実装後に広げたい候補。

- エンダーマン
- クモ系の派生
- 洞窟やネザー由来の敵
- 水中系の敵

ここは別途 `monster-catalog` に切り出してもよいです。

## 7. ログ収集メモ

### 実プレイで十分なもの

- 後方クリーパー
- 複数敵
- 暗所危険
- 昼 mob
- 死亡

### 手書き fixture で補いやすいもの

- 音だけの敵気配
- `うるさい` 抑制
- さっき見た敵が壁向こうに消えたケース

## 8. 次の段階

この文書を元に、後で `fixtures/` に JSON を増やしていく。

最初は以下を優先するとよい。

1. `creeper_behind_close.json`
2. `two_hostiles_close.json`
3. `hostile_audio_only.json`
4. `bright_but_connected_dark_cave.json`
5. `dark_has_coal_and_stick.json`
6. `combat_end_aftermath.json`
