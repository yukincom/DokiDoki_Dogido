# 現行仕様

この文書は、2026-05-24 時点で会話中に固まった方針をまとめた現行仕様です。

既存のメモより優先して参照する前提です。

## 1. 目的とスコープ

- Minecraft のプレイ状況を読み取り、怖がりな AI キャラクター「ドギド」が実況・警告・雑談を行う
- まずは 1 人プレイ前提で設計する
- 他プレイヤーがいても、基本的には接続中のメインプレイヤーだけを観測対象にする
- コンパニオン bot は作らない

## 2. システム構成

### 基本方針

- サーバーは本リポジトリの `dogido_server` として実装する（`yuno-chan-api` からは定時お知らせと LINE / Discord 受信のみ流用予定）
- `mindcraft` はそのまま使うのではなく、取得できる情報やコード構成の参考に使う
- Minecraft 側は AI bot ではなく、プレイヤー本人を観測するクライアントアダプタとして実装する

### 推奨構成

```text
Minecraft Java Edition
  -> Minecraft client adapter (Fabric client mod 想定)
  -> dogido-server (Python / FastAPI)
     -> event normalizer
     -> state machine
     -> priority rule engine
     -> LLM response generator
     -> audio router
  -> M5Stack Push Avatar
```

### 責務分担

#### Minecraft client adapter

- プレイヤー本人の状態を取得する
- ゲーム固有イベントを検出する
- `dogido-server` にローカル送信する
  - 受信 endpoint の正本は [受信 API 仕様](adapter-api.md)

#### dogido-server

- イベントを正規化する
- ドギドの内部状態を管理する
- 発話優先度を判定する
- 緊急音声と通常会話を分離する
- 必要なときだけ LLM を呼ぶ

#### M5Stack Push Avatar

- SD カード上のキャッシュ悲鳴音声を低遅延再生する
- 通常の状況説明 TTS を再生する

## 3. 観測ポリシー

将来的に他ゲームにも展開したいが、チートツールにはしない。

そのため、取得情報は「プレイヤーが知り得る情報」に寄せる。

### 取得してよい情報

- プレイヤー本人の位置
- 視線方向
- 体力
- 空腹
- 所持品
- 近くで見えているエンティティ
- 周辺ブロック
- 周辺資源候補
- 時刻
- 天候
- 被弾
- 死亡要因
- クライアントが受け取った音情報

### 強く制限する情報

- 未観測領域の完全な透視
- 壁の向こうの敵の正確な座標断定
- 音だけで得た未視認敵の詳細断定
- 他プレイヤーの隠れた所持品

### 情報の確度レベル

#### `visual`

- 見えている敵や mob
- もっとも確度が高い

#### `auditory`

- 音だけで存在を感じた脅威
- 方向は出してよいが、断定は抑える

#### `inferred`

- 暗い空間の広がり
- 周辺資源の傾向
- 湧きそうな場所
- 推定情報として扱う

## 4. Minecraft 側で取りたい情報

### プレイヤー状態

- `player_name`
- `position`
- `yaw`
- `pitch`
- `health`
- `hunger`
- `inventory`
- `held_item`
- `dimension`

### ワールド状態

- `time_of_day`
- `weather`
- `biome`
- `local_light`
- `sky_visible`
- `ceiling_height`
- `enclosure_score`

### 周辺状況

- 視認できている敵
- 視認できている平和 mob
- 周辺資源候補
- 最近の被弾
- 死亡原因
- モンスターの音
- ブロック破壊の方向と対象

## 5. 音由来の脅威検知

### 基本方針

- プレイヤーの後ろや左右からの危険は分かったほうが面白い
- ただし、未視認敵の完全特定にはしない

### 個別仕様

- スケルトンは、音で `完全察知` させず `たまに予感だけする` 扱いにする
- 正本は [スケルトン仕様](skeleton-spec.md) を参照する

### 表現ルール

- 視認済みなら具体名を出してよい
  - 例: 「後ろ！クリーパーが近づいてくるで！」
- 音だけなら存在と方向中心にする
  - 例: 「左奥からなんか声するで」
- 以前視認していた敵が壁向こうに消えた場合は記憶ベースで言ってよい
  - 例: 「さっきのウィッチ、向こうにまだおるかも」

### 方向粒度

- 前
- 後ろ
- 左
- 右
- 上
- 下
- 左奥
- 右奥

### 実装指針

- 音イベントは粗い方向へ量子化する
- 音だけで得た情報は `certainty=low` として扱う
- 出力時は断定語を避ける

## 6. 暗さと危険度の判定

暗さは単純な明るさ判定ではなく、危険度として扱う。

巨大洞窟のように、足元だけ明るくても暗闇とつながっている場合があるため。

### 判定に使う要素

- `local_light`
- `connected_dark_volume`
- `nearest_dark_spawn_distance`
- `recent_hostile_audio`
- `recent_hostile_visual`
- `enclosure_score`
- `sky_visible`
- `ceiling_height`

### `enclosure_score` の意味

- 屋内や地下っぽさの補助指標
- 素材密度だけで最終判定はしない

### `connected_dark_volume` の意味

- プレイヤー周辺とつながっている暗い空間の広がり
- 局所的に明るくても、先に暗い空間が広がるなら危険とみなす

### 判定方針

- `暗い` と `危ない` は別概念にする
- 松明を促す判断は、単なる照度より湧きリスクを優先する

## 7. 音声出力方針

### 緊急音声

- 絶叫系悲鳴は TTS ではなく SD カードのキャッシュ音声を使う
- サーバーからは `cue_id`, `priority`, `interrupt` などの再生命令だけを送る

### 通常音声

- 状況説明
- 助言
- 雑談
- 死亡時フォロー

これらは通常の TTS を使う。

### 優先順位

1. 悲鳴
2. 短い警告
3. 通常 TTS

### 割り込み

- 通常会話中でも悲鳴は割り込む
- TTS 再生中でも中断して悲鳴を優先する

## 8. ドギドの状態機械

正本は [状態機械](state-machine.md) を参照する。

### 状態一覧

- `normal`
- `alert`
- `panic`
- `suppressed_panic`
- `aftermath`

### ボス戦の例外

- 一部のボス戦は、通常モンスターと同じ `panic` 連打にはしない
- ボスごとに `panic_policy` を持たせ、`reveal_only` や `tactical` を選べる形にする
- 個別仕様は [ボス仕様](boss-spec.md) を参照する

## 9. `うるさい` 抑制ルール

### 検知対象

- うるさい
- 静かにして
- 黙れ

まずはルールベースで検知する。

### 発動条件

- 3 回以上言われたら発動

### 発動後の挙動

- `panic` の絶叫系悲鳴を 5 〜 10 秒だけ抑制する
- 抑制中は弱い悲鳴に変更する
  - 例: `ハァハァ`, `ひっ・・・・`
- それでも交戦が続いている場合は、状況解説寄りへシフトする
  - 例: `後ろ！`, `あと 2 体！`

### 抑制解除

- 一定時間経過後
- もしくは戦闘終了後

## 10. 初期パラメータ案

詳細は [状態機械](state-machine.md) を参照する。

以下は初期値の要約であり、実機調整を前提とする。

- 緊急視認警告距離: `8` マス
- `panic` 移行距離: `7` マス
- 複数敵警戒距離: `10` マス
- 交戦終了候補距離: `10` マス
- 交戦終了確認時間: `4` 〜 `5` 秒
- 抑制継続時間: `5` 〜 `10` 秒
- 余韻状態時間: `8` 秒前後
- 悲鳴クールダウン: `1.0` 〜 `1.5` 秒

## 11. 戦闘判定

詳細は [状態機械](state-machine.md) を参照する。

### `panic` に入る条件候補

- 敵が `7` マス以内
- 敵が `10` マス以内に 2 体以上
- 直近 3 秒以内に被弾

### 戦闘終了条件候補

- `10` マス以内に敵がいない
- 直近 `4` 〜 `5` 秒で被弾していない
- 直近 `4` 〜 `5` 秒で敵の音や視認更新がない

## 12. 地形・状態の例外

- 水辺、燃焼、粉雪のように `同じ敵でも状況で脅威度が変わる` ケースは別ルールで扱う
- 正本は [環境由来の敵仕様](environmental-hostile-spec.md) を参照する

## 13. 最小イベントスキーマ案

正本は [イベントスキーマ](event-schema.md) を参照する。

### 最小構成の考え方

- `event`
- `player`
- `world`
- `visual_threats`
- `auditory_threats`
- `inventory`
- `combat`

## 14. 受信 API

adapter から `dogido-server` へ送る endpoint の正本は [受信 API 仕様](adapter-api.md) を参照する。

## 15. 抽象化方針

将来的に他ゲームへ展開できるよう、抽象化できるものは抽象化する。

ただし、すべてを完全汎用にはしない。

### 抽象イベントの例

- `threat_detected`
- `threat_approaching`
- `visibility_low`
- `resource_option_found`
- `player_died`

### Minecraft 固有情報の例

- `entity_type=creeper`
- `sound_event=entity.witch.ambient`
- `block_context=deepslate_cave`

### 公開/非公開の切り分け方針

- OSS で出しやすいもの
  - Minecraft adapter
  - event schema
  - 基本 state machine
- 差別化要素として閉じやすいもの
  - 他ゲーム adapter
  - 高度な threat scoring
  - 予測ロジック

## 15. 直近の実装順

1. Minecraft adapter が送るイベント JSON を定義する
2. adapter -> server の受信 API を定義する
3. `visual / auditory / inferred` の扱いを明文化する
4. state machine を仕様化する
5. audio cue 一覧と優先度を定義する
6. `yuno-chan-api` 由来機能（定時お知らせ・LINE / Discord 受信）の取り込み方法を決める
